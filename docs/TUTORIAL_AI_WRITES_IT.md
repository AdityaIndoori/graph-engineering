# Tutorial: let the AI write the graph for you

Writing TOML by hand is fine once you know the format. But the fastest path is to
describe what you want in plain English and have an agent produce the spec — then
validate it before it runs.

This tutorial answers the question directly: **yes, the agent needs one context
file.** It is [`GENG_FOR_AI.md`](GENG_FOR_AI.md), it ships with this repo, and
this tutorial shows exactly how to use it.

- [Why a context file is required](#why-a-context-file-is-required)
- [Step 1: put the context file where the agent can read it](#step-1-put-the-context-file-where-the-agent-can-read-it)
- [Step 2: ask in plain English](#step-2-ask-in-plain-english)
- [Step 3: validate before you run](#step-3-validate-before-you-run)
- [Step 4: run it](#step-4-run-it)
- [Making this permanent](#making-this-permanent)
- [What to check in a generated spec](#what-to-check-in-a-generated-spec)

---

## Why a context file is required

I tested this before writing anything. With no context provided, I asked an agent
to write a geng spec. Its answer, verbatim and correct:

> Honest answer: no. I don't have reliable knowledge of a tool called "geng" that
> uses a TOML spec file. The name collides with several unrelated things I do know
> of (e.g. `geng`, a Rust game engine crate by kuviman...) I have no grounded
> knowledge of a documented "geng TOML spec file" schema — its keys, sections, or
> semantics — so writing one would be fabrication.

That is the right answer, and it explains the problem. `geng` is new and small, so
it is not in any model's training data. Worse, the *name collides* with an
established Rust crate — so an agent that guesses confidently will produce
plausible nonsense.

The fix is not a better prompt. It is giving the agent the actual schema.
`GENG_FOR_AI.md` is written for exactly this: the model, the complete schema, the
hard rules, every adapter, the design patterns, and a self-check list. One file,
nothing else needed.

---

## Step 1: put the context file where the agent can read it

```console
$ curl -O https://raw.githubusercontent.com/AdityaIndoori/graph-engineering/main/geng.py
$ curl -O https://raw.githubusercontent.com/AdityaIndoori/graph-engineering/main/docs/GENG_FOR_AI.md
```

Two files in your project directory. That is the entire setup.

---

## Step 2: ask in plain English

The prompt has three parts: **read the context file, describe the goal, demand
only TOML back.**

With `omp`:

```console
$ omp -p --no-session --approval-mode always-ask "Read GENG_FOR_AI.md. Then write a geng TOML spec that audits src/calc.py for missing edge cases using a read-only agent, writes pytest tests for the findings using a writing agent, then runs pytest as a gate. Output ONLY the TOML."
```

With Claude Code:

```console
$ claude -p "Read GENG_FOR_AI.md. Then write a geng TOML spec that audits src/calc.py for missing edge cases using a read-only agent, writes pytest tests for the findings using a writing agent, then runs pytest as a gate. Output ONLY the TOML." --allowedTools "Read,Grep,Glob"
```

Note the read-only adapter on both. You are asking for text, not for changes to
your project, so give the agent no ability to make them.

**Here is what omp actually returned when I ran that** (abridged; this is real
output, not an illustration):

```toml
[settings]
max_parallel = 1

[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[agents.omp]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.shell]
argv = ["bash", "-lc", "{prompt}"]

[nodes.audit]
agent  = "omp_ro"
prompt = "Audit src/calc.py for missing edge-case handling: division by zero, None/empty inputs, non-numeric types, overflow, negative and boundary values, float precision. Do not edit any file. Output a markdown list; cite file:line for each finding."

[nodes.write_tests]
needs  = ["audit"]
agent  = "omp"
prompt = """
Write pytest tests covering the edge cases below for src/calc.py.
- Create or extend tests/test_calc.py only. Do not modify src/calc.py.
- One test per finding, named after the edge case.

## audit findings
{audit}
"""

[nodes.verify]
needs  = ["write_tests"]
agent  = "shell"
gate   = true
prompt = "python -m pytest -q"
```

It got the important things right without being told: the read-only adapter for
the auditing node, `{audit}` correctly declared in `needs`, and a gate that runs
real tests rather than asking an agent for its opinion. That is the context file
doing its job.

---

## Step 3: validate before you run

**Never run a generated spec without checking it first.** Two commands, in this
order.

First, does it parse, and is the shape what you asked for?

```console
$ python geng.py plan generated.toml
```

```
wave 1  audit                agent=omp_ro
wave 2  write_tests          agent=omp
wave 3  verify               agent=shell        gate
```

That output answers three questions at once: the TOML is valid, the dependencies
form the order you intended, and the `gate` flag landed on the right node. A cycle
or an unknown dependency would have failed here with exit code 2, before anything
executed.

Second, what commands will actually run?

```console
$ python geng.py run generated.toml --dry-run
```

This prints the exact argv for every node and executes nothing — no files written,
no state, no worktrees, no tokens spent. Read those lines. This is your last
checkpoint before an agent touches your repository, and it is where you catch a
wrong flag or a prompt that says more than you meant.

**One real correction to make.** The generated spec above used
`["bash", "-lc", "{prompt}"]` for its gate. On Windows with WSL installed but no
distro, `bash` resolves to a stub that fails with `execvpe(/bin/bash) failed` — so
your gate fails for a reason unrelated to your tests. Prefer invoking the tool
directly:

```toml
[agents.pytest]
argv       = ["{python}", "-m", "pytest", "-q", "tests/test_calc.py"]
prompt_via = "none"
```

`prompt_via = "none"` stops geng appending the prompt as a final argument, which
`pytest` would read as a file path. `{python}` resolves to the interpreter running
geng, so it works on machines where the command is `python3`.

---

## Step 4: run it

```console
$ python geng.py run generated.toml
```

```
wave 1  (1 node(s), 4 at a time)
  ok   audit

wave 2  (1 node(s), 4 at a time)
  ok   write_tests

wave 3  (1 node(s), 4 at a time)
  ok   verify

3/3 ok, 0 failed, 0 skipped
```

That is the real result of the graph above: omp audited the code with no ability
to change it, a second omp invocation wrote 15 pytest tests from that audit, and
the gate ran them and they passed. The gate is what makes the last sentence a fact
rather than a claim.

If a node fails, `.geng/log/<node>.log` has the exact command, cwd, exit code,
stdout and stderr. Fix the cause and re-run with `--resume`: completed nodes are
cached, so only the failed node and its dependents re-execute.

---

## Making this permanent

Two ways to avoid pasting the same instruction every time.

**Point your agent's project rules at the file.** Add a line to your `AGENTS.md`,
`CLAUDE.md`, or omp rules file:

```markdown
When asked to write or modify a geng spec (`*.toml` run by `geng.py`), first read
`docs/GENG_FOR_AI.md` for the schema and rules. Never guess the format.
```

Now "add a review node to release.toml" works with no preamble.

**Or make the graph write graphs.** A geng node can generate a spec, and another
can validate it:

```toml
[agents.omp_ro]
argv = ["omp", "-p", "--no-session", "--approval-mode", "always-ask"]

[agents.omp]
argv = ["omp", "-p", "--no-session", "--auto-approve"]

[agents.check]
argv       = ["{python}", "geng.py", "plan", "generated.toml"]
prompt_via = "none"

[nodes.design]
agent  = "omp_ro"
out    = "build/spec-draft.md"
prompt = "Read GENG_FOR_AI.md. Design a geng graph that lints, tests and builds this project. Explain the node choices. Do not write files."

[nodes.emit]
needs  = ["design"]
agent  = "omp"
prompt = "Write this design to generated.toml as valid geng TOML, nothing else:\n\n{design}"

[nodes.validate]
needs  = ["emit"]
agent  = "check"
gate   = true
prompt = "the generated spec must parse and have no cycles"
```

The last node is the interesting one: **`geng.py plan` is itself the gate.** If
the AI produced invalid TOML or a cycle, it exits 2 and the run halts. The tool
verifies its own generated input, with no agent asked for an opinion.

---

## What to check in a generated spec

The agent has a self-check list at the end of `GENG_FOR_AI.md`. These are the
items worth re-checking yourself, because they are where a wrong spec does damage
rather than just failing:

- **Is there a real gate?** Any graph producing code should end in a node running
  your actual test, build or typecheck command. If the last node is an agent
  saying "looks good", the graph proves nothing.
- **Are review and planning nodes read-only?** A reviewer with write access
  quietly fixes what it finds and reports success. Check for `_ro` adapters.
- **Could two nodes edit the same file?** If so, they need `isolate = true`, or
  they must be sequential. One file, one owner.
- **Does every `{placeholder}` name a node in that node's `needs`?** geng enforces
  this, so a mistake here fails loudly rather than passing an empty string.
- **Do the prompts say more than you meant?** An agent writing prompts for other
  agents can be expansive. "Do not expand scope" is worth keeping.
- **Is `max_parallel` sane for your budget?** Every concurrent node is concurrent
  spend.

And the standing caveat: most tasks do not need a graph. If the work is one
linear job that fits in one context window and you are the one reviewing it, a
single agent in a loop is simpler and cheaper. See
[when not to use this](../README.md#when-not-to-use-this).

---

## Next

- [TUTORIAL.md](TUTORIAL.md) — write a graph by hand, so you can read what the AI gives you
- [TUTORIAL_OMP.md](TUTORIAL_OMP.md) · [TUTORIAL_CLAUDE.md](TUTORIAL_CLAUDE.md) — the two harness walkthroughs
- [GENG_FOR_AI.md](GENG_FOR_AI.md) — the context file itself; worth reading once yourself
