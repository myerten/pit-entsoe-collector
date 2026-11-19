@echo off
REM PIT-Audit yerel toplayıcı (Windows). Anahtarı aşağıya yazın.
set ENTSOE_API_KEY=BURAYA_API_ANAHTARI
set COLLECTOR_ID=kku-local
cd /d "%~dp0"
python collector.py >> collector.log 2>&1
