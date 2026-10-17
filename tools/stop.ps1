param(
    [ValidateRange(0, 60)]
    [int]$GraceSeconds = 20
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$prefix = $root.TrimEnd('\') + '\'
$statePath = Join-Path $root 'var\run\server.json'
$state = $null
$requestPath = $null

function Get-ProjectProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^(python(?:w|[0-9.]+)?|py|prometheus|promtool|ruff)\.exe$' -and
        $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Get-RegisteredProcess {
    if ($null -eq $state) { return $null }
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $state.pid)
    if ($null -eq $process -or $process.ExecutablePath -ne $state.executable) {
        return $null
    }
    $native = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
    if ($null -eq $native) { return $null }
    if ($native.StartTime.ToFileTimeUtc() -ne [long]$state.process_start_ticks) {
        return $null
    }
    return $process
}

if (Test-Path -LiteralPath $statePath) {
    try {
        $candidate = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $recordedId = 0
        $recordedTicks = [long]0
        if (
            $candidate.version -eq 1 -and $candidate.workspace -eq $root -and
            [int]::TryParse([string]$candidate.pid, [ref]$recordedId) -and $recordedId -gt 0 -and
            [long]::TryParse([string]$candidate.process_start_ticks, [ref]$recordedTicks) -and
            $recordedTicks -gt 0 -and $candidate.executable -and
            $candidate.instance_id -match '^[a-f0-9]{32}$'
        ) {
            $state = $candidate
        } else {
            Write-Warning 'Ignoring invalid or foreign shutdown metadata; only workspace executables will be stopped.'
        }
    } catch [System.ArgumentException] {
        Write-Warning 'Shutdown metadata is unreadable; only workspace executables will be stopped.'
    }
}

$registered = Get-RegisteredProcess
if ($null -ne $registered) {
    $requestPath = Join-Path $root ("var\run\stop-" + $state.instance_id + '.request')
    [System.IO.File]::WriteAllText($requestPath, 'stop')
    Write-Host 'Requesting a clean application shutdown...'
    $deadline = [DateTime]::UtcNow.AddSeconds($GraceSeconds)
    while ([DateTime]::UtcNow -lt $deadline -and $null -ne (Get-RegisteredProcess)) {
        Start-Sleep -Milliseconds 200
    }
}

$targets = @(Get-ProjectProcesses)
$registered = Get-RegisteredProcess
if ($null -ne $registered -and $registered.ProcessId -notin $targets.ProcessId) {
    $targets += $registered
}
foreach ($target in ($targets | Sort-Object ProcessId -Unique)) {
    $current = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $target.ProcessId)
    if ($null -eq $current) { continue }
    if ($current.CreationDate -ne $target.CreationDate -or $current.ExecutablePath -ne $target.ExecutablePath) {
        throw "Process $($target.ProcessId) changed identity; refusing to stop it."
    }
    $insideWorkspace = $current.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    $registered = Get-RegisteredProcess
    if (-not $insideWorkspace -and ($null -eq $registered -or $registered.ProcessId -ne $current.ProcessId)) {
        throw "Process $($current.ProcessId) is not owned by this workspace."
    }
    Write-Warning "Stopping leftover project process $($current.Name), PID $($current.ProcessId)."
    $native = Get-Process -Id ([int]$current.ProcessId) -ErrorAction SilentlyContinue
    if ($null -ne $native) {
        Stop-Process -Id ([int]$current.ProcessId) -Force
        if (-not $native.WaitForExit(5000)) {
            throw "Project process $($current.ProcessId) did not exit."
        }
    }
}

$startPath = Join-Path $root 'Start-CleanAir.cmd'
$launchers = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.IndexOf($startPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
})
foreach ($launcher in $launchers) {
    $current = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $launcher.ProcessId)
    if ($null -ne $current -and $current.CreationDate -eq $launcher.CreationDate) {
        Stop-Process -Id ([int]$current.ProcessId) -Force
    }
}

if (@(Get-ProjectProcesses).Count -or $null -ne (Get-RegisteredProcess)) {
    throw 'Project processes are still running. No resource-release success is being reported.'
}
$python = Join-Path $root 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'The bundled Python runtime is missing; database release cannot be verified.'
}
& $python -B -c 'import sys; from pathlib import Path; from clean_air.shutdown_control import release_storage; release_storage(Path(sys.argv[1]))' $root
if ($LASTEXITCODE -ne 0) {
    throw 'Database or workspace-lock release failed. Inspect the error above.'
}
if ($requestPath -and (Test-Path -LiteralPath $requestPath)) {
    Remove-Item -LiteralPath $requestPath
}
if (@(Get-ProjectProcesses).Count) {
    throw 'A project process restarted during shutdown. Close it before creating a ZIP.'
}

$resources = @(
    'runtime\python\python.exe', 'runtime\python\_sqlite3.pyd',
    'runtime\prometheus\prometheus.exe', 'var\run\application.lock',
    'var\data\clean_air.sqlite3', 'var\data\clean_air.sqlite3-wal',
    'var\data\clean_air.sqlite3-shm', 'var\monitoring\lock',
    'var\logs\application.jsonl', 'var\logs\prometheus.log'
)
foreach ($relative in $resources) {
    $path = Join-Path $root $relative
    if (Test-Path -LiteralPath $path) {
        $handle = [System.IO.File]::Open(
            $path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::None
        )
        try { $null = $handle.ReadByte() } finally { $handle.Dispose() }
    }
}
Write-Host 'Clean Air Finder is stopped. Project processes and database locks are released.'
Write-Host 'You can create your ZIP now.'
