# Writing your first graph

A tutorial. Start with an empty file, end with a graph that ships a code change
behind a real test gate. Every line is explained before it is used.

You need Python 3.11+. You do **not** need an AI agent installed for parts 1–4;
we use Python itself as a stand-in so you can see the machinery clearly. Part 5
swaps in a real agent, and nothing else changes.

- [Part 1: one node](#part-1-one-node)
- [Part 2: two nodes and an edge](#part-2-two-nodes-and-an-edge)
- [Part 3: a gate that can't be argued with](#part-3-a-gate-that-cant-be-argued-with)
- [Part 4: doing three things at once](#part-4-doing-three-things-at-once)
- [Part 5: swapping in a real agent](#part-5-swapping-in-a-real-agent)
- [Part 6: isolating work that writes files](#part-6-isolating-work-that-writes-files)
- [What to reach for next](#what-to-reach-for-next)

---

## Part 1: one node

Make a directory, put `geng.py` in it, and create `hello.toml`:

```toml
[agents.py]
argv = ["{python}", "-c", "{prompt}"]

[nodes.greet]
prompt = "print('hello from a node')"
```

Run it:

```console
$ python geng.py run hello.toml
```

```
wave 1  (1 node(s), 4 at a time)
  ok   greet

1/1 ok, 0 failed, 0 skipped
```

**What those five lines mean.**

`[agents.py]` defines *how to run something*. The name `py` is yours to choose.
`argv` is the command, written as a list of separate words — the same way you'd
type it in a terminal, but split up so no shell gets involved and nothing needs
escaping.

Two placeholders appear in it:

- `{python}` becomes the Python interpreter currently running geng. Hardcoding
  `python` would break on machines where the command is `python3`.
- `{prompt}` is where the node's `prompt` gets substituted.

`[nodes.greet]` defines *a step to run*. `greet` is your name for it. Because we
didn't say which agent to use, it used `py` — the only one defined. Its `prompt`
is the text handed to that command.

So `[agents.*]` is the **how**, `[nodes.*]` is the **what**. That split is the
reason switching AI vendors later is a one-line change.

> **Why "wave 1"?** geng groups steps into waves. Everything in a wave can run at
> the same time because nothing in it depends on anything else in it. With one
> node there's one wave. This matters in Part 4.

Look in the new `.geng/` directory:

```
.geng/state.json        what happened, per node
.geng/out/greet.txt     everything the node printed
.geng/log/greet.log     the exact command, exit code, stdout and stderr
```

`out/greet.txt` is the important one — it's how nodes talk to each other.

---

## Part 2: two nodes and an edge

A second node that uses the first one's output. Create `chain.toml`:

```toml
[agents.py]
argv = ["{python}", "-c", "{prompt}"]

[nodes.pick]
prompt = "print('blue')"

[nodes.announce]
needs  = ["pick"]
prompt = "print('the colour is {pick}')"
```

```console
$ python geng.py run chain.toml
```

```
wave 1  (1 node(s), 4 at a time)
  ok   pick

wave 2  (1 node(s), 4 at a time)
  ok   announce
```

```console
$ type .geng\out\announce.txt      # macOS/Linux: cat
the colour is blue
```

Two new things, and they work together:

**`needs = ["pick"]`** is an edge. It means *don't start until `pick` has
finished successfully*. That's also why `announce` landed in wave 2.

**`{pick}`** inside the prompt is replaced by whatever `pick` printed.

Now try deleting the `needs` line and running again. It fails:

```
ERR  announce: prompt references unknown placeholder {pick}
```

This is deliberate. If an undeclared reference quietly became an empty string,
your graph would *look* connected while actually passing nothing — and you'd
debug the agent instead of the wiring. **You may only read from nodes you
declared a dependency on.**

> **Other placeholders.** `{pick_path}` gives the file path instead of the
> contents, useful when the output is large or when you want the agent to open it
> itself. Full list in the [spec reference](../README.md#spec-reference).

---

## Part 3: a gate that can't be argued with

Everything so far could have been one script. Here's where graphs earn their
keep.

We'll write a "report", then **verify** it. Create `gate.toml`:

```toml
[agents.py]
argv = ["{python}", "-c", "{prompt}"]

[nodes.report]
out    = "build/report.txt"
prompt = "print('findings: alpha, beta')"

[nodes.verify]
needs  = ["report"]
gate   = true
prompt = "import sys; t = open(r'{report_path}').read(); sys.exit(0 if 'gamma' in t else 'MISSING gamma')"
```

```console
$ python geng.py run gate.toml
```

```
wave 1  (1 node(s), 4 at a time)
  ok   report

wave 2  (1 node(s), 4 at a time)
  GATE verify exit=1 .geng\log\verify.log

halted: gate node(s) failed: verify
```

The run **stopped**, and `python geng.py run gate.toml` returned exit code 1 —
so a CI job or a shell script wrapping it would fail too.

Three things to notice:

**`out = "build/report.txt"`** copies the node's output to a path you choose, so
it's a real artifact rather than something buried in `.geng/`.

**`gate = true`** means *if this fails, stop the whole run*. Without it, a
failure only skips the steps that depended on it.

**The verify node is not an AI agent.** It's a program that checks a fact and
exits 0 or non-zero. Our fake check looked for "gamma" and didn't find it, so it
failed — correctly.

This is the single most important idea in the tool. In a real graph that node is
`npm test`, `pytest`, `cargo build`, `tsc --noEmit`:

```toml
[agents.shell]
argv = ["bash", "-lc", "{prompt}"]      # Windows: ["powershell", "-Command", "{prompt}"]

[nodes.verify]
needs  = ["implement"]
agent  = "shell"
gate   = true
prompt = "npm test"
```

An AI agent asked "is this code good?" is guessing, and it's motivated to say
yes. `npm test` returns 0 or 1 and has no opinion. **Put your trust in the thing
that can't negotiate.**

Fix the graph by changing the report to include `gamma`, then re-run:

```
wave 1  ...
  ok   report
wave 2  ...
  ok   verify

2/2 ok, 0 failed, 0 skipped
```

---

## Part 4: doing three things at once

Independent work should run simultaneously. Create `fanout.toml`:

```toml
[settings]
default_agent = "py"
max_parallel  = 3

[agents.py]
argv = ["{python}", "-c", "{prompt}"]

[nodes.frontend]
prompt = "import time; time.sleep(2); print('frontend audited')"

[nodes.backend]
prompt = "import time; time.sleep(2); print('backend audited')"

[nodes.docs]
prompt = "import time; time.sleep(2); print('docs audited')"

[nodes.summary]
needs  = ["frontend", "backend", "docs"]
out    = "build/summary.txt"
prompt = "print('''{frontend}\n{backend}\n{docs}''')"
```

```console
$ python geng.py run fanout.toml
```

```
wave 1  (3 node(s), 3 at a time)
  ok   backend
  ok   docs
  ok   frontend

wave 2  (1 node(s), 3 at a time)
  ok   summary
```

Three nodes that each sleep 2 seconds finished in about 2 seconds total, not 6 —
they had no dependencies between them, so they shared wave 1. `summary` needed
all three, so it waited. This is **fan-out then fan-in**, and it's the pattern
worth knowing: many readers, one joiner.

`[settings]` is new. `default_agent` saves repeating `agent = "py"` on every
node, and `max_parallel` caps how many run at once — that's your cost throttle
once these are real agents burning real tokens.

**Now run it again:**

```console
$ python geng.py run fanout.toml --resume
```

```
  cached backend
  cached docs
  cached frontend
  cached summary
```

Instant. `--resume` skips anything that already succeeded. Change *one* prompt
and re-run: only that node and the nodes downstream of it re-execute. The
untouched ones stay cached, because each node's identity is a hash of its prompt,
its command, and its ancestors' hashes.

Two more flags worth knowing before you point this at real money:

```console
$ python geng.py plan fanout.toml       # diagram + waves, executes nothing
$ python geng.py run fanout.toml --dry-run   # prints the exact command per node
```

`--dry-run` is how you confirm the argv is what you meant *before* an agent runs.

---

## Part 5: swapping in a real agent

Everything above used Python as a stand-in. Here's the payoff: the graph doesn't
change. Only the `[agents.*]` block does.

```toml
[agents.dev]
argv = ["claude", "-p", "--permission-mode", "acceptEdits"]
```

Change that one line to move the whole graph to another vendor:

```toml
argv = ["codex", "exec", "--sandbox", "workspace-write"]   # Codex
argv = ["opencode", "run", "--auto"]                       # OpenCode
argv = ["kiro-cli", "chat", "--no-interactive"]            # Kiro
argv = ["gemini", "-p"]                                    # Gemini
argv = ["cursor-agent", "-p", "-f"]                        # Cursor
argv = ["amp", "-x"]                                       # Amp
argv = ["aider", "--yes-always", "--message"]              # aider
```

Copy-pasteable versions of all of these are in
[`adapters.toml`](../adapters.toml).

Notice there's no `{prompt}` in these. When it's absent, geng appends the prompt
as the final argument — which is exactly how these CLIs expect it.

**The trick worth learning: define the same agent twice, with different powers.**

```toml
[agents.dev]
argv = ["claude", "-p", "--permission-mode", "acceptEdits"]

[agents.reader]
argv = ["claude", "-p", "--allowedTools", "Read,Grep,Glob"]
```

`reader` has no editing tools. A node using it *cannot modify your files* — not
because you asked it nicely, but because the capability isn't there. Use it for
planning and reviewing:

```toml
[nodes.review]
needs  = ["verify"]
agent  = "reader"
prompt = "Review the diff for correctness and missed callsites. Report only defects you can cite a line for."
```

A reviewer that can edit will often just fix what it finds and report success,
and you learn nothing. A reviewer that *can't* edit has to tell you.

---

## Part 6: isolating work that writes files

When several nodes edit the same repository at once, they trample each other.
One line fixes it:

```toml
[nodes.implement]
agent   = "dev"
isolate = true
prompt  = "Add request-id logging to the HTTP layer."
```

`isolate = true` gives that node **its own git worktree** — a separate checkout
of your repo on its own branch, `geng/implement`. The node works there. Your
actual working directory is never touched.

If the node succeeds, its work is committed to that branch. **If it fails,
nothing is committed** — so a half-finished edit can never be mistaken for
something worth reviewing. Downstream nodes can then read:

- `{implement_branch}` → `geng/implement`
- `{implement_worktree}` → the path to that checkout
- `{implement_commit}` → the commit hash

Which is how a reviewer gets a diff to look at:

```toml
[nodes.review]
needs  = ["verify", "implement"]
agent  = "reader"
prompt = "Run `git diff main...{implement_branch}` and review that diff."
```

When you're happy: `git merge geng/implement`.

Add `cwd` to scope a node to a subdirectory *inside* its worktree — that's how
you give three parallel nodes one package each:

```toml
[nodes.api]
isolate = true
cwd     = "packages/api"
prompt  = "Rename logger.log to ctx.log in this package only."
```

> **The one rule:** one file, one owner. Never let two nodes edit the same file.
> Worktrees stop them corrupting each other mid-write, but they don't stop two
> agents making incompatible design choices.

---

## What to reach for next

You now know every concept. The full option list is the
[spec reference](../README.md#spec-reference); the options not covered above are
`retries` (retry a flaky step, with backoff), `timeout`, `ok_exit` (when a
non-zero exit means success, as with `grep`), and `env`.

The five graphs in [`examples/`](../examples) are the real patterns:

| File | What it demonstrates |
| --- | --- |
| [`smoke.toml`](../examples/smoke.toml) | everything in parts 1–4, runs with no agent installed |
| [`feature.toml`](../examples/feature.toml) | plan → implement → **test gate** → two reviewers on different models → you decide |
| [`migration.toml`](../examples/migration.toml) | one owner per package, one barrier, then an audit for missed callsites |
| [`flaky.toml`](../examples/flaky.toml) | prove the bug is real *before* theorising, race hypotheses, then one writer |
| [`isolation.toml`](../examples/isolation.toml) | the worktree guarantees, as a test fixture |

**And the honest advice:** most tasks don't need any of this. One agent in a loop
is simpler and cheaper, and you should reach for a graph only when your context
is overflowing, when you need a judge that isn't the author, or when genuinely
independent work is running one-at-a-time for no reason. See
[when not to use this](../README.md#when-not-to-use-this).
