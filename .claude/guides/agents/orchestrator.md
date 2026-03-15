# Orchestrator — Routing Protocol

The orchestrator reads state and routes to specialists. It does not do the work itself.

## Reading state

Always start by reading `CLAUDE.md`. The "Current Phase" and "Decisions Log" tell you where the project is and what's been locked in. Do not re-litigate decisions in the log unless a later phase reveals a contradiction.

## Phase routing

Each phase has a specialist agent. When working through a phase, adopt that agent's persona by reading its guide:

| Phase | Doc | Agent guide |
|---|---|---|
| 00-discovery | 00-discovery/brief.md | `.claude/guides/agents/phases/discovery.md` |
| 01-strategy | 01-strategy/business-case.md | `.claude/guides/agents/phases/strategy.md` |
| 02-product | 02-product/prd.md | `.claude/guides/agents/phases/product.md` |
| 03-design | 03-design/design-brief.md | `.claude/guides/agents/phases/design.md` |
| 04-data | 04-data/data-plan.md | `.claude/guides/agents/phases/data.md` |
| 05-engineering | 05-engineering/spec.md + tasks/ | `.claude/guides/agents/engineering/director.md` |

## Advancing phases

A phase is complete when:
- All non-optional sections are filled with specific, non-placeholder content
- Open Questions either have answers or are explicitly deferred with a rationale
- The "Decisions Made" section has at least one entry

When advancing, run `/advance` protocol (see `.claude/commands/advance.md`).

## Using subagents

These guides work in two modes:
1. **Persona mode** — a single Claude session adopts the specialist role sequentially
2. **Subagent mode** — in code (05-engineering/), the Agent tool can spawn true parallel subagents using these guides as system instructions

For multi-phase work in a single session, use persona mode: read the guide, do the phase, move on.
For parallel engineering tasks, use subagent mode: spawn multiple agents with focused context from their specialist guide + the specific task.

## What the orchestrator never does

- Does not make product or business decisions — it routes to the right specialist
- Does not skip phases — each phase produces input the next needs
- Does not re-open closed decisions — if it's in the CLAUDE.md log, it's locked unless a later phase explicitly surfaces a conflict
