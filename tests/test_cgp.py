import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CGP = os.path.join(ROOT, "scripts", "cgp")


def issue(n, title):
    return {"__typename": "Issue", "number": n, "title": title, "state": "OPEN",
            "url": f"https://github.com/acme/app/issues/{n}", "repository": {"nameWithOwner": "acme/app"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "db.json")
        status = {"id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT", "options": [
            {"id": "a", "name": "Todo", "color": "GREEN"}, {"id": "b", "name": "In Progress", "color": "GREEN"},
            {"id": "c", "name": "Done", "color": "GREEN"}]}
        self.write_db({
            "scopes": "'gist', 'project', 'repo'", "fields": [status], "comments": {},
            "items": [
                {"id": "i1", "content": issue(1, "one"), "values": {"Status": {"optionId": "a"}}},
                {"id": "i2", "content": issue(2, "two"), "values": {"Status": {"optionId": "b"}}},
                {"id": "i3", "content": issue(3, "three"), "values": {"Status": {"optionId": "c"}}},
                {"id": "i4", "content": {"__typename": "DraftIssue", "title": "draft", "body": ""}, "values": {}},
            ]})
        bin_ = os.path.join(self.tmp, "bin")
        os.makedirs(bin_)
        os.symlink(os.path.join(ROOT, "tests", "fakegh"), os.path.join(bin_, "gh"))
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE_GH_DB": self.db,
                    "CGP_HOME": os.path.join(self.tmp, "home")}

    def write_db(self, d):
        with open(self.db, "w") as f:
            json.dump(d, f)

    def read_db(self):
        with open(self.db) as f:
            return json.load(f)

    def cgp(self, *args, input=None, ok=True):
        p = subprocess.run([sys.executable, CGP, *args], capture_output=True, text=True, env=self.env, input=input)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout) if p.stdout.strip() else None
        return p

    def setup_board(self):
        return self.cgp("setup", "https://github.com/orgs/acme/projects/1", "--repo", "acme/app")

    def state(self):
        with open(os.path.join(self.env["CGP_HOME"], "state.json")) as f:
            return json.load(f)


class TestSetup(Base):
    def test_missing_scope(self):
        d = self.read_db(); d["scopes"] = "'gist', 'repo'"; self.write_db(d)
        p = self.cgp("setup", "https://github.com/orgs/acme/projects/1", ok=False)
        self.assertEqual(p.returncode, 3)
        self.assertIn("gh auth refresh -s project", p.stderr)

    def test_bad_url(self):
        self.assertNotEqual(self.cgp("setup", "https://example.com/x", ok=False).returncode, 0)

    def test_columns_fields_and_remap(self):
        res = self.setup_board()
        names = [o["name"] for o in self.read_db()["fields"][0]["options"]]
        self.assertEqual(names[0], "🆕 Todo")
        self.assertEqual(names[1], "🧠 Plan")
        self.assertEqual(len(names), 10)
        colors = {o["name"]: o["color"] for o in self.read_db()["fields"][0]["options"]}
        self.assertEqual(colors["🔍 Plan Review"], "ORANGE")
        self.assertEqual(colors["🎉 Done"], "GREEN")
        fnames = {f["name"] for f in self.read_db()["fields"]}
        self.assertTrue({"Waiting On", "Plan", "PR", "Repo"} <= fnames)
        # Todo/Done kept, "In Progress" and the unset draft fall to Todo
        self.assertEqual(res["itemsRemapped"], {"todo": 3, "done": 1})
        self.assertEqual(res["repos"], {"acme/app": None})

    def test_idempotent_keeps_ids(self):
        self.setup_board()
        before = self.read_db()["fields"][0]["options"]
        self.setup_board()
        self.assertEqual(self.read_db()["fields"][0]["options"], before)


class TestListAndMove(Base):
    def test_list_batch_order_and_counts(self):
        self.setup_board()
        self.cgp("move", "i1", "pr_approved")
        self.cgp("move", "i2", "plan_approval")
        snap = self.cgp("list")
        self.assertEqual(snap["status"], "work")
        self.assertEqual([i["item"] for i in snap["batch"]], ["i1", "i4"])  # pr_approved before todo
        self.assertEqual(snap["counts"]["plan_approval"], 1)
        self.assertEqual(snap["batch"][0]["repo"], "acme/app")
        self.assertEqual(self.state()["counts"]["plan_approval"], 1)

    def test_concurrency_cap(self):
        self.setup_board()
        self.cgp("config", "concurrency", "1")
        self.assertEqual(len(self.cgp("list")["batch"]), 1)

    def test_idle_and_done(self):
        self.setup_board()
        for i in ("i1", "i2", "i4"):
            self.cgp("move", i, "plan_approval")
        self.assertEqual(self.cgp("list")["status"], "idle")
        for i in ("i1", "i2", "i4"):
            self.cgp("move", i, "done")
        self.assertEqual(self.cgp("list")["status"], "done")

    def test_wait_returns_on_timeout_when_idle(self):
        self.setup_board()
        for i in ("i1", "i2", "i4"):
            self.cgp("move", i, "pr_approval")
        r = self.cgp("wait", "--timeout", "0")
        self.assertEqual(r["status"], "idle")

    def test_set_text_field(self):
        self.setup_board()
        self.cgp("set", "i1", "plan", "https://claude.ai/artifact/x")
        it = next(i for i in self.cgp("list")["items"] if i["item"] == "i1")
        self.assertEqual(it["plan"], "https://claude.ai/artifact/x")
        self.cgp("set", "i1", "repo", "acme/other")
        it = next(i for i in self.cgp("list")["items"] if i["item"] == "i1")
        self.assertEqual(it["repo"], "acme/other")


class TestQuestions(Base):
    def test_ask_blocks_until_human_reply(self):
        self.setup_board()
        self.cgp("move", "i1", "plan")
        r = self.cgp("ask", "i1", input="1. Which db? (default: sqlite)")
        self.assertEqual(r["round"], 1)
        snap = self.cgp("list")
        self.assertNotIn("i1", [i["item"] for i in snap["batch"]])
        self.assertEqual([i["item"] for i in snap["waitingOnYou"]], ["i1"])
        self.assertEqual(self.state()["waiting"][0]["title"], "one")
        # an agent comment does not count as a reply
        self.cgp("comment", "i1", input="working on it")
        self.assertEqual(len(self.cgp("list")["waitingOnYou"]), 1)
        # a human comment does
        d = self.read_db()
        d["comments"]["acme/app#1"].append({"body": "sqlite is fine", "created_at": "2026-01-01T00:01:00Z",
                                            "user": {"login": "kelsin", "type": "User"}})
        self.write_db(d)
        snap = self.cgp("list")
        self.assertEqual(snap["waitingOnYou"], [])
        it = next(i for i in snap["batch"] if i["item"] == "i1")
        self.assertTrue(it["answered"])

    def test_bot_comment_is_not_a_reply(self):
        self.setup_board()
        self.cgp("ask", "i1", input="q")
        d = self.read_db()
        d["comments"]["acme/app#1"].append({"body": "ci says hi", "created_at": "2026-01-01T00:05:00Z",
                                            "user": {"login": "dependabot", "type": "Bot"}})
        self.write_db(d)
        self.assertEqual(len(self.cgp("list")["waitingOnYou"]), 1)

    def test_rescope_hint_after_three_rounds(self):
        self.setup_board()
        for _ in range(3):
            self.cgp("ask", "i1", input="q")
        self.cgp("ask", "i1", input="q")
        last = self.read_db()["comments"]["acme/app#1"][-1]["body"]
        self.assertIn("rescoping", last)

    def test_feedback_since_last_agent_comment(self):
        self.setup_board()
        d = self.read_db()
        d["comments"]["acme/app#1"] = [
            {"body": "old human note", "created_at": "2026-01-01T00:00:01Z", "user": {"login": "k", "type": "User"}}]
        self.write_db(d)
        self.cgp("comment", "i1", input="status: plan posted")
        self.assertEqual(self.cgp("feedback", "i1"), [])
        d = self.read_db()
        d["comments"]["acme/app#1"].append({"body": "please add tests", "created_at": "2026-01-01T00:09:00Z",
                                            "user": {"login": "k", "type": "User"}})
        self.write_db(d)
        fb = self.cgp("feedback", "i1")
        self.assertEqual([f["body"] for f in fb], ["please add tests"])


class TestWorkers(Base):
    def test_worker_lifecycle_and_move_updates_column(self):
        self.setup_board()
        self.cgp("worker", "start", "i1", "todo", "one")
        self.cgp("move", "i1", "plan")
        self.assertEqual(self.state()["workers"][0]["column"], "plan")
        self.cgp("worker", "stop", "i1")
        self.assertEqual(self.state()["workers"], [])
        self.cgp("worker", "start", "i1", "todo", "one")
        self.cgp("worker", "clear")
        self.assertEqual(self.state()["workers"], [])


if __name__ == "__main__":
    unittest.main()
