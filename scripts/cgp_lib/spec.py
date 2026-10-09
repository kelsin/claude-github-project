"""Speculative implementation (setting `speculative`, off by default): a low-rated story waiting in Plan Review is drafted as local commits
on its own `cgp/<n>` branch, in the worktree `prepare` already made, while its plan awaits review. Nothing is pushed, no PR is opened and the
card never moves. When the plan is approved unchanged the commits are adopted (`prepare` decides, in code, see `resolve`); otherwise the
branch is reset to where the draft started. State per story is data["spec"][item]: {base, head, fingerprint, plan, touches, state, reason,
startedAt, trusted, artifactComments}, state one of running, ready, failed, discarded, adopted. The draft's worker row is keyed
"<item>:spec" (consts.SPEC_SUFFIX) so it never replaces the story's own row."""
import os
import hashlib
import subprocess
from .consts import SPEC_SUFFIX
from .util import call, covers, die, norm_path, now_iso, out
from .gh import is_agent, is_bot, rest, trusted
from .store import cfg, load_data, load_json, state_path, update_data, update_state
from .board import fetch_items, issue_item, parse_item
from .gitutil import default_ref, git, wt_path
from .gitwt import cmd_sync, cmd_worktree, is_ancestor, uncommitted_work
from .policy import forbidden
from .repoconf import merged_globs, repo_config, rules_text

LIVE = ("running", "ready")  # states in which the branch holds (or is getting) commits nobody has reviewed
COLUMNS = ("plan_review", "plan_approved", "implement")  # the columns a draft can exist in


def is_spec(key):
    return key.endswith(SPEC_SUFFIX)


def story_of(key):
    return key[:-len(SPEC_SUFFIX)] if is_spec(key) else key


def refuse_while_drafting(item, what):
    """Dies while the story's branch holds a draft that is running or ready: syncing, publishing or opening a PR would expose it."""
    state = (load_data().get("spec", {}).get(item) or {}).get("state")
    if state in LIVE:
        die(f"a speculative draft of this story is {state}: {what} waits until it is adopted or discarded (cgp spec status)")


def touches_of(data, item):
    return sorted({norm_path(f) for f in data.get("touches", {}).get(item, [])})


def local_problem(c, data, it):
    """Why a story may not be drafted speculatively (None when it may), from local state only: no network call."""
    r = (data.get("ratings") or {}).get(it["item"])
    if not c["settings"].get("speculative"):
        return "speculative is off"
    if it["kind"] != "issue" or it["column"] != "plan_review" or it["closed"]:
        return "the story is not an open issue in plan_review"
    if it["held"] or it["waiting"]:
        return "the story is on hold or waiting on you"
    if not it["plan"] or it["skipPlan"]:
        return "the story has no plan link"
    if not (r and r.get("rating") == "low" and r.get("column") == "plan"):  # cgp rate stores the column at rating time: the worker rated it in Plan
        return "the plan worker did not rate the story low"
    if not touches_of(data, it["item"]):
        return "the plan declares no files (cgp touches)"
    return None


def bad_touches(c, it, names):
    """Why a declared or changed path is off limits to a draft (None when none is): the always-deny list and guardFiles only."""
    guarded = [g.lower() for g in merged_globs(c, "guardFiles", [it["issueRepo"]])]
    return next((why for why in (forbidden(guarded, n.rstrip("/")) for n in names) if why), None)


def fingerprint(it, touches):
    """What the draft was built on: plan link, declared files and the newest human comment on the story. Returns (fingerprint, human
    comments). The plan's own text is not read (artifact comments are counted by the worker instead, see `--artifact-comments`)."""
    human = [cm for cm in rest(f"repos/{it['issueRepo']}/issues/{it['number']}/comments") if not is_agent(cm) and not is_bot(cm)]
    last = max(human, key=lambda cm: cm["created_at"], default=None)
    text = "|".join([it["plan"] or "", ",".join(touches), str(last and (last.get("id") or last["created_at"]))])
    return hashlib.sha1(text.encode()).hexdigest()[:16], human


def diff_problem(c, it, wt, spec):
    """Why the draft's actual changes may not be adopted (None when they may): every file must be inside the declared touches and
    pass the always-deny and guardFiles checks."""
    names = [n for n in git(wt, "diff", "-z", "--name-only", "--no-renames", f"{spec['base']}..{spec['head']}").split("\0") if n]
    why = bad_touches(c, it, names)
    if why:
        return why
    extra = next((n for n in names if not any(covers(t, n) for t in spec["touches"])), None)
    return f"{extra} is not in the plan's files" if extra else None


def drop_row(key):
    update_state(lambda st: st.__setitem__("workers", [w for w in st["workers"] if w["item"] != key + SPEC_SUFFIX]))


def discard(it, state, why, forget=False):
    """Stop the draft's worker row, put the branch back at where the draft started and record why (or forget the story's entry, so a
    changed plan may be drafted again)."""
    key = it["item"]
    drop_row(key)
    spec = load_data().get("spec", {}).get(key)
    wt = wt_path(it)
    if spec and spec["state"] in LIVE and spec.get("base") and os.path.isdir(wt):
        subprocess.run(["git", "-C", wt, "reset", "--hard", spec["base"]], capture_output=True)

    def upd(d):
        if forget:
            d.get("spec", {}).pop(key, None)
        elif key in d.get("spec", {}):
            d["spec"][key].update(state=state, reason=why)
    update_data(upd)


def record(it, entry):
    update_data(lambda d: d.setdefault("spec", {}).__setitem__(it["item"], entry))


def start(c, it, a):
    data = load_data()
    why = local_problem(c, data, it)
    if why:
        die(why)
    old = data.get("spec", {}).get(it["item"])
    if old and old["state"] in LIVE:
        die(f"the story already has a draft ({old['state']})")
    wt = call(cmd_worktree, item=a.item)
    synced = call(cmd_sync, item=a.item) if "error" not in wt else wt
    if "error" in synced or synced.get("state") not in ("clean", "rebased"):
        die(f"the worktree is not ready: {synced.get('error') or synced.get('state')}")
    wt = wt["path"]
    touches = touches_of(data, it["item"])
    fp, human = fingerprint(it, touches)
    if old and old.get("fingerprint") == fp:
        die(f"a draft of this plan was already tried ({old['state']})")
    from .story import author_trusted  # story imports this module
    raw = next((r for r in fetch_items(c["board"]["id"], True) if r["id"] == it["item"]), None)  # get_item does not read GitHub's blockedBy
    native = parse_item(raw, c) if raw else it
    blockers = [b for b in native["nativeBlockedBy"] if b["state"] == "OPEN"]
    entry = {"base": None, "head": None, "fingerprint": fp, "plan": it["plan"], "touches": touches, "state": "failed", "startedAt": now_iso(),
             "trusted": False, "artifactComments": a.artifact_comments or 0}
    refused = (bad_touches(c, it, touches) or ("the story is blocked on GitHub" if blockers or native["nativeOverflow"] else None)
               or (None if author_trusted(it) and all(trusted(it["issueRepo"], cm) for cm in human) else "the story or its feedback is not from people you trust")
               or uncommitted_work(wt))
    base = git(wt, "rev-parse", "HEAD")
    if not refused and not is_ancestor(wt, base, default_ref(wt)):
        refused = "the branch already holds commits that are not on the default branch"
    if refused:
        record(it, {**entry, "reason": refused})
        out({"started": False, "reason": refused})
        return
    record(it, {**entry, "base": base, "state": "running", "trusted": True})
    out({"started": True, "item": a.item, "number": it["number"], "issueRepo": it["issueRepo"], "plan": it["plan"], "touches": touches,
         "worktree": wt, "base": base, "repoConfig": repo_config(c, it["issueRepo"]), "houseRules": rules_text(c, it["issueRepo"])})


def current(it, states):
    spec = load_data().get("spec", {}).get(it["item"])
    if not spec or spec["state"] not in states:
        die(f"the story has no speculative draft that is {' or '.join(states)} (cgp spec status)")
    return spec


def finish(c, it, a):
    spec = current(it, ("running",))
    wt = wt_path(it)
    head = git(wt, "rev-parse", "HEAD")
    why = uncommitted_work(wt)
    if why:
        die(f"commit everything first: {why}")
    if head == spec["base"]:
        discard(it, "failed", "the draft made no commits")
        out({"state": "failed", "reason": "the draft made no commits"})
        return
    why = diff_problem(c, it, wt, {**spec, "head": head})
    if why:
        discard(it, "failed", why)
        out({"state": "failed", "reason": why})
        return
    update_data(lambda d: d["spec"][it["item"]].update(head=head, state="ready", finishedAt=now_iso()))
    drop_row(it["item"])
    out({"state": "ready", "base": spec["base"], "head": head})


def fail(c, it, a):
    current(it, LIVE)
    discard(it, "failed", "the worker gave up")
    out({"state": "failed"})


def status(c, it, a):
    out(load_data().get("spec", {}).get(it["item"]))


def why_not(c, it, spec, artifact):
    """Why the draft may not be adopted (None when it may): every check is code, not the worker's judgment."""
    if spec["state"] != "ready":
        return "the draft was still running" if spec["state"] == "running" else f"the draft is {spec['state']}"
    if it["column"] not in ("plan_approved", "implement") or it["held"] or it["closed"]:
        return f"the story is not approved ({it['column']})"
    wt = wt_path(it)
    if not os.path.isdir(wt) or git(wt, "rev-parse", "HEAD", check=False) != spec["head"]:
        return "the branch is not at the commit the draft ended on"
    dirty = uncommitted_work(wt)
    if dirty:
        return dirty
    if artifact is not None and artifact > spec["artifactComments"]:
        return "the plan artifact got a new comment"
    if fingerprint(it, touches_of(load_data(), it["item"]))[0] != spec["fingerprint"]:
        return "the plan, its files or the feedback changed"
    if not is_ancestor(wt, spec["base"], default_ref(wt)):
        return "the draft's base is not on the default branch"
    return diff_problem(c, it, wt, spec)


def resolve(c, it, artifact=None):
    """Adopt the ready draft of an approved story, or discard it. {} when the story has no draft in play. Adopting keeps the entry (state
    adopted) until the worker confirms the artifact's comments with `cgp spec resolve --artifact-comments N` (it clears the entry)."""
    spec = load_data().get("spec", {}).get(it["item"])
    if not spec or spec["state"] not in LIVE + ("adopted",):
        return {}
    if spec["state"] == "adopted":
        if artifact is None:
            return {}
        if artifact > spec["artifactComments"]:
            update_data(lambda d: d["spec"][it["item"]].update(state="ready"))
            discard(it, "discarded", "the plan artifact got a new comment")
            return {"speculative": {"discarded": True, "reason": "the plan artifact got a new comment"}}
        update_data(lambda d: d.get("spec", {}).pop(it["item"], None))
        return {"speculative": {"adopted": True, "confirmed": True}}
    why = why_not(c, it, spec, artifact)
    if why:
        discard(it, "discarded", why)
        return {"speculative": {"discarded": True, "reason": why}}
    update_data(lambda d: d["spec"][it["item"]].update(state="adopted") if artifact is None else d["spec"].pop(it["item"], None))
    return {"speculative": {"adopted": True, "base": spec["base"], "head": spec["head"], "confirmed": artifact is not None}}


def prepare_hook(c, it):
    """`cgp prepare` calls this before it syncs the worktree: an approved story's draft is adopted or discarded here."""
    return resolve(c, it) if it["column"] in ("plan_approved", "implement") else {}


def cmd_spec(a):
    c = cfg()
    it = issue_item(c, a.item)
    if a.action == "resolve":
        res = resolve(c, it, a.artifact_comments)
        out(res.get("speculative") or {"state": (load_data().get("spec", {}).get(a.item) or {}).get("state")})
        return
    {"start": start, "finish": finish, "fail": fail, "status": status}[a.action](c, it, a)


def worker_stopped(key, outcome):
    """A draft's worker row was stopped (`cgp worker stop <item>:spec`): a failed run is not retried for the same plan."""
    if outcome != "fail":
        return
    c = cfg()
    it = issue_item(c, story_of(key))
    spec = load_data().get("spec", {}).get(it["item"])
    if spec and spec["state"] in LIVE:
        discard(it, "failed", "the worker stalled or crashed")
    elif not spec:
        record(it, {"base": None, "head": None, "fingerprint": None, "plan": it["plan"], "touches": touches_of(load_data(), it["item"]),
                    "state": "failed", "reason": "the worker stalled or crashed", "startedAt": now_iso(), "trusted": False, "artifactComments": 0})


def sweep(c, items):
    """First thing in a snapshot: stop and discard drafts whose story went on or changed. A story that left Plan Review for anything but
    approval, was put on hold or closed, or whose plan or files changed loses its draft; a draft still running when the plan was approved
    is never adopted half done. A ready draft of an approved story is left for `prepare` to adopt or discard."""
    data = load_data()
    by_id = {i["item"]: i for i in items if i["column"] != "done" and not i["closed"]}
    rows = {story_of(w["item"]) for w in load_json(state_path(), {}).get("workers", []) if is_spec(w["item"])}
    for key in sorted(set(data.get("spec", {})) | rows):
        it, spec = by_id.get(key), data.get("spec", {}).get(key)
        live_spec = bool(spec) and spec["state"] in LIVE
        if not it or it["kind"] != "issue":
            drop_row(key)  # the story is gone: its entry is pruned
        elif it["column"] not in COLUMNS:
            discard(it, "discarded", f"the story is in {it['column']}", forget=True)
        elif it["held"] and (live_spec or key in rows):
            discard(it, "discarded", "the story is on hold")
        elif it["column"] == "plan_review" and spec and (spec["plan"] != it["plan"] or spec["touches"] != touches_of(data, key)):
            discard(it, "discarded", "the plan or its files changed", forget=True)
        elif it["column"] == "plan_review" and live_spec and local_problem(c, data, it):
            discard(it, "discarded", local_problem(c, data, it))
        elif it["column"] != "plan_review" and (spec or {}).get("state") == "running":
            discard(it, "discarded", "the plan was approved before the draft finished")
        elif it["column"] != "plan_review" and key in rows:
            drop_row(key)


def candidates(c, live, batch, in_flight, free):
    """Stories to draft this cycle, from local state only: the slots the batch left, up to speculativeMax. A draft never defers real work:
    real work is chosen first, a draft that would clash with it is discarded and a story that would clash is not started."""
    from .sched import hard_conflict  # sched imports this module
    data = load_data()
    cap, patterns = c["settings"]["concurrency"], merged_globs(c, "sharedFiles", {i["issueRepo"] for i in live})
    files = lambda i: set(touches_of(data, i["item"]))
    real = batch + [i for i in live if i["item"] in in_flight]
    clash = lambda a, b: a["issueRepo"] == b["issueRepo"] and hard_conflict(files(a), files(b), patterns)
    for i in live:
        spec = data.get("spec", {}).get(i["item"])
        hit = next((r for r in batch if clash(i, r)), None) if spec and spec["state"] in LIVE else None
        if hit:
            discard(i, "discarded", f"it clashes with {hit['title']}, which is starting")
    if not c["settings"].get("speculative"):
        return []
    room = c["settings"]["speculativeMax"] - sum(1 for k in in_flight if is_spec(k))
    if cap > 0:
        room = min(room, free - len(batch))
    picked = []
    for i in sorted(live, key=lambda i: (i["priorityRank"], -i["unlocks"])):
        spec = data.get("spec", {}).get(i["item"])
        if len(picked) >= room:
            break
        if local_problem(c, data, i) or i["item"] + SPEC_SUFFIX in in_flight or (spec and spec["plan"] == i["plan"] and spec["touches"] == touches_of(data, i["item"])):
            continue  # (an entry for this very plan: tried, running, ready, failed or discarded)
        if not any(clash(i, r) for r in real):
            picked.append(i)
    return picked


def prune(d, live_ids):
    d["spec"] = {k: v for k, v in d.get("spec", {}).items() if k in live_ids}
