"""Flaky CI capture: when `cgp ci-triage` finds a failure that a rerun fixed, each failed job is counted by key (repo + job name) and
filed once as a Todo story on Hold with the cgp-flaky label, so flakes get fixed instead of rerun. Names come from the PR's CI and can be
written by its author: they are sanitized, and no log text ever reaches an issue. Everything here is best effort; ci-triage's verdict
never depends on it."""
import hashlib
import json
import re
from .util import age_seconds, now_iso
from .gh import gh, post_comment
from .store import cfg, update_data
from .intake import create_story, priority_option

LABEL = "cgp-flaky"
FLAKY_FILE_AT = 1  # flaky observations before a story is filed (raise it if the stories prove noisy)
MAX_FLAKED = 3  # more flaked jobs than this in one run look like an infrastructure outage: nothing is filed
CLAIM_TTL = 600  # seconds after which a claim of a caller that never finished can be retaken
KEEP = 200  # entries kept per repo that hold no story and no live claim
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_MARKUP = re.compile(r"[`@#<>]")
_MATRIX = re.compile(r"\s*\([^()]*\)\s*$")
_PARAM = re.compile(r"\[[^\[\]]*\]\s*$")


def clean(text, cap):
    """A name safe for a public issue: escape and control characters, backticks, @, #, < and > removed, capped."""
    return _MARKUP.sub("", _CTRL.sub(" ", _ANSI.sub("", str(text or "")))).strip()[:cap]


def flake_key(repo, job):
    """12 hex characters per repo and job; a matrix suffix ` (...)` is dropped so matrix legs share a key."""
    return hashlib.sha1(f"{repo}\n{_MATRIX.sub('', clean(job, 100))}".encode()).hexdigest()[:12]


def flake_body(job, workflow, tests, link, count, key):
    lines = [f"The CI job `{clean(job, 100)}` (workflow `{clean(workflow, 100)}`) failed and then passed on a rerun without a code change.", ""]
    if tests:
        lines += ["Failing tests:"] + [f"- `{_PARAM.sub('', clean(t, 200))}`" for t in tests] + [""]
    if re.fullmatch(r"https://github\.com/[\w./-]+", link or ""):
        lines.append(f"Failed run: {link}")
    lines += [f"Flaky observations so far: {count}", "", f"<!-- cgp-flake:{key} -->"]
    return "\n".join(lines)


def comment_at(count):
    return count in (2, 5) or (count >= 10 and count % 10 == 0)


def capture(repo, pr, head, jobs):
    """Count and file the flaky jobs of one triage; returns the fields ci-triage adds to its output."""
    res = {"flakes": [], "filed": []}
    errors = []
    if len(jobs) > MAX_FLAKED:
        res["skipped"] = "many"
        return res
    try:
        c = cfg()
        hold = priority_option(c, "Hold")
    except (SystemExit, Exception) as e:
        return dict(res, fileError=str(e) or "no Hold priority")
    for j in jobs:
        key = flake_key(repo, j["name"])
        entry = {"key": key, "job": clean(j["name"], 100), "tests": [clean(t, 200) for t in j.get("tests", [])]}
        mine = {}
        try:
            note_observation(repo, pr, head, key, entry, mine)
            if mine.get("claimed"):
                file_story(c, repo, key, j, entry, hold, mine["count"], res)
            elif mine.get("comment"):
                comment(repo, entry["issue"], j, mine["count"])
        except (SystemExit, Exception) as e:
            errors.append(str(e) or "gh failed")
        res["flakes"].append(entry)
    if errors:
        res["fileError"] = "; ".join(errors)[:300]
    return res


def note_observation(repo, pr, head, key, entry, mine):
    """Count this flaky observation (once per head sha) and decide, under the lock, who files the story: only the caller that
    claims the entry does; a fresh claim of another caller means skip."""
    def upd(d):
        e = d.setdefault("flakes", {}).setdefault(repo, {}).setdefault(key, {"count": 0, "issue": None})
        if e.get("lastSha") != head:
            e.update(count=e["count"] + 1, lastSha=head, lastPr=pr, lastSeen=now_iso(), job=entry["job"], tests=entry["tests"])
            mine["fresh"] = True
        mine["count"] = e["count"]
        if e["count"] >= FLAKY_FILE_AT and (not e["issue"] or (e["issue"] == "pending" and age_seconds(e.get("claimedAt")) > CLAIM_TTL)):
            e.update(issue="pending", claimedAt=now_iso())
            mine["claimed"] = True
        elif e["issue"] and e["issue"] != "pending":
            mine["comment"] = mine.get("fresh") and comment_at(e["count"])
        mine["issue"] = e["issue"]
    update_data(upd)
    entry.update(count=mine["count"], issue=None if mine["issue"] == "pending" else mine["issue"])


def file_story(c, repo, key, j, entry, hold, count, res):
    def store(number):  # as soon as the issue exists, before it goes on the board: a partial failure is adopted next time
        url = f"https://github.com/{repo}/issues/{number}"
        entry["issue"] = url
        update_data(lambda d: d["flakes"][repo][key].update(issue=url, claimedAt=None))
    try:
        story = create_story(c, repo, f"Flaky: {entry['job']}", flake_body(j["name"], j.get("workflow"), entry["tests"], j.get("link"), count, key),
                             hold, on_issue=store, labels=[LABEL], marker=f"<!-- cgp-flake:{key} -->")
        res["filed"].append(story["url"])
    finally:
        if not entry["issue"] or entry["issue"] == "pending":  # nothing was created: let the next flake retry
            entry["issue"] = None
            update_data(lambda d: d["flakes"][repo][key].update(issue=None, claimedAt=None))


def comment(repo, url, j, count):
    number = url.rsplit("/", 1)[-1]
    if json.loads(gh("api", f"repos/{repo}/issues/{number}").stdout).get("state") == "closed":
        return  # a closed story is not refiled or nagged: reopen it by hand if the flake is back
    link = j.get("link") if re.fullmatch(r"https://github\.com/[\w./-]+", j.get("link") or "") else ""
    post_comment(repo, number, f"Flaked again ({count} observations so far)." + (f" Failed run: {link}" if link else ""))


def prune(d):
    """Drop flake state that can never matter again: keep every entry with a story or a live claim, and the newest KEEP others."""
    for repo, entries in list(d.get("flakes", {}).items()):
        live = {k: e for k, e in entries.items() if e.get("issue") and (e["issue"] != "pending" or age_seconds(e.get("claimedAt")) <= CLAIM_TTL)}
        rest_ = sorted((k for k in entries if k not in live), key=lambda k: entries[k].get("lastSeen") or "", reverse=True)
        kept = {**live, **{k: entries[k] for k in rest_[:KEEP]}}
        if kept:
            d["flakes"][repo] = kept
        else:
            del d["flakes"][repo]
