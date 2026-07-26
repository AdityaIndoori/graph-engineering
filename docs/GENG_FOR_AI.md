# geng: complete reference for AI agents

Feed this single file to a coding agent and it can write correct geng specs.
Everything an agent needs is here: the model, the full schema, the rules, and
worked examples. No other file is required.

> If you are a human, read `TUTORIAL.md` instead. This file is deliberately
> dense and exists to be pasted into an agent's context.

---

## 1. What geng is

`geng.py` is a single-file, dependency-free Python task runner (Python 3.11+) for
AI coding agents. You describe a directed graph of steps in TOML; geng executes
it.

The three-line model:

- **A node is a subprocess.** One node = one invocation of a command line.
- **An edge is a file.** A node's stdout is captured and substituted into the
  prompts of nodes that declared a dependency on it.
- **The exit code is the contract.** Exit `0` means success. Non-zero means
  failure. geng never parses stdout to judge success, because most agent CLIs
  emit no reliable machine-readable output when they fail.

Commands:

```
python geng.py run   <spec.toml>              execute
python geng.py run   <spec.toml> --resume     skip nodes already green
python geng.py run   <spec.toml> --only ID    run ID plus its dependencies
python geng.py run   <spec.toml> --dry-run    print argv per node, execute nothing
python geng.py plan  <spec.toml>              mermaid diagram + wave preview
```

Exit codes from geng itself: `0` all selected nodes succeeded, `1` something
failed or was skipped, `2` the spec is invalid, `130` interrupted.

Execution order: geng computes **waves** by Kahn layering. All nodes whose
dependencies are already satisfied run concurrently in one wave, capped by
`max_parallel`. Waves execute in sequence.

Run state is written to `.geng/` next to the spec file:

```
.geng/state.json      per node: status, exit code, attempts, content key
.geng/out/<id>.txt    the node's captured stdout (the edge payload)
.geng/log/<id>.log    argv, cwd, exit code, stdout, stderr
.geng/wt/<id>/        git worktree, for isolate = true nodes
.geng/.lock           held during a run; a second concurrent run is refused
```

---

## 2. Complete schema

```toml
[settings]                      # optional table
default_agent = "dev"           # agent used by nodes that don't name one
max_parallel  = 4               # max concurrent nodes per wave (default 4)
base_ref      = "HEAD"          # git ref that isolate = true branches from

[agents.<name>]                 # at least one required
argv       = ["claude", "-p"]   # REQUIRED: command as a list of words
prompt_via = "arg"              # "arg" (default), "stdin", or "none"
ok_exit    = [0]                # exit codes counted as success (default [0])
env        = { KEY = "value" }  # extra environment variables

[nodes.<id>]                    # at least one required; id matches [A-Za-z_][A-Za-z0-9_-]*
prompt  = "text {placeholders}" # REQUIRED, non-empty
agent   = "dev"                 # optional if settings.default_agent is set,
                                #   or if exactly one agent is defined
needs   = ["other_id"]          # dependencies = the graph's edges
out     = "build/result.md"     # also copy stdout to this path
gate    = true                  # if this node fails, halt the whole run
isolate = true                  # run in its own git worktree + branch
cwd     = "packages/api"        # run in this subdirectory
retries = 2                     # retry on failure; backoff 2^n sec, capped at 15
timeout = 900                   # seconds before the node is killed (exit 124)
ok_exit = [0]                   # overrides the agent's ok_exit
env     = { KEY = "value" }     # merged over the agent's env
```

### argv placeholders

| Placeholder | Becomes |
| --- | --- |
| `{prompt}` | the node's rendered prompt |
| `{python}` | the Python interpreter running geng (`sys.executable`) |

If `argv` contains no `{prompt}` and `prompt_via = "arg"`, the prompt is
**appended as the final argument**. This is what most agent CLIs expect.

If `prompt_via = "stdin"`, the prompt is piped to the process's stdin instead.

If `prompt_via = "none"`, the prompt is never passed to the process at all. Use
this for a fixed command such as a test runner: the node's `prompt` then serves as
documentation of what the command checks. Without it, geng appends the prompt as a
final argument and `pytest -q "run the tests"` fails with a usage error.

### prompt placeholders

Available only for nodes listed in that node's own `needs`:

| Placeholder | Becomes |
| --- | --- |
| `{dep}` | the dependency's captured stdout, stripped |
| `{dep_path}` | absolute path to `.geng/out/dep.txt` |
| `{dep_branch}` | `geng/dep` — only if that dep has `isolate = true` |
| `{dep_worktree}` | path to the dep's worktree — only if `isolate = true` |
| `{dep_commit}` | the dep's commit hash, or empty if it changed nothing |
| `{graph_root}` | directory containing the spec file |

Nodes also receive `GENG_NODE`, `GENG_OUT`, `GENG_ROOT` in their environment, and
`GENG_BRANCH` when isolated.

---

## 3. Hard rules — violating these produces an error or a broken graph

1. **A prompt may only reference nodes in its own `needs`.** An undeclared
   `{placeholder}` is a hard error (`prompt references unknown placeholder`), not
   an empty string. If a prompt needs a value, declare the dependency.
2. **Literal braces in a prompt must be doubled.** Python f-strings, JS template
   literals and Rust `format!` all use `{}`. Write `{{i + 1}}` to emit `{i + 1}`.
   A single unmatched brace is an error.
3. **No dependency cycles.** Rejected before anything executes.
4. **`argv` is a list, never a string.** No shell is involved, so no quoting or
   escaping is needed. `["bash", "-lc", "{prompt}"]` if you genuinely want a shell.
5. **`isolate = true` requires a git repository** and deletes any pre-existing
   `geng/<id>` branch.
6. **One file, one owner.** Never let two nodes edit the same file. Worktrees
   prevent concurrent corruption but not incompatible design decisions.
7. **`agent` may be omitted only** when `settings.default_agent` is set or
   exactly one agent is defined.
8. **A gate node halts the entire run**, not just its dependents.

---

## 4. Agent adapters, verbatim

```toml
[agents.claude]       # Claude Code
argv = ["claude", "-p", "--permission-mode", "acceptEdits"]

[agents.claude_ro]    # read-only: cannot edit, by capability
argv = ["claude", "-p", "--allowedTools", "Read,Grep,Glob"]

[agents.omp]          # Oh My Pi
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.omp_ro]       # read-only: approval required, nothing auto-approved
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[agents.codex]        # OpenAI Codex
argv = ["codex", "exec", "--sandbox", "workspace-write", "--ask-for-approval", "never"]

[agents.codex_ro]
argv = ["codex", "exec", "--sandbox", "read-only", "--ask-for-approval", "never"]

[agents.opencode]
argv = ["opencode", "run", "--auto"]

[agents.kiro]
argv = ["kiro-cli", "chat", "--no-interactive", "--trust-tools=read,grep"]

[agents.gemini]
argv = ["gemini", "-p"]

[agents.cursor]       # without -f it proposes edits but never applies them
argv = ["cursor-agent", "-p", "-f"]

[agents.amp]
argv = ["amp", "-x"]

[agents.aider]
argv = ["aider", "--yes-always", "--message"]

# Non-agent nodes. These are the most valuable nodes in most graphs: they make
# an agent's claim falsifiable.
[agents.shell]
argv = ["bash", "-lc", "{prompt}"]

[agents.pwsh]
argv = ["powershell", "-NoProfile", "-Command", "{prompt}"]

[agents.py]
argv = ["{python}", "-c", "{prompt}"]

# A fixed test command. prompt_via = "none" stops geng appending the prompt,
# which would otherwise be read as an extra file path.
[agents.pytest]
argv       = ["{python}", "-m", "pytest", "-q"]
prompt_via = "none"
```

---

## 5. Design patterns to apply when generating a spec

**Put verification in a non-agent node.** An agent asked "is this good?" is
guessing and is motivated to say yes. `npm test` returns 0 or 1 and has no
opinion. Any graph that produces code should end in a `gate` node running the
project's real test or build command.

**Restrict capability per node, not by instruction.** A reviewer that *can* edit
will often silently fix what it finds and report success, teaching you nothing.
Give review nodes a `_ro` adapter. "Do not edit files" in a prompt is not a
boundary; a missing tool is.

**Parallelise reading and verifying; serialise writing and deciding.** Multiple
read-only investigators are safe and useful. Multiple concurrent writers cause
incompatible decisions. Prefer many readers feeding one writer.

**Reproduce before theorising.** For a bug hunt, the first node should be a gate
that proves the bug exists. If it will not reproduce, stop.

**Use a different model for review.** Agents on the same model reading the same
context share blind spots and will agree with each other. A second adapter
pointed at another vendor is the cheapest defence against correlated error.

**Prefer many small nodes to one big one.** Resume granularity equals node size,
so a failure costs one node rather than the whole run.

---

## 6. Worked examples

### Sequential with a gate

```toml
[settings]
default_agent = "dev"

[agents.dev]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.shell]
argv = ["bash", "-lc", "{prompt}"]

[nodes.implement]
prompt = "Add a --verbose flag to src/cli.py. Update the argument parser and the help text."

[nodes.verify]
needs  = ["implement"]
agent  = "shell"
gate   = true
prompt = "python -m pytest -q"
```

### Fan-out, fan-in

```toml
[settings]
default_agent = "reader"
max_parallel  = 3

[agents.reader]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[nodes.security]
prompt = "Audit src/auth.py for security issues. Cite file:line for each finding."

[nodes.perf]
prompt = "Audit src/query.py for N+1 queries. Cite file:line for each finding."

[nodes.report]
needs  = ["security", "perf"]
out    = "build/audit.md"
prompt = """
Merge these two audits into one markdown report, de-duplicated, most severe first.

## security
{security}

## performance
{perf}
"""
```

### Isolated parallel writers, one barrier

```toml
[settings]
default_agent = "dev"
max_parallel  = 3
base_ref      = "main"

[agents.dev]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.shell]
argv = ["bash", "-lc", "{prompt}"]

[nodes.api]
isolate = true
cwd     = "packages/api"
prompt  = "Replace every logger.log call with ctx.log in this package only."

[nodes.web]
isolate = true
cwd     = "packages/web"
prompt  = "Replace every logger.log call with ctx.log in this package only."

[nodes.check]
needs  = ["api", "web"]
agent  = "shell"
gate   = true
prompt = "pnpm -r typecheck && pnpm -r test"

[nodes.merge_note]
needs  = ["check", "api", "web"]
agent  = "shell"
prompt = "echo 'merge with: git merge {api_branch} {web_branch}'"
```

### Implement, verify, then two independent reviewers

```toml
[settings]
base_ref = "main"

[agents.dev]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[agents.codex_ro]
argv = ["codex", "exec", "--sandbox", "read-only", "--ask-for-approval", "never"]

[agents.shell]
argv = ["bash", "-lc", "{prompt}"]

[nodes.plan]
agent  = "omp_ro"
out    = "build/plan.md"
prompt = "Plan how to add request-id logging to the HTTP layer. List files and the change per file. Do not edit anything."

[nodes.implement]
needs   = ["plan"]
agent   = "dev"
isolate = true
retries = 1
prompt  = "Implement exactly this plan, nothing more:\n\n{plan}"

[nodes.verify]
needs  = ["implement"]
agent  = "shell"
gate   = true
prompt = "cd {implement_worktree} && npm test"

[nodes.review_a]
needs  = ["verify", "implement"]
agent  = "omp_ro"
out    = "build/review-a.md"
prompt = "Run `git diff main...{implement_branch}` and review it. Report only defects you can cite a line for."

[nodes.review_b]
needs  = ["verify", "implement"]
agent  = "codex_ro"
out    = "build/review-b.md"
prompt = "Run `git diff main...{implement_branch}` and review it. Report only defects you can cite a line for."
```

---

## 7. Checklist before returning a generated spec

- [ ] Every `{placeholder}` refers to a node named in that node's `needs`.
- [ ] Literal braces intended for the target language are doubled.
- [ ] Any node producing code is followed by a `gate` node running a real test,
      build or typecheck command — not an agent's opinion.
- [ ] Review and planning nodes use a read-only adapter.
- [ ] No two nodes write the same file; parallel writers use `isolate = true`.
- [ ] `argv` is a list of separate words, not one string.
- [ ] Every referenced agent is defined in `[agents.*]`.
- [ ] No cycles.
- [ ] Validate with `python geng.py plan spec.toml`, then inspect the exact
      commands with `python geng.py run spec.toml --dry-run` before a real run.
