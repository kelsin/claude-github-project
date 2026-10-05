"""Pull requests: checks, CI waiting, merging."""
import json
import re
import time
from .util import Poll, die, out
from .gh import gh
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref, require_repo
from .session import set_phase


def pr_checks(repo, pr):
    """Checks list; [] when none are reported; None on a transient gh failure."""
    p = gh("pr", "checks", str(pr), "-R", repo, "--json", "name,bucket,link,workflow", check=False)
    if p.returncode in (0, 8):
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            return None
    return [] if "no checks reported" in (p.stderr + p.stdout) else None


def failed_logs(repo, failed):
    seen = set()
    for f in failed[:3]:
        m = re.search(r"/runs/(\d+)", f.get("link") or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            log = gh("run", "view", m.group(1), "-R", repo, "--log-failed", check=False).stdout
            f["log"] = "\n".join(log.strip().splitlines()[-60:])
    return failed


def cmd_ci_wait(a):
    a.repo = require_repo(cfg(), a.repo)
    pr = f"{a.repo}#{a.pr}"
    before = set_phase("ci", pr=pr)
    try:
        ci_wait(a)
    finally:
        if before is not False:  # waiting is over: back to what the worker was doing (it sets the next phase itself)
            set_phase(*before, pr=pr)


def ci_wait(a):
    poll, none_since = Poll(a.timeout, a.interval), time.time()
    while True:
        if a.sha:  # checks of an older head say nothing about the commit just pushed
            v = pr_view(a.repo, a.pr, check=False)
            if not v or v.get("headRefOid") != a.sha:
                none_since = time.time()
                if not poll.wait():
                    out({"state": "pending", "note": f"PR head is not {a.sha} yet", "head": (v or {}).get("headRefOid")})
                    return
                continue
        checks = pr_checks(a.repo, a.pr)
        if checks is None:  # transient gh error: never mistake it for "no CI"
            none_since = time.time()  # and the no-checks grace starts only once gh answers again
            if not poll.wait():
                out({"state": "pending", "note": "gh errors while polling"})
                return
            continue
        if checks:
            none_since = time.time()
        failed = [x for x in checks if x["bucket"] in ("fail", "cancel")]
        pending = [x for x in checks if x["bucket"] == "pending"]
        if failed:
            out({"state": "red", "failed": failed_logs(a.repo, failed)})
            return
        if checks and not pending:
            out({"state": "green", "checks": len(checks)})
            return
        if not checks and time.time() - none_since > a.grace:
            out({"state": "none", "note": "no CI checks reported; treat as green"})
            return
        if not poll.wait():
            out({"state": "pending", "pending": [x["name"] for x in pending]})
            return


def pr_view(repo, pr, check=True):
    p = gh("pr", "view", str(pr), "-R", repo, "--json",
           "state,isDraft,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,url,mergedAt,headRefOid,headRefName", check=check)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def cmd_pr_state(a):
    out(pr_view(require_repo(cfg(), a.repo), a.pr))


def record_reviewed(item, ref, sha=None):
    """Remember the commit the user is shown in PR Review: the only one (besides clean rebases, see allow_head) `merge` accepts."""
    sha = sha or (pr_view(*ref, check=False) or {}).get("headRefOid")
    if sha:
        def upd(d):
            d.setdefault("reviewed", {})[item] = {"pr": f"{ref[0]}#{ref[1]}", "sha": sha}
            d.setdefault("cleanRebase", {}).pop(item, None)
            d.setdefault("tainted", [])
            d["tainted"] = [i for i in d["tainted"] if i != item]
        update_data(upd)


def allow_head(item, sha):
    """A head that came from a clean, conflict-free rebase or branch update by this tool needs no new approval."""
    update_data(lambda d: d.setdefault("cleanRebase", {}).setdefault(item, []).append(sha))


def changed_since_review(item, ref, v):
    """None when the PR head is what the user reviewed; else a message. Without a record (a story approved before this check
    existed) the current head is taken as reviewed."""
    d = load_data()
    rec = d.get("reviewed", {}).get(item)
    key = f"{ref[0]}#{ref[1]}"
    head = v["headRefOid"]
    if not rec or rec["pr"] != key:
        record_reviewed(item, ref, head)
        return None
    if head == rec["sha"] or head in d.get("cleanRebase", {}).get(item, []):
        return None
    return (f"the PR head is {head[:8]} but you reviewed {rec['sha'][:8]}: code changed after review. Post a status comment "
            f"saying what changed and move the story to pr_review for re-review")


def merge_target(a):
    """Merging is only ever done for the story's own PR, and only while the user has it in PR Approved, at the commit they reviewed."""
    c = cfg()
    it = get_item(c, a.item)
    ref = parse_pr_ref(c, it["pr"])
    if it["column"] != "pr_approved" or not ref:
        die("refusing to merge: the story must be in pr_approved (set by the user) with a valid PR field")
    v = pr_view(*ref)
    if v["state"] == "OPEN" and v.get("headRefName") != f"cgp/{it['number']}":
        die(f"refusing to merge: the PR field points at a branch ({v.get('headRefName')}) this loop did not open (expected cgp/{it['number']})")
    if v["state"] == "OPEN" and v["isDraft"]:
        die("refusing to merge: the PR is a draft")
    a.repo, a.pr, a.view = ref[0], ref[1], v  # the view just fetched serves merge-wait's first poll
    a.delegated = it["autoApprove"]["pr"]  # the user let the agent approve this PR: there was no review to hold it to
    msg = changed_since_review(a.item, ref, v) if v["state"] == "OPEN" and not a.delegated else None
    if msg:
        cancel_auto_merge(*ref)  # an armed auto-merge would otherwise merge the unreviewed push once CI is green
        die("refusing to merge: " + msg, code=7)
    set_phase("merging", item=a.item)
    return a


def cancel_auto_merge(repo, pr):
    return gh("pr", "merge", str(pr), "-R", repo, "--disable-auto", check=False).returncode == 0


def cmd_merge(a):
    if a.cancel:
        c = cfg()
        ref = parse_pr_ref(c, get_item(c, a.item)["pr"])
        if not ref:
            die("story has no valid PR field set")
        out({"cancelled": cancel_auto_merge(*ref)})
        return
    a = merge_target(a)
    p = gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--auto", "--delete-branch", check=False)
    if p.returncode:
        checks = pr_checks(a.repo, a.pr)  # no auto-merge: this merges at once, so never over red or pending checks
        if checks is None or any(x["bucket"] != "pass" and x["bucket"] != "skipping" for x in checks):
            out({"requested": False, "error": "auto-merge is unavailable and the checks are not all green yet; run ci-wait, then merge-wait merges when the PR is clean"})
            return
        p2 = gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--delete-branch", check=False)
        if p2.returncode:
            out({"requested": False, "error": (p2.stderr or p.stderr).strip()})
            return
    out({"requested": True})


def cmd_merge_wait(a):
    a = merge_target(a)
    poll = Poll(a.timeout, a.interval)
    while True:
        v, a.view = a.view or pr_view(a.repo, a.pr, check=False), None
        if v is None:  # a transient gh failure must not end the wait (ci-wait is as tolerant)
            if not poll.wait():
                out({"state": "pending", "note": "gh errors while polling"})
                return
            continue
        if v["state"] == "MERGED":
            out({"state": "merged"})
            return
        if v["state"] == "CLOSED":
            out({"state": "closed"})
            return
        if v["mergeable"] == "CONFLICTING" or v["mergeStateStatus"] == "DIRTY":
            out({"state": "conflict", "pr": v})
            return
        if any(x["bucket"] in ("fail", "cancel") for x in (pr_checks(a.repo, a.pr) or [])):
            out({"state": "ci-red", "pr": v})
            return
        changed = v["state"] == "OPEN" and not a.delegated and changed_since_review(a.item, (a.repo, a.pr), v)
        if changed:
            cancel_auto_merge(a.repo, a.pr)
            out({"state": "changed", "note": changed, "pr": v})
            return
        if v["mergeStateStatus"] == "BEHIND":
            if gh("pr", "update-branch", str(a.pr), "-R", a.repo, check=False).returncode == 0:
                moved = pr_view(a.repo, a.pr, check=False)  # the update commit is this tool's own: no re-approval
                if moved:
                    allow_head(a.item, moved["headRefOid"])
        elif v["reviewDecision"] in ("CHANGES_REQUESTED", "REVIEW_REQUIRED") and v["mergeStateStatus"] == "BLOCKED":
            out({"state": "blocked", "pr": v})
            return
        elif v["mergeStateStatus"] == "CLEAN" and not v.get("autoMergeRequest"):
            gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--delete-branch", check=False)
        if not poll.wait():
            out({"state": "pending", "pr": v})
            return
