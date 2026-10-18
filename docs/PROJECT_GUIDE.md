# Clean Air Finder - Understand it, demonstrate it, explain it

> **Version 2 update (2026-10-18):** this guide records the original city-first
> implementation. The current UI defaults to named Bulgarian official stations
> and a latest-common-hour search when dates are blank. Search settings retains
> city averages and adds reference-day/improvement definitions and cleanest
> day/hour searches. Schema 2 adds a tenth table, `station_metadata`, without
> removing the original tables. For the current rules and UI flow, read
> [README](../README.md), [METHODOLOGY.md](METHODOLOGY.md) and
> [DATA_SOURCES.md](DATA_SOURCES.md). The original city SQL examples remain useful;
> select `view=cities` and `objective=average` when reproducing them.

> **The whole project in one sentence:** a background collector saves real air
> measurements locally; the website compares that saved history; the Windows
> package brings its own Python, database engine and monitoring.

This is a learning guide, not a script to memorize. Read the short explanations
first. Open the expandable reference boxes only when you need them.

**Best reading experience:** in VS Code, press **Ctrl+Shift+V** to open Markdown
Preview. The reference boxes are collapsed there, so you do not face the whole
document at once.

## Choose your reading route

| If you have... | Read... | You should be able to explain... |
|---|---|---|
| 5 minutes | Sections 1-3 | What it does, why these choices, why installation is unnecessary. |
| 15 more minutes | Sections 4-6 | Where data comes from, how the winner is calculated, every application control. |
| A technical interview | Sections 7-10 | Tables, SQL, failure handling, trade-offs and a practical demonstration. |

**Contents**

1. [The one-minute explanation](#1-the-one-minute-explanation)
2. [How we developed it and why](#2-how-we-developed-it-and-why)
3. [Why unzip-and-run works, including SQLite](#3-why-unzip-and-run-works-including-sqlite)
4. [What happens end to end](#4-what-happens-end-to-end)
5. [The complete application UI](#5-the-complete-application-ui)
6. [Useful feature combinations](#6-useful-feature-combinations)
7. [Understand the database](#7-understand-the-database)
8. [Your SQL demonstration lab](#8-your-sql-demonstration-lab)
9. [Interview questions and short answers](#9-interview-questions-and-short-answers)
10. [Demonstrate it and be honest about its limits](#10-demonstrate-it-and-be-honest-about-its-limits)

Commands assume PowerShell is open in the project root. No application code or
measurement data was changed to create this guide.

---

## 1. The one-minute explanation

### What problem am I solving?

A family considering relocation wants to compare Bulgarian cities over a chosen
historical period, using actual measurements rather than invented data.

The user chooses dates, PM2.5 or PM10, and a daily reference. The application
finds the **lowest average measured pollution among cities with enough data**.
It also explains coverage, stations, exclusions and limitations.

### Five facts to remember

1. **Collecting and searching are separate.** Searching never calls the external provider.
2. **Lower concentration wins.** We do not invent a weighted multi-pollutant score.
3. **A winner is not automatically healthy.** It can still exceed the selected reference.
4. **Missing data is not zero pollution.** Coverage determines whether a station qualifies.
5. **Portable means bundled, not dependency-free.** The dependencies are already in the ZIP.

### A short interview introduction

> "This is a local-first air-quality comparison service. It imports official EEA
> measurements into SQLite and builds daily aggregates. The user selects a
> historical period and pollutant; the API ranks eligible cities by their average
> concentration and explains data quality. I chose a portable Windows delivery
> so a reviewer can extract it and run one launcher without installing Python,
> a database server or Docker."

**Be accurate about your role:** development was AI-assisted. Explain the choices
you approved, the implementation you now understand and the checks performed.
Do not claim an independent human review or that you personally typed every line.
The record is in [AI_USAGE.md](AI_USAGE.md) and [PROMPT_HISTORY.md](PROMPT_HISTORY.md).

## 2. How we developed it and why

The order was: **understand -> verify real data -> build the smallest complete
flow -> add reliability -> package -> test -> publish**.

<details>
<summary><strong>Open the development story and decision/trade-off ledger</strong></summary>

### The development story

| Stage | What happened | Why that order mattered |
|---|---|---|
| Understand the brief | Separated the business question, data obligations and operational requirements. | A dashboard alone would not satisfy collection, history, retention and resilience. |
| Research the meaning of "cleanest" | Checked WHO references, the European AQI and real data sources. | Daily, annual and hourly thresholds are not interchangeable. |
| Simplify the design | Chose Python, SQLite and server-rendered HTML instead of a separate frontend build and database server. | Fewer moving parts for a focused local application. |
| Prove real data access | Examined actual Bulgarian EEA Parquet files, metadata, intervals and validity flags. | The calculation had to follow the real source format, not assumptions. |
| Build one complete flow | Import, persist, aggregate, compare, expose an API and display results. | Proved the core answer before presentation and packaging work. |
| Add operational behaviour | Retries, checkpoints, monitoring, backups, archiving and explicit errors. | A provider failure must not erase useful stored history. |
| Make it portable | Bundled Windows Python, packages and Prometheus; tested a moved folder. | Copying a normal virtual environment would not reliably be relocatable. |
| Verify and publish | Tested calculations, failures, real-data latency and launchers; published source and a portable Release. | Evidence is stronger than saying "it should work." |
| Add the full Git checkout | Put the runtime and database into `main`, using Git LFS for large files. | The owner wanted the portable files in Git, not only in Releases. |

### The decisions you should be able to defend

| Decision | Reason | Trade-off |
|---|---|---|
| Python | Suitable for HTTP, typed Parquet data, SQL and a small API. | Runtime and packages still need managing; we bundle them. |
| FastAPI + Uvicorn | Validation, HTTP service and generated OpenAPI schema. | It is a Python server, not a static website. |
| HTML/Jinja2 + small JavaScript file | No React/Node build system is needed for this UI. | Less suitable for a much larger, highly interactive application. |
| Standard-library SQLite access | Transactions and SQL without another server or ORM. | We write and maintain the SQL explicitly. |
| SQLite with WAL | Fits a local, mostly-read workload with one collector. | Not a multi-server database; writers are serialized. |
| EEA measurements | Official reporting, validated history, provisional current data, anonymous access and documented reuse terms. | Reporting delays, gaps and unequal pollutant coverage remain. |
| UTC days | Align validated daily history with hourly observations. | These are not Bulgarian local-midnight days. |
| Equal station/day weights | Simple, inspectable calculation; extra stations do not give a city extra country-ranking weight. | Not population-weighted exposure; station placement still matters. |
| WHO or custom daily reference | Gives interpretable context without changing what "lowest average" means. | Does not certify health or legal compliance. |
| Native Windows ZIP | Reviewer can run the packaged application without installing a stack. | Windows x64 only; not a universal Linux/macOS package. |
| Local Prometheus | Real observability with no SaaS registration. | Another bundled process; external paging is not configured. |
| Git LFS | Stores large binaries/database that ordinary GitHub files cannot accommodate. | Git checkouts need LFS and use its storage/bandwidth quotas. |

**Why not OpenAQ?** It was a reasonable alternative; its documented API uses an
API key. Free registration was allowed by the brief, so that is not inherently
wrong. EEA was chosen after confirming its actual Bulgarian files, historical
and provisional datasets, access and licensing. We did not prove that every
alternative has worse coverage.

</details>

## 3. Why unzip-and-run works, including SQLite

### What the assignment required versus what we chose

The assignment required **easy deployment and one documented run command**.
It did not mandate Docker or a particular cloud.

The owner additionally wanted a folder that could be zipped and handed to
colleagues. Therefore, we chose **a complete Windows package**, not a source
checkout that expects the reviewer to install a toolchain.

### What is actually bundled?

| Component | What it does |
|---|---|
| [Start-CleanAir.cmd](../Start-CleanAir.cmd) | Finds the bundled interpreter using the launcher's own location. |
| [Python executable](../runtime/python/python.exe) and [Python DLL](../runtime/python/python313.dll) | Run Python independently of the user's installed Python. |
| [Standard-library ZIP](../runtime/python/python313.zip) | Contains Python's standard-library modules. |
| [Bundled packages](../runtime/packages) | FastAPI, Uvicorn, HTTPX, Jinja2, PyArrow, Pydantic, monitoring client and test tools. |
| [Prometheus](../runtime/prometheus) | Runs the monitoring service without Docker or a cloud account. |
| [Data directory](../var/data) | Contains the separate, persistent measurement database. |

The delivered runtime is CPython 3.13.16. The original machine's Python 3.12.10
was checked and used for building; it was not replaced.

### How can moving the folder still work?

The launcher uses its own directory, application paths come from the source
file's location, and [python313._pth](../runtime/python/python313._pth) contains
relative search paths:

```text
python313.zip
.
..\packages
..\..
import site
```

That tells embedded Python where to find its library, packages and the project.
There is no copied virtual environment depending on an old user's absolute path.
Startup does **not** run pip or download Python.
Normal users do not need pip. A developer rebuilding the runtime uses an
installed Python/pip to prepare the bundled packages ahead of distribution.

The builder verifies official binary checksums and hash-pinned wheels. Exact
versions and notices are in the [runtime manifest](../runtime/manifest.json)
and [third-party documentation](THIRD_PARTY.md).

### Is SQLite part of Python, or separate?

**Both descriptions refer to different layers:**

```text
Python application
       |
       v
sqlite3                  Python standard-library API
       |
       v
_sqlite3.pyd             Compiled bridge into the engine
       |
       v
sqlite3.dll              SQLite database engine, bundled here
       |
       v
clean_air.sqlite3        Separate file containing our actual data
```

The actual data file is [var/data/clean_air.sqlite3](../var/data/clean_air.sqlite3).
The bundled engine is [sqlite3.dll](../runtime/python/sqlite3.dll), currently
SQLite 3.50.4; [_sqlite3.pyd](../runtime/python/_sqlite3.pyd) is Python's bridge.

| Question | Answer |
|---|---|
| Do I install a separate database server? | No. The engine runs inside the Python process. |
| Do I run `pip install sqlite3`? | No. The Python interface is in the standard library. |
| Are the measurements inside Python's program files? | No. They live in the separate database file. |
| What survives a restart? | The database, backups and archives remain on disk. |
| Does each search create a new database? | No. Connections open the same persistent file. |
| Why do WAL/SHM files sometimes appear? | They support SQLite transaction/concurrency handling; they are not extra databases. |
| Can I copy an open database file by itself? | Do not. Use the backup API or stop/checkpoint it first. |

**Remember:** Python includes access to SQLite. It does not include your
application's measurement data; the application collects and stores that data.

### The two download routes

- **Release ZIP:** extract and run. No Python, Docker, SQL-server or Git LFS installation is needed.
- **Git checkout:** Git LFS must retrieve the large files first. A small LFS pointer is not an executable.

Windows 10/11 x64, a writable local folder and a browser are assumed. Corporate
device policy may require approval. Network/synchronized storage is unsuitable
for this live WAL database. This is not an installed Windows service.

## 4. What happens end to end

### A. Startup: from double-click to a working page

1. The launcher invokes bundled Python with `-m clean_air serve --open-browser`.
2. The CLI reads TOML settings, validates them and prepares workspace-local paths/logs.
3. SQLite schema version and WAL mode are checked/initialized.
4. A workspace lock prevents another application/manual writer using this installation.
5. Bundled Prometheus starts and must become ready.
6. FastAPI/Uvicorn starts; a small shutdown-control channel records the server identity.
7. The API lifespan starts one collector if online collection is enabled.
8. The browser opens after the live endpoint responds.

With an existing database, stored history is usable immediately. With an empty
database, the page explains backfill progress; it does not invent a winner.

```text
Browser ---- local HTTP ----> FastAPI ---- SQL ----> SQLite
                                  ^
                                  |
                         one collector thread
                                  |
                                  v
                     EEA metadata and Parquet

Prometheus ---- scrapes /metrics ----> application
```

### B. Collection: how measurements reach disk

Think of this as an independent supply chain: discover, download, validate,
store, summarize and checkpoint.

<details>
<summary><strong>Open the collection pipeline step by step</strong></summary>

| Step | What happens |
|---|---|
| Discover | Fetch official Bulgarian cities and sampling-point mappings. Metadata normally refreshes daily. |
| Inventory | Request PM2.5/PM10 file URLs for E1a and E2a. The inventory is CSV; metadata is JSON. |
| Download | Download typed, compressed Parquet files; use ETags to avoid reprocessing unchanged files. |
| Filter | Keep the configured historical window, supported pollutants/units and aligned hour/day intervals. |
| Validate | Reject malformed schemas/unknown units; exclude missing, nonfinite, negative or source-invalid values from averages. |
| Store | Upsert source observations and retain provenance/quality fields. |
| Aggregate | Rebuild daily physical-station values for affected stations. |
| Checkpoint | Commit that file's observations, aggregates and import checkpoint together. |
| Finish | Record cycle success, source freshness and history readiness; attempt retention maintenance. |

The loop waits the configured 3,600 seconds **after a cycle finishes**. It is
approximately hourly, not a continuous stream or an operating-system scheduler.
Nothing is collected while the application is closed or in offline mode.

</details>

<details>
<summary><strong>Source details an interviewer may ask about</strong></summary>

- Provider: [EEA Air Quality Download Service](https://eeadata.github.io/AQ.Portal/pages/download-data.html).
- API base: `https://eeadmz1-downloads-api-appservice.azurewebsites.net`.
- Country: `BG`; pollutant code `6001` = PM2.5, `5` = PM10.
- Dataset selector **2** = validated E1a; **1** = provisional E2a.
- E1a files use `airquality-p-e1a`; current E2a files use `airquality-p`.
- Forecast/modelled and gap-filled datasets are not used for ranking.
- Parquet is a typed columnar file format, not a JSON response of measurement rows.
- Relevant source fields include Samplingpoint, Pollutant, Start, End, Value,
  Unit, AggType, Validity, Verification, ResultTime and DataCapture.
- Source timestamps without a timezone are interpreted as UTC. SQLite stores
  their integer Unix seconds; daily dates are ISO strings.
- Recognized mass-concentration unit spellings are treated as `ug/m3`.
  The code rejects other units; it does not silently convert arbitrary units.
- Source decimal concentrations become floating-point values for storage/calculation.
- A verified probe found server date filters excluding overlapping current
  files. Inventory requests therefore omit them and timestamps are filtered locally.
- A source file can span many years; we do not retain all of those years just
  because they were downloaded. Transient Parquet files are removed afterward.
- Source terms are documented as CC BY 4.0, copyright European Commission.
  Attribution and the fact that we filter/aggregate the data are provided.

</details>

### C. Searching: no external call in the user's request

1. Validate dates, pollutant, profile and custom threshold.
2. Check that initial history exists and the requested dates are stored.
3. Read indexed daily aggregates from local SQLite.
4. Select stations that meet the period's coverage policy.
5. Calculate city daily means, then period means.
6. Rank the unrounded means and return all tied winners.
7. Add reference statistics, exclusions, source/freshness and method details.
8. Render HTML, or return the same comparison as JSON.

With JavaScript, the form requests an HTML results fragment from `/results`.
Without JavaScript, the form's ordinary GET submission still returns a complete
page. The JSON route is `/api/cleanest`. All use the same comparison function.

### D. The calculation, without mystery

**Hourly-derived station day**

- A UTC day has 24 expected hours.
- Default minimum: 18 valid distinct hours, or 75%.
- Mean = sum of observed valid values / observed valid hours.
- Missing hours are not zero.

**Provider-reported daily value**

- Prefer a valid reported daily mean when available.
- Preserve its reported capture percentage if supplied.
- If underlying hourly capture is unspecified, keep it unknown (`NULL`), not 100%.

**Physical station and source precedence**

- Multiple sampling methods at the same station do not receive duplicate city weight.
- For the same observation key, validated E1a wins over provisional E2a.
- Within that source class, the latest source result timestamp wins.
- An invalidating revision can remove a formerly valid value from the daily calculation.
- The source Verification flag is stored, but dataset priority is a separate rule.

**Period**

```text
qualifying station means -> city mean for each day -> mean of city daily means
```

A station needs at least `ceil(number_of_requested_days * 0.90)` qualifying days.
That is **27 days for a 30-day request**, or **329 for a 365-day request**.
The city must also meet the period coverage requirement.

| Illustration only | Day 1 station values | Day 2 station values | Period mean |
|---|---|---|---:|
| City Alpha | 10 and 30 -> city mean 20 | 20 and 40 -> city mean 30 | 25 |
| City Beta | 26 -> city mean 26 | 26 -> city mean 26 | 26 |

Alpha wins. It is still above a PM2.5 daily reference of 15 on both days.
Changing the reference to 30 changes the descriptive within-reference count,
not the winning mean. These numbers illustrate mathematics, not mock runtime data.

Default WHO daily references are **15 ug/m3 for PM2.5** and **45 ug/m3 for PM10**.
The annual guidelines, 5 and 15 respectively, are not used to classify arbitrary
periods. The European AQI's hourly colour bands are deliberately not applied to
these period averages.

<details>
<summary><strong>Precision, policy and reproducibility</strong></summary>

- Raw means determine order. Display rounding does not.
- Ties use absolute tolerance `1e-9`, no relative tolerance. Alphabetical order
  makes display deterministic; it does not select one exclusive tied winner.
- Within-reference means city daily mean **less than or equal to** the threshold.
- Its percentage denominator is valid city days. Requested-day coverage is separate.
- The policy ID hashes reference/method settings, including the required day count.
  It is not a cryptographic hash of the entire measurement database.
- Reproduce a result with the same database snapshot, dates, pollutant, threshold
  and coverage settings. A last-success timestamp alone does not freeze the data.
- We compare available qualifying days; remaining gaps can still bias results.
- Stations are city-mapped monitoring stations, not a population-weighted or
  background-only exposure model.

</details>

### E. Failure, shutdown and retention

- Requests have bounded timeouts and retries for transport errors, 429 and 5xx responses.
  Defaults are a 90-second timeout and three attempts total, with bounded
  exponential delays between retryable attempts.
- Importing is **atomic per file**, not one transaction across the entire country.
  A partially failed refresh can expose new successful files alongside older ones.
- Failed files retain their last committed state and are replayed safely.
  Completed checkpoints survive a restart.
- Before any initial successful import, comparisons return an explicit not-ready response.
  Afterward, a provider failure does not remove the stored history.
- Collection success and measurement freshness are different; source age is monitored separately.
- The stop launcher requests cooperative shutdown, then handles leftover project processes,
  checkpoints SQLite and checks critical file locks. It does not indiscriminately kill Python.
- Records older than 36 months are verified into gzip JSON-lines archives before
  active copies are removed. Restoration verifies checksum/counts and rebuilds daily values.
  Deleting rows alone would not necessarily shrink SQLite's file; incremental
  vacuum and WAL checkpointing reclaim available storage. Restoration remaps
  source-file IDs by URL and checks foreign keys.
- **Known limitation:** automatic archiving runs after a successful collection
  cycle. Prolonged collection failures can delay it; a separate retention schedule is future work.

## 5. The complete application UI

This covers every application-owned navigation/form control. Cards and table
headings that are only displays are identified so you do not invent features.
External EEA/license websites are not application screens.

<details>
<summary><strong>Main page: every link, field, button, card and expandable panel</strong></summary>

### Main page

| What you can click/use | What it does | What to say in the interview |
|---|---|---|
| Keyboard "Skip to comparison" link | Jumps to the search section. | Basic keyboard accessibility is included. |
| Clean Air Finder name/logo | Returns to `/`, using the default search settings. | It is a home link, not a saved-search feature. |
| **How it works** | Scrolls to the method explanation; hidden on very narrow layouts. | Explains what the answer means without leaving the page. |
| **API** | Opens local API documentation in the same tab. | This is a web service as well as a UI. |
| **Monitoring** | Opens the configured local Prometheus address in a new tab. | Visible when monitoring is enabled. |
| Collection/status banner | Displays progress, freshness, failure or offline state; not a button. | JavaScript refreshes status about every 15 seconds. |
| **From** date | Browser date picker or typed date. | Minimum/maximum follow locally stored history. |
| **To (inclusive)** date | Selects the final included UTC day. | Today/incomplete UTC days and future dates are not accepted. |
| Browser calendar arrows/days | Navigate months and choose a date. | Exact calendar appearance depends on the browser/Windows locale. |
| **Compare by** | Select PM2.5 or PM10. | No silent pollutant substitution. |
| **Daily reference** | Select WHO 2021 or a custom reference. | A reference describes days; it is not the ranking formula. |
| Custom numeric field | Appears only for the custom profile; accepts finite values at or above zero. | Units are `ug/m3`; zero is allowed, negative/NaN/infinity are not. |
| **Find cleaner air** | Validates and runs the comparison; disables while waiting. | Changing a dropdown alone does not submit a new comparison. |
| Winner and four statistic cards | Display average, daily-reference counts, coverage and contributing stations. | They are not clickable buttons. |
| Eligible-city table | Shows rank, mean, coverage, station count, reference-day count and provisional station-days. | Column headings do not provide interactive sorting. |
| Station count in the table | Hover to see physical station IDs. | IDs are not separate map/detail-page links. |
| Wide ranking table on mobile | Scroll horizontally inside the table. | The rest of the page remains responsive. |
| **Cities excluded - see coverage and reasons** | Expands/collapses exclusion details. | Absence from a ranking is not proof of dirty air. |
| **Sources, station details and limitations** | Expands/collapses provenance, IDs, unknown coverage and warnings. | Makes the result auditable. |
| Source link in expanded details | Opens EEA documentation in a new tab. | Identifies where the measurements originated. |
| License link in details/footer | Opens CC BY 4.0 in a new tab. | Provides reuse attribution. |
| Footer **EEA** link | Opens official download documentation. | Not a live external data request made by the search. |
| Error panel | Explains the rejected request; a reason list can be expanded where provided. | Fix dates/profile/coverage or choose an explicitly different pollutant. |

Changing dates/pollutant/reference does not refresh old displayed results until
you submit. After submission the URL reflects the search; it can be copied or
reloaded. There is no account-based saved history.

For a tie, all winning names are shown, but the four cards describe the first
listed winner; the table contains each city's own statistics. Also, **100% period
coverage means every requested day qualified**, not that every hour was observed.

If initial backfill completes while the page is open, the status polling reloads
the page so comparison becomes available. A later failure/offline state does not
erase existing results. Offline mode does not update the source.

</details>

<details>
<summary><strong>API page: every link and what each endpoint shows</strong></summary>

### API documentation page

| Link/control | Destination and purpose |
|---|---|
| Brand / **Back to comparison** | Return to the main UI. |
| **OpenAPI JSON** | `/openapi.json`: generated machine-readable schema. |
| **Try the full stored period for PM10** | Opens a real JSON comparison for available dates. |
| **GET /api/status** | Progress, history dates, source timestamps, exclusions scope and worker state. |
| **GET /health/live** | Liveness and version. |
| **GET /health/ready** | 200 after initial history exists; 503 before it is ready. |
| **GET /metrics** | Prometheus exposition text, not a chart or raw measurement export. |

This is deliberately **not** a Swagger "Try it out" console. Local HTML docs
and OpenAPI JSON avoid external JavaScript/CDN requirements.

With the app running, the same service can be demonstrated from PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/cleanest?start=2026-09-17&end=2026-10-16&pollutant=PM10&profile=who_2021' | ConvertTo-Json -Depth 10
```

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/cleanest?start=2026-09-17&end=2026-10-16&pollutant=PM2.5&profile=custom&threshold=10' | ConvertTo-Json -Depth 10
```

Use dates stored in your copy and adjust the port if configured differently.
These GET requests read local data; they do not trigger a source download.

</details>

<details>
<summary><strong>Monitoring: what to use in Prometheus</strong></summary>

Monitoring normally opens at `http://127.0.0.1:9090`. It is a separate generic
tool; this guide covers the functions useful for our project, not every
administrative feature of Prometheus.

- **Query/expression browser:** enter a metric/expression and execute it; view
  the returned value or time graph.
- **Targets:** confirm the application scrape target is up and inspect scrape errors.
- **Alerts/rules:** inspect pending/firing conditions.
- **Configuration/status:** inspect the loaded scrape settings and server details.
  Navigation grouping can differ between Prometheus UI versions.

Useful expressions:

```promql
up{job="clean-air-finder"}
```

```promql
clean_air_collection_healthy
```

```promql
clean_air_source_age_seconds / 3600
```

```promql
clean_air_worker_alive
```

```promql
rate(clean_air_http_requests_total[5m])
```

`up=1` means scraping works, not that all air measurements are fresh.
The collection-health gauge reflects the last attempt's status; inspect readiness,
progress and source age too. Offline mode legitimately has no collector thread.

The four alert rules cover application down, collection failure, unexpectedly
stopped collector and stale observations. They have persistence delays to avoid
instant noise. Email/pager delivery is not configured.

Prometheus stores time-series metrics in [its own directory](../var/monitoring),
not in the measurement database. Its WAL is different from SQLite's WAL.
Defaults are a 15-second scrape interval, a 30-second rule-evaluation interval,
seven-day metric retention and a 48-hour source-age warning threshold.
Counters reset on process restart; database run history persists.

Human-readable diagnostic evidence is separate again: JSON application logs in
the [log directory](../var/logs) rotate at 5 MB with three backups. The Prometheus
console log is rotated on startup after exceeding 10 MB.

</details>

## 6. Useful feature combinations

The quickest experiment is to keep dates/pollutant fixed and change only the
daily reference. Then change the pollutant. This separates **reference
statistics** from **eligibility/ranking** in your mind.

<details>
<summary><strong>Open the feature/setup combinations you can demonstrate</strong></summary>

| What you want to demonstrate | Setup/action | What it proves |
|---|---|---|
| Default particulate comparison | PM2.5 + WHO + recent completed days. | Fine-particle ranking with context. |
| Broader current city coverage | Same dates, explicitly choose PM10. | Pollutants can have different monitoring coverage. |
| Your own daily preference | Same pollutant/dates, custom threshold 10 or 20. | Reference statistics change, not the ranking rule. |
| A strict boundary example | Custom threshold 0. | Zero is valid; equality counts as within-reference. |
| Short versus long history | One day, thirty days, full stored year. | Eligibility and ranking depend on the requested period. |
| Earlier measurement coverage | Select an earlier stored period. | Historical pollutant coverage can differ from recent coverage. |
| Deterministic offline demo | Launch with `--offline`. | The UI/API work without contacting the provider. |
| Raw versus summarized evidence | Show observation SQL, then daily aggregates. | Daily values are derived, not made up. |
| Machine-readable consumer | Call `/api/cleanest` with the same filters. | HTML and API share the comparison logic. |
| Operational handover | Show metrics, make a backup, then use the stop launcher. | Data and process lifecycle are maintained locally. |

CLI flags can be combined: for example,
`.\Start-CleanAir.cmd --offline --no-browser --port 8001` uses stored data,
does not open a browser, and moves the app/monitoring scrape target to port 8001.

</details>

### Why did I only see three cities?

At the checked snapshot, all 17 named cities had stored measurements. PM2.5 had
some coverage-qualified history for seven cities, but only **Sofia, Varna and
Ruse** qualified for the checked recent thirty days and year. PM10 qualified
fourteen for the checked thirty days and thirteen for the year.

These are snapshot-specific counts, not a hard-coded three-city limit.
The WHO threshold does not filter the eligible-city list.

### Configuration versus search controls

- **UI:** dates, pollutant, WHO/custom profile and custom daily threshold.
- **TOML + restart:** ports, online collection, interval, initial 12-36 month
  window, coverage policy, freshness warning and monitoring retention.
- **CLI:** offline/no-browser/application-only diagnostics, manual sync, backup,
  archive/restore, status, tests, benchmarks and handover.

See [settings](../config/settings.toml) and [reference profiles](../config/profiles.toml).
Coverage defaults are engineering choices, not a statement of legal compliance.
Keep profile names/versions/source claims honest if editing built-in references.

**Not implemented:** a city-picker/map, live-now dashboard, user accounts,
weighted multi-pollutant AQI, manual upload UI, or a CSV-download button.
The SQL/export recipes below are operator techniques, not hidden UI features.

## 7. Understand the database

### The relationship picture

```text
cities
  |
  +-- stations
         |
         +-- sampling_points ---- observations ---- import_files
         |
         +-- station_days

metadata          application state
collection_runs   import progress/history
archives          verified cold-storage index
```

### The nine application tables

Counts below are a checked snapshot from **2026-10-17 15:05 UTC**, not live promises.

<details>
<summary><strong>Open the nine-table purpose/key/count map</strong></summary>

| Table | Main purpose | Key | Snapshot rows |
|---|---|---|---:|
| `cities` | Official candidate city names. | `name` | 17 |
| `stations` | Physical stations and optional city mapping. | `id` | 49 |
| `sampling_points` | Measurement/method identifiers belonging to stations. | `id` | 291 |
| `import_files` | Provenance, ETag and completed-file checkpoint. | `id`; unique `url` | 87 |
| `observations` | Filtered in-window source records, including retained invalid records. | point + pollutant + start + aggregation | 217,048 |
| `station_days` | One normalized daily value per physical station/pollutant/day. | station + pollutant + day | 12,217 |
| `collection_runs` | Attempts, progress, success/failure and counters. | `id` | 5 |
| `metadata` | JSON-encoded application state values. | `key` | 5 |
| `archives` | Archive filename, checksum, counts and cutoff. | `name` | 0 |

Zero archives is expected for this short active history; tests exercise the
older-than-36-month behaviour. City count, station count and sampling-point count
are different concepts.

</details>

<details>
<summary><strong>All columns and how to explain them</strong></summary>

### Location/reference tables

| Table | Columns |
|---|---|
| `cities` | `name`: city name and primary key. |
| `stations` | `id`: physical station ID; `city`: foreign key to city, nullable when not mapped. |
| `sampling_points` | `id`: source sampling-point ID; `station_id`: foreign key to physical station. |

Example: `SPO-BG0024A_00005_100` is a sampling point. `BG0024A` is the physical
station. Several methods/points can map to one physical station.

### `import_files`

| Column | Meaning |
|---|---|
| `id` | Local integer primary key referenced by observations. |
| `url` | Unique public source Parquet URL. |
| `dataset` | 2 validated E1a, 1 provisional E2a. |
| `etag` | Upstream version identifier used for conditional HTTP requests. |
| `last_success` | Unix seconds of the last completed import of this file. |
| `rows_imported` | In-window rows processed on that import, not necessarily newly inserted rows. |
| `window_start` | Earliest configured window loaded; extending history can require a re-read. |

### `observations`

| Column | Meaning |
|---|---|
| `point_id` | Foreign key to sampling point. |
| `pollutant` | 6001 PM2.5 or 5 PM10. |
| `started`, `ended` | UTC interval boundaries as Unix seconds. |
| `value` | Concentration as SQLite REAL, nullable for missing/nonfinite values. |
| `aggregation` | `hour` or `day`; these records are not interchangeable independent samples. |
| `validity` | Provider quality flag; nonpositive values do not enter daily means. |
| `verification` | Stored provider verification flag; not the same as our source priority. |
| `result_time` | Source result/revision timestamp, Unix seconds. |
| `data_capture` | Reported capture normalized to 0-1, or NULL if unspecified. |
| `priority` | 2 for validated, 1 for provisional source. |
| `file_id` | Foreign key to the import/provenance row. |

The composite primary key makes repeated imports duplicate-safe. A correction
updates that row rather than creating another measurement.

### `station_days`

| Column | Meaning |
|---|---|
| `station_id`, `pollutant`, `day` | Composite key; `day` is `YYYY-MM-DD` in UTC. |
| `mean` | Normalized daily concentration. |
| `coverage` | Observed-hour fraction or provider capture; NULL means unspecified underlying capture. |
| `observation_count` | Distinct hours for a derived day; 1 for a reported daily record. |
| `kind` | `validated daily`, `provisional daily`, `validated hourly` or `provisional hourly`. |

The API applies coverage eligibility at query time. A daily row can exist but
still be ineligible for the chosen policy.

### State and operations

| Table | Columns |
|---|---|
| `metadata` | `key`, `value`. Value is JSON, not always plain text. Keys include metadata refresh, initial-import flag, latest observation, last successful cycle and requested start; restoration can add an archive-pause timestamp. |
| `collection_runs` | `id`, `started`, nullable `finished`, `status`, `message`, `total_files`, `completed_files`, `imported_rows`, `rejected_rows`. |
| `archives` | `name`, `sha256`, `created`, `observations`, `station_days`, `cutoff`. Counts refer to archived records. |

Run statuses include `running`, `completed`, `failed` and `interrupted`.
Completed-file counts include processed failures; the message/status explains
the result. Imported-row counters include upserts/reprocessing, not only new rows.

### Constraints and indexes

- Foreign keys are enabled for application connections.
- Observation pollutant and aggregation values have CHECK constraints.
- Both large fact tables use composite keys and `WITHOUT ROWID`.
- Indexes support observation point/time, retention by start time, and
  station-day pollutant/date queries.
- Schema version is stored in `PRAGMA user_version`, currently 1.
  Unknown schema versions fail explicitly; there is no general migration framework.

Exact definitions: [database.py](../clean_air/database.py).

</details>

## 8. Your SQL demonstration lab

### Open a safe copy, with no extra database software

Use the project's own backup command, then Python's bundled SQLite console:

```powershell
.\runtime\python\python.exe -m clean_air backup
if ($LASTEXITCODE -ne 0) { throw "Backup failed; do not continue." }
$demo = Get-ChildItem -LiteralPath .\var\backups -Filter 'clean-air-*.sqlite3' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $demo) { throw "No verified backup found." }
.\runtime\python\python.exe -m sqlite3 $demo.FullName
```

Now paste **SQL**, not PowerShell, into the SQLite console. Finish a statement
with `;`. Type `.quit` to leave it.

This is Python's small SQLite console, not the full separate `sqlite3.exe` tool:
do not assume `.tables`, `.schema`, `.headers` or `.mode` exist. Use the SQL and
PRAGMA examples below instead. Tuple-style printed rows are normal.

Use SELECT/read-only PRAGMAs for the interview. The demo database is a copy;
do not experiment with UPDATE/DELETE/DROP on the live tracked database.

<details>
<summary><strong>Q1-Q2: show all tables and the amount of stored data</strong></summary>

### Q1. What tables exist?

```sql
-- Q1: application tables
SELECT name
FROM sqlite_schema
WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
ORDER BY name;
```

### Q2. Show the size of every application table

```sql
-- Q2: row counts
SELECT 'cities' AS table_name, COUNT(*) AS rows FROM cities
UNION ALL SELECT 'stations', COUNT(*) FROM stations
UNION ALL SELECT 'sampling_points', COUNT(*) FROM sampling_points
UNION ALL SELECT 'import_files', COUNT(*) FROM import_files
UNION ALL SELECT 'observations', COUNT(*) FROM observations
UNION ALL SELECT 'station_days', COUNT(*) FROM station_days
UNION ALL SELECT 'collection_runs', COUNT(*) FROM collection_runs
UNION ALL SELECT 'metadata', COUNT(*) FROM metadata
UNION ALL SELECT 'archives', COUNT(*) FROM archives;
```

</details>

<details>
<summary><strong>Q3-Q5: raw data, a selected city, and useful filters</strong></summary>

**Q3:** show all columns for a manageable sample. Remove the LIMIT only if you
really want every raw record printed; exporting is better for the full dataset.

```sql
-- Q3: raw sample
SELECT *
FROM observations
ORDER BY started DESC, point_id
LIMIT 50;
```

**Q4:** attach human-readable location and provenance. Change `'Sofia'` to another
city. These are raw records, not the period city-ranking result.

```sql
-- Q4: a chosen city's raw observations
SELECT s.city, s.id AS station_id, o.point_id,
       CASE o.pollutant WHEN 6001 THEN 'PM2.5' WHEN 5 THEN 'PM10' END AS pollutant,
       datetime(o.started, 'unixepoch') AS start_utc,
       datetime(o.ended, 'unixepoch') AS end_utc,
       o.value, o.aggregation, o.validity, o.verification, o.priority,
       f.dataset, f.url AS source_url
FROM observations o
JOIN sampling_points p ON p.id = o.point_id
JOIN stations s ON s.id = p.station_id
JOIN import_files f ON f.id = o.file_id
WHERE s.city = 'Sofia'
ORDER BY o.started DESC, o.point_id
LIMIT 50;
```

**Q5:** combine city, pollutant, time, source validity and value. Notice that the
upper timestamp is the **next day, exclusive**, so all hours of October 16 count.

```sql
-- Q5: combine raw filters
SELECT s.city, o.point_id, datetime(o.started, 'unixepoch') AS start_utc,
       o.value, o.aggregation, o.validity, o.priority
FROM observations o
JOIN sampling_points p ON p.id = o.point_id
JOIN stations s ON s.id = p.station_id
WHERE s.city = 'Sofia'
  AND o.pollutant = 6001
  AND o.started >= unixepoch('2026-09-17')
  AND o.started < unixepoch('2026-10-17')
  AND o.aggregation = 'hour'
  AND o.validity > 0
  AND o.value >= 0
  AND o.value > 15
ORDER BY o.started
LIMIT 50;
```

For `station_days`, use `day BETWEEN 'start' AND 'end'`; ISO date strings sort
chronologically. For raw observations, use epoch boundaries as above.

</details>

<details>
<summary><strong>Q6-Q8: cities, normalized days and why a city is excluded</strong></summary>

**Q6:** cities with actual observations, not just a metadata entry. Counts are
raw source rows, potentially including different aggregations/invalid records.

```sql
-- Q6: stored observations by city and pollutant
SELECT s.city,
       CASE o.pollutant WHEN 6001 THEN 'PM2.5' WHEN 5 THEN 'PM10' END AS pollutant,
       COUNT(*) AS raw_rows
FROM observations o
JOIN sampling_points p ON p.id = o.point_id
JOIN stations s ON s.id = p.station_id
WHERE s.city IS NOT NULL
GROUP BY s.city, o.pollutant
ORDER BY s.city, o.pollutant;
```

**Q7:** the daily input used by comparison. NULL coverage means unspecified
provider capture, not zero. Dates below are examples inside the checked snapshot.

```sql
-- Q7: a city's normalized station days
SELECT s.city, d.station_id, d.day, d.mean,
       d.coverage, d.observation_count, d.kind
FROM station_days d
JOIN stations s ON s.id = d.station_id
WHERE s.city = 'Sofia' AND d.pollutant = 6001
  AND d.day BETWEEN '2026-09-17' AND '2026-10-16'
ORDER BY d.day, d.station_id;
```

**Q8:** a thirty-day default-policy diagnosis. It requires at least 27 qualifying
days at a station. It also lists cities that have no usable days for this choice.

```sql
-- Q8: diagnose period eligibility
WITH station_coverage AS (
    SELECT s.city, d.station_id, COUNT(*) AS valid_days
    FROM station_days d
    JOIN stations s ON s.id = d.station_id
    WHERE d.pollutant = 6001
      AND d.day BETWEEN '2026-09-17' AND '2026-10-16'
      AND (d.coverage IS NULL OR d.coverage >= 0.75)
      AND s.city IS NOT NULL
    GROUP BY s.city, d.station_id
)
SELECT c.name AS city, COALESCE(MAX(sc.valid_days), 0) AS best_station_days,
       ROUND(100.0 * COALESCE(MAX(sc.valid_days), 0) / 30, 1) AS best_station_percent,
       CASE WHEN COALESCE(MAX(sc.valid_days), 0) >= 27
            THEN 'station qualifies' ELSE 'insufficient coverage' END AS explanation
FROM cities c
LEFT JOIN station_coverage sc ON sc.city = c.name
GROUP BY c.name
ORDER BY c.name;
```

Changing the dates means changing the denominator and minimum required days too.
The API computes those automatically; this example makes the policy visible.

</details>

<details>
<summary><strong>Q9: reproduce the core city-ranking calculation in SQL</strong></summary>

This matches the core calculation for the example thirty-day PM2.5 request,
default coverage rules and WHO daily threshold 15. It is not a generic replacement
for the API's validation, tie/exclusion metadata or configurable policy.

Do not replace this with `AVG(station_days.mean)` directly across all rows:
that would give days with more reporting stations extra weight.

```sql
-- Q9: core ranking, thirty-day PM2.5 example
WITH valid_days AS (
    SELECT *
    FROM station_days
    WHERE pollutant = 6001
      AND day BETWEEN '2026-09-17' AND '2026-10-16'
      AND (coverage IS NULL OR coverage >= 0.75)
),
qualifying_stations AS (
    SELECT station_id
    FROM valid_days
    GROUP BY station_id
    HAVING COUNT(*) >= 27
),
city_days AS (
    SELECT s.city, d.day, AVG(d.mean) AS city_mean
    FROM valid_days d
    JOIN qualifying_stations q ON q.station_id = d.station_id
    JOIN stations s ON s.id = d.station_id
    WHERE s.city IS NOT NULL
    GROUP BY s.city, d.day
)
SELECT city, COUNT(*) AS valid_days,
       AVG(city_mean) AS period_mean,
       SUM(CASE WHEN city_mean <= 15 THEN 1 ELSE 0 END) AS days_within_reference,
       SUM(CASE WHEN city_mean > 15 THEN 1 ELSE 0 END) AS days_above_reference
FROM city_days
GROUP BY city
HAVING COUNT(*) >= 27
ORDER BY period_mean, city;
```

For the checked snapshot this produces Sofia, Varna and Ruse. If changing to PM10,
use code 5 and the intended reference, normally 45, rather than silently keeping
the PM2.5 reference. Order by the raw mean, not a rounded display column.

</details>

<details>
<summary><strong>Q10-Q14: quality, collection, source files, freshness and archives</strong></summary>

**Q10:** inspect retained records excluded from concentration means. This is not
the full rejected-row count: some unsupported rows never enter raw storage.

```sql
-- Q10: retained invalid/missing/negative records
SELECT point_id, pollutant, datetime(started, 'unixepoch') AS start_utc,
       value, aggregation, validity, verification
FROM observations
WHERE validity <= 0 OR value IS NULL OR value < 0
ORDER BY started DESC
LIMIT 50;
```

**Q11:** inspect recent collector attempts and counters.

```sql
-- Q11: collection history
SELECT id, datetime(started, 'unixepoch') AS started_utc,
       datetime(finished, 'unixepoch') AS finished_utc,
       status, completed_files, total_files, imported_rows, rejected_rows, message
FROM collection_runs
ORDER BY id DESC
LIMIT 10;
```

**Q12:** inspect actual imported source URLs and their conditional-download state.

```sql
-- Q12: provenance and source checkpoints
SELECT id, dataset, datetime(last_success, 'unixepoch') AS last_success_utc,
       rows_imported, window_start, etag, url
FROM import_files
ORDER BY dataset, url;
```

**Q13:** collection success is not the same timestamp as the newest observation.

```sql
-- Q13: source freshness versus collector success
SELECT key,
       datetime(json_extract(value, '$'), 'unixepoch') AS utc_time
FROM metadata
WHERE key IN ('last_success', 'latest_observation')
ORDER BY key;
```

**Q14:** no rows currently means no retained archive manifest, not a broken query.

```sql
-- Q14: archived-history manifests
SELECT name, sha256, datetime(created, 'unixepoch') AS created_utc,
       observations, station_days, cutoff
FROM archives
ORDER BY created DESC;
```

</details>

<details>
<summary><strong>Q15-Q18: unmapped stations, capture quality, schema and date gaps</strong></summary>

**Q15:** country observations can exist without an official city mapping.

```sql
-- Q15: stations excluded from city comparisons
SELECT s.id AS station_id, s.city, COUNT(d.day) AS stored_station_days
FROM stations s
LEFT JOIN station_days d ON d.station_id = s.id
WHERE s.city IS NULL
GROUP BY s.id, s.city
ORDER BY s.id;
```

**Q16:** distinguish provider daily values from hourly-derived values.

```sql
-- Q16: daily-record quality summary
SELECT kind, COUNT(*) AS records,
       SUM(CASE WHEN coverage IS NULL THEN 1 ELSE 0 END) AS unspecified_capture,
       MIN(coverage) AS minimum_known_capture,
       MAX(coverage) AS maximum_known_capture
FROM station_days
GROUP BY kind
ORDER BY kind;
```

**Q17:** inspect columns, relationships and query indexes with real SQL.

```sql
-- Q17: schema inspection
PRAGMA table_info(observations);
PRAGMA foreign_key_list(observations);
PRAGMA index_list(station_days);
```

**Q18:** find dates with no city-mapped station meeting daily coverage. This is
a snapshot investigation, not proof that no external provider has data.

```sql
-- Q18: coverage-qualified date gaps in the checked year
WITH RECURSIVE requested_days(day) AS (
    SELECT '2025-10-17'
    UNION ALL
    SELECT date(day, '+1 day')
    FROM requested_days
    WHERE day < '2026-10-16'
)
SELECT r.day
FROM requested_days r
WHERE NOT EXISTS (
    SELECT 1
    FROM station_days d
    JOIN stations s ON s.id = d.station_id
    WHERE d.day = r.day AND s.city IS NOT NULL
      AND (d.coverage IS NULL OR d.coverage >= 0.75)
)
ORDER BY r.day;
```

The checked snapshot identified 2026-06-06, 2026-08-23 and 2026-08-24.
August 23 had no stored raw observations; the other two did not meet the daily
policy. Investigate source/import evidence; do not fabricate values or lower
standards merely to make a demonstration look complete.

</details>

### Change filters confidently

<details>
<summary><strong>Open the SQL filter cheat sheet</strong></summary>

| Interviewer asks... | Relevant condition or join |
|---|---|
| "Only this city?" | Join point -> station, then `s.city = 'Sofia'`. |
| "Only one physical station?" | `s.id = 'BG0024A'`, choosing an ID from your data. |
| "Only one measurement method?" | `o.point_id = 'SPO-BG0024A_00005_100'`. |
| "Only PM10?" | `pollutant = 5`; PM2.5 is 6001. |
| "Only validated source?" | Raw `priority = 2`, or file `dataset = 2`. |
| "Only hourly values?" | Raw `aggregation = 'hour'`. |
| "Over the reference?" | Apply it to the intended daily city mean, not an arbitrary period average. |
| "No known capture?" | `coverage IS NULL`, never `coverage = NULL`. |
| "Between two raw timestamps?" | `started >= unixepoch(start)` and `started < unixepoch(next_day_after_end)`. |
| "What was rejected?" | Validity/value checks plus collection logs/counters; not every discarded row is persisted. |

</details>

### Show all data without flooding the terminal

The complete raw query is `SELECT * FROM observations ORDER BY started;`.
For every row, use a CSV rather than scrolling through hundreds of thousands
of tuples. The following uses a **read-only connection** to the live file and
streams rows. It creates/overwrites only the export file.

<details>
<summary><strong>Copy-and-run PowerShell: export all stored observations</strong></summary>

```powershell
@'
from contextlib import closing
from pathlib import Path
import csv
import sqlite3

root = Path.cwd()
database = root / "var" / "data" / "clean_air.sqlite3"
output = root / "var" / "reports" / "observations.csv"
output.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
    cursor = connection.execute("SELECT * FROM observations ORDER BY started")
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(column[0] for column in cursor.description)
        writer.writerows(cursor)
print(output)
'@ | & .\runtime\python\python.exe -B -
```

CSV includes raw identifiers, codes and invalid/missing records for traceability.
Use the joins from Q4 when you want city names in an export.

</details>

**Application-code safety:** SQL parameters use bound values, not concatenated
user input. For example, the application passes dates and thresholds separately
from its SQL text. Literal strings above are for your controlled console demo.

## 9. Interview questions and short answers

Use these to test understanding, not to recite words you cannot defend.

<details>
<summary><strong>Deployment and architecture</strong></summary>

**Why can it run without Python installed?**  
The package contains its own interpreter, standard library and third-party
packages. Launchers and module paths are relative to the extracted folder.

**Is this the same as a virtual environment?**  
No. It uses the official embedded Windows distribution with an explicit path
file. A copied venv can contain machine-specific executable paths.

**What processes are running?**  
One Python API process with a collector thread and shutdown watcher, plus an
owned native Prometheus process. The browser is a client, not the backend.

**Why no Docker, React or PostgreSQL?**  
They were not required for a small single-host tool. SQLite and HTML avoid
installing more services/build tools. That is a scope choice, not a claim that
those technologies are inferior.

**Can I run this on Linux or expose it publicly unchanged?**  
Not this Windows bundle. Windows locking/shutdown and the Prometheus executable
would need adapting. Public exposure needs additional authentication/TLS,
deployment supervision and operational review; this app deliberately binds locally.

</details>

<details>
<summary><strong>Data source and meaning</strong></summary>

**Where does the data come from and why there?**  
EEA official reporting, validated historical E1a and provisional E2a. We verified
anonymous access, actual Bulgarian files, formats and reuse terms.

**What exactly do you download?**  
JSON city/point metadata, a CSV list of file URLs and typed Parquet measurement
files. Locally we retain relevant observations and daily aggregates.
Parquet is read in batches, but relevant rows are accumulated per file for an
atomic import; this is not an unlimited-memory streaming platform.

**Is provisional data fake, forecast or gap-filled?**  
No. It is reported measurement data not yet treated as the annual validated
dataset. We disclose it and do not use modelled/gap-filled dataset selectors.

**Is this real time?**  
No instant stream. The running collector periodically asks for latest available
observations; reporting delay and our refresh interval both matter.
The search UI only accepts completed historical UTC days.

**Why UTC instead of Bulgaria's timezone?**  
Daily validated values already have UTC-day windows. We cannot accurately shift
a daily mean without its underlying hourly readings. UTC also avoids 23/25-hour
local-day ambiguity.

**Does cleanest mean medically safe?**  
No. It means lowest selected-pollutant mean among eligible cities. WHO daily
guidance uses an annual 99th-percentile convention. City aggregates and arbitrary
periods are not compliance certificates.

</details>

<details>
<summary><strong>Database and calculations</strong></summary>

**Where is the database? Is it inside Python?**  
The engine is bundled with Python; the actual database is the separate file in
the data directory. No database server/login is involved.

**Why store raw records and daily aggregates?**  
Raw records give traceability/reprocessing; daily aggregates keep searches small.
Storing only final winners would make other periods/policies harder to audit.

**What stops duplicates?**  
The compound observation key and upsert. Source priority/revision time decides
which correction can replace an existing observation.

**How do you avoid counting a station twice?**  
Sampling points map to physical stations; daily normalization produces one
station/pollutant/day record before city averaging.

**Why not average every daily station row over the whole period directly?**  
That would weight days according to their reporting station counts. We first
average by city/day, then give each valid day equal period weight.

**Why not replace gaps with zero?**  
Zero is a real concentration value, not "unknown." Using it for gaps would
artificially make poorly measured cities look clean.

**What does NULL coverage mean?**  
For provider-reported valid daily values, hourly capture can be unspecified.
We preserve that uncertainty instead of pretending it is zero or complete.

**Why only three PM2.5 cities?**  
The recent eligible list reflects pollutant and period coverage, not a hard-coded
allowlist or a rule that only three cities are healthy.

**What if I change the threshold?**  
The reference-day statistics change. The lowest-average ranking does not.
Changing the pollutant/date range can change both eligibility and the winner.

</details>

<details>
<summary><strong>Reliability, performance and ownership</strong></summary>

**What if the provider is down?**  
Bounded retries fail explicitly; existing committed history remains searchable.
With no initial usable history, the UI explains that backfill is not ready.

**Is a whole refresh atomic?**  
No. Each file is atomic with its rollups/checkpoint. A failed cycle can retain
successful new files alongside older data. The warning and timestamps matter.

**What if the process crashes mid-import?**  
The current transaction rolls back, prior files stay committed, and the next run
replays unfinished work. That is why idempotence and durable checkpoints matter.

**Why can readers work while ingestion writes?**  
SQLite WAL allows readers alongside a writer; writes use short transactions.
It is still one serialized writer, not unlimited write concurrency.

**How did you demonstrate speed?**  
Daily rollups/indexes, service-level and full HTTP benchmarks, multiple period
lengths and four concurrent clients. Recorded worst HTTP p95 was 136.557 ms
against a 300 ms target on the documented machine, not a universal guarantee.

**What is monitored?**  
Request counts/latency, collection status, progress, worker state, source age
and local row counts. Logs explain failures; health/readiness are separate.

**How do you archive safely?**  
Write compressed records and metadata, verify counts/checksum, then remove
active copies transactionally and reclaim space. Restore is verified too.
Automatic retention still depends on a successful sync; that is a known limitation.

**Why one published commit?**  
Development initially had milestones. The owner requested one public snapshot;
the original history was preserved locally. One published snapshot is not the
same evidence of progress as publishing the milestone history.

**How was AI used?**  
Research, implementation and test assistance, with the decisions/corrections
documented. Explain what you personally reviewed and understand. Automated
checks do not substitute for owning or independently reviewing the submission.

**What would you improve next?**  
Independent retention scheduling, deeper source-gap investigation,
station-type filtering/domain review, larger history/load tests, fresh-machine
validation and external alert delivery. Multi-instance deployment could justify
PostgreSQL and a separate worker later.

</details>

## 10. Demonstrate it and be honest about its limits

### A ten-minute demonstration

1. Extract the portable package; show that its runtime and data are included.
2. Start with `--offline` for a predictable demo, leaving monitoring enabled.
3. Show the status/history range and explain UTC/completed days.
4. Compare PM10 over a useful stored period; explain winner, average and coverage.
5. Change only the reference threshold; show why the winner does not change.
6. Switch to PM2.5 and open exclusions; explain the smaller comparison scope.
7. Open the API result and monitoring; distinguish application health from freshness.
8. Open a verified SQL backup; show Q1, Q2, Q4 and Q9.
9. Explain a failure case, the archive policy and the known limits below.
10. Quit the SQL console, then use the stop launcher before moving/zipping files.

### Operator command card

<details>
<summary><strong>Open the complete run/stop/backup/sync/archive/test command card</strong></summary>

| Goal | PowerShell command | Important condition |
|---|---|---|
| Start | `.\Start-CleanAir.cmd` | Keep its console open. |
| Offline | `.\Start-CleanAir.cmd --offline` | Saved history works; no source updates. |
| Do not open a browser | `.\Start-CleanAir.cmd --no-browser` | Open the printed local URL yourself. |
| Another app port | `.\Start-CleanAir.cmd --port 8001` | Generated monitoring target follows it; the port must be free. |
| App-only diagnostics | `.\Start-CleanAir.cmd --no-monitoring` | Explicitly omits Prometheus; not the full monitored delivery. |
| Stop | `.\Stop-CleanAir.cmd` | Wait for completion before ZIP/move. |
| Status | `.\runtime\python\python.exe -m clean_air status` | Inspect JSON without starting the service. |
| Doctor | `.\runtime\python\python.exe -m clean_air doctor` | Checks runtime presence/database integrity. |
| Sync | `.\runtime\python\python.exe -m clean_air sync` | Stop the app first; same workspace writer lock. |
| Force re-read | `.\runtime\python\python.exe -m clean_air sync --force` | Bypasses ETags, not validation or duplicate rules. |
| Backup | `.\runtime\python\python.exe -m clean_air backup` | Consistent SQLite backup; can run alongside readers/service. |
| Archive | `.\runtime\python\python.exe -m clean_air archive` | Stop the app first. |
| Restore | `.\runtime\python\python.exe -m clean_air restore ARCHIVE-NAME.jsonl.gz` | File and checksum must be in the archives directory. |
| Handover | `.\Prepare-Handover.cmd` | Stop first; uses a verified database snapshot. |
| Tests | `.\Test-CleanAir.cmd` | Runs lint and project tests, not every vendored library's tests. |
| Service benchmark | `.\runtime\python\python.exe -m clean_air benchmark --iterations 100` | Existing real history required. |
| HTTP benchmark | `.\runtime\python\python.exe .\tools\benchmark_http.py --requests 100 --concurrency 4` | App must be running; use the appropriate port. |

Restoration pauses automatic archiving for 24 hours. Restore creates active
records from cold history; it does not magically supply missing source measurements.
Stop also closes leftover processes using this workspace's bundled runtime,
including a SQL console if you leave one running.
Local handover packages can include Git history; with LFS this can also include
large local object caches. Public Release packages omit Git internals. If
manually zipping the workspace, avoid including an existing ZIP from the
distribution directory inside another ZIP.

</details>

### Limits to state without being defensive

- We compare **eligible measured cities**, not every Bulgarian city or personal exposure.
- Station types are not filtered to background-only; station locations and gaps matter.
- The checked year has coverage gaps; some requests correctly return insufficient data.
- Hourly updates require the app to be running and the provider to report.
- The current UTC day is stored as it arrives but not exposed as a completed-day result.
- Automatic archiving can be delayed by failed collection. Manual archiving exists.
- Monitoring has local alert states but no external pager/email delivery.
- The bundled runtime targets Windows x64 and the deployment is single-host/local-only.
- Benchmarks cover the documented dataset/machine/load, not every future scale.
- Most recent complete project validation had 69 tests passing; one upstream
  TestClient deprecation warning is documented, not hidden.
- The initial portable Release and later complete Git LFS checkout were both
  relocation/download tested on the same Windows machine, not a clean VM.

### What to open if they ask "show me the code"

<details>
<summary><strong>Open the source-file responsibility map</strong></summary>

| Responsibility | File |
|---|---|
| Entry point, lifecycle and CLI commands | [__main__.py](../clean_air/__main__.py) |
| Configuration and pollutant codes | [settings.py](../clean_air/settings.py) |
| Public provider requests and Parquet validation | [source.py](../clean_air/source.py) |
| Scheduling, metadata, checkpoints and per-file import | [collector.py](../clean_air/collector.py) |
| Tables, constraints, upsert and daily normalization | [database.py](../clean_air/database.py) |
| Coverage, averaging, ranking and reference statistics | [comparison.py](../clean_air/comparison.py) |
| Routes, errors, templates and health endpoints | [web.py](../clean_air/web.py) |
| Form submission and status polling | [app.js](../clean_air/static/app.js) |
| Process ownership, monitoring, packaging, benchmarks | [operations.py](../clean_air/operations.py) |
| Cooperative stop signal and storage release | [shutdown_control.py](../clean_air/shutdown_control.py) |
| Archive verification and restoration | [archive.py](../clean_air/archive.py) |
| Metrics and logging | [metrics.py](../clean_air/metrics.py), [logging_setup.py](../clean_air/logging_setup.py) |
| Portable runtime builder | [build_portable.py](../tools/build_portable.py) |

</details>

Further evidence: [methodology](METHODOLOGY.md), [operations](OPERATIONS.md),
[verification](VERIFICATION.md), [AI usage](AI_USAGE.md) and
[your exact prompt history](PROMPT_HISTORY.md).

**Final self-test:** explain the difference between source observations,
station-day aggregates, city daily means and period means without looking.
If you can then demonstrate the SQL and explain an excluded city, you understand
the central design rather than just its presentation.
