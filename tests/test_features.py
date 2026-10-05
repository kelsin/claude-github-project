"""Priority, intake, notifications, history, repo config, preview providers, models, draft PRs."""
import json
import os
import stat

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
    def script(self):
        out = os.path.join(self.tmp, "events.txt")
        path = os.path.join(self.tmp, "notify.sh")
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\necho "$CGP_EVENT|$CGP_TITLE|$CGP_BOARD" >> "{out}"\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path, out

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

    def test_the_command_can_be_unset(self):
        self.setup_board()
        self.cgp("config", "notifyCommand", "echo hi")
        self.assertEqual(self.cgp("config", "notifyCommand", "")["notifyCommand"], "")


class TestHistory(Base):
    def test_moves_and_worker_runs_are_logged_and_replayed(self):
        self.setup_board()
        self.cgp("worker", "start", "i1")
        self.cgp("move", "i1", "plan")
        self.cgp("worker", "stop", "i1")
        p = self.cgp("replay", "i1", ok=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        for word in ("worker-start", "move", "was=todo to=plan", "worker-stop"):
            self.assertIn(word, p.stdout)
        kinds = [e["kind"] for e in self.cgp("replay", "i1", "--json")["events"]]
        self.assertEqual(kinds, ["worker-start", "move", "worker-stop"])

    def test_report_summarises_finished_stories(self):
        m = test_cgp.load_cgp()
        story = {"title": "S", "events": [
            {"at": "2026-01-01T00:00:00Z", "kind": "worker-start"}, {"at": "2026-01-01T01:00:00Z", "kind": "move", "was": "plan", "to": "plan_review"},
            {"at": "2026-01-01T02:00:00Z", "kind": "ask"}, {"at": "2026-01-01T03:00:00Z", "kind": "worker-start"},
            {"at": "2026-01-01T06:00:00Z", "kind": "move", "was": "pr_approved", "to": "done"}]}
        s = m.story_summary(story)
        self.assertEqual((s["runs"], s["reviews"], s["questions"], s["hours"]), (2, 1, 1, 6.0))
        self.assertIsNone(m.story_summary({"title": "x", "events": [{"at": "2026-01-01T00:00:00Z", "kind": "ask"}]})["hours"])
        story["events"].append({"at": "2026-01-02T00:00:00Z", "kind": "move", "was": "done", "to": "todo"})
        self.assertIsNone(m.story_summary(story)["doneAt"])  # reopened: not done any more
        story["events"] = story["events"][3:]
        story["startedAt"] = "2025-12-31T18:00:00Z"
        self.assertEqual(m.story_summary({**story, "events": story["events"][:2]})["hours"], 12.0)  # start survives trimming
        self.setup_board()
        self.assertEqual(self.cgp("report", "--json")["done"], 0)

    def test_the_history_file_is_not_mistaken_for_a_board(self):
        self.setup_board()
        self.cgp("worker", "start", "i1")
        self.assertEqual(self.cgp("repos"), {"acme/app": None})  # still works with <board>.history.json present


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
