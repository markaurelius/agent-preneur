# Data Plan

> ~1 page. Decide what you'll measure before you build, not after.
> Instrumentation that isn't planned here usually doesn't get built.

## North Star Metric

<!-- The single number that best represents whether users are getting value.
     Example: "Weekly active projects per paying team." -->

## Supporting Metrics

<!-- 3-5 metrics that explain movement in the north star. -->

| Metric | Definition | Target (90 days) |
|---|---|---|
| | | |
| | | |
| | | |

## Instrumentation Plan

<!-- What events need to be tracked? When and where are they fired?
     Be specific enough that an engineer can implement them. -->

| Event | Trigger | Properties |
|---|---|---|
| `user_signed_up` | On account creation | `source`, `plan` |
| `[event_name]` | | |
| `[event_name]` | | |

**Analytics tool:** <!-- e.g., PostHog, Mixpanel, custom, none for v1 -->

## Experiment Design

<!-- What's the first thing you'll A/B test once you have users?
     If you don't know yet, what question do you most want to answer? -->

**Question:**
**Hypothesis:**
**Metric:**
**Minimum sample size:**

## Data Infrastructure

<!-- What does the data stack look like?
     v1 can often just be Postgres + application logs. Be honest about complexity. -->

- Storage:
- Querying:
- Alerting:

---

## Open Questions

-

## Decisions Made

-
