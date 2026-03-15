  Seed Spec: venture-template

  Goal: A single-repo template for a small team to take an idea from zero to shipped product. Every phase feeds the next — business docs aren't separate from the codebase, they're upstream of it. When it's time to
   build, the engineering scaffold initializes inside this same repo with full context from prior phases.

  Core philosophy:
  - Phases are numbered folders (00 → 05). You move forward, not sideways.
  - Each phase has one primary document (not a folder of forms). Keep it tight.
  - The CLAUDE.md teaches the AI assistant the full context at every phase, so Claude Code in implementation mode knows why decisions were made, not just what to build.
  - make commands drive transitions between phases (e.g. make scaffold bootstraps the engineering folder from the spec).

  Repo structure:

  /
  ├── CLAUDE.md                   # AI context: project status, phase, key decisions log
  ├── Makefile                    # Phase lifecycle commands
  ├── README.md                   # How to use this template
  ├── .env.example
  ├── .claude/
  │   └── guides/                 # Carried over from engineering template
  │
  ├── 00-discovery/
  │   └── brief.md                # Problem, users, why now, what success looks like
  │
  ├── 01-strategy/
  │   └── business-case.md        # Market size, competitive landscape, revenue model, OKRs
  │
  ├── 02-product/
  │   └── prd.md                  # Requirements, user stories, out-of-scope, open questions
  │
  ├── 03-design/
  │   └── design-brief.md         # UX flows, component inventory, design principles
  │
  ├── 04-data/
  │   └── data-plan.md            # Key metrics, instrumentation plan, experiment design
  │
  └── 05-engineering/             # Bootstrapped by `make scaffold` — becomes the live codebase
      ├── spec.md                 # Technical architecture derived from PRD + data plan
      ├── Makefile                # Mirrors engineering template (make dev, make test, etc.)
      ├── Dockerfile
      ├── docker-compose.yml
      └── src/

  Key behaviors to implement:

  1. make scaffold — reads 02-product/prd.md and 04-data/data-plan.md, generates 05-engineering/spec.md as a structured technical spec, then bootstraps the full engineering scaffold (Docker, Makefile, src/
  skeleton) based on the chosen stack.
  2. make status — prints current phase, key decisions from CLAUDE.md, and what the next action is.
  3. Each document template has a ## Open Questions and ## Decisions Made section. Decisions made get summarized into the top-level CLAUDE.md as a running log — this is what gives the AI full context during
  implementation.
  4. The CLAUDE.md at the root should always reflect:
    - Current phase
    - Stack choices (once decided)
    - 5-10 key decisions that affect implementation
    - Links to each phase doc

  Document templates should be minimal: Each phase doc is ~1-2 pages max when filled in. No 20-field intake forms. If a section doesn't apply, delete it.

  Transition to code is explicit: The last section of 02-product/prd.md is ## Engineering Handoff — a short bulleted summary written for an engineer (or AI) that maps product requirements to technical primitives.
  This feeds directly into 05-engineering/spec.md.
