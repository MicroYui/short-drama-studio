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
# existing npm exec cache without invoking npm, then allow only the managed
# feed on a cache miss.
$env:npm_config_registry = $ManagedRegistry
$env:npm_config_replace_registry_host = "always"

$CachedPnpm = & node (Join-Path $ScriptDir "find-cached-pnpm.mjs") $PackageManager 2>$null |
    Select-Object -First 1
$Cached = $LASTEXITCODE -eq 0 -and $CachedPnpm -and (Test-Path $CachedPnpm)

if ($Cached) {
    & node $CachedPnpm @args
} else {
    & npm exec "--registry=$ManagedRegistry" --replace-registry-host=always --yes "--package=$PackageManager" -- pnpm @args
}
exit $LASTEXITCODE
