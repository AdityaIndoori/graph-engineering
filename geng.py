#!/usr/bin/env python3
"""geng - a harness-agnostic graph runner for coding agents.

A node is a subprocess. An edge is a file. The exit code is the contract.
Nothing here knows or cares which agent CLI you use.

    python geng.py run graph.toml
    python geng.py plan graph.toml          # mermaid + wave preview, no execution
    python geng.py run graph.toml --resume  # skip nodes already green
    python geng.py run graph.toml --only lint,test

Stdlib only. Python 3.11+ (tomllib).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shlex
import string
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- spec loading

RESERVED = {"agents", "settings", "nodes"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


class SpecError(Exception):
    """The graph is malformed. Raised before anything executes."""


@dataclass
class Agent:
    """A harness adapter: how to turn a prompt string into an argv."""

    name: str
    argv: list[str]
    prompt_via: str = "arg"  # "arg" | "stdin"
    env: dict[str, str] = field(default_factory=dict)
    ok_exit: list[int] = field(default_factory=lambda: [0])


@dataclass
class Node:
    id: str
    agent: str
    prompt: str
    needs: list[str] = field(default_factory=list)
    out: str | None = None
    retries: int = 0
    timeout: int | None = None
    isolate: bool = False
    cwd: str | None = None
    gate: bool = False
    env: dict[str, str] = field(default_factory=dict)
    ok_exit: list[int] | None = None


@dataclass
class Graph:
    path: Path
    root: Path
    agents: dict[str, Agent]
    nodes: dict[str, Node]
    settings: dict
    # Memo for node_key; populated lazily, shared across the whole run.
    keys: dict[str, str] = field(default_factory=dict)


def _as_list(value, what: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise SpecError(f"{what}: expected a string or list of strings, got {value!r}")


def load_graph(path: Path) -> Graph:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecError(f"no such graph spec: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"{path}: invalid TOML: {exc}")

    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise SpecError("[settings] must be a table")

    agents: dict[str, Agent] = {}
    for name, body in (raw.get("agents") or {}).items():
        if not isinstance(body, dict):
            raise SpecError(f"agents.{name} must be a table")
        argv = body.get("argv")
        if isinstance(argv, str):
            argv = shlex.split(argv)
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise SpecError(f"agents.{name}.argv must be a non-empty argv list or command string")
        prompt_via = body.get("prompt_via", "arg")
        if prompt_via not in ("arg", "stdin", "none"):
            raise SpecError(
                f"agents.{name}.prompt_via must be 'arg', 'stdin' or 'none' "
                f"('none' for tools that take no prompt, like a fixed test command)")
        agents[name] = Agent(
            name=name,
            argv=argv,
            prompt_via=prompt_via,
            env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
            ok_exit=[int(c) for c in _as_list_int(body.get("ok_exit"), f"agents.{name}.ok_exit")] or [0],
        )

    nodes: dict[str, Node] = {}
    node_table = raw.get("nodes")
    if not isinstance(node_table, dict) or not node_table:
        raise SpecError("spec defines no [nodes.<id>] tables")
    for nid, body in node_table.items():
        if not IDENT.match(nid):
            raise SpecError(f"node id {nid!r} must match {IDENT.pattern}")
        if not isinstance(body, dict):
            raise SpecError(f"nodes.{nid} must be a table")
        unknown = set(body) - {
            "agent", "prompt", "needs", "out", "retries", "timeout",
            "isolate", "cwd", "gate", "env", "ok_exit",
        }
        if unknown:
            raise SpecError(f"nodes.{nid}: unknown keys {sorted(unknown)}")
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SpecError(f"nodes.{nid}.prompt is required and must be a non-empty string")
        agent = body.get("agent", settings.get("default_agent"))
        if agent is None and len(agents) == 1:
            # With exactly one agent defined there is no ambiguity, so requiring
            # default_agent would be ceremony. Two or more must be explicit.
            agent = next(iter(agents))
        if not isinstance(agent, str):
            raise SpecError(
                f"nodes.{nid} has no agent: set `agent = ...` on the node, or "
                f"`default_agent = ...` under [settings] "
                f"(defined agents: {', '.join(sorted(agents)) or 'none'})")
        if agent not in agents:
            raise SpecError(f"nodes.{nid}.agent = {agent!r} is not defined in [agents]")
        nodes[nid] = Node(
            id=nid,
            agent=agent,
            prompt=prompt,
            needs=_as_list(body.get("needs"), f"nodes.{nid}.needs"),
            out=body.get("out"),
            retries=int(body.get("retries", 0)),
            timeout=int(body["timeout"]) if body.get("timeout") is not None else None,
            isolate=bool(body.get("isolate", False)),
            cwd=body.get("cwd"),
            gate=bool(body.get("gate", False)),
            env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
            ok_exit=[int(c) for c in _as_list_int(body.get("ok_exit"), f"nodes.{nid}.ok_exit")] or None,
        )

    for nid, node in nodes.items():
        for dep in node.needs:
            if dep not in nodes:
                raise SpecError(f"nodes.{nid}.needs references unknown node {dep!r}")
            if dep == nid:
                raise SpecError(f"nodes.{nid} depends on itself")

    graph = Graph(path=path, root=path.parent.resolve(), agents=agents, nodes=nodes, settings=settings)
    waves(graph)  # raises on cycle
    return graph


def _as_list_int(value, what: str) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list) and all(isinstance(v, int) for v in value):
        return list(value)
    raise SpecError(f"{what}: expected an int or list of ints, got {value!r}")


def waves(graph: Graph) -> list[list[str]]:
    """Kahn layering. Each wave is a set of nodes with no unmet deps."""
    pending = {nid: set(n.needs) for nid, n in graph.nodes.items()}
    done: set[str] = set()
    out: list[list[str]] = []
    while pending:
        ready = sorted(nid for nid, deps in pending.items() if deps <= done)
        if not ready:
            raise SpecError(f"dependency cycle among: {sorted(pending)}")
        out.append(ready)
        done |= set(ready)
        for nid in ready:
            del pending[nid]
    return out


# ------------------------------------------------------------------ artifacts

class Store:
    """Durable node state on disk. Survives reboots, CI runners, and harness swaps.

    .geng/
      state.json          node -> {status, exit, attempts, key, out, started, ended}
      out/<node>.txt      captured stdout (the edge payload downstream nodes read)
      log/<node>.log      stdout+stderr transcript for humans
    """

    def __init__(self, root: Path):
        self.dir = root / ".geng"
        self.out = self.dir / "out"
        self.log = self.dir / "log"
        for d in (self.dir, self.out, self.log):
            d.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "state.json"
        # Nodes in a wave run on threads and all record into this one file.
        self.lock = threading.Lock()
        try:
            self.state = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.state = {}

    def record(self, nid: str, **fields) -> None:
        with self.lock:
            self.state.setdefault(nid, {}).update(fields)
            # Unique tmp name: os.replace onto a path another thread still has
            # open fails with PermissionError on Windows.
            tmp = self.file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.file)  # atomic; a crash mid-write never truncates state

    def get(self, nid: str) -> dict:
        return self.state.get(nid, {})

    def out_path(self, nid: str) -> Path:
        return self.out / f"{nid}.txt"

    def read_out(self, nid: str) -> str:
        try:
            return self.out_path(nid).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""


def node_key(graph: Graph, node: Node, _memo: dict[str, str] | None = None) -> str:
    """Idempotency key: node identity + prompt + agent argv + dep keys.

    Change the prompt or an upstream node and the key changes, so --resume
    re-runs exactly the affected subtree and nothing else.

    Memoized per call tree: without it, a graph whose sink depends on every
    earlier node (a gather-all summarizer) recomputes ancestors exponentially —
    measured at over 70 s for 30 such nodes.
    """
    memo = graph.keys if _memo is None else _memo
    if node.id in memo:
        return memo[node.id]
    h = hashlib.sha256()
    h.update(node.id.encode())
    h.update(node.prompt.encode())
    h.update("\x00".join(graph.agents[node.agent].argv).encode())
    for dep in sorted(node.needs):
        h.update(node_key(graph, graph.nodes[dep], memo).encode())
    memo[node.id] = h.hexdigest()[:16]
    return memo[node.id]


# ------------------------------------------------------------------ templating

class _Ctx(dict):
    """Missing keys raise instead of silently yielding an empty prompt."""

    def __missing__(self, key):
        raise SpecError(f"prompt references unknown placeholder {{{key}}}")


def render(node: Node, graph: Graph, store: Store) -> str:
    """Substitute {dep} with an upstream node's captured stdout, {dep_path} with its file.

    For an isolated upstream, {dep_branch} and {dep_worktree} name the branch and
    checkout holding its committed work — that is how a reviewer node gets a diff
    to look at.

    Only declared dependencies are in scope: an undeclared reference is an error,
    not an empty string. That keeps the spec's edges honest.
    """
    ctx = _Ctx(graph_root=str(graph.root))
    for dep in node.needs:
        ctx[dep] = store.read_out(dep).strip()
        ctx[f"{dep}_path"] = str(store.out_path(dep))
        if graph.nodes[dep].isolate:
            # Branch and worktree paths are derived from the spec, not from state,
            # so a --dry-run resolves them without having executed anything.
            ctx[f"{dep}_branch"] = f"geng/{dep}"
            ctx[f"{dep}_worktree"] = str(graph.root / ".geng" / "wt" / dep)
            ctx[f"{dep}_commit"] = store.get(dep).get("commit") or ""
    try:
        return string.Formatter().vformat(node.prompt, (), ctx)
    except (IndexError, KeyError) as exc:
        raise SpecError(f"nodes.{node.id}.prompt: bad placeholder {exc}")


# ------------------------------------------------------------------- isolation

def make_worktree(root: Path, nid: str, base: str) -> Path:
    """git worktree per node. The one isolation primitive every harness respects."""
    wt = root / ".geng" / "wt" / nid
    branch = f"geng/{nid}"
    # Clean up unconditionally. Gating this on `wt.exists()` leaves git's internal
    # .git/worktrees/<nid> metadata dangling whenever .geng was deleted out of band
    # (a reset, an evicted CI cache, an interrupted run), and the node then fails
    # forever with "a branch named 'geng/<nid>' already exists".
    for cleanup in (["worktree", "prune"],
                    ["worktree", "remove", "--force", str(wt)],
                    ["branch", "-D", branch]):
        subprocess.run(["git", *cleanup], cwd=root, capture_output=True, text=True)
    r = subprocess.run(["git", "worktree", "add", "-b", branch, str(wt), base],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise SpecError(f"nodes.{nid}: git worktree add failed: {r.stderr.strip()}\n"
                        f"    if a stale worktree is to blame, try: git worktree prune")
    return wt


def commit_worktree(wt: Path, nid: str) -> str | None:
    """Commit whatever the node changed, so the branch IS the deliverable.

    Without this an isolated node's edits sit unstaged in a throwaway checkout and
    `git show geng/<nid>` returns the base tree — a downstream reviewer node would
    review nothing at all. Returns the new sha, or None if the node changed nothing.
    """
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=wt,
                           capture_output=True, text=True).stdout.strip()
    if not dirty:
        return None
    subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, text=True)
    r = subprocess.run(["git", "commit", "-m", f"geng: {nid}", "--no-verify"],
                       cwd=wt, capture_output=True, text=True)
    if r.returncode != 0:
        raise SpecError(f"nodes.{nid}: git commit in worktree failed: "
                        f"{(r.stderr or r.stdout).strip()}")
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=wt,
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------- runner

RESET, DIM, RED, GRN, YEL, CYN = "\033[0m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m"


def _c(text: str, colour: str) -> str:
    return text if os.environ.get("NO_COLOR") else f"{colour}{text}{RESET}"


def run_node(graph: Graph, node: Node, store: Store, dry: bool) -> tuple[bool, int]:
    """Execute one node. Returns (ok, exit_code). Never raises on agent failure."""
    agent = graph.agents[node.agent]
    prompt = render(node, graph, store)
    # `{python}` resolves to the interpreter running geng. Without it a spec has
    # to hardcode `python`, which is absent on many Linux distributions.
    argv = [a.replace("{python}", sys.executable) for a in agent.argv]
    argv = [a.replace("{prompt}", prompt) if "{prompt}" in a else a for a in argv]
    if agent.prompt_via == "arg" and not any("{prompt}" in a for a in agent.argv):
        argv = argv + [prompt]

    # A dry run must not mutate the repo: report and bail before creating any
    # worktree or touching state. It validates argv and prompt rendering only.
    if dry:
        base = graph.settings.get("base_ref", "HEAD")
        where = f"new worktree from {base}" + (f" then {node.cwd}" if node.cwd else "") \
                if node.isolate else \
                str((graph.root / node.cwd).resolve() if node.cwd else graph.root)
        print(f"    {_c('cwd ', DIM)} {where}")
        print(f"    {_c('argv', DIM)} {shlex.join(argv)}")
        return True, 0

    # `cwd` scopes WITHIN the worktree rather than being mutually exclusive with it:
    # a per-package migration node needs both its own branch and its own directory.
    workdir = graph.root
    wt_root: Path | None = None
    if node.isolate:
        wt_root = make_worktree(graph.root, node.id, graph.settings.get("base_ref", "HEAD"))
        workdir = wt_root
    if node.cwd:
        workdir = (workdir / node.cwd).resolve()

    env = {**os.environ, **agent.env, **node.env,
           "GENG_NODE": node.id, "GENG_OUT": str(store.out_path(node.id)),
           "GENG_ROOT": str(graph.root)}
    if node.isolate:
        env["GENG_BRANCH"] = f"geng/{node.id}"

    ok_exit = node.ok_exit if node.ok_exit is not None else agent.ok_exit
    attempts, code, stdout = 0, -1, ""
    while attempts <= node.retries:
        attempts += 1
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                env=env,
                input=prompt if agent.prompt_via == "stdin" else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=node.timeout,
            )
            code, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        except FileNotFoundError:
            code, stdout, stderr = 127, "", f"executable not found: {argv[0]}"
        except subprocess.TimeoutExpired:
            code, stdout, stderr = 124, "", f"timeout after {node.timeout}s"

        (store.log / f"{node.id}.log").write_text(
            f"$ {shlex.join(argv)}\n[cwd] {workdir}\n[exit] {code} (attempt {attempts})\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
            encoding="utf-8",
        )
        if code in ok_exit:
            break
        if attempts <= node.retries:
            time.sleep(min(2 ** attempts, 15))

    # Write-then-record: the artifact exists before the node is marked green.
    store.out_path(node.id).write_text(stdout, encoding="utf-8")
    if node.out:
        dest = (graph.root / node.out).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(stdout, encoding="utf-8")

    ok = code in ok_exit
    extra: dict[str, object] = {}
    if node.isolate and wt_root is not None:
        # Only publish a branch for work that passed. A failed node's half-edit
        # must not look like a reviewable deliverable. Commit from the worktree
        # ROOT, not from `cwd`, so `git add -A` stages everything the node changed.
        extra["branch"] = f"geng/{node.id}"
        extra["worktree"] = str(wt_root)
        extra["commit"] = commit_worktree(wt_root, node.id) if ok else None
    store.record(node.id, status="ok" if ok else "failed", exit=code, attempts=attempts,
                 key=node_key(graph, node), out=str(store.out_path(node.id)),
                 ended=time.strftime("%Y-%m-%dT%H:%M:%S"), **extra)
    return ok, code


def run(graph: Graph, resume: bool, only: set[str] | None, jobs: int, dry: bool) -> int:
    """Execute the graph, holding an exclusive lock so two runs cannot interleave.

    Each process loads state.json into memory once; without a lock, a second
    concurrent run finishing first has its completed-node records silently
    overwritten when the first run writes its own stale copy back.
    """
    if dry:  # a dry run touches no state, so it needs no lock
        return _run(graph, resume, only, jobs, dry)
    lock = graph.root / ".geng" / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SpecError(
            f"another geng run is already in progress against {graph.root}\n"
            f"    if no run is active, this lock is stale: delete {lock}")
    os.write(fd, f"pid {os.getpid()} started {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                 .encode())
    os.close(fd)
    try:
        return _run(graph, resume, only, jobs, dry)
    finally:
        lock.unlink(missing_ok=True)


def _run(graph: Graph, resume: bool, only: set[str] | None, jobs: int, dry: bool) -> int:
    store = Store(graph.root)
    layers = waves(graph)
    jobs = jobs or int(graph.settings.get("max_parallel", 4))
    selected = only or set(graph.nodes)
    if only:
        # Pull in the transitive dependencies of an explicit selection.
        frontier = list(only)
        while frontier:
            nid = frontier.pop()
            if nid not in graph.nodes:
                raise SpecError(f"--only references unknown node {nid!r}")
            for dep in graph.nodes[nid].needs:
                if dep not in selected:
                    selected.add(dep)
                    frontier.append(dep)

    skipped: set[str] = set()
    failed: set[str] = set()

    for depth, layer in enumerate(layers, 1):
        batch = [graph.nodes[n] for n in layer if n in selected]
        if not batch:
            continue
        print(_c(f"\nwave {depth}", CYN) + _c(f"  ({len(batch)} node(s), {jobs} at a time)", DIM))

        runnable = []
        for node in batch:
            blocked = [d for d in node.needs if d in failed or d in skipped]
            if blocked:
                skipped.add(node.id)
                store.record(node.id, status="skipped", blocked_by=blocked)
                print(f"  {_c('skip', YEL)} {node.id} {_c('blocked by ' + ','.join(blocked), DIM)}")
                continue
            prior = store.get(node.id)
            if resume and prior.get("status") == "ok" and prior.get("key") == node_key(graph, node):
                print(f"  {_c('cached', DIM)} {node.id}")
                continue
            runnable.append(node)

        if not runnable:
            continue
        with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {pool.submit(run_node, graph, n, store, dry): n for n in runnable}
            for fut in cf.as_completed(futures):
                node = futures[fut]
                try:
                    ok, code = fut.result()
                except SpecError as exc:
                    ok, code = False, -1
                    # Write the log here too. run_node's own log write lives inside
                    # the subprocess try-block, so an error raised before it (bad
                    # placeholder, worktree failure) would otherwise leave the path
                    # printed below pointing at a file that never existed.
                    (store.log / f"{node.id}.log").write_text(
                        f"[error] {exc}\n", encoding="utf-8")
                    store.record(node.id, status="failed", error=str(exc))
                    print(f"  {_c('ERR ', RED)} {node.id}: {exc}")
                if ok:
                    print(f"  {_c('ok  ', GRN)} {node.id}")
                else:
                    failed.add(node.id)
                    tag = "GATE" if node.gate else "FAIL"
                    print(f"  {_c(tag, RED)} {node.id} exit={code} "
                          f"{_c(str(store.log / (node.id + '.log')), DIM)}")

        if any(graph.nodes[n].gate for n in failed):
            gates = sorted(n for n in failed if graph.nodes[n].gate)
            print(_c(f"\nhalted: gate node(s) failed: {', '.join(gates)}", RED))
            return 1

    total = len(selected)
    print(f"\n{total - len(failed) - len(skipped)}/{total} ok, "
          f"{len(failed)} failed, {len(skipped)} skipped")
    return 1 if (failed or skipped) else 0


def plan(graph: Graph) -> int:
    layers = waves(graph)
    print("```mermaid\nflowchart TD")
    for nid, node in graph.nodes.items():
        shape = f"{nid}{{{{{nid}}}}}" if node.gate else f"{nid}[{nid}]"
        print(f"    {shape}")
        for dep in node.needs:
            print(f"    {dep} --> {nid}")
    print("```\n")
    for i, layer in enumerate(layers, 1):
        for nid in layer:
            n = graph.nodes[nid]
            flags = ",".join(f for f, on in
                             (("gate", n.gate), ("isolate", n.isolate), (f"retries={n.retries}", n.retries))
                             if on)
            print(f"wave {i}  {nid:<20} agent={n.agent:<12} {flags}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="geng", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "plan"):
        p = sub.add_parser(name)
        p.add_argument("spec", type=Path)
        if name == "run":
            p.add_argument("--resume", action="store_true",
                           help="skip nodes already green with an unchanged key")
            p.add_argument("--only", default="", help="comma-separated node ids (deps pulled in)")
            p.add_argument("--jobs", type=int, default=0, help="max concurrent nodes")
            p.add_argument("--dry-run", action="store_true", help="print argv per node, execute nothing")
    args = ap.parse_args(argv)

    try:
        graph = load_graph(args.spec)
        if args.cmd == "plan":
            return plan(graph)
        only = {s.strip() for s in args.only.split(",") if s.strip()} or None
        return run(graph, args.resume, only, args.jobs, args.dry_run)
    except SpecError as exc:
        print(_c(f"spec error: {exc}", RED), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(_c("\ninterrupted; rerun with --resume to continue", YEL), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
