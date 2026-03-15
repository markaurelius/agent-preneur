Formalize the transition from the current phase to the next.

Read `CLAUDE.md` to determine the current phase.
Read the current phase doc.

Steps:
1. Check completeness: are there unfilled template sections or open questions that block the next phase? If yes, list them and stop — do not advance until resolved.
2. Extract 1-3 key decisions from this phase doc that affect downstream work.
3. Update `CLAUDE.md`:
   - Add extracted decisions to the Decisions Log (newest at top)
   - Advance "Current Phase" to the next phase
   - Update "Next Action" to the first concrete step in the next phase
4. Write a one-paragraph handoff note at the bottom of the current phase doc under a `## Handoff` section — written for the next phase's agent, summarizing what it needs to know.
5. Tell the user what phase they're now in and what to do next.

Phase order: 00-discovery → 01-strategy → 02-product → 03-design → 04-data → (run `make scaffold`)

Do not create or edit the next phase doc — that happens in the next interview session.
