"""`cgp review`: every story waiting on you, oldest wait first and smallest diff first within the same age. Read-only like `cgp status`
(no lock, no snapshot). `--html` writes the same rows as one self-contained page."""
import concurrent.futures
import html
import json
import os
import subprocess
import tempfile
from urllib.parse import urlparse
from .consts import HOME
from .util import age_seconds, die, out, printable
from .gh import gh
from .store import cfg, load_data
from .board import fetch_items, parse_item, parse_pr_ref
from .notify import REVIEW
from .policy import current_rating
from .pr import ci_state, pr_checks

PR_TIMEOUT = 10  # seconds to wait for one PR's gh calls; a slow or failing gh gives null PR facts
DIFF_KEYS = ("additions", "deletions", "changedFiles")


def pr_facts(ref):
    """{diff, files, ci} of a PR; every part is None when gh does not answer."""
    repo, number = ref
    try:
        p = gh("pr", "view", str(number), "-R", repo, "--json", ",".join(DIFF_KEYS + ("files",)), check=False, timeout=PR_TIMEOUT)
        view = json.loads(p.stdout) if not p.returncode else None
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        view = None
    view = view if isinstance(view, dict) else None
    checks = pr_checks(repo, number)
    return {"diff": {k: view.get(k) for k in DIFF_KEYS} if view else None,
            "files": [f["path"] for f in view.get("files") or [] if isinstance(f, dict) and f.get("path")] if view else None,
            "ci": ci_state(checks) if checks is not None else None}


def collect(c):
    """The rows, in the order they should be looked at."""
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"], bool(c["settings"]["nativeDependencies"]))]
    live = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft") and i["column"] != "done"
            and (i["column"] in REVIEW or i["waiting"])]
    data = load_data()
    seen = data.get("notified") or {}
    refs = {i["item"]: parse_pr_ref(c, i["pr"]) for i in live}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    futures = {k: pool.submit(pr_facts, r) for k, r in refs.items() if r}
    rows = []
    for n, it in enumerate(live):
        facts = {"diff": None, "files": None, "ci": None}
        if it["item"] in futures:
            try:
                facts = futures[it["item"]].result()
            except Exception:  # a crash in one PR's lookup must not lose the digest
                pass
        entry = seen.get(it["item"]) or {}
        since = entry.get("since") if entry.get("column") == it["column"] and entry.get("waiting") == it["waiting"] else None
        approx = since is None or bool(entry.get("approx"))
        rating = current_rating(data, it)
        files = data.get("touches", {}).get(it["item"]) or facts["files"] or None
        hours = int(age_seconds(since) // 3600) if since else None
        row = {"title": it["title"], "url": it["url"], "column": it["column"], "waitingSince": since, "waitingHours": hours,
               "approximate": approx, "rating": rating, "files": files, "diff": facts["diff"], "ci": facts["ci"],
               "preview": it["preview"], "pr": it["pr"], "plan": it["plan"]}
        row["packet"] = {k: row[k] for k in ("plan", "pr", "preview", "ci", "diff", "files", "rating")}
        d = facts["diff"]
        size = (d.get("additions") or 0) + (d.get("deletions") or 0) if d else None
        rows.append(((approx, -(hours or 0), size is None, size or 0, n), row))
    pool.shutdown(wait=False)
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


def link(url, label):
    """An anchor for an http(s) URL, plain escaped text for anything else (a board field is untrusted)."""
    url = printable(url or "")
    u = urlparse(url)
    text = html.escape(printable(label), quote=True)
    if u.scheme in ("http", "https") and u.netloc:
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{text}</a>'
    return text


def age_text(h):
    return "?" if h is None else f"{h // 24}d {h % 24}h" if h >= 24 else f"{h}h"


def render_html(c, rows):
    nag = int(c["settings"].get("nagAfterHours") or 0)
    esc = lambda s: html.escape(printable(str(s)), quote=True)

    def tone(h):
        return "" if not nag or h is None else "bad" if h >= nag else "warn" if h * 2 >= nag else ""

    body = []
    for r in rows:
        d, links = r["diff"], []
        size = f'+{esc(d.get("additions"))} -{esc(d.get("deletions"))}<br>{esc(d.get("changedFiles"))} files' if d else (
            f'{len(r["files"])} files' if r["files"] else "")
        for url, label in ((r["plan"], "Plan"), (r["pr"], "PR"), (r["preview"], "Preview")):
            if url:
                links.append(link(url, label))
        body.append(
            f'<tr><td data-l="Waiting" class="{tone(r["waitingHours"])}">{age_text(r["waitingHours"])}{"~" if r["approximate"] else ""}</td>'
            f'<td data-l="Story">{link(r["url"], r["title"])}<br><small>{esc(r["column"])}</small></td>'
            f'<td data-l="Size">{size}</td><td data-l="Rating">{esc(r["rating"] or "")}</td>'
            f'<td data-l="CI">{esc(r["ci"] or "")}</td><td data-l="Links">{" &middot; ".join(links)}</td></tr>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Review digest</title>
<style>body{{font:15px system-ui,sans-serif;margin:16px;color:#1c2330;background:#f6f7f9}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid #d9dee7;vertical-align:top}}th{{font-size:12px;text-transform:uppercase}}
.warn{{color:#b7791f;font-weight:bold}}.bad{{color:#c0392b;font-weight:bold}}small{{color:#5d6778}}
@media (max-width:600px){{thead{{display:none}}tr,td{{display:block}}tr{{border:1px solid #d9dee7;margin-bottom:10px;padding:4px}}td{{border:0}}
td::before{{content:attr(data-l) ": ";color:#5d6778}}}}</style></head><body>
<h1>Review digest</h1><p>{esc(c["board"]["title"])}: {len(rows)} waiting on you. A "~" means the wait is approximate.</p>
<table><thead><tr><th>Waiting</th><th>Story</th><th>Size</th><th>Rating</th><th>CI</th><th>Links</th></tr></thead>
<tbody>{"".join(body)}</tbody></table></body></html>
"""


def write_html(path, text):
    if os.path.islink(path):
        die(f"{path} is a symlink; refusing to write through it")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".review-", suffix=".tmp")  # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_review(a):
    c = cfg()
    rows = collect(c)
    if a.html:
        path = os.path.abspath(os.path.expanduser(a.html)) if isinstance(a.html, str) else os.path.join(HOME, "review.html")
        write_html(path, render_html(c, rows))
        out({"path": path, "stories": len(rows)})
        return
    out({"board": c["board"], "nagAfterHours": int(c["settings"].get("nagAfterHours") or 0), "stories": rows})
