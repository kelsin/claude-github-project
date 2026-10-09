"""House rules from review history: `cgp rules propose` clusters the points trusted people repeat in review comments on merged cgp PRs
and writes a PROPOSED rules file under the cgp home, never into a repo. The user reviews it and commits .cgp-rules.md to the default
branch themselves; workers read it from there (repoconf.rules_text). No LLM and no network beyond gh: the grouping is mechanical."""
import json
import os
import re
from .consts import HOME
from .util import age_seconds, die, now_iso, out, printable
from .gh import gh, is_agent, is_bot, rest, trusted
from .store import cfg, load_data, replace_retry, update_data
from .board import require_repo
from .repoconf import RULES_CAP, rules_text

MIN_LENGTH = 15  # shorter comments ("LGTM", "nit") say nothing
SIMILAR = 0.5  # Jaccard similarity of two comments' words for the same cluster
STUB_LENGTH = 160
PROPOSED_HEADING = "## Proposed from review history"
HEADER = "<!-- Proposed by cgp. Review every line; this text is pasted into agent prompts. -->"
STOPWORDS = frozenset("""a about above after again all also an and any are as at be because been before being but by can could did do does
    don done for from had has have here how i if in into is it its just like make more most no not of on one only or other our out over
    should so some such than that the their them then there these they this those to too up use used was we were what when where which
    while who will with would you your""".split())
UNSAFE = re.compile(r"https?://|www\.|```|\b(curl|wget|sudo|eval|chmod|ssh)\b|\|\s*(ba)?sh\b|\$\(|"
                    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|instructions|rules)\b|system prompt", re.I | re.S)


def plain(body):
    """A comment without what is not prose: code blocks (suggestions included), inline code, quoted lines and URLs."""
    body = re.sub(r"```.*?(```|$)", " ", body or "", flags=re.S)
    body = re.sub(r"^\s*>.*$", " ", body, flags=re.M)
    body = re.sub(r"`[^`]*`|https?://\S+", " ", body)
    body = re.sub(r"<!--.*?(-->|$)", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return " ".join(printable(body).split())


def words(body):
    text = plain(body).lower()
    return frozenset(w for w in re.findall(r"[a-z][a-z0-9_-]+", text) if w not in STOPWORDS) if len(text) >= MIN_LENGTH else frozenset()


def cluster(comments):
    """Greedy grouping in the given order: a comment joins the first cluster whose first comment it resembles (Jaccard >= SIMILAR).
    comments: [(pr number, body)]; returns [{"words", "items": [(pr, body)]}]."""
    groups = []
    for pr, body in comments:
        ws = words(body)
        if not ws:
            continue
        for g in groups:
            if len(ws & g["words"]) / len(ws | g["words"]) >= SIMILAR:
                g["items"].append((pr, body))
                break
        else:
            groups.append({"words": ws, "items": [(pr, body)]})
    return groups


def recurring(groups, minimum):
    return [g for g in groups if len(g["items"]) >= minimum and len({pr for pr, _ in g["items"]}) >= 2]


def stub(group):
    """The shortest safe example of a cluster as one short line; None when every example looks like a command, link or injection."""
    safe = [plain(b) for _, b in group["items"] if not UNSAFE.search(b) and "<" not in b]
    safe = [s for s in safe if len(s) >= MIN_LENGTH and "[house-rules]" not in s]
    return min(safe, key=len)[:STUB_LENGTH] if safe else None


def collect(repo, limit):
    """Trusted human review comments on merged cgp PRs: [(pr number, body)]. One non-paginated list call (rest() always paginates)."""
    pulls = json.loads(gh("api", f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100").stdout)
    merged = [p for p in pulls if p.get("merged_at") and (p.get("head") or {}).get("ref", "").startswith("cgp/")][:max(1, limit)]
    found = []
    for p in merged:
        n = p["number"]
        reviews = [r for r in rest(f"repos/{repo}/pulls/{n}/reviews") if (r.get("body") or "").strip()]
        for cm in rest(f"repos/{repo}/pulls/{n}/comments") + reviews + rest(f"repos/{repo}/issues/{n}/comments"):
            if not is_bot(cm) and not is_agent(cm) and trusted(repo, cm):
                found.append((n, cm.get("body") or ""))
    return found, len(merged)


def proposal_path(repo, out_path=None):
    return out_path or os.path.join(HOME, "proposals", repo.replace("/", "__"), "cgp-rules.proposed.md")


def write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        if os.path.lexists(tmp):  # a stale file or symlink: O_EXCL would refuse it
            os.remove(tmp)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(tmp, flags, 0o600), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        replace_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def unheaded(text):
    return text[len(HEADER):].lstrip("\n") if text.startswith(HEADER) else text


def render(existing, bullets):
    """Header, the existing rules, then the new bullets that still fit in RULES_CAP; (text, how many bullets were added).
    Bullets whose text is already in the existing rules are skipped, so running again after committing a proposal adds nothing."""
    existing = unheaded(existing)
    text = f"{HEADER}\n\n{existing}\n" if existing else f"{HEADER}\n"
    if existing and len(existing) >= RULES_CAP:
        return text + "\n<!-- The rules file is already at its size limit, so nothing was added. -->\n", 0
    added = 0
    for b in bullets:
        if b.split("\n", 1)[0][2:] in existing:
            continue
        piece = b + "\n"
        if not added and PROPOSED_HEADING not in text:
            piece = f"\n{PROPOSED_HEADING}\n\n" + piece
        if len(text) + len(piece) > RULES_CAP:
            break
        text += piece
        added += 1
    return text, added


def propose(c, repo, limit, minimum, out_path):
    if not c["repos"].get(repo):
        return {"proposed": 0, "note": f"no local clone of {repo}; run: cgp repo-path {repo} <path>"}
    comments, prs = collect(repo, limit)
    bullets = []
    for g in recurring(cluster(comments), minimum):
        line = stub(g)
        if line:
            bullets.append(f"- {line}\n  (seen in PRs {', '.join('#' + str(n) for n in sorted({pr for pr, _ in g['items']}))})")
    if not bullets:
        return {"proposed": 0, "prs": prs, "comments": len(comments)}
    text, added = render(rules_text(c, repo) or "", bullets)
    path = proposal_path(repo, out_path)
    write(path, text)
    res = {"proposed": added, "path": path, "prs": prs, "comments": len(comments)}
    return res if added else {**res, "note": "the rules file is already at its size limit; nothing was added"}


def rules_due(c, data, repo):
    """Whether `rules propose` is past its interval for this repo (rulesProposeDays, 0 = never). No network."""
    days = c["settings"].get("rulesProposeDays", 0)
    at = data.get("rulesProposedAt", {}).get(repo)
    return bool(days) and (not at or age_seconds(at) >= days * 86400)


def pending(c):
    """Repos whose proposal file differs from the default branch's rules file: [{"repo", "path"}]."""
    rows = []
    for repo in c["repos"]:
        path = proposal_path(repo)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                if unheaded(f.read().strip()) != unheaded(rules_text(c, repo, fresh=True) or ""):
                    rows.append({"repo": repo, "path": path})
    return rows


def cmd_rules(a):
    c = cfg()
    repo = require_repo(c, a.repo) if a.repo else next(iter(c["repos"]), None) if len(c["repos"]) == 1 else None
    if not repo:
        die("name the repo: cgp rules propose --repo <owner/name> (see: cgp repos)")
    if a.if_due and not rules_due(c, load_data(), repo):
        out({"proposed": 0, "skipped": "not due"})
        return
    try:
        res = propose(c, repo, a.limit, a.min, a.out)
    finally:  # every outcome counts as a run, errors included, so a broken repo is not retried on every cycle
        update_data(lambda d: d.__setitem__("rulesProposedAt", {**d.get("rulesProposedAt", {}), repo: now_iso()}))
    out(res)

