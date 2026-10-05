"""Putting stories on the board from the terminal: `cgp add` creates an issue, `cgp import` adopts existing ones by label."""
import json
import sys
from urllib.parse import quote
from .consts import PRIORITY_OPTIONS
from .util import die, out
from .gh import gh, gql, rest, trusted
from .store import cfg
from .board import fetch_items, parse_item, require_repo, set_single


def default_repo(c, repo):
    """The repo to create or import in: the one named, else the board's only linked repo."""
    if repo:
        return require_repo(c, repo)
    if len(c["repos"]) != 1:
        die(f"several repos are linked ({', '.join(c['repos'])}); pass --repo <owner/name>")
    return next(iter(c["repos"]))


def priority_option(c, name):
    """Option id of a Priority value (any case), or None when no priority was asked for."""
    if not name:
        return None
    field = c["fields"].get("priority")
    if not field:
        die("this board has no Priority field; run /cgp:setup again to add it")
    match = next((n for n, _ in PRIORITY_OPTIONS if n.lower() == name.lower()), None)
    if not match:
        die(f"priority must be one of {[n for n, _ in PRIORITY_OPTIONS]}")
    return field["id"], field["options"][match]


def put_on_board(c, node_id, priority=None):
    """Add an issue to the board in Todo (with a priority when given); returns the board item id."""
    item = gql("""mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }""",
               p=c["board"]["id"], c=node_id)["addProjectV2ItemById"]["item"]["id"]
    set_single(c, item, c["fields"]["status"]["id"], c["fields"]["status"]["options"]["todo"])
    if priority:
        set_single(c, item, *priority)
    return item


def create_story(c, repo, title, body, priority=None):
    """Create an issue and put it on the board in Todo (the repo and priority must be validated already)."""
    issue = json.loads(gh("api", f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={body}").stdout)
    item = put_on_board(c, issue["node_id"], priority)
    return {"item": item, "number": issue["number"], "url": issue["html_url"], "repo": repo}


def cmd_add(a):
    c = cfg()
    repo = default_repo(c, a.repo)
    priority = priority_option(c, a.priority)  # validated before anything is created
    body = sys.stdin.read().strip() if a.body == "-" else (a.body or "")
    out(create_story(c, repo, a.title, body, priority))


def cmd_import(a):
    c = cfg()
    repos = [default_repo(c, a.repo)] if a.repo or len(c["repos"]) == 1 else list(c["repos"])
    on_board = set()
    for raw in fetch_items(c["board"]["id"]):
        it = parse_item(raw, c)
        if it["kind"] == "issue":
            on_board.add((it["issueRepo"], it["number"]))
    added, skipped = [], []
    for repo in repos:
        for issue in rest(f"repos/{repo}/issues?state=open&labels={quote(a.label, safe='')}"):
            if "pull_request" in issue or (repo, issue["number"]) in on_board:
                continue
            if not trusted(repo, issue):  # anyone can open an issue with a label on a public repo: only trusted authors are taken in
                skipped.append({"repo": repo, "number": issue["number"], "author": (issue.get("user") or {}).get("login")})
                continue
            added.append({"repo": repo, "number": issue["number"], "title": issue["title"],
                          "item": put_on_board(c, issue["node_id"])})
    out({"label": a.label, "added": added, "skippedUntrusted": skipped})
