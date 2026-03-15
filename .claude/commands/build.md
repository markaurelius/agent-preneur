Route an engineering task to the appropriate specialist agent.

Task: $ARGUMENTS

Read `CLAUDE.md` for project context and stack decisions.
Read `05-engineering/spec.md` for architecture.

If $ARGUMENTS is empty or "plan":
  Read `.claude/guides/agents/engineering/director.md` and run the task decomposition protocol.
  Generate or update `05-engineering/tasks/backlog.md`.

Otherwise:
  Read `.claude/guides/agents/engineering/director.md` to determine which discipline this task belongs to.
  Then read the appropriate specialist guide:
    - frontend  → `.claude/guides/agents/engineering/frontend.md`
    - backend   → `.claude/guides/agents/engineering/backend.md`
    - devops    → `.claude/guides/agents/engineering/devops.md`
    - data      → `.claude/guides/agents/engineering/data-eng.md`
    - security  → `.claude/guides/agents/engineering/security.md`

  Adopt that specialist's role and implement the task.
  After implementation, note any cross-discipline concerns (e.g. API contract changes that affect frontend).
