"""The GitHub Project: fields, items, columns, views, setup/migration and the board-level commands."""
import os
import re
import subprocess
from .consts import ALL_KEYS, AUTO_FIELD, AUTO_OPTIONS, COLUMNS, DEFAULTS, PATHS, PR_URL, SCHEMA, SKIP, STORY_OPTION, TEXT_FIELDS, VIEWS, WAITING_FIELD
from .util import die, norm, out, split_repo
from .gh import gh, gql
from .store import board_file, cfg, load_json, lock_holder, save_board


PROJECT_FRAGMENT = """
  id title url
  repositories(first: 50) { nodes { nameWithOwner } }
  views(first: 50) { nodes { id name layout } }
  fields(first: 50) { nodes {
    __typename
    ... on ProjectV2FieldCommon { id name dataType }
    ... on ProjectV2SingleSelectField { options { id name color description } }
  } }
"""


def parse_board_url(url):
    m = re.match(r"https?://github\.com/(orgs|users)/([^/]+)/projects/(\d+)", url)
    if not m:
        die("not a GitHub project URL (expected https://github.com/orgs/<org>/projects/<n> or /users/<user>/projects/<n>)")
    return ("organization" if m.group(1) == "orgs" else "user"), m.group(2), int(m.group(3))


def fetch_project(kind, owner, number):
    q = f"query($o:String!,$n:Int!){{ {kind}(login:$o){{ projectV2(number:$n){{ {PROJECT_FRAGMENT} }} }} }}"
    proj = gql(q, o=owner, n=number)[kind]["projectV2"]
    if not proj:
        die("project not found or not accessible")
    return proj


def fields_by_name(proj):
    return {f["name"]: f for f in proj["fields"]["nodes"] if f.get("id")}


def check_scope():
    p = gh("auth", "status", check=False)
    text = p.stdout + p.stderr
    m = re.search(r"Token scopes:(.*)", text)
    if m and "'project'" not in m.group(1):
        die("gh token lacks the 'project' scope. Run: gh auth refresh -s project", code=3)


# Status, Waiting On, Plan, PR, Preview and Auto Approve are asked for by name: a positional fieldValues(first:N) can miss them on boards with many fields.
VALUE_FIELDS = (("fvStatus", "Status"), ("fvWaiting", WAITING_FIELD), ("fvPlan", TEXT_FIELDS["plan"]), ("fvPr", TEXT_FIELDS["pr"]),
                ("fvPreview", TEXT_FIELDS["preview"]), ("fvAuto", AUTO_FIELD))


FIELD_VALUES = " ".join(
    f'{alias}: fieldValueByName(name:"{name}"){{ __typename'
    " ... on ProjectV2ItemFieldSingleSelectValue{ optionId name } ... on ProjectV2ItemFieldTextValue{ text } }"
    for alias, name in VALUE_FIELDS)


ITEM_FIELDS = """id isArchived
  content{ __typename
    ... on Issue{ number title url state repository{nameWithOwner} }
    ... on PullRequest{ number title url state repository{nameWithOwner} }
    ... on DraftIssue{ title body } }
  FIELD_VALUES""".replace("FIELD_VALUES", FIELD_VALUES)

ITEMS_QUERY = """
query($p:ID!,$after:String){ node(id:$p){ ... on ProjectV2{ items(first:100, after:$after){
  pageInfo{hasNextPage endCursor}
  nodes{ ITEM_FIELDS } } } } }
""".replace("ITEM_FIELDS", ITEM_FIELDS)


def fetch_items(project_id):
    items, after = [], None
    while True:
        page = gql(ITEMS_QUERY, p=project_id, after=after)["node"]["items"]
        items += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return items
        after = page["pageInfo"]["endCursor"]


ITEM_QUERY = """
query($i:ID!){ node(id:$i){ ... on ProjectV2Item{ ITEM_FIELDS } } }
""".replace("ITEM_FIELDS", ITEM_FIELDS)


def get_item(c, item):
    raw = gql(ITEM_QUERY, i=item)["node"]
    if not raw:
        die(f"item {item} not found")
    return parse_item(raw, c)


def issue_item(c, item):
    """get_item for commands that only make sense on an issue (drafts have no comments, branch or worktree)."""
    it = get_item(c, item)
    if it["kind"] != "issue":
        die("item is not an issue on this board")
    return it


def parse_item(raw, c):
    content = raw.get("content") or {}
    vals = {name: raw.get(alias) for alias, name in VALUE_FIELDS}
    by_opt = {o: k for k, o in c["fields"]["status"]["options"].items()}
    status = vals.get("Status")
    column = by_opt.get(status["optionId"]) if status else None
    kind = {"Issue": "issue", "DraftIssue": "draft", "PullRequest": "pr"}.get(content.get("__typename"), "other")
    issue_repo = (content.get("repository") or {}).get("nameWithOwner")
    text = lambda n: (vals.get(n) or {}).get("text") or None
    waiting_on = (vals.get(WAITING_FIELD) or {}).get("name") or None
    auto = (vals.get(AUTO_FIELD) or {}).get("name")
    return {
        "item": raw["id"],
        "kind": kind,
        "title": content.get("title", ""),
        "url": content.get("url"),
        "number": content.get("number"),
        "issueRepo": issue_repo,
        "column": column or "todo",
        "unset": column is None,
        "plan": text("Plan"),
        "skipPlan": (text("Plan") or "").strip().lower() == SKIP,
        "autoApprove": {"plan": auto in ("Plan", "Both"), "pr": auto in ("PR", "Both")},
        "pr": text("PR"),
        "preview": text("Preview"),
        "waitingOn": waiting_on,
        "waiting": bool(waiting_on) and waiting_on != STORY_OPTION,  # on the user, not merely queued behind a story
        "archived": raw["isArchived"],
        "closed": content.get("state") in ("CLOSED", "MERGED"),
    }


def set_single(c, item, field_id, option_id):
    gql("""mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{
      projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ clientMutationId } }""",
        p=c["board"]["id"], i=item, f=field_id, o=option_id)


def set_text(c, item, field_id, value):
    if value:
        gql("""mutation($p:ID!,$i:ID!,$f:ID!,$t:String!){ updateProjectV2ItemFieldValue(input:{
          projectId:$p,itemId:$i,fieldId:$f,value:{text:$t}}){ clientMutationId } }""",
            p=c["board"]["id"], i=item, f=field_id, t=value)
    else:
        clear_field(c, item, field_id)


def clear_field(c, item, field_id):
    gql("""mutation($p:ID!,$i:ID!,$f:ID!){ clearProjectV2ItemFieldValue(input:{
      projectId:$p,itemId:$i,fieldId:$f}){ clientMutationId } }""",
        p=c["board"]["id"], i=item, f=field_id)


def item_issue(item):
    d = gql("""query($i:ID!){ node(id:$i){ ... on ProjectV2Item{ content{ __typename
        ... on Issue{ number title repository{nameWithOwner} } } } } }""", i=item)
    content = d["node"]["content"] or {}
    if content.get("__typename") != "Issue":
        die(f"item {item} is not an issue (draft items must be converted: cgp adopt <item> <owner/repo>)")
    return content["repository"]["nameWithOwner"], content["number"], content["title"]


def known_repo(c, repo):
    return next((r for r in c["repos"] if r.lower() == (repo or "").lower()), None)


def parse_pr_ref(c, url):
    """(repo, number) for a PR URL on one of the board's linked repos, else None."""
    m = PR_URL.match((url or "").strip())
    repo = known_repo(c, m.group(1)) if m else None
    return (repo, int(m.group(2))) if repo else None


def require_repo(c, repo):
    r = known_repo(c, repo)
    if not r:
        die(f"{repo} is not a repo linked to this board (see: cgp repos)")
    return r


def waiting_you(field):
    """Option id of 'You' on the Waiting On field; dies when the field is not a usable single-select."""
    if "options" not in field:
        die(f"the existing '{WAITING_FIELD}' field is not a single-select field; rename or delete it, then run setup again")
    you = next((o["id"] for o in field["options"] if o["name"] == "You"), None)
    if not you:
        die(f"the '{WAITING_FIELD}' field has no 'You' option; add one in the board settings, then run setup again")
    return you


def ensure_story_option(field):
    """Add the 'Another story' option to the Waiting On field (existing options keep their ids); returns its option id."""
    have = next((o["id"] for o in field["options"] if o["name"] == STORY_OPTION), None)
    if have:
        return have
    opts = [{"id": o["id"], "name": o["name"], "color": o.get("color") or "GRAY", "description": o.get("description") or ""}
            for o in field["options"]]
    opts.append({"name": STORY_OPTION, "color": "YELLOW", "description": ""})
    data = gql("""mutation($f:ID!,$o:[ProjectV2SingleSelectFieldOptionInput!]!){
      updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$o}){ projectV2Field{
        ... on ProjectV2SingleSelectField{ id options{id name} } } } }""", f=field["id"], o=opts)
    return next(o["id"] for o in data["updateProjectV2Field"]["projectV2Field"]["options"] if o["name"] == STORY_OPTION)


def board_keys(c):
    """The columns this board actually has, in pipeline order (an unmigrated board still has plan_review and pr_review)."""
    return [k for k in ALL_KEYS if k in c["fields"]["status"]["options"]]


def status_options(status):
    """Column key -> option id for the current columns of a Status field."""
    return {key: o["id"] for key, name, _, _ in COLUMNS for o in status["options"] if norm(o["name"]) == norm(name)}


def legacy_layout(options):
    """True for the ten-column layout: it has Plan Approval / PR Approval, which the human columns were called then."""
    return bool({norm(o["name"]) for o in options} & {"plan approval", "pr approval"})


def apply_columns(proj, status, dry_run=False):
    """Make the Status options the current COLUMNS. Returns (Status field, summary).

    A board in the old layout is migrated in place, never by name alone: its agent-side "Plan Review" / "PR Review"
    would otherwise be matched to the new human columns of the same names and stories nobody reviewed would look
    approvable. Instead their stories first move to Plan / Implement (whose workers now review inline), and the
    "Plan Approval" / "PR Approval" options are renamed with their ids kept, so the stories waiting on the user stay put.
    Safe to repeat: the item moves come first and the options change in one mutation.
    """
    desired = [f"{emoji} {name}" for _, name, emoji, _ in COLUMNS]
    old = {norm(o["name"]): o["id"] for o in status["options"]}
    legacy = legacy_layout(status["options"])
    keep, premove = {n: i for n, i in old.items() if n in {norm(n_) for _, n_, _, _ in COLUMNS}}, {}
    if legacy:
        keep.pop("plan review", None)  # the agent-side one; the human column of that name comes from "plan approval"
        keep.pop("pr review", None)
        for new, was in (("plan review", "plan approval"), ("pr review", "pr approval")):
            if was in old:
                keep[new] = old[was]
        for was, to in (("plan review", "plan"), ("pr review", "implement")):
            if was in old:
                premove[old[was]] = old.get(to)
    if not legacy and [o["name"] for o in status["options"]] == desired:
        return status, {"changed": False}
    matched = set(keep.values())
    moves, remap = [], {}
    for raw in fetch_items(proj["id"]):
        cur = (raw.get("fvStatus") or {}).get("optionId")
        if cur and premove.get(cur):
            moves.append((raw["id"], premove[cur]))
        elif cur and cur in matched:
            continue  # option id is preserved below, so the item keeps its status
        else:
            closed = (raw.get("content") or {}).get("state") in ("CLOSED", "MERGED")
            remap[raw["id"]] = "Done" if closed else ""
    summary = {"changed": True, "legacy": legacy, "movedToReview": len(moves),
               "remapped": {"todo": sum(1 for t in remap.values() if t != "Done"),
                            "done": sum(1 for t in remap.values() if t == "Done")}}
    if dry_run:
        return status, {**summary, "dryRun": True}
    for item, option in moves:
        set_single({"board": {"id": proj["id"]}}, item, status["id"], option)
    data = gql("""mutation($f:ID!,$o:[ProjectV2SingleSelectFieldOptionInput!]!){
      updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$o}){ projectV2Field{
        ... on ProjectV2SingleSelectField{ id options{id name} } } } }""",
               f=status["id"],
               o=[{"name": f"{e} {n}", "color": col, "description": "",
                   **({"id": keep[norm(n)]} if norm(n) in keep else {})} for _, n, e, col in COLUMNS])
    status = data["updateProjectV2Field"]["projectV2Field"]
    new_opts = {norm(o["name"]): o["id"] for o in status["options"]}
    for item, tag in remap.items():
        set_single({"board": {"id": proj["id"]}}, item, status["id"], new_opts["done" if tag == "Done" else "todo"])
    return status, summary


def ensure_views(proj, fields):
    """Create the VIEWS missing from the board; views that exist by name are left as the user has them.

    A fresh board's untouched default "View 1" table becomes Tasks. Returns the names created.
    """
    have = {v["name"]: v for v in proj["views"]["nodes"]}
    done = []
    for name, layout, shown, flt in VIEWS:
        if name in have:
            continue
        config = {"visibleFieldIds": [fields[f]["id"] for f in shown if f in fields]}
        default = have.get("View 1")
        if default and default["layout"] == layout:  # reuse the starter view rather than leave an extra tab
            gql("""mutation($v:ID!,$n:String!,$f:String,$c:ProjectV2ViewConfigurationInput){
              updateProjectV2View(input:{viewId:$v,name:$n,filter:$f,configuration:$c}){ clientMutationId } }""",
                v=default["id"], n=name, f=flt, c=config)
            del have["View 1"]
        else:
            view = gql("""mutation($p:ID!,$n:String!,$l:ProjectV2ViewLayout!,$c:ProjectV2ViewConfigurationInput){
              createProjectV2View(input:{projectId:$p,name:$n,layout:$l,configuration:$c}){ projectV2View{ id } } }""",
                       p=proj["id"], n=name, l=layout, c=config)["createProjectV2View"]["projectV2View"]
            if flt:
                gql("""mutation($v:ID!,$f:String){ updateProjectV2View(input:{viewId:$v,filter:$f}){ clientMutationId } }""",
                    v=view["id"], f=flt)
        done.append(name)
    return done


def cmd_setup(a):
    check_scope()
    kind, owner, number = parse_board_url(a.url)
    proj = fetch_project(kind, owner, number)
    fields = fields_by_name(proj)
    status = fields.get("Status")
    if not status or "options" not in status:
        die("project has no single-select Status field")
    if WAITING_FIELD in fields:
        waiting_you(fields[WAITING_FIELD])  # fail before the board is touched

    repo_id = None
    if a.repo:  # validate before touching the board
        owner_, name_ = split_repo(a.repo)
        repo_id = gql("query($o:String!,$n:String!){ repository(owner:$o,name:$n){ id } }",
                      o=owner_, n=name_)["repository"]["id"]

    if a.dry_run:
        have_views = {v["name"] for v in proj["views"]["nodes"]}
        out({"dryRun": True, "columns": apply_columns(proj, status, dry_run=True)[1],
             "fieldsToAdd": [n for n in (WAITING_FIELD, *TEXT_FIELDS.values(), AUTO_FIELD) if n not in fields],
             "viewsToAdd": [v[0] for v in VIEWS if v[0] not in have_views], "repoToLink": a.repo})
        return
    status, migration = apply_columns(proj, status)
    options = status_options(status)

    def ensure(name, dtype, opts=None):
        if name in fields:
            return fields[name]
        v = {"p": proj["id"], "t": dtype, "n": name, "o": opts}
        d = gql("""mutation($p:ID!,$t:ProjectV2CustomFieldType!,$n:String!,$o:[ProjectV2SingleSelectFieldOptionInput!]){
          createProjectV2Field(input:{projectId:$p,dataType:$t,name:$n,singleSelectOptions:$o}){ projectV2Field{
            ... on ProjectV2FieldCommon{ id name } ... on ProjectV2SingleSelectField{ options{id name} } } } }""", **v)
        return d["createProjectV2Field"]["projectV2Field"]

    waiting = ensure(WAITING_FIELD, "SINGLE_SELECT", [{"name": "You", "color": "RED", "description": ""},
                                                      {"name": STORY_OPTION, "color": "YELLOW", "description": ""}])
    text_ids = {k: ensure(n, "TEXT")["id"] for k, n in TEXT_FIELDS.items()}
    ensure(AUTO_FIELD, "SINGLE_SELECT", [{"name": n, "color": "GREEN", "description": ""} for n in AUTO_OPTIONS])
    fields = fields_by_name(fetch_project(kind, owner, number))  # now with the fields added above
    views = ensure_views(proj, fields)

    old = load_json(board_file(proj["id"]), {})
    paths = load_json(PATHS, {})
    repos = {r: paths.get(r) for r in old.get("repos", {})}
    c = {
        "board": {"id": proj["id"], "url": proj["url"], "title": proj["title"],
                  "kind": kind, "owner": owner, "number": number},
        "schema": SCHEMA,
        "fields": {"status": {"id": status["id"], "options": options},
                   "waiting": {"id": waiting["id"], "you": waiting_you(waiting),
                               "story": ensure_story_option(waiting)},
                   **text_ids},
        "repos": repos,
        "settings": {**DEFAULTS, **old.get("settings", {})},
    }

    for r in proj["repositories"]["nodes"]:
        c["repos"].setdefault(r["nameWithOwner"], None)
    if a.repo:
        rid = repo_id
        if a.repo not in c["repos"]:
            gql("""mutation($p:ID!,$r:ID!){ linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r}){
              clientMutationId } }""", p=proj["id"], r=rid)
        c["repos"].setdefault(a.repo, None)
        if a.repo_path:
            c["repos"][a.repo] = os.path.abspath(a.repo_path)
    save_board(c)

    out({"board": c["board"], "repos": c["repos"], "itemsRemapped": migration.get("remapped", {"todo": 0, "done": 0}),
         "migration": migration, "viewsCreated": views,
         "note": "items whose old status matched a new column name kept it; closed ones went to Done, all others to Todo"})


def migrate_columns(c, dry_run=False):
    """Bring a board's Status options up to the current columns (see apply_columns) and its config with them."""
    b = c["board"]
    proj = fetch_project(b["kind"], b["owner"], b["number"])
    status = fields_by_name(proj).get("Status")
    if not status or "options" not in status:
        die("project has no single-select Status field")
    status, summary = apply_columns(proj, status, dry_run)
    if not dry_run:
        c["fields"]["status"] = {"id": status["id"], "options": status_options(status)}
        c["schema"] = SCHEMA
        save_board(c)
    return summary


def cmd_migrate(a):
    """Update this board's columns by hand (`cgp use` does it by itself unless autoMigrate is 0)."""
    c = cfg()
    holder = lock_holder(c["board"]["id"])
    if holder and not a.dry_run:
        die("another session is running this board's loop; migrate when it has stopped")
    out(migrate_columns(c, a.dry_run))


def cmd_repo_path(a):
    c = cfg()
    if a.repo:
        a.repo = require_repo(c, a.repo)
    if a.path:
        c["repos"][a.repo] = os.path.abspath(a.path)
        save_board(c)
    out(c["repos"] if not a.repo else {a.repo: c["repos"].get(a.repo)})


def cmd_discover(a):
    c = cfg()
    roots = a.roots or [os.path.expanduser(p) for p in ("~/src", "~/code", "~/projects", "~/dev")]
    wanted = {r.lower(): r for r in c["repos"] if not c["repos"][r]}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d1 in sorted(os.listdir(root)):
            for cand in (os.path.join(root, d1), *(os.path.join(root, d1, d2) for d2 in
                         (sorted(os.listdir(os.path.join(root, d1))) if os.path.isdir(os.path.join(root, d1)) else []))):
                if not os.path.exists(os.path.join(cand, ".git")):
                    continue
                url = subprocess.run(["git", "-C", cand, "remote", "get-url", "origin"],
                                     capture_output=True, text=True).stdout.strip()
                m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
                if m and m.group(1).lower() in wanted:
                    c["repos"][wanted.pop(m.group(1).lower())] = cand
    save_board(c)
    out({"found": {r: p for r, p in c["repos"].items() if p}, "missing": [r for r, p in c["repos"].items() if not p]})


def cmd_config(a):
    c = cfg()
    if a.key:
        if a.key not in DEFAULTS:
            die(f"unknown setting; one of {list(DEFAULTS)}")
        if a.key in ("sharedFiles", "guardFiles"):
            if a.value is None:
                die(f"usage: cgp config {a.key} <comma-separated globs, or empty for none>")
            c["settings"][a.key] = [g.strip() for g in a.value.split(",") if g.strip()]
        else:
            if a.value is None or not re.fullmatch(r"\d+", a.value):
                die(f"usage: cgp config {a.key} <non-negative integer>")
            c["settings"][a.key] = int(a.value)
        save_board(c)
    out(c["settings"])


def cmd_adopt(a):
    cfg()  # dies unless a board is configured
    owner, name = split_repo(a.repo)
    rid = gql("query($o:String!,$n:String!){ repository(owner:$o,name:$n){ id } }", o=owner, n=name)["repository"]["id"]
    d = gql("""mutation($i:ID!,$r:ID!){ convertProjectV2DraftIssueItemToIssue(input:{itemId:$i,repositoryId:$r}){
      item{ content{ ... on Issue{ number url } } } } }""", i=a.item, r=rid)
    issue = d["convertProjectV2DraftIssueItemToIssue"]["item"]["content"]
    out(issue)


def cmd_repos(a):
    c = cfg()
    out(c["repos"])


def rank(it):
    return (ALL_KEYS.index(it["column"]), -(it["number"] or 0))
