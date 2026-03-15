# Claude Workflow Guide

## How This Repo Works

Phases 00-04 produce documents. Phase 05 is the live codebase.
Business docs are upstream of the code — decisions made in discovery shape the engineering spec.

**`CLAUDE.md` is the shared memory across all agents.** Every specialist reads it. Every decision goes into it.

## Agent Architecture

This repo uses a multi-agent structure. Each phase and engineering discipline has a dedicated specialist.

```
Orchestrator (.claude/guides/agents/orchestrator.md)
├── Phase specialists (one per phase, activated sequentially)
│   ├── Discovery  → .claude/guides/agents/phases/discovery.md
│   ├── Strategy   → .claude/guides/agents/phases/strategy.md
│   ├── Product    → .claude/guides/agents/phases/product.md
│   ├── Design     → .claude/guides/agents/phases/design.md
│   └── Data       → .claude/guides/agents/phases/data.md
└── Engineering specialists (activated per task)
    ├── Director   → .claude/guides/agents/engineering/director.md
    ├── Frontend   → .claude/guides/agents/engineering/frontend.md
    ├── Backend    → .claude/guides/agents/engineering/backend.md
    ├── DevOps     → .claude/guides/agents/engineering/devops.md
    ├── Data Eng   → .claude/guides/agents/engineering/data-eng.md
    └── Security   → .claude/guides/agents/engineering/security.md
```

## Two modes of agent use

**Persona mode** (single session) — a Claude session reads a specialist guide and adopts that role. Used during interview sessions and manual phase work. Sequential.

**Subagent mode** (parallel, in code) — the Agent tool spawns independent Claude instances using specialist guides as their instructions. Used in `05-engineering/` for parallel task execution. Each subagent gets focused context — only what its task requires.

## Slash commands (native Claude Code)

| Command | What it does |
|---|---|
| `/interview` | Start or resume the guided interview |
| `/review [phase]` | Devil's Advocate review of a phase doc |
| `/advance` | Formalize phase transition, update CLAUDE.md |
| `/build [task]` | Route an engineering task to the right specialist |
| `/build` (no args) | Run the Director to generate the task backlog |

## Phase workflow

1. Write `pitch.md` → run `/interview` or `make interview`
2. Each phase: interview → `/review` → `/advance`
3. After phase 04: `make scaffold` generates `05-engineering/spec.md` + code skeleton
4. In phase 05: `/build` to decompose spec into tasks, then `/build [task]` per task

## Keeping CLAUDE.md current

Every time a key decision is made, it goes into CLAUDE.md Decisions Log — immediately, not at the end of the phase. The Decisions Log is what gives engineering agents their "why." If it's not in CLAUDE.md, it's not a decision — it's a preference that will be forgotten.
