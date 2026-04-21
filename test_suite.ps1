$ErrorActionPreference = "SilentlyContinue"
Set-Location $PSScriptRoot
$Base = "http://127.0.0.1:18921"

$line = Select-String -Path "logs\app.log" -Pattern "token=" | Select-Object -Last 1
$parts = $line.Line -split "token="
$Token = ($parts[1] -split "[\s?]") | Select-Object -First 1
Write-Host "Token: $Token"
if (-not $Token) { Write-Host "ERROR: No token"; exit 1 }

$P = 0; $F = 0

function ok($m) { Write-Host "  OK: $m"; $script:P++ }
function ng($m) { Write-Host "  FAIL: $m"; $script:F++ }

Write-Host ""
Write-Host "--- 1. Status ---"
$j = (Invoke-RestMethod "$Base/api/status?token=$Token") | ConvertTo-Json -Compress
Write-Host "  $j"
if ($j -match 'model_loaded.:true') { ok "1.1 model loaded" } else { ng "1.1 model loaded" }
if ($j -match 'engine_state.:.IDLE') { ok "1.2 state IDLE" } else { ng "1.2 state IDLE" }

Write-Host ""
Write-Host "--- 2. Audio Devices ---"
$j = (Invoke-RestMethod "$Base/api/audio/devices?token=$Token") | ConvertTo-Json -Compress
Write-Host "  $j"
if ($j -match 'ok.:true') { ok "2.1 audio devices" } else { ng "2.1 audio devices" }

Write-Host ""
Write-Host "--- 3. Config ---"
$j = (Invoke-RestMethod "$Base/api/config?token=$Token") | ConvertTo-Json -Compress
Write-Host "  $j"
if ($j -match 'engine') { ok "3.1 config read" } else { ng "3.1 config read" }

Write-Host ""
Write-Host "--- 4. Record Start/Stop ---"
$j = (Invoke-RestMethod "$Base/api/record/start?token=$Token" -Method POST -ContentType "application/json" -Body "{}") | ConvertTo-Json -Compress
Write-Host "  Start: $j"
if ($j -match 'ok.:true') { ok "4.1 record start" } else { ng "4.1 record start" }

Start-Sleep 2

$j = (Invoke-RestMethod "$Base/api/record/stop?token=$Token" -Method POST -ContentType "application/json" -Body "{}") | ConvertTo-Json -Compress
Write-Host "  Stop: $j"
if ($j -match 'ok.:true') { ok "4.2 record stop" } else { ng "4.2 record stop" }

Start-Sleep 5
$st = (Invoke-RestMethod "$Base/api/status?token=$Token").engine_state
Write-Host "  State: $st"
if ($st -eq "IDLE") { ok "4.3 back to IDLE" } else { ng "4.3 back to IDLE" }

Write-Host ""
Write-Host "--- 5. Auto Transcribe (3s) ---"
$j = (Invoke-RestMethod "$Base/api/test/transcribe?token=$Token" -Method POST -ContentType "application/json" -Body '{"duration":3}') | ConvertTo-Json -Compress
Write-Host "  $j"
if ($j -match 'ok.:true') { ok "5.1 auto transcribe" } else { ng "5.1 auto transcribe" }

Write-Host ""
Write-Host "--- 6. Edge Cases ---"
Invoke-RestMethod "$Base/api/record/start?token=$Token" -Method POST -ContentType "application/json" -Body "{}" | Out-Null
$j = (Invoke-RestMethod "$Base/api/record/start?token=$Token" -Method POST -ContentType "application/json" -Body "{}") | ConvertTo-Json -Compress
if ($j -match 'ok.:false') { ok "6.1 double start rejected" } else { ng "6.1 double start" }

Invoke-RestMethod "$Base/api/record/stop?token=$Token" -Method POST -ContentType "application/json" -Body "{}" | Out-Null
$j = (Invoke-RestMethod "$Base/api/record/stop?token=$Token" -Method POST -ContentType "application/json" -Body "{}") | ConvertTo-Json -Compress
if ($j -match 'ok.:false') { ok "6.2 double stop rejected" } else { ng "6.2 double stop" }

Start-Sleep 5

Write-Host ""
Write-Host "--- 7. Hot Switch funasr ---"
$j = (Invoke-RestMethod "$Base/api/config?token=$Token" -Method PUT -ContentType "application/json" -Body '{"stt":{"engine":"funasr"}}') | ConvertTo-Json -Compress
Write-Host "  Config: $j"
if ($j -match 'ok.:true') { ok "7.1 config update" } else { ng "7.1 config update" }

Write-Host "  Waiting 15s for FunASR..."
Start-Sleep 15

$j = (Invoke-RestMethod "$Base/api/status?token=$Token") | ConvertTo-Json -Compress
Write-Host "  Status: $j"
if ($j -match 'model_loaded.:true') { ok "7.2 funasr loaded" } else { ng "7.2 funasr loaded" }
if ($j -match 'engine_state.:.IDLE') { ok "7.3 state IDLE" } else { ng "7.3 state IDLE" }

Write-Host ""
Write-Host "--- 8. FunASR Transcribe ---"
$j = (Invoke-RestMethod "$Base/api/test/transcribe?token=$Token" -Method POST -ContentType "application/json" -Body '{"duration":3}') | ConvertTo-Json -Compress
Write-Host "  $j"
if ($j -match 'ok.:true') { ok "8.1 funasr transcribe" } else { ng "8.1 funasr transcribe" }

Write-Host ""
Write-Host "--- 9. Switch back faster_whisper ---"
$j = (Invoke-RestMethod "$Base/api/config?token=$Token" -Method PUT -ContentType "application/json" -Body '{"stt":{"engine":"faster_whisper"}}') | ConvertTo-Json -Compress
Write-Host "  Config: $j"
if ($j -match 'ok.:true') { ok "9.1 config update" } else { ng "9.1 config update" }

Write-Host "  Waiting 15s..."
Start-Sleep 15

$j = (Invoke-RestMethod "$Base/api/status?token=$Token") | ConvertTo-Json -Compress
Write-Host "  Status: $j"
if ($j -match 'model_loaded.:true') { ok "9.2 fw loaded" } else { ng "9.2 fw loaded" }
if ($j -match 'engine_state.:.IDLE') { ok "9.3 state IDLE" } else { ng "9.3 state IDLE" }

Write-Host ""
Write-Host "--- 10. 5x Consecutive ---"
$fc = 0
for ($i = 1; $i -le 5; $i++) {
    $j = (Invoke-RestMethod "$Base/api/test/transcribe?token=$Token" -Method POST -ContentType "application/json" -Body '{"duration":2}') | ConvertTo-Json -Compress
    if ($j -match 'ok.:true') { Write-Host "  Round ${i}: OK" } else { Write-Host "  Round ${i}: FAIL"; $fc++ }
}
if ($fc -eq 0) { ok "10.1 5x consecutive" } else { ng "10.1 5x consecutive ($fc failed)" }

Write-Host ""
Write-Host "================================"
Write-Host "  RESULTS: $P passed, $F failed"
Write-Host "================================"
if ($F -gt 0) { exit 1 } else { exit 0 }
