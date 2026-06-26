@echo off
cd /d C:\Users\smada\Downloads\supplier-sync\supplier-sync
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set MAX_PRODUCTS_PER_SUPPLIER=0
venv\Scripts\python.exe -m src.main >> data\logs\cron.log 2>&1