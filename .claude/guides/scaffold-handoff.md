# Scaffold Handoff Guide

After `make scaffold` runs, you have `05-engineering/spec.md` and Docker boilerplate in `05-engineering/`.
This guide explains what to do next depending on your situation.

---

## Situation A: New project (no existing code)

Build directly in `05-engineering/`:

1. Review and edit `spec.md` — confirm stack, data model, and API design
2. Replace the stub `Dockerfile` with one appropriate for your stack
3. Update `docker-compose.yml` if you need Redis, a different DB, or additional services
4. Run `/build` in Claude Code to decompose `spec.md` into a task backlog
5. Run `/build [task-name]` to implement each task with the right specialist agent

---

## Situation B: Existing codebase in a separate repo

The spec is documentation, not a scaffold to build into. Use it as context.

1. Copy `05-engineering/spec.md` into your code repo (e.g. as `SPEC.md` or `.claude/spec.md`)
2. Create or update a `CLAUDE.md` in your code repo — reference the spec and copy the key decisions from `my-venture/CLAUDE.md`
3. Add your existing `Dockerfile` and `docker-compose.yml` to the code repo if not already there — ignore the stubs in `05-engineering/`
4. Open Claude Code in your code repo and use `/build [task]` for new features — it will use the spec as context

---

## Docker setup

The scaffold generates a generic Docker setup. Customize before first use:

- **`Dockerfile`** — replace the `FROM` line with your stack's base image and add your install + start commands
- **`docker-compose.yml`** — includes app + Postgres by default; uncomment Redis if needed; update the port if your app uses something other than 8000
- **`.env`** — not generated (never commit secrets); create it locally from a `.env.example` you maintain

The docker-compose `develop.watch` block enables live reload in development — update the `path` to match where your source files live.

---

## Updating the spec over time

The spec is a living document. Update it when:
- You make a significant architecture decision not captured in it
- A v2 feature gets promoted to the current milestone
- The data model changes meaningfully

Keep `my-venture/CLAUDE.md` Decisions Log in sync — it's the fastest-loading context for any Claude session.
