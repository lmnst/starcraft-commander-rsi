# Start a live RSI batch on an OpenAI-compatible HTTP API. Ctrl+C stops it as a manual stop.
# The endpoint and API key are read from parameters or environment variables and are never written to the batch.
param(
    [int]$Rounds = 10,
    [int]$MatchesPerRound = 2,
    [int]$Parallel = 1,
    [string]$SeedVersion = "",
    [switch]$NoEvolve,
    [int]$MaxFrames = 0,
    [string]$Model = "claude-opus-5-5",
    [string]$LiveEffort = "low",
    [string]$ReviewEffort = "high",
    [string]$Map = "Destination",
    # One bot, or several (-Opponent PylonPuller,Locutus) that matches cycle through in match order.
    [string[]]$Opponent = @("PylonPuller"),
    [string]$Endpoint = "",
    [string]$ApiKeyEnv = ""
)

$ErrorActionPreference = "Stop"

function Resolve-EnvValue([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
        if ($value) { [Environment]::SetEnvironmentVariable($Name, $value, "Process") }
    }
    return $value
}

if (-not $Endpoint) {
    foreach ($candidate in @("RSI_API_ENDPOINT", "OPENAI_BASE_URL")) {
        $Endpoint = Resolve-EnvValue $candidate
        if ($Endpoint) { break }
    }
}
if (-not $Endpoint) { throw "Set -Endpoint or environment variable RSI_API_ENDPOINT / OPENAI_BASE_URL" }

if ($ApiKeyEnv) {
    if (-not (Resolve-EnvValue $ApiKeyEnv)) { throw "$ApiKeyEnv is not set" }
} else {
    foreach ($candidate in @("OPENAI_API_KEY", "RSI_API_KEY", "AGROUTER_API_KEY")) {
        if (Resolve-EnvValue $candidate) {
            $ApiKeyEnv = $candidate
            break
        }
    }
    if (-not $ApiKeyEnv) { throw "Set OPENAI_API_KEY (or pass -ApiKeyEnv)" }
}

Set-Location (Split-Path -Parent $PSScriptRoot)
$extra = @()
if ($SeedVersion) { $extra += @("--seed-version", (Resolve-Path $SeedVersion).Path) }
if ($NoEvolve) { $extra += "--no-evolve" }
if ($MaxFrames -gt 0) { $extra += @("--max-frames", $MaxFrames) }
py -3.12 app/rsi_live.py `
    --rounds $Rounds --matches-per-round $MatchesPerRound --parallel $Parallel --map $Map --opponent ($Opponent -join ",") `
    --backend openai --endpoint $Endpoint --api-key-env $ApiKeyEnv `
    --live-model $Model --review-model $Model --reasoning-effort $LiveEffort `
    --review-mode agentic --review-reasoning-effort $ReviewEffort --review-timeout 180 @extra
exit $LASTEXITCODE
