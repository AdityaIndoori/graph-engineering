# Contributing

Thanks for taking a look. This is a small, deliberately-scoped project, so the
bar is mostly about keeping it small.

## Ground rules

**`geng.py` stays dependency-free.** Standard library only, Python 3.11+. The
entire value proposition is a single file you can `curl` next to a repo and delete
afterwards. A `requirements.txt` would defeat the point.

**No packaging.** No `pyproject.toml`, no PyPI, no `pip install`. Same reason.

**Every behaviour change needs a test.** `tests/test_geng.py` is stdlib
`unittest` with no plugins. Tests that need `git` skip themselves when it is
absent, so the suite passes on a machine without it.

## Before you open a pull request

```console
$ python -m unittest discover tests -v      # must be green
$ python geng.py run examples/smoke.toml    # must print 6/6 ok
$ python tools/audit_deck.py                # only if you touched docs/pitch.html
```

CI runs the suite on Linux, macOS and Windows across Python 3.11–3.13. Because
`geng` shells out to `git` for worktree isolation, please assume both POSIX and
Windows behaviour matters — in particular, do not hardcode `python` in an example
spec (`python3` is what exists on many distributions; use `{python}`).

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`docs:`, `test:`, `ci:`, `refactor:`, `chore:`. Subject in the imperative mood,
under 50 characters where you can manage it.

## What is likely to be accepted

- New adapters in `adapters.toml` for agent CLIs that expose a non-interactive
  mode and a meaningful exit code
- Bug fixes with a failing test that the fix turns green
- Portability fixes for platforms the CI matrix does not cover

## What is unlikely to be accepted

- Parsing agent stdout to determine success. The exit code is the contract, on
  purpose — most harnesses do not emit reliable machine-readable output on failure.
- A plugin system, a config schema registry, or a web UI.
- Anything that makes `geng.py` need a dependency.

If you want a richer runner, that is a legitimate need and the README lists better
tools for it under "Alternatives".
