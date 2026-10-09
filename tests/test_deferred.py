"""`cgp defer`: the PR description's Deferred section becomes stories on hold."""
import os
import sys

import test_cgp

sys.path.insert(0, os.path.join(test_cgp.ROOT, "scripts"))
from cgp_lib.deferred import parse_deferred  # noqa: E402


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


class TestDefer(test_cgp.PRBase):
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
        self.assertEqual([k["number"] for k in data["deferred"]["i1"]], [f["number"] for f in r["filed"]])
        self.assertNotIn("parents", data)
        self.assertNotIn("children", data)
        self.assertEqual(len([c for c in self.comments() if "Deferred work filed" in c]), 1)
        self.assertEqual(self.cgp("list")["held"].count("Tidy the parser"), 1)

    def test_a_rerun_files_only_what_is_missing(self):
        self.cgp("defer", "i1")
        r = self.cgp("defer", "i1")
        self.assertEqual((r["filed"], len(r["already"])), ([], 2))
        self.assertEqual(len(self.issues()), 2)
        self.save_data(deferred={"i1": self.data()["deferred"]["i1"][:1]})
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
        self.assertEqual(len(self.data()["deferred"]["i1"]), 10)

    def test_a_failed_hold_leaves_nothing_dispatchable_and_no_duplicate(self):
        d = self.read_db()
        d["failures"] = [{"match": "o_Hold", "times": 3, "stderr": "gh: HTTP 502"}]
        self.write_db(d)
        self.cgp("defer", "i1", ok=False)
        d = self.read_db()
        self.assertEqual(len(self.issues()), 1)
        item = next(i for i in d["items"] if i["content"].get("number") == self.issues()[0]["number"])
        self.assertNotIn("Status", item["values"])  # never Todo without Hold
        self.assertEqual(len(self.data()["deferred"]["i1"]), 1)
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
        self.assertIn("CGP defer", test_cgp.read_text("skills", "run", "columns", "pr_approved.md"))
        self.assertIn("13. **Deferred work.**", test_cgp.read_text("skills", "run", "columns", "shared.md"))


if __name__ == "__main__":
    test_cgp.unittest.main()
