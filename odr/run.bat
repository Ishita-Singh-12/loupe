@echo off
setlocal
cd /d "%~dp0\.."
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and retry.
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10+ is required. Your default python is older.
  exit /b 1
)
if not exist odr\.venv\Scripts\python.exe (
  echo First run: creating virtual environment and installing dependencies...
  python -m venv odr\.venv || exit /b 1
  odr\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip || exit /b 1
  odr\.venv\Scripts\python.exe -m pip install --quiet -e "sdk[langchain]" -r odr\requirements.txt || exit /b 1
)
odr\.venv\Scripts\python.exe odr\launcher.py %*
