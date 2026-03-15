Run a structured Devil's Advocate review of a phase document.

If $ARGUMENTS specifies a phase name or number (e.g. "discovery", "01", "product"), review that phase doc.
Otherwise, read `CLAUDE.md` to determine the current phase and review that doc.

Phase doc locations:
- 00 / discovery  → 00-discovery/brief.md
- 01 / strategy   → 01-strategy/business-case.md
- 02 / product    → 02-product/prd.md
- 03 / design     → 03-design/design-brief.md
- 04 / data       → 04-data/data-plan.md

Read the corresponding agent guide from `.claude/guides/agents/phases/[phase].md` for the review methodology and specific stress-test questions for that phase.

Produce a structured critique with:
1. What's strong (1-3 bullets — be specific, not encouraging)
2. What's weak or vague (1-3 bullets — name the exact section and why it's a problem)
3. The single most important question left unanswered
4. A go/no-go recommendation: is this doc solid enough to move forward, or does it need more work?

Do not rewrite the doc unless the user asks. Flag, don't fix.
