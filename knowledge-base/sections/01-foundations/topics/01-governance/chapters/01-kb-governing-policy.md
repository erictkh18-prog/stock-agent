---
chapter_id: CH-001
title: Knowledge Base Governing Policy
status: Approved
owner: Eric + Copilot
last_reviewed: 2026-03-26
confidence: High
sources:
  - project agreement
---

# Objective
- Define how the project knowledge base is authored, reviewed, and used for decision-making.

# Core Concepts
- The knowledge base is the primary reference for product logic and implementation reasoning.
- Approved chapters have higher authority than ad hoc assumptions.

# Rules And Thresholds
- Rule 1: For implementation tasks, consult relevant Approved chapters first.
- Rule 2: If no Approved chapter exists, use Reviewed content and mark assumptions.
- Rule 3: Any new external source content starts as Draft.

# Use Cases
- Building screening logic.
- Designing recommendation explanations.
- Adding API behavior tied to trading concepts.

# Non-Use Cases
- Purely mechanical refactors that do not change business logic.

# Implementation Guidance
- Before coding strategy logic, map impacted files to chapter references.
- If chapter conflicts are found, ask for tie-break decision and log it.

# Examples
- Example: New indicator-based filter request.
- Action: ingest sources -> draft chapter -> derive rules -> implement -> cite chapter.

# Common Mistakes
- Treating unreviewed source claims as production logic.
- Mixing facts and opinions without labeling confidence.

# Open Questions
- None currently.

# References
- `knowledge-base/INDEX.md`
- `knowledge-base/templates/chapter-template.md`
