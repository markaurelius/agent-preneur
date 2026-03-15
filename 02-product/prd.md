# Product Requirements

> ~2 pages. Be concrete enough that an engineer can estimate scope.
> Every requirement should trace back to a user problem in 00-discovery/brief.md.

## Problem Statement

<!-- One paragraph restating the core problem this product solves, from the user's POV. -->

## Users

<!-- Primary user persona. One sentence. What's their job-to-be-done? -->

## Requirements

### Must Have (v1)

<!-- The minimum set needed to deliver real value. If it's not on this list, it ships later. -->

- [ ]
- [ ]
- [ ]

### Nice to Have (v2)

<!-- Don't build these yet. Listing them here prevents scope creep during v1. -->

- [ ]
- [ ]

### Out of Scope

<!-- Explicit list of things we decided NOT to build and why. -->

-

## User Stories

<!-- Format: As a [user], I want to [action] so that [outcome].
     Cover the core happy path flows. 3-5 stories max for v1. -->

1. As a __, I want to __ so that __.
2. As a __, I want to __ so that __.
3. As a __, I want to __ so that __.

---

## Open Questions

<!-- Product decisions not yet made. Each should have an owner and a deadline. -->

-

## Decisions Made

<!-- Resolved questions. Summarize into CLAUDE.md. -->

-

---

## Engineering Handoff

<!-- Written for the engineer (or AI) picking this up.
     Map product requirements to technical primitives.
     This section feeds directly into 05-engineering/spec.md. -->

**Core entities:**
- <!-- e.g., User, Project, Invoice — the main data models -->

**Key actions/APIs:**
- <!-- e.g., POST /projects, GET /dashboard, webhooks for X -->

**Integrations required:**
- <!-- e.g., Stripe for billing, SendGrid for email, S3 for uploads -->

**Performance/scale constraints:**
- <!-- e.g., must support 10k concurrent users, < 200ms p99 on search -->

**Auth model:**
- <!-- e.g., email+password, OAuth via Google, API keys for B2B -->

**Deployment target:**
- <!-- e.g., single region AWS, serverless, self-hosted Docker -->
