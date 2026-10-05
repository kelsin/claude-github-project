"""Priority, intake, notifications, repo config, preview providers, models, draft PRs."""
import importlib
import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_cgp

Base, PRBase = test_cgp.Base, test_cgp.PRBase


class TestPriority(Base):
    def test_high_goes_first_low_last_and_hold_is_never_dispatched(self):
        self.setup_board()
        d = self.read_db()
        d["items"][0]["values"]["Priority"] = {"optionId": "o_Low"}
        d["items"][1]["values"]["Priority"] = {"optionId": "o_High"}
        self.write_db(d)
        snap = self.cgp("list")
        self.assertEqual([i["item"] for i in snap["batch"]][:2], ["i2", "i1"])  # i4 has no priority: after both
        d = self.read_db(); d["items"][1]["values"]["Priority"] = {"optionId": "o_Hold"}; self.write_db(d)
        snap = self.cgp("list")
        self.assertNotIn("i2", [i["item"] for i in snap["batch"]])
        self.assertEqual(snap["held"], ["two"])
        self.assertIn("On hold (1): two", self.cgp("status", ok=False).stdout)


class TestIntake(Base):
    def test_add_creates_the_issue_and_puts_it_in_todo_with_a_priority(self):
        self.setup_board()
        r = self.cgp("add", "Fix the thing", "--body", "-", "--priority", "high", input="Details here")
        self.assertEqual((r["repo"], r["number"]), ("acme/app", 101))
        d = self.read_db()
        self.assertEqual(d["repo_issues"]["acme/app"][0]["body"], "Details here")
        item = next(i for i in d["items"] if i["id"] == r["item"])
        self.assertEqual(item["content"]["title"], "Fix the thing")
        self.assertEqual(item["values"]["Priority"], {"optionId": "o_High"})
        self.assertEqual(self.cgp("list")["counts"]["todo"], 4)

    def test_a_bad_priority_creates_nothing(self):
        self.setup_board()
        p = self.cgp("add", "x", "--priority", "urgent", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.read_db().get("repo_issues", {}), {})

    def test_import_adds_labelled_issues_once_and_skips_prs(self):
        self.setup_board()
        d = self.read_db()
        issues = [{"number": 7, "node_id": "I_7", "title": "labelled", "repo": "acme/app", "html_url": "https://github.com/acme/app/issues/7",
                   "labels": [{"name": "cgp"}]},
                  {"number": 8, "node_id": "I_8", "title": "a pr", "repo": "acme/app", "html_url": "x", "labels": [{"name": "cgp"}], "pull_request": {}},
                  {"number": 1, "node_id": "I_1", "title": "one", "repo": "acme/app", "html_url": "x", "labels": [{"name": "cgp"}]}]
        d["repo_issues"] = {"acme/app": issues}
        self.write_db(d)
        r = self.cgp("import", "cgp")
        self.assertEqual([a["number"] for a in r["added"]], [7])  # 1 is on the board already, 8 is a PR
        self.assertEqual(self.cgp("import", "cgp")["added"], [])


class TestNotify(Base):
    def script(self, body='echo "$CGP_EVENT|$CGP_TITLE|$CGP_BOARD" >> "$OUT"'):
        """A notifier the CLI accepts: owned by us and not group/world writable, and outside /tmp (the temp dir may be under it)."""
        here = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, here, True)
        out = os.path.join(self.tmp, "events.txt")
        path = os.path.join(here, "notify.sh")
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\nOUT="{out}"\n{body}\n')
        os.chmod(path, 0o700)
        return path, out

    def seeded(self, command):
        self.setup_board()
        self.force("i2", "plan_review")
        self.cgp("config", "notifyCommand", command)
        self.cgp("list")  # seeds
        self.force("i1", "pr_review")
        self.cgp("list")

    def events(self, out):
        return open(out).read().splitlines() if os.path.exists(out) else []

    def test_events_fire_once_and_enabling_does_not_announce_the_board(self):
        self.setup_board()
        self.force("i2", "plan_review")
        path, out = self.script()
        self.cgp("config", "notifyCommand", path)
        self.cgp("list")  # seeds: nothing is announced
        self.assertEqual(self.events(out), [])
        self.force("i1", "pr_review")
        d = self.read_db(); d["items"][1]["values"]["Waiting On"] = {"optionId": "o_You"}; self.write_db(d)
        self.cgp("list")
        self.cgp("list")  # no change: no new events
        got = sorted(e.rsplit("|", 1)[0] for e in self.events(out))
        self.assertEqual(got, ["review|one", "waiting|two"])

    def test_the_command_runs_without_tokens_in_its_environment(self):
        path, out = self.script('env | grep -E "^(GH_|GITHUB_|GIT_|MY_)" >> "$OUT"; echo ran >> "$OUT"')
        self.env.update(GH_TOKEN="s1", GITHUB_PAT="s2", GIT_ASKPASS="s3", MY_TOKEN="s4")
        self.seeded(path)
        self.assertEqual(self.events(out), ["ran"])

    def test_the_command_must_be_a_safe_absolute_path(self):
        path, out = self.script()
        def bump():  # a new review event each time
            self.force("i1", "plan"); self.cgp("list")
            self.force("i1", "pr_review"); self.cgp("list")
        self.seeded(f"sh {path}")  # not an absolute program
        self.assertEqual(self.events(out), [])
        self.cgp("config", "notifyCommand", path)
        os.chmod(path, 0o770)  # group writable
        bump()
        self.assertEqual(self.events(out), [])
        os.chmod(path, 0o700)
        bump()
        self.assertEqual(len(self.events(out)), 1)

    def test_doctor_warns_about_a_command_that_will_not_run(self):
        self.setup_board()
        self.cgp("config", "notifyCommand", "echo hi")
        self.assertIn("⚠️  Test Board 1: notifyCommand  it will not run: the program must be an absolute path",
                      self.cgp("doctor", ok=False).stdout)

    def test_the_command_can_be_unset(self):
        self.setup_board()
        self.cgp("config", "notifyCommand", "echo hi")
        self.assertEqual(self.cgp("config", "notifyCommand", "")["notifyCommand"], "")


class TestRepoConfig(test_cgp.SyncBase):
    def commit_config(self, text):
        with open(os.path.join(self.clone, ".cgp.json"), "w") as f:
            f.write(text)
        self.git(self.clone, "add", "."); self.git(self.clone, "commit", "-qm", "config")
        self.git(self.clone, "push", "-q", "origin", "HEAD:main")

    def test_config_comes_from_the_default_branch_and_is_cleaned(self):
        self.assertEqual(self.cgp("repo-config", "acme/app")["config"], {})
        self.commit_config(json.dumps({"test": "make test", "lint": 5, "sharedFiles": ["a/*", 3], "evil": "x", "preview": {"provider": "vercel"}}))
        self.assertEqual(self.cgp("repo-config", "acme/app")["config"],
                         {"test": "make test", "sharedFiles": ["a/*"], "preview": {"provider": "vercel"}})
        self.assertEqual(self.cgp("repo-config", "i1")["repo"], "acme/app")

    def test_a_story_branch_cannot_change_its_own_config(self):
        with open(os.path.join(self.wt, ".cgp.json"), "w") as f:
            f.write(json.dumps({"guardFiles": [], "test": "evil"}))
        self.git(self.wt, "add", "."); self.git(self.wt, "commit", "-qm", "mine")
        self.assertEqual(self.cgp("repo-config", "acme/app")["config"], {})

    def test_a_repo_can_add_guarded_files(self):
        self.commit_config(json.dumps({"guardFiles": ["secrets.txt"]}))
        with open(os.path.join(self.wt, "secrets.txt"), "w") as f:
            f.write("x")
        self.git(self.wt, "add", "."); self.git(self.wt, "commit", "-qm", "s")
        p = self.cgp("guard", "i1", ok=False)
        self.assertEqual(p.returncode, 4)
        self.assertEqual(json.loads(p.stdout)["violations"], ["secrets.txt"])

    def test_prepare_returns_the_repo_config_and_settings(self):
        self.commit_config(json.dumps({"lint": "ruff check"}))
        r = self.cgp("prepare", "i1")
        self.assertEqual(r["repoConfig"], {"lint": "ruff check"})
        self.assertEqual(r["settings"], {"draftPRs": False})


class TestPreviewProviders(PRBase):
    def comment(self, login, body):
        d = self.read_db()
        d["comments"].setdefault("acme/app#1", []).append(
            {"body": body, "created_at": "2026-01-01T00:00:01Z", "user": {"login": login, "type": "Bot"}})
        self.write_db(d)

    def test_vercel_and_cloudflare_bots_are_recognised_by_login(self):
        self.cgp("config", "previewProvider", "vercel")
        self.comment("mallory", "https://evil.vercel.app")
        self.assertIsNone(self.cgp("preview", "i1")["preview"])
        self.comment("vercel[bot]", "[Visit Preview](https://site-git-x-team.vercel.app)")
        self.assertEqual(self.cgp("preview", "i1")["preview"], "https://site-git-x-team.vercel.app")
        self.cgp("config", "previewProvider", "cloudflare")
        self.comment("cloudflare-workers-and-pages[bot]", "Preview: https://abc123.site.pages.dev")
        self.assertEqual(self.cgp("preview", "i1")["preview"], "https://abc123.site.pages.dev")

    def test_the_deployments_api_needs_no_bot(self):
        self.cgp("config", "previewProvider", "deployments")
        self.prs(self.view(headRefOid="aaa111"))
        self.db_set(deployments=[{"id": 5, "statuses": [{"state": "pending"}, {"state": "success", "environment_url": "https://pr1.example.dev"},
                                                         ]}])
        self.assertEqual(self.cgp("preview", "i1")["preview"], "https://pr1.example.dev")
        self.db_set(deployments=[{"id": 5, "statuses": [{"state": "success", "environment_url": "javascript:alert(1)"}]}])
        self.assertIsNone(self.cgp("preview", "i1")["preview"])

    def test_an_unknown_provider_is_refused(self):
        self.cgp("config", "previewProvider", "nope")
        self.assertIn("unknown previewProvider", self.cgp("preview", "i1", ok=False).stderr)


class TestModels(Base):
    def test_models_follow_the_rating(self):
        self.setup_board()
        self.cgp("config", "reviewerModel", "low:haiku,medium:sonnet,high:opus")
        self.cgp("config", "plannerModel", "opus")
        self.assertEqual(self.cgp("models", "low"), {"planner": "opus", "reviewer": "haiku", "implementer": None})
        self.assertEqual(self.cgp("models", "high")["reviewer"], "opus")

    def test_bad_values_are_refused(self):
        self.setup_board()
        for bad in ("gpt-4", "low:nope", "extreme:opus"):
            self.assertIn("model setting must be", self.cgp("config", "reviewerModel", bad, ok=False).stderr)


class TestDraftPRs(PRBase):
    def test_a_draft_is_made_ready_when_the_story_goes_to_pr_review(self):
        self.force("i1", "implement")
        self.prs(self.view(isDraft=True))
        self.cgp("move", "i1", "pr_review")
        self.assertEqual([c[2] for c in self.calls("ready")], ["1"])
        self.prs(self.view(isDraft=False))
        self.force("i1", "implement")
        self.cgp("move", "i1", "pr_review")
        self.assertEqual(len(self.calls("ready")), 1)  # a PR that is not a draft is left alone


def node(n, state="OPEN", repo="acme/app", author="kelsin"):
    return {"number": n, "state": state, "repository": {"nameWithOwner": repo}, "author": {"login": author}}


class TestNativeDependencies(Base):
    def native(self, key, *nodes, total=None):
        d = self.read_db()
        d.setdefault("blocked_by", {})[key] = list(nodes)
        if total is not None:
            d.setdefault("blocked_by_total", {})[key] = total
        self.write_db(d)

    def batch(self):
        return [i["item"] for i in self.cgp("list")["batch"]]

    def test_a_github_dependency_holds_a_story_whatever_the_ranks_and_releases_when_closed(self):
        self.setup_board()
        self.force("i1", "plan_approved")  # the blocker (#2) is still in Todo: behind i1, so a cgp block would not hold
        self.native("acme/app#1", node(2))
        snap = self.cgp("list")
        self.assertNotIn("i1", [i["item"] for i in snap["batch"]])
        self.assertEqual(snap["blocked"][0]["blockedBy"], ["two"])
        self.native("acme/app#1", node(2, state="CLOSED"))
        self.assertIn("i1", self.batch())
        self.assertEqual(self.cgp("list")["blocked"], [])

    def test_a_blocker_that_is_not_on_the_board_is_ignored(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.native("acme/app#1", node(99), node(5, repo="other/repo"))
        self.assertIn("i1", self.batch())

    def test_a_todo_story_that_skips_planning_is_gated_by_blocks(self):
        self.setup_board()
        d = self.read_db()
        d["items"][0]["values"]["Plan"] = {"text": "Skip"}
        self.write_db(d)
        self.assertIn("i1", self.batch())
        self.native("acme/app#1", node(2))
        self.assertNotIn("i1", self.batch())
        self.native("acme/app#1")
        self.force("i2", "pr_review")
        self.cgp("block", "i1", "i2")  # cgp's own blocks gate it too
        self.assertNotIn("i1", self.batch())

    def test_more_dependencies_than_were_read_fail_closed(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.native("acme/app#1", total=11)
        snap = self.cgp("list")
        self.assertNotIn("i1", [i["item"] for i in snap["batch"]])
        self.assertEqual(len(snap["blocked"][0]["blockedBy"]), 1)

    def test_a_github_only_cycle_goes_to_a_person_once(self):
        self.setup_board()
        self.native("acme/app#1", node(2))
        self.native("acme/app#2", node(1))
        snap = self.cgp("list")
        self.assertEqual(len(snap["waitingOnYou"]), 1)
        asked = [c for cms in self.read_db()["comments"].values() for c in cms]
        self.assertEqual(len(asked), 1)
        self.assertIn("wait on each other", asked[0]["body"])
        self.cgp("list")
        self.assertEqual(sum(len(c) for c in self.read_db()["comments"].values()), 1)  # not again while the question waits

    def test_a_local_block_that_closes_a_cycle_with_github_is_dropped(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.cgp("block", "i2", "i1")  # i2 (Todo) waits for i1 (ahead of it): holds on its own
        self.native("acme/app#1", node(2))  # now i1 also waits for i2 on GitHub
        snap = self.cgp("list")
        self.assertEqual(snap["waitingOnYou"], [])  # a cycle cgp can break needs no question
        self.assertEqual([b["title"] for b in snap["blocked"]], ["one"])  # GitHub's edge stays, the overlap block goes

    def test_cgp_block_refuses_a_cycle_through_a_github_dependency(self):
        self.setup_board()
        self.native("acme/app#2", node(1))  # two waits for one on GitHub
        p = self.cgp("block", "i1", "i2", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("cycle", p.stderr)

    def test_the_setting_turns_it_off_and_validates(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.native("acme/app#1", node(2))
        self.assertEqual(self.cgp("config")["nativeDependencies"], 1)
        self.assertNotIn("i1", self.batch())
        self.assertEqual(self.cgp("config", "nativeDependencies", "off")["nativeDependencies"], 0)
        self.assertIn("i1", self.batch())
        self.assertEqual(self.cgp("status", "--json")["githubBlockedBy"], [])
        self.assertNotEqual(self.cgp("config", "nativeDependencies", "maybe", ok=False).returncode, 0)
        self.assertEqual(self.cgp("config", "nativeDependencies", "on")["nativeDependencies"], 1)

    def test_a_server_without_the_field_falls_back_to_cgps_own_blocks(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.force("i2", "pr_review")
        d = self.read_db(); d["no_blocked_by_field"] = True; self.write_db(d)
        p = self.cgp("list", ok=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("blockedBy", p.stderr)
        self.assertIn("i1", [i["item"] for i in json.loads(p.stdout)["batch"]])
        self.cgp("block", "i1", "i2")  # local blocks still work
        self.assertNotIn("i1", self.batch())

    def test_status_and_list_agree_and_status_shows_who_opened_the_blocker(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.native("acme/app#1", node(2, author="mallory"), node(77, repo="other/repo"))
        self.assertEqual(self.cgp("list")["blocked"][0]["blockedBy"], ["two"])
        res = self.cgp("status", "--json")
        self.assertEqual(res["blocked"], [{"title": "one", "blockedBy": ["two"]}])
        shown = res["githubBlockedBy"][0]["blockedBy"]
        self.assertEqual([(b["story"], b["author"], b["onBoard"]) for b in shown],
                         [("acme/app#2", "mallory", True), ("other/repo#77", "kelsin", False)])
        text = self.cgp("status", ok=False).stdout
        self.assertIn("acme/app#2 (opened by mallory)", text)
        self.assertIn("other/repo#77 (opened by kelsin; not on this board, ignored)", text)

    def test_doctor_probes_for_the_field(self):
        self.setup_board()
        self.assertIn("✅ Test Board 1: GitHub issue dependencies", self.cgp("doctor", ok=False).stdout)
        d = self.read_db(); d["no_blocked_by_field"] = True; self.write_db(d)
        self.assertIn("⚠️  Test Board 1: GitHub issue dependencies", self.cgp("doctor", ok=False).stdout)
        self.cgp("config", "nativeDependencies", "off")
        self.assertNotIn("GitHub issue dependencies", self.cgp("doctor", ok=False).stdout)

    def test_the_prompts_tell_workers_a_github_block_is_not_theirs_to_clear(self):
        for name in ("shared.md", "plan_approved.md"):
            with open(os.path.join(test_cgp.ROOT, "skills", "run", "columns", name)) as f:
                text = f.read()
            self.assertIn("GitHub", text)
            self.assertIn("`waiting:`", text)

    def order(self, first, second):
        """native_order(second waits for first), in this process, on the fake gh."""
        with mock.patch.dict(os.environ, self.env, clear=True):
            m = test_cgp.load_cgp()
            c = m.cfg()
            return m.native_order(c, m.get_item(c, second), m.get_item(c, first))

    def test_epic_order_is_written_to_github_with_the_blockers_database_id(self):
        self.setup_board()
        self.force("i1", "plan_approved")
        self.assertEqual(self.order("i2", "i1"), "github")
        post = self.read_db()["dependency_posts"][0]
        self.assertEqual(post[:3], ["api", "-X", "POST"])
        self.assertIn("repos/acme/app/issues/1/dependencies/blocked_by", post)
        self.assertEqual(post[post.index("-F") + 1], "issue_id=9002")
        self.assertNotIn("i1", self.batch())  # read back as a dependency

    def test_epic_order_skips_drafts_and_falls_back_locally_when_github_refuses(self):
        self.setup_board()
        self.assertEqual(self.order("i2", "i4"), "local")  # i4 is a draft: no issue to put an edge on
        self.assertNotIn("dependency_posts", self.read_db())
        d = self.read_db(); d["dependencies_rc"] = 1; self.write_db(d)
        self.force("i1", "plan_approved")
        self.assertEqual(self.order("i2", "i1"), "local")
        self.assertEqual(self.order("i2", "i1"), "local")
        with open(os.path.join(self.env["CGP_HOME"], "boards", next(n for n in os.listdir(os.path.join(self.env["CGP_HOME"], "boards")) if n.endswith(".data.json")))) as f:
            self.assertEqual(json.load(f)["epicOrder"], {"i4": ["i2"], "i1": ["i2"]})  # once each
        self.assertNotIn("i1", self.batch())  # the local order holds although i2 ranks behind
        self.force("i2", "done")
        self.assertIn("i1", self.batch())

    def test_epic_order_with_the_setting_off_stays_local(self):
        self.setup_board()
        self.cgp("config", "nativeDependencies", "off")
        self.assertEqual(self.order("i2", "i1"), "local")
        self.assertNotIn("dependency_posts", self.read_db())


class TestPolicyGlobs(unittest.TestCase):
    def match(self, glob, path):
        test_cgp.load_cgp()
        return importlib.import_module("cgp_lib.policy").glob_match(glob, path)

    def test_stars_stay_inside_a_segment_and_double_star_spans_segments(self):
        for glob, path, want in (("*.md", "README.md", True), ("*.md", "docs/a.md", False), ("docs/**/*.md", "docs/a.md", True),
                                 ("docs/**/*.md", "docs/a/b/c.md", True), ("docs/**/*.md", "docs/a/b.txt", False),
                                 ("docs/**/*.md", "src/docs/a.md", False), ("docs/*.md", "docs/a/b.md", False),
                                 ("**/SKILL.md", "SKILL.md", True), ("**/SKILL.md", "x/y/skill.md", True), ("skills/**", "skills/a/b", True),
                                 ("docs/*.md", "docs/a.mdx", False), ("a?.md", "ab.md", True), ("a?.md", "a/.md", False), ("**", "x/y", True)):
            self.assertEqual(self.match(glob, path), want, (glob, path))


class PolicyBase(test_cgp.PRBase):
    def setUp(self):
        super().setUp()
        self.setting(autoApprove="plan:low,pr:low")

    def setting(self, **kw):
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        path = os.path.join(boards, next(n for n in os.listdir(boards) if not n.endswith((".data.json", ".history.json"))))
        with open(path) as f:
            board = json.load(f)
        board["settings"].update(kw)
        with open(path, "w") as f:
            json.dump(board, f)

    def data(self):
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        with open(os.path.join(boards, next(n for n in os.listdir(boards) if n.endswith(".data.json")))) as f:
            return json.load(f)

    def set_data(self, **kw):
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        path = os.path.join(boards, next(n for n in os.listdir(boards) if n.endswith(".data.json")))
        d = self.data()
        d.update(kw)
        with open(path, "w") as f:
            json.dump(d, f)

    def comments(self):
        return [c["body"] for c in self.read_db()["comments"].get("acme/app#1", [])]

    def verdict(self, to):
        r = self.cgp("move", "i1", to)
        return r["column"], r.get("policy")


class TestPlanPolicy(PolicyBase):
    def setUp(self):
        super().setUp()
        self.force("i1", "plan")
        self.cgp("touches", "i1", "docs/guide.md", "README.md")
        self.cgp("rate", "i1", "low")

    def test_off_by_default(self):
        self.setting(autoApprove="plan:never,pr:never")
        self.assertEqual(self.verdict("plan_review"), ("plan_review", None))
        self.assertIn("human approval", self.cgp("move", "i1", "plan_approved", ok=False).stderr)

    def test_a_low_risk_docs_plan_is_approved_and_its_files_snapshotted(self):
        r = self.cgp("move", "i1", "plan_review")
        self.assertEqual((r["column"], r["autoApproved"], r["requested"], r["policy"]["approved"]), ("plan_approved", True, "plan_review", True))
        self.assertEqual(self.data()["approvedTouches"]["i1"], ["docs/guide.md", "README.md"])
        self.assertEqual(self.data()["policy"]["i1"]["gate"], "plan_approved")
        self.assertTrue(any("Auto-approved by the policy (plan)" in c and "<!-- cgp -->" in c for c in self.comments()))

    def test_the_gate_can_be_asked_for_directly(self):
        self.assertEqual(self.verdict("plan_approved")[0], "plan_approved")

    def refused(self, why):
        self.assertIn(why, self.cgp("move", "i1", "plan_approved", ok=False).stderr)
        column, policy = self.verdict("plan_review")
        self.assertEqual(column, "plan_review")
        self.assertFalse(policy["approved"])
        self.assertIn(why, policy["reason"])
        self.force("i1", "plan")  # back where the worker was, for the next case

    def test_the_rating_must_exist_and_not_exceed_the_threshold(self):
        self.cgp("rate", "i1", "medium")
        self.refused("rated medium")
        self.setting(autoApprove="plan:medium,pr:never")
        self.assertEqual(self.verdict("plan_review")[0], "plan_approved")
        self.force("i1", "plan")
        self.set_data(ratings={})
        self.setting(autoApprove="plan:high,pr:never")
        self.refused("no risk rating")

    def test_a_rating_from_another_column_is_stale(self):
        self.force("i1", "todo")
        self.cgp("rate", "i1", "low")
        self.force("i1", "plan")
        self.refused("no risk rating")

    def test_files_outside_the_globs_are_refused(self):
        self.cgp("touches", "i1", "docs/guide.md", "src/app.py")
        self.refused("src/app.py is outside autoApproveFiles")
        self.assertNotIn("approvedTouches", self.data())  # a refusal snapshots nothing

    def test_the_built_in_always_deny_set_cannot_be_opened_up(self):
        self.setting(autoApproveFiles=["**"])
        for path in ("CLAUDE.md", "docs/AGENTS.md", "x/SKILL.md", "skills/a/b.txt", "skills/run/columns/plan.md", "mkdocs.yml"):
            self.cgp("touches", "i1", path)
            self.refused("never auto-approved")

    def test_a_guard_hit_is_an_absolute_refusal_even_when_the_plan_lists_it(self):
        self.setting(autoApproveFiles=["**"])
        for path in (".husky/pre-commit", "Makefile", "sub/Dockerfile"):
            self.cgp("touches", "i1", path)
            self.set_data(approvedTouches={"i1": [path]})  # approvedTouches does not matter here
            self.refused("guarded file")

    def test_empty_touches_directories_and_odd_paths_fail_closed(self):
        self.setting(autoApproveFiles=["**"])
        self.cgp("touches", "i1", "")
        self.refused("declares no files")
        for path in ("docs/", "docs/../x.md", "/etc/x.md"):
            self.cgp("touches", "i1", path)
            self.refused("not a plain file path")

    def test_hold_an_untrusted_author_and_the_wrong_column_veto_it(self):
        d = self.read_db(); d["items"][0]["values"]["Priority"] = {"optionId": "o_Hold"}; self.write_db(d)
        self.refused("Hold")
        d["items"][0]["values"].pop("Priority"); d["issue_authors"] = {"acme/app#1": {"login": "mallory", "author_association": "NONE"}}; self.write_db(d)
        self.refused("not written by someone you trust")
        d["issue_authors"] = {}; self.write_db(d)
        self.force("i1", "todo")
        self.assertEqual(self.cgp("move", "i1", "plan_review")["column"], "plan_review")
        self.assertIn("human approval", self.cgp("move", "i1", "plan_approved", ok=False).stderr)

    def test_a_tainted_story_is_refused(self):
        self.set_data(tainted=["i1"])
        self.refused("by hand")


class TestPRPolicy(PolicyBase):
    FILE = "docs/guide.md"

    def setUp(self):
        super().setUp()
        self.force("i1", "implement")
        self.set_data(approvedTouches={"i1": [self.FILE, "docs/"]})
        self.cgp("rate", "i1", "low")
        self.db_set(pr_files={"acme/app#1": [self.FILE]})
        self.prs(self.view())

    def refused(self, why, **kw):
        self.assertIn(why, self.cgp("move", "i1", "pr_approved", ok=False).stderr)
        column, policy = self.verdict("pr_review")
        self.assertEqual(column, "pr_review")
        self.assertFalse(policy["approved"])
        self.assertIn(why, policy["reason"])
        self.force("i1", "implement")

    def test_a_low_risk_docs_change_goes_straight_to_pr_approved_and_merge_stays_pinned(self):
        r = self.cgp("move", "i1", "pr_review")
        self.assertEqual((r["column"], r["autoApproved"], r["policy"]["approved"]), ("pr_approved", True, True))
        self.assertEqual(self.data()["reviewed"]["i1"]["sha"], "aaa111")
        self.assertTrue(any("Auto-approved by the policy (PR)" in c for c in self.comments()))
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})
        self.prs(self.view(headRefOid="bbb222"))  # a push after the policy's check: it is not delegated, so merge refuses
        self.assertEqual(self.cgp("merge", "i1", ok=False).returncode, 7)

    def test_off_by_default(self):
        self.setting(autoApprove="plan:low,pr:never")
        self.assertEqual(self.verdict("pr_review"), ("pr_review", None))

    def test_the_rating_threshold_and_a_missing_rating_veto_it(self):
        self.cgp("rate", "i1", "medium")
        self.refused("rated medium")
        self.set_data(ratings={})
        self.refused("no risk rating")

    def test_a_tainted_story_is_refused(self):
        self.set_data(tainted=["i1"])
        self.refused("by hand")

    def test_an_empty_file_list_is_refused(self):
        self.db_set(pr_files={"acme/app#1": []})
        self.refused("empty or may be truncated")

    def test_a_possibly_truncated_file_list_is_refused(self):
        self.db_set(pr_files={"acme/app#1": [self.FILE] * 3000})
        self.refused("empty or may be truncated")

    def test_a_long_paginated_file_list_is_read_in_full(self):
        self.db_set(pr_files={"acme/app#1": [self.FILE] * 150 + ["src/late.py"]})
        self.refused("src/late.py is outside autoApproveFiles")  # the file on the second page was seen
        self.db_set(pr_files={"acme/app#1": [self.FILE] * 150})
        self.assertEqual(self.verdict("pr_review")[0], "pr_approved")

    def test_a_fork_is_refused(self):
        self.prs(self.view(isCrossRepository=True))
        self.refused("fork")

    def test_a_draft_is_refused_unless_draftprs_opened_it_and_is_then_made_ready(self):
        self.prs(self.view(isDraft=True))
        self.refused("draft")
        self.setting(draftPRs=1)
        self.db_set(calls=[])  # the refused move above readied the draft for its reviewer
        self.assertEqual(self.verdict("pr_review")[0], "pr_approved")
        self.assertEqual([c[2] for c in self.calls("ready")], ["1"])
        self.assertEqual(self.data()["reviewed"]["i1"]["sha"], "aaa111")

    def test_renames_and_deletions_outside_the_globs_are_refused(self):
        self.db_set(pr_files={"acme/app#1": [{"filename": self.FILE, "status": "renamed", "previous_filename": "src/old.py"}]})
        self.refused("src/old.py is outside autoApproveFiles")
        self.db_set(pr_files={"acme/app#1": [{"filename": "src/gone.py", "status": "removed"}]})
        self.refused("src/gone.py is outside autoApproveFiles")
        self.db_set(pr_files={"acme/app#1": [{"filename": self.FILE, "status": "renamed", "previous_filename": "docs/old.md"}]})
        self.assertEqual(self.verdict("pr_review")[0], "pr_approved")  # a rename inside the globs and the plan's files is fine

    def test_a_diff_beyond_the_approved_plan_files_is_refused(self):
        self.db_set(pr_files={"acme/app#1": [self.FILE, "README.md"]})
        self.refused("README.md is not in the approved plan")
        self.set_data(approvedTouches={})
        self.refused("no approved plan files")

    def test_always_deny_guard_and_skip_plan_veto_it(self):
        self.setting(autoApproveFiles=["**"])
        self.set_data(approvedTouches={"i1": ["CLAUDE.md", ".husky/pre-commit"]})
        for name, why in (("CLAUDE.md", "never auto-approved"), (".husky/pre-commit", "guarded file")):
            self.db_set(pr_files={"acme/app#1": [name]})
            self.refused(why)
        self.set_data(approvedTouches={"i1": [self.FILE]})
        self.db_set(pr_files={"acme/app#1": [self.FILE]})
        d = self.read_db(); d["items"][0]["values"]["Plan"] = {"text": "Skip"}; self.write_db(d)
        self.refused("Plan: Skip")

    def test_an_untrusted_author_is_refused(self):
        d = self.read_db(); d["issue_authors"] = {"acme/app#1": {"login": "mallory", "author_association": "NONE"}}; self.write_db(d)
        self.refused("not written by someone you trust")

    def test_a_push_while_the_policy_checks_hands_the_story_to_a_person(self):
        self.prs(self.view(), self.view(), self.view(headRefOid="bbb222"))  # read, read after the files, read after recording
        column, policy = self.verdict("pr_review")
        self.assertEqual(column, "pr_review")
        self.assertIn("moved", policy["reason"])
        self.assertEqual(self.data()["reviewed"]["i1"]["sha"], "bbb222")  # the commit the person is shown

        self.prs(self.view(), self.view(headRefOid="bbb222"))
        self.force("i1", "implement")
        self.assertIn("moved", self.verdict("pr_review")[1]["reason"])


class TestPolicySettings(PolicyBase):
    def tty(self, *args, env=None):
        """`cgp` with a terminal as stdin, outside any Claude session."""
        e = {**self.env, **(env or {})}
        for k in ("CGP_SESSION", "CLAUDECODE"):
            e.pop(k, None)
        e.update(env or {})
        master, slave = pty.openpty()
        try:
            return subprocess.run([sys.executable, test_cgp.CGP, *args], stdin=slave, capture_output=True, text=True, cwd=self.tmp, env=e)
        finally:
            os.close(slave)
            os.close(master)

    def test_a_person_at_a_terminal_can_set_the_policy_and_it_is_normalised(self):
        p = self.tty("config", "autoApprove", "pr:medium, plan:low")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["autoApprove"], "plan:low,pr:medium")
        p = self.tty("config", "autoApproveFiles", "docs/**/*.md, *.txt")
        self.assertEqual(json.loads(p.stdout)["autoApproveFiles"], ["docs/**/*.md", "*.txt"])

    def test_agents_and_scripts_cannot_change_it(self):
        for env, kw in (({"CGP_SESSION": "abc"}, {}), ({"CLAUDECODE": "1"}, {}), ({}, {"input": ""})):  # a session, Claude Code, no terminal
            p = self.cgp("config", "autoApprove", "plan:high,pr:high", ok=False, env=env, **kw)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("only be changed by you", p.stderr)
            p = self.cgp("config", "autoApproveFiles", "**", ok=False, env=env, **kw)
            self.assertIn("only be changed by you", p.stderr)
        p = self.tty("config", "autoApprove", "plan:high", env={"CGP_SESSION": "abc"})
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.cgp("config")["autoApprove"], "plan:low,pr:low")  # unchanged, and readable by anyone

    def test_ordinary_settings_stay_open_to_workers(self):
        self.assertEqual(self.cgp("config", "concurrency", "2", env={"CGP_SESSION": "abc"})["concurrency"], 2)

    def test_bad_values_are_rejected(self):
        for args in (("autoApprove", "plan:urgent"), ("autoApprove", "merge:low"), ("autoApprove", "low"), ("autoApproveFiles", "/etc/*"),
                     ("autoApproveFiles", "docs/../*.md")):
            p = self.tty("config", *args)
            self.assertNotEqual(p.returncode, 0, args)
        self.assertEqual(self.cgp("config")["autoApprove"], "plan:low,pr:low")

    def test_a_change_notifies_and_status_and_doctor_show_the_policy(self):
        here = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, here, True)
        out = os.path.join(self.tmp, "events.txt")
        path = os.path.join(here, "notify.sh")
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\necho "$CGP_EVENT|$CGP_TITLE" >> "{out}"\n')
        os.chmod(path, 0o700)
        self.setting(notifyCommand=path)
        self.tty("config", "autoApprove", "plan:high")
        with open(out) as f:
            self.assertEqual(f.read().strip(), "policy|autoApprove set to plan:high,pr:never")
        self.assertIn("auto-approval policy: plan:high,pr:never", self.cgp("status", ok=False).stdout)
        self.assertIn("auto-approval policy  autoApprove plan:high,pr:never", self.cgp("doctor", ok=False).stdout)

    def test_ratings_are_per_column_and_shown_in_list(self):
        self.force("i1", "plan")
        self.assertIsNone(next(i for i in self.cgp("list")["items"] if i["item"] == "i1")["rating"])
        self.assertEqual(self.cgp("rate", "i1", "high"), {"item": "i1", "rating": "high", "column": "plan"})
        self.assertEqual(next(i for i in self.cgp("list")["items"] if i["item"] == "i1")["rating"], "high")
        self.force("i1", "implement")
        self.assertIsNone(next(i for i in self.cgp("list")["items"] if i["item"] == "i1")["rating"])
        self.assertNotEqual(self.cgp("rate", "i1", "urgent", ok=False).returncode, 0)
        self.assertNotEqual(self.cgp("rate", "i4", "low", ok=False).returncode, 0)  # a draft

    def test_the_column_prompts_tell_workers_to_rate_and_fall_back(self):
        for name in ("plan.md", "implement.md"):
            with open(os.path.join(test_cgp.ROOT, "skills", "run", "columns", name)) as f:
                text = f.read()
            self.assertIn("`CGP rate <item>", text)
            self.assertIn("policy", text)


class TestSubStories(PolicyBase):
    SPECS = [{"title": "Part one", "scope": "Do the first half.", "files": ["docs/a.md"]},
             {"title": "Part two", "scope": "Do the second half.", "files": ["src/b.py"], "after": ["Part one"]}]

    def declare(self, specs=None, ok=True):
        return self.cgp("split", "i1", "--declare", input=json.dumps(self.SPECS if specs is None else specs), ok=ok)

    def approved(self, specs=None):
        """The parent has declared a split and a person approved the plan (the loop snapshots the declaration)."""
        self.force("i1", "plan")
        self.declare(specs)
        self.force("i1", "plan_approved")
        self.cgp("list")

    def split(self):
        self.approved()
        return self.cgp("split", "i1")["children"]

    def issues(self):
        return self.read_db().get("repo_issues", {}).get("acme/app", [])

    def finish(self, kids, merged=True):
        d = self.read_db()
        d["prs"] = {}
        for k in kids:
            item = next(i for i in d["items"] if i["id"] == k["item"])
            item["values"]["Status"] = {"optionId": next(o["id"] for o in next(f for f in d["fields"] if f["name"] == "Status")["options"] if "Done" in o["name"])}
            if merged:
                item["values"]["PR"] = {"text": f"https://github.com/acme/app/pull/{k['number'] + 100}"}
                d["prs"][f"acme/app#{k['number'] + 100}"] = {"state": "MERGED", "isDraft": False, "headRefOid": "bbb", "headRefName": "x"}
        self.write_db(d)

    def test_split_creates_only_the_approved_declaration(self):
        kids = self.split()
        self.assertEqual([k["title"] for k in kids], ["Part one", "Part two"])
        first = self.issues()[0]
        self.assertTrue(first["body"].endswith("Part of acme/app#1"))
        d = self.read_db()
        for k in kids:  # in Todo with the plan skipped, and their files recorded for the guard
            item = next(i for i in d["items"] if i["id"] == k["item"])
            self.assertEqual(item["values"]["Plan"], {"text": "Skip"})
            self.assertEqual(self.data()["approvedTouches"][k["item"]], ["docs/a.md"] if k["title"] == "Part one" else ["src/b.py"])
            self.assertEqual(self.data()["parents"][k["item"]], "i1")
        self.cgp("list")  # a sub-story in Todo keeps its approved files
        self.assertIn(kids[0]["item"], self.data()["approvedTouches"])
        self.assertTrue(any("Split into 2 sub-stories" in c for c in self.comments()))

    def test_order_is_written_to_github_or_kept_locally(self):
        kids = self.split()
        self.assertEqual(len(self.read_db()["dependency_posts"]), 1)
        self.assertEqual(self.data()["epicOrder"], {})
        self.assertNotIn(kids[1]["item"], [i["item"] for i in self.cgp("list")["batch"]])  # Part two waits for Part one
        self.assertIn(kids[0]["item"], [i["item"] for i in self.cgp("list")["batch"]])

    def test_order_stays_local_when_native_dependencies_are_off(self):
        self.setting(nativeDependencies=0)
        kids = self.split()
        self.assertEqual(self.data()["epicOrder"], {kids[1]["item"]: [kids[0]["item"]]})
        self.assertNotIn("dependency_posts", self.read_db())

    def test_an_invalid_declaration_is_stored_and_creates_nothing(self):
        self.force("i1", "plan")
        for bad in ([], [{"title": "x"}], [{**self.SPECS[0], "after": ["nope"]}], [{**self.SPECS[0], "repo": "other/repo"}],
                    [{**self.SPECS[0], "title": "a\nb"}], [dict(self.SPECS[0], after=["Part one"])],
                    [dict(self.SPECS[0], after=["Part two"]), dict(self.SPECS[1], after=["Part one"])], self.SPECS + [self.SPECS[0]]):
            self.assertNotEqual(self.declare(bad, ok=False).returncode, 0, bad)
        self.assertEqual(self.data().get("splits", {}), {})

    def test_one_bad_spec_in_the_snapshot_creates_nothing(self):
        self.approved()
        bad = self.data()["approvedSplits"]["i1"] + [{"title": "Elsewhere", "scope": "x", "files": [], "repo": "other/repo", "after": []}]
        self.set_data(approvedSplits={"i1": bad})
        self.assertIn("not a repo linked", self.cgp("split", "i1", ok=False).stderr)
        self.assertEqual(self.issues(), [])

    def test_at_most_ten_sub_stories(self):
        self.force("i1", "plan")
        many = [{"title": f"s{n}", "scope": "x", "files": []} for n in range(11)]
        self.assertIn("at most 10", self.declare(many, ok=False).stderr)
        self.declare(many[:10])
        self.assertEqual(len(self.data()["splits"]["i1"]), 10)

    def test_refusals(self):
        self.force("i1", "plan")
        self.declare()
        self.assertIn("works only in", self.cgp("split", "i1", ok=False).stderr)  # before the plan is approved
        self.force("i1", "plan_approved")
        self.assertIn("works only in", self.declare(ok=False).stderr)  # no new declaration after approval
        self.set_data(policyPlans=["i1"])
        self.assertIn("auto-approval policy", self.cgp("split", "i1", ok=False).stderr)
        self.assertEqual(self.issues(), [])
        self.set_data(policyPlans=[], approvedSplits={}, splits={})
        self.assertIn("declare", self.cgp("split", "i1", ok=False).stderr.lower())  # nothing approved to create

    def test_a_policy_approved_plan_is_marked(self):
        self.force("i1", "plan")
        self.cgp("touches", "i1", "docs/guide.md")
        self.cgp("rate", "i1", "low")
        self.declare()
        self.assertEqual(self.cgp("move", "i1", "plan_review")["column"], "plan_approved")
        self.assertEqual(self.data()["policyPlans"], ["i1"])
        self.assertIn("auto-approval policy", self.cgp("split", "i1", ok=False).stderr)
        self.force("i1", "plan")
        self.cgp("list")
        self.assertEqual(self.data()["policyPlans"], [])  # re-planned: a person approves the next one

    def test_a_policy_decision_on_record_blocks_a_split_even_without_the_marker(self):
        self.approved()
        self.set_data(policyPlans=[], policy={"i1": {"gate": "plan_approved", "approved": True, "reason": "x", "at": "now"}})
        self.assertIn("auto-approval policy", self.cgp("split", "i1", ok=False).stderr)
        self.assertEqual(self.issues(), [])

    def test_a_skip_plan_story_cannot_declare_or_split(self):
        self.force("i1", "plan")
        self.cgp("set", "i1", "plan", "Skip")
        self.assertIn("skips its plan", self.declare(ok=False).stderr)
        self.set_data(approvedSplits={"i1": self.SPECS})
        self.force("i1", "plan_approved")
        self.assertIn("skips its plan", self.cgp("split", "i1", ok=False).stderr)

    def test_a_spec_naming_a_guarded_file_needs_the_plan_to_list_it(self):
        self.force("i1", "plan")
        bad = [{"title": "CI", "scope": "x", "files": [".github/workflows/ci.yml"]}]
        self.assertIn("guarded", self.declare(bad, ok=False).stderr)
        self.assertIn("guarded", self.declare([{"title": "A", "scope": "x", "files": ["CLAUDE.md"]}], ok=False).stderr)
        self.cgp("touches", "i1", ".github/workflows/ci.yml")
        self.declare(bad)

    def test_a_second_split_is_a_no_op_and_a_short_one_is_not_closed(self):
        kids = self.split()
        n = len(self.issues())
        again = self.cgp("split", "i1")["children"]
        self.assertEqual((len(self.issues()), [k["item"] for k in again]), (n, [k["item"] for k in kids]))
        self.assertEqual(len([c for c in self.comments() if "Split into" in c]), 1)
        d = self.data()
        self.set_data(children={"i1": d["children"]["i1"][:1]})  # one child never got created
        self.finish(kids[:1])
        snap = self.cgp("list")
        self.assertNotEqual(next(i for i in snap["items"] if i["item"] == "i1")["column"], "done")
        self.assertNotIn("issue_closes", self.read_db())
        self.assertEqual(len([c for c in self.comments() if "approved sub-stories were created" in c]), 1)
        self.cgp("list")
        self.assertEqual(len([c for c in self.comments() if "approved sub-stories were created" in c]), 1)

    def test_a_sub_story_cannot_be_split_or_declare(self):
        kids = self.split()
        self.force(kids[0]["item"], "plan")
        for extra in ((), ("--declare",)):
            p = self.cgp("split", kids[0]["item"], *extra, input=json.dumps(self.SPECS), ok=False)
            self.assertIn("one nesting level", p.stderr)

    def test_the_parent_waits_for_its_sub_stories_and_is_not_dispatched(self):
        kids = self.split()
        snap = self.cgp("list")
        self.assertNotIn("i1", [i["item"] for i in snap["batch"]])
        row = next(b for b in snap["blocked"] if b["title"] == "one")
        self.assertEqual(sorted(row["blockedBy"]), ["Part one", "Part two"])
        self.assertEqual(next(i for i in snap["items"] if i["item"] == "i1")["waitingOn"], "Another story")
        self.assertEqual(self.cgp("prepare", "i1")["split"]["created"], ["Part one", "Part two"])
        self.assertIn("cycle", self.cgp("block", kids[0]["item"], "i1", ok=False).stderr)  # the parent already waits for it

    def test_the_parent_closes_when_every_sub_story_merged(self):
        kids = self.split()
        self.finish(kids[:1])
        self.assertNotIn("i1", [i["item"] for i in self.cgp("list")["items"] if i["column"] == "done"])
        self.finish(kids)
        snap = self.cgp("list")
        self.assertEqual(next(i for i in snap["items"] if i["item"] == "i1")["column"], "done")
        self.assertEqual(self.read_db()["issue_closes"], ["acme/app#1"])
        done = [c for c in self.comments() if "All sub-stories are done" in c][0]
        self.assertIn("pull/201", done)
        self.assertIn("pull/202", done)

    def test_a_sub_story_closed_any_other_way_goes_to_a_person_once(self):
        kids = self.split()
        self.finish(kids, merged=False)
        snap = self.cgp("list")
        self.assertEqual([i["item"] for i in snap["waitingOnYou"]], ["i1"])
        self.assertNotIn("issue_closes", self.read_db())
        self.assertEqual(len([c for c in self.comments() if "not by a merged PR" in c]), 1)
        self.cgp("list")
        self.assertEqual(len([c for c in self.comments() if "not by a merged PR" in c]), 1)

    def test_a_sub_story_written_by_someone_else_does_not_count(self):
        kids = self.split()
        self.finish(kids)
        d = self.read_db()
        d["issue_authors"] = {"acme/app#101": {"login": "mallory", "author_association": "NONE"}}
        self.write_db(d)
        self.cgp("list")
        self.assertNotIn("issue_closes", self.read_db())

    def test_the_prompts_split_and_stop(self):
        read = TestMandatorySubagents.read
        approved = read(self, "plan_approved.md")
        self.assertIn("CGP split <item>", approved)
        self.assertIn("stop WITHOUT moving", approved.split("CGP split <item>")[1].split("\n")[0])
        self.assertIn("CGP split <item> --declare", read(self, "plan.md"))
        self.assertIn("Sub-stories", read(self, "plan_artifact.md"))
        self.assertIn("shorter than", approved)


class TestMandatorySubagents(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(test_cgp.ROOT, "skills", "run", "columns", name)) as f:
            return f.read()

    def test_shared_rules_require_sub_agents(self):
        s = self.read("shared.md")
        self.assertIn("at least one", s)
        self.assertNotIn("do the same passes yourself", s)
        self.assertNotIn("ran inline", s)
        with open(os.path.join(test_cgp.ROOT, "skills", "run", "SKILL.md")) as f:
            self.assertIn("Agent tool", f.read())

    def test_column_files_spawn_at_least_one(self):
        for name in ("implement.md", "plan.md"):
            self.assertIn("at least one", self.read(name))
