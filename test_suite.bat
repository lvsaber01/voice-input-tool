@echo off
setlocal enabledelayedexpansion

set BASE=http://127.0.0.1:18921

:: Extract token from log using powershell
for /f "usebackq delims=" %%t in ('powershell -Command "(Select-String -Path logs\app.log -Pattern 'token=' | Select-Object -Last 1).ToString().Split('token=')[1].Split()[0]"') do set TOKEN=%%t

echo Token: %TOKEN%
if "%TOKEN%"=="" (
    echo ERROR: No token found
    exit /b 1
)

set PASS=0
set FAIL=0

call :pass "1.0 Token extracted"
echo.

:: Helper: GET
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/status?token=%TOKEN%') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo %RESP% | findstr /c:"model_loaded" >nul && call :pass "1.1 model loaded" || call :fail "1.1 model loaded"
echo %RESP% | findstr /c:"IDLE" >nul && call :pass "1.2 state IDLE" || call :fail "1.2 state IDLE"
echo.

:: 2. Audio Devices
echo --- 2. Audio Devices ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/audio/devices?token=%TOKEN%') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo %RESP% | findstr /c:"ok" >nul && call :pass "2.1 audio devices" || call :fail "2.1 audio devices"
echo.

:: 3. Config Read
echo --- 3. Config Read ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/config?token=%TOKEN%') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo %RESP% | findstr /c:"engine" >nul && call :pass "3.1 config read" || call :fail "3.1 config read"
echo.

:: 4. Record Start/Stop
echo --- 4. Record Start/Stop ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/record/start?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Start: %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "4.1 record start" || call :fail "4.1 record start"

timeout /t 2 /nobreak >nul

for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/record/stop?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Stop: %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "4.2 record stop" || call :fail "4.2 record stop"

timeout /t 5 /nobreak >nul

for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/status?token=%TOKEN%').engine_state" 2^>nul') do set ST=%%r
echo State: %ST%
echo "%ST%"=="IDLE" && call :pass "4.3 back to IDLE" || call :fail "4.3 back to IDLE"
echo.

:: 5. Auto Transcribe
echo --- 5. Auto Transcribe (3s) ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/test/transcribe?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{\"duration\":3}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "5.1 auto transcribe" || call :fail "5.1 auto transcribe"
echo.

:: 6. Edge Cases
echo --- 6. Edge Cases ---
powershell -Command "Invoke-RestMethod -Uri '%BASE%/api/record/start?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}' | Out-Null" 2>nul
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/record/start?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}').ok" 2^>nul') do set V=%%r
echo %V%=="False" && call :pass "6.1 double start rejected" || call :fail "6.1 double start"

powershell -Command "Invoke-RestMethod -Uri '%BASE%/api/record/stop?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}' | Out-Null" 2>nul
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/record/stop?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{}').ok" 2^>nul') do set V=%%r
echo %V%=="False" && call :pass "6.2 double stop rejected" || call :fail "6.2 double stop"

timeout /t 5 /nobreak >nul
echo.

:: 7. Hot Switch funasr
echo --- 7. Hot Switch funasr ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/config?token=%TOKEN%' -Method PUT -ContentType 'application/json' -Body '{\"stt\":{\"engine\":\"funasr\"}}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Config: %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "7.1 config update" || call :fail "7.1 config update"

echo Waiting 15s for FunASR...
timeout /t 15 /nobreak >nul

for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/status?token=%TOKEN%') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Status: %RESP%
echo %RESP% | findstr /c:"model_loaded.*true" >nul && call :pass "7.2 funasr loaded" || call :fail "7.2 funasr loaded"
echo %RESP% | findstr /c:"IDLE" >nul && call :pass "7.3 state IDLE" || call :fail "7.3 state IDLE"
echo.

:: 8. FunASR Transcribe
echo --- 8. FunASR Transcribe ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/test/transcribe?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{\"duration\":3}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "8.1 funasr transcribe" || call :fail "8.1 funasr transcribe"
echo.

:: 9. Switch back
echo --- 9. Switch back faster_whisper ---
for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/config?token=%TOKEN%' -Method PUT -ContentType 'application/json' -Body '{\"stt\":{\"engine\":\"faster_whisper\"}}') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Config: %RESP%
echo %RESP% | findstr /c:"true" >nul && call :pass "9.1 config update" || call :fail "9.1 config update"

echo Waiting 15s for model...
timeout /t 15 /nobreak >nul

for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod '%BASE%/api/status?token=%TOKEN%') | ConvertTo-Json" 2^>nul') do set RESP=%%r
echo Status: %RESP%
echo %RESP% | findstr /c:"model_loaded.*true" >nul && call :pass "9.2 fw loaded" || call :fail "9.2 fw loaded"
echo %RESP% | findstr /c:"IDLE" >nul && call :pass "9.3 state IDLE" || call :fail "9.3 state IDLE"
echo.

:: 10. 5x Consecutive
echo --- 10. 5x Consecutive ---
set FC=0
for /l %%i in (1,1,5) do (
    for /f "usebackq delims=" %%r in ('powershell -Command "(Invoke-RestMethod -Uri '%BASE%/api/test/transcribe?token=%TOKEN%' -Method POST -ContentType 'application/json' -Body '{\"duration\":2}') | ConvertTo-Json" 2^>nul') do set R=%%r
    echo !R! | findstr /c:"true" >nul && echo Round %%i: OK || (echo Round %%i: FAIL & set /a FC+=1)
)
if !FC!==0 (call :pass "10.1 5x consecutive") else call :fail "10.1 5x consecutive"
echo.

:: SUMMARY
echo ================================
echo   RESULTS: %PASS% passed, %FAIL% failed
echo ================================
if %FAIL% gtr 0 (exit /b 1) else exit /b 0

:pass
set /a PASS+=1
echo   OK: %~1
goto :eof
:fail
set /a FAIL+=1
echo   FAIL: %~1
goto :eof
