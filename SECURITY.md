# Security policy

## Reporting a vulnerability

Please open a [GitHub security advisory](https://github.com/AdityaIndoori/graph-engineering/security/advisories/new)
rather than a public issue.

## Threat model, stated plainly

`geng` executes commands you write, in specs you write, with the credentials of
whoever runs it. It is a task runner: **a graph spec is executable code.** Treat a
`geng.toml` from an untrusted source exactly as you would treat a `Makefile` or a
`.github/workflows/*.yml` from that source — do not run it.

Specific things worth knowing:

- **Prompts are interpolated into argv.** `geng` passes arguments as a list to
  `subprocess.run` without a shell, so there is no shell-injection surface from
  `geng` itself. If *your* adapter is `["bash", "-lc", "{prompt}"]`, then your
  prompt is shell code by construction — that is the adapter's choice, not a
  `geng` defect.
- **Upstream node output flows into downstream prompts.** If a node's output is
  attacker-influenced, so is the next node's prompt. This is prompt injection and
  `geng` cannot prevent it. Keep the destructive capability in a node that only
  runs after a deterministic gate.
- **`isolate = true` runs `git` commands** in the repository it is invoked from,
  including `git worktree add`, `git branch -D geng/<node>` and `git commit`. It
  will delete a pre-existing branch named `geng/<node>`.
- **Restrict capability at the adapter, not in the prompt.** A node that must not
  write should be given a read-only agent invocation (`--allowedTools`,
  `--sandbox read-only`). An instruction not to write is not a permission boundary.

## Supported versions

The `main` branch is the supported version.
