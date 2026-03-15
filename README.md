# Venture Template

A single-repo template for taking a business idea from zero to shipped product.
One repo per idea. Business docs are upstream of the codebase — every engineering decision traces back to a discovery conversation.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- `make` (included on macOS/Linux)
- Docker (for the engineering phase)

## Setup

### Option A — GitHub template (recommended)

Click **Use this template** on GitHub → name your repo after the idea → clone it.

### Option B — Manual copy

```bash
cp -r startup-template my-idea
cd my-idea
rm -rf .git seed_spec.md
git init && git add . && git commit -m "init from venture template"
```

---

## Workflow

### Phase 1 — Pitch

Write a rough description of your idea in `pitch.md`. One paragraph is enough.
Don't polish it. The interview will draw out the details.

### Phase 2 — Interview

```
/interview
```

Claude reads your pitch and works through five phases with you — one question at a time.
Each phase has a specialist agent grounded in a specific methodology:

| Phase | Agent methodology |
|---|---|
| 00 Discovery | Jobs-to-be-Done, Mom Test |
| 01 Strategy | Porter's Five Forces, pre-mortem, Blue Ocean |
| 02 Product | Shape Up (appetite scoping), Jobs Stories |
| 03 Design | Progressive disclosure, constraint-based design |
| 04 Data | North Star Framework, Lean Analytics stages |

The interview fills in all five phase docs and keeps `CLAUDE.md` updated with decisions as they're made.

### Phase 3 — Review and advance

After each phase, run a Devil's Advocate review before moving on:

```
/review                   # review current phase
/review strategy          # review a specific phase
```

When the phase is solid:

```
/advance
```

This extracts key decisions into `CLAUDE.md`, writes a handoff note, and moves to the next phase.

### Phase 4 — Scaffold

Once all five phase docs are filled:

```bash
make scaffold
```

This generates `05-engineering/spec.md` from the PRD + data plan (using Claude if available),
then bootstraps the engineering skeleton: Dockerfile, docker-compose.yml, Makefile, and `src/`.

### Phase 5 — Build

```
/build
```

The Engineering Director reads `spec.md` and decomposes it into a typed task backlog at `05-engineering/tasks/backlog.md`.

Then implement task by task, each routed to the right specialist:

```
/build set up CI pipeline
/build implement user auth
/build build the project list page
/build review auth implementation
```

Engineering specialists:

| Specialist | Scope |
|---|---|
| Director | Decomposes spec, routes tasks |
| Backend | API, business logic, database, auth |
| Frontend | UI components, state, accessibility |
| DevOps | Docker, CI/CD, infra, observability |
| Data Engineering | Event tracking, analytics, schema design |
| Security | Reviews implementations — runs after, not before |

---

## Commands reference

| Command | Where | What it does |
|---|---|---|
| `make status` | Terminal | Current phase, decisions, doc completion |
| `make interview` | Terminal | Launch interview session (same as `/interview`) |
| `make scaffold` | Terminal | Generate spec.md + engineering skeleton |
| `/interview` | Claude Code | Start or resume guided interview |
| `/review [phase]` | Claude Code | Devil's Advocate critique of a phase doc |
| `/advance` | Claude Code | Formalize phase transition, update CLAUDE.md |
| `/build [task]` | Claude Code | Route task to engineering specialist |
| `/build` | Claude Code | Run Director to generate task backlog |

---

## How CLAUDE.md works

`CLAUDE.md` is the shared memory across all agents. Every specialist reads it before working.
Keep it current:

- **Decisions Log** — add every meaningful decision as it's made, not at the end of the phase. Format: `- [phase] Decision: Rationale`
- **Current Phase** — update when you `/advance`
- **Stack Choices** — fill in during the design phase; engineering agents depend on this
- **Next Action** — always points to the immediate next step

If a decision isn't in `CLAUDE.md`, engineering agents won't know about it.

---

## Tips

**On the pitch:** Don't overthink it. Two sentences describing the problem and who has it is enough to start. The interview fills in the rest.

**On the interview:** Push back when Claude accepts a vague answer. If it writes something you disagree with, say so — the docs should reflect what you actually believe.

**On `/review`:** This is a Devil's Advocate agent — it's supposed to be skeptical. If the critique doesn't surface anything you haven't thought about, the phase doc is in good shape.

**On `/advance`:** Don't advance a phase with unresolved Open Questions that affect the next phase. Decisions deferred here become assumptions baked into the engineering spec.

**On the task backlog:** Tasks should be small enough to implement in a single Claude Code session (~1-3 hours of work). If a task is bigger, ask the Director to break it down further.

**On security review:** Run `/review security` after implementing any auth, data access, or external integration. It's fast and catches the issues that are expensive to fix post-launch.
