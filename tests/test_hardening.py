"""Tests for the safety gates and tools added after the first review: worker start, review-bound merging, guard,
stalled workers, status, doctor, gc, setup --dry-run."""
import importlib.machinery
import importlib.util
import json
import os
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
        self.cgp("worker", "start", "i2")  # the column is looked up too
        w = self.state()["workers"][0]
        self.assertEqual((w["title"], w["column"]), ("two", "todo" if w["column"] == "todo" else w["column"]))
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
        self.assertEqual(self.calls("merge"), [])
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

    def test_story_without_a_record_is_taken_as_reviewed(self):
        self.assertEqual(self.cgp("merge", "i1"), {"requested": True})  # approved before the check existed

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
        with open(os.path.join(boards, "P1.data.json")) as f:
            return json.load(f)

    def commit(self, name, text="x\n"):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write(text)
        self.git(self.wt, "add", "."); self.git(self.wt, "commit", "-qm", name)

    def test_a_clean_rebase_is_allowed_a_conflicting_one_taints(self):
        self.commit("g.txt")
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

    def test_guard_covers_more_than_github_files_and_is_configurable(self):
        self.commit(".npmrc", "registry=https://evil.example\n")
        p = self.cgp("guard", "i1", ok=False)
        self.assertEqual(p.returncode, 4)
        self.assertEqual(json.loads(p.stdout)["violations"], [".npmrc"])
        self.cgp("touches", "i1", ".npmrc")
        self.assertTrue(self.cgp("guard", "i1")["ok"])
        self.cgp("touches", "i1", "")
        self.cgp("config", "guardFiles", "")
        self.assertTrue(self.cgp("guard", "i1")["ok"])


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


class TestDocs(test_cgp.unittest.TestCase):
    def test_cli_reference_is_up_to_date(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_loader("gen", importlib.machinery.SourceFileLoader("gen", os.path.join(root, "scripts", "gen-cli-docs")))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        with open(os.path.join(root, "docs", "cli.md")) as f:
            self.assertEqual(f.read(), gen.render(), "run scripts/gen-cli-docs")
