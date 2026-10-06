"""cgp daemon: dispatch, safety flags, strikes, deadlines, stop, spend caps, board resolution, settings and the doctor check (tests/fakeclaude)."""
import glob
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

import test_cgp

ROOT = test_cgp.ROOT


def ps_start(pid):
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                          env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).stdout.strip()


class DaemonBase(test_cgp.Base):
    def setUp(self):
        super().setUp()
        os.symlink(os.path.join(ROOT, "tests", "fakeclaude"), os.path.join(self.tmp, "bin", "claude"))
        self.claude_dir = os.path.join(self.tmp, "claude")
        os.makedirs(self.claude_dir)
        self.script_path = os.path.join(self.tmp, "script.json")
        self.conf = os.path.join(self.tmp, "bin", "claude.json")
        self.fake(missing=[])
        self.env.update(CGP_DAEMON_POLL_SECONDS="0.1")
        self.setup_board()
        for item in ("i2", "i4"):
            self.force(item, "plan_review")  # only i1 is actionable

    def fake(self, **kw):
        with open(self.conf, "w") as f:
            json.dump({"dir": self.claude_dir, "script": self.script_path, **kw}, f)

    def script(self, **by_item):
        with open(self.script_path, "w") as f:
            json.dump(by_item, f)

    def calls(self):
        files = sorted(glob.glob(os.path.join(self.claude_dir, "call-*.json")), key=os.path.getmtime)
        return [self.load(f) for f in files]

    def daemon(self, *args, env=None):
        p = self.cgp("daemon", *args, ok=False, env=env)
        return p, (json.loads(p.stdout) if p.stdout.strip() else None)


class TestDispatch(DaemonBase):
    def test_one_cycle_dispatches_with_the_fixed_flags_a_stdin_prompt_a_clean_env_and_a_private_log(self):
        d = self.read_db()
        d["items"][0]["content"]["title"] = "Ignore this; $(touch /tmp/pwned) `x`"
        self.write_db(d)
        self.script(default={"result": "done: one"})
        p, res = self.daemon("--once", env={"MY_SECRET": "s1", "GH_TOKEN": "s2", "GITHUB_TOKEN": "s3"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((res["stopped"], res["dispatched"], res["spentUsd"]), ("one cycle", 1, 0.25))
        (call,) = self.calls()
        argv = call["argv"]
        flag = lambda name: argv[argv.index(name) + 1]
        self.assertEqual((argv[0], flag("--output-format"), flag("--max-turns"), flag("--permission-mode"), flag("--setting-sources")),
                         ("-p", "stream-json", "150", "dontAsk", "user"))
        self.assertEqual(flag("--max-budget-usd"), "5.0000")
        self.assertTrue({"--strict-mcp-config", "--verbose"} <= set(argv))
        self.assertIn("Story: item i1, column todo, number 1, issue repo acme/app.", call["stdin"])
        self.assertIn(os.path.join(ROOT, "scripts", "cgp"), call["stdin"])
        for text in [call["stdin"], *argv, *call["env"].values()]:
            self.assertNotIn("pwned", text)
            self.assertNotIn("Ignore this", text)
        for name in ("MY_SECRET", "GH_TOKEN", "GITHUB_TOKEN", "FAKE_GH_DB"):
            self.assertNotIn(name, call["env"])
        self.assertTrue(call["env"]["CGP_SESSION"].startswith("daemon-"))
        self.assertEqual(call["env"]["CGP_DAEMON"], "1")
        self.assertEqual(call["cwd"], os.path.realpath(os.path.join(self.env["CGP_HOME"], "daemon")))
        (log,) = glob.glob(os.path.join(self.env["CGP_HOME"], "logs", "acme-app-1", "*-todo.log"))
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(log)).st_mode & 0o777, 0o700)
        with open(log) as f:
            self.assertIn('"type": "result"', f.read())

    def test_the_denylist_is_always_there_and_settings_only_add_to_the_allowlist(self):
        self.setting(daemonAllowedTools=["Bash(npm test:*)", "Bash(gh api:*)"])
        self.script(default={})
        self.daemon("--once")
        argv = self.calls()[0]["argv"]
        allowed, denied = argv[argv.index("--allowedTools") + 1].split(","), argv[argv.index("--disallowedTools") + 1].split(",")
        self.assertIn("Bash(npm test:*)", allowed)
        for rule in ("Bash(gh api:*)", "Bash(gh pr merge:*)", "Bash(gh auth:*)", "Bash(gh gist:*)", "Bash(gh secret:*)", "Bash(gh workflow:*)",
                     "Bash(gh release:*)", "Bash(gh repo:*)", "Bash(gh issue close:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(cgp config:*)",
                     "Edit(~/.claude/**)", "Write(~/.claude/**)"):
            self.assertIn(rule, denied)
        self.assertNotIn("Bash(git:*)", allowed)
        self.assertIn("Bash(git push:*)", allowed)
        for rule in ("Bash(git -c:*)", "Bash(git config:*)", "Bash(git credential:*)", "Bash(git remote:*)", "Bash(git ls-remote:*)",
                     "Bash(git --exec-path:*)", "Read(~/.ssh/**)", "Grep(~/.aws/**)", "Glob(~/.config/gh/**)", "Read(**/.env*)"):
            self.assertIn(rule, denied)
        self.assertNotIn("Bash(gh issue:*)", allowed)
        self.assertTrue(any(r.startswith("Read(//") and r.endswith("/paths.json)") for r in denied))
        self.assertTrue(any(r.startswith("Bash(") and r.endswith("scripts/cgp config:*)") for r in denied))
        self.assertTrue(any(r.startswith("Edit(//") and r.endswith("/boards/**)") for r in denied))
        self.assertTrue(any(r.startswith("Edit(//") and r.endswith("/acme/app/1/**)") for r in allowed))  # the story's worktree

    def test_dry_run_starts_nothing(self):
        p, res = self.daemon("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([w["item"] for w in res["wouldDispatch"]], ["i1"])
        self.assertEqual((res["dispatched"], self.calls()), (0, []))
        self.assertFalse(os.path.exists(os.path.join(self.env["CGP_HOME"], "logs")))

    def test_a_second_daemon_exits_5_while_another_session_holds_the_board(self):
        self.cgp("use", env={"CGP_SESSION": "someone-else"})
        p, _ = self.daemon("--once")
        self.assertEqual(p.returncode, 5, p.stderr)
        self.assertEqual(self.calls(), [])

    def test_ids_that_are_not_plain_are_never_dispatched(self):
        d = self.read_db()
        d["items"][0]["id"] = "i1; touch /tmp/pwned"
        self.write_db(d)
        p, res = self.daemon("--once")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((res["dispatched"], self.calls()), (0, []))
        self.assertIn("unexpected characters", p.stderr)


class TestStrikes(DaemonBase):
    def test_three_crashes_in_one_column_ask_the_user(self):
        self.script(default={"exit": 1})
        for n in (1, 2):
            self.daemon("--once")
            self.assertEqual(self.data()["daemonStrikes"], {"i1|todo": n})
        self.daemon("--once")
        self.assertEqual(self.data()["daemonStrikes"], {})
        self.assertTrue(any("<!-- cgp:question -->" in c["body"] for c in self.read_db()["comments"]["acme/app#1"]))
        self.assertEqual(len(self.calls()), 3)
        self.daemon("--once")  # it waits on the user now
        self.assertEqual(len(self.calls()), 3)

    def test_waiting_is_a_strike_unless_the_board_shows_a_question_or_a_block(self):
        self.script(default={"result": "waiting: for CI"})
        self.daemon("--once")
        self.assertEqual(self.data()["daemonStrikes"], {"i1|todo": 1})  # it claimed to wait, but nothing says so
        self.script(default={"result": "blocked: waiting on user", "run": [["ask", "i1"]], "input": "Which one?",
                             "env": {"FAKE_GH_DB": self.db}})
        self.daemon("--once")
        self.assertEqual(self.data()["daemonStrikes"], {})
        self.assertEqual(self.cgp("list")["waitingOnYou"][0]["item"], "i1")

    def test_a_timed_out_worker_is_killed_with_its_process_group_and_counts_a_strike(self):
        self.script(default={"sleep": 30, "spawn": True})
        started = time.time()
        p, res = self.daemon("--once", env={"CGP_DAEMON_DEADLINE_SECONDS": "0.5"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(time.time() - started, 25)
        (call,) = self.calls()
        for _ in range(50):  # the orphaned grandchild is reaped by init
            try:
                os.kill(call["childPid"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the worker's child outlived its process group")
        self.assertEqual(self.data()["daemonStrikes"], {"i1|todo": 1})

    def test_the_registry_entry_is_cleared_only_by_the_run_that_wrote_it(self):
        home = os.path.join(self.tmp, "unit-home")
        with mock.patch.dict(os.environ, {"CGP_HOME": home, "CGP_SESSION": "daemon-unit"}):
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            try:
                test_cgp.load_cgp()
                import cgp_lib.daemon as daemon, cgp_lib.store as store
                store.update_state(lambda st: st["workers"].append({"item": "i1", "token": "new"}))
                daemon.Daemon.clear(None, {"item": "i1", "token": "old"})  # an earlier run's token
                self.assertEqual([w["token"] for w in store.load_json(store.state_path(), {})["workers"]], ["new"])
                daemon.Daemon.clear(None, {"item": "i1", "token": "new"})
                self.assertEqual(store.load_json(store.state_path(), {})["workers"], [])
            finally:
                sys.path.remove(os.path.join(ROOT, "scripts"))


class TestOrphans(DaemonBase):
    """A daemon that died hard leaves its workers running; the next one to claim the board stops them (old state file names them)."""

    def setUp(self):
        super().setUp()
        p = subprocess.Popen(["true"])
        p.wait()
        self.old = f"daemon-{p.pid}"
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()  # not a group leader
            p.wait()

    def sleeper(self, ignore_term=False, new_session=True):
        """A fake worker: sleeps in its own session (a group leader), optionally ignoring SIGTERM. Always killed in tearDown."""
        code = ("import signal,sys\n" + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                + "print('ready', flush=True)\nsys.stdin.readline()")
        p = subprocess.Popen([sys.executable, "-c", code], start_new_session=new_session, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), "ready")  # the signal handling is installed
        return p

    def lock(self):
        return os.path.join(self.env["CGP_HOME"], "locks", os.path.basename(self.board_path()))

    def leave(self, *rows, raw=None):
        """What a daemon that was killed leaves behind: its lock (fresh heartbeat) and a state file with these worker rows."""
        home = self.env["CGP_HOME"]
        os.makedirs(os.path.join(home, "locks"), exist_ok=True)
        with open(self.lock(), "w") as f:
            json.dump({"session": self.old, "at": time.time()}, f)
        with open(os.path.join(home, f"state-{self.old}.json"), "w") as f:
            f.write(raw if raw is not None else json.dumps({"workers": list(rows)}))

    def row(self, p, **kw):
        return {"item": "i1", "column": "todo", "pid": p.pid, "pgid": p.pid, "start": ps_start(p.pid), "token": "t", **kw}

    def strikes(self):
        try:
            return self.data().get("daemonStrikes", {})
        except StopIteration:  # no data file yet
            return {}

    def old_rows(self):
        with open(os.path.join(self.env["CGP_HOME"], f"state-{self.old}.json")) as f:
            return json.load(f)["workers"]

    def alive(self, p):
        return p.poll() is None

    def use(self):
        return self.cgp("use", env={"CGP_SESSION": "daemon-new"})

    def test_a_live_orphan_is_killed_its_row_cleared_and_a_strike_counted(self):
        p = self.sleeper()
        self.leave(self.row(p))
        self.use()
        self.assertFalse(self.alive(p))
        self.assertEqual(self.old_rows(), [])
        self.assertEqual(self.strikes(), {"i1|todo": 1})

    def test_the_daemon_reaps_orphans_when_it_starts_and_does_not_double_dispatch(self):
        p = self.sleeper()
        self.leave(self.row(p))
        self.script(default={"result": "done: one"})
        proc, res = self.daemon("--once")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.alive(p))
        self.assertEqual(res["dispatched"], 1)  # the orphan is gone, so the one new worker is the only one
        self.assertIn("left running", proc.stderr)

    def test_a_worker_that_ignores_sigterm_is_killed(self):
        p = self.sleeper(ignore_term=True)
        self.leave(self.row(p))
        self.use()
        self.assertFalse(self.alive(p))

    def test_a_dead_daemons_state_file_is_found_without_its_lock(self):
        p = self.sleeper()
        self.leave(self.row(p))
        os.remove(self.lock())  # as `cgp gc` does
        path = os.path.join(self.env["CGP_HOME"], f"state-{self.old}.json")
        with open(path, "w") as f:
            json.dump({"boardKey": os.path.basename(self.lock())[:-5], "workers": [self.row(p)]}, f)
        self.use()
        self.assertFalse(self.alive(p))
        self.assertEqual(self.old_rows(), [])
        self.assertEqual(self.strikes(), {"i1|todo": 1})

    def test_a_live_worker_that_could_not_be_stopped_keeps_its_row_and_is_named(self):
        p = self.sleeper()
        self.leave(self.row(p, start="Thu Jan  1 00:00:00 1970"))
        r = self.cgp("use", env={"CGP_SESSION": "daemon-new"}, ok=False)
        self.assertTrue(self.alive(p))
        self.assertEqual([w["item"] for w in self.old_rows()], ["i1"])
        self.assertIn("unstick <item> --kill", r.stderr)
        self.assertIn("i1", r.stderr)

    def test_a_dead_pid_is_only_cleared(self):
        d = subprocess.Popen(["true"])
        d.wait()
        self.leave({"item": "i1", "column": "todo", "pid": d.pid, "pgid": d.pid, "start": "x", "token": "t"})
        self.use()
        self.assertEqual(self.old_rows(), [])
        self.assertEqual(self.strikes(), {})

    def test_a_pid_whose_start_time_does_not_match_is_left_alone(self):
        p = self.sleeper()
        self.leave(self.row(p, start="Thu Jan  1 00:00:00 1970"))
        self.use()
        self.assertTrue(self.alive(p))
        self.assertEqual(self.strikes(), {})

    def test_a_process_that_is_not_a_group_leader_is_left_alone(self):
        p = self.sleeper(new_session=False)  # in the test's own group
        self.leave(self.row(p, pgid=p.pid))
        self.use()
        self.assertTrue(self.alive(p))

    def test_rows_without_identity_and_pid_1_are_never_signalled(self):
        p = self.sleeper()
        self.leave({"item": "i1", "column": "todo", "pid": p.pid}, {"item": "i2", "column": "todo", "pid": 1, "pgid": 1, "start": "x"})
        self.use()
        self.assertTrue(self.alive(p))

    def test_a_missing_or_corrupt_old_state_does_nothing(self):
        self.leave(raw="{not json")
        self.use()
        os.remove(os.path.join(self.env["CGP_HOME"], f"state-{self.old}.json"))
        self.cgp("release", env={"CGP_SESSION": "daemon-new"})
        self.leave()
        os.remove(os.path.join(self.env["CGP_HOME"], f"state-{self.old}.json"))
        self.use()

    def test_a_live_daemons_workers_are_not_touched(self):
        p = self.sleeper()
        self.old = f"daemon-{os.getpid()}"  # alive; a takeover leaves it to notice by itself
        self.leave(self.row(p))
        self.cgp("use", "--takeover", env={"CGP_SESSION": "daemon-new"})
        self.assertTrue(self.alive(p))


class TestLifecycle(DaemonBase):
    def test_a_signal_lets_the_running_worker_finish_and_dispatches_nothing_new(self):
        self.script(default={"sleep": 1.5, "result": "done: slow"})
        proc = subprocess.Popen([sys.executable, test_cgp.CGP, "daemon"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=self.tmp, env=self.env)
        for _ in range(100):
            if self.calls():
                break
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, stderr)
        res = json.loads(stdout)
        self.assertEqual((res["stopped"], res["dispatched"], res["spentUsd"]), ("stopped", 1, 0.25))  # the worker was not cut short
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(glob.glob(os.path.join(self.env["CGP_HOME"], "stop-daemon-*")))
        self.assertFalse(glob.glob(os.path.join(self.env["CGP_HOME"], "locks", "*.json")))  # the board is released

    def test_the_daemon_ends_when_every_story_is_done(self):
        for item in ("i1", "i2", "i4"):
            self.force(item, "done")
        p, res = self.daemon()
        self.assertEqual((p.returncode, res["stopped"], res["dispatched"]), (0, "all stories are Done", 0))


class TestRateLimit(DaemonBase):
    def test_a_rate_limit_doubles_the_nap_and_a_good_read_resets_it(self):
        for item in ("i1", "i2", "i4"):
            self.force(item, "done")  # the daemon ends once it can read the board
        d = self.read_db()
        d["failures"] = [{"match": "items(first:100", "times": 3, "stderr": "gh: HTTP 429: API rate limit exceeded"}]
        self.write_db(d)
        p, res = self.daemon("--verbose")
        self.assertEqual((p.returncode, res["stopped"]), (0, "all stories are Done"), p.stderr)
        waits = [line.split("waiting ")[1] for line in p.stderr.splitlines() if "rate limiting" in line]
        self.assertEqual(waits, ["0.2s", "0.4s", "0.8s"])


class TestSpend(DaemonBase):
    def test_a_story_cap_shrinks_the_budget_and_then_asks(self):
        self.setting(daemonMaxBudgetUsd=4.0, daemonStoryBudgetUsd=5.0)
        self.script(default={"cost": 3.0})
        budgets = []
        for _ in range(2):
            self.daemon("--once")
            argv = self.calls()[-1]["argv"]
            budgets.append(argv[argv.index("--max-budget-usd") + 1])
        self.assertEqual(budgets, ["4.0000", "2.0000"])
        self.assertEqual(self.data()["daemonSpend"], {"i1": 6.0})
        self.daemon("--once")  # 6 of 5 spent
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(any("daemonStoryBudgetUsd" in c["body"] for c in self.read_db()["comments"]["acme/app#1"]))

    def test_the_daemon_cap_stops_new_workers(self):
        self.setting(daemonTotalBudgetUsd=2.0)
        self.script(default={"cost": 3.0})
        p, res = self.daemon()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((res["stopped"], res["dispatched"], res["spentUsd"]), ("spend cap reached", 1, 3.0))
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "2.0000")


class TestBoardResolution(DaemonBase):
    def test_several_boards_need_cgp_board_and_the_workers_get_it(self):
        first = os.path.basename(self.board_path())[:-5]
        board = self.load(self.board_path())
        board["board"].update(id="P2", number=2, title="Other", url="https://github.com/orgs/acme/projects/2")
        with open(os.path.join(os.path.dirname(self.board_path()), "other.json"), "w") as f:
            json.dump(board, f)
        self.script(default={})
        p, _ = self.daemon("--once")
        self.assertEqual(p.returncode, 6, p.stderr)  # a neutral directory and two boards
        p, res = self.daemon("--once", env={"CGP_BOARD": first})
        self.assertEqual((p.returncode, res["dispatched"]), (0, 1), p.stderr)
        self.assertEqual(self.calls()[0]["env"]["CGP_BOARD"], first)
        p, res = self.daemon("--dry-run", env={"CGP_BOARD": "https://github.com/orgs/acme/projects/1/"})  # a URL names it too
        self.assertEqual(p.returncode, 0, p.stderr)


class TestSettings(DaemonBase):
    def test_daemon_settings_are_human_only(self):
        for env in ({"CGP_SESSION": "s1"}, {"CLAUDECODE": "1"}):
            p = self.cgp("config", "daemonMaxTurns", "500", ok=False, env=env)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("can only be changed by you", p.stderr)
        self.assertNotEqual(self.cgp("config", "daemonAllowedTools", "Bash(rm:*)", ok=False).returncode, 0)  # no terminal on stdin
        self.assertEqual(self.cgp("config")["daemonMaxTurns"], 150)

    def test_a_person_can_set_them_and_bad_values_are_refused(self):
        good = {"daemonMaxTurns": ("40", 40), "daemonMaxBudgetUsd": ("2.5", 2.5), "daemonStoryBudgetUsd": ("0", 0.0),
                "daemonTotalBudgetUsd": ("12", 12.0), "daemonConcurrency": ("3", 3),
                "daemonAllowedTools": ("Bash(npm test:*), Read", ["Bash(npm test:*)", "Read"])}
        for key, (value, want) in good.items():
            p = self.tty("config", key, value)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)[key], want)
        for key, value in (("daemonMaxTurns", "0"), ("daemonMaxTurns", "many"), ("daemonMaxBudgetUsd", "0"), ("daemonMaxBudgetUsd", "-1"),
                           ("daemonStoryBudgetUsd", "lots"), ("daemonAllowedTools", "rm -rf /"), ("daemonAllowedTools", "Bash()")):
            self.assertNotEqual(self.tty("config", key, value).returncode, 0, (key, value))

    def test_headless_plan_links_are_limited_to_claude_ai_and_the_storys_own_issue(self):
        env = {"CGP_DAEMON": "1"}
        for url in ("https://github.com/other/repo/issues/1#issuecomment-5", "https://github.com/acme/app/issues/2#issuecomment-5",
                    "https://github.com/acme/app/blob/main/plan.md"):
            p = self.cgp("set", "i1", "plan", url, ok=False, env=env)
            self.assertNotEqual(p.returncode, 0, url)
            self.assertIn("headless Plan links", p.stderr)
        self.cgp("set", "i1", "plan", "https://github.com/acme/app/issues/1#issuecomment-5", env=env)
        self.cgp("set", "i1", "plan", "https://claude.ai/artifact/abc", env=env)
        self.cgp("set", "i1", "plan", "https://github.com/other/repo/issues/1#issuecomment-5")  # a chat session is not restricted


class TestDoctor(DaemonBase):
    def test_doctor_checks_that_claude_knows_the_flags(self):
        self.assertIn("✅ claude for cgp daemon", self.cgp("doctor", ok=False).stdout)
        self.fake(missing=["--max-turns", "--strict-mcp-config"])
        out = self.cgp("doctor", ok=False).stdout
        self.assertIn("⚠️  claude for cgp daemon  this claude does not list --max-turns, --strict-mcp-config", out)

    def test_doctor_warns_when_claude_is_missing(self):
        os.remove(os.path.join(self.tmp, "bin", "claude"))
        self.assertIn("⚠️  claude for cgp daemon  cgp daemon needs the claude CLI", self.cgp("doctor", ok=False, env={"PATH": os.path.join(self.tmp, "bin") + ":/usr/bin:/bin"}).stdout)


if __name__ == "__main__":
    unittest.main()
