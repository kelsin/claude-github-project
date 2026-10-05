"""Per-story commands: comments, questions, feedback, field updates, moves, prepare."""
import json
import re
import sys
from .consts import DEFAULT_PHASE, SKIP, LINKS_END, LINKS_START, MARK, NETLIFY_BOT, QMARK, TEXT_FIELDS
from .util import call, die, now_iso, out
from .gh import gh, is_agent, is_bot, post_comment, rest, trusted
from .store import cfg, load_data, update_data, update_state
from .board import board_keys, clear_field, get_item, item_issue, parse_pr_ref, set_single, set_text
from .session import set_phase, worker_pr
from .gitwt import cmd_sync, cmd_worktree
from .pr import cancel_auto_merge, cmd_pr_state, pr_view, record_reviewed


def list_replies_since(repo, number, marker_ids, since=None):
    """Trusted human comments posted after the newest agent comment containing a marker.
    `since` (when the question was posted) limits the fetch to the comments that can matter."""
    comments = rest(f"repos/{repo}/issues/{number}/comments" + (f"?since={since}" if since else ""))
    last = None
    for cm in comments:
        if any(m in (cm.get("body") or "") for m in marker_ids) and is_agent(cm):
            last = cm["created_at"]
    if last is None:
        return [], None
    return [cm for cm in comments if cm["created_at"] > last and not is_agent(cm) and not is_bot(cm)
            and trusted(repo, cm)], last


def process_replies(c, items):
    """Clear 'Waiting On' for items whose question got a human reply."""
    asked = load_data().get("asked", {})
    for it in items:
        if not it["waiting"] or it["kind"] != "issue":
            continue
        replies, _ = list_replies_since(it["issueRepo"], it["number"], [QMARK], asked.get(it["item"]))
        if replies:
            clear_field(c, it["item"], c["fields"]["waiting"]["id"])
            it["waiting"] = it["waitingOn"] = False
            update_data(lambda d, i=it["item"]: d.setdefault("answered", []).append(i))


def cmd_move(a):
    c = cfg()
    if a.column not in board_keys(c):
        die(f"column must be one of {board_keys(c)}")
    it = get_item(c, a.item)
    if it["column"] == a.column:
        out({"item": a.item, "column": a.column, "unchanged": True, "note": f"already in {a.column}"})
        return
    # the user's Auto Approve field delegates a gate to the agent: only from the agent column just before it, and a PR needs its link
    auto = {"plan_approved": ("plan", "plan"), "pr_approved": ("pr", "implement")}.get(a.column)
    if a.column in ("plan_approved", "pr_approved") and not (
            auto and it["autoApprove"][auto[0]] and it["column"] == auto[1] and (auto[0] == "plan" or parse_pr_ref(c, it["pr"]))):
        die(f"only the user moves stories into {a.column}; it is a human approval (unless Auto Approve covers it)")
    if it["column"] in ("plan_approval", "pr_approval"):
        die(f"story is in {it['column']}: only the user moves it out")
    ref = parse_pr_ref(c, it["pr"])
    if a.column == "done" and not (it["closed"] and it["kind"] == "issue"):  # a closed issue is finished already
        if it["column"] != "pr_approved" or not ref or pr_view(*ref)["state"] != "MERGED":
            die("a story reaches Done only from pr_approved, after its PR merged")
    if a.column != "done" and it["column"] == "pr_approved" and ref:
        cancel_auto_merge(*ref)  # leaving PR Approved must not leave a merge armed
    set_single(c, a.item, c["fields"]["status"]["id"], c["fields"]["status"]["options"][a.column])
    if a.column == "pr_approval" and ref:
        record_reviewed(a.item, ref)  # the commit the user is about to review: merge accepts only this one

    def upd(st):
        for w in st["workers"]:
            if w["item"] == a.item:
                w["column"] = a.column
                for k in ("phase", "detail", "phaseAt"):
                    w.pop(k, None)  # a phase belongs to the column it was set in
                if a.column in DEFAULT_PHASE:
                    w.update(phase=DEFAULT_PHASE[a.column], phaseAt=now_iso())
    update_state(upd)
    update_data(lambda d: d.__setitem__("answered", [i for i in d.get("answered", []) if i != a.item]))
    out({"item": a.item, "column": a.column})


def cmd_set(a):
    c = cfg()
    key = a.field.lower()
    if key not in TEXT_FIELDS or key == "preview":
        die(f"field must be one of {[k for k in TEXT_FIELDS if k != 'preview']} (Preview is written by: cgp preview)")
    value = a.value
    if key == "pr" and value and not parse_pr_ref(c, value):
        die("PR must be https://github.com/<linked repo>/pull/<n> on a repo linked to this board")
    set_text(c, a.item, c["fields"][key], value)
    if value:  # a published plan or an opened PR means review comes next
        if key == "pr":
            worker_pr(a.item, parse_pr_ref(c, value))
        if value.strip().lower() != SKIP:  # no plan is published when planning is skipped
            set_phase("reviewing", item=a.item)
        if key in ("plan", "pr"):
            sync_links(c, a.item)
    out({"item": a.item, a.field: value or None})


def sync_links(c, item):
    """Keep a Plan / PR / Preview block at the end of the issue body, listing whichever links exist so far."""
    it = get_item(c, item)
    if it["kind"] != "issue":
        return
    rows = [f"- {label}: {it[k]}" for label, k in (("Plan", "plan"), ("PR", "pr"), ("Preview", "preview")) if it[k]]
    if not rows:
        return
    path = f"repos/{it['issueRepo']}/issues/{it['number']}"
    body = json.loads(gh("api", path).stdout).get("body") or ""
    block = "\n".join([LINKS_START, *rows, LINKS_END])
    pat = re.compile(re.escape(LINKS_START) + r".*?" + re.escape(LINKS_END), re.S)
    new = pat.sub(lambda _: block, body) if pat.search(body) else f"{body.rstrip()}\n\n{block}".lstrip()
    if new != body:
        gh("api", "-X", "PATCH", path, "-f", f"body={new}", check=False)


def cmd_preview(a):
    """Copy the Netlify deploy preview URL from the story's PR into the Preview field, once Netlify has posted it."""
    c = cfg()
    if "preview" not in c["fields"]:
        die("this board has no Preview field; run /cgp:setup again to add it")
    ref = parse_pr_ref(c, get_item(c, a.item)["pr"])
    if not ref:
        die("story has no valid PR field set")
    repo, pr = ref
    url_re = re.compile(rf"https://deploy-preview-{pr}--[a-z0-9-]+\.netlify\.app\b")
    urls = [m.group(0) for cm in rest(f"repos/{repo}/issues/{pr}/comments")
            if (cm.get("user") or {}).get("login") == NETLIFY_BOT  # only Netlify's own bot: anyone can type a URL
            for m in [url_re.search(cm.get("body") or "")] if m]
    if not urls:
        out({"item": a.item, "preview": None, "note": "no Netlify deploy preview on the PR yet"})
        return
    set_text(c, a.item, c["fields"]["preview"], urls[-1])
    sync_links(c, a.item)
    out({"item": a.item, "preview": urls[-1]})


def read_body():
    body = sys.stdin.read().strip()
    if not body:
        die("empty comment body (pipe it on stdin)")
    return body


def advance_cursor(item, asked=None):
    """Feedback read earlier in this run is now handled; `asked` records when a question was posted (see process_replies)."""
    def commit(st):
        if item in st.get("pending", {}):
            st.setdefault("cursors", {})[item] = st["pending"].pop(item)
        if asked:
            st.setdefault("asked", {})[item] = asked
    update_data(commit)


def cmd_comment(a):
    c = cfg()
    repo, number, _ = item_issue(a.item)
    body = read_body()
    target_repo, target = repo, number
    if a.pr:
        ref = parse_pr_ref(c, get_item(c, a.item)["pr"])
        if not ref:
            die("story has no valid PR field set")
        target_repo, target = ref
    cm = post_comment(target_repo, target, f"{MARK}\n{body}")

    advance_cursor(a.item)
    out({"url": cm["html_url"]})


def cmd_ask(a):
    c = cfg()
    repo, number, _ = item_issue(a.item)
    rounds = sum(1 for cm in rest(f"repos/{repo}/issues/{number}/comments") if QMARK in (cm.get("body") or ""))
    body = read_body()
    if rounds >= 3:
        body += "\n\n_This story has needed several rounds of questions; consider rescoping or splitting it._"
    cm = post_comment(repo, number, f"{QMARK}\n{MARK}\n❓ **Question for you** (reply here; work resumes automatically)\n\n{body}")
    set_single(c, a.item, c["fields"]["waiting"]["id"], c["fields"]["waiting"]["you"])

    advance_cursor(a.item, asked=cm.get("created_at"))
    out({"url": cm["html_url"], "round": rounds + 1})


def cmd_answers(a):
    """Q&A history for a story: every question and the human replies after it."""
    repo, number, _ = item_issue(a.item)
    comments = rest(f"repos/{repo}/issues/{number}/comments")
    thread = []
    for cm in comments:
        if is_bot(cm):
            continue
        who = "agent" if is_agent(cm) else "human" if trusted(repo, cm) else "untrusted"
        thread.append({"who": who, "at": cm["created_at"],
                       "body": None if who == "untrusted" else cm["body"].replace(QMARK, "").replace(MARK, "").strip()})
    out(thread)


def cmd_feedback(a):
    c = cfg()
    repo, number, _ = item_issue(a.item)
    it = get_item(c, a.item)
    ref = parse_pr_ref(c, it["pr"])
    issue_comments = rest(f"repos/{repo}/issues/{number}/comments")
    sources = [("issue", issue_comments)]
    if ref:
        prc, prn = ref
        sources.append(("pr-comment", rest(f"repos/{prc}/issues/{prn}/comments")))
        sources.append(("pr-inline", rest(f"repos/{prc}/pulls/{prn}/comments")))
        reviews = [r for r in rest(f"repos/{prc}/pulls/{prn}/reviews")
                   if (r.get("body") or "").strip() or r["state"] == "CHANGES_REQUESTED"]
        for r in reviews:
            r["created_at"] = r.get("submitted_at") or ""
        sources.append(("pr-review", reviews))
    st = load_data()
    cursor = st.get("cursors", {}).get(a.item) or max(
        [cm["created_at"] for _, cms in sources for cm in cms if is_agent(cm) and "created_at" in cm] or [""])
    seen = max([cm["created_at"] for _, cms in sources for cm in cms if "created_at" in cm] or [cursor])

    def pend(st):
        st.setdefault("pending", {})[a.item] = max(seen, cursor)
    update_data(pend)
    found, ignored = [], []
    for w, cms in sources:
        for cm in cms:
            if cm["created_at"] <= cursor or is_agent(cm) or is_bot(cm):
                continue
            row = {"where": w, "author": (cm.get("user") or {}).get("login"), "at": cm["created_at"],
                   "url": cm.get("html_url"), "path": cm.get("path")}
            if trusted(repo if w == "issue" else ref[0], cm):
                found.append({**row, "body": cm.get("body")})
            else:
                ignored.append(row)  # no body: text from people without write access is never shown to agents
    out({"comments": sorted(found, key=lambda f: f["at"]), "ignoredUntrusted": ignored})


def cmd_prepare(a):
    """Everything a worker reads before acting, in one call: the story, feedback, the Q&A history when the user just
    answered, the PR state, and the worktree brought up to date. Each part fails on its own (`error`) so one problem
    does not hide the rest."""
    c = cfg()
    it = get_item(c, a.item)

    res = {"story": it}
    if it["kind"] != "issue":
        res["note"] = "a draft: convert it first (cgp adopt <item> <owner/repo>); drafts have no comments or worktree"
        out(res)
        return
    res["feedback"] = call(cmd_feedback, item=a.item)
    if a.item in load_data().get("answered", []):
        res["answers"] = call(cmd_answers, item=a.item)
    ref = parse_pr_ref(c, it["pr"])
    if ref:
        worker_pr(a.item, ref)
        res["prState"] = call(cmd_pr_state, repo=ref[0], pr=ref[1])
    res["worktree"] = call(cmd_worktree, item=a.item)
    if "error" not in res["worktree"]:
        res["sync"] = call(cmd_sync, item=a.item)
    out(res)
