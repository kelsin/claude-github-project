"""Priority, intake, notifications, repo config, preview providers, models, draft PRs."""
import json
import os
import shutil
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


class TestRepoConfig(test_cgp.TestSync):
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
