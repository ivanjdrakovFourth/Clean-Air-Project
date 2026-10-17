# Operations and recovery

## Supported deployment

64-bit Windows 10/11, local writable disk, one application process per workspace.
All application-owned data stays in that workspace. Do not put an open WAL
database on a network filesystem or let a synchronization utility copy its files
independently. Public internet exposure, authentication, TLS termination and
multi-instance operation are outside this local deployment.

Default ports: API/UI 8000 and Prometheus 9090, both on `127.0.0.1`.
Startup refuses occupied ports; it never terminates another application.

## Normal operation

```powershell
.\Start-CleanAir.cmd
```

The launcher verifies local prerequisites, starts bundled Prometheus and its
local storage, waits for monitoring readiness, then starts the app and one
collector thread. A workspace file lock prevents a second application or manual
writer. The browser opens only after the application's live endpoint responds.

Collection uses bounded timeouts, retries for transport/429/5xx failures and
ETag conditional downloads. Official metadata is refreshed daily. Successfully
imported files and their aggregates commit together with their checkpoints.
An interrupted file is replayed safely; completed files remain committed.

The initial import must complete before comparisons are enabled. Once history
exists, collection failures do not make cached historical queries unavailable.
Provisional values, stale source data and failed updates are disclosed.

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
| `/health/ready` | 200 once initial history exists; 503 before backfill completes. A later source outage does not erase readiness. |
| `/api/status` | Available dates, progress, latest attempt, data freshness, worker state and comparison scope. |
| `/metrics` | API counters/latency, collection health, worker state, source age, progress and local row counts. |
| Prometheus `/targets` | Scrape status for the application. |
| Prometheus `/alerts` | Application-down, failed-collection, stopped-worker and stale-source rules. |

Prometheus retains seven days by default. Alert states are available locally.
Email, paging and external Alertmanager delivery are **not configured**.
Application JSON logs rotate at 5 MB with three backups. Prometheus console logs
are rotated on the next startup when they exceed 10 MB.

### Source outage or bad source data

1. Check `/api/status` and `var\logs\application.jsonl`.
2. Existing historical results should still work; the UI should show the failure.
3. Correct network/source conditions and wait for the next configured refresh.
4. To run a manual retry, stop the app first:

   ```powershell
   .\runtime\python\python.exe -m clean_air sync
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
.\runtime\python\python.exe -m clean_air backup
```

This uses SQLite's backup API, runs an integrity check and writes a timestamped
snapshot under `var\backups`. A backup can be taken while the service is running.
Keep independent copies according to your organization's policy; keeping both
the database and its backup on one physical disk does not protect against disk loss.

To recover:

1. Stop the application.
2. Keep the existing database, its WAL and logs together for diagnosis.
3. Use a verified backup as `var\data\clean_air.sqlite3` in a clean extracted copy
   of the project; do not combine it with another database's WAL.
4. Run the doctor command, then start the application.

```powershell
.\runtime\python\python.exe -m clean_air doctor
```

Do not attempt automatic destructive database repair. Integrity failures are
reported explicitly.

## Archiving and restoring

The worker archives older-than-36-month records after a successful refresh.
To run it explicitly while the app is stopped:

```powershell
.\runtime\python\python.exe -m clean_air archive
```

Each archive is gzip-compressed JSON Lines containing versioned metadata,
observations and daily records. Row counts and SHA-256 are verified before
deleting active copies. The `.sha256` sidecar must stay beside the archive.

To restore an archive, place it and its checksum in `var\archives`, then:

```powershell
.\runtime\python\python.exe -m clean_air restore before-YYYY-MM-DD-NUMBER.jsonl.gz
```

Restoration verifies checksum/counts, remaps source file identifiers, respects
source precedence, rebuilds daily values and checks foreign keys. It is
transactional and pauses archiving for 24 hours so the restored history can be
queried. A source-only empty installation still needs its normal initial import.

## Handover and relocation

Run `Stop-CleanAir.cmd`, wait for completion, then run `Prepare-Handover.cmd`. A busy workspace lock causes an
explicit failure instead of packaging files while a writer is active.
The local handover archive includes a consistent database snapshot and `.git`
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
