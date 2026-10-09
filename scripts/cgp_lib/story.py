"""Per-story commands: comments, questions, feedback, field updates, moves, prepare."""
import hashlib
import json
import re
import sys
from urllib.parse import urlsplit
from .consts import DEFAULT_PHASE, KEY_RENAMES, SKIP, LINKS_END, LINKS_START, MARK, NETLIFY_BOT, QMARK, TEXT_FIELDS
from .util import call, die, norm_path, now_iso, out
from .gh import gh, is_agent, is_bot, post_comment, rest, trusted
from .store import cfg, load_data, update_data, update_state
from .board import board_keys, clear_field, get_item, item_issue, parse_pr_ref, set_single, set_text
from .session import clear_phase, reset_strikes, set_phase, worker_pr
from .repoconf import repo_config, rules_text, safe_pattern
from .gitwt import cmd_sync, cmd_worktree
from .pr import cancel_auto_merge, changed_since_review, cmd_pr_state, default_branch, forget_review, pr_view, record_reviewed, stack_gate, stack_of
from .stack import prepare_stack, stale as stack_stale
from .policy import GATE_KEYS, current_rating, evaluate, public, record_decision, require_human


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
            update_data(lambda d, i=it["item"]: (d.setdefault("answered", []).append(i), reset_strikes(d, i)))


def gate_open(c, it, target):
    """True when the story is where a human gate can be passed on the agent's behalf: only from the agent column just before it, and a PR
    needs its link."""
    src = {"plan_approved": "plan", "pr_approved": "implement"}.get(target)
    return bool(src and it["column"] == src and (target == "plan_approved" or parse_pr_ref(c, it["pr"])))


def auto_allows(c, it, target):
    """True when the user's Auto Approve field lets the agent pass the human gate `target` for this item."""
    return gate_open(c, it, target) and it["autoApprove"][GATE_KEYS[target]]


def policy_verdict(c, it, target):
    """The auto-approval policy's verdict for the gate (None when the policy is off for it), see policy.evaluate. A stacked story
    or a PR that does not target the default branch is never approved."""
    verdict = evaluate(c, it, target, lambda: author_trusted(it))
    why = stack_gate(c, it) if verdict and verdict["approved"] and target == "pr_approved" else None
    return {"approved": False, "reason": why} if why else verdict


def ready_pr(ref):
    gh("pr", "ready", str(ref[1]), "-R", ref[0], check=False)


def cmd_move(a):
    c = cfg()
    a.column = KEY_RENAMES.get(a.column, a.column)  # the user's columns were called plan_approval / pr_approval before schema 3
    if a.column not in board_keys(c):
        die(f"column must be one of {board_keys(c)}")
    it = get_item(c, a.item)
    if it["column"] == a.column:
        out({"item": a.item, "column": a.column, "unchanged": True, "note": f"already in {a.column}"})
        return
    requested = None
    target = {"plan_review": "plan_approved", "pr_review": "pr_approved"}.get(a.column, a.column)
    # the user's Auto Approve field (read live: they may have set it after the worker started) or, failing that, the policy delegates a gate
    field_ok = target in GATE_KEYS and auto_allows(c, it, target)
    verdict = policy_verdict(c, it, target) if target in GATE_KEYS and not field_ok and gate_open(c, it, target) else None
    stacked = stack_gate(c, it) if field_ok and target == "pr_approved" else None
    if stacked and a.column == "pr_approved":
        die(f"refusing to approve the PR: {stacked}")
    field_ok = field_ok and not stacked  # a delegated move into PR Review then stays there for the user
    policy_ok = bool(verdict and verdict["approved"])
    if target != a.column and (field_ok or policy_ok):
        requested, a.column = a.column, target
    if a.column in GATE_KEYS and not (field_ok or policy_ok):
        die(f"only the user moves stories into {a.column}; it is a human approval (unless Auto Approve or the auto-approval policy covers it)"
            + (f": the policy refused: {verdict['reason']}" if verdict else ""))
    if it["column"] in ("plan_review", "pr_review"):
        die(f"story is in {it['column']}: only the user moves it out")
    ref = parse_pr_ref(c, it["pr"])
    if a.column == "done" and not (it["closed"] and it["kind"] == "issue"):  # a closed issue is finished already
        if it["column"] != "pr_approved" or not ref or pr_view(*ref)["state"] != "MERGED":
            die("a story reaches Done only from pr_approved, after its PR merged")
        why = stack_gate(c, it)
        if why:
            die(f"a story reaches Done only when its PR merged into the default branch: {why}")
    if a.column != "done" and it["column"] == "pr_approved" and ref:
        cancel_auto_merge(*ref)  # leaving PR Approved must not leave a merge armed
    if policy_ok and a.column == "pr_approved":  # a policy approval stands in for the review: ready the PR and record the commit it checked
        if verdict["draft"]:
            ready_pr(ref)
        record_reviewed(a.item, ref, verdict["sha"])  # (`merge` stays pinned to it: a later push is refused, exit 7)
        if (pr_view(*ref, check=False) or {}).get("headRefOid") != verdict["sha"]:  # pushed meanwhile: a person reviews it instead
            verdict, policy_ok, requested, a.column = {"approved": False, "reason": "the PR head moved while it was being approved"}, False, None, "pr_review"
    shown = None
    if a.column == "pr_review" and ref:  # read the commit the user will be shown first: a failed read must abort the move,
        shown = pr_view(*ref)  # not leave the story in PR Review with no record of what was reviewed
        if not shown or not shown.get("headRefOid"):
            die("could not read the PR head; not moving the story to pr_review")
        rec = stack_of(a.item)
        why = stack_stale(rec) if rec else None
        if why:
            die(f"not moving the story to pr_review: {why}")
    if policy_ok:
        record_decision(it, a.column, verdict)  # before the move: the files that were checked are the files approved
    set_single(c, a.item, c["fields"]["status"]["id"], c["fields"]["status"]["options"][a.column])
    if requested == "pr_review" and not policy_ok:  # redirected to pr_approved: no review to record, but a merge refuses a draft
        if (pr_view(*ref, check=False) or {}).get("isDraft"):
            ready_pr(ref)
    if shown:
        if shown["isDraft"]:  # draftPRs opens PRs as drafts: ready for review now
            ready_pr(ref)
        record_reviewed(a.item, ref, shown["headRefOid"])  # the commit the user is about to review: merge accepts only this one
    if a.column == "implement" and it["column"] == "plan_approved":
        snapshot_touches(a.item)  # fills a missing snapshot only: what the user approved is what the guard allows

    def upd(st):
        for w in st["workers"]:
            if w["item"] == a.item:
                w["column"] = a.column
                clear_phase(w)  # a phase belongs to the column it was set in
                if a.column in DEFAULT_PHASE:
                    w.update(phase=DEFAULT_PHASE[a.column], phaseAt=now_iso())
    update_state(upd)
    update_data(lambda d: (d.__setitem__("answered", [i for i in d.get("answered", []) if i != a.item]), reset_strikes(d, a.item)))
    if policy_ok:
        post_comment(it["issueRepo"], it["number"], f"{MARK}\nAuto-approved by the policy ({'plan' if a.column == 'plan_approved' else 'PR'}): {verdict['reason']}.")
        advance_cursor(a.item)  # like `cgp comment`: posting counts as having read the feedback so far
    out({"item": a.item, "column": a.column, **({"autoApproved": True} if requested or policy_ok else {}), **({"requested": requested} if requested else {}),
         **({"policy": public(verdict)} if verdict else {})})


def cmd_approve(a):
    """The human passes the gate the story waits at (plan_review -> plan_approved, pr_review -> pr_approved)."""
    c = cfg()
    require_human("`cgp approve` can only be run by you, in a terminal outside a Claude session")
    it = get_item(c, a.item)
    target = {"plan_review": "plan_approved", "pr_review": "pr_approved"}.get(it["column"])
    if not target:
        if it["column"] in ("plan_approved", "pr_approved"):
            out({"item": a.item, "column": it["column"], "unchanged": True, "note": f"already in {it['column']}"})
            return
        die(f"story is in {it['column']}: only a story in plan_review or pr_review can be approved")
    ref = sha = reviewed = None
    if target == "plan_approved" and not it["plan"]:
        die("the story has no plan link to approve")
    if target == "pr_approved":
        ref = parse_pr_ref(c, it["pr"])
        if not ref:
            die("the story has no valid PR link to approve")
        v = pr_view(*ref)
        if not v or not v.get("headRefOid"):
            die("could not read the PR head; not approving")
        msg = changed_since_review(a.item, ref, v)  # reads the record: writing it would clear cleanRebase
        if msg:
            die("refusing to approve: " + msg)
        sha = v["headRefOid"]
        reviewed = load_data()["reviewed"][a.item]["sha"]
        if v["isDraft"]:
            ready_pr(ref)
    set_single(c, a.item, c["fields"]["status"]["id"], c["fields"]["status"]["options"][target])

    def upd(st):
        for w in st["workers"]:
            if w["item"] == a.item:
                w["column"] = target
                clear_phase(w)
                if target in DEFAULT_PHASE:
                    w.update(phase=DEFAULT_PHASE[target], phaseAt=now_iso())
    update_state(upd)
    update_data(lambda d: (d.__setitem__("answered", [i for i in d.get("answered", []) if i != a.item]), reset_strikes(d, a.item)))
    out({"item": a.item, "column": target, "approved": True, **({"sha": sha, "reviewed": reviewed} if sha else {"plan": it["plan"]})})


def send_back(c, item, reason):
    """Put a story back in Implement because what its PR was built on changed (a stacked story's blocker): auto-merge is disarmed, the
    review record forgotten and a comment says why. Not a move through a human gate, so `cgp move` stays as strict as it was."""
    it = get_item(c, item)
    ref = parse_pr_ref(c, it["pr"])
    if ref:
        cancel_auto_merge(*ref)
    forget_review(item)
    if it["column"] != "implement":
        set_single(c, item, c["fields"]["status"]["id"], c["fields"]["status"]["options"]["implement"])

        def upd(st):
            for w in st["workers"]:
                if w["item"] == item:
                    w["column"] = "implement"
                    clear_phase(w)
                    w.update(phase=DEFAULT_PHASE["implement"], phaseAt=now_iso())
        update_state(upd)
    post_comment(it["issueRepo"], it["number"], f"{MARK}\n{reason}")


def snapshot_touches(item):
    """Save the files the story declared when it entered Plan Approved / Implement: `cgp guard` allows guarded paths only from
    this copy, so a worker cannot widen it afterwards by re-declaring its touches."""
    def upd(d):
        snap = d.setdefault("approvedTouches", {})
        if item not in snap:
            snap[item] = list(d.get("touches", {}).get(item, []))
    update_data(upd)


def valid_plan(value):
    """`Skip`, or an https URL on claude.ai or github.com (no credentials, port or whitespace); anything else is not a plan link."""
    if value.lower() == SKIP:
        return True
    if not re.fullmatch(r"[^\s()\[\]<>]+", value):
        return False
    u = urlsplit(value)
    return u.scheme == "https" and u.netloc == (u.hostname or "?") and u.hostname in ("claude.ai", "github.com")


def valid_preview(url):
    """An https URL a link in a Markdown issue body can carry safely: no whitespace, no brackets or parentheses, no `@` in the host."""
    if not url or not re.fullmatch(r"https://[^\s()\[\]<>]+", url):
        return False
    return "@" not in urlsplit(url).netloc


def cmd_set(a):
    c = cfg()
    key = a.field.lower()
    if key not in TEXT_FIELDS or key == "preview":
        die(f"field must be one of {[k for k in TEXT_FIELDS if k != 'preview']} (Preview is written by: cgp preview)")
    value = a.value.strip() if a.value else a.value
    if key == "plan" and value and not valid_plan(value):
        die("Plan must be Skip or an https link on claude.ai or github.com")
    if key == "pr" and value:
        ref = parse_pr_ref(c, value)
        if not ref:
            die("PR must be https://github.com/<linked repo>/pull/<n> on a repo linked to this board")
        it = get_item(c, a.item)
        if it["column"] in ("pr_review", "pr_approved") and (it["pr"] or "").strip() != value:
            die(f"the story is in {it['column']}: the PR the user reviews cannot be replaced. Only the user can fix this: ask them "
                "to move the story out of that column")
        view = pr_view(*ref) or {}
        if view.get("isCrossRepository") is not False:
            die("that PR comes from a fork (or its origin could not be read); the loop only works on its own branches")
        rec = stack_of(a.item)
        if rec:  # a stacked story's PR is aimed at its blocker's branch, or already at the default branch
            if view.get("baseRefName") not in (rec["branch"], default_branch(c, ref[0])):
                die(f"that PR targets {view.get('baseRefName')}: a story stacked on {rec['branch']} opens its PR against that branch")
            why = stack_stale(rec)
            if why:
                die(f"refusing: {why}")
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


# provider -> (the bot whose comment carries the URL, the URL pattern; {pr} is the PR number). "deployments" asks GitHub's
# Deployments API instead, which needs no bot. A repo's .cgp.json can name a provider or give its own {bot, pattern}.
PREVIEW_PROVIDERS = {
    "netlify": (NETLIFY_BOT, r"https://deploy-preview-{pr}--[a-z0-9-]+\.netlify\.app\b"),
    "vercel": ("vercel[bot]", r"https://[a-z0-9-]+\.vercel\.app\b"),
    "cloudflare": ("cloudflare-workers-and-pages[bot]", r"https://[a-z0-9.-]+\.pages\.dev\b"),
}


def preview_url(c, repo, pr):
    """The newest deploy preview URL of a PR from the story's provider, or None."""
    conf = repo_config(c, repo).get("preview", {})
    provider = conf.get("provider") or c["settings"].get("previewProvider") or "netlify"
    if conf.get("bot") and conf.get("pattern"):
        bot, pattern = conf["bot"], conf["pattern"]
    elif provider == "deployments":
        head = (pr_view(repo, pr, check=False) or {}).get("headRefOid")
        for dep in (rest(f"repos/{repo}/deployments?sha={head}") if head else []):
            for st in rest(f"repos/{repo}/deployments/{dep['id']}/statuses"):
                url = st.get("environment_url") or ""
                if st.get("state") == "success" and re.fullmatch(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,500}", url):
                    return url
        return None
    elif provider in PREVIEW_PROVIDERS:
        bot, pattern = PREVIEW_PROVIDERS[provider]
    else:
        die(f"unknown previewProvider {provider!r}; one of {[*PREVIEW_PROVIDERS, 'deployments']}")
    url_re = safe_pattern(pattern, pr)
    if not url_re:
        die("the preview pattern is not a valid regular expression")
    urls = [m.group(0) for cm in rest(f"repos/{repo}/issues/{pr}/comments")
            if (cm.get("user") or {}).get("login") == bot  # only the provider's own bot: anyone can type a URL
            for m in [url_re.search(cm.get("body") or "")] if m]
    return urls[-1] if urls else None


def cmd_preview(a):
    """Copy the deploy preview URL from the story's PR into the Preview field, once the provider has posted it."""
    c = cfg()
    if "preview" not in c["fields"]:
        die("this board has no Preview field; run /cgp:setup again to add it")
    ref = parse_pr_ref(c, get_item(c, a.item)["pr"])
    if not ref:
        die("story has no valid PR field set")
    url = preview_url(c, *ref)
    if url and not valid_preview(url):
        die("the deploy preview URL is not a plain https link; not copying it into the story")
    if not url:
        out({"item": a.item, "preview": None, "note": "no deploy preview on the PR yet"})
        return
    set_text(c, a.item, c["fields"]["preview"], url)
    sync_links(c, a.item)
    out({"item": a.item, "preview": url})


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


def ask_user(c, item, body):
    """Post a question on the story's issue and set Waiting On to You; returns the question's comment and its round number."""
    repo, number, _ = item_issue(item)
    comments = rest(f"repos/{repo}/issues/{number}/comments")
    questions = [cm for cm in comments if QMARK in (cm.get("body") or "") and is_agent(cm)]
    rounds = len(questions)
    marker = f"<!-- cgp:q:{hashlib.sha1(body.encode()).hexdigest()[:12]} -->"  # a retry after a half-done ask finds its own question
    last = questions[-1] if questions else None
    if last and marker in last["body"] and comments[-1] is last:
        cm, rounds = last, rounds - 1  # still the last comment of any kind, so unanswered: reuse it instead of posting it again
    else:
        if rounds >= 3:
            body += "\n\n_This story has needed several rounds of questions; consider rescoping or splitting it._"
        cm = post_comment(repo, number, f"{QMARK}\n{marker}\n{MARK}\n❓ **Question for you** (reply here; work resumes automatically)\n\n{body}")
    set_single(c, item, c["fields"]["waiting"]["id"], c["fields"]["waiting"]["you"])

    advance_cursor(item, asked=cm.get("created_at"))
    return cm, rounds + 1


def cmd_ask(a):
    c = cfg()
    item_issue(a.item)  # fail on a draft before reading stdin
    cm, rnd = ask_user(c, a.item, read_body())
    out({"url": cm["html_url"], "round": rnd})


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


def author_trusted(it):
    """False when the issue was written by someone who may not steer the loop (not you, an owner, or a collaborator with write
    access): its title and body are then only data. Fails closed when the author cannot be read."""
    try:
        issue = json.loads(gh("api", f"repos/{it['issueRepo']}/issues/{it['number']}").stdout)
        return trusted(it["issueRepo"], issue)
    except (SystemExit, ValueError, TypeError):
        return False


def cmd_prepare(a):
    """Everything a worker reads before acting, in one call: the story, feedback, the Q&A history when the user just
    answered, the PR state, and the worktree brought up to date. Each part fails on its own (`error`) so one problem
    does not hide the rest."""
    c = cfg()
    it = get_item(c, a.item)

    it["rating"] = current_rating(load_data(), it)  # what the worker rated it in this column (cgp rate)
    res = {"story": it}
    if it["kind"] != "issue":
        res["note"] = "a draft: convert it first (cgp adopt <item> <owner/repo>); drafts have no comments or worktree"
        out(res)
        return
    res["authorTrusted"] = author_trusted(it)
    d = load_data()
    snap = d.get("approvedTouches", {}).get(a.item)  # None when the story has no approved snapshot
    res["plan"] = {"rating": (d.get("ratings") or {}).get(a.item, {}).get("rating"),  # the last rating, whatever column it was given in
                   "declared": [norm_path(t) for t in d.get("touches", {}).get(a.item, [])], "approved": None if snap is None else [norm_path(t) for t in snap]}
    from .epics import state  # epics imports this module
    split = state(load_data(), a.item)
    if split:
        res["split"] = split  # declared / approved / created sub-stories (cgp split)
    res["feedback"] = call(cmd_feedback, item=a.item)
    if a.item in load_data().get("answered", []):
        res["answers"] = call(cmd_answers, item=a.item)
    ref = parse_pr_ref(c, it["pr"])
    if ref:
        worker_pr(a.item, ref)
        res["prState"] = call(cmd_pr_state, repo=ref[0], pr=ref[1])
    res["repoConfig"] = repo_config(c, it["issueRepo"])  # test / lint commands, reviewers, ... from the repo's .cgp.json
    res["houseRules"] = rules_text(c, it["issueRepo"])  # the default branch's .cgp-rules.md, or null
    res["settings"] = {"draftPRs": bool(c["settings"].get("draftPRs"))}
    res["stack"], stack_wait = prepare_stack(c, it)  # the blocker's branch the worktree is cut from (stackedStories), else null
    res["worktree"] = {"error": stack_wait} if stack_wait else call(cmd_worktree, item=a.item)
    if "error" not in res["worktree"]:
        res["sync"] = call(cmd_sync, item=a.item)
    out(res)
