"""Cycle-time numbers for stories that reached Done: time per column, sends back and CI reruns. Read-only, nothing is stored."""
import time
from datetime import datetime
from urllib.parse import quote
from .consts import ALL_KEYS, COLUMNS
from .util import die, norm, out, printable
from .gh import gql, rest
from .store import cfg
from .board import fetch_items, parse_item, parse_pr_ref

BATCH = 50
TIMELINE = """timelineItems(itemTypes:[PROJECT_V2_ITEM_STATUS_CHANGED_EVENT, ADDED_TO_PROJECT_V2_EVENT], first:100AFTER){
  pageInfo{hasNextPage endCursor}
  nodes{ __typename
    ... on ProjectV2ItemStatusChangedEvent{ createdAt previousStatus status project{id} }
    ... on AddedToProjectV2Event{ createdAt project{id} } } }"""
BATCH_QUERY = ("query($ids:[ID!]!){ nodes(ids:$ids){ ... on ProjectV2Item{ id content{ ... on Issue{ createdAt "
               + TIMELINE.replace("AFTER", "") + " } } } } }")
MORE_QUERY = ("query($i:ID!,$after:String){ node(id:$i){ ... on ProjectV2Item{ content{ ... on Issue{ "
              + TIMELINE.replace("AFTER", ", after:$after") + " } } } } }")
KEYS_BY_NAME = {norm(n): k for k, n, _, _ in COLUMNS}


def stamp(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def fetch_timelines(ids):
    """{item id: (issue createdAt, [timeline nodes])} for project items, 50 per call; an issue with more than 100 events is followed by cursor."""
    res = {}
    for n in range(0, len(ids), BATCH):
        for node in gql(BATCH_QUERY, ids=ids[n:n + BATCH])["nodes"]:
            issue = (node or {}).get("content") or {}
            if not issue.get("timelineItems"):
                continue
            page, events = issue["timelineItems"], list(issue["timelineItems"]["nodes"])
            while page["pageInfo"]["hasNextPage"]:
                page = gql(MORE_QUERY, i=node["id"], after=page["pageInfo"]["endCursor"])["node"]["content"]["timelineItems"]
                events += page["nodes"]
            res[node["id"]] = (issue["createdAt"], events)
    return res


def story_events(created, nodes, board_id):
    """(anchor time, [(time, from column, to column)]) of one story on this board, or None when its history cannot be trusted.
    Events of other projects and with column names this board does not know are ignored."""
    added = sorted(stamp(e["createdAt"]) for e in nodes if e.get("__typename") == "AddedToProjectV2Event" and (e.get("project") or {}).get("id") == board_id)
    moves = sorted(((stamp(e["createdAt"]), KEYS_BY_NAME.get(norm(e["previousStatus"])), KEYS_BY_NAME.get(norm(e["status"])))
                    for e in nodes if e.get("__typename") == "ProjectV2ItemStatusChangedEvent" and (e.get("project") or {}).get("id") == board_id),
                   key=lambda m: m[0])  # time only: same-second ties keep GitHub's order
    moves = [m for m in moves if m[1] and m[2]]
    if not moves or (not added and moves[0][1] != ALL_KEYS[0]):  # it started elsewhere and nothing says when: missing early history
        return None
    return (added[0] if added else stamp(created)), moves


def done_time(nodes, board_id):
    """Time of the latest move into Done on this board, or None when there is none."""
    times = [stamp(e["createdAt"]) for e in nodes if e.get("__typename") == "ProjectV2ItemStatusChangedEvent"
             and (e.get("project") or {}).get("id") == board_id and KEYS_BY_NAME.get(norm(e["status"])) == "done"]
    return max(times, default=None)


def column_durations(anchor, moves):
    """Seconds spent in each column before the last move, visits added up. Done itself has no duration."""
    spent, since, col = {}, anchor, moves[0][1]
    for at, _, to in moves:
        spent[col] = spent.get(col, 0) + max(at - since, 0)
        since, col = at, to
    spent.pop("done", None)
    return spent


def sends_back(moves):
    """How many moves went to an earlier column of the pipeline."""
    return sum(1 for _, a, b in moves if ALL_KEYS.index(b) < ALL_KEYS.index(a))


def median(values):
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def ci_reruns(c, stories):
    """(total, lookups that worked, lookups that failed): reruns are run_attempt - 1 of every run on the story's cgp/<n> branch."""
    total, ok, failed = 0, 0, 0
    for s in stories:
        ref = parse_pr_ref(c, s["pr"])
        if not ref:
            continue
        branch = quote(f"cgp/{s['number']}", safe="")
        try:
            pages = rest(f"repos/{ref[0]}/actions/runs?branch={branch}")
        except SystemExit:
            failed += 1
            continue
        total += sum(max((r.get("run_attempt") or 1) - 1, 0) for p in pages for r in p.get("workflow_runs", []))
        ok += 1
    return total, ok, failed


def compute(c, days, now=None):
    now = now or time.time()
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"])]
    done = [i for i in items if i["kind"] == "issue" and i["column"] == "done"]
    timelines = fetch_timelines([i["item"] for i in done])
    per_col, back, counted, skipped = {}, [], [], 0
    for i in done:
        found = timelines.get(i["item"])
        hist = story_events(*found, c["board"]["id"]) if found else None
        if not hist or hist[1][-1][2] != "done":
            at = done_time(found[1], c["board"]["id"]) if found else None
            if at is None or at >= now - days * 86400:  # a story known to be done before the window is not counted at all
                skipped += 1
            continue
        if hist[1][-1][0] < now - days * 86400:
            continue
        counted.append(i)
        for k, secs in column_durations(*hist).items():
            per_col.setdefault(k, []).append(secs)
        back.append(sends_back(hist[1]))
    total, ok, failed = ci_reruns(c, counted)
    return {"days": days, "stories": len(counted), "skipped": skipped,
            "columns": {k: {"median": median(per_col[k]), "worst": max(per_col[k]), "n": len(per_col[k])} for k in ALL_KEYS if k in per_col},
            "sentBack": {"stories": sum(1 for n in back if n), "sends": sum(back)},
            "ciReruns": total if ok or not failed else None, "ciPrs": ok, "ciLookupsFailed": failed,
            "cost": None}


def duration(secs):
    m = int(secs // 60)
    d, h, m = m // 1440, m % 1440 // 60, m % 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def render(res):
    n = res["stories"]
    names = {k: name for k, name, _, _ in COLUMNS}
    lines = [f"Cycle time, last {res['days']} days ({n} stories done" + (f", {res['skipped']} skipped" if res["skipped"] else "") + ")"]
    if not n:
        return lines + ["no stories done in this window"]
    lines += ["", f"{'column':<15} {'median':<10} {'worst':<10} n"]
    lines += [f"{names[k]:<15} {duration(v['median']):<10} {duration(v['worst']):<10} {v['n']}" for k, v in res["columns"].items()]
    sb = res["sentBack"]
    ci = "n/a (lookups failed)" if res["ciReruns"] is None else f"{res['ciReruns']} across {res['ciPrs']} PRs"
    lines += ["", f"sent back       {sb['stories']} of {n} stories ({round(100 * sb['stories'] / n)}%), {sb['sends']} sends",
              f"CI reruns       {ci}" + (f" ({res['ciLookupsFailed']} lookups failed)" if res["ciLookupsFailed"] and res["ciReruns"] is not None else ""),
              "cost / story    n/a (no spend data)"]
    return lines


def cmd_stats(a):
    """Time per column, sends back and CI reruns of the stories that reached Done in the last --days days."""
    if a.days <= 0:
        die("--days must be a positive number")
    res = compute(cfg(), a.days)
    if a.json:
        out(res)
        return
    for line in render(res):
        print(printable(line))
