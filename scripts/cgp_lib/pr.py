"""Pull requests: checks, CI waiting, merging."""
import json
import os
import re
import time
from .util import Poll, die, out
from .gh import gh
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref, require_repo
from .gitutil import default_ref
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
           "state,isDraft,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,url,mergedAt,headRefOid,headRefName,"
           "isCrossRepository,baseRefName", check=check)
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


def is_approved_head(item, sha):
    """True when `sha` is the commit the user reviewed, or one made from it by a clean rebase or branch update of this tool.
    A rebase of any other local commit must never be passed off as approved."""
    d = load_data()
    rec = d.get("reviewed", {}).get(item)
    return bool(rec and (sha == rec["sha"] or sha in d.get("cleanRebase", {}).get(item, [])))


def changed_since_review(item, ref, v):
    """None when the PR head is what the user reviewed; else a message. No record for this PR is a refusal too: the way out is
    moving the story to PR Review, which records the head the user is shown."""
    d = load_data()
    rec = d.get("reviewed", {}).get(item)
    key = f"{ref[0]}#{ref[1]}"
    head = v["headRefOid"]
    if not rec or rec["pr"] != key:
        return ("no review of this PR is on record. Move the story to pr_review so the commit you review is recorded, "
                "then approve it again")
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
    if v["state"] == "OPEN" and v.get("isCrossRepository") is not False:  # a fork can use any branch name, cgp/<n> included
        die("refusing to merge: the PR comes from a fork (or its origin could not be read), not a branch of this repo")
    default = default_branch(c, ref[0])
    if v["state"] == "OPEN" and default and v.get("baseRefName") != default:
        die(f"refusing to merge: the PR targets {v.get('baseRefName')}, not the default branch {default}")
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


def default_branch(c, repo):
    """Name of the repo's default branch from the local clone; None when there is no clone to ask."""
    path = c["repos"].get(repo)
    try:
        return default_ref(path).split("/", 1)[1] if path and os.path.isdir(path) else None
    except SystemExit:
        return None


def cancel_auto_merge(repo, pr):
    """Disarm auto-merge and check that it is off (a failed disable would leave an unreviewed push free to merge). A repo without
    auto-merge has nothing to disarm: that is success too. Never raises, so a move is never blocked by it."""
    for n in range(3):
        gh("pr", "merge", str(pr), "-R", repo, "--disable-auto", check=False)
        v = pr_view(repo, pr, check=False)
        if v and not v.get("autoMergeRequest"):
            return True
        if n < 2:
            time.sleep(0.3 * (n + 1))
    return False


def pin(v):
    """The `--match-head-commit` argument that makes a merge fail when the head is not the commit that was just looked at."""
    return ["--match-head-commit", v["headRefOid"]]


def cmd_merge(a):
    if a.cancel:
        c = cfg()
        ref = parse_pr_ref(c, get_item(c, a.item)["pr"])
        if not ref:
            die("story has no valid PR field set")
        out({"cancelled": cancel_auto_merge(*ref)})
        return
    a = merge_target(a)
    p = gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--auto", "--delete-branch", *pin(a.view), check=False)
    if p.returncode:
        checks = pr_checks(a.repo, a.pr)  # no auto-merge: this merges at once, so never over red or pending checks
        if checks is None or any(x["bucket"] != "pass" and x["bucket"] != "skipping" for x in checks):
            out({"requested": False, "error": "auto-merge is unavailable and the checks are not all green yet; run ci-wait, then merge-wait merges when the PR is clean"})
            return
        p2 = gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--delete-branch", *pin(a.view), check=False)
        if p2.returncode:
            out({"requested": False, "error": (p2.stderr or p.stderr).strip()})
            return
    out({"requested": True})


def still_approved(c, item):
    """False only when the board says the story is no longer in PR Approved (the user took the approval back); a failed read
    says nothing, so the caller keeps going."""
    try:
        return get_item(c, item)["column"] == "pr_approved"
    except SystemExit:
        return True


def update_branch(repo, pr, sha):
    """Merge the base branch into the PR branch, but only while its head is `sha` (a push made meanwhile is not touched)."""
    return gh("api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/update-branch", "-f", f"expected_head_sha={sha}", check=False).returncode == 0


def cmd_merge_wait(a):
    a = merge_target(a)
    c = cfg()
    poll = Poll(a.timeout, a.interval)
    last_head = None
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
        if not still_approved(c, a.item):  # taken back while we waited: nothing may merge on the old approval
            now = pr_view(a.repo, a.pr, check=False)  # a merge may just have moved the card to Done
            if now and now["state"] == "MERGED":
                out({"state": "merged"})
                return
            cancel_auto_merge(a.repo, a.pr)
            out({"state": "revoked", "note": "the story is no longer in pr_approved; auto-merge was disarmed"})
            return
        if v["mergeable"] == "CONFLICTING" or v["mergeStateStatus"] == "DIRTY":
            out({"state": "conflict", "pr": v})
            return
        checks = pr_checks(a.repo, a.pr)
        if any(x["bucket"] in ("fail", "cancel") for x in (checks or [])):
            out({"state": "ci-red", "pr": v})
            return
        changed = v["state"] == "OPEN" and not a.delegated and changed_since_review(a.item, (a.repo, a.pr), v)
        if changed:
            cancel_auto_merge(a.repo, a.pr)
            out({"state": "changed", "note": changed, "pr": v})
            return
        if last_head and v["headRefOid"] != last_head:  # the head moved (branch update, a delegated fix): the armed merge was for the old one
            gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--auto", "--delete-branch", *pin(v), check=False)
        last_head = v["headRefOid"]
        if v["mergeStateStatus"] == "BEHIND":
            if update_branch(a.repo, a.pr, v["headRefOid"]):
                # the REST call only queues the update: wait for the new head. It is this tool's own commit, so no re-approval
                for _ in range(8):
                    moved = pr_view(a.repo, a.pr, check=False)
                    if moved and moved["headRefOid"] != v["headRefOid"]:
                        if a.delegated or is_approved_head(a.item, v["headRefOid"]):
                            allow_head(a.item, moved["headRefOid"])
                        break
                    time.sleep(1)
        elif v["reviewDecision"] in ("CHANGES_REQUESTED", "REVIEW_REQUIRED") and v["mergeStateStatus"] == "BLOCKED":
            out({"state": "blocked", "pr": v})
            return
        elif (v["mergeStateStatus"] == "CLEAN" and not v.get("autoMergeRequest") and checks is not None
              and all(x["bucket"] in ("pass", "skipping") for x in checks)):
            gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--delete-branch", *pin(v), check=False)
        if not poll.wait():
            out({"state": "pending", "pr": v})
            return
