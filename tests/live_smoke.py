#!/usr/bin/env python3
"""Opt-in smoke test against a real, throwaway board: CGP_LIVE_TEST=1 CGP_LIVE_BOARD=<board url> python3 tests/live_smoke.py

Runs the read-only commands (setup --dry-run, doctor, status, list) with your real gh token and checks that their output has the
expected shape. It changes nothing on the board. The unit tests use a fake gh, so this is what catches a GraphQL schema change."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CGP = os.path.join(ROOT, "scripts", "cgp")
BOARD = os.environ.get("CGP_LIVE_BOARD", "")


def run(*args):
    p = subprocess.run([sys.executable, CGP, *args], capture_output=True, text=True)
    return p


@unittest.skipUnless(os.environ.get("CGP_LIVE_TEST") == "1" and BOARD, "set CGP_LIVE_TEST=1 and CGP_LIVE_BOARD=<board url>")
class LiveSmoke(unittest.TestCase):
    def test_dry_run_setup_reads_the_board(self):
        p = run("setup", BOARD, "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        res = json.loads(p.stdout)
        self.assertTrue(res["dryRun"])
        self.assertIn("columns", res)

    def test_doctor_has_no_hard_failures(self):
        p = run("doctor", "--board", BOARD)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_status_and_list_have_the_expected_shape(self):
        status = run("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("counts", json.loads(status.stdout))
        listing = run("list", "--brief")
        self.assertIn(listing.returncode, (0, 5), listing.stderr)  # 5: another session runs this board
        if listing.returncode == 0:
            self.assertIn("batch", json.loads(listing.stdout))

    def test_issue_dependencies_are_readable(self):
        # the blockedBy field the board query asks for: `status --json` lists what GitHub says blocks each story
        status = run("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIsInstance(json.loads(status.stdout)["githubBlockedBy"], list)
        self.assertNotIn("does not offer issue dependencies", status.stderr)


if __name__ == "__main__":
    unittest.main()
