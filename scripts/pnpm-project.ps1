$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ManagedRegistry = "https://packagefeedproxy.microsoft.io/npm/"

Set-Location $ProjectRoot
$PackageManager = (Get-Content -Raw "package.json" | ConvertFrom-Json).packageManager
if ($PackageManager -notmatch '^pnpm@[^@]+$') {
    Write-Error "package.json must pin packageManager as pnpm@<version>."
    exit 1
}

# npm obeys the managed registry; Corepack does not inherit .npmrc. Prefer the
# existing npm exec cache, then allow only the managed feed on a cache miss.
$env:npm_config_registry = $ManagedRegistry
$env:npm_config_replace_registry_host = "always"

$CachedPnpm = & npm exec --offline --yes "--package=$PackageManager" -- where.exe pnpm 2>$null |
    Select-Object -First 1
$Cached = $LASTEXITCODE -eq 0 -and $CachedPnpm -and (Test-Path $CachedPnpm)

if ($Cached) {
    & $CachedPnpm @args
} else {
    & npm exec --yes "--package=$PackageManager" -- pnpm @args
}
exit $LASTEXITCODE
