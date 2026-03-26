---
chapter_id: CH-002
title: Decision Priority Order
status: Approved
owner: Eric + Copilot
last_reviewed: 2026-03-26
confidence: High
sources:
  - project agreement
---

# Objective
- Provide a deterministic priority order for reasoning when building features.

# Core Concepts
- Not all information has equal authority.
- Priority ordering avoids contradictory behavior and drift.

# Rules And Thresholds
- Priority 1: Approved knowledge-base chapters.
- Priority 2: Explicit project constraints (free APIs, platform limits, runtime constraints).
- Priority 3: Existing codebase conventions and proven tests.
- Priority 4: External assumptions (must be labeled and reviewed).

# Use Cases
- Choosing indicator thresholds for scanner ranking.
- Resolving conflicting guidance from multiple websites.

# Non-Use Cases
- Personal preference tweaks without logic impact.

# Implementation Guidance
- In implementation summaries, list which chapters were used.
- If external assumptions are used, record them in changelog and chapter open questions.

# Examples
- Two sources disagree on RSI buy zone.
- Apply Approved chapter threshold first; if missing, create Draft chapter and request review.

# Common Mistakes
- Skipping priority checks and applying first-found internet advice.

# Open Questions
- None currently.

# References
- `knowledge-base/INDEX.md`
- `knowledge-base/CHANGELOG.md`
