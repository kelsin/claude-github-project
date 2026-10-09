"""Deferred work: unfixed review nits and out-of-scope items sit in a `## Deferred` section of the PR description. After the PR
merged, `cgp defer <item>` files each entry as a Todo story with Priority Hold, pointing back to the parent. Rerunning files only
what is missing. The PR text is data: it only ever becomes the title and a quoted reason of a story that waits on a person."""
import hashlib
import json
import re
from .consts import MARK
from .util import die, out
from .gh import gh, post_comment, viewer
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref
from .intake import create_story, priority_option

MAX_ITEMS = 10
BAD_TEXT = re.compile(r"[\x00-\x1f\x7f]")


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
    field = c["fields"].get("priority")
    if not field or "Hold" not in field["options"]:
        die("this board has no Priority field with a Hold option; run /cgp:setup again")
    hold = priority_option(c, "Hold")  # before anything is created
    items, skipped = parse_deferred(pr.get("body"))
    known = {k["key"]: k for k in load_data().get("deferred", {}).get(a.item, [])}
    parent = f"{it['issueRepo']}#{it['number']}"
    filed, already = [], []
    room = MAX_ITEMS - len(known)  # the cap is per story, over every run
    for i in items:
        if i["key"] in known:
            already.append(known[i["key"]])
            continue
        if room <= 0:
            skipped.append(i["title"])
            continue
        room -= 1
        body = f"Deferred from {parent} (PR {pr.get('url') or it['pr']})\n\n"
        body += "Filed automatically from the PR's Deferred section; read it before releasing this story from Hold."
        if i["reason"]:
            body += "\n\n> " + i["reason"].replace("@", "@​")
        rec = {"key": i["key"], "repo": it["issueRepo"], "title": i["title"]}

        def record(number, rec=rec):  # as soon as the issue exists, before it goes on the board
            rec["number"] = number
            update_data(lambda d: d.setdefault("deferred", {}).setdefault(a.item, []).append(dict(rec)))
        create_story(c, it["issueRepo"], i["title"].replace("@", "@​"), body, hold, on_issue=record)
        filed.append(rec)
    if filed:
        post_comment(it["issueRepo"], it["number"], f"{MARK}\nDeferred work filed as stories on hold:\n" +
                     "\n".join(f"- {k['repo']}#{k['number']} {k['title'].replace('@', '@' + chr(0x200b))}" for k in filed))
    out({"item": a.item, "filed": filed, "already": already, "skipped": skipped})
