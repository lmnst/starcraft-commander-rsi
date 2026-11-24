# Start an overnight RSI v2 batch (evolvable MacroBot.cpp + Champion/Challenger L0 selection, no PurpleWave).
param(
    [int]$Rounds = 24,
    [int]$Parallel = 2,
    [switch]$NoEvolve,
    [int]$MaxFrames = 0,
    [string]$Model = "claude-opus-5-5",
    [string]$LiveEffort = "low",
    [string]$ReviewEffort = "high",
    [string]$Map = "Destination",
    [string[]]$Opponent = @("PylonPuller", "Steamhammer", "BananaBrain"),
    [string]$Endpoint = "https://agrouter-ng-test.kiwiar.com/v1",
    [string]$ApiKeyEnv = "AGROUTER_API_KEY",
    [string]$BatchDir = ""
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

$explicitPy = "C:\Users\21425\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $explicitPy) {
    $cmd = $explicitPy
    $prefix = @()
} else {
    $cmd = "py"
    $prefix = @("-3.12")
}

if (-not (Resolve-EnvValue $ApiKeyEnv)) { throw "$ApiKeyEnv is not set" }

Set-Location (Split-Path -Parent $PSScriptRoot)
$extra = @()
if ($NoEvolve) { $extra += "--no-evolve" }
if ($MaxFrames -gt 0) { $extra += @("--max-frames", $MaxFrames) }
if ($BatchDir) { $extra += @("--batch-dir", $BatchDir) }

& $cmd @prefix app/rsi_live.py `
    --evolution-mode v2 `
    --rounds $Rounds --matches-per-round ($Opponent.Length) --parallel $Parallel `
    --map $Map --opponent ($Opponent -join ",") `
    --backend openai --endpoint $Endpoint --api-key-env $ApiKeyEnv `
    --live-model $Model --review-model $Model --reasoning-effort $LiveEffort `
    --review-mode agentic --review-reasoning-effort $ReviewEffort --review-timeout 240 @extra
exit $LASTEXITCODE
