# Clean Air Finder - Version 3

**Which measured Bulgarian city had the cleanest air for the selected time?**

A portable Windows application with source-specific local history, six
pollutants, source comparisons, an in-app Data Explorer, friendly monitoring,
configurable alerts and light/dark themes.

**Repository:** [ivanjdrakovFourth/Clean-Air-Project](https://github.com/ivanjdrakovFourth/Clean-Air-Project)

**Complete portable download:** [Version 3.0.0 release](https://github.com/ivanjdrakovFourth/Clean-Air-Project/releases/tag/v3.0.0).
Use the prepared Windows ZIP, not GitHub's generic source ZIP, for an
extract-and-run package without a Git LFS installation.

## 1. Start and stop

Extract the complete portable folder on **Windows 10/11 x64**, then double-click
[Start-CleanAir.cmd](Start-CleanAir.cmd), or:

```powershell
.\Start-CleanAir.cmd
```

Open **http://127.0.0.1:8000**. Keep the console running. Stop with
[Stop-CleanAir.cmd](Stop-CleanAir.cmd) or Ctrl+C before moving/packaging files.

At startup, an **OK-only information window** explains the automatic source
checks. OK acknowledges the notice; collection has already started and does not
wait for your click. Each enabled source checks for changes and fills missed
dates, including after days or months without running the app. New records keep
being saved while it runs.

Each source reports its own completion in an in-app notification and persistent
Alerts history. Missing credentials, unavailable feeds, failed checks and stale
provider readings are not falsely reported as up to date. In Monitoring, you
can also enable optional Windows notifications for automatic source updates.

The package brings its own **Python, SQLite engine, Python packages and
Prometheus**. No Docker, Node.js, Python installation, database server or cloud
account is required to run it. Node/Playwright used for development validation
are not application dependencies or included runtime requirements.

Use a writable local folder, not a network/synchronized drive, and do not run
inside a ZIP. Windows/organization executable policies still apply.

Offline use:

```powershell
.\Start-CleanAir.cmd --offline
```

Stored-source searches still work. Provider collection is disabled; the UI does
not override offline mode when a refresh button is clicked.

## 2. The normal interface

| Menu | What you can do |
|---|---|
| **Compare** | Choose a source or several sources, pollutant and optional historical dates. See the cleanest measured city and its contributing monitor names, supplied addresses and coordinates. |
| **Monitoring** | Inspect each collector, freshness, coverage by city/pollutant, quota/backfill progress, Prometheus graphs/health alerts and maintenance outcomes. Configure an encrypted local OpenAQ key and request collection. |
| **Alerts** | Create/edit/pause/delete source-specific city and health rules; inspect persistent events; optionally request Windows desktop notifications. |
| **Data** | Browse actual source tables with filters, pagination and CSV export. Queue source-specific backup, retention and verified archive restore operations. No SQL required. |
| **API** | Read local endpoint documentation and generated OpenAPI JSON. |
| **Sun/moon** | Switch light/dark themes. The preference is saved in the browser; system preference is the initial default. |

**Blank dates** mean the latest common recent reported hour, not instantaneous
sensor streaming. Set both dates for historical UTC days; equal dates mean one
completed day. Changing a filter requires pressing Search; the page marks an
unsent selection.

## 3. Sources really are separate

| Selection | Own database | What it contains |
|---|---|---|
| **EEA** | `var\data\clean_air.sqlite3` | Bulgarian official observations reported through the EEA, including validated/provisional history. |
| **OpenAQ** | `var\data\openaq.sqlite3` | Separately downloaded OpenAQ observations with upstream provider, license and monitor class. Includes permitted reference/community sensors according to configuration. |
| **Bulgarian direct** | `var\data\bg_direct.sqlite3` | Reserved for genuine direct-source measurements/restored history. The checked government endpoints supplied site metadata but no usable concentration feed, so no fake history is seeded. |
| **Compare selected sources** | Queries each selected store | Separate answer/error panels, top-result comparison and matched-station diagnostics; never a silent blend or fallback. |

Station/city is a **geographical view**, not a source. Selecting OpenAQ never
reads EEA history. Selecting an unavailable source returns an explicit status
instead of borrowing another database.

Some OpenAQ locations are republished **EEA observations**. Agreement there is
not independent confirmation. Numeric station comparisons use the same reported
hour or exactly shared qualifying historical days. Community locations without
a verified shared identity are not guessed into a match.

Source checks and limits: [DATA_SOURCES.md](docs/DATA_SOURCES.md).

### Prefilled history and future OpenAQ updates

The version 3 delivery contains independently collected EEA and OpenAQ history
for the **last 12 months**, subject to each monitor's real lifetime, source gaps,
quality and reuse permissions. This satisfies the assignment's minimum history;
36 months is the automatic cold-archive boundary, not a requirement to download
36 months initially. Missing source readings are never manufactured.

The portable Release ZIP contains sanitized working database snapshots. The Git
checkout carries versioned, checksummed snapshots under `seed-data`, stored
through Git LFS. Startup imports those into the matching working store once,
preserving equal/newer local revisions and personal alert settings. An updated
seed is merged, not copied over a colleague's live database.

**An OpenAQ key is not needed to search the bundled OpenAQ history.** It is
needed for future OpenAQ updates on that computer. EEA updates remain anonymous.
The direct Bulgarian portal is explicitly unavailable as a concentration feed;
its reserved database is not falsely advertised as populated.

Configure a key through **Monitoring -> OpenAQ credential setup**, or run:

```powershell
.\Configure-OpenAQ.cmd
```

Alternatively, provide `OPENAQ_API_KEY` in the process environment. Environment
configuration takes precedence. Do not put a key into tracked TOML, a Git commit,
an alert or a screenshot.

The local credential is encrypted with **Windows user-bound DPAPI** under
`var\secrets`; it is excluded from Git, source databases and normal handover ZIPs.
Another computer/user must configure its own key. No key is echoed back by the UI.

The first import backfills available supported records in the configured **12-36
month window**, subject to sensor lifetimes, quality, permissions and provider
availability. It uses UTC hourly values, not OpenAQ's local-day aggregates.
Source names never stand in for concentration records.

Later starts retain the original requested history floor and resume missing
time, rather than silently skipping a gap that has become older than a moving
12-month boundary. Stored history grows; records older than 36 months move to
verified source-specific cold archives instead of being discarded.

OpenAQ's free published allowance is **60 requests/minute, 2,000/hour**. The app
paces below those limits, persists checkpoints and cooldown state, reduces
timed-out windows and retries without deleting another source's history.
Full initial collection can take hours; **Monitoring shows the actual progress**.

Normal historical OpenAQ comparisons wait for completion. The explicit
**Allow partial cached history** setting can inspect already loaded eligible
records, with a prominent warning and normal coverage checks. It does not claim
the source has finished collecting. Data Explorer can always show actual cached
rows, including a partially collected window.

## 4. Pollutants and what "cleanest" means

Supported mass-concentration pollutants are **PM2.5, PM10, NO2, O3, SO2 and CO**,
where the chosen source actually supplies them.

All concentrations are normalized to **ug/m3**. A reported mg/m3 is multiplied
by 1,000. Gas ppm/ppb values are **not** converted using guessed pressure,
temperature or molecular assumptions. Missing/unsupported source coverage is
disclosed.

The normal definition is **lowest pollution concentration = cleaner measured
air**, not a quality score where higher is better.

Search settings also offers:

- the highest share of valid days within a daily reference;
- greatest reduction versus stored earlier history using the same station cohort;
- cleanest day/hour inside historical dates, optionally restricted to a location;
- reference/government-only versus all source-provided monitors.

Those are different questions and their titles/criteria say so. Relative
improvement is not necessarily the cleanest absolute air.

Default eligibility is 75% valid hours for an hourly-derived day and 90%
qualifying days per station/period. Provider-valid daily capture can be unknown.
Missing records never become zero. Provider city groupings and station placement
can differ; the actual contributing addresses are displayed.

WHO daily references: PM2.5 15, PM10 45, NO2 25, SO2 40 and CO 4,000 ug/m3.
**O3 has no WHO 24-hour guideline here**; the eight-hour value is not falsely
applied to daily means. Choose a custom daily reference for an ozone
reference-day ranking. Daily references are not applied to hourly results.
This is not WHO/legal compliance certification or an absence-of-risk guarantee.

Details: [METHODOLOGY.md](docs/METHODOLOGY.md).

## 5. Monitoring and alerts are different

### System/data monitoring

Prometheus is bundled and normally starts with the app. **Monitoring** presents
its real graphs/active alerts plus local source/cache health, so opening the
advanced Prometheus interface is optional.

Health distinguishes:

- failed/partial collection;
- missing credentials or unsupported feeds;
- normal initial backfill and quota cooldown;
- stale/future timestamps;
- source-excluded readings and missing coverage;
- city/pollutant combinations a source never monitored.

An HTTP 200 alone is not a guarantee of good measurement data. A never-monitored
city is not automatically a broken collector. Empty graphs are not zero latency.
Local source checks remain visible if Prometheus is unavailable, with an explicit
warning that time-series monitoring could not be retrieved.

### Personal air-quality and source alerts

In **Alerts**, choose sources, pollutant, city/condition, latest-hour or rolling
complete-day window, definition and trigger.

Rules include cleanest-city changes, cleaner/dirtier city concentrations,
at/below/above crossings, changed completed searches, missing city data, stale
sources, unavailable sources and collection failure.

Each source has its own persisted baseline. The first assessment normally
sets a baseline rather than inventing a change. Partial ongoing imports do not
trigger ranking-change alerts. Repeated unchanged results do not create duplicates.

Events remain in `var\data\alerts.sqlite3`. Optional desktop delivery uses
Windows' built-in notification facilities and is requested only if enabled by
the user. Windows focus/session/policy may suppress a popup: **requested does not
mean seen**. The in-app history records requested/failed/uncertain delivery.
The app must remain running; it cannot notify after its own process has stopped.
External on-call paging/Alertmanager delivery is not configured.

Personal rules/history and credentials are excluded from ordinary shared
portable packages. Colleagues configure their own notifications.
Automatic source-check outcomes are also kept separately from measurement
stores. Their optional Windows delivery preference is local, not shared.

## 6. Data, backups and archives through the UI

**Data Explorer** reads only allowlisted tables in the selected source store.
Dates, city, pollutant and station are bound filters; there is no arbitrary SQL
execution endpoint. Raw excluded observations are distinguishable from eligible
daily means. CSV exports include all filtered rows, not only the displayed page,
and neutralize spreadsheet-formula strings.

**Backup** uses SQLite's consistent backup API and verifies integrity. The
verified file can be downloaded from the job outcome.

**Archive** verifies compressed records older than 36 calendar months before
removing active copies. Each source's archives remain separate:

```text
var\archives\                EEA, including legacy archives
var\archives\openaq\         OpenAQ
var\archives\bg_direct\      genuine Bulgarian-direct archives, if supplied
```

**Restore** checks source identity, SHA-256, record counts and relationships.
A mismatched source is rejected. Verified restored windows remain queryable in
that source; no other source is copied over them. Restoration pauses automatic
retention for 24 hours.

Operations are queued, source-locked and show waiting/running/completed/failed
states. A busy source does not block another source's backup. The Monitoring
menu also runs offline automated checks and measured local query benchmarks.
Synthetic operational tests are not production measurements.

Schema 1/2 databases are backed up before schema 3 expands the pollutant
constraint and preserves measurements. Stop before upgrading; an active old
instance prevents migration.

## 7. Handover and Git updates

Create a runnable package without Git/LFS caches:

```powershell
.\Stop-CleanAir.cmd
.\Prepare-Handover.cmd --without-git
```

It refuses unfinished EEA/OpenAQ backfills, sanitizes each source snapshot,
includes source archives and current
runtime/code/docs, and excludes local keys, personal alert state, logs and
temporary files. **Do not manually share the whole working folder with
credentials/personal state included.**

Existing complete checkout:

```powershell
.\Stop-CleanAir.cmd
git pull --ff-only https://github.com/ivanjdrakovFourth/Clean-Air-Project.git main
git lfs pull
.\Start-CleanAir.cmd
```

Version 3 adds new LFS data snapshots. `git lfs pull` ensures both runtime and
seeds are real files rather than pointers (normal LFS-enabled checkouts usually
download them automatically). Local databases migrate and keep collecting
independently. Initial clones also need Git LFS:

```powershell
git lfs install
git clone https://github.com/ivanjdrakovFourth/Clean-Air-Project.git
Set-Location .\Clean-Air-Project
git lfs pull
.\Start-CleanAir.cmd
```

Do not hard-reset a colleague's collected data or resolve unrelated Git histories
by deleting their work. A fresh clone into another folder is the safe alternative.

## 8. Architecture, configuration and verification

```text
Browser UI / JSON API
  +-- selected-source query -> EEA SQLite / OpenAQ SQLite / direct-source SQLite
  +-- comparison -> separate answers and matched-window diagnostics
  +-- Data Explorer -> read-only filtered records and CSV
  +-- Monitoring -> source health + actual Prometheus API
  +-- Alerts -> local rule/event store + optional Windows delivery
  +-- maintenance queue -> verified source-specific operations

Independent collection workers -> original providers -> own SQLite checkpoints
Bundled Prometheus -> /metrics -> platform alert rules
```

Settings are in `config\settings.toml` and reference facts in
`config\profiles.toml`. Credentials are never stored there.

Run offline lint/tests:

```powershell
.\Test-CleanAir.cmd
```

CLI equivalents remain available with explicit `--source eea`, `--source openaq`
or `--source bg_direct` for status, doctor, sync, backup, archive, restore and
benchmark operations. No real direct connector is fabricated.

More: [source research](docs/DATA_SOURCES.md), [methodology](docs/METHODOLOGY.md),
[operations](docs/OPERATIONS.md), [licenses](docs/THIRD_PARTY.md),
[AI usage](docs/AI_USAGE.md) and [verification](docs/VERIFICATION.md).

The repository also preserves the supplied PDF document exports unchanged for
completeness. They are historical exports, not regenerated version 3 manuals;
the current Markdown documents and application interface are authoritative.

With more time: obtain a complete licensed direct Bulgarian measurement feed;
review geographical grouping/station types with domain experts; add external
on-call delivery; test clean Windows VMs and larger workloads. Separate provider
errors/coverage are preserved rather than hidden to make comparisons look complete.
