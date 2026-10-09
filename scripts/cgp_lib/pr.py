"""Pull requests: checks, CI waiting, merging."""
import json
import os
import re
import time
from .util import Poll, die, out
from urllib.parse import quote
from .gh import gh, rest
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref, require_repo
from .gitutil import default_ref
from .session import set_phase
from .flakes import capture, clean


def checks_green(checks):
    """Every check passed or was skipped (pr_checks' None, a transient gh failure, is never green)."""
    return checks is not None and all(x["bucket"] in ("pass", "skipping") for x in checks)


def ci_state(checks):
    """red / pending / green / none for a checks list (pr_checks' result, not None)."""
    if any(x["bucket"] in ("fail", "cancel") for x in checks):
        return "red"
    if any(x["bucket"] == "pending" for x in checks):
        return "pending"
    return "green" if checks else "none"


def pr_checks(repo, pr):
    """Checks list; [] when none are reported; None on a transient gh failure."""
    p = gh("pr", "checks", str(pr), "-R", repo, "--json", "name,bucket,link,workflow", check=False)
    if p.returncode in (0, 8):
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            return None
    return [] if "no checks reported" in (p.stderr + p.stdout) else None


def run_id(link):
    m = re.search(r"/runs/(\d+)", link or "")
    return m.group(1) if m else None


def failed_logs(repo, failed, limit=3):
    """Attach the last 60 log lines of each failed run (once per run) to the first `limit` failed checks (all when None)."""
    seen = set()
    for f in failed[:limit]:
        rid = run_id(f.get("link"))
        if rid and rid not in seen:
            seen.add(rid)
            log = gh("run", "view", rid, "-R", repo, "--log-failed", check=False).stdout
            f["log"] = "\n".join(log.strip().splitlines()[-60:])
    return failed


TEST_FORMATS = (re.compile(r"FAILED (\S+::[^\s]+)"), re.compile(r"(?<!--- )FAIL: (\w+ \([\w.]+\))"), re.compile(r"^[^\t\n]*\t[^\t\n]*\t\S+ +● ([\w .›/:-]{1,200}?) *$", re.M),
                re.compile(r"--- FAIL: (\S+)"), re.compile(r"test (\S+) \.\.\. FAILED"))


def extract_tests(log):
    """Names of failing tests found in a CI log (pytest, unittest, jest, go, cargo): at most 20, 200 characters each. Labels only."""
    found = []
    for rx in TEST_FORMATS:
        for name in rx.findall(log or ""):
            name = clean(name, 200)
            if name and name not in found:
                found.append(name)
    return found[:20]


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
        state = ci_state(checks)
        failed = [x for x in checks if x["bucket"] in ("fail", "cancel")]
        pending = [x for x in checks if x["bucket"] == "pending"]
        if state == "red":
            out({"state": "red", "failed": failed_logs(a.repo, failed)})
            return
        if state == "green":
            out({"state": "green", "checks": len(checks)})
            return
        if state == "none" and time.time() - none_since > a.grace:
            out({"state": "none", "note": "no CI checks reported; treat as green"})
            return
        if not poll.wait():
            out({"state": "pending", "pending": [x["name"] for x in pending]})
            return


RERUNS_PER_PR = 2
RERUN_MARGIN = 15  # seconds a triage needs left, once the checks settled, to reserve a rerun, make it and look at it


def main_failures(repo, base):
    """Names of the jobs whose latest check run on the PR's base branch failed or timed out (cancelled is concurrency, not breakage).
    Matching is by name only. An unreadable or pending main counts as green."""
    try:
        runs = [r for chunk in rest(f"repos/{repo}/commits/{quote(base, safe='')}/check-runs") for r in chunk.get("check_runs", [])]
    except SystemExit:
        return set()
    return {r["name"] for r in runs if r.get("conclusion") in ("failure", "timed_out")}


def settled(a, poll, names=None):
    """Poll until none of the checks (those named `names`, else all) is pending: (checks, "ok"), or (None, "pending"/"unknown") at the deadline."""
    seen_gh = False
    while True:
        checks = pr_checks(a.repo, a.pr)
        seen_gh = seen_gh or checks is not None
        if checks is not None and not any(x["bucket"] == "pending" and (names is None or x["name"] in names) for x in checks):
            return checks, "ok"
        if not poll.wait():
            return None, "pending" if seen_gh else "unknown"


def named_red(checks, names):
    return any(x["bucket"] in ("fail", "cancel") for x in checks if x["name"] in names)


def reserve_rerun(prk, head, runs, jobs):
    """Record the rerun BEFORE making it (a crash can then never allow a second one); None when it is allowed, else why not."""
    res = []

    def upd(d):
        recs = d.setdefault("reruns", {}).setdefault(prk, {})
        if head in recs or len(recs) >= RERUNS_PER_PR:
            res.append("this commit was rerun already" if head in recs else f"{RERUNS_PER_PR} reruns used on this PR")
        else:
            recs[head] = {"runs": runs, "jobs": jobs}
    update_data(upd)
    return res[0] if res else None


def cmd_ci_triage(a):
    a.repo = require_repo(cfg(), a.repo)
    pr = f"{a.repo}#{a.pr}"
    before = set_phase("ci", pr=pr)
    try:
        ci_triage(a)
    finally:
        if before is not False:
            set_phase(*before, pr=pr)


def ci_triage(a):
    """After ci-wait said red: flaky (green after one rerun), main-broken (the base branch fails the same jobs) or real. Only the
    check results and the rerun decide; log text is never read for a verdict. Every failed check counts, not just the rerun ones."""
    poll, prk = Poll(a.timeout, a.interval), f"{a.repo}#{a.pr}"
    v = pr_view(a.repo, a.pr, check=False)
    head = (v or {}).get("headRefOid")
    if not head:
        return out({"verdict": "unknown", "note": "gh could not read the PR"})
    if a.sha and a.sha != head:
        return out({"verdict": "stale", "head": head, "note": f"PR head is not {a.sha}"})
    checks, state = settled(a, poll)
    if checks is None:
        return out({"verdict": state, "head": head})
    rec = load_data().get("reruns", {}).get(prk, {}).get(head)
    done = rec["jobs"] if rec else []
    left = rec.get("pendingJobs", []) if rec else []  # failed runs an earlier call did not manage to rerun
    base = v.get("baseRefName") or "main"
    failed = [x for x in checks if x["bucket"] in ("fail", "cancel")]
    red_names = {x["name"] for x in failed}
    if rec and not named_red(checks, {j["name"] for j in done}) and not any(j["name"] in red_names for j in left):
        return settle(a, head, done, done_jobs(done, "pass"), failed, base)  # a rerun of this commit is recorded and came out green
    if not failed:
        return out({"verdict": "none", "head": head, "note": "no failed checks"})
    if poll.deadline - time.time() < min(RERUN_MARGIN, a.timeout / 2):  # too little time left to rerun and look at it once: a kill would leave a reservation without a rerun
        return out({"verdict": "pending", "head": head, "note": "too little time left to rerun; run ci-triage again"})
    failed_logs(a.repo, failed, limit=None)
    logs = {run_id(f.get("link")): f["log"] for f in failed if "log" in f}
    red = main_failures(a.repo, base)
    jobs = [{"name": f["name"], "workflow": f.get("workflow") or "", "runId": run_id(f.get("link")), "link": f.get("link") or "",
             "mainRed": f["name"] in red, "rerun": "skipped", "tests": extract_tests(logs.get(run_id(f.get("link")))),
             **({"log": f["log"]} if "log" in f else {})} for f in failed]
    todo = [j for j in jobs if not j["mainRed"]]
    if not todo:
        return out({"verdict": "main-broken", "head": head, "jobs": jobs})
    if rec and named_red(checks, {j["name"] for j in done}):
        return out({"verdict": "real", "head": head, "jobs": jobs, "note": "still red after the rerun"})
    if rec:  # a recorded rerun was partial: only its remaining runs are rerun now, under the reservation already made
        saved = [j for j in left if j["name"] in red_names]
        if not saved:
            return out({"verdict": "real", "head": head, "jobs": jobs, "note": "still red after the rerun"})
        done_saved, resume = done, True
    else:
        if any(not j["runId"] for j in todo):  # a status no one can rerun
            return out({"verdict": "real", "head": head, "jobs": jobs, "note": "a failed check has no workflow run to rerun"})
        saved = [{k: j[k] for k in ("name", "workflow", "runId", "link", "tests")} for j in todo]
        why = reserve_rerun(prk, head, sorted({j["runId"] for j in todo}), saved)
        if why:
            return out({"verdict": "real", "head": head, "jobs": jobs, "note": f"not rerun: {why}"})
        done_saved, resume = [], False
    err = rerun_runs(a, prk, head, saved, done_saved, resume)
    if err:
        return out(dict(err, head=head, jobs=jobs))
    saved = done_saved + saved
    names, seen_pending = {j["name"] for j in saved}, False
    while True:  # right after the rerun gh may still show the old failure: red counts only once a pending state was seen
        checks = pr_checks(a.repo, a.pr)
        if checks is not None:
            running = any(x["bucket"] == "pending" for x in checks)
            seen_pending = seen_pending or running
            if not running and not named_red(checks, names):
                return settle(a, head, saved, [dict(j, rerun="pass" if not j["mainRed"] else "skipped") for j in jobs],
                              [x for x in checks if x["bucket"] in ("fail", "cancel")], base)
            if not running and seen_pending:
                return out({"verdict": "real", "head": head, "jobs": [dict(j, rerun="fail" if not j["mainRed"] else "skipped") for j in jobs]})
        if not poll.wait():
            return out({"verdict": "pending", "head": head, "jobs": jobs, "note": "rerun still running; run ci-triage again"})


def done_jobs(saved, rerun):
    return [dict(j, rerun=rerun, mainRed=False) for j in saved]


def record_reran(prk, head, reran, left):
    """Rewrite the rerun record to the jobs actually rerun, plus the jobs still to rerun."""
    update_data(lambda d: d["reruns"][prk][head].update(runs=sorted({j["runId"] for j in reran}), jobs=reran, pendingJobs=left))


def rerun_runs(a, prk, head, saved, done_saved, resume):
    """Rerun the failed jobs of every run of `saved`, once each, never retried. A failure leaves the record listing exactly the
    runs rerun so far (`done_saved` plus these) and the rest as pendingJobs for a later call; None when all went through."""
    ok = []
    for rid in sorted({j["runId"] for j in saved}):
        p = gh("run", "rerun", rid, "-R", a.repo, "--failed", check=False)
        if p.returncode:
            reran = done_saved + [j for j in saved if j["runId"] in ok]
            if reran:
                record_reran(prk, head, reran, [j for j in saved if j["runId"] not in ok])
            elif not resume:
                update_data(lambda d: d["reruns"][prk].pop(head, None))
            err = (p.stderr or p.stdout).strip()[:300]
            return {"verdict": "pending" if "409" in err or reran else "unknown", "note": f"gh run rerun {rid} failed: {err}"}
        ok.append(rid)
    if resume:
        record_reran(prk, head, done_saved + saved, [])
    return None


def settle(a, head, saved, jobs, others, base):
    """The rerun jobs passed. Any other failed check decides over the flake: all of them red on the base branch is main-broken, else
    real. The flake is captured either way."""
    names = {j["name"] for j in saved}
    others = [x for x in others if x["name"] not in names]
    res = {"verdict": "flaky", "head": head, "jobs": jobs}
    if others:
        red = main_failures(a.repo, base)
        known = {j["name"] for j in jobs}
        res["jobs"] = jobs + [{"name": x["name"], "workflow": x.get("workflow") or "", "runId": run_id(x.get("link")), "link": x.get("link") or "",
                               "mainRed": x["name"] in red, "rerun": "skipped", "tests": []} for x in others if x["name"] not in known]
        res["verdict"] = "main-broken" if all(x["name"] in red for x in others) else "real"
        res["note"] = "other failed checks remain after the rerun"
    res.update(capture(a.repo, a.pr, head, saved))
    out(res)


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
        if not checks_green(checks):
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
        elif v["mergeStateStatus"] == "CLEAN" and not v.get("autoMergeRequest") and checks_green(checks):
            gh("pr", "merge", str(a.pr), "-R", a.repo, "--squash", "--delete-branch", *pin(v), check=False)
        if not poll.wait():
            out({"state": "pending", "pr": v})
            return
