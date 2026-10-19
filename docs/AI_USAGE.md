# AI usage

## Version 3.0.2 privacy and delivery update

Personal names, email addresses and identifying account references were removed
from project-controlled documents and PDF exports. Current clone instructions
use the repository URL supplied by the reader rather than embedding a personal
account. Required data-owner attribution and software licenses remain intact.
The release includes independent prefilled source histories and the existing
portable runtime. GitHub account ownership and previous commit metadata are
separate from the contents of the delivered files.

## Version 3.0.1 national-route correction

The owner reported that the Bulgarian-direct source could not update.
Inspection of the official portal confirmed that its map AJAX returns only
placeholders and its detail AJAX is empty, but following the portal's own
report/export forms revealed a working hourly CSV endpoint.
The correction uses that direct export, not observations copied from EEA.
The first genuine year-long import stored 43,675 hourly records in the separate
national store. Tests cover CSV parsing, mass units, source isolation,
station-checkpoint resumption and duplicate clock-label quarantine.
The collector preserves the portal's three-minute export spacing, so complete
initial station coverage takes time and is not falsely reported as finished.

## Version 3 multi-source and operational UI

The owner requested separately retained histories for EEA, OpenAQ and any usable
direct Bulgarian feed; selectable combined comparisons; more pollutants;
station provenance; themes; personal alerts; and non-developer monitoring/data
maintenance menus. The original assignment was rechecked: prefill at least
12 months, and archive records when older than 36 months.

Primary-source requests and the actual returned metadata/measurements were used
to implement OpenAQ v3, including permission windows, UTC hourly normalization,
fixed-host header authentication, adaptive timeout windows and persisted
per-key request pacing. The national direct feed still did not supply usable
concentrations. It is disclosed as unavailable, not populated with copied EEA
observations or placeholder zeros.

Source databases, checkpoints and archives are physically separate. Optional
matched-source diagnostics disclose common EEA upstream. Personal alert rules,
baselines and events persist locally, with optional Windows delivery whose
"requested" status is not misrepresented as proof that a popup was seen.
Prometheus remains real; the normal Monitoring menu reads its APIs.

The owner authorized publishing new prefilled source databases via Git LFS.
Version 3 therefore adds sanitized, checksummed seeds and startup merge logic
instead of replacing colleagues' live stores. Credentials, quota timestamps and
personal alert state are not published. The API key supplied in conversation is
kept encrypted locally and is never copied into this document or prompt history.
Runtime binaries are unchanged.

The owner subsequently specified acknowledgement-only startup notification and
automatic replenishment after long absences, with separate provider completion
messages. Collection begins independently of the browser acknowledgement.
Source-specific notices and optional Windows delivery use the private local
event store. Tests cover a long gap predating the new last-year boundary, so
moving the configured window cannot silently discard missed time.

Offline regression tests, native Edge UI exercises, source-query timing and
fresh-copy checks validate the implementation. Tests exposed and corrected
Windows SQLite backup-handle retention and missing empty checkpoints for
inactive OpenAQ sensors; neither was hidden by a silent retry or fake record.
Final evidence and limitations are in [VERIFICATION.md](VERIFICATION.md).
This work remains AI-assisted, not an independent human audit.

## Version 2 source/search upgrade

The owner requested clearer cleanest-air wording, official Bulgarian station
detail, a default latest-reading search and configurable historical/time
definitions. Primary-source requests verified 32 national site identities,
addresses and coordinates. The national live map returned unlabelled,
untimestamped zero placeholders rather than usable concentrations.

The owner approved using factual Bulgarian station metadata with Bulgaria's
actual EEA-delivered observations, clearly identifying the common provenance,
and approved cleanest-day/hour searches where real hourly history exists.
No runtime AI or search API was introduced.

Implementation preserved the city-average option, added an automatically
backed-up schema migration, timestamp/freshness-cohort checks, explicit ranking
definitions and compatibility tests. SQL query-plan inspection corrected a slow
join in wide hourly searches; the top-time result set is bounded and its full
scope disclosed. Existing user-authored/generated PDFs were not overwritten.

This project was developed with an AI assistant using **Copilot SDK in VS Code**.

### Version 2 publication scope

The owner explicitly selected the primary project repository for the final
update and requested a small ordinary Git pull for colleagues
who already have the complete runtime. This update therefore publishes code,
configuration, tests and Markdown evidence without changing the committed
runtime or SQLite LFS objects. Each installation backs up/migrates its own
database and refreshes real data through its existing collector.
The refreshed complete ZIP is a separate portable deliverable.

## What AI was used for

- Reading the user-supplied assignment and translating it into implementation
  requirements and verification checks.
- Researching WHO guidance, revised European AQI methodology, EEA data access,
  source schemas and reuse conditions.
- Designing and implementing the Python application, SQLite storage, ingestion,
  configurable references, UI, monitoring, portable packaging and documentation.
- Writing and running automated tests, real-data imports and performance checks.

## Important corrections and decisions

- An initial React/PostgreSQL/Docker proposal was simplified to server-rendered
  HTML and SQLite after discussion with the owner.
- Copying a virtual environment was not treated as portable. The owner explicitly
  chose bundled Windows Python and packages so recipients need no Python install.
- The initial source assumption was corrected using real files: validated history
  is daily, current data hourly. The owner approved UTC dates to preserve matching
  averaging windows instead of pretending daily means could be shifted accurately.
- EEA dataset selectors and storage paths were checked rather than inferred.
  The current container is `airquality-p`; server date filters were observed to
  omit overlapping files, so filtering is performed locally.
- Hourly European AQI bands and annual WHO guidance were not applied to arbitrary
  historical period averages.
- Default interactive API documentation would have needed external assets and
  inline scripts; it was replaced with offline documentation and generated
  OpenAPI JSON.
- Test failures were investigated and corrected. For example, a coverage-boundary
  fixture originally requested a date beyond its seeded available history; it was
  corrected to test coverage without accidentally testing an unavailable period.
- Relocation checks exposed an offline-status issue: a cached successful import
  message could still describe online updates. Both server-rendered and
  client-refreshed status now use the current offline setting, with a regression test.
- A later request added an explicit stop launcher for manual ZIP creation.
  Cooperative shutdown, scoped leftover-process cleanup, SQLite release checks
  and preservation of unrelated Python processes were exercised on Windows.

## Workflow and responsibility

The assistant researched primary sources, implemented incremental milestones,
ran real-data checks and automated tests, measured latency and tested relocation.
The owner approved portable Windows packaging, UTC day semantics and local Git
milestone commits. Initial implementation and verification were completed locally,
before the later public-publication request.

Public publication was subsequently explicitly requested by the owner: one
source snapshot commit in the primary project repository, plus
the complete portable application as a GitHub Release. Original development
history is retained on a local backup branch. Public release packages omit
internal Git metadata, live logs, credentials and temporary state.

The owner subsequently requested that the portable runtime and database also
appear in the public `main` branch. They are tracked with Git LFS where needed;
the owner approved replacing the published snapshot to keep `main` at one
commit. The earlier public snapshot and development milestones remain preserved
locally, and the existing portable Release remains available.

The owner then requested publication of the complete runnable project in
an additional repository. That
checkout includes the source, bundled runtime, Python packages and database
snapshot, with large binaries and the database stored through Git LFS. Logs,
credentials, caches and other transient local state remain excluded.

Synthetic measurements exist only in automated test fixtures. The application
does not substitute mock data when a source fails.

The AI-generated implementation and automated checks are not an independent human
review. The submitting developer remains responsible for understanding the
methodology, verifying the results, reviewing changes and being able to explain
and extend every submitted component during the technical interview.
