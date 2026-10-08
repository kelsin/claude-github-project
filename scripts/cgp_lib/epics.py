"""Sub-stories: a planner declares a split (`cgp split <item> --declare`), the approved declaration is snapshotted like touches, and
`cgp split <item>` then creates exactly those stories. The parent is not implemented: it waits for its children and is closed
when every one of them reached Done by a merged PR. One nesting level; links live in cgp's data and in a `Part of` line in each child."""
import fnmatch
import json
import os
import re
import sys
from .consts import ALL_KEYS, MARK
from .util import covers, die, norm_path, out
from .gh import gh, post_comment, viewer
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref, require_repo, set_text
from .intake import create_story
from .pr import pr_view
from .policy import ALWAYS_DENY, glob_match
from .repoconf import merged_globs
from .story import ask_user, author_trusted

MAX_CHILDREN = 10


def read_specs(c, parent, raw, allowed=()):
    """The normalised list of child specs {title, repo, scope, files, after} from parsed JSON; dies on anything invalid, so nothing
    half-valid is ever stored or created. A spec may name a guarded or always-denied file only when `allowed` (the plan's touches) lists it."""
    if not isinstance(raw, list) or not raw:
        die("the declaration must be a non-empty JSON list of {title, scope, files, repo?, after?}")
    if len(raw) > MAX_CHILDREN:
        die(f"at most {MAX_CHILDREN} sub-stories per story")
    specs = []
    for n, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            die(f"sub-story {n} is not an object")
        title, scope, files = s.get("title"), s.get("scope"), s.get("files", [])
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or re.search(r"[\x00-\x1f\x7f]", title):
            die(f"sub-story {n} needs a one-line title of at most 200 characters")
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 20000:
            die(f"sub-story {n} needs a scope text")
        if not isinstance(files, list) or not all(isinstance(f, str) and f.strip() for f in files):
            die(f"sub-story {n}: files must be a list of paths")
        after = s.get("after", [])
        if not isinstance(after, list) or not all(isinstance(t, str) for t in after):
            die(f"sub-story {n}: after must be a list of sub-story titles")
        specs.append({"title": title.strip(), "repo": require_repo(c, s["repo"]) if s.get("repo") else parent["issueRepo"],
                      "scope": scope.strip(), "files": [norm_path(f) for f in files], "after": after})
    for s in specs:
        guarded = [g.lower() for g in merged_globs(c, "guardFiles", [s["repo"]])]
        for f in s["files"]:
            names = [f.rstrip("/")] + ([f + "_"] if f.endswith("/") else [])
            hit = any(glob_match(g, n) for g in ALWAYS_DENY for n in names) or any(
                fnmatch.fnmatchcase(x.lower(), g) for g in guarded for n in names for x in (n, os.path.basename(n)))
            if hit and not any(covers(t, f) for t in allowed):
                die(f"sub-story {s['title']!r}: {f} is a guarded file; the plan's files (cgp touches) must list it")
    titles = [s["title"] for s in specs]
    if len(set(titles)) != len(titles):
        die("sub-story titles must be unique")
    for s in specs:
        for t in s["after"]:
            if t not in titles or t == s["title"]:
                die(f"{s['title']!r} cannot come after {t!r}")
    edges = {s["title"]: s["after"] for s in specs}

    def cyclic(t, path):
        return t in path or any(cyclic(o, path | {t}) for o in edges[t])
    if any(cyclic(t, frozenset()) for t in titles):
        die("the sub-stories' order contains a cycle")
    return specs


def check_parent(c, a, columns):
    """The parent as a parsed story, after the refusals both subcommands share."""
    it = get_item(c, a.item)
    if it["kind"] != "issue":
        die("only an issue can be split (convert a draft first: cgp adopt)")
    if a.item in load_data().get("parents", {}):
        die("a sub-story cannot be split again (one nesting level)")
    if it["column"] not in columns:
        die(f"the story is in {it['column']}: this works only in {', '.join(columns)}")
    return it


def cmd_split(a):
    c = cfg()
    if a.declare:
        it = check_parent(c, a, ("todo", "plan"))
        if a.item in load_data().get("children", {}):
            die("this story was already split")
        try:
            raw = json.loads(sys.stdin.read() or "null")
        except ValueError:
            die("the declaration on stdin is not valid JSON")
        if it["skipPlan"]:
            die("the story skips its plan: sub-stories are only created from a plan you approved")
        specs = read_specs(c, it, raw, [norm_path(t) for t in load_data().get("touches", {}).get(a.item, [])])
        update_data(lambda d: d.setdefault("splits", {}).__setitem__(a.item, specs))
        out({"item": a.item, "declared": [s["title"] for s in specs]})
        return
    it = check_parent(c, a, ALL_KEYS[ALL_KEYS.index("plan_approved"):-1])
    if it["skipPlan"]:
        die("the story skips its plan: sub-stories are only created from a plan you approved")
    snapshot_splits(a.item)
    data = load_data()
    verdict = (data.get("policy") or {}).get(a.item) or {}
    if a.item in data.get("policyPlans", []) or (verdict.get("gate") == "plan_approved" and verdict.get("approved")):
        die("the plan was approved by the auto-approval policy, not by a person: sub-stories are only created from a plan you approved")
    if not data.get("approvedSplits", {}).get(a.item):
        die("the approved plan declares no sub-stories (in Plan: cgp split <item> --declare)")
    snap = data.get("approvedTouches", {}).get(a.item)
    specs = read_specs(c, it, data.get("approvedSplits", {}).get(a.item),  # the approved snapshot, validated again
                       [norm_path(t) for t in (snap if snap is not None else data.get("touches", {}).get(a.item, []))])
    if not c["fields"].get("plan"):
        die("this board has no Plan field; run /cgp:setup again")
    if not author_trusted(it):
        die("the story was not written by someone you trust")
    done = {k["spec"]: k for k in data.get("children", {}).get(a.item, [])}  # a retry after a failure creates only what is missing
    if len(done) == len(specs):  # already complete: nothing to create or announce again
        out({"item": a.item, "children": [done[n] for n in sorted(done)]})
        return
    for n, s in enumerate(specs):
        if n in done:
            continue
        body = f"{s['scope']}\n\nPart of {it['issueRepo']}#{it['number']}"
        r = create_story(c, s["repo"], s["title"], body)
        kid = {"spec": n, "item": r["item"], "repo": s["repo"], "number": r["number"], "title": s["title"], "url": r["url"]}
        done[n] = kid

        def link(d, kid=kid, files=s["files"]):  # recorded at once: a failure further on must not lose track of what exists
            d.setdefault("children", {}).setdefault(a.item, []).append(kid)
            d.setdefault("parents", {})[kid["item"]] = a.item
            d.setdefault("approvedTouches", {})[kid["item"]] = list(files)  # what the guard allows the child; its plan is skipped
        update_data(link)
        set_text(c, kid["item"], c["fields"]["plan"], "Skip")
    from .sched import native_order  # sched imports this module for the parent lifecycle
    node = lambda k: {"item": k["item"], "kind": "issue", "issueRepo": k["repo"], "number": k["number"]}
    for n, s in enumerate(specs):
        for t in s["after"]:
            native_order(c, node(done[n]), node(done[next(i for i, o in enumerate(specs) if o["title"] == t)]))
    kids = [done[n] for n in sorted(done)]
    post_comment(it["issueRepo"], it["number"], f"{MARK}\nSplit into {len(kids)} sub-stories; this story waits for them and is closed "
                 "when every one is done:\n" + "\n".join(f"- {k['repo']}#{k['number']} {k['title']}" for k in kids))
    out({"item": a.item, "children": kids})


def snapshot_splits(item):
    """Fill a missing snapshot of the declared split (see sched.snapshot, which takes it when the plan is approved)."""
    def upd(d):
        snap = d.setdefault("approvedSplits", {})
        if item not in snap and d.get("splits", {}).get(item):
            snap[item] = list(d["splits"][item])
    update_data(upd)


def parent_edges(by_id, data):
    """Parent -> its children that are still live: the parent waits for them, whatever the ranks."""
    return {p: ids for p, kids in (data.get("children") or {}).items() if p in by_id
            for ids in [[k["item"] for k in kids if k["item"] in by_id]] if ids}


def state(data, item):
    """What a worker needs to know about the split of a story (None when there is none)."""
    declared = [s["title"] for s in (data.get("splits") or {}).get(item, [])]
    approved = [s["title"] for s in (data.get("approvedSplits") or {}).get(item, [])]
    created = [k["title"] for k in (data.get("children") or {}).get(item, [])]
    parent = (data.get("parents") or {}).get(item)
    if not (declared or approved or created or parent):
        return None
    return {"declared": declared, "approved": approved, "created": created, "isSubStory": bool(parent)}


def finished(c, kid, row):
    """True when the sub-story is Done because its own PR merged (and the issue is one this account wrote)."""
    if not row or row["column"] != "done":
        return False
    ref = parse_pr_ref(c, row["pr"])
    merged = bool(ref and (pr_view(*ref, check=False) or {}).get("state") == "MERGED")
    try:
        author = (json.loads(gh("api", f"repos/{kid['repo']}/issues/{kid['number']}").stdout).get("user") or {}).get("login")
    except (SystemExit, ValueError):
        return False
    return merged and author == viewer()


def close_finished(c, items):
    """Close each parent whose sub-stories are all Done by a merged PR (comment listing their PRs); a sub-story that ended any other
    way goes to a person once. Marks the closed parents `closed` in `items`: the caller files them under Done."""
    data = load_data()
    by_id = {i["item"]: i for i in items}
    is_done = lambda k: by_id.get(k["item"], {}).get("column") == "done"  # noqa: E731
    for parent, kids in (data.get("children") or {}).items():
        p = by_id.get(parent)
        expected = len((data.get("approvedSplits") or {}).get(parent) or [])
        if p and expected and len(kids) < expected and p["column"] != "done" and not p["closed"] and not p["waiting"] \
                and (data.get("epicAsked") or {}).get(parent) != ["short", len(kids)] \
                and all(is_done(k) for k in kids):
            ask_user(c, parent, f"Only {len(kids)} of the {expected} approved sub-stories were created, so this story is not closed. "
                     "Run `CGP split` again to create the rest, then reply here.")
            short = ["short", len(kids)]
            update_data(lambda d, parent=parent, short=short: d.setdefault("epicAsked", {}).__setitem__(parent, short))
            p["waiting"], p["waitingOn"] = True, "You"
            continue
        if not p or not expected or len(kids) != expected or p["closed"] or p["column"] == "done" or not all(is_done(k) for k in kids):
            continue
        bad = [k for k in kids if not finished(c, k, by_id.get(k["item"]))]
        if bad:
            ids = sorted(k["item"] for k in bad)
            if not p["waiting"] and (data.get("epicAsked") or {}).get(parent) != ids:
                ask_user(c, parent, "These sub-stories are Done, but not by a merged PR of their own (or they are not stories this loop "
                         "created): " + ", ".join(f"{k['repo']}#{k['number']} {k['title']}" for k in bad) + ". Reopen them, or close this "
                         "story yourself if the work is finished, then reply here.")
                update_data(lambda d, parent=parent, ids=ids: d.setdefault("epicAsked", {}).__setitem__(parent, ids))
                p["waiting"], p["waitingOn"] = True, "You"
            continue
        prs = [f"- {k['repo']}#{k['number']} {k['title']}: {by_id[k['item']]['pr']}" for k in kids]
        gh("api", "-X", "PATCH", f"repos/{p['issueRepo']}/issues/{p['number']}", "-f", "state=closed", "-f", "state_reason=completed")
        post_comment(p["issueRepo"], p["number"], f"{MARK}\nAll sub-stories are done, so this story is closed:\n" + "\n".join(prs))
        p["closed"] = True
