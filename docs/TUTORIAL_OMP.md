# Tutorial: geng with Oh My Pi (`omp`)

You have `omp` installed and you want it doing real work in a graph. This
tutorial gets you there in four steps, each one running before the next is
introduced.

**Prerequisite:** finish [TUTORIAL.md](TUTORIAL.md) first. It teaches the spec
itself using Python as a stand-in agent, so you learn the machinery without
spending tokens. This file only covers what is specific to `omp`.

- [The two adapters you need](#the-two-adapters-you-need)
- [Step 1: one omp node](#step-1-one-omp-node)
- [Step 2: read, then write](#step-2-read-then-write)
- [Step 3: add a gate that can't be argued with](#step-3-add-a-gate-that-cant-be-argued-with)
- [Step 4: isolate the writer](#step-4-isolate-the-writer)
- [omp specifics worth knowing](#omp-specifics-worth-knowing)

---

## The two adapters you need

Almost every graph you write will use these two, so understand them before
anything else:

```toml
# Can edit files. Use for implementation nodes.
[agents.omp]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

# CANNOT edit files. Use for planning, auditing and review nodes.
[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]
```

Flag by flag:

| Flag | Why it's there |
| --- | --- |
| `-p` | Print mode: process the prompt, print the answer, exit. Without it, `omp` opens an interactive session and your graph hangs forever. |
| `--no-session` | Don't save a session file. Each node is a fresh, independent run — which is the point of a graph. |
| `--auto-approve` | Approve tool calls without asking. Required for unattended writing, because nobody is at the keyboard to say yes. |
| `--approval-mode always-ask` | The read-only boundary. Every write needs approval, and in print mode nobody can grant it, so writes cannot happen. |

**The `_ro` adapter is the important one, and it is a real boundary, not a
politely-worded prompt.** I verified this by trying to break it: a node told
"overwrite this file" with `--approval-mode always-ask` left the file untouched
and replied `NOWRITE`. The same prompt with `--auto-approve` destroyed the file
immediately.

> **A trap worth knowing.** `--tools "read,grep,glob"` looks like it should
> restrict capability, and it does **not** — I tested it, and the agent wrote the
> file anyway. Use `--approval-mode always-ask` for read-only nodes. Do not rely
> on `--tools` as a safety boundary.

Why does this matter so much? Because a reviewer that *can* edit will usually
just fix what it finds and report success — and you learn nothing about the
defect. A reviewer that *cannot* edit has no choice but to tell you.

---

## Step 1: one omp node

Make a directory with `geng.py` in it and a file to work on:

```console
$ mkdir demo && cd demo
$ curl -O https://raw.githubusercontent.com/AdityaIndoori/graph-engineering/main/geng.py
```

Create `src/calc.py`:

```python
def divide(a, b):
    return a / b
```

Create `audit.toml`:

```toml
[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[nodes.audit]
out    = "build/audit.md"
prompt = "Audit src/calc.py for unhandled edge cases. Cite file:line for each finding. Do not edit anything."
```

There's no `agent =` line and no `default_agent`, because with exactly one agent
defined there is no ambiguity — geng uses it.

**Before spending a token, check what will actually run:**

```console
$ python geng.py run audit.toml --dry-run
```

```
wave 1  (1 node(s), 4 at a time)
    cwd  C:\...\demo
    argv omp -p --no-session --approval-mode always-ask 'Audit src/calc.py for unhandled edge cases...'
  ok   audit
```

Notice the prompt became the last argument. Our `argv` had no `{prompt}`
placeholder, so geng appended it — which is exactly how `omp` expects it.

Now run it for real:

```console
$ python geng.py run audit.toml
$ cat build/audit.md
```

You get omp's findings. Note that only the answer landed in the file: `omp`
prints its progress to stderr and only the final answer to stdout, so the edge
payload is clean. That matters in the next step.

---

## Step 2: read, then write

Now use the audit's output to drive a second node that *writes*. Create
`tests.toml`:

```toml
[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[agents.omp]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[nodes.audit]
agent  = "omp_ro"
prompt = "Audit src/calc.py for unhandled edge cases. Cite file:line for each. Do not edit anything."

[nodes.write_tests]
needs  = ["audit"]
agent  = "omp"
prompt = """
Write pytest tests for src/calc.py covering the findings below.
Create tests/test_calc.py only. Do not modify src/calc.py.

## findings
{audit}
"""
```

Two agents are now defined, so every node must say which one it uses — the
ambiguity is real, so geng makes you resolve it.

```console
$ python geng.py run tests.toml
```

```
wave 1  (1 node(s), 4 at a time)
  ok   audit

wave 2  (1 node(s), 4 at a time)
  ok   write_tests
```

`{audit}` was replaced by the first node's entire output. This is the pattern
that makes graphs worth the trouble: **the reader had no power to change
anything, and the writer worked from what the reader found.** Two clean context
windows, and a division of authority you can point at.

---

## Step 3: add a gate that can't be argued with

`write_tests` reported success. Does that mean the tests pass? No — it means omp
believed it was finished. Those are different claims, and only one of them is
checkable.

Add a third node. The cleanest way to run a fixed command is to invoke it
directly and tell geng there is no prompt to pass:

```toml
[agents.pytest]
argv       = ["{python}", "-m", "pytest", "-q", "tests/test_calc.py"]
prompt_via = "none"

[nodes.verify]
needs  = ["write_tests"]
agent  = "pytest"
gate   = true
prompt = "the AI-written tests must actually pass"
```

Two details that will bite you if you skip them:

**`prompt_via = "none"`.** By default geng appends the prompt as the final
argument, which is right for agent CLIs and wrong here — `pytest` would read
`"the AI-written tests must actually pass"` as a file path and exit 4 with a usage
error. With `prompt_via = "none"` the prompt is never passed, and it serves as
documentation of what the gate checks.

**Avoid `["bash", "-lc", ...]` on Windows.** It looks portable and is not: on a
machine with WSL installed but no distro, `bash` resolves to a stub that fails
with `execvpe(/bin/bash) failed`. Your gate then fails for a reason unrelated to
your tests. Invoke the tool directly, as above, and `{python}` keeps it working
on every OS.

```console
$ python geng.py run tests.toml
```

If the generated tests fail:

```
wave 3  (1 node(s), 4 at a time)
  GATE verify exit=1 .geng\log\verify.log

halted: gate node(s) failed: verify
```

The run stops and `geng` itself exits non-zero, so a CI job or shell script
wrapping it fails too. Read `.geng/log/verify.log` for pytest's actual output —
it records the exact command, the cwd, the exit code, stdout and stderr.

**This node is not an AI agent, and that is the entire point.** `pytest` returns
0 or 1. It has no opinion about whether it did a good job, cannot be persuaded,
and cannot decide the failure is unimportant. Every graph that produces code
should end in one of these.

---

## Step 4: isolate the writer

While a writing node runs, it's editing your working directory — so you can't
safely touch the repo, and two writing nodes would trample each other.

Add one line to the writer:

```toml
[nodes.write_tests]
needs   = ["audit"]
agent   = "omp"
isolate = true
prompt  = "..."
```

Requires a git repo. Now omp works in `.geng/wt/write_tests/`, a separate
checkout on branch `geng/write_tests`. Your files are untouched. If the node
succeeds, its work is committed to that branch; **if it fails, nothing is
committed**, so a half-finished edit can never look reviewable.

The gate must then test the worktree, not your unchanged directory:

```toml
[nodes.verify]
needs  = ["write_tests"]
agent  = "shell"
gate   = true
prompt = "cd {implement_worktree} && python -m pytest -q"
```

Wait — that placeholder names the wrong node. It must match a node in `needs`:

```toml
prompt = "cd {write_tests_worktree} && python -m pytest -q"
```

geng would have caught that mistake for you:

```
ERR  verify: prompt references unknown placeholder {implement_worktree}
```

That strictness exists so a broken graph fails loudly instead of quietly testing
the wrong directory and reporting success.

When you're satisfied: `git merge geng/write_tests`.

To review the diff first, add a read-only reviewer:

```toml
[nodes.review]
needs  = ["verify", "write_tests"]
agent  = "omp_ro"
out    = "build/review.md"
prompt = "Run `git diff main...{write_tests_branch}` and review it. Report only defects you can cite a line for."
```

It lists `write_tests` in `needs` purely to bring `{write_tests_branch}` into
scope. And because it uses `omp_ro`, it cannot quietly fix what it finds.

---

## omp specifics worth knowing

**Pick a different model per node.** `--model` is per-invocation, so a node can
use a cheap model and another a strong one:

```toml
[agents.omp_fast]
argv = ["omp", "-p", "--no-session", "--auto-approve", "--model", "smol"]

[agents.omp_deep]
argv = ["omp", "-p", "--no-session", "--auto-approve", "--thinking", "high"]
```

Use the cheap one for mechanical edits and the expensive one for the node that
needs judgement. This is the cost lever that a single loop cannot give you.

**A second opinion from a different vendor.** Agents on the same model reading the
same context share blind spots and will agree with each other. If you have
another CLI, point one review node at it:

```toml
[agents.codex_ro]
argv = ["codex", "exec", "--sandbox", "read-only", "--ask-for-approval", "never"]
```

**Set a wall-clock ceiling.** `--max-time 600` inside `argv`, or geng's own
`timeout = 600` on the node. The node-level one is preferable: geng records it as
exit 124 so you can tell a timeout from a failure.

**Keep nodes stateless.** `--no-session` on every adapter. A node that resumes a
previous session inherits context the graph doesn't know about, which defeats the
clean-window property the whole design depends on.

**Cap concurrency to cap spend.** `max_parallel` under `[settings]` is how many
agents run at once. Three parallel omp nodes cost three times as much per wave.

---

## Next

- Have omp write the spec **for** you: [TUTORIAL_AI_WRITES_IT.md](TUTORIAL_AI_WRITES_IT.md)
- The same patterns with Claude Code: [TUTORIAL_CLAUDE.md](TUTORIAL_CLAUDE.md)
- Every option: [spec reference](../README.md#spec-reference)
- And the honest caveat: most tasks don't need a graph at all —
  [when not to use this](../README.md#when-not-to-use-this)
