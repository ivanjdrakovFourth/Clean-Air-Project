# Verification record

Verified on **2026-10-16 UTC** on the shared Windows 11 development machine.
These are measured results, not promises for every machine or dataset.

## Delivered runtime

- Portable CPython **3.13.16**, SQLite **3.50.4**.
- Bundled Prometheus **3.15.0**, with four validated alert rules.
- Official runtime SHA-256 checks verified before extraction.
- A clean rebuild using all hash-pinned wheels in `requirements.lock` succeeded.
- Unused pip-generated executables containing build-host interpreter paths were
  removed. Native tools and Python `-m` entry points remain functional.
- Loaded Visual C++ runtime DLLs used by Python/Arrow were located in the bundle;
  the imported process did not require an installed `msvcp140` runtime.

## Real data

The actual EEA import and conditional refresh succeeded without registration.

| Item | Verified value |
|---|---:|
| Measurement files processed per complete cycle | 87 |
| Stored source observations after refresh | 216,769 |
| Derived physical-station/day records | 12,187 |
| EEA city candidates | 17 |
| Physical stations retained | 49 |
| Stations without an official city mapping, excluded | 12 |
| Queryable completed history | 2025-10-16 through 2026-10-15, inclusive UTC |

Source-validity, missing-value and negative-value exclusions were counted and
logged, not converted into zero pollution. Repeated imports did not duplicate
stored observations. Actual full-year results used three eligible PM2.5 cities
and thirteen eligible PM10 cities under the default coverage policy.

These counts describe this snapshot. Source updates can change counts, coverage
and results. They are not a claim of complete national monitoring.

## Automated tests

**Ruff passed; 60 offline tests passed**, including in the relocated project via
the packaged `Test-CleanAir.cmd`.

Coverage includes:

- station/day weighting and distinct physical station identity;
- exact daily-reference boundaries, custom thresholds and unrounded ranking;
- pollutant selection, ties and insufficient coverage;
- 75% hourly and 90% period eligibility boundaries;
- invalid/reversed/future dates and nonfinite or missing custom thresholds;
- SQLite persistence, WAL, indexes, revisions and source precedence;
- actual Parquet schemas, units, aggregation windows and missing identifiers;
- HTTP retries, conditional downloads and file-level replay;
- a real collector failure path followed by successful cached API queries;
- no third-party requests during comparisons;
- exact 36-month archive boundary, verified round-trip restoration, corruption
  rejection and no active-data pruning after archive verification failure;
- consistent database backup and handover ZIP contents;
- runtime checksum verification and safe extraction;
- explicit API errors, offline HTML/API documentation, output escaping and host validation;
- correct offline status even when the previous stored collection message
  described online updates.

One upstream **Starlette TestClient deprecation warning** is emitted for its
current HTTPX integration. It does not fail tests or affect production requests.
The dependency lock prevents an unreviewed framework upgrade; future upgrades
should address that test-client transition and rerun the suite.

## Complete HTTP performance

Target: **p95 below 300 ms**. Each case ran **100 successful, shape-validated
requests with four concurrent clients** against the actual HTTP service and real
stored data.

Machine: Windows 11 x64, Intel64 Family 6 Model 170, 22 logical CPUs, Python 3.13.
Other activity on the shared machine was not controlled.

| Pollutant | Period | HTTP p95 |
|---|---|---:|
| PM2.5 | One day | 66.571 ms |
| PM2.5 | Thirty days | 60.741 ms |
| PM2.5 | Twelve months | 60.715 ms |
| PM10 | One day | 78.140 ms |
| PM10 | Thirty days | 63.425 ms |
| PM10 | Twelve months | 136.557 ms |

**All six cases met the target.** No query-result cache was introduced to obtain
these results. Daily rollups and indexes were sufficient.

Full evidence:

- [Complete HTTP measurements](benchmark-http.json)
- [Isolated comparison-service measurements](benchmark-service.json)

The latter benchmark is deliberately separate: in-process timings are not
misrepresented as complete HTTP timings.

## Browser checks

- Loaded the actual service and checked desktop/mobile layouts at 1440, 390 and
  260 CSS pixels, with no document-level horizontal overflow.
- Submitted a full-year PM10 comparison through the form.
- Changed WHO guidance to a custom daily threshold of zero. The displayed winner
  and mean stayed **Vratsa / 11.88 ug/m3**, while days within the reference changed
  from **99.7% to 0.3%**.
- Confirmed reversed dates receive HTTP 422 and an accessible, useful error panel.
- Confirmed all automatically loaded application resources are same-origin.
- Confirmed offline status remains accurate after client-side status polling.

The example winner is only for that pollutant, date range, snapshot and policy.
It is not an unconditional statement about where to live.

## Monitoring and portability checks

1. Started the complete original application and monitoring; both returned ready.
2. Prometheus successfully scraped `/metrics`; its generated configuration and
   all four alert rules passed `promtool`.
3. Attempted handover during an active application process. It was rejected with
   an explicit workspace-lock error; the running application remained alive.
4. Generated a ZIP using `Prepare-Handover.cmd`; the packager verified its SQLite
   snapshot and every ZIP entry's CRC.
5. Extracted and renamed the project to a different folder containing spaces.
6. Ran the copied interpreter's `doctor` from a different working directory:
   it resolved the new workspace, reported database integrity `ok`, and retained
   the complete historical range.
7. Ran the **actual copied `Start-CleanAir.cmd`** with
   `--offline --no-browser --port 8001`, without using installed Python.
8. Confirmed offline API results remained correct and Prometheus automatically
   scraped `127.0.0.1:8001`, not the original port.
9. Ran all sixty tests through the copied Windows test launcher. The final
   offline-status correction was also checked in that relocated project.
10. Used the relocated UI on a mobile viewport and confirmed configurable
    comparisons worked with no external application resources.

The relocation check used the same Windows machine, not a newly provisioned VM.
Corporate execution policy and other hardware/OS combinations were not tested.
The final ZIP includes a SHA-256 sidecar; regenerate and recheck it after changes.

## Stop launcher follow-up

Added `Stop-CleanAir.cmd` and a workspace-local cooperative shutdown channel.
The stop-launcher update passed **68 tests**, with Ruff passing.
The existing upstream TestClient deprecation warning remains non-blocking.

An actual Windows start/stop exercise verified:

- a running application and Prometheus shut down cleanly, without forced process termination;
- running the stop launcher again succeeds when the application is already stopped;
- an orphaned interpreter from the workspace is terminated and its resources released;
- an unrelated interpreter outside the workspace remains running;
- SQLite is checkpointed, database integrity is checked, and critical runtime,
  database, log and monitoring files can be opened without sharing conflicts.

New regression tests also cover stale stop requests, shutdown metadata cleanup,
active workspace-lock refusal, existing Ctrl+C behaviour and explicit failed-start
errors. The handover-content test requires both the stop launcher and its helper.

## Complete Git LFS checkout follow-up

The runtime files and measurement database are now part of `main`, with large
binaries and SQLite stored through Git LFS. Runtime files retain their original
bytes without Git newline conversion. Python extension modules (`.pyd`) are
included; only disposable bytecode caches are excluded.

The current complete suite contains **69 passing tests**, with Ruff passing.
The additional test verifies that the Windows start launcher explains an
unresolved LFS pointer instead of trying to execute it as Python. The database
was checkpointed and integrity-checked before preparing the tracked snapshot.

Git LFS must retrieve its objects for a runnable Git checkout. The previously
published portable Release remains available as a complete ordinary ZIP.

## Version 2: Bulgarian station detail and expanded search

Verified on **2026-10-18** with **101 passing tests** and Ruff passing.
The upstream TestClient deprecation warning remains non-blocking.

### Source and real-data checks

- The official Bulgarian national map returned 32 station identities, addresses
  and coordinates, but no usable timestamped/pollutant-labelled readings.
  Its placeholder zeros were not imported.
- All 32 national sites were joined one-to-one to EEA station IDs at identical
  coordinates. Names and whitespace-normalized addresses were checked against
  the actual national response, not copied from an unofficial station list.
- The real EEA refresh completed all 87 files. The checked database then contained
  **218,071 observations** and 32 named national sites.
- The checked latest common PM10 hour compared **27 stations**, with the same
  2026-10-18 14:00-15:00 UTC interval for every included site.
- Historical station view, original city view, cleanest day, cleanest hour,
  reference-day shares and earlier-history improvement all returned real results.

### Correctness and compatibility

New tests cover optional/paired dates, stale/future readings, common-hour cohorts,
physical-station method deduplication, station addresses, explicit location scope,
alternate ranking definitions, exact day weighting with unequal reporting,
matching baseline station cohorts, real zero versus placeholder zero, result
limits and no fabricated hourly history.

Schema 1 migration makes a verified backup before adding station metadata and
the hourly index. Tests prove preserved observations, idempotent migration and
no continuation after backup failure. New archives preserve station metadata;
older archive headers without it remain restorable.
An active old application holds the shared workspace lock and prevents migration
before any schema change. Shutdown, storage release and migration reuse that lock.

### Browser and monitoring

Actual browser checks verified:

- initial blank dates, PM10 station view, collapsed settings and a clearly labelled
  "Cleanest measured station" answer with site address and reported UTC hour;
- changed-filter notice, historical submissions and original city averages;
- custom thresholds not changing a lowest-average winner;
- distinct reference-day/improvement titles and button wording;
- cleanest day/hour, station selection and explicit incompatible/no-hour errors;
- no document-level horizontal overflow at 1440, 900, 390 and 260 pixels, with
  advanced settings both open and closed.
- automatic refresh of an untouched current-results page after collection
  finishes, without discarding unsent date selections.

Bundled Prometheus remained healthy, scraped the application and passed
`promtool` configuration/rule validation. No runtime AI API was added.

### Performance, including a failed check and its correction

The first concurrent full-year PM10 HTTP test exceeded the 300 ms engineering
target. Investigation found avoidable Python-side daily materialization; daily
aggregation was moved into SQLite without changing its weighting/coverage rules.
Query-plan inspection also removed a quadratic join in the advanced hourly search.

The final HTTP benchmark used **100 requests per case, four concurrent clients**:

| Pollutant | One day p95 | Thirty days p95 | Twelve months p95 |
|---|---:|---:|---:|
| PM2.5 | 80.609 ms | 72.939 ms | 72.494 ms |
| PM10 | 74.580 ms | 73.106 ms | 88.210 ms |

All six normal location-comparison cases met the 300 ms target. A wide advanced
hour search is more work: the checked full year evaluated 152,804 station/hour
combinations, returned the disclosed best 50 and took about **0.98 seconds**
in-process. A one-day hourly search took about **20 ms**. Those exploratory
hour-search timings are not presented as four-client p95 measurements.

See the separately named version-2 benchmark JSON files for the measured output.
These are hardware/load/snapshot-specific results, not universal latency promises.
The additional latest-hour HTTP benchmark achieved **70.110 ms p95** over 100
requests with four concurrent clients. An exploratory full-year hourly HTTP
request took **1.805 seconds** while other benchmark activity was present;
this is a one-off advanced-query measurement, not a p95 claim.

- [Normal HTTP benchmark](benchmark-v2-http.json)
- [Comparison-service benchmark](benchmark-v2-service.json)
- [Current and exploratory advanced HTTP checks](benchmark-v2-expanded.json)

### Portable package and small-update deployment

The actual version-2 release ZIP was extracted and renamed to another folder.
Its bundled doctor reported database integrity `ok` and 32 verified sites, and
all **101 tests** passed through the copied Windows test launcher. The copied
start/stop scripts also ran successfully from outside that folder on port 8001;
Prometheus registered and scraped that changed target, and shutdown released files.

The Git update intentionally leaves existing runtime and committed SQLite LFS
objects unchanged. Existing complete installations receive source/configuration
changes with an ordinary fast-forward pull; startup backs up and migrates their
own local databases. Updated full database contents are in the separate portable
ZIP, not forced over a colleague's working measurement history.
