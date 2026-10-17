"""Deferred work: unfixed review nits and out-of-scope items sit in a `## Deferred` section of the PR description. After the PR
merged, `cgp defer <item> --list` shows the entries with keys and `cgp defer <item> --review -` takes a reviewer's decision (JSON on
stdin: `keep` with a Medium or Low priority, `drop` with a reason) and files the kept ones as Todo stories, pointing back to the
parent. Without `--review` every entry is filed with Priority Hold. Rerunning files only what is missing. The PR text is data: it
only ever becomes the title and a quoted reason of a story, and a story filed from a review never has its plan auto-approved."""
import hashlib
import json
import re
import sys
from .consts import MARK
from .util import die, out
from .gh import gh, post_comment, viewer
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref
from .intake import create_story, priority_option

MAX_ITEMS = 10
BAD_TEXT = re.compile(r"[\x00-\x1f\x7f]")
PRIORITIES = ("Medium", "Low")  # what a review may choose: never High or Hold


def parse_deferred(body):
    """(items, skipped) from a PR description: the bullets `- title :: reason` under the first `## Deferred` heading."""
    items, skipped, seen, inside = [], [], set(), False
    for line in (body or "").splitlines():
        if re.match(r"\s{0,3}#{1,6}\s", line):
            if inside:
                break
            inside = re.match(r"\s{0,3}##\s+deferred\s*#*\s*$", line, re.I) is not None
            continue
        m = inside and re.match(r"\s*[-*]\s+(.*\S)\s*$", line)
        if not m:
            continue
        title, _, reason = m.group(1).partition(" :: ")
        title, reason = title.strip(), reason.strip()
        if not title or len(title) > 200 or len(reason) > 500 or BAD_TEXT.search(title + reason):
            skipped.append(m.group(1)[:80])
        elif item_key(title) not in seen:
            seen.add(item_key(title))
            if len(items) < MAX_ITEMS:
                items.append({"title": title, "reason": reason, "key": item_key(title)})
            else:
                skipped.append(title)
    return items, skipped


def item_key(title):
    return hashlib.sha1(" ".join(title.lower().split()).encode()).hexdigest()


def neutral(text):
    return text.replace("@", "@\u200b")


def read_decision(raw, items):
    """(keep {key: priority}, drop {key: reason}, unknown [key]) from the reviewer's JSON, matched to the parsed bullets; dies on anything odd."""
    if not isinstance(raw, dict):
        die("the decision must be a JSON object with keep and drop lists")
    keys = {i["key"] for i in items}
    norm = lambda k: " ".join(k.lower().split())
    lists = {}
    for name in ("keep", "drop"):
        lists[name] = raw.get(name, [])
        if not isinstance(lists[name], list) or not all(isinstance(e, dict) for e in lists[name]):
            die(f"{name} must be a list of objects")
    keep, drop, unknown = {}, {}, []
    for e in lists["keep"]:
        key, pri = e.get("key"), e.get("priority") or "Low"
        if not isinstance(key, str) or not isinstance(pri, str):
            die("key and priority must be strings")
        if pri.capitalize() not in PRIORITIES:
            die(f"priority must be one of {list(PRIORITIES)}")
        keep[norm(key)] = pri.capitalize()
    for e in lists["drop"]:
        key, why = e.get("key"), e.get("reason") or ""
        if not isinstance(key, str) or not isinstance(why, str):
            die("key and reason must be strings")
        if len(why) > 500 or BAD_TEXT.search(why):
            die("a drop reason must be at most 500 characters without control characters")
        drop[norm(key)] = why
    if set(keep) & set(drop):
        die("the same key is in keep and drop")
    for k in list(keep) + list(drop):
        if k not in keys:
            unknown.append(k)
    return ({k: v for k, v in keep.items() if k in keys}, {k: v for k, v in drop.items() if k in keys}, unknown)


def cmd_defer(a):
    c = cfg()
    it = get_item(c, a.item)
    if it["kind"] != "issue":
        die("only an issue can have deferred work")
    if it["column"] not in ("pr_approved", "done"):
        die(f"the story is in {it['column']}: this works only in pr_approved or done")
    ref = parse_pr_ref(c, it["pr"])
    if not ref:
        die("the story has no valid PR link")
    pr = json.loads(gh("pr", "view", str(ref[1]), "-R", ref[0], "--json", "state,body,author,url").stdout)
    if pr.get("state") != "MERGED":
        die("the PR is not merged yet")
    if (pr.get("author") or {}).get("login") != viewer():
        die("the PR was not written by this account")
    items, skipped = parse_deferred(pr.get("body"))
    data = load_data()
    known = {k["key"]: k for k in data.get("deferredFiled", {}).get(a.item, [])}
    dropped = {k["key"]: k for k in data.get("deferredDropped", {}).get(a.item, [])}
    if a.list:
        if a.review is not None:
            die("--list and --review cannot be combined")
        out({"item": a.item, "items": [{k: i[k] for k in ("key", "title", "reason")} for i in items], "skipped": skipped,
             "filed": list(known), "dropped": list(dropped)})
        return
    field = c["fields"].get("priority")
    review = a.review is not None
    if review:
        if a.review != "-":
            die("--review takes - (the decision as JSON on stdin)")
        try:
            raw = json.loads(sys.stdin.read() or "null")
        except ValueError:
            die("the decision on stdin is not valid JSON")
        keep, drop, unknown = read_decision(raw, items)
        for name in set(keep.values()):
            priority_option(c, name)  # before anything is created or saved
        def save(d):
            s = d.setdefault("deferredDecision", {}).setdefault(a.item, {"keep": {}, "drop": {}})
            for k, v in keep.items():
                if k not in s["keep"] and k not in s["drop"]:
                    s["keep"][k] = v
            for k, v in drop.items():
                if k not in s["keep"] and k not in s["drop"]:
                    s["drop"][k] = v
        update_data(save)
        saved = load_data()["deferredDecision"][a.item]
    else:
        if not field or "Hold" not in field["options"]:
            die("this board has no Priority field with a Hold option; run /cgp:setup again")
        pri = priority_option(c, "Hold")  # before anything is created
        saved, unknown = {"keep": {}, "drop": {}}, []
    newly = [{"key": i["key"], "title": i["title"], "reason": saved["drop"][i["key"]]} for i in items
             if i["key"] in saved["drop"] and i["key"] not in dropped]
    if newly:
        update_data(lambda d: d.setdefault("deferredDropped", {}).setdefault(a.item, []).extend(dict(n) for n in newly))
    parent = f"{it['issueRepo']}#{it['number']}"
    filed, already, unreviewed = [], [], []
    room = MAX_ITEMS - len(known)  # the cap is per story, over every run
    for i in items:
        if i["key"] in known:
            already.append(known[i["key"]])
            continue
        if i["key"] in dropped or i["key"] in saved["drop"]:
            continue
        if review and i["key"] not in saved["keep"]:
            unreviewed.append(i["title"])
            continue
        if room <= 0:
            skipped.append(i["title"])
            continue
        room -= 1
        body = f"Deferred from {parent} (PR {pr.get('url') or it['pr']})\n\n"
        if review:
            body += ("Filed automatically from the PR's Deferred section after review against the original request; the priority was "
                     "chosen by that review; a person approves the plan.")
        else:
            body += "Filed automatically from the PR's Deferred section; read it before releasing this story from Hold."
        if i["reason"]:
            body += "\n\n> " + neutral(i["reason"])
        rec = {"key": i["key"], "repo": it["issueRepo"], "title": i["title"]}
        if review:
            rec["priority"] = saved["keep"][i["key"]]

        def record(number, rec=rec):  # as soon as the issue exists, before it goes on the board
            rec["number"] = number
            update_data(lambda d: d.setdefault("deferredFiled", {}).setdefault(a.item, []).append(dict(rec)))
        made = create_story(c, it["issueRepo"], neutral(i["title"]), body, priority_option(c, rec["priority"]) if review else pri, on_issue=record)
        if review:
            update_data(lambda d, n=made["item"]: d.setdefault("deferredReviewed", []).append(n))
        filed.append(rec)
    if filed or newly:
        lines = [f"- {k['repo']}#{k['number']} {neutral(k['title'])}" + (f" ({k['priority']})" if review else "") for k in filed]
        lines += [f"- dropped: {neutral(n['title'])}" + (f" ({neutral(n['reason'])})" if n["reason"] else "") for n in newly]
        head = ("Deferred work reviewed against the original request (at most %d bullets are read):" % MAX_ITEMS if review
                else "Deferred work filed as stories on hold:")
        tail = [f"Not filed (over the limit or unparsed): {neutral(', '.join(skipped))}"] if skipped and review else []
        post_comment(it["issueRepo"], it["number"], "\n".join([MARK, head] + lines + tail))
    res = {"item": a.item, "filed": filed, "already": already, "skipped": skipped}
    if review:
        res.update(dropped=newly, unknown=unknown, unreviewed=unreviewed)
    out(res)
