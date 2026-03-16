# Roadmap — Analogue Prediction Engine

> This document tracks known improvements to the current system and the expansion
> roadmap to new domains. The v1 geopolitics system is the proof of concept.

---

## The Core Thesis

The system works when three things are true:

1. **A rich historical corpus exists** — events with known outcomes, structured enough to embed meaningfully
2. **The question type recurs** — history doesn't repeat exactly, but it rhymes; analogues are findable
3. **The question resolves after Claude's training cutoff** — otherwise Claude is recalling, not reasoning

Where all three hold, this architecture can make better-than-random predictions in domains that were previously hard because of fragmented data, slow iteration cycles, or high cost of expertise.

---

## Current System: Known Improvements

### Corpus Quality (highest leverage)

The description field is the single most important thing — it's what gets embedded. Better descriptions → better retrieval → lower Brier score.

| Improvement | Effort | Expected Impact |
|---|---|---|
| **Switch to ACLED dataset** | Medium | High — 250k+ conflict events, rich outcomes, clean structure |
| **Generate richer descriptions via Claude** | Low | High — one-time cost to expand sparse CoW rows into narrative text |
| **Add UCDP conflict termination data** | Medium | Medium — strong outcome data for war endings |
| **Add outcome to ChromaDB metadata** | Low | Medium — enables filtering by outcome type at retrieval time |

**ACLED** is the priority corpus upgrade. Fields map cleanly to our schema and outcomes are specific: "Territorial gain", "Strategic withdrawal", "Armed clash — government forces victorious". Register free at acleddata.com.

**Richer descriptions** example — current CoW output:
```
Dispute 4021: conflict involving RUS in 2014. Outcome: Victory for side A.
```
Claude-generated version (one API call per event at ingest time):
```
In 2014, Russia conducted a covert military operation to annex Crimea from Ukraine,
deploying unmarked special forces ("little green men") alongside local militia.
Ukrainian forces offered minimal resistance. Russia achieved full territorial control
within weeks. Outcome: Russian annexation, internationally unrecognized.
```
The second version retrieves correctly for questions about territorial disputes, hybrid warfare, and annexation. The first one barely matches anything.

### Retrieval

| Improvement | Effort | Expected Impact |
|---|---|---|
| Switch to hybrid mode by default | Low | Medium — date proximity re-ranking is cheap and helps |
| Tune `top_k` per domain | Low | Low — try 3, 5, 10 and compare Brier |
| Add actor-based filtering | Medium | Medium — find analogues involving the same countries |

### Prompt

The prompt is the cheapest lever. Each version is a new file and a new YAML.

Known prompt experiments to try:
- **Chain of thought**: ask Claude to reason step by step before giving probability
- **Devil's advocate**: ask Claude to argue both YES and NO before settling
- **Base rate first**: ask Claude to estimate base rate from analogues before adjusting
- **Explicit analogue weighting**: ask Claude to score each analogue's relevance 1-5 before synthesizing

### Data Leakage

All current questions resolve before Claude's knowledge cutoff (~Aug 2025). This means:
- Brier scores are optimistic — Claude may be recalling, not reasoning
- Prompt improvements are harder to isolate from memorization effects

Fix: once live forecasting (forecast.py) accumulates resolved questions from 2025+, use those as the evaluation set. True out-of-sample performance.

---

## Domain Expansion

The architecture requires two things per new domain:
1. A **corpus parser** — maps domain dataset → `{id, description, actors, event_type, outcome, date, region}`
2. A **question source** — resolved binary questions to evaluate against

The rest (embedding, ChromaDB, retrieval, Claude synthesis, Brier scoring) is already domain-agnostic.

---

### Domain: Finance

**Thesis:** Financial markets have deep structured history. Price action, earnings, macro events, and geopolitical shocks all recur in recognizable patterns. Claude can reason from analogues about whether a stock, sector, or macro variable will be above/below a threshold at a future date.

**Corpus candidates:**
| Dataset | Events | Outcomes | Access |
|---|---|---|---|
| CRSP / Compustat | US equity history 1925–present | Price, earnings, volatility | Academic subscription |
| Federal Reserve FRED | Macro indicators (GDP, CPI, rates) | Values over time | Free API |
| Quandl / Nasdaq Data Link | Commodities, FX, futures | OHLCV | Freemium |
| SEC EDGAR | Earnings reports, 8-K filings | Actual vs. estimate | Free |

**Question source:** Metaculus has finance/economics questions. Manifold Markets has prediction markets with binary resolution. Good Judgment Open publishes resolved questions.

**Analogue example:**
```
Question: "Will the Fed cut rates before December 2025?"

Analogue: "March 2020 — Fed cuts rates 150bps in emergency session following COVID-19
market shock. Core CPI at 2.1%, unemployment rising rapidly. Outcome: YES — cut."

Analogue: "2019 — Fed cuts 75bps (insurance cuts) despite low unemployment and
moderate growth. Outcome: YES — cut, but gradual."
```

**Key difference from geopolitics:** Finance has *much* richer quantitative data. Descriptions should include the relevant numeric context (rates, spreads, volatility levels) not just narrative.

---

### Domain: Emerging Technology Forecasting

**Thesis:** Technology adoption curves, research breakthroughs, and capability jumps follow historical patterns. The history of prior technology S-curves (electricity, internet, mobile) provides analogues for current technology trajectories (AI, biotech, quantum).

**Corpus candidates:**
| Dataset | Events | Access |
|---|---|---|
| Our World in Data | Technology adoption curves, research milestones | Free |
| USPTO patent filings | Innovation clusters by domain | Free API |
| arXiv papers | Research publication velocity by field | Free API |
| Gartner Hype Cycle archives | Technology maturity assessments | Public summaries |

**Question source:** Metaculus technology category, Manifold Markets, AI Impacts forecasts.

**Analogue example:**
```
Question: "Will a commercially available quantum computer solve a problem
intractable for classical computers by 2027?"

Analogue: "1958–1965 — Integrated circuit development. First working IC (1958),
commercial availability (1961), cost drop 10x (1965). Time from lab to commercial: 7 years."

Analogue: "2012–2016 — Deep learning. AlexNet breakthrough (2012), commercial APIs (2014),
widespread deployment (2016). Time from breakthrough to deployment: 4 years."
```

**Key difference:** Technology questions are about capability thresholds, not discrete events. The corpus needs to capture *rates of progress* not just what happened.

---

### Domain: Epidemiology / Public Health

**Thesis:** Disease outbreaks, vaccination campaigns, and public health interventions follow patterns across history. Historical epidemic data provides analogues for outbreak trajectories and policy outcomes.

**Corpus:** CDC wonder database, WHO outbreak database, historical epidemic literature (well-documented for Spanish flu, SARS, MERS, H1N1).

**Use case:** Given a new outbreak in week 3, what's the probability it reaches 100k cases in 6 months? Historical analogues from similar outbreaks (attack rate, R0, intervention timing, geography) provide the prior.

---

### Domain: Legal / Regulatory

**Thesis:** Legal outcomes follow precedent. Given a case's facts, analogous prior cases predict likely outcomes — which is literally how common law works. Regulatory decisions (antitrust, FDA approvals, FCC rulings) also recur.

**Corpus:** CourtListener (Free Law Project), SEC enforcement actions, FTC antitrust cases, FDA approval history.

**Use case:** "Will the FTC block this merger?" Given the merger's market concentration, sector, and acquirer history, analogues from prior FTC decisions provide the prior.

---

## What Makes a Good Domain

Score a potential domain on these five criteria:

| Criterion | Why it matters |
|---|---|
| **Historical depth** | More events = better analogue pool. 10+ years minimum; 50+ ideal |
| **Outcome clarity** | Events need clear binary or scalar resolutions. "Conflict ended" > "tensions remained elevated" |
| **Recurrence** | Similar events need to happen often enough to find analogues. War patterns recur; once-in-a-century events don't |
| **Question availability** | Need resolved binary questions to evaluate against. Metaculus, prediction markets, or domain-specific sources |
| **Post-cutoff volume** | Questions resolving after Claude's training cutoff are the only valid out-of-sample test |

**Best near-term expansion:** Finance. Deep historical data, clear outcomes, abundant prediction market questions, and direct economic signal from better-than-random predictions.

---

## Architecture for Multi-Domain

When adding a second domain, the minimal change is:

```python
# One ChromaDB collection per domain
COLLECTIONS = {
    "geopolitics": "historical_events",
    "finance":     "financial_events",
    "technology":  "technology_events",
}

# One corpus parser per domain
# src/ingestion/acled.py   → geopolitics upgrade
# src/ingestion/fred.py    → finance macro
# src/ingestion/crsp.py    → finance equity
```

The `RunConfig` gains a `domain` field that selects the collection and applies domain-appropriate question filtering. Everything else — retrieval, synthesis, scoring — is unchanged.

A domain-specific prompt (e.g., `prompts/finance-v1.txt`) replaces the geopolitics framing but keeps the same structure.
