@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    goto run_py
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    goto run_python
)

echo [ERROR] Python not found. Please install Python 3 and add it to PATH.
exit /b 9009

:run_py
py -3 "%SCRIPT_DIR%check_update.py"
exit /b %ERRORLEVEL%

:run_python
python "%SCRIPT_DIR%check_update.py"
exit /b %ERRORLEVEL%
