"""Test suite for geng.

Runs on Windows, macOS and Linux with no third-party packages:

    python -m unittest discover tests -v

Tests that need `git` skip themselves cleanly when it is absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import geng  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
HAVE_GIT = shutil.which("git") is not None
# `python` is not on PATH on many Linux distros; the specs under test use whatever
# interpreter is running them, which is always correct.
PY = sys.executable


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="geng-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def spec(self, body: str, name: str = "g.toml") -> Path:
        p = self.tmp / name
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def agent(self) -> str:
        """A [agents.py] block that shells out to this interpreter."""
        return f'[agents.py]\nargv = [{json.dumps(PY)}, "-c", "{{prompt}}"]\n'

    def run_cli(self, *args: str) -> tuple[int, str]:
        """Invoke geng in-process, capturing stdout, exactly as the CLI would.

        Colour codes are stripped so assertions match on words rather than on
        terminal escapes: geng prints "<esc>skip<esc> after", which would defeat
        a naive `assertIn("skip after", ...)` on any machine without NO_COLOR set.
        """
        import io
        from contextlib import redirect_stdout, redirect_stderr

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = geng.main([*args])
        return code, ANSI.sub("", buf.getvalue())


class TestSpecValidation(Base):
    """Malformed graphs must be rejected before anything executes."""

    def test_cycle_is_rejected(self):
        s = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            needs = ["b"]
            [nodes.b]
            prompt = "print(2)"
            needs = ["a"]
        """)
        with self.assertRaises(geng.SpecError) as cm:
            geng.load_graph(s)
        self.assertIn("cycle", str(cm.exception))

    def test_unknown_dependency_is_rejected(self):
        s = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            needs = ["ghost"]
        """)
        with self.assertRaisesRegex(geng.SpecError, "ghost"):
            geng.load_graph(s)

    def test_unknown_agent_is_rejected(self):
        s = self.spec("""
            [nodes.a]
            agent = "nope"
            prompt = "print(1)"
        """)
        with self.assertRaisesRegex(geng.SpecError, "not defined"):
            geng.load_graph(s)

    def test_sole_agent_is_used_without_declaring_it(self):
        """One agent means no ambiguity, so a minimal spec needs no boilerplate."""
        g = geng.load_graph(self.spec(f"""
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
        """))
        self.assertEqual(g.nodes["a"].agent, "py")

    def test_ambiguous_agent_must_be_declared(self):
        """Two agents and no default is a real ambiguity: fail, naming the options."""
        s = self.spec(f"""
            {self.agent()}
            [agents.other]
            argv = ["echo"]
            [nodes.a]
            prompt = "print(1)"
        """)
        with self.assertRaisesRegex(geng.SpecError, "other, py"):
            geng.load_graph(s)

    def test_missing_prompt_is_rejected(self):
        s = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            needs = []
        """)
        with self.assertRaisesRegex(geng.SpecError, "prompt"):
            geng.load_graph(s)

    def test_self_dependency_is_rejected(self):
        s = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            needs = ["a"]
        """)
        with self.assertRaisesRegex(geng.SpecError, "itself"):
            geng.load_graph(s)

    def test_unknown_node_key_is_rejected(self):
        """A typo in a node key must fail loudly, not be silently ignored."""
        s = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            retires = 3
        """)
        with self.assertRaisesRegex(geng.SpecError, "unknown keys"):
            geng.load_graph(s)

    def test_graph_with_no_nodes_is_rejected(self):
        s = self.spec(self.agent())
        with self.assertRaisesRegex(geng.SpecError, "no \\[nodes"):
            geng.load_graph(s)

    def test_missing_file_is_a_spec_error(self):
        with self.assertRaisesRegex(geng.SpecError, "no such graph spec"):
            geng.load_graph(self.tmp / "absent.toml")

    def test_invalid_toml_is_a_spec_error(self):
        s = self.spec("this is not = = toml")
        with self.assertRaisesRegex(geng.SpecError, "invalid TOML"):
            geng.load_graph(s)


class TestWaves(Base):
    """Layering determines what may run concurrently, so it is load-bearing."""

    def graph(self, edges: dict[str, list[str]]) -> geng.Graph:
        body = f'[settings]\ndefault_agent = "py"\n{self.agent()}'
        for nid, needs in edges.items():
            body += f'[nodes.{nid}]\nprompt = "print(1)"\nneeds = {json.dumps(needs)}\n'
        return geng.load_graph(self.spec(body))

    def test_diamond_layers_into_three_waves(self):
        g = self.graph({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        self.assertEqual(geng.waves(g), [["a"], ["b", "c"], ["d"]])

    def test_independent_nodes_share_one_wave(self):
        g = self.graph({"a": [], "b": [], "c": []})
        self.assertEqual(geng.waves(g), [["a", "b", "c"]])

    def test_chain_produces_one_node_per_wave(self):
        g = self.graph({"a": [], "b": ["a"], "c": ["b"]})
        self.assertEqual(geng.waves(g), [["a"], ["b"], ["c"]])

    def test_node_only_runs_after_its_deepest_dependency(self):
        g = self.graph({"a": [], "b": ["a"], "c": ["b"], "d": ["a", "c"]})
        layers = geng.waves(g)
        self.assertEqual(layers.index(["d"]), 3)


class TestIdempotencyKey(Base):
    """--resume must re-run exactly the invalidated subtree and nothing else."""

    def make(self, prompt_b: str) -> geng.Graph:
        return geng.load_graph(self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "{prompt_b}"
            [nodes.b]
            prompt = "print(2)"
            needs = ["a"]
        """, name=f"k{abs(hash(prompt_b))}.toml"))

    def test_key_is_stable_across_loads(self):
        k1 = geng.node_key(g1 := self.make("print(1)"), g1.nodes["a"])
        k2 = geng.node_key(g2 := self.make("print(1)"), g2.nodes["a"])
        self.assertEqual(k1, k2)

    def test_changing_a_prompt_changes_its_key(self):
        g1, g2 = self.make("print(1)"), self.make("print(99)")
        self.assertNotEqual(geng.node_key(g1, g1.nodes["a"]),
                            geng.node_key(g2, g2.nodes["a"]))

    def test_upstream_change_invalidates_downstream(self):
        """The whole point: editing `a` must also invalidate `b`."""
        g1, g2 = self.make("print(1)"), self.make("print(99)")
        self.assertNotEqual(geng.node_key(g1, g1.nodes["b"]),
                            geng.node_key(g2, g2.nodes["b"]))


class TestTemplating(Base):
    """Edges are declared; an undeclared reference must never render as empty."""

    def test_undeclared_placeholder_is_an_error(self):
        g = geng.load_graph(self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            [nodes.b]
            prompt = "print('{{a}}')"
        """))
        store = geng.Store(g.root)
        with self.assertRaisesRegex(geng.SpecError, "unknown placeholder"):
            geng.render(g.nodes["b"], g, store)

    def test_declared_placeholder_substitutes_upstream_stdout(self):
        g = geng.load_graph(self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
            [nodes.b]
            prompt = "print('{{a}}')"
            needs = ["a"]
        """))
        store = geng.Store(g.root)
        store.out_path("a").write_text("hello upstream\n", encoding="utf-8")
        self.assertIn("hello upstream", geng.render(g.nodes["b"], g, store))

    def test_doubled_braces_survive_as_literals(self):
        """Prompts often contain f-strings; {{x}} must render as {x}."""
        g = geng.load_graph(self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(f'{{{{n}}}}')"
        """))
        out = geng.render(g.nodes["a"], g, geng.Store(g.root))
        self.assertIn("{n}", out)


class TestExecution(Base):
    """End-to-end behaviour through the real CLI entry point."""

    def pipeline(self) -> Path:
        return self.spec(f"""
            [settings]
            default_agent = "py"
            max_parallel = 3
            {self.agent()}
            [nodes.x]
            prompt = "print('X')"
            [nodes.y]
            prompt = "print('Y')"
            [nodes.join]
            needs = ["x", "y"]
            out = "build/joined.txt"
            prompt = "print('got {{x}} and {{y}}')"
        """)

    def test_fan_out_fan_in_and_artifact(self):
        code, out = self.run_cli("run", str(self.pipeline()))
        self.assertEqual(code, 0, out)
        self.assertIn("3/3 ok", out)
        joined = (self.tmp / "build" / "joined.txt").read_text(encoding="utf-8")
        self.assertEqual(joined.strip(), "got X and Y")

    def test_resume_caches_completed_nodes(self):
        spec = self.pipeline()
        self.run_cli("run", str(spec))
        code, out = self.run_cli("run", str(spec), "--resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("cached"), 3, out)

    def test_resume_reruns_only_the_invalidated_subtree(self):
        spec = self.pipeline()
        self.run_cli("run", str(spec))
        spec.write_text(spec.read_text(encoding="utf-8")
                        .replace("print('X')", "print('X2')"), encoding="utf-8")
        code, out = self.run_cli("run", str(spec), "--resume")
        self.assertEqual(code, 0, out)
        # y is untouched and stays cached; x changed and join depends on it.
        self.assertIn("cached y", out)
        self.assertNotIn("cached x", out)
        self.assertNotIn("cached join", out)

    def test_gate_failure_halts_the_graph(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.bad]
            gate = true
            prompt = "raise SystemExit(3)"
            [nodes.after]
            needs = ["bad"]
            prompt = "print('should not run')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        self.assertIn("halted", out)
        self.assertFalse((self.tmp / ".geng" / "out" / "after.txt").exists())

    def test_downstream_is_skipped_when_dependency_fails(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.bad]
            prompt = "raise SystemExit(1)"
            [nodes.after]
            needs = ["bad"]
            prompt = "print('nope')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        self.assertIn("skip after", out)

    def test_retries_until_success(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.flaky]
            retries = 2
            prompt = "import pathlib,sys; p=pathlib.Path('n'); n=(int(p.read_text()) if p.exists() else 0)+1; p.write_text(str(n)); sys.exit(0 if n>=3 else 1)"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        state = json.loads((self.tmp / ".geng" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["flaky"]["attempts"], 3)

    def test_ok_exit_accepts_a_nonzero_code(self):
        """grep-style tools use exit 1 for 'no match', which can be success."""
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            ok_exit = [7]
            prompt = "raise SystemExit(7)"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)

    def test_only_pulls_in_transitive_dependencies(self):
        code, out = self.run_cli("run", str(self.pipeline()), "--only", "join")
        self.assertEqual(code, 0, out)
        self.assertIn("3/3 ok", out)

    def test_missing_executable_is_reported_not_raised(self):
        spec = self.spec("""
            [agents.ghost]
            argv = ["definitely-not-a-real-binary-xyz"]
            [settings]
            default_agent = "ghost"
            [nodes.a]
            prompt = "hi"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        self.assertIn("exit=127", out)

    def test_timeout_is_enforced(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.slow]
            timeout = 1
            prompt = "import time; time.sleep(30)"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        self.assertIn("exit=124", out)

    def test_python_token_resolves_to_this_interpreter(self):
        """`{python}` keeps specs portable where only `python3` is on PATH."""
        spec = self.spec("""
            [agents.p]
            argv = ["{python}", "-c", "{prompt}"]
            [settings]
            default_agent = "p"
            [nodes.a]
            prompt = "import sys; print(sys.executable)"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        got = (self.tmp / ".geng" / "out" / "a.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(Path(got), Path(PY))

    def test_prompt_via_none_appends_nothing(self):
        """A fixed command (a test runner) must not receive the prompt as argv."""
        spec = self.spec(f"""
            [agents.fixed]
            argv = [{json.dumps(PY)}, "-c", "import sys; print(len(sys.argv) - 1)"]
            prompt_via = "none"
            [settings]
            default_agent = "fixed"
            [nodes.a]
            prompt = "this text is documentation, not an argument"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        extra = (self.tmp / ".geng" / "out" / "a.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(extra, "0", "prompt leaked into argv")

    def test_bad_prompt_via_is_rejected(self):
        s = self.spec("""
            [agents.x]
            argv = ["echo"]
            prompt_via = "telepathy"
            [nodes.a]
            prompt = "hi"
        """)
        with self.assertRaisesRegex(geng.SpecError, "prompt_via"):
            geng.load_graph(s)

    def test_stdin_prompt_delivery(self):
        spec = self.spec(f"""
            [agents.stdin_py]
            argv = [{json.dumps(PY)}, "-"]
            prompt_via = "stdin"
            [settings]
            default_agent = "stdin_py"
            [nodes.a]
            prompt = "print('via stdin')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        self.assertIn("via stdin",
                      (self.tmp / ".geng" / "out" / "a.txt").read_text(encoding="utf-8"))

    def test_parallel_nodes_do_not_corrupt_state(self):
        """Every node in a wide wave must appear exactly once in state.json."""
        body = f'[settings]\ndefault_agent = "py"\nmax_parallel = 8\n{self.agent()}'
        for n in range(12):
            body += f'[nodes.n{n}]\nprompt = "print({n})"\n'
        code, out = self.run_cli("run", str(self.spec(body)))
        self.assertEqual(code, 0, out)
        state = json.loads((self.tmp / ".geng" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state), 12)
        self.assertTrue(all(v["status"] == "ok" for v in state.values()))


class TestDryRun(Base):
    """A dry run reports what would happen and changes nothing."""

    def test_dry_run_writes_no_artifacts(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            out = "build/a.txt"
            prompt = "print('side effect')"
        """)
        code, out = self.run_cli("run", str(spec), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.tmp / "build").exists())
        self.assertFalse((self.tmp / ".geng" / "state.json").exists())

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_dry_run_creates_no_worktree(self):
        repo = self.git_repo()
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            {self.agent()}
            [nodes.a]
            isolate = true
            prompt = "print('x')"
        """, name="iso.toml")
        shutil.move(str(spec), repo / "iso.toml")
        code, out = self.run_cli("run", str(repo / "iso.toml"), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertFalse((repo / ".geng" / "wt").exists())

    def git_repo(self) -> Path:
        return _make_repo(self.tmp / "repo")


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main", ".")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "geng test")
    run("git", "config", "commit.gpgsign", "false")
    (path / "file.txt").write_text("base\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    return path


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TestWorktreeIsolation(Base):
    """isolate = true is the safety property that lets a wave write in parallel."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = _make_repo(self.tmp / "repo")

    def spec_in_repo(self, body: str) -> Path:
        p = self.repo / "g.toml"
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def git(self, *args: str) -> str:
        return subprocess.run(("git",) + args, cwd=self.repo,
                              capture_output=True, text=True).stdout

    def test_parallel_writers_to_one_file_stay_isolated(self):
        spec = self.spec_in_repo(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            max_parallel = 2
            {self.agent()}
            [nodes.alpha]
            isolate = true
            prompt = "open('file.txt','w').write('alpha')"
            [nodes.beta]
            isolate = true
            prompt = "open('file.txt','w').write('beta')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git("show", "geng/alpha:file.txt").strip(), "alpha")
        self.assertEqual(self.git("show", "geng/beta:file.txt").strip(), "beta")
        # The checkout the developer is sitting in must be untouched.
        self.assertEqual(self.git("show", "main:file.txt").strip(), "base")

    def test_failed_node_publishes_no_commit(self):
        """A half-finished edit must never look like a reviewable deliverable."""
        spec = self.spec_in_repo(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            {self.agent()}
            [nodes.bad]
            isolate = true
            prompt = "open('file.txt','w').write('half'); raise SystemExit(1)"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        state = json.loads((self.repo / ".geng" / "state.json").read_text(encoding="utf-8"))
        self.assertIsNone(state["bad"]["commit"])
        self.assertEqual(self.git("show", "geng/bad:file.txt").strip(), "base")

    def test_node_that_changes_nothing_records_no_commit(self):
        spec = self.spec_in_repo(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            {self.agent()}
            [nodes.noop]
            isolate = true
            prompt = "print('looked around, changed nothing')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        state = json.loads((self.repo / ".geng" / "state.json").read_text(encoding="utf-8"))
        self.assertIsNone(state["noop"]["commit"])

    def test_downstream_reads_branch_placeholders(self):
        spec = self.spec_in_repo(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            {self.agent()}
            [nodes.edit]
            isolate = true
            prompt = "open('file.txt','w').write('changed')"
            [nodes.report]
            needs = ["edit"]
            out = "report.txt"
            prompt = "print('branch={{edit_branch}} commit={{edit_commit}}')"
        """)
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        report = (self.repo / "report.txt").read_text(encoding="utf-8")
        self.assertIn("branch=geng/edit", report)
        self.assertRegex(report, r"commit=[0-9a-f]{6,}")


class TestConcurrencyGuard(Base):
    """Two runs against one repo must not silently lose each other's records."""

    def test_second_concurrent_run_is_refused(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
        """)
        graph = geng.load_graph(spec)
        lock = self.tmp / ".geng" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("held by a pretend run", encoding="utf-8")
        with self.assertRaisesRegex(geng.SpecError, "already in progress"):
            geng.run(graph, False, None, 0, False)

    def test_lock_is_released_after_a_run(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
        """)
        self.assertEqual(self.run_cli("run", str(spec))[0], 0)
        self.assertFalse((self.tmp / ".geng" / ".lock").exists())
        # A second sequential run must therefore succeed.
        self.assertEqual(self.run_cli("run", str(spec))[0], 0)

    def test_lock_is_released_even_when_a_node_fails(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "raise SystemExit(1)"
        """)
        self.assertEqual(self.run_cli("run", str(spec))[0], 1)
        self.assertFalse((self.tmp / ".geng" / ".lock").exists())

    def test_dry_run_needs_no_lock(self):
        spec = self.spec(f"""
            [settings]
            default_agent = "py"
            {self.agent()}
            [nodes.a]
            prompt = "print(1)"
        """)
        lock = self.tmp / ".geng" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("held", encoding="utf-8")
        self.assertEqual(self.run_cli("run", str(spec), "--dry-run")[0], 0)


class TestKeyMemoization(Base):
    """A gather-all sink must not cost exponential time to key."""

    def test_dense_fan_in_keys_quickly(self):
        body = f'[settings]\ndefault_agent = "py"\n{self.agent()}'
        ids = [f"n{i}" for i in range(30)]
        for i, nid in enumerate(ids):
            body += (f'[nodes.{nid}]\nprompt = "print({i})"\n'
                     f'needs = {json.dumps(ids[:i])}\n')
        graph = geng.load_graph(self.spec(body))
        start = time.monotonic()
        key = geng.node_key(graph, graph.nodes["n29"])
        elapsed = time.monotonic() - start
        self.assertEqual(len(key), 16)
        # Unmemoized this took over 70s; memoized it is milliseconds.
        self.assertLess(elapsed, 2.0, f"keying 30 dense nodes took {elapsed:.1f}s")

    def test_memo_does_not_leak_between_graphs(self):
        """A fresh load must recompute, or an edited prompt would stay cached."""
        def build(p):
            return geng.load_graph(self.spec(f"""
                [settings]
                default_agent = "py"
                {self.agent()}
                [nodes.a]
                prompt = "{p}"
            """, name=f"m{p}.toml"))
        self.assertNotEqual(geng.node_key(g1 := build("one"), g1.nodes["a"]),
                            geng.node_key(g2 := build("two"), g2.nodes["a"]))


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TestWorktreeRecovery(Base):
    """The common "interrupt a run, delete .geng, retry" path must self-heal."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = _make_repo(self.tmp / "repo")
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "marker.txt").write_text("in pkg\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "pkg"], cwd=self.repo, capture_output=True)

    def iso_spec(self, extra: str = "") -> Path:
        p = self.repo / "g.toml"
        p.write_text(textwrap.dedent(f"""
            [settings]
            default_agent = "py"
            base_ref = "main"
            {self.agent()}
            [nodes.edit]
            isolate = true
            {extra}
            prompt = "open('touched.txt','w').write('x')"
        """), encoding="utf-8")
        return p

    def test_rerun_after_state_is_deleted(self):
        spec = self.iso_spec()
        self.assertEqual(self.run_cli("run", str(spec))[0], 0)
        # Simulate a user wiping state for a clean slate; git's worktree metadata
        # and the geng/edit branch both survive this.
        shutil.rmtree(self.repo / ".geng")
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)

    def test_isolate_and_cwd_compose(self):
        """cwd must scope INSIDE the worktree, not be silently discarded."""
        spec = self.iso_spec('cwd = "pkg"')
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 0, out)
        wt = self.repo / ".geng" / "wt" / "edit"
        self.assertTrue((wt / "pkg" / "touched.txt").exists(),
                        "node did not run inside its cwd within the worktree")
        self.assertFalse((wt / "touched.txt").exists())
        # The commit must still capture work from the worktree root.
        shown = subprocess.run(["git", "show", "--stat", "geng/edit"], cwd=self.repo,
                               capture_output=True, text=True).stdout
        self.assertIn("pkg/touched.txt", shown.replace("\\", "/"))


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TestErrorPathLogging(Base):
    """If the runner prints a log path, that file must exist."""

    def test_spec_error_still_writes_a_log(self):
        repo = _make_repo(self.tmp / "repo")
        spec = repo / "g.toml"
        spec.write_text(textwrap.dedent(f"""
            [settings]
            default_agent = "py"
            base_ref = "does-not-exist-ref"
            {self.agent()}
            [nodes.edit]
            isolate = true
            prompt = "print(1)"
        """), encoding="utf-8")
        code, out = self.run_cli("run", str(spec))
        self.assertEqual(code, 1)
        self.assertIn("ERR", out)
        log = repo / ".geng" / "log" / "edit.log"
        self.assertTrue(log.exists(), "runner reported an error but wrote no log")
        self.assertIn("error", log.read_text(encoding="utf-8"))


class TestShippedExamples(Base):
    """The examples in the repo must actually load and render."""

    def test_every_example_spec_loads(self):
        for spec in sorted((ROOT / "examples").glob("*.toml")):
            with self.subTest(spec=spec.name):
                graph = geng.load_graph(spec)
                self.assertTrue(graph.nodes)
                geng.waves(graph)

    def test_smoke_example_runs_green(self):
        """smoke.toml is the zero-dependency quickstart, so it must always pass."""
        dest = self.tmp / "smoke.toml"
        # The example uses geng's own `{python}` token, so it needs no rewriting.
        dest.write_text((ROOT / "examples" / "smoke.toml").read_text(encoding="utf-8"),
                        encoding="utf-8")
        code, out = self.run_cli("run", str(dest))
        self.assertEqual(code, 0, out)
        self.assertIn("6/6 ok", out)


if __name__ == "__main__":
    unittest.main()
