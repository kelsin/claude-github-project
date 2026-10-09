"""Automatic intake, opt-in per repo with `"intake"` in its .cgp.json (read from the default branch): a Todo story for a default branch whose
latest workflow run is red, and the dependency PRs of bots imported into PR Review for the user to merge by hand. Runs from the
scheduler snapshot, at most once per `intakeSeconds` per repo; a failure is reported in the session state and never stops the loop.

Dependency stories never go through a worker (it would open its own `cgp/<n>` PR next to the bot's), so they are created in PR Review
with Plan: Skip and the bot's PR linked. Nothing here changes what the auto-approval policy allows: it refuses a Plan: Skip story."""
import fnmatch
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote
from .consts import INTAKE_BOTS, INTAKE_KEEP_SECONDS, INTAKE_MAX_OPEN, INTAKE_PER_CYCLE
from .util import printable
from .gh import gh, rest, viewer
from .store import load_data, update_data
from .board import set_single, set_text
from .intake import add_item, priority_option, put_on_board
from .policy import MAX_FILES, vet
from .repoconf import repo_config
from .story import sync_links

LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "*.lock", "go.sum")  # real lockfiles; manifests are rated medium
_checked = {}  # board id -> when this process last looked at the repos' config
_repaired = {}  # pending key -> when this process last tried to finish it


def sanitize(text, limit=120):
    """Text from a workflow name for a title or a body: control, bidi and markup characters removed, one line, cut to `limit`."""
    text = "".join(ch for ch in printable(text) if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp"))
    return re.sub(r"\s+", " ", re.sub(r"[<>@`\\\[\]]", " ", text)).strip()[:limit]


def api(path):
    return json.loads(gh("api", path).stdout)


def open_story(by_item, entry):
    i = by_item.get(entry.get("item"))
    return bool(i) and not i["closed"] and i["column"] != "done"


def find_marked(repo, marker):
    """An issue this account already created for the marker (its creation succeeded but the board add did not), else None."""
    me = viewer()
    for issue in rest(f"repos/{repo}/issues?state=all&creator={quote(me, safe='')}"):
        if "pull_request" not in issue and (issue.get("user") or {}).get("login") == me and marker in (issue.get("body") or ""):
            return issue
    return None


def file_story(c, items, repo, key, kind, title, body, finish, extra):
    """Create the issue and put it on the board, once per incident `key`. A claim is written before the issue exists and the hidden marker
    in its body is searched for before one is created, so a crash between creating the issue and adding it to the board (or a wiped
    state file) finds the issue again instead of filing a second one."""
    marker = f"<!-- cgp-intake:{kind}:{key} -->"
    update_data(lambda d: d.setdefault("intake", {}).setdefault("pending", {}).setdefault(key, {"at": time.time(), "kind": kind, "repo": repo, **extra}))
    issue = find_marked(repo, marker)
    if not issue:
        issue = json.loads(gh("api", f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={body}\n\n{marker}").stdout)
    item = next((i["item"] for i in items if i["issueRepo"] == repo and i["number"] == issue["number"]), None) \
        or (put_on_board(c, issue["node_id"]) if kind == "run" else add_item(c, issue["node_id"]))
    finish(item, issue["number"])
    return complete(key, kind, {"at": time.time(), "repo": repo, "item": item, "number": issue["number"], **extra})


def complete(key, kind, entry):
    """Turn the pending claim into a permanent entry."""
    def done(d):
        sec = d.setdefault("intake", {})
        sec.setdefault("pending", {}).pop(key, None)
        sec.setdefault(kind + "s", {})[key] = entry
    update_data(done)
    return entry


def red_runs(repo):
    """The newest completed push / schedule run of every active workflow on the default branch (one page of the latest 100 runs)."""
    branch = api(f"repos/{repo}")["default_branch"]
    runs = api(f"repos/{repo}/actions/runs?branch={quote(branch, safe='')}&status=completed&per_page=100").get("workflow_runs") or []
    newest = {}
    for r in runs:
        if r.get("head_branch") != branch or r.get("event") not in ("push", "schedule") or r.get("status") != "completed":
            continue
        wf = r.get("workflow_id")
        if wf is not None and (wf not in newest or (r.get("created_at") or "", r["id"]) > (newest[wf].get("created_at") or "", newest[wf]["id"])):
            newest[wf] = r
    red = [r for r in newest.values() if r.get("conclusion") in ("failure", "timed_out")]
    if not red:
        return []
    active = {w["id"] for w in api(f"repos/{repo}/actions/workflows?per_page=100").get("workflows", []) if w.get("state") == "active"}
    return sorted((r for r in red if r["workflow_id"] in active), key=lambda r: r["id"])


def scan_red(c, items, repo, state, by_item, budget, created):
    made = 0
    for r in red_runs(repo):
        key = f"{repo}#run{int(r['id'])}"
        if key in state.get("runs", {}) or made >= budget:
            continue
        # one story per workflow while it is open; once Done, a later red run files a new one (a green run clears nothing)
        if any(e.get("wf") == r["workflow_id"] and open_story(by_item, e) for e in state.get("runs", {}).values()):
            continue
        name = sanitize(r.get("name")) or f"workflow {int(r['workflow_id'])}"
        sha = re.sub(r"[^0-9a-f]", "", str(r.get("head_sha") or "").lower())[:7]
        body = (f"The latest run of a workflow on the default branch is red.\n\n- Run: https://github.com/{repo}/actions/runs/{int(r['id'])}\n"
                f"- Workflow: {int(r['workflow_id'])} `{name}`\n- Commit: {sha}\n- Conclusion: {r['conclusion']}")
        prio = priority_option(c, "High") if c["fields"].get("priority") else None

        def finish(item, number, prio=prio):
            if prio:
                set_single(c, item, *prio)
        entry = file_story(c, items, repo, key, "run", f"Fix failing main: {name}", body, finish, {"wf": r["workflow_id"]})
        by_item[entry["item"]] = {"closed": False, "column": "todo"}
        created.add(entry["item"])
        state.setdefault("runs", {})[key] = entry
        made += 1


def rate_pr(c, repo, number):
    """low only when every file is a manifest or lockfile and none is guarded or denied; otherwise (or when unreadable) medium."""
    try:
        files = rest(f"repos/{repo}/pulls/{number}/files")
    except SystemExit:
        return "medium"
    names = [n for f in files for n in (f.get("filename"), f.get("previous_filename")) if n]
    if not files or len(files) >= MAX_FILES or len(names) < len(files):
        return "medium"
    plain = all(any(fnmatch.fnmatchcase(os.path.basename(n).lower(), g) for g in LOCKFILES) for n in names)
    return "low" if plain and vet(c, [repo], names, ["**"]) is None else "medium"


def dep_finish(c, items, repo, n, item, sha):
    """Everything a dependency story needs, each step safe to repeat. The in-memory item is updated first and Waiting On is set before the
    Status, so a failure half way never leaves a story that a worker could be dispatched for (an empty Status reads as Todo)."""
    fields, url = c["fields"], f"https://github.com/{repo}/pull/{n}"
    for i in items:
        if i["item"] == item:
            i.update(column="pr_review", waiting=True, waitingOn="You", skipPlan=True, plan="Skip", pr=url)
    if fields["waiting"].get("you"):
        set_single(c, item, fields["waiting"]["id"], fields["waiting"]["you"])
    set_single(c, item, fields["status"]["id"], fields["status"]["options"]["pr_review"])
    if fields.get("plan"):
        set_text(c, item, fields["plan"], "Skip")
    set_text(c, item, fields["pr"], url)
    sync_links(c, item)
    rate_and_store(c, repo, n, item, sha)


def rate_and_store(c, repo, n, item, sha):
    rating = {"rating": rate_pr(c, repo, n), "column": "pr_review", "sha": sha}
    update_data(lambda d: d.setdefault("ratings", {}).__setitem__(item, rating))
    update_data(lambda d: d.get("intake", {}).get("prs", {}).get(f"{repo}#pr{n}", {}).__setitem__("sha", sha))


def repair(c, items):
    """Finish a dependency story whose creation was cut short (it is on the board, maybe still looking like Todo). Not throttled by
    intakeSeconds, but tried at most once a minute per story."""
    for key, p in list(load_data().get("intake", {}).get("pending", {}).items()):
        if p.get("kind") != "pr" or time.time() - _repaired.get(key, 0) < 60:
            continue
        _repaired[key] = time.time()
        issue = find_marked(p["repo"], f"<!-- cgp-intake:pr:{key} -->")
        item = next((i["item"] for i in items if issue and i["issueRepo"] == p["repo"] and i["number"] == issue["number"]), None)
        if item:
            dep_finish(c, items, p["repo"], p["pr"], item, p.get("sha"))
            complete(key, "pr", {"at": time.time(), "repo": p["repo"], "item": item, "number": issue["number"], "pr": p["pr"], "sha": p.get("sha")})


def scan_deps(c, items, repo, conf, state, by_item, budget, created):
    bots = {b.lower() for b in conf.get("bots", INTAKE_BOTS)}
    pulls = api(f"repos/{repo}/pulls?state=open&sort=created&direction=asc&per_page=100")
    made = 0
    for p in pulls:
        user, head, base = p.get("user") or {}, (p.get("head") or {}).get("repo") or {}, (p.get("base") or {}).get("repo") or {}
        n, sha = p["number"], (p.get("head") or {}).get("sha")
        key = f"{repo}#pr{n}"
        known = state.get("prs", {}).get(key)
        if known:  # a new head: the rating of the files it had no longer holds
            if sha and known.get("sha") != sha and open_story(by_item, known):
                rate_and_store(c, repo, n, known["item"], sha)
                known["sha"] = sha
            continue
        if made >= budget or p.get("draft") or user.get("type") != "Bot" \
                or (user.get("login") or "").lower() not in bots or not head.get("full_name") \
                or head.get("full_name") != base.get("full_name"):
            continue
        url = f"https://github.com/{repo}/pull/{n}"
        entry = file_story(c, items, repo, key, "pr", f"Dependency update: PR #{n}",
                           f"A bot opened {url}. Review and merge it there; this story is Done once it is merged or closed.",
                           lambda item, number, n=n, sha=sha: dep_finish(c, items, repo, n, item, sha), {"pr": n, "sha": sha})
        by_item[entry["item"]] = {"closed": False, "column": "pr_review"}
        created.add(entry["item"])
        state.setdefault("prs", {})[key] = entry
        made += 1


def close_merged(repo, state, by_item):
    """Close the intake issue of a dependency PR that was merged or closed (the snapshot then files it under Done)."""
    for e in state.get("prs", {}).values():
        if e.get("repo") == repo and open_story(by_item, e) and api(f"repos/{repo}/pulls/{e['pr']}").get("state") == "closed":
            gh("api", "-X", "PATCH", f"repos/{repo}/issues/{e['number']}", "-f", "state=closed", "-f", "state_reason=completed")
            by_item[e["item"]]["closed"] = True


def room(opts, repo, state, by_item):
    """How many more intake stories this repo may get now: under maxOpen open ones, and at most INTAKE_PER_CYCLE a scan."""
    open_now = sum(1 for k in ("runs", "prs") for e in state.get(k, {}).values() if e.get("repo") == repo and open_story(by_item, e))
    return min(opts.get("maxOpen", INTAKE_MAX_OPEN) - open_now, INTAKE_PER_CYCLE)


def run_intake(c, items, created=None):
    """One intake pass (the ids of the board items it creates are added to `created`). None when nothing was scanned (not due, or no
    repo opted in), else the list of per-repo errors."""
    created = set() if created is None else created
    by_item = {i["item"]: i for i in items}
    errors = []
    try:
        repair(c, items)
    except (SystemExit, Exception) as e:
        errors.append(f"repair: {e if isinstance(e, Exception) else 'a gh call failed (see stderr)'}")
    scanned = scan(c, items, by_item, created, errors)
    return errors if errors or scanned else None


def scan(c, items, by_item, created, errors):
    """The throttled part of run_intake; whether anything was scanned."""
    interval, now = c["settings"]["intakeSeconds"], time.time()
    key = c["board"]["id"]
    if now - _checked.get(key, 0) < interval:
        return False
    _checked[key] = now
    conf = {r: (repo_config(c, r, fresh=True).get("intake") or {}) for r in c["repos"]}  # fresh: a long-lived process sees config changes
    conf = {r: v for r, v in conf.items() if v.get("redMain") or v.get("dependencies")}
    if not conf:
        return False
    due = []

    def claim(d):  # compare-and-set: of several snapshots in the same window only one scans
        sec = d.setdefault("intake", {})
        at, t = sec.setdefault("scanAt", {}), time.time()
        due.extend(r for r in conf if t - at.get(r, 0) >= interval)
        at.update({r: t for r in due})
        for kind in ("runs", "prs"):
            sec[kind] = {k: v for k, v in sec.get(kind, {}).items() if t - v.get("at", t) < INTAKE_KEEP_SECONDS or open_story(by_item, v)}
        sec["pending"] = {k: v for k, v in sec.get("pending", {}).items() if t - v.get("at", t) < INTAKE_KEEP_SECONDS}
    update_data(claim)
    if not due:
        return False
    state = load_data().get("intake", {})
    for repo in due:
        try:
            opts = conf[repo]
            if opts.get("dependencies"):
                close_merged(repo, state, by_item)
            if opts.get("redMain") and room(opts, repo, state, by_item) > 0:
                scan_red(c, items, repo, state, by_item, room(opts, repo, state, by_item), created)
            if opts.get("dependencies") and room(opts, repo, state, by_item) > 0:
                scan_deps(c, items, repo, opts, state, by_item, room(opts, repo, state, by_item), created)
        except (SystemExit, Exception) as e:  # one repo's failure (a gh error, an unexpected answer) must not stop the others or the loop
            errors.append(f"{repo}: {e if isinstance(e, Exception) else 'a gh call failed (see stderr)'}")
    return True
