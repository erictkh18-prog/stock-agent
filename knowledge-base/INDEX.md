# Knowledge Base Index

## Purpose
- Centralize reusable project/domain knowledge for review and future chatbot use.
- Make implementation decisions consistent by referencing approved chapters first.

## How To Use This Knowledge Base
- Read section index first, then topic, then chapter.
- Use only `Approved` chapters as default decision logic.
- Treat `Draft` and `Reviewed` chapters as reference, not authority.

## Section / Topic / Chapter Structure
- `sections/<section-id>-<section-name>/topics/<topic-id>-<topic-name>/chapters/<chapter-id>-<chapter-name>.md`

## Sections

### 01 Foundations
- Scope: governance, evidence standards, reasoning policy, conflict resolution.
- Path: `knowledge-base/sections/01-foundations/`

### 02 Trading Domain
- Scope: market concepts, setup logic, indicators, risk management.
- Path: `knowledge-base/sections/02-trading-domain/`

### 03 Product & UX Rules
- Scope: stock-screening UX rules, prioritization, recommendation explainability.
- Path: `knowledge-base/sections/03-product-ux-rules/`

### 04 Engineering Patterns
- Scope: architecture, API contracts, caching, observability, testing patterns.
- Path: `knowledge-base/sections/04-engineering-patterns/`

## Content Status Levels
- `Draft`: captured but not validated.
- `Reviewed`: internally checked and clarified.
- `Approved`: authoritative project guidance.

## Chapter Metadata Requirements
Each chapter must declare:
- `status`
- `owner`
- `last_reviewed`
- `confidence`
- `sources`

## Update Workflow
1. Capture source with ingestion template.
2. Convert claims into explicit rules.
3. Add chapter under the right topic.
4. Record change in `knowledge-base/CHANGELOG.md`.
5. Promote status only after review.
