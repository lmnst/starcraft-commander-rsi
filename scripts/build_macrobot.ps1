# Build build/MacroBot.dll after proving app/MacroBot.cpp with default parameters equals baseline/Bot.cpp.
# SDK locations come from the "macrobot_build" entry of local.settings.json (not tracked):
#   "macrobot_build": {"cmake": "...cmake.exe", "generator": "Visual Studio 15 2017", "generator_instance": "...",
#                      "bwapi_root": "<dir with include/BWAPI.h>", "bwapi_lib": "...BWAPI.lib", "bwem_root": "<BWEM dir>"}
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$settings = (Get-Content -Raw -Encoding UTF8 "local.settings.json" | ConvertFrom-Json).macrobot_build
if (-not $settings) { throw "local.settings.json has no macrobot_build entry" }

py -3.12 -m app.rsi.macrobot_check | Out-Null
if ($LASTEXITCODE -ne 0) { throw "app/MacroBot.cpp is not equivalent to baseline/Bot.cpp at default parameters" }

$out = Join-Path $root "build/macrobot"
New-Item -ItemType Directory -Force $out | Out-Null
# CMake 3.12 (bundled with VS 2017) has no -S/-B: configure from inside the build directory.
$configure = @((Join-Path $root "app/macrobot"), "-G", $settings.generator, "-A", "Win32",
               "-DBWAPI_ROOT=$($settings.bwapi_root)", "-DBWAPI_LIB=$($settings.bwapi_lib)", "-DBWEM_ROOT=$($settings.bwem_root)")
if ($settings.generator_instance) { $configure += "-DCMAKE_GENERATOR_INSTANCE=$($settings.generator_instance)" }
Push-Location $out
try { & $settings.cmake @configure } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
& $settings.cmake --build $out --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

Copy-Item (Join-Path $out "Release/MacroBot.dll") "build/MacroBot.dll" -Force
$record = [ordered]@{
    source_sha256 = (Get-FileHash "app/MacroBot.cpp" -Algorithm SHA256).Hash.ToLower()
    dll_sha256    = (Get-FileHash "build/MacroBot.dll" -Algorithm SHA256).Hash.ToLower()
    built_at      = (Get-Date).ToString("o")
}
$record | ConvertTo-Json | Out-File -Encoding ascii "build/MacroBot.json"
$record | ConvertTo-Json
