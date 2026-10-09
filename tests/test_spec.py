"""Speculative implementation: a low-rated Plan Review story is drafted as local commits (spec.py)."""
import os
import subprocess
import unittest

import test_cgp

Base = test_cgp.Base
PLAN = "https://claude.ai/artifact/abc"


def node(n, state="OPEN", repo="acme/app"):
    return {"number": n, "state": state, "repository": {"nameWithOwner": repo}, "author": {"login": "kelsin"}}


class TestSpecSettings(Base):
    def test_it_is_off_by_default_with_one_worker(self):
        self.setup_board()
        s = self.cgp("config")
        self.assertEqual((s["speculative"], s["speculativeMax"]), (0, 1))

    def test_an_agent_cannot_change_either_setting(self):
        self.setup_board()
        for key, value in (("speculative", "on"), ("speculativeMax", "3")):
            p = self.cgp("config", key, value, ok=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("can only be changed by you", p.stderr)

    def test_a_person_sets_them_and_bad_values_are_rejected(self):
        self.setup_board()
        self.assertEqual(self.tty("config", "speculative", "on").returncode, 0)
        self.assertEqual(self.tty("config", "speculativeMax", "2").returncode, 0)
        self.assertEqual((self.cgp("config")["speculative"], self.cgp("config")["speculativeMax"]), (1, 2))
        self.assertIn("on|off", self.tty("config", "speculative", "maybe").stderr)
        self.assertIn("non-negative integer", self.tty("config", "speculativeMax", "many").stderr)


class SpecBase(test_cgp.SyncBase):
    """i1 (worktree from SyncBase) is rated low in Plan and sits in Plan Review with its plan linked and f.txt declared."""

    def setUp(self):
        super().setUp()
        self.setting(speculative=1, speculativeMax=1)
        self.cgp("set", "i1", "plan", PLAN)
        self.force("i1", "plan")
        self.cgp("touches", "i1", "f.txt")
        self.cgp("rate", "i1", "low")  # stored with column "plan": the real order is rate, then move to Plan Review
        self.cgp("move", "i1", "plan_review")

    def offered(self):
        return [i["item"] for i in self.cgp("list")["speculative"]]

    def spec(self, item="i1"):
        return self.data().get("spec", {}).get(item)

    def head(self):
        return subprocess.run(["git", "-C", self.wt, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, name="f.txt", text="draft\n"):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write(text)
        self.git(self.wt, "add", "."); self.git(self.wt, "commit", "-qm", f"draft {name}")

    def column(self, item):
        board = self.load(self.board_path())
        opt = next(i for i in self.read_db()["items"] if i["id"] == item)["values"]["Status"]["optionId"]
        return next(k for k, v in board["fields"]["status"]["options"].items() if v == opt)

    def item(self, item="i1"):
        return next(i for i in self.read_db()["items"] if i["id"] == item)

    def edit_db(self, fn):
        d = self.read_db()
        fn(d)
        self.write_db(d)

    def comment(self, body="looks off", login="kelsin", assoc="OWNER"):
        self.edit_db(lambda d: d.setdefault("comments", {}).setdefault("acme/app#1", []).append(
            {"body": body, "created_at": "2026-02-01T00:00:00Z", "user": {"login": login, "type": "User"}, "author_association": assoc}))

    def start(self, n=0):
        res = self.cgp("spec", "start", "i1", "--artifact-comments", str(n))
        self.assertTrue(res["started"], res)
        return res

    def ready(self):
        self.start()
        self.base = self.head()
        self.commit()
        return self.cgp("spec", "finish", "i1")

    def approve(self):
        self.force("i1", "plan_approved")

    def git_out(self, *args, cwd=None):
        return subprocess.run(["git", "-C", cwd or self.wt, *args], capture_output=True, text=True, check=True).stdout.strip()

    def edit_spec(self, **kw):
        spec = self.data()["spec"]
        spec["i1"].update(kw)
        self.set_data(spec=spec)


class TestSpecSnapshot(SpecBase):
    def test_a_low_rated_story_is_offered_never_batched_and_its_card_stays(self):
        snap = self.cgp("list")
        self.assertEqual([i["item"] for i in snap["speculative"]], ["i1"])
        self.assertNotIn("i1", [i["item"] for i in snap["batch"]])
        self.assertEqual(snap["status"], "work")
        self.assertEqual(self.column("i1"), "plan_review")

    def test_it_reads_only_local_state(self):
        self.db_set(failures=[{"match": "/comments", "times": 99}])  # a comments call would fail the whole snapshot
        self.assertEqual(self.offered(), ["i1"])

    def test_off_by_default(self):
        self.setting(speculative=0)
        self.assertEqual(self.offered(), [])

    def test_only_a_low_rating_given_in_the_plan_column_qualifies(self):
        for rating in ({"rating": "medium", "column": "plan"}, {"rating": "high", "column": "plan"}, {"rating": "low", "column": "plan_review"}):
            self.set_data(ratings={"i1": rating})
            self.assertEqual(self.offered(), [], rating)
        self.set_data(ratings={})
        self.assertEqual(self.offered(), [])

    def test_hold_no_touches_no_plan_link_and_the_wrong_column_do_not_qualify(self):
        touches = self.data()["touches"]
        self.set_data(touches={})
        self.assertEqual(self.offered(), [])
        self.set_data(touches=touches)
        self.edit_db(lambda d: self.item_values(d).update(Priority={"optionId": "o_Hold"}))
        self.assertEqual(self.offered(), [])
        self.edit_db(lambda d: self.item_values(d).pop("Priority"))
        self.edit_db(lambda d: self.item_values(d).update(Plan={"text": "Skip"}))
        self.assertEqual(self.offered(), [])
        self.edit_db(lambda d: self.item_values(d).update(Plan={"text": PLAN}))
        self.force("i1", "plan")
        self.assertEqual(self.offered(), [])

    def item_values(self, d):
        return next(i for i in d["items"] if i["id"] == "i1")["values"]

    def test_speculative_max_and_free_slots_limit_it_and_real_work_comes_first(self):
        self.force("i2", "plan")
        self.cgp("set", "i2", "plan", PLAN + "2")
        self.cgp("touches", "i2", "g.txt")
        self.cgp("rate", "i2", "low")
        self.cgp("move", "i2", "plan_review")
        self.assertEqual(self.offered(), ["i1"])  # speculativeMax 1
        self.setting(speculativeMax=2)
        self.assertEqual(sorted(self.offered()), ["i1", "i2"])
        self.setting(concurrency=1)
        snap = self.cgp("list")
        self.assertTrue(snap["batch"])  # a Todo story takes the only slot
        self.assertEqual(snap["speculative"], [])

    def test_a_draft_that_would_clash_with_real_work_is_not_started_and_a_running_one_is_discarded(self):
        self.force("i2", "plan_approved")
        self.cgp("touches", "i2", "f.txt")
        snap = self.cgp("list")
        self.assertIn("i2", [i["item"] for i in snap["batch"]])
        self.assertEqual(snap["speculative"], [])
        self.cgp("touches", "i2", "g.txt")
        self.start()
        base = self.head()
        self.commit()
        self.cgp("touches", "i2", "f.txt")
        self.assertIn("i2", [i["item"] for i in self.cgp("list")["batch"]])
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", base))

    def test_a_worker_row_of_a_draft_keeps_the_story_row_uses_a_slot_and_records_no_strikes(self):
        self.cgp("worker", "start", "i1")
        self.cgp("worker", "start", "i1:spec")
        self.assertEqual(sorted(w["item"] for w in self.state()["workers"]), ["i1", "i1:spec"])
        self.assertEqual(self.state()["workers"][1]["phase"], "implementing")
        self.assertEqual(self.cgp("list")["inFlight"], ["i1", "i1:spec"])
        self.cgp("worker", "stop", "i1")
        self.setting(concurrency=1)
        snap = self.cgp("list")
        self.assertTrue(snap["batch"])  # real work is not held back by the draft's row...
        self.assertEqual(snap["speculative"], [])  # ...but no further draft starts while the only slot is in use
        self.cgp("worker", "stop", "i1:spec", "--outcome", "fail")
        self.assertNotIn("daemonStrikes", self.data())
        self.assertEqual(self.spec()["state"], "failed")
        self.assertEqual(self.offered(), [])  # not retried for the same plan

    def test_a_changed_plan_may_be_drafted_again(self):
        self.cgp("worker", "start", "i1:spec")
        self.cgp("worker", "stop", "i1:spec", "--outcome", "fail")
        self.assertEqual(self.offered(), [])
        self.cgp("set", "i1", "plan", PLAN + "-v2")
        self.assertEqual(self.offered(), ["i1"])

    def test_an_approved_story_dispatches_at_once_while_its_draft_runs_and_the_draft_is_dropped(self):
        self.start()
        self.cgp("worker", "start", "i1:spec")
        self.approve()
        snap = self.cgp("list")
        self.assertIn("i1", [i["item"] for i in snap["batch"]])
        self.assertNotIn("i1:spec", snap["inFlight"])
        self.assertEqual(self.spec()["state"], "discarded")

    def test_leaving_plan_review_or_going_on_hold_discards_the_draft(self):
        self.ready()
        self.edit_db(lambda d: self.item_values(d).update(Priority={"optionId": "o_Hold"}))
        self.cgp("list")
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", self.base))
        self.edit_db(lambda d: self.item_values(d).pop("Priority"))
        self.force("i1", "plan")
        self.cgp("list")
        self.assertEqual((self.spec()["state"], self.spec()["forgotten"], self.head()), ("discarded", True, self.base))

    def test_a_rating_above_low_discards_it(self):
        self.ready()
        self.set_data(ratings={"i1": {"rating": "medium", "column": "plan"}})
        self.cgp("list")
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", self.base))

    def test_a_blocked_story_is_not_offered(self):
        self.force("i2", "plan_approved")
        self.set_data(blocks={"i1": ["i2"]})
        self.assertEqual(self.offered(), [])
        self.set_data(blocks={})
        self.assertEqual(self.offered(), ["i1"])

    def test_the_snapshot_names_the_draft_rows_it_dropped_so_their_agents_can_be_stopped(self):
        self.start()
        self.cgp("worker", "start", "i1:spec")
        self.assertEqual(self.cgp("list")["stopDrafts"], [])
        self.approve()
        self.assertEqual(self.cgp("list")["stopDrafts"], ["i1:spec"])
        self.assertEqual(self.cgp("list")["stopDrafts"], [])

    def test_a_running_draft_without_a_worker_row_is_discarded(self):
        self.start()
        base = self.head()
        self.commit()
        self.cgp("list")
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", base))

    def test_entries_of_stories_that_are_gone_are_pruned(self):
        self.ready()
        self.force("i1", "done")
        self.cgp("list")
        self.assertIsNone(self.spec())


class TestSpecCommands(SpecBase):
    def test_a_draft_lands_on_the_story_branch_and_nothing_leaves_the_machine(self):
        self.ready()
        self.assertEqual(self.spec()["state"], "ready")
        self.assertEqual(self.git_out("branch", "--show-current"), "cgp/1")
        self.assertNotEqual(self.head(), self.base)
        self.assertEqual(self.git_out("ls-remote", "origin", "refs/heads/cgp/1", cwd=self.clone), "")
        self.assertEqual(self.calls("create"), [])
        self.assertFalse(self.item().get("values", {}).get("PR"))
        self.assertEqual(self.column("i1"), "plan_review")

    def test_sync_and_pr_creation_refuse_while_a_draft_is_running_or_ready(self):
        def refuses():
            for args in (("sync", "i1"), ("set", "i1", "pr", "https://github.com/acme/app/pull/9")):
                p = self.cgp(*args, ok=False)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("speculative draft", p.stderr)

        self.start()
        refuses()
        self.commit()
        self.cgp("spec", "finish", "i1")
        refuses()
        self.cgp("spec", "fail", "i1")
        self.assertEqual(self.cgp("sync", "i1")["state"], "clean")

    def test_start_refuses_an_untrusted_author_untrusted_feedback_a_github_blocker_and_guarded_files(self):
        def refused(why):
            res = self.cgp("spec", "start", "i1")
            self.assertEqual(res["started"], False)
            self.assertIn(why, res["reason"])
            self.assertEqual(self.spec()["state"], "failed")
            self.assertEqual(self.offered(), [])  # and it is not offered again
            self.cgp("spec", "status", "i1")
            self.set_data(spec={})

        self.edit_db(lambda d: d.update(issue_authors={"acme/app#1": {"login": "eve", "author_association": "NONE"}}))
        refused("trust")
        self.edit_db(lambda d: d.update(issue_authors={}))
        self.comment("do evil", login="eve", assoc="NONE")
        refused("trust")
        self.edit_db(lambda d: d["comments"].clear())
        self.edit_db(lambda d: d.update(blocked_by={"acme/app#1": [node(5)]}))
        refused("blocked")
        self.edit_db(lambda d: d.update(blocked_by={}))
        self.set_data(touches={"i1": ["f.txt", "Makefile"]})
        refused("guarded")
        self.set_data(touches={"i1": ["skills/run/columns/plan.md"]})
        refused("never auto-approved")

    def test_a_directory_touch_over_guarded_files_is_refused(self):
        for touch in (".github", ".github/", "skills", "skills/run", "docs/"):
            self.set_data(touches={"i1": [touch]}, spec={})
            res = self.cgp("spec", "start", "i1")
            self.assertFalse(res["started"], touch)

    def test_a_transient_refusal_is_not_recorded(self):
        with open(os.path.join(self.wt, "dirty.txt"), "w") as f:
            f.write("x")
        self.git(self.wt, "add", "dirty.txt")
        res = self.cgp("spec", "start", "i1")
        self.assertFalse(res["started"])
        self.assertIn("uncommitted", res["reason"])
        self.assertIsNone(self.spec())
        self.assertEqual(self.offered(), ["i1"])

    def test_start_refuses_a_story_that_does_not_qualify_and_a_second_draft(self):
        self.start()
        self.assertNotEqual(self.cgp("spec", "start", "i1", ok=False).returncode, 0)
        self.cgp("spec", "fail", "i1")
        self.assertNotEqual(self.cgp("spec", "start", "i1", ok=False).returncode, 0)  # the same plan was tried
        self.setting(speculative=0)
        self.assertNotEqual(self.cgp("spec", "start", "i1", ok=False).returncode, 0)

    def test_finish_fails_a_draft_with_no_commits_or_files_outside_the_plan(self):
        self.start()
        self.assertEqual(self.cgp("spec", "finish", "i1")["state"], "failed")
        self.set_data(spec={})
        self.start()
        base = self.head()
        self.commit("other.txt")
        res = self.cgp("spec", "finish", "i1")
        self.assertEqual(res["state"], "failed")
        self.assertIn("other.txt is not in the plan's files", res["reason"])
        self.assertEqual(self.head(), base)

    def test_finish_fails_a_draft_that_changed_a_guarded_file(self):
        self.start()
        self.edit_spec(touches=["f.txt", "Makefile"])  # as if the plan had listed it
        self.commit("Makefile", "all:\n")
        res = self.cgp("spec", "finish", "i1")
        self.assertIn("guarded", res["reason"])

    def test_the_fingerprint_ignores_the_agents_own_comments(self):
        self.start()
        fp = self.spec()["fingerprint"]
        self.cgp("comment", "i1", input="status: all good")
        self.commit()
        self.cgp("spec", "finish", "i1")
        self.approve()
        self.assertTrue(self.cgp("prepare", "i1")["speculative"]["adopted"])
        self.assertEqual(fp, self.spec()["fingerprint"])


class TestSpecResolve(SpecBase):
    def test_an_unchanged_approved_plan_adopts_the_commits_and_clears_the_state(self):
        self.ready()
        draft = self.head()
        self.approve()
        res = self.cgp("prepare", "i1")
        self.assertEqual((res["speculative"]["adopted"], res["speculative"]["head"]), (True, draft))
        self.assertEqual(self.head(), draft)
        self.assertEqual(self.spec()["state"], "adopted")
        self.assertEqual(self.cgp("spec", "resolve", "i1", "--artifact-comments", "0"), {"adopted": True, "confirmed": True})
        self.assertIsNone(self.spec())
        self.assertNotIn("speculative", self.cgp("prepare", "i1"))
        self.assertEqual(self.cgp("sync", "i1")["state"], "clean")

    def test_prepare_keeps_reporting_the_adoption_until_it_is_confirmed(self):
        self.ready()
        self.approve()
        first = self.cgp("prepare", "i1")["speculative"]
        second = self.cgp("prepare", "i1")["speculative"]
        self.assertEqual((first["adopted"], first["confirmed"]), (True, False))
        self.assertEqual(first, second)
        self.assertEqual(self.cgp("spec", "resolve", "i1", "--artifact-comments", "0")["confirmed"], True)
        self.assertNotIn("speculative", self.cgp("prepare", "i1"))

    def test_a_new_artifact_comment_discards_it_before_or_after_prepare(self):
        self.start(n=1)
        self.commit()
        self.cgp("spec", "finish", "i1")
        self.approve()
        res = self.cgp("spec", "resolve", "i1", "--artifact-comments", "2")
        self.assertTrue(res["discarded"])
        self.assertIn("artifact", res["reason"])
        self.assertEqual(self.spec()["state"], "discarded")
        self.set_data(spec={})
        self.force("i1", "plan_review")
        self.ready()
        self.approve()
        self.assertTrue(self.cgp("prepare", "i1")["speculative"]["adopted"])
        self.assertTrue(self.cgp("spec", "resolve", "i1", "--artifact-comments", "1")["discarded"])
        self.assertEqual(self.head(), self.base)

    def discarded(self, why):
        res = self.cgp("prepare", "i1")["speculative"]
        self.assertTrue(res["discarded"], res)
        self.assertIn(why, res["reason"])
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", self.base))

    def test_a_changed_plan_changed_files_or_new_feedback_discard_it(self):
        self.ready()
        self.edit_db(lambda d: next(i for i in d["items"] if i["id"] == "i1")["values"].update(Plan={"text": PLAN + "-v2"}))
        self.approve()
        self.discarded("changed")
        for change in (lambda: self.set_data(touches={"i1": ["f.txt", "g.txt"]}), lambda: self.comment()):
            self.set_data(spec={})
            self.edit_db(lambda d: d.update(comments={}))
            self.edit_db(lambda d: next(i for i in d["items"] if i["id"] == "i1")["values"].update(Plan={"text": PLAN}))
            self.set_data(touches={"i1": ["f.txt"]})
            self.force("i1", "plan_review")
            self.ready()
            change()
            self.approve()
            self.discarded("changed")

    def test_a_draft_still_running_is_never_adopted_half_done(self):
        self.start()
        base = self.head()
        self.commit()
        self.approve()
        res = self.cgp("prepare", "i1")["speculative"]
        self.assertTrue(res["discarded"])
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", base))

    def test_a_draft_whose_diff_left_the_declared_files_is_discarded(self):
        self.ready()
        self.edit_spec(touches=["elsewhere.txt"])
        self.approve()
        self.discarded("not in the plan's files")

    def test_a_branch_that_moved_after_the_draft_ended_is_discarded(self):
        self.ready()
        self.commit("f.txt", "more\n")
        self.approve()
        self.discarded("not at the commit")

    def test_a_base_that_is_not_on_the_default_branch_is_discarded(self):
        self.ready()
        self.edit_spec(base=self.head())  # an unpushed commit: not reachable from the default branch
        self.approve()
        res = self.cgp("prepare", "i1")["speculative"]
        self.assertTrue(res["discarded"])
        self.assertIn("default branch", res["reason"])


class TestSpecDiscardBackstop(SpecBase):
    def test_late_draft_commits_after_a_discard_are_reset_when_the_real_worker_prepares(self):
        self.start()
        self.cgp("worker", "start", "i1:spec")
        base = self.head()
        self.approve()
        self.cgp("list")  # discards the running draft
        self.assertEqual((self.spec()["state"], self.head()), ("discarded", base))
        self.commit()  # the draft agent was not stopped and commits late
        self.commit("g.txt")
        res = self.cgp("prepare", "i1")
        self.assertEqual((self.head(), res["sync"]["state"]), (base, "clean"))
        self.assertIsNone(self.spec()["base"])
        self.commit("h.txt")  # the real worker's own work is left alone by later prepares
        mine = self.head()
        self.cgp("prepare", "i1")
        self.assertEqual(self.head(), mine)

    def test_a_branch_the_real_worker_already_pushed_is_not_reset(self):
        self.start()
        base = self.head()
        self.approve()
        self.cgp("list")
        self.commit()
        self.git(self.wt, "push", "-q", "origin", "HEAD:refs/heads/cgp/1")
        self.git(self.wt, "fetch", "-q", "origin")
        mine = self.head()
        self.cgp("prepare", "i1")
        self.assertNotEqual(mine, base)
        self.assertEqual(self.head(), mine)

    def test_untracked_files_of_a_discarded_draft_are_removed(self):
        self.start()
        base = self.head()
        self.commit()
        with open(os.path.join(self.wt, "junk.txt"), "w") as f:
            f.write("x")
        os.makedirs(os.path.join(self.wt, "newdir"))
        with open(os.path.join(self.wt, "newdir", "y.txt"), "w") as f:
            f.write("y")
        self.approve()
        self.cgp("list")
        self.assertEqual(self.head(), base)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "junk.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "newdir")))

    def test_a_failed_reset_leaves_the_draft_live_so_sync_keeps_refusing(self):
        self.ready()
        self.edit_spec(base="0" * 40)  # git cannot reset to it
        self.approve()
        self.edit_spec(base="0" * 40, head=self.head(), state="running")
        res = self.cgp("prepare", "i1")["speculative"]
        self.assertFalse(res["discarded"])
        self.assertEqual(self.spec()["state"], "running")
        p = self.cgp("sync", "i1", ok=False)
        self.assertIn("speculative draft", p.stderr)


class TestSpecDocs(unittest.TestCase):
    def test_the_command_is_listed_in_the_generated_cli_docs(self):
        self.assertIn("`spec`", test_cgp.read_text("docs", "cli.md"))
        self.assertIn("speculative", test_cgp.read_text("docs", "settings.md"))


if __name__ == "__main__":
    unittest.main()
