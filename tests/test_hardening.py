"""Tests for the safety gates and tools added after the first review: worker start, review-bound merging, guard,
stalled workers, status, doctor, gc, setup --dry-run."""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import time

import test_cgp

Base, PRBase = test_cgp.Base, test_cgp.PRBase


class TestWorkerStart(Base):
    def test_title_and_column_come_from_the_board_not_the_command_line(self):
        self.setup_board()
        self.cgp("worker", "start", "i1", "todo", "$(touch pwned)")  # an old caller passing the title
        w = self.state()["workers"][0]
        self.assertEqual((w["title"], w["column"]), ("one", "todo"))
        self.cgp("worker", "stop", "i1")
        self.force("i2", "implement")
        self.cgp("worker", "start", "i2")  # the column is looked up too
        w = self.state()["workers"][0]
        self.assertEqual((w["title"], w["column"]), ("two", "implement"))
        self.assertFalse(os.path.exists("pwned"))


class TestReviewedMerge(PRBase):
    def reviewed(self, sha="aaa111"):
        self.prs(self.view(headRefOid=sha))
        self.force("i1", "implement")
        self.cgp("move", "i1", "pr_review")  # records the commit the user will see
        self.force("i1", "pr_approved")

    def test_merge_refuses_a_head_that_changed_after_review(self):
        self.reviewed()
        self.prs(self.view(headRefOid="bbb222"))
        for cmd in (("merge", "i1"), ("merge-wait", "i1", "--interval", "0")):
            p = self.cgp(*cmd, ok=False)
            self.assertEqual(p.returncode, 7, p.stderr)
            self.assertIn("reviewed aaa111", p.stderr)
        # nothing was requested, and each refusal disarmed any auto-merge already armed on the unreviewed push
        self.assertEqual(self.calls("merge"), [["pr", "merge", "1", "-R", "acme/app", "--disable-auto"]] * 2)
        self.prs(self.view(headRefOid="aaa111"))
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})

    def test_rereview_records_the_new_head(self):
        self.reviewed()
        self.prs(self.view(headRefOid="bbb222"))
        self.force("i1", "implement")
        self.cgp("move", "i1", "pr_review")
        self.force("i1", "pr_approved")
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})

    def test_a_story_with_auto_approve_pr_is_not_held_to_a_reviewed_commit(self):
        self.reviewed()
        d = self.read_db(); d["items"][0]["values"]["Auto Approve"] = {"optionId": "o_PR"}; self.write_db(d)  # made by setup
        self.prs(self.view(headRefOid="bbb222"))
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})

    def test_only_the_loops_own_branch_is_merged(self):
        self.prs(self.view(headRefName="someone/else"))
        self.assertIn("did not open", self.cgp("merge", "i1", ok=False).stderr)

    def test_drafts_are_not_merged(self):
        self.prs(self.view(isDraft=True))
        self.assertIn("draft", self.cgp("merge", "i1", ok=False).stderr)

    def test_direct_merge_needs_green_checks(self):
        self.db_set(auto_merge_unavailable=True, checks=[{"name": "t", "bucket": "pending", "link": "", "workflow": "w"}])
        self.assertFalse(self.cgp("merge", "i1")["requested"])
        self.assertEqual([c for c in self.calls("merge") if "--auto" not in c], [])
        self.db_set(checks=[{"name": "t", "bucket": "pass", "link": "", "workflow": "w"}])
        self.assertTrue(self.cgp("merge", "i1")["requested"])


class TestSyncRecords(test_cgp.TestSync):
    def data(self):
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        try:
            with open(os.path.join(boards, "P1.data.json")) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_data(self, **kw):
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        os.makedirs(boards, exist_ok=True)
        merged = {**self.data(), **kw}
        with open(os.path.join(boards, "P1.data.json"), "w") as f:
            json.dump(merged, f)

    def review_head(self):
        """The user reviewed the worktree's current commit."""
        self.save_data(reviewed={"i1": {"pr": "acme/app#1", "sha": subprocess_out(self.wt, "rev-parse", "HEAD")}})

    def commit(self, name, text="x\n"):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write(text)
        self.git(self.wt, "add", "."); self.git(self.wt, "commit", "-qm", name)

    def test_a_clean_rebase_is_allowed_a_conflicting_one_taints(self):
        self.commit("g.txt")
        self.review_head()
        self.push_to_main("one\ntwo\n")
        self.assertEqual(self.cgp("sync", "i1")["state"], "rebased")
        head = subprocess_out(self.wt, "rev-parse", "HEAD")
        self.assertEqual(self.data()["cleanRebase"]["i1"], [head])
        with open(os.path.join(self.wt, "f.txt"), "w") as f:
            f.write("mine\n")
        self.git(self.wt, "commit", "-qam", "mine")
        self.push_to_main("theirs\n")
        self.assertEqual(self.cgp("sync", "i1")["state"], "conflict")
        self.assertIn("i1", self.data()["tainted"])

    def test_a_rebase_of_an_unreviewed_commit_is_not_allowed(self):
        self.commit("g.txt")
        self.review_head()
        self.commit("h.txt")  # a local commit nobody reviewed
        self.push_to_main("one\ntwo\n")
        self.assertEqual(self.cgp("sync", "i1")["state"], "rebased")
        self.assertEqual(self.data()["reviewed"]["i1"]["sha"] != subprocess_out(self.wt, "rev-parse", "HEAD"), True)
        self.assertEqual(self.data().get("cleanRebase", {}).get("i1", []), [])

    def test_a_story_with_no_review_record_allows_nothing(self):
        self.commit("g.txt")
        self.push_to_main("one\ntwo\n")
        self.assertEqual(self.cgp("sync", "i1")["state"], "rebased")
        self.assertEqual(self.data().get("cleanRebase", {}), {})

    def test_guard_covers_more_than_github_files_and_is_configurable(self):
        self.commit(".npmrc", "registry=https://evil.example\n")
        p = self.cgp("guard", "i1", ok=False)
        self.assertEqual(p.returncode, 4)
        self.assertEqual(json.loads(p.stdout)["violations"], [".npmrc"])
        self.cgp("touches", "i1", ".npmrc")
        self.assertTrue(self.cgp("guard", "i1")["ok"])
        self.cgp("touches", "i1", "")
        self.cgp("config", "guardFiles", "")
        self.assertFalse(self.cgp("guard", "i1", ok=False).returncode == 0)  # the built-in guard cannot be emptied
        self.cgp("config", "guardFiles", "extra.txt")
        self.commit("extra.txt")
        self.assertEqual(sorted(json.loads(self.cgp("guard", "i1", ok=False).stdout)["violations"]), [".npmrc", "extra.txt"])

    def test_guard_matches_case_insensitively_and_covers_the_new_defaults(self):
        for name in ("makefile", ".cgp.json", ".githooks/pre-commit", "Jenkinsfile", ".envrc"):
            os.makedirs(os.path.dirname(os.path.join(self.wt, name)), exist_ok=True)
            self.commit(name)
        v = json.loads(self.cgp("guard", "i1", ok=False).stdout)["violations"]
        self.assertEqual(sorted(v), sorted(["makefile", ".cgp.json", ".githooks/pre-commit", "Jenkinsfile", ".envrc"]))

    def test_guard_does_not_let_a_quoted_name_through(self):
        os.makedirs(os.path.join(self.wt, ".github"))
        self.commit(".github/wörkflow\u00e9.yml")  # git quotes non-ASCII names unless -z is used
        self.assertEqual(json.loads(self.cgp("guard", "i1", ok=False).stdout)["violations"], [".github/wörkflow\u00e9.yml"])

    def test_guard_allows_only_the_touches_declared_at_approval(self):
        self.commit(".npmrc", "x\n")
        self.cgp("touches", "i1", ".npmrc")
        self.save_data(approvedTouches={"i1": ["docs/"]})  # approved with different files
        self.assertEqual(self.cgp("guard", "i1", ok=False).returncode, 4)
        self.save_data(approvedTouches={"i1": [".npmrc"]})
        self.assertTrue(self.cgp("guard", "i1")["ok"])

    def test_guard_allows_nothing_guarded_for_a_skipped_plan(self):
        self.commit(".npmrc", "x\n")
        self.cgp("touches", "i1", ".npmrc")
        d = self.read_db(); d["items"][0]["values"]["Plan"] = {"text": "Skip"}; self.write_db(d)
        self.assertEqual(self.cgp("guard", "i1", ok=False).returncode, 4)

    def test_moving_to_implement_snapshots_the_touches_and_replanning_forgets_them(self):
        self.cgp("touches", "i1", "a.py")
        self.force("i1", "plan_approved")
        self.cgp("list", "--brief")
        self.assertEqual(self.data()["approvedTouches"]["i1"], ["a.py"])
        self.cgp("touches", "i1", "a.py", ".npmrc")  # re-declared later: not what was approved
        self.cgp("list", "--brief")
        self.assertEqual(self.data()["approvedTouches"]["i1"], ["a.py"])
        self.cgp("move", "i1", "implement")
        self.assertEqual(self.data()["approvedTouches"]["i1"], ["a.py", ".npmrc"])  # entering Implement from the approved plan
        self.force("i1", "plan")
        self.cgp("list", "--brief")
        self.assertNotIn("i1", self.data()["approvedTouches"])


def subprocess_out(path, *args):
    import subprocess
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout.strip()


class TestStalledWorkers(Base):
    def test_old_workers_are_reported(self):
        self.setup_board()
        self.cgp("worker", "start", "i1")
        self.assertEqual(self.cgp("list", "--brief")["stalled"], [])
        path = os.path.join(self.env["CGP_HOME"], "state-default.json")
        with open(path) as f:
            st = json.load(f)
        st["workers"][0]["startedAt"] = "2020-01-01T00:00:00Z"
        with open(path, "w") as f:
            json.dump(st, f)
        self.assertEqual([w["item"] for w in self.cgp("list", "--brief")["stalled"]], ["i1"])
        self.cgp("config", "maxWorkerMinutes", "0")
        self.assertEqual(self.cgp("list", "--brief")["stalled"], [])


class TestStatus(Base):
    def test_table_and_json(self):
        self.setup_board()
        self.force("i2", "plan_review")
        before = self.read_db()
        text = self.cgp("status", ok=False)  # not JSON
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("🙋 Plan Review (1)", text.stdout)
        self.assertIn("two", text.stdout)
        self.assertEqual(self.cgp("status", "--json")["counts"]["plan_review"], 1)
        self.assertEqual(self.read_db()["items"], before["items"])  # reads only


class TestSetupDryRun(Base):
    def test_dry_run_touches_nothing(self):
        before = self.read_db()
        r = self.cgp("setup", "https://github.com/orgs/acme/projects/1", "--repo", "acme/app", "--dry-run")
        self.assertTrue(r["dryRun"])
        self.assertIn("Waiting On", r["fieldsToAdd"])
        self.assertEqual(r["viewsToAdd"], ["Tasks", "Board", "Approvals"])
        self.assertEqual(self.read_db(), before)
        self.assertFalse(os.path.exists(os.path.join(self.env["CGP_HOME"], "boards")))


class TestPriorityFirst(Base):
    def seed_fields(self):
        d = self.read_db()
        d["fields"] += [{"id": f"F_{n}", "name": n, "dataType": "x"} for n in ("Title", "Repository", "Assignees")]
        self.write_db(d)

    def tasks(self):
        return next(v for v in self.read_db()["views"] if v["name"] == "Tasks")

    def test_new_tasks_view_has_priority_after_title(self):
        self.seed_fields()
        self.setup_board()
        ids = self.tasks()["fieldIds"]
        self.assertEqual(ids[:2], ["F_Title", "F_Priority"])

    def test_existing_tasks_view_is_reordered_once(self):
        self.seed_fields()
        self.setup_board()
        d = self.read_db()
        for v in d["views"]:
            v["fieldIds"] = [i for i in v["fieldIds"] if i != "F_Priority"] + (["F_Priority"] if v["name"] == "Tasks" else [])
        board_before = next(v for v in d["views"] if v["name"] == "Board")["fieldIds"]
        tasks = next(v for v in d["views"] if v["name"] == "Tasks")
        tasks["fieldIds"].remove("F_Repository")  # a column the user hid stays hidden
        self.write_db(d)
        dry = self.cgp("setup", "https://github.com/orgs/acme/projects/1", "--dry-run")
        self.assertEqual(dry["viewsToUpdate"], ["Tasks"])
        self.assertEqual(self.tasks()["fieldIds"][-1], "F_Priority")  # dry run wrote nothing
        r = self.setup_board()
        self.assertEqual(r["viewsUpdated"], ["Tasks"])
        ids = self.tasks()["fieldIds"]
        self.assertEqual(ids[:2], ["F_Title", "F_Priority"])
        self.assertNotIn("F_Repository", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(next(v for v in self.read_db()["views"] if v["name"] == "Board")["fieldIds"], board_before)
        self.assertEqual(self.setup_board()["viewsUpdated"], [])
        dry = self.cgp("setup", "https://github.com/orgs/acme/projects/1", "--dry-run")
        self.assertEqual(dry["viewsToUpdate"], [])


class TestDoctorAndGc(Base):
    def test_doctor_reports_and_flags_problems(self):
        self.setup_board()
        p = self.cgp("doctor", ok=False)
        self.assertIn("✅ python", p.stdout)
        self.assertIn("❌ Test Board 1: clone of acme/app", p.stdout)  # setup ran without --repo-path
        self.assertEqual(p.returncode, 1)

    def test_gc_removes_only_dead_session_files(self):
        self.setup_board()
        home = self.env["CGP_HOME"]
        old = time.time() - 30 * 86400
        for name in ("state-dead.json", "stop-dead", "state.json"):
            path = os.path.join(home, name)
            with open(path, "w") as f:
                f.write("{}")
            os.utime(path, (old, old))
        fresh = os.path.join(home, "state-fresh.json")
        with open(fresh, "w") as f:
            f.write("{}")
        dry = self.cgp("gc", "--dry-run")
        self.assertEqual(len(dry["removedFiles"]), 3)
        self.assertTrue(os.path.exists(os.path.join(home, "state-dead.json")))
        self.cgp("gc")
        self.assertFalse(os.path.exists(os.path.join(home, "state-dead.json")))
        self.assertFalse(os.path.exists(os.path.join(home, "state.json")))
        self.assertTrue(os.path.exists(fresh))


class TestAskRecordsTime(Base):
    def test_question_time_is_kept_so_replies_are_fetched_since_it(self):
        self.setup_board()
        self.cgp("ask", "i1", input="1. which?")
        boards = os.path.join(self.env["CGP_HOME"], "boards")
        with open(os.path.join(boards, "P1.data.json")) as f:
            self.assertIn("i1", json.load(f)["asked"])


def boards_dir(env):
    return os.path.join(env["CGP_HOME"], "boards")


class TestMergeGates(PRBase):
    def data(self):
        with open(os.path.join(boards_dir(self.env), "P1.data.json")) as f:
            return json.load(f)

    def save_data(self, **kw):
        merged = {**self.data(), **kw}
        with open(os.path.join(boards_dir(self.env), "P1.data.json"), "w") as f:
            json.dump(merged, f)

    def option(self, column):
        with open(os.path.join(boards_dir(self.env), "P1.json")) as f:
            return json.load(f)["fields"]["status"]["options"][column]

    def wait(self, *extra):
        return self.cgp("merge-wait", "i1", "--interval", "0", *extra)

    def test_every_merge_is_pinned_to_the_head_that_was_read(self):
        self.prs(self.view(headRefOid="aaa111"))
        self.cgp("merge", "i1")
        self.assertEqual(self.calls("merge")[0][-2:], ["--match-head-commit", "aaa111"])
        self.db_set(auto_merge_unavailable=True, calls=[])
        self.cgp("merge", "i1")
        self.assertTrue(all(c[-2:] == ["--match-head-commit", "aaa111"] for c in self.calls("merge")))
        self.assertEqual(len(self.calls("merge")), 2)
        self.db_set(calls=[], prs={"acme/app#1": self.view(mergeStateStatus="CLEAN")})
        self.db_set(prs={"acme/app#1": [self.view(mergeStateStatus="CLEAN"), self.view(state="MERGED")]})
        self.wait()
        self.assertEqual(self.calls("merge")[0][-2:], ["--match-head-commit", "aaa111"])

    def test_a_head_that_moves_between_the_read_and_the_merge_is_not_merged(self):
        self.db_set(head_is="zzz999", auto_merge_unavailable=True)  # gh refuses the pin
        self.assertFalse(self.cgp("merge", "i1")["requested"])

    def test_a_pr_from_a_fork_is_refused_even_on_a_cgp_branch(self):
        self.prs(self.view(isCrossRepository=True))
        self.assertIn("fork", self.cgp("merge", "i1", ok=False).stderr)
        v = self.view(); del v["isCrossRepository"]  # an answer that omits it fails closed
        self.prs(v)
        self.assertIn("fork", self.cgp("merge", "i1", ok=False).stderr)

    def test_a_pr_against_another_base_is_refused_when_the_default_branch_is_known(self):
        clone = self.make_clone()
        subprocess.run(["git", "-C", clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
        self.cgp("repo-path", "acme/app", clone)
        self.prs(self.view(baseRefName="release"))
        self.assertIn("default branch main", self.cgp("merge", "i1", ok=False).stderr)
        self.prs(self.view())
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})

    def test_set_pr_refuses_forks_and_replacing_the_pr_under_review(self):
        self.force("i1", "implement")
        self.prs(self.view(isCrossRepository=True))
        self.assertIn("fork", self.cgp("set", "i1", "pr", "https://github.com/acme/app/pull/1", ok=False).stderr)
        self.db_set(prs={"acme/app#1": self.view(), "acme/app#2": self.view()})
        self.cgp("move", "i1", "pr_review")
        p = self.cgp("set", "i1", "pr", "https://github.com/acme/app/pull/2", ok=False)
        self.assertIn("cannot be replaced", p.stderr)
        self.cgp("set", "i1", "pr", "https://github.com/acme/app/pull/1")  # the same PR is no change
        self.force("i1", "implement")
        self.cgp("set", "i1", "pr", "https://github.com/acme/app/pull/2")

    def test_a_missing_review_record_is_refused_not_adopted(self):
        self.save_data(reviewed={})
        for cmd in (("merge", "i1"), ("merge-wait", "i1", "--interval", "0")):
            p = self.cgp(*cmd, ok=False)
            self.assertEqual(p.returncode, 7, p.stderr)
            self.assertIn("pr_review", p.stderr)
        self.assertEqual(self.data()["reviewed"], {})
        self.force("i1", "implement")  # the way out: a pass through PR Review records the head
        self.cgp("move", "i1", "pr_review")
        self.force("i1", "pr_approved")
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})

    def test_a_delegated_story_merges_with_the_pin_and_no_record(self):
        self.save_data(reviewed={})
        d = self.read_db(); d["items"][0]["values"]["Auto Approve"] = {"optionId": "o_PR"}; self.write_db(d)
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})
        self.assertIn("--match-head-commit", self.calls("merge")[0])

    def test_a_failed_head_read_aborts_the_move_to_pr_review(self):
        self.force("i1", "implement")
        self.db_set(prs={"acme/app#7": self.view()})  # the story's PR cannot be read
        p = self.cgp("move", "i1", "pr_review", ok=False)
        self.assertNotEqual(p.returncode, 0)
        item = next(i for i in self.read_db()["items"] if i["id"] == "i1")
        self.assertEqual(item["values"]["Status"]["optionId"], self.option("implement"))

    def test_merge_wait_returns_revoked_when_the_user_takes_the_approval_back(self):
        self.db_set(status_seq={"i1": [self.option("pr_approved"), self.option("pr_review")]}, calls=[])
        r = self.wait()
        self.assertEqual(r["state"], "revoked")
        self.assertIn("--disable-auto", self.calls("merge")[-1])

    def test_a_delegated_story_is_not_revoked(self):
        d = self.read_db(); d["items"][0]["values"]["Auto Approve"] = {"optionId": "o_PR"}; self.write_db(d)
        self.db_set(status_seq={"i1": [self.option("pr_approved"), self.option("pr_review")]})
        self.prs(self.view(), self.view(state="MERGED"))
        self.assertEqual(self.wait()["state"], "merged")

    def test_merged_is_reported_even_when_the_card_was_moved(self):
        self.db_set(status_seq={"i1": [self.option("pr_approved"), self.option("done")]})
        self.prs(self.view(state="MERGED"))
        self.assertEqual(self.wait()["state"], "merged")

    def test_cancel_checks_that_auto_merge_is_really_off(self):
        self.assertEqual(self.cgp("merge", "i1", "--cancel"), {"cancelled": True})  # also a repo that has no auto-merge
        self.db_set(auto_merge_unavailable=True, calls=[])
        self.assertEqual(self.cgp("merge", "i1", "--cancel"), {"cancelled": True})
        self.db_set(auto_merge_stuck=True, calls=[])
        self.assertEqual(self.cgp("merge", "i1", "--cancel"), {"cancelled": False})
        self.assertEqual(len(self.calls("merge")), 3)  # retried
        self.cgp("move", "i1", "pr_review")  # a stuck auto-merge never blocks the move itself

    def test_wait_never_merges_directly_over_unknown_or_unfinished_checks(self):
        self.prs(self.view(mergeStateStatus="CLEAN"))
        self.db_set(checks_seq=[{"rc": 1, "stderr": "boom"}], calls=[])
        self.assertEqual(self.wait("--timeout", "0")["state"], "pending")
        self.db_set(checks=[{"name": "t", "bucket": "pending", "link": ""}], checks_seq=None)
        d = self.read_db(); d.pop("checks_seq", None); self.write_db(d)
        self.assertEqual(self.wait("--timeout", "0")["state"], "pending")
        self.assertEqual(self.calls("merge"), [])

    def test_auto_merge_is_armed_again_when_the_head_moves(self):
        self.save_data(cleanRebase={"i1": ["bbb222"]})
        self.prs(self.view(), self.view(headRefOid="bbb222"), self.view(state="MERGED"))
        self.assertEqual(self.wait()["state"], "merged")
        armed = [c for c in self.calls("merge") if "--auto" in c]
        self.assertEqual(armed[-1][-2:], ["--match-head-commit", "bbb222"])

    def test_branch_update_uses_the_expected_head_and_whitelists_the_new_head(self):
        self.prs(self.view(mergeStateStatus="BEHIND"), self.view(headRefOid="ccc333"), self.view(state="MERGED"))
        self.assertEqual(self.wait()["state"], "merged")
        call = self.read_db()["update_branch_calls"][0]
        self.assertEqual(call[-2:], ["-f", "expected_head_sha=aaa111"])
        self.assertIn("repos/acme/app/pulls/1/update-branch", call)
        self.assertEqual(self.data()["cleanRebase"]["i1"], ["ccc333"])


class TestSetValidation(Base):
    def test_plan_is_skip_or_an_https_link_on_claude_ai_or_github(self):
        self.setup_board()
        for bad in ("http://claude.ai/artifact/x", "https://evil.example/plan", "https://claude.ai/a b", "javascript:alert(1)",
                    "https://claude.ai@evil.example/x", "https://claude.ai.evil.example/x", "https://claude.ai:444/x", "see below"):
            self.assertNotEqual(self.cgp("set", "i1", "plan", bad, ok=False).returncode, 0, bad)
        for good in ("https://claude.ai/artifact/x", "https://github.com/acme/app/issues/1", "  Skip  ", "skip"):
            self.cgp("set", "i1", "plan", good)
        self.assertEqual(self.cgp("set", "i1", "plan", "  Skip  ")["plan"], "Skip")
        self.cgp("set", "i1", "plan", "")  # clearing is fine


class TestPreviewUrls(test_cgp.PRBase):
    def test_a_preview_url_that_could_break_out_of_markdown_is_refused(self):
        self.cgp("config", "previewProvider", "deployments")
        self.prs(self.view(headRefOid="aaa111"))
        for url in ("https://x.example/a)[b](https://evil.example", "https://user@evil.example/"):
            self.db_set(deployments=[{"id": 5, "statuses": [{"state": "success", "environment_url": url}]}])
            self.assertNotEqual(self.cgp("preview", "i1", ok=False).returncode, 0, url)
        self.db_set(deployments=[{"id": 5, "statuses": [{"state": "success", "environment_url": "https://pr1.example.dev/x?y=1"}]}])
        self.assertEqual(self.cgp("preview", "i1")["preview"], "https://pr1.example.dev/x?y=1")


class TestRepoPath(test_cgp.Base):
    def test_origin_must_be_the_repo_on_github_and_an_existing_clone_is_never_repointed(self):
        self.setup_board()
        for origin in ("https://github.com/evil/app.git", "https://github.com.evil.example/acme/app", "https://evil.example/acme/app.git",
                       "git@evil.example:acme/app.git", "https://github.com/acme/app-fork.git"):
            p = self.cgp("repo-path", "acme/app", self.make_clone(origin=origin, name="c" + str(abs(hash(origin)))), ok=False)
            self.assertIn("not a clone of acme/app", p.stderr, origin)
        self.assertIn("not a clone", self.cgp("repo-path", "acme/app", os.path.join(self.tmp, "missing"), ok=False).stderr)
        good = self.make_clone(origin="git@github.com:Acme/App.git", name="good")  # ssh form, any case
        self.cgp("repo-path", "acme/app", good)
        self.cgp("repo-path", "acme/app", good)  # the same path again is no change
        other = self.make_clone(name="other")
        self.assertIn("already has a local clone", self.cgp("repo-path", "acme/app", other, ok=False).stderr)
        self.assertEqual(self.cgp("repo-path", "acme/app")["acme/app"], good)

    def test_a_local_origin_is_allowed(self):
        self.setup_board()
        local = self.make_clone(origin=os.path.join(self.tmp, "origin.git"))
        self.assertEqual(self.cgp("repo-path", "acme/app", local), {"acme/app": local})


class TestAuthorTrust(test_cgp.Base):
    def test_prepare_reports_whether_the_issue_author_may_steer_the_loop(self):
        self.setup_board()
        self.assertTrue(self.cgp("prepare", "i1")["authorTrusted"])
        d = self.read_db(); d["issue_authors"] = {"acme/app#1": {"login": "eve", "author_association": "NONE"}}; self.write_db(d)
        self.assertFalse(self.cgp("prepare", "i1")["authorTrusted"])
        d["issue_authors"] = {"acme/app#1": {"login": "bob", "author_association": "COLLABORATOR"}}
        d["perms"] = {"bob": "write"}
        self.write_db(d)
        self.assertTrue(self.cgp("prepare", "i1")["authorTrusted"])

    def test_import_skips_untrusted_authors_and_reports_them(self):
        self.setup_board()
        d = self.read_db()
        d["repo_issues"] = {"acme/app": [
            {"number": 7, "node_id": "I_7", "title": "mine", "repo": "acme/app", "html_url": "x", "labels": [{"name": "cgp"}]},
            {"number": 9, "node_id": "I_9", "title": "theirs", "repo": "acme/app", "html_url": "x", "labels": [{"name": "cgp"}],
             "user": {"login": "eve"}, "author_association": "NONE"}]}
        self.write_db(d)
        r = self.cgp("import", "cgp")
        self.assertEqual([a["number"] for a in r["added"]], [7])
        self.assertEqual(r["skippedUntrusted"], [{"repo": "acme/app", "number": 9, "author": "eve"}])


class TestAskRounds(test_cgp.Base):
    def test_only_the_agents_own_question_markers_count_as_rounds(self):
        self.setup_board()
        d = self.read_db()
        d["comments"]["acme/app#1"] = [{"body": "<!-- cgp:question -->", "created_at": f"2026-01-01T00:00:0{n}Z",
                                        "user": {"login": "eve", "type": "User"}, "author_association": "NONE"} for n in range(5)]
        self.write_db(d)
        self.assertEqual(self.cgp("ask", "i1", input="1. which?")["round"], 1)


class TestTerminalText(test_cgp.Base):
    def test_status_and_doctor_strip_control_and_bidi_characters(self):
        self.setup_board()
        d = self.read_db()
        d["items"][1]["content"]["title"] = "evil\x1b[31m red \u202egnp.exe \x9b"
        self.write_db(d)
        self.force("i2", "plan_review")
        text = self.cgp("status", ok=False).stdout
        self.assertIn("evil[31m red gnp.exe", text)
        for ch in ("\x1b", "\u202e", "\x9b"):
            self.assertNotIn(ch, text)


class TestWorktreeCleanup(test_cgp.TestSync):
    def finish(self):
        d = self.read_db(); d["items"][0]["content"]["state"] = "CLOSED"; self.write_db(d)

    def test_a_worktree_with_uncommitted_changes_survives_cleanup_and_gc(self):
        with open(os.path.join(self.wt, "wip.txt"), "w") as f:
            f.write("unsaved\n")
        self.finish()
        self.cgp("list", "--brief")
        self.assertTrue(os.path.isdir(self.wt))
        self.assertNotIn(self.wt, self.cgp("gc", "--dry-run")["removedWorktrees"])
        self.assertTrue(os.path.isdir(self.wt))

    def test_a_clean_worktree_of_a_finished_story_is_removed(self):
        self.finish()
        self.cgp("list", "--brief")
        self.assertFalse(os.path.isdir(self.wt))


class TestSkillText(test_cgp.unittest.TestCase):
    def test_no_command_in_the_instructions_interpolates_a_title(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for top in ("skills", "docs"):
            for dirpath, _, files in os.walk(os.path.join(root, top)):
                for name in files:
                    if name.endswith(".md"):
                        with open(os.path.join(dirpath, name)) as f:
                            bad += [f"{name}: {m}" for m in re.findall(r'(?:--title|\badd) "<title>"', f.read())]
        self.assertEqual(bad, [])


class TestDocs(test_cgp.unittest.TestCase):
    def test_cli_reference_is_up_to_date(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_loader("gen", importlib.machinery.SourceFileLoader("gen", os.path.join(root, "scripts", "gen-cli-docs")))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        with open(os.path.join(root, "docs", "cli.md")) as f:
            self.assertEqual(f.read(), gen.render(), "run scripts/gen-cli-docs")
