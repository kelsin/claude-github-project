"""Native Windows support, exercised on any OS: the Windows branches run against stubs, and static checks keep POSIX-only imports guarded."""
import ast
import glob
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "scripts", "cgp_lib")


def fresh_import(name, **modules):
    """cgp_lib.<name> imported anew, with sys.modules entries (None hides a module) patched in during the import."""
    for n in [n for n in sys.modules if n == "cgp_lib" or n.startswith("cgp_lib.")]:
        del sys.modules[n]
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    saved = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        return importlib.import_module(f"cgp_lib.{name}")
    finally:
        sys.path.remove(os.path.join(ROOT, "scripts"))
        for k, v in saved.items():
            sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)


class StubMsvcrt(types.ModuleType):
    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self):
        super().__init__("msvcrt")
        self.calls = []

    def locking(self, fd, mode, n):
        self.calls.append((mode, n))


class TestWindowsImports(unittest.TestCase):
    def test_cli_imports_without_fcntl_and_the_lock_is_reentrant(self):
        msvcrt = StubMsvcrt()
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"CGP_HOME": home}):
            fresh_import("cli", fcntl=None, msvcrt=msvcrt)
            store = sys.modules["cgp_lib.store"]
            self.assertIsNone(store.fcntl)
            with mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
                with store.locked():
                    with store.locked():
                        pass
            self.assertEqual(msvcrt.calls, [(msvcrt.LK_NBLCK, 1), (msvcrt.LK_UNLCK, 1)])

    @unittest.skipIf(sys.platform == "win32", "needs fcntl")
    def test_posix_still_uses_flock(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"CGP_HOME": home}):
            store = fresh_import("store")
            with mock.patch.object(store.fcntl, "flock") as flock:
                with store.locked():
                    pass
            self.assertEqual([c.args[1] for c in flock.call_args_list], [store.fcntl.LOCK_EX, store.fcntl.LOCK_UN])

    def test_no_unguarded_posix_only_imports(self):
        bad = []
        for path in glob.glob(os.path.join(LIB, "*.py")):
            with open(path, encoding="utf-8") as f:
                body = ast.parse(f.read()).body  # top level only: a guarded import sits inside a Try
            for node in body:
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                bad += [f"{os.path.basename(path)}: {n}" for n in names if n.split(".")[0] in ("fcntl", "pty", "pwd", "termios", "grp")]
        self.assertEqual(bad, [])

    def test_hooks_json_runs_the_cli(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)
        commands = [h["command"] for g in hooks["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertTrue(commands and all("scripts/cgp" in c for c in commands))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_hooks_json_falls_through_a_broken_python3(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            command = json.load(f)["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory() as tmp:
            plugin = os.path.join(tmp, "plugin")
            os.makedirs(os.path.join(plugin, "scripts"))
            with open(os.path.join(plugin, "scripts", "cgp"), "w") as f:
                f.write("import sys; print('ran', *sys.argv[1:])\n")
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            for name, body in (("python3", "#!/bin/sh\nexit 9009\n"), ("python", '#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)):
                path = os.path.join(bindir, name)
                with open(path, "w") as f:
                    f.write(body)
                os.chmod(path, 0o755)
            env = {**os.environ, "CLAUDE_PLUGIN_ROOT": plugin, "PATH": bindir + os.pathsep + os.environ["PATH"]}
            p = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
            self.assertEqual(p.stdout.strip(), "ran session-title", p.stderr)


class TestWindowsBranches(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        patch = mock.patch.dict(os.environ, {"CGP_HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)
        fresh_import("cli")
        self.util = sys.modules["cgp_lib.util"]

    def test_unix_only_helpers_take_the_windows_branch(self):
        from_mod = lambda n: sys.modules[f"cgp_lib.{n}"]
        with mock.patch.object(self.util, "IS_WINDOWS", True), mock.patch.object(from_mod("gitwt"), "IS_WINDOWS", True), \
                mock.patch.object(from_mod("session"), "IS_WINDOWS", True), mock.patch.object(from_mod("notify"), "IS_WINDOWS", True), \
                mock.patch.object(from_mod("unstick"), "IS_WINDOWS", True), mock.patch("subprocess.run", side_effect=AssertionError("no subprocess")):
            self.assertEqual(self.util.ps_field(os.getpid(), "lstart"), "")
            self.assertTrue(from_mod("gitwt").git_running())
            self.assertIn("not supported on Windows", from_mod("notify").command_problem("/bin/true"))
            with mock.patch.object(from_mod("session"), "pid_alive", return_value=True):
                self.assertFalse(from_mod("session").kill_orphan({"pid": 4242, "pgid": 4242, "start": "x"}))
            unstick = from_mod("unstick")
            with mock.patch.object(unstick, "pid_alive", return_value=True):
                self.assertTrue(unstick.live_worker({"pid": 4242, "start": "Mon Jan  1 00:00:00 2024"}))  # no start time to compare

    def test_pid_alive_asks_the_os_for_the_exit_code(self):
        state = types.SimpleNamespace(code=259, handle=1, closed=[])

        def fn(f):
            f.argtypes = f.restype = None
            return f

        @fn
        def open_process(access, inherit, pid):
            return state.handle

        @fn
        def exit_code(handle, ref):
            ref.value = state.code
            return 1

        @fn
        def close_handle(handle):
            state.closed.append(handle)
        wintypes = types.SimpleNamespace(DWORD=lambda: types.SimpleNamespace(value=0), BOOL=bool, HANDLE=object)
        kernel32 = types.SimpleNamespace(OpenProcess=open_process, GetExitCodeProcess=exit_code, CloseHandle=close_handle)
        ctypes = types.SimpleNamespace(WinDLL=lambda name, use_last_error=False: kernel32, POINTER=lambda t: t, byref=lambda o: o,
                                       get_last_error=lambda: 5, wintypes=wintypes)
        with mock.patch.object(self.util, "IS_WINDOWS", True), mock.patch.dict(sys.modules, {"ctypes": ctypes, "ctypes.wintypes": wintypes}), \
                mock.patch("os.kill", side_effect=AssertionError("os.kill would terminate the process on Windows")):
            self.assertTrue(self.util.pid_alive(7))
            self.assertEqual(state.closed, [1])
            state.code = 1  # exited
            self.assertFalse(self.util.pid_alive(7))
            state.handle = 0  # cannot open: access denied means it exists
            self.assertTrue(self.util.pid_alive(7))

    def test_replace_retry_waits_out_a_transient_permission_error(self):
        store = sys.modules["cgp_lib.store"]
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("in use")
            return "ok"
        with mock.patch.object(store, "IS_WINDOWS", True), mock.patch.object(store.os, "replace", flaky), mock.patch.object(store.time, "sleep"):
            self.assertEqual(store.replace_retry("a", "b"), "ok")
        self.assertEqual(len(calls), 3)
        with mock.patch.object(store, "IS_WINDOWS", False), mock.patch.object(store.os, "replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                store.replace_retry("a", "b")  # POSIX never retries


class TestEncoding(unittest.TestCase):
    @staticmethod
    def legacy_env(home, **extra):
        return {**os.environ, "CGP_HOME": home, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "PYTHONCOERCECLOCALE": "0",
                "LC_ALL": "C", "LANG": "C", **extra}

    def test_emoji_survives_a_legacy_code_page(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, "x.json")
            code = ("import sys; sys.path.insert(0, %r)\nfrom cgp_lib.store import save_json, load_json\n"
                    "save_json(%r, {'t': '\\U0001F680 caf\\u00e9'})\nprint(ascii(load_json(%r, None)['t']))\n" % (os.path.join(ROOT, "scripts"), path, path))
            p = subprocess.run([sys.executable, "-c", code], env=self.legacy_env(home), capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), b"'\\U0001f680 caf\\xe9'")
            with open(path, "rb") as f:
                raw = f.read()
            self.assertIn("\U0001F680 café".encode("utf-8"), raw)
            self.assertNotIn(b"\r\n", raw)

    def test_stdin_is_read_as_utf8(self):
        with tempfile.TemporaryDirectory() as home:
            prompt = "/cgp:run https://github.com/users/\u3042\u3044/projects/3"
            stdin = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
            p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "cgp"), "session-title"], input=stdin,
                               env=self.legacy_env(home), capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("\u3042\u3044", json.loads(p.stdout.decode("utf-8"))["hookSpecificOutput"]["sessionTitle"])


if __name__ == "__main__":
    unittest.main()
