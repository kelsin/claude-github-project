"""Opt-in policy auto-approval: with the `autoApprove` setting (plan:low,pr:never style) the agent may pass a human gate itself for a
low-risk story whose files are all inside `autoApproveFiles`. It only ever adds approvals and never widens what the user's own Auto
Approve field allows. `evaluate` is called by `cgp move` (see story.cmd_move); every refusal leaves the story for the human column."""
import fnmatch
import functools
import os
import re
import sys
from .util import covers, die, norm_path, now_iso, out
from .gh import rest
from .models import RATINGS
from .store import cfg, load_data, update_data
from .board import get_item, parse_pr_ref
from .pr import pr_view
from .repoconf import merged_globs

LEVELS = ("never",) + RATINGS  # the highest rating a gate may be approved at
GATE_KEYS = {"plan_approved": "plan", "pr_approved": "pr"}
HUMAN_ONLY = ("autoApprove",)  # setting names (prefixes) only a person at a terminal may change
MAX_FILES = 3000  # GitHub lists at most this many files of a pull request: a list that long may be cut off
# Never auto-approved, whatever the settings say: instructions to agents, the plugin's own prompts, docs build and deploy config.
ALWAYS_DENY = ["CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "**/AGENTS.md", "**/SKILL.md", "skills/**", "**/columns/*.md",
               "mkdocs.yml", "**/mkdocs.yml", "book.toml", "**/book.toml", "docusaurus.config.*", "**/docusaurus.config.*",
               "docs/conf.py", "docs/_config.yml", "docs/package.json", "**/.vitepress/**", "**/.docusaurus/**", ".readthedocs.y*ml",
               "**/CLAUDE*.md", "**/GEMINI.md", ".cursor/**", ".windsurf/**", ".agents/**", "**/.claude/**", "**/*.mdc", "**/CONTRIBUTING.md",
               ".github/**"]


def parse(value):
    """{gate: highest rating} from `plan:low,pr:never`; a gate not named stays `never`. Raises ValueError on anything else."""
    res = {"plan": "never", "pr": "never"}
    for pair in (p.strip() for p in (value or "").split(",")):
        gate, _, level = pair.partition(":")
        if pair and (gate not in res or level not in LEVELS):
            raise ValueError(f"autoApprove must look like plan:low,pr:never (gates plan and pr, levels {', '.join(LEVELS)}); got {value!r}")
        if pair:
            res[gate] = level
    return res


def render(policy):
    return ",".join(f"{g}:{policy[g]}" for g in ("plan", "pr"))


def check_globs(globs):
    """Dies on a glob that could match outside the repo or is not a relative path."""
    for g in globs:
        if g.startswith("/") or ".." in g.split("/") or "\\" in g:
            die(f"{g!r} is not a repo-relative glob")


def agent_context():
    """True when the caller may be an agent or a script: a Claude session, or no terminal on stdin."""
    return bool(os.environ.get("CGP_SESSION") or os.environ.get("CLAUDECODE") or not sys.stdin.isatty())


def require_human(message):
    """Dies with `message` unless a person is at a terminal outside a Claude session (stops accidental or instruction-driven use by an
    agent; not a security boundary: a same-user process could fake a terminal)."""
    if agent_context():
        die(message)


@functools.lru_cache(maxsize=None)
def _segment(pattern):
    return re.compile("".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in re.sub(r"\*+", "*", pattern)), re.I)


def glob_match(glob, path):
    """Segment-wise glob: `*` and `?` stay inside one path segment, a segment `**` is any number of segments (also none). Case-insensitive."""
    g, p = glob.split("/"), path.split("/")

    def go(i, j):
        if i == len(g):
            return j == len(p)
        if g[i] == "**":
            return any(go(i + 1, k) for k in range(j, len(p) + 1))
        return j < len(p) and _segment(g[i]).fullmatch(p[j]) is not None and go(i + 1, j + 1)
    return go(0, 0)


def vet(c, repos, names, globs):
    """Why a changed path may not be auto-approved (None when every one may): it must be a plain file path inside `globs`, not on the
    always-deny list and not a guarded file (the guard ignores approvedTouches here: a guard hit is an absolute refusal)."""
    guarded = [g.lower() for g in merged_globs(c, "guardFiles", repos)]
    for n in names:
        if not n or n.startswith("/") or n.endswith("/") or any(s in ("", ".", "..") for s in n.split("/")):
            return f"{n or '(no name)'} is not a plain file path"
        if any(glob_match(g, n) for g in ALWAYS_DENY):
            return f"{n} is never auto-approved"
        low = n.lower()
        if any(fnmatch.fnmatchcase(x, g) for g in guarded for x in (low, os.path.basename(low))):
            return f"{n} is a guarded file"
        if not any(glob_match(g, n) for g in globs):
            return f"{n} is outside autoApproveFiles"
    return None


def current_rating(data, it):
    """The worker's rating of the story, when it was given in the column the story is in now (a rating of an earlier column is stale)."""
    r = (data.get("ratings") or {}).get(it["item"])
    return r["rating"] if r and r.get("column") == it["column"] else None


def evaluate(c, it, gate, author_trusted):
    """None when the policy is off for this gate. Otherwise a verdict: {"approved", "reason"} plus, when approved, `touches` (plan) or
    `sha` and `draft` (pr), the exact things that were checked. Fails closed on anything unread, empty, unexpected or truncated."""
    key = GATE_KEYS[gate]
    try:
        level = parse(c["settings"].get("autoApprove"))[key]
    except ValueError:
        level = "never"
    if level == "never":
        return None
    no = lambda why: {"approved": False, "reason": why}
    data = load_data()
    item, repo, rating = it["item"], it["issueRepo"], current_rating(data, it)
    if it["kind"] != "issue":
        return no("the story is not an issue")
    if it["held"]:
        return no("the story is on Hold")
    if rating is None:
        return no("the story has no risk rating for this column (cgp rate)")
    if LEVELS.index(rating) > LEVELS.index(level):
        return no(f"rated {rating}, above the {level} the policy allows for the {key}")
    if item in data.get("tainted", []):
        return no("a conflict in the branch was resolved by hand")
    globs = c["settings"].get("autoApproveFiles") or []
    if key == "plan":
        files = [norm_path(f) for f in data.get("touches", {}).get(item, [])]
        if not files:
            return no("the plan declares no files (cgp touches)")
        why = vet(c, [repo], files, globs)
        if why:
            return no(why)
        if not author_trusted():
            return no("the story was not written by someone you trust")
        return {"approved": True, "reason": f"rated {rating}; the plan changes {len(files)} file(s), all inside autoApproveFiles", "touches": files}
    ref = parse_pr_ref(c, it["pr"])
    approved = [norm_path(t) for t in data.get("approvedTouches", {}).get(item) or []]
    if not ref:
        return no("the story has no valid PR")
    if it["skipPlan"] or not approved:
        return no("no approved plan files are on record (Plan: Skip or none declared)")
    v = pr_view(*ref, check=False)
    if not v or not v.get("headRefOid"):
        return no("could not read the PR head")
    if ref[0].lower() != repo.lower():
        return no("the PR is not in the story's own repository")
    if v.get("state") != "OPEN":
        return no("the PR is not open")
    if v.get("isCrossRepository") is not False:
        return no("the PR comes from a fork (or its origin could not be read)")
    if v.get("headRefName") != f"cgp/{it['number']}":
        return no("the PR is not the story's own branch")
    if v.get("isDraft") and not c["settings"].get("draftPRs"):
        return no("the PR is a draft that this loop did not open (draftPRs is off)")
    try:
        files = rest(f"repos/{ref[0]}/pulls/{ref[1]}/files")
    except SystemExit:
        return no("could not list the PR's files")
    if not files or len(files) >= MAX_FILES or not all(f.get("filename") for f in files):
        return no("the PR's file list is empty or may be truncated")
    names = [n for f in files for n in (f["filename"], f.get("previous_filename")) if n]  # a rename counts at both ends
    why = vet(c, sorted({repo, ref[0]}), names, globs)
    if why:
        return no(why)
    extra = next((n for n in names if not any(covers(t, n) for t in approved)), None)
    if extra:
        return no(f"{extra} is not in the approved plan's files")
    again = pr_view(*ref, check=False)
    if not again or again.get("headRefOid") != v["headRefOid"]:
        return no("the PR head moved while it was checked")
    if not author_trusted():
        return no("the story was not written by someone you trust")
    return {"approved": True, "reason": f"rated {rating}; the PR changes {len(files)} file(s), all inside autoApproveFiles and the approved plan",
            "sha": v["headRefOid"], "draft": bool(v.get("isDraft"))}


def public(verdict):
    return {k: verdict[k] for k in ("approved", "reason")} if verdict else None


def record_decision(it, gate, verdict):
    """Store the verdict; an approved plan also snapshots the files that were checked (what the guard and the PR gate hold the branch to),
    in the same locked update, so the files evaluated are the files approved."""
    def upd(d):
        d.setdefault("policy", {})[it["item"]] = {"gate": gate, "at": now_iso(), **public(verdict)}
        if verdict["approved"] and "touches" in verdict:
            d.setdefault("approvedTouches", {})[it["item"]] = list(verdict["touches"])
            if it["item"] not in d.setdefault("policyPlans", []):  # sub-stories are only created from a plan a person approved (epics.cmd_split)
                d["policyPlans"].append(it["item"])
    update_data(upd)


def cmd_rate(a):
    c = cfg()
    it = get_item(c, a.item)
    if it["kind"] != "issue":
        die("only an issue can be rated (convert a draft first: cgp adopt)")
    update_data(lambda d: d.setdefault("ratings", {}).__setitem__(a.item, {"rating": a.rating, "column": it["column"]}))
    out({"item": a.item, "rating": a.rating, "column": it["column"]})
