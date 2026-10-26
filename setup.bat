@echo off
cd /d "%~dp0"
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt || goto :err
if not exist data\engine_v2.db python -m pe.v2 rebuild
pytest -q
echo.
echo Ready. Activate with:  .venv\Scripts\activate
pause
exit /b 0
:err
echo Setup failed. Is Python 3.11+ installed and on PATH?
pause
exit /b 1
