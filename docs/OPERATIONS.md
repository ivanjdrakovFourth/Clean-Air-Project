# Operations and recovery

## Supported deployment

64-bit Windows 10/11, local writable disk, one application process per workspace.
All application-owned data stays in that workspace. Do not put an open WAL
database on a network filesystem or let a synchronization utility copy its files
independently. Public internet exposure, authentication, TLS termination and
multi-instance operation are outside this local deployment.

Default ports: API/UI 8000 and Prometheus 9090, both on `127.0.0.1`.
Startup refuses occupied ports; it never terminates another application.

The complete Git checkout includes the runtime and source seeds through Git LFS.
Run `git lfs pull` before startup if the binaries are pointer files. The portable
Release ZIP contains the actual files and needs no Git LFS client.
New seeds are checked and merged once into the matching local working database,
not copied over it. Equal/newer local revisions and personal rules remain.
The old tracked EEA snapshot is retained for checkout compatibility; do not
commit a live store or hard-reset it to resolve local changes.
Logs, credentials, personal alerts and transient state are not tracked.

## Normal operation

```powershell
.\Start-CleanAir.cmd
```

The launcher verifies local prerequisites, starts bundled Prometheus and its
local storage, waits for monitoring readiness, then starts the app, independent
EEA/OpenAQ workers, alert evaluation and queued maintenance. OpenAQ starts only
when enabled and a local key exists. The direct feed has no invented collector.
A workspace file lock prevents a second application or manual writer; source
locks coordinate in-app data jobs and collectors.
The browser opens only after the application's live endpoint responds.

Every normal startup begins checks without waiting for a browser response.
The shared interface shows an acknowledgement-only dialog with a single OK
button; Escape does not cancel it and it cannot cancel collection. Source
completion/failure messages remain in Alerts and are shown separately for each
provider. Optional Windows source-update notifications are configured in
Monitoring. A missing key or unavailable direct feed never earns a false
"up to date" message.

Collection uses bounded timeouts, retries for transport/429/5xx failures and
ETag conditional downloads. Official metadata is refreshed daily. Successfully
imported files and their aggregates commit together with their checkpoints.
An interrupted file is replayed safely; completed files remain committed.

The first requested history floor is preserved. A long pause is caught up from
stored checkpoints rather than clipped to a newly moving last-year window.
Existing history grows and older-than-36-month records are verified into cold
archives. EEA transport/collection failures and busy source locks retry after
60 seconds; successful regular checks use the configured refresh interval.

The delivery prefills 12 months of available permitted EEA/OpenAQ records.
Bundled data is searchable offline without an OpenAQ key; a new empty source
must collect valid history first. OpenAQ normally gates unfinished historical
backfill, with explicit partial-cache opt-in and verified bundled/restored
windows. Once history exists, failures do not erase cached historical queries.
Provisional values, stale source data and failed updates are disclosed.

Save a key through Monitoring or `Configure-OpenAQ.cmd`. The environment
`OPENAQ_API_KEY` overrides the Windows user-encrypted file in `var\secrets`.
Never commit/share it. Moving the encrypted file to another user/computer is not
credential setup; configure a new local key there.

OpenAQ defaults to 45 requests/minute and at most 1,900 application requests in a
rolling hour, with a 1,900-request cycle budget, persisted timestamps and
interruptible cooldown. HTTP 408 shrinks a historical window; an unsuccessful
one-day window is deferred and retried. Collection can take hours. Minimum
observed date alone does not prove that every sensor's backfill finished.

### Current and historical searches

The default view is measured cities with PM10 and EEA selected. Blank
dates compare the latest common reported hour, within six hours by default;
an older database does not receive a stale "current" winner. Choose dates to
use that selected provider's history. Search settings offers station detail, other
definitions and cleanest-day/hour searches; see [methodology](METHODOLOGY.md).

Current search freshness is deliberately stricter than the general 48-hour
source-staleness alert. Offline mode can still answer current searches only
while the saved observations satisfy that six-hour comparison policy.
No AI API, web search or provider proxy request runs during a comparison.
Combined mode queries each checked store and retains explicit source errors.

The named station catalog is a verified, bundled snapshot, not a promise of
continuously updated street coverage. Do not relabel national-map placeholder
zeros as live readings. Source checks are in [DATA_SOURCES.md](DATA_SOURCES.md).

### Database upgrade

Schema 1/2 is backed up automatically under `var\backups\before-schema-3-*.sqlite3`
before migration to schema 3. A failed backup stops migration. Existing
measurements/city mappings remain; the pollutant constraint expands to all six
supported codes and indexes are recreated. Active old instances prevent
migration. Old unmarked archive headers remain EEA-only; new archives carry
provider and station provenance.

Keep the backup and use matching application/schema versions when rolling back.
Do not run an old application against a migrated database.

To stop and release files before making a ZIP:

```powershell
.\Stop-CleanAir.cmd
```

The stop launcher requests a normal server shutdown through workspace-local
control files and waits up to 20 seconds. It validates the server PID, executable
and process creation time, so a reused PID is not mistaken for the application.
If needed, it stops leftover Python, Prometheus and Ruff processes whose
executables belong to this workspace. Unrelated Python installations and other
applications are not stopped.

It then flushes SQLite's WAL, checks database integrity and verifies the important
database, log, monitoring and runtime file locks are released. It does not delete
the database or measurement history. Wait for it to finish before making your ZIP.
It is safe to run when the application is already stopped.

The launcher uses built-in Windows PowerShell with a process-only execution
policy; it does not change the machine's execution policy or override
organization-enforced policies.

Ctrl+C in the start console remains supported. A still-pending source request
may be interrupted on process exit; transactions and checkpoints preserve
committed imports. Shutdown control files are generated in `var\run` and are
excluded from the handover ZIP.

## Health and monitoring

| Surface | Interpretation |
|---|---|
| `/health/live` | The application responds. |
| `/health/ready` | Application readiness, not a guarantee that all providers and pollutants are fully covered. Inspect per-source state. |
| `/api/status?source=openaq` | Selected-source dates, progress, latest attempt, data freshness, worker state and comparison scope. |
| Monitoring / `/api/monitoring` | Friendly source/city health, actual Prometheus charts/alerts, backfill/quota and maintenance state. |
| `/metrics` | API counters/latency, collection health, worker state, source age, progress and local row counts. |
| Prometheus `/targets` | Scrape status for the application. |
| Prometheus `/alerts` | Six platform rules, including application/collector failures and per-provider collection/staleness. |

Prometheus retains seven days by default. Alert states are available locally.
Email, paging and external Alertmanager delivery are **not configured**.
Application JSON logs rotate at 5 MB with three backups. Prometheus console logs
are rotated on the next startup when they exceed 10 MB.

### Personal alerts and operational menus

Create/edit/pause/delete rules in **Alerts**. Sources keep independent baselines;
the first evaluation normally records a baseline. Completed qualifying result
changes trigger events; partial imports and unchanged heartbeats do not pretend
to be cleanest-city changes. Rules cover city changes, cleaner/dirtier values,
threshold crossings, result changes and source/city health.

Rules/events live in `var\data\alerts.sqlite3`, separate from measurement stores.
Optional Windows notification delivery is recorded as requested, failed or
uncertain. Focus Assist, session and organization policy can suppress display.
In-app event history remains available; browser closure is allowed, but stopping
the application stops its monitoring and notification workers.

**Data** filters actual source tables, paginates and exports CSV without SQL.
Its maintenance actions and Monitoring diagnostics are queued. Inspect the
completed/failed outcome rather than assuming a button click succeeded.
Verified backup downloads are offered only after successful completion.

### Source outage or bad source data

1. Check `/api/status` and `var\logs\application.jsonl`.
2. Existing historical results should still work; the UI should show the failure.
3. Correct network/source conditions and wait for the next configured refresh.
4. To run a manual retry, stop the app first:

   ```powershell
   .\runtime\python\python.exe -m clean_air sync --source eea
   .\runtime\python\python.exe -m clean_air sync --source openaq
   ```

5. Use `sync --force` only when an explicit re-read is needed. It bypasses ETags,
   not validation, precedence or duplicate protection.

Never replace failed imports with empty data or mock observations. Malformed
files fail explicitly; other files can still be processed. If the initial
backfill cannot complete, the application remains live and explains why history
is not ready.

### Source data is stale

Collection success means the upstream service responded, not that its observations
are fresh. A separate age metric tracks the latest source measurement, with a
48-hour warning threshold by default. Investigate reporting delay and dataset
availability; do not reset timestamps to silence an alert.

### No city is eligible

Inspect the excluded-city list. Try another period or explicitly choose PM10,
which currently has broader Bulgarian coverage. Do not silently substitute it
for PM2.5 or lower quality settings without recording the change.

### Worker has stopped

Inspect logs and `/api/status`. Prometheus alerts when collection is enabled but
the thread is no longer alive. Restart after investigating the exception.

## Backups

```powershell
.\runtime\python\python.exe -m clean_air backup --source eea
```

This uses SQLite's backup API, runs an integrity check and writes a timestamped
snapshot under `var\backups`. Use `--source openaq` for that provider. A backup
can be taken while the service is running; the UI coordinates source operations.
Keep independent copies according to your organization's policy; keeping both
the database and its backup on one physical disk does not protect against disk loss.

To recover:

1. Stop the application.
2. Keep the existing database, its WAL and logs together for diagnosis.
3. Use a verified same-provider backup at its matching `var\data` path in a clean
   extracted copy; do not combine it with another database's WAL or source.
4. Run the doctor command, then start the application.

```powershell
.\runtime\python\python.exe -m clean_air doctor --source eea
```

Do not attempt automatic destructive database repair. Integrity failures are
reported explicitly.

## Archiving and restoring

The worker archives older-than-36-month records after a successful refresh.
To run it explicitly while the app is stopped:

```powershell
.\runtime\python\python.exe -m clean_air archive --source eea
```

Each archive is gzip-compressed JSON Lines containing versioned metadata,
observations and daily records. Row counts and SHA-256 are verified before
deleting active copies. The `.sha256` sidecar must stay beside the archive.

EEA uses `var\archives`; OpenAQ uses `var\archives\openaq`, and direct-source
archives use `var\archives\bg_direct`. To restore, place the archive and checksum
in the correct provider directory, then use its source flag:

```powershell
.\runtime\python\python.exe -m clean_air restore before-YYYY-MM-DD-NUMBER.jsonl.gz --source eea
```

Restoration verifies checksum/counts, remaps source file identifiers, respects
source precedence, rebuilds daily values and checks foreign keys. It is
transactional and pauses archiving for 24 hours so the verified restored window
can be queried even while unrelated source backfill is pending. Wrong-provider
archives are rejected. No old records means no fabricated archive.

## Handover and relocation

Run `Stop-CleanAir.cmd`, wait for completion, then run `Prepare-Handover.cmd`. A busy workspace lock causes an
explicit failure instead of packaging files while a writer is active. It refuses
unfinished EEA/OpenAQ history, checks consistent source snapshots, removes local
quota/operational metadata and collection-run logs, and vacuums shared copies.
Personal alerts, credentials, caches, logs, untracked user PDFs and temporary
state are excluded. The local handover archive includes `.git`
history when present in the checkout, but not transient state. Public GitHub
release archives omit internal Git metadata. Generated monitoring paths are regenerated
after extraction. Python uses relative module paths, not a copied virtual
environment's absolute paths.
Build-machine-specific pip console launchers are omitted; commands use the
bundled interpreter's `-m` entry points. The native Ruff helper is retained.

Verify ZIP hashes with PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 '.\dist\CleanAirFinder-Windows-x64.zip'
```

Compare with its `.sha256` sidecar. Extract to a different local directory and
run the doctor command before handing it over.

### Publishing prefilled Git seeds

After both supported backfills finish, stop the application and run:

```powershell
.\runtime\python\python.exe -m clean_air seed
```

This builds sanitized source snapshots and a SHA-256/count/schema/provider/date
manifest under `seed-data`. It refuses incomplete histories, including missing
or pending OpenAQ sensor cursors. No direct Bulgarian concentration seed is
invented. Commit the snapshots through the configured Git LFS rules; do not
substitute your live `var\data` stores.

On the recipient's first start, manifest/hash/schema/source/count checks precede
merge. Existing data is backed up; file IDs map by URL; equal/newer local
revisions survive; daily values rebuild transactionally. Applied hashes prevent
repeat imports. A 12-month seed never certifies an unfinished 36-month request.
The portable Release ZIP instead contains sanitized working stores, so it needs
neither Git nor a repeated seed import.
