"""`cgp defer`: the PR description's Deferred section becomes stories on hold."""
import os
import sys

import test_cgp

sys.path.insert(0, os.path.join(test_cgp.ROOT, "scripts"))
from cgp_lib.deferred import parse_deferred, item_key  # noqa: E402


class TestParse(test_cgp.unittest.TestCase):
    def test_no_section_or_an_empty_one_has_nothing(self):
        self.assertEqual(parse_deferred(""), ([], []))
        self.assertEqual(parse_deferred("## Summary\n- a :: b"), ([], []))
        self.assertEqual(parse_deferred("## Deferred\n\n## Next\n- a"), ([], []))

    def test_bullets_with_and_without_a_reason(self):
        items, skipped = parse_deferred("x\n## deferred\n- Fix the thing :: it is slow\n* Other  \n## Tests\n- not me")
        self.assertEqual([(i["title"], i["reason"]) for i in items], [("Fix the thing", "it is slow"), ("Other", "")])
        self.assertEqual(skipped, [])

    def test_bad_titles_are_skipped(self):
        items, skipped = parse_deferred("## Deferred\n- " + "x" * 201 + "\n- bad\x07title\n- ok\n- fine :: " + "y" * 501)
        self.assertEqual([i["title"] for i in items], ["ok"])
        self.assertEqual(len(skipped), 3)

    def test_only_a_spaced_separator_splits(self):
        items, _ = parse_deferred("## Deferred\n- Rename Foo::bar\n- Other :: why Foo::bar")
        self.assertEqual([(i["title"], i["reason"]) for i in items], [("Rename Foo::bar", ""), ("Other", "why Foo::bar")])

    def test_ten_at_most_and_duplicates_collapse(self):
        items, skipped = parse_deferred("## Deferred\n" + "\n".join(f"- item {n}" for n in range(11)) + "\n- ITEM   1")
        self.assertEqual(len(items), 10)
        self.assertEqual(skipped, ["item 10"])


class DeferBase(test_cgp.PRBase):
    BODY = "Closes #1\n\n## Deferred\n- Tidy the parser :: @mallory said so\n- Add a test\n"

    def setUp(self):
        super().setUp()
        self.merged()

    def merged(self, state="MERGED"):
        self.prs(self.view(state=state, body=self.BODY, author={"login": "kelsin"}, url=self.PR))

    def issues(self):
        return self.read_db().get("repo_issues", {}).get("acme/app", [])

    def comments(self):
        return [c["body"] for c in self.read_db()["comments"].get("acme/app#1", [])]

    def refused(self, why):
        p = self.cgp("defer", "i1", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(why, p.stderr)
        self.assertEqual(self.issues(), [])


class TestDefer(DeferBase):
    def test_files_stories_on_hold_pointing_back(self):
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["Tidy the parser", "Add a test"])
        first = self.issues()[0]
        self.assertTrue(first["body"].startswith(f"Deferred from acme/app#1 (PR {self.PR})"))
        self.assertIn("> @​mallory said so", first["body"])
        d = self.read_db()
        for f in r["filed"]:
            item = next(i for i in d["items"] if i["content"].get("number") == f["number"])
            self.assertEqual(item["values"]["Priority"], {"optionId": "o_Hold"})
        data = self.data()
        self.assertEqual([k["number"] for k in data["deferredFiled"]["i1"]], [f["number"] for f in r["filed"]])
        self.assertNotIn("parents", data)
        self.assertNotIn("children", data)
        self.assertEqual(len([c for c in self.comments() if "Deferred work filed" in c]), 1)
        self.assertEqual(self.cgp("list")["held"].count("Tidy the parser"), 1)

    def test_the_schedulers_deferred_list_does_not_break_it(self):
        self.save_data(deferred=["i2"])  # the scheduler keeps a list of overlap-deferred item ids under this key
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["Tidy the parser", "Add a test"])
        self.assertEqual(self.data()["deferred"], ["i2"])

    def test_a_rerun_files_only_what_is_missing(self):
        self.cgp("defer", "i1")
        r = self.cgp("defer", "i1")
        self.assertEqual((r["filed"], len(r["already"])), ([], 2))
        self.assertEqual(len(self.issues()), 2)
        self.save_data(deferredFiled={"i1": self.data()["deferredFiled"]["i1"][:1]})
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["Add a test"])
        self.assertEqual(len(self.issues()), 3)

    def test_mentions_in_a_title_are_neutralised(self):
        self.prs(self.view(state="MERGED", body="## Deferred\n- Ask @mallory about Foo::bar", author={"login": "kelsin"}, url=self.PR))
        self.cgp("defer", "i1")
        self.assertEqual(self.issues()[0]["title"], "Ask @\u200bmallory about Foo::bar")
        self.assertIn("Ask @\u200bmallory about Foo::bar", [c for c in self.comments() if "Deferred work filed" in c][0])
        self.assertNotIn("@mallory", self.comments()[-1])

    def test_the_cap_is_per_story_across_runs(self):
        def body(names):
            return "## Deferred\n" + "\n".join(f"- {n}" for n in names)
        self.prs(self.view(state="MERGED", body=body(f"a{n}" for n in range(6)), author={"login": "kelsin"}, url=self.PR))
        self.cgp("defer", "i1")
        self.prs(self.view(state="MERGED", body=body(f"b{n}" for n in range(6)), author={"login": "kelsin"}, url=self.PR))
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["b0", "b1", "b2", "b3"])
        self.assertEqual(r["skipped"], ["b4", "b5"])
        self.assertEqual(len(self.issues()), 10)
        self.assertEqual(len(self.data()["deferredFiled"]["i1"]), 10)

    def test_a_failed_hold_leaves_nothing_dispatchable_and_no_duplicate(self):
        d = self.read_db()
        d["failures"] = [{"match": "o_Hold", "times": 3, "stderr": "gh: HTTP 502"}]
        self.write_db(d)
        self.cgp("defer", "i1", ok=False)
        d = self.read_db()
        self.assertEqual(len(self.issues()), 1)
        item = next(i for i in d["items"] if i["content"].get("number") == self.issues()[0]["number"])
        self.assertNotIn("Status", item["values"])  # never Todo without Hold
        self.assertEqual(len(self.data()["deferredFiled"]["i1"]), 1)
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["Add a test"])
        self.assertEqual(len(self.issues()), 2)

    def test_refusals_create_nothing(self):
        self.merged("OPEN")
        self.refused("not merged")
        self.prs(self.view(state="MERGED", body=self.BODY, author={"login": "mallory"}))
        self.refused("not written by this account")
        self.merged()
        self.force("i1", "plan")
        self.refused("works only in")
        self.force("i1", "pr_approved")
        board = self.load(self.board_path())
        del board["fields"]["priority"]["options"]["Hold"]
        with open(self.board_path(), "w") as f:
            test_cgp.json.dump(board, f)
        self.refused("Hold")

    def test_no_pr_link_is_refused(self):
        d = self.read_db()
        next(i for i in d["items"] if i["id"] == "i1")["values"].pop("PR")
        self.write_db(d)
        self.refused("no valid PR link")

    def test_the_prompts_run_it(self):
        text = test_cgp.read_text("skills", "run", "columns", "pr_approved.md")
        for want in ("CGP defer", "--list", "--review -", "no bullets"):
            self.assertIn(want, text)
        self.assertIn("13. **Deferred work.**", test_cgp.read_text("skills", "run", "columns", "shared.md"))


class TestDeferReview(DeferBase):
    A, B = item_key("Tidy the parser"), item_key("Add a test")

    def review(self, keep=(), drop=(), ok=True, **extra):
        return self.cgp("defer", "i1", "--review", "-", ok=ok, input=test_cgp.json.dumps({"keep": list(keep), "drop": list(drop), **extra}))

    def priority_of(self, number):
        d = self.read_db()
        return next(i for i in d["items"] if i["content"].get("number") == number)["values"]["Priority"]

    def reviews(self):
        return [c for c in self.comments() if "reviewed against" in c]

    def nothing(self, p):
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.issues(), [])
        self.assertNotIn("deferredDecision", self.data())

    def test_list_prints_keys_and_creates_nothing(self):
        r = self.cgp("defer", "i1", "--list")
        self.assertEqual([(i["key"], i["title"]) for i in r["items"]], [(self.A, "Tidy the parser"), (self.B, "Add a test")])
        self.assertEqual((r["skipped"], r["filed"], r["dropped"]), ([], [], []))
        self.assertEqual((self.issues(), self.comments()), ([], []))
        self.prs(self.view(state="MERGED", body="## Deferred\n" + "\n".join(f"- n{n}" for n in range(12)), author={"login": "kelsin"}, url=self.PR))
        r = self.cgp("defer", "i1", "--list")
        self.assertEqual((len(r["items"]), r["skipped"]), (10, ["n10", "n11"]))

    def test_keep_one_drop_one(self):
        r = self.review([{"key": self.A, "priority": "Medium"}], [{"key": self.B, "reason": "@bob unrelated"}])
        self.assertEqual([f["title"] for f in r["filed"]], ["Tidy the parser"])
        self.assertEqual([d["title"] for d in r["dropped"]], ["Add a test"])
        self.assertEqual(len(self.issues()), 1)
        self.assertEqual(self.priority_of(r["filed"][0]["number"]), {"optionId": "o_Medium"})
        self.assertIn("approves the plan", self.issues()[0]["body"])
        self.assertEqual(len(self.reviews()), 1)
        self.assertIn("(Medium)", self.reviews()[0])
        self.assertIn("dropped: Add a test (@\u200bbob unrelated)", self.reviews()[0])
        self.assertEqual(len(self.data()["deferredReviewed"]), 1)

    def test_drop_only_posts_once_and_a_rerun_posts_nothing(self):
        d = [{"key": self.A, "reason": "no"}, {"key": self.B}]
        r = self.review(drop=d)
        self.assertEqual((r["filed"], len(r["dropped"])), ([], 2))
        self.assertEqual(len(self.reviews()), 1)
        r = self.review(drop=d)
        self.assertEqual((r["filed"], r["dropped"]), ([], []))
        self.assertEqual((len(self.reviews()), self.issues()), (1, []))

    def test_an_omitted_bullet_is_unreviewed_until_a_later_run_decides(self):
        r = self.review([{"key": self.A, "priority": "Low"}])
        self.assertEqual(r["unreviewed"], ["Add a test"])
        self.assertEqual(len(self.issues()), 1)
        r = self.review(drop=[{"key": self.B, "reason": "x"}])
        self.assertEqual(([f["title"] for f in r["filed"]], r["unreviewed"], len(r["already"])), ([], [], 1))
        self.assertEqual(len(r["dropped"]), 1)

    def test_bad_decisions_change_nothing(self):
        for p in (self.cgp("defer", "i1", "--review", "-", ok=False, input="{"),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input="[]"),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input='{"keep": {}}'),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input='{"keep": ["x"]}'),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input='{"drop": [{"key": 3}]}'),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input='{"drop": [{"key": "a", "reason": 3}]}'),
                  self.cgp("defer", "i1", "--review", "-", ok=False, input='{"keep": [{"key": "a", "priority": 1}]}'),
                  self.review([{"key": self.A}], [{"key": self.A, "reason": "x"}], ok=False),
                  self.review([{"key": self.A, "priority": "Hold"}], ok=False),
                  self.review([{"key": self.A, "priority": "High"}], ok=False),
                  self.review([{"key": self.A, "priority": "Urgent"}], ok=False),
                  self.review(drop=[{"key": self.A, "reason": "x" * 501}], ok=False),
                  self.review(drop=[{"key": self.A, "reason": "a\x07b"}], ok=False)):
            self.nothing(p)

    def test_keys_ignore_case_and_spaces_and_unknown_ones_are_listed(self):
        r = self.review([{"key": "  " + self.A.upper() + " "}, {"key": "f" * 40, "priority": "Low"}])
        self.assertEqual(len(r["filed"]), 1)
        self.assertEqual(r["unknown"], ["f" * 40])
        self.assertEqual(len(self.issues()), 1)

    def test_a_missing_priority_files_at_low(self):
        r = self.review([{"key": self.A}])
        self.assertEqual(self.priority_of(r["filed"][0]["number"]), {"optionId": "o_Low"})

    def test_a_failed_create_replays_the_saved_decision(self):
        d = self.read_db()
        d["failures"] = [{"match": "o_Medium", "times": 3, "stderr": "gh: HTTP 502"}]
        self.write_db(d)
        self.review([{"key": self.A, "priority": "Medium"}, {"key": self.B, "priority": "Low"}], ok=False)
        self.assertEqual(len(self.issues()), 1)
        r = self.review([{"key": self.A, "priority": "Low"}, {"key": self.B, "priority": "Medium"}])
        self.assertEqual((len(self.issues()), [f["title"] for f in r["filed"]], len(r["already"])), (2, ["Add a test"], 1))
        self.assertEqual(self.priority_of(r["filed"][0]["number"]), {"optionId": "o_Low"})  # the saved decision, not the new one
        self.assertEqual(self.data()["deferredFiled"]["i1"][0]["priority"], "Medium")
        r = self.review(drop=[{"key": self.A, "reason": "x"}, {"key": self.B, "reason": "x"}])
        self.assertEqual((r["filed"], r["dropped"], len(self.issues())), ([], [], 2))

    def test_no_flag_files_everything_on_hold_without_reading_stdin(self):
        r = self.cgp("defer", "i1", input="not json")
        self.assertEqual(len(r["filed"]), 2)
        self.assertNotIn("dropped", r)
        self.assertEqual(self.priority_of(r["filed"][0]["number"]), {"optionId": "o_Hold"})
        self.assertNotIn("deferredReviewed", self.data())

    def test_a_reviewed_drop_is_not_refiled_without_the_flag(self):
        self.review(drop=[{"key": self.A, "reason": "x"}])
        r = self.cgp("defer", "i1")
        self.assertEqual([f["title"] for f in r["filed"]], ["Add a test"])

    def test_the_review_does_not_need_a_hold_option(self):
        board = self.load(self.board_path())
        del board["fields"]["priority"]["options"]["Hold"]
        with open(self.board_path(), "w") as f:
            test_cgp.json.dump(board, f)
        self.assertEqual(len(self.review([{"key": self.A, "priority": "Low"}])["filed"]), 1)

    def test_mentions_in_the_reason_are_neutralised_in_the_body(self):
        self.review([{"key": self.A}])
        self.assertIn("> @\u200bmallory said so", self.issues()[0]["body"])
        self.assertNotIn("@mallory", self.comments()[-1])


if __name__ == "__main__":
    test_cgp.unittest.main()
