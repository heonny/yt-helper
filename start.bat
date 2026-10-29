@echo off
pushd "%~dp0" || goto fail
python -c "import ytmusicapi" 2>nul || python -m pip install --user -r requirements.txt || goto fail
python src\server\app.py
if %errorlevel%==0 exit /b
:fail
pause
