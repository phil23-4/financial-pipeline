# Documentation

This directory holds the project's long-form documentation for maintainers,
analysts, and paper reviewers. The primary entry point remains the root
[`README.md`](../README.md), which gives the overview, installation, and CLI
usage. The files here go deeper: design decisions, day-to-day operation, and
failure diagnosis.

## Files in this directory

| file | audience | purpose |
| --- | --- | --- |
| [`_FACTS.md`](_FACTS.md) | documentation authors | Verified fact inventory with per-claim source-file line citations. Treat this as the source of truth when writing or reviewing the other documents. |
| [`DESIGN_RATIONALE.md`](DESIGN_RATIONALE.md) | maintainers, paper reviewers | Architecture, data model, ingestion paths, design decisions with alternatives and trade-offs, and known limitations. |
| [`OPERATIONAL_REFERENCE.md`](OPERATIONAL_REFERENCE.md) | operators | Prerequisites, installation, SurrealDB configuration, the full CLI reference, CSV input formats, a typical batch workflow, timing estimates, post-batch verification, and the environment-variable reference. |
| [`DEBUGGING_GUIDE.md`](DEBUGGING_GUIDE.md) | operators | Failure diagnosis: five debugging principles, a copy-paste diagnostic command reference, and a symptom-to-cause-to-fix playbook. |

## Reading order

New operators read the root [`README.md`](../README.md) first, then
[`OPERATIONAL_REFERENCE.md`](OPERATIONAL_REFERENCE.md) before running a batch,
and [`DEBUGGING_GUIDE.md`](DEBUGGING_GUIDE.md) when a batch fails. Maintainers
and reviewers read [`DESIGN_RATIONALE.md`](DESIGN_RATIONALE.md) for the
justifications behind the implementation, and [`_FACTS.md`](_FACTS.md) to
check any claim against the source.

## Accuracy policy

The files in this directory are official project documentation. Each technical
claim cites its source inline in the form `Verified from <path>:<line>`, and a
claim that cannot be verified is marked with a `<!-- TODO: ... -->` comment
rather than guessed. A conflict between source files is recorded with
`<!-- CONFLICT: ... -->` and the code is treated as authoritative. These rules
are defined in the repository's [`AGENTS.md`](../AGENTS.md).

## Relationship to the source tree

Each document is scoped to a different slice of the codebase:

- [`_FACTS.md`](_FACTS.md) covers configuration, schema fields, CLI flags,
  function signatures, constants, log messages, and dependencies.
- [`DESIGN_RATIONALE.md`](DESIGN_RATIONALE.md) covers the reasoning behind the
  persistence, extraction, and enrichment layers.
- [`OPERATIONAL_REFERENCE.md`](OPERATIONAL_REFERENCE.md) covers how to run the
  commands defined in `src/fin_pipeline/cli.py`.
- [`DEBUGGING_GUIDE.md`](DEBUGGING_GUIDE.md) covers the failure modes of the
  ingestion, SEC retrieval, persistence, and bulk XBRL paths.

The schema and field names referenced throughout these documents are declared
in `src/fin_pipeline/db/db.py` and `src/fin_pipeline/models/schemas.py`. When
a document and the code disagree, trust the code and report the discrepancy.
