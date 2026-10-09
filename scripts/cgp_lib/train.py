"""Merge train: approved PRs of a repo and base branch merge one at a time, dependencies first, then the lowest issue number.
The position is a pure function of board and PR state, so concurrent workers agree and a crash loses nothing."""
import heapq
from .board import parse_pr_ref
from .store import load_data
from .pr import merge_queue_enabled, pr_checks, pr_view


def reach(blocks, item):
    """(every story `item` depends on, directly or through a chain, whether GitHub dependencies were left unread).
    The overflow sentinel ("native:...") is not a story and orders nothing."""
    seen, todo, overflow = set(), list(blocks.get(item, [])), False
    while todo:
        b = todo.pop()
        if b.startswith("native:"):
            overflow = True
        elif b not in seen and b != item:
            seen.add(b)
            todo.extend(blocks.get(b, []))
    return seen, overflow


def train_order(members, blocks):
    """Items of `members` ([{"item", "number"}]) in merge order: after everything they depend on, else lowest number first
    (Kahn with a min-heap: comparing numbers pairwise is not transitive). Stories on a dependency cycle follow, by number."""
    num = {m["item"]: m["number"] or 0 for m in members}
    before = {i: reach(blocks, i)[0] & set(num) for i in num}
    queued = {i for i in num if not before[i]}
    heap = [(num[i], i) for i in queued]
    heapq.heapify(heap)
    order = []
    while heap:
        order.append(heapq.heappop(heap)[1])
        for j in num:
            if j not in queued and before[j] <= set(order):
                queued.add(j)
                heapq.heappush(heap, (num[j], j))
    return order + sorted((i for i in num if i not in queued), key=lambda i: num[i])


def started(data, item, v):
    """The PR was updated by this tool or has auto-merge armed (and is not behind again): it keeps the head until it merges or fails."""
    return v["headRefOid"] in data.get("cleanRebase", {}).get(item, []) or (bool(v.get("autoMergeRequest")) and v.get("mergeStateStatus") != "BEHIND")


def eligible(x, v, base):
    """Same conditions as pr.merge_target, plus: not sent back for review."""
    return (v["state"] == "OPEN" and v.get("headRefName") == f"cgp/{x['number']}" and v.get("isCrossRepository") is False
            and v.get("baseRefName") == base and not v["isDraft"]
            and not (v["mergeStateStatus"] == "BLOCKED" and v["reviewDecision"] in ("CHANGES_REQUESTED", "REVIEW_REQUIRED")))


def train_ahead(c, me, live, blocks, views):
    """The stories whose PR merges before the story `me`: {"ahead": [...], "merged": [items whose PR is merged], "note": text or None}.
    `live` are the board's live stories and `blocks` their effective_blocks; `views` caches PR views for the call. Fails closed: a PR
    that cannot be read holds. A story holds for what it depends on; otherwise for the PR that already started, else for the first
    earlier PR that is ready (conflicting, red, sent-back or itself held PRs do not stall independent ones)."""
    by_id, data, merged = {i["item"]: i for i in live}, load_data(), set()

    def view(x):
        ref = parse_pr_ref(c, x["pr"])
        if not ref:
            return None
        if ref not in views:
            views[ref] = pr_view(*ref, check=False)
        v = views[ref]
        if v and v["state"] == "MERGED":
            merged.add(x["item"])
        return v

    def unresolved(x):  # stories x depends on whose PR has not merged (the board's column lags behind the PR)
        ids, _ = reach(blocks, x["item"])
        return [by_id[b] for b in sorted(ids, key=lambda b: by_id[b]["number"] or 0)
                if b in by_id and not (by_id[b]["column"] == "pr_approved" and (view(by_id[b]) or {}).get("state") == "MERGED")]

    def entry(x, why):
        return {"item": x["item"], "number": x["number"], "title": x["title"], "pr": x["pr"], "why": why}

    mv = view(me)
    note = "GitHub dependencies beyond the ones read were ignored for the train order" if reach(blocks, me["item"])[1] else None
    if mv is None:
        return {"ahead": [{"item": me["item"], "number": me["number"], "title": me["title"], "pr": me["pr"], "why": "the PR could not be read"}],
                "merged": sorted(merged), "note": note}
    ahead = [entry(b, "dependency") for b in unresolved(me)]
    if ahead or mv["state"] != "OPEN" or started(data, me["item"], mv):
        return {"ahead": ahead, "merged": sorted(merged), "note": note}
    repo, base = parse_pr_ref(c, me["pr"])[0], mv.get("baseRefName")
    cands = {}
    for x in live:
        ref = parse_pr_ref(c, x["pr"])
        if x is me or x["column"] != "pr_approved" or x["waiting"] or x["held"] or x["item"] in data.get("stack", {}) or not ref or ref[0] != repo:
            continue
        v = view(x)
        if v and eligible(x, v, base) and me["item"] not in reach(blocks, x["item"])[0]:
            cands[x["item"]] = (x, v)
    for x, v in cands.values():
        if started(data, x["item"], v):
            ahead.append(entry(x, "already updating"))
    if not ahead:
        order = train_order([me] + [x for x, _ in cands.values()], blocks)
        for i in order[:order.index(me["item"])]:
            x, v = cands[i]
            if v["mergeable"] == "CONFLICTING" or v["mergeStateStatus"] == "DIRTY" or unresolved(x):
                continue
            if any(k["bucket"] in ("fail", "cancel") for k in (pr_checks(repo, parse_pr_ref(c, x["pr"])[1]) or [])):
                continue
            ahead.append(entry(x, "earlier in the train"))
            break
    return {"ahead": ahead, "merged": sorted(merged), "note": note}


def train_held(c, stories, live, blocks):
    """Story -> its non-empty `ahead` list, for the pr_approved `stories` that wait for a PR ahead of theirs (none in a repo with a
    merge queue). The scheduler dispatches only the head, so a trailing story cannot take a worker slot from it."""
    views, queues, held = {}, {}, {}
    for s in stories:
        ref = parse_pr_ref(c, s["pr"]) if s["column"] == "pr_approved" else None
        if not ref:
            continue
        v = views.setdefault(ref, pr_view(*ref, check=False))
        if not v or v["state"] != "OPEN":
            continue
        if (ref[0], v["baseRefName"]) not in queues:
            queues[ref[0], v["baseRefName"]] = merge_queue_enabled(ref[0], v["baseRefName"])
        if not queues[ref[0], v["baseRefName"]]:
            ahead = train_ahead(c, s, live, blocks, views)["ahead"]
            if ahead:
                held[s["item"]] = ahead
    return held
