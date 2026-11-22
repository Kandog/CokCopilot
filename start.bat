@echo off
rem CokCopilot launcher: builds the UI on first run, then starts the app.
setlocal
cd /d "%~dp0"

if not exist "frontend\dist\index.html" (
  echo Building the user interface ^(first run only^)...
  pushd frontend
  call npm install || goto :fail
  call npm run build || goto :fail
  popd
)

cd backend
uv run python -m cokcopilot %*
exit /b %errorlevel%

:fail
echo.
echo Build failed. Make sure Node.js 18+ is installed: https://nodejs.org
pause
exit /b 1
