# Background: where "graph engineering" came from

This is the context that does not belong in a README. It matters because the term
arrived with a lot of noise attached, and knowing which parts are load-bearing
tells you when to reach for a graph and when to ignore the whole discourse.

## The term is days old. The structure is years old.

The phrase is precisely datable. On **2026-07-18** Peter Steinberger tweeted *"Are
we still talking loops or did we shift to graphs yet?"*, and a few hours later
Hamel Husain published *"Loop Engineering Is Dead. Enter Graph Engineering."* By
that weekend there were courses, roadmaps and tool stacks.

The mechanism it names is 19 months older. Anthropic published the five composable
patterns — prompt chaining, routing, parallelization, orchestrator-workers,
evaluator-optimizer — in [December 2024](https://www.anthropic.com/engineering/building-effective-agents).
LangGraph shipped `StateGraph` in January 2024 and now
[calls it graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph)
while conceding the genealogy outright: *"It's the latest term to come out of X's
AI content factory, joining prompt engineering, context engineering, harness
engineering, and loop engineering."*

Anthropic's own documentation never uses the word. Grep
`code.claude.com/docs/en/workflows` and you get zero hits for "graph" — they ship
the same capability as **dynamic workflows**. No AI lab has adopted the term as
product vocabulary.

The lineage underneath is real, though: loop engineering was popularized by
[Addy Osmani](https://addyosmani.com/blog/loop-engineering/) in June 2026, and it
descends from Geoffrey Huntley's [Ralph loop](https://ghuntley.com/ralph/) —
`while :; do cat PROMPT.md | claude-code ; done` — from July 2025. Loop
engineering held the title for about six weeks.

Turing Post's framing is the one worth keeping:

> A loop is already a graph. It is simply a graph whose path returns to an earlier node.

## The one part that is genuinely new

Most of the discourse is a rename. This part is not.

Prefect's [directed agentic graph](https://www.prefect.io/blog/loops-vs-graphs)
essay makes the argument with a refund agent. The state-of-the-art way to build one
is to write a skill listing ten checks a refund must pass, then give the agent a
tool that issues refunds. What you have actually done is **hand the agent the
capability to move money, along with a polite note about how you would like it
done.** It works if the agent reads the skill, and follows the steps in order, and
targets the right customer, and no one injected anything into its context.

Split it into two nodes and the agent doing the diligence *cannot issue refunds* —
the tool does not exist in its harness. Only after a programmatic decision point
does a second node get that capability, scoped to one customer, usable once.

> The number one reason to adopt graphs is the ability to change an agent's
> capabilities depending on the path it took to get there.

That is why `geng` treats the adapter — the argv — as the unit of capability, and
why a read-only reviewer is a different `[agents.*]` block rather than a sterner
prompt.

## The honest counter-case

Anthropic, scoping out the exact use case that gets hyped:

> most coding tasks involve fewer truly parallelizable tasks than research, and
> LLM agents are not yet great at coordinating and delegating to other agents in
> real time.

Cognition's [Don't build multi-agents](https://cognition.com/blog/dont-build-multi-agents)
is the sharpest version: *"Actions carry implicit decisions, and conflicting
decisions carry bad results."* Their canonical failure is splitting Flappy Bird
into two subtasks and getting a Super Mario background with a non-matching bird.
Their [2026 follow-up](https://cognition.com/blog/multi-agents-working) revises the
position with a condition that is exactly the rule `geng`'s examples encode:
**multi-agent works when writes stay single-threaded and the extra agents
contribute intelligence rather than actions.**

Measured costs: agents use roughly 4× the tokens of chat and multi-agent systems
roughly 15×; Claude Code agent teams about 7×. One documented incident had two
loops collide on a single PR for ~400k tokens against an ~80k baseline.

And the failure mode to actually fear is not cost, it is **correlated error**.
Twenty agents on one model reading the same flawed context will agree with each
other at scale. A graph multiplies a shared mistake exactly as efficiently as it
multiplies good work. The only defence is an edge that touches reality — tests
that ran, a build that compiled, money that moved — rather than another agent's
opinion. That is what a `gate` node is for.

## Why this runner is shaped the way it is

Harness-native graphing is real and good, and it does not travel:

- Claude Code dynamic workflows resume **only within the same session**
- Kiro's wave scheduler is locked to `tasks.md` inside the IDE
- LangGraph's typed channels need the orchestrator and the work in one Python process

What every harness exposes is a non-interactive invocation and an exit code. Four
consequences follow, and they are the whole design:

1. **Exit code authoritative, JSON best-effort.** Only Gemini (0/1/42/53) and Kiro
   (0/1/3) publish exit-code tables. Cursor documents that on failure it emits *no*
   valid JSON. Kiro and aider have no JSON mode at all.
2. **Files as the state channel.** Claude Code documents the parent→subagent
   channel as prompt-string-only. A context window is not a state channel.
3. **`git worktree` as the isolation primitive.** Portable, and you can inspect the
   result yourself with `git diff` afterwards.
4. **Node-sized resume.** Every checkpointing system re-runs the interrupted node
   from the top — LangGraph, Dagster `FROM_FAILURE`, Actions `rerun --failed`,
   Make. Claude Code's docs say it plainly: *"a workflow that fans work out across
   many small agents preserves more progress than one long agent."*

## Sources

- [Prefect — Loops vs. graphs](https://www.prefect.io/blog/loops-vs-graphs) — directed agentic graphs; the refund argument
- [LangChain — 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph) — first-party adoption of the term
- [Turing Post — Is graph engineering real?](https://www.turingpost.com/p/is-graph-engineering-real-why-everyone-is-talking-about-it) — chronology and fact-check
- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — the five patterns, Dec 2024
- [Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — token multiples, non-determinism, the coding caveat
- [Cognition — Don't build multi-agents](https://cognition.com/blog/dont-build-multi-agents) and [the follow-up](https://cognition.com/blog/multi-agents-working)
- [Geoffrey Huntley — Ralph](https://ghuntley.com/ralph/) — the loop this reacts to
- [Addy Osmani — Loop engineering](https://addyosmani.com/blog/loop-engineering/) — the orchestration tax
- [Louis-François Bouchard — Graph engineering explained](https://www.louisbouchard.ai/graph-engineering-explained/) — "a graph is what you get when one loop is no longer enough"
- [Chase AI — Move over loop engineering](https://www.youtube.com/watch?v=Joqh7Tui9B8) — the video that prompted this project
- Harness CLI contracts: [Claude Code headless](https://code.claude.com/docs/en/headless) · [Codex non-interactive](https://developers.openai.com/codex/noninteractive) · [OpenCode CLI](https://opencode.ai/docs/cli/) · [Kiro headless](https://kiro.dev/docs/cli/headless/)
