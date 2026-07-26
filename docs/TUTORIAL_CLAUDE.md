# Tutorial: geng with Claude Code

Same graph, different agent. If you have already read
[TUTORIAL_OMP.md](TUTORIAL_OMP.md), the only thing that changes is the
`[agents.*]` block — which is the whole portability claim, demonstrated.

**Prerequisite:** finish [TUTORIAL.md](TUTORIAL.md) first. It teaches the spec
itself using Python as a stand-in agent, so you learn the machinery without
spending tokens.

> **Provenance, stated plainly.** The flags below come from Anthropic's headless
> documentation (`code.claude.com/docs/en/headless` and `/cli-reference`). Unlike
> the omp tutorial, I could not execute them end to end while writing this — the
> Claude API was unreachable from my machine. The `geng` side is fully verified;
> treat the exact Claude flag behaviour as doc-verified, and confirm with
> `--dry-run` before your first real run.

- [The two adapters you need](#the-two-adapters-you-need)
- [Step 1: one Claude node](#step-1-one-claude-node)
- [Step 2: plan, then implement](#step-2-plan-then-implement)
- [Step 3: the gate](#step-3-the-gate)
- [Step 4: isolate the writer and review the diff](#step-4-isolate-the-writer-and-review-the-diff)
- [Claude specifics worth knowing](#claude-specifics-worth-knowing)

---

## The two adapters you need

```toml
# Can edit files. Use for implementation nodes.
[agents.claude]
argv = ["claude", "-p", "--permission-mode", "acceptEdits"]

# CANNOT edit files. Use for planning, auditing and review nodes.
[agents.claude_ro]
argv = ["claude", "-p", "--allowedTools", "Read,Grep,Glob,Bash(git diff:*)"]
```

Flag by flag:

| Flag | Why it's there |
| --- | --- |
| `-p` | Print mode: process the prompt, print the result, exit. Without it Claude opens an interactive session and your graph hangs. |
| `--permission-mode acceptEdits` | Accept file edits without prompting. Required for unattended writing, because nobody is at the keyboard. |
| `--allowedTools "Read,Grep,Glob"` | An allowlist. Tools not named here do not exist for that invocation, so the node **cannot** write. |
| `Bash(git diff:*)` | Grants exactly one shell command pattern — enough to inspect a diff, not enough to change anything. |

**The read-only adapter is a capability boundary, not a request.** This matters
more than it sounds: a reviewer that *can* edit will usually fix what it finds and
report success, so you learn nothing about the defect. A reviewer that *cannot*
edit has no choice but to tell you.

One extra flag worth adding in CI:

```toml
[agents.claude_ci]
argv = ["claude", "-p", "--bare", "--permission-mode", "acceptEdits"]
```

`--bare` skips hooks, skills, plugins, MCP servers and `CLAUDE.md`. Anthropic
recommends it for CI, and the reason is reproducibility: without it, a teammate's
`~/.claude` hook or a project `.mcp.json` silently changes your results.

---

## Step 1: one Claude node

```console
$ mkdir demo && cd demo
$ curl -O https://raw.githubusercontent.com/AdityaIndoori/graph-engineering/main/geng.py
```

Create `audit.toml`:

```toml
[agents.claude_ro]
argv = ["claude", "-p", "--allowedTools", "Read,Grep,Glob"]

[nodes.audit]
out    = "build/audit.md"
prompt = "Audit src/ for unhandled error paths. Cite file:line for each finding. Do not edit anything."
```

No `agent =` line is needed: with exactly one agent defined there is no
ambiguity, so geng uses it.

**Check the command before spending a token:**

```console
$ python geng.py run audit.toml --dry-run
```

```
wave 1  (1 node(s), 4 at a time)
    cwd  C:\...\demo
    argv claude -p --allowedTools Read,Grep,Glob 'Audit src/ for unhandled error paths...'
  ok   audit
```

The prompt became the final argument, because our `argv` contained no `{prompt}`
placeholder — which is exactly what `claude -p` expects. Confirm that line looks
like something you would happily type yourself, then:

```console
$ python geng.py run audit.toml
$ cat build/audit.md
```

---

## Step 2: plan, then implement

Split thinking from doing, so the thinking cannot quietly become doing:

```toml
[agents.claude_ro]
argv = ["claude", "-p", "--allowedTools", "Read,Grep,Glob"]

[agents.claude]
argv = ["claude", "-p", "--permission-mode", "acceptEdits"]

[nodes.plan]
agent  = "claude_ro"
out    = "build/plan.md"
prompt = "Plan how to add request-id logging to the HTTP layer. List the files to touch and the change per file. Do not edit anything."

[nodes.implement]
needs  = ["plan"]
agent  = "claude"
prompt = "Implement exactly this plan. Do not expand scope.\n\n{plan}"
```

Two agents are defined now, so each node must name one — the ambiguity is real,
so geng makes you resolve it rather than guessing.

```console
$ python geng.py run feature.toml
```

`{plan}` was replaced by the planning node's full output. The planner had no
capability to edit, so what you have is a reviewable plan and a separate,
auditable act of implementation.

---

## Step 3: the gate

`implement` reported success. That means Claude believed it was finished — not
that the code works. Only one of those is checkable, so check it:

```toml
[agents.tests]
argv       = ["npm", "test"]
prompt_via = "none"

[nodes.verify]
needs  = ["implement"]
agent  = "tests"
gate   = true
prompt = "the suite must pass before anything is reviewed or merged"
```

`prompt_via = "none"` is important. By default geng appends the prompt as the
final argument — correct for agent CLIs, wrong here, because `npm test "the suite
must pass..."` would be a usage error. With `"none"` the prompt is never passed
and simply documents what the gate enforces.

```
wave 3  (1 node(s), 4 at a time)
  GATE verify exit=1 .geng\log\verify.log

halted: gate node(s) failed: verify
```

`geng` itself then exits non-zero, so a CI job wrapping it fails too.

**This node is not an AI agent, and that is the point of the whole exercise.**
`npm test` returns 0 or 1, has no opinion about whether it did well, and cannot be
persuaded that a failure is unimportant.

> Avoid `["bash", "-lc", "npm test"]` for this. It looks portable but on Windows
> with WSL installed and no distro, `bash` resolves to a stub that fails with
> `execvpe(/bin/bash) failed` — and your gate then fails for a reason that has
> nothing to do with your tests. Invoke the tool directly.

---

## Step 4: isolate the writer and review the diff

While `implement` runs it is editing your working directory. Add one line:

```toml
[nodes.implement]
needs   = ["plan"]
agent   = "claude"
isolate = true
retries = 1
prompt  = "Implement exactly this plan. Do not expand scope.\n\n{plan}"
```

Requires a git repo. Claude now works in `.geng/wt/implement/` on branch
`geng/implement`; your files are untouched. On success the work is committed to
that branch. **On failure nothing is committed**, so a half-finished edit can
never be mistaken for something reviewable.

`retries = 1` gives one more attempt with backoff before the node is marked
failed.

The gate must now test the worktree rather than your unchanged directory:

```toml
[agents.tests_in]
argv       = ["{python}", "-c", "import subprocess,sys; sys.exit(subprocess.run(['npm','test'], cwd=r'{implement_worktree}').returncode)"]
prompt_via = "none"
```

And two reviewers can read the diff — one of them on a different vendor, because
agents on the same model reading the same context share blind spots and will
agree with each other:

```toml
[agents.codex_ro]
argv = ["codex", "exec", "--sandbox", "read-only", "--ask-for-approval", "never"]

[nodes.review_claude]
needs  = ["verify", "implement"]
agent  = "claude_ro"
out    = "build/review-claude.md"
prompt = "Run `git diff main...{implement_branch}` and review it. Report only defects you can cite a line for."

[nodes.review_codex]
needs  = ["verify", "implement"]
agent  = "codex_ro"
out    = "build/review-codex.md"
prompt = "Run `git diff main...{implement_branch}` and review it. Report only defects you can cite a line for."
```

Both list `implement` in `needs` purely to bring `{implement_branch}` into
scope — geng refuses to substitute a placeholder from an undeclared dependency, so
the graph cannot lie about what it reads. Note `claude_ro` needs
`Bash(git diff:*)` in its `--allowedTools` to run that command.

Because they depend only on `verify`, both reviewers land in the same wave and run
concurrently. When you are satisfied: `git merge geng/implement`.

The finished graph is [`examples/feature.toml`](../examples/feature.toml).

---

## Claude specifics worth knowing

**Structured output.** `--output-format json` returns an object containing
`result`, `session_id` and `total_cost_usd`; `--json-schema` constrains the final
message to a schema. geng captures whatever the process prints, so a downstream
node receives that JSON as its `{placeholder}` — useful when the next step is a
program rather than an agent. Remember that geng still judges success by the exit
code, because Claude's JSON is not emitted on every failure path.

**Model per node.** `--model` is per-invocation, so use a cheap model for
mechanical edits and a strong one for the node needing judgement:

```toml
[agents.claude_fast]
argv = ["claude", "-p", "--permission-mode", "acceptEdits", "--model", "haiku"]
```

This per-node cost control is something a single loop cannot give you.

**Keep nodes stateless.** Do not add `--continue` or `--resume` to an adapter. A
node that resumes a previous session inherits context the graph does not know
about, which destroys the clean-context-window property the design depends on.

**Claude Code has its own graph feature.** Dynamic workflows and subagents are
good, and they resume only within the same session and only in Claude Code. Use
them if you never intend to switch vendors; use geng when you want the same graph
to survive a change of tools or move into CI.

**Cap concurrency to cap spend.** `max_parallel` under `[settings]` is how many
agents run at once. Two parallel reviewers cost twice as much per wave.

---

## Next

- Have Claude write the spec **for** you: [TUTORIAL_AI_WRITES_IT.md](TUTORIAL_AI_WRITES_IT.md)
- The same graph with `omp`: [TUTORIAL_OMP.md](TUTORIAL_OMP.md)
- Every option: [spec reference](../README.md#spec-reference)
- And the caveat that matters: most tasks do not need a graph —
  [when not to use this](../README.md#when-not-to-use-this)
