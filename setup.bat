@echo off
echo ============================================
echo  SaranshDesigns AI Agent - Setup
echo ============================================

echo.
echo [1/4] Creating virtual environment...
python -m venv venv

echo.
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat

echo.
echo [3/4] Installing dependencies...
pip install -r requirements.txt

echo.
echo [4/4] Creating .env file from template...
if not exist .env (
    copy .env.example .env
    echo .env file created. Please open it and fill in your API keys.
) else (
    echo .env already exists. Skipping.
)

echo.
echo ============================================
echo  Setup complete!
echo  Next steps:
echo  1. Open .env and fill in your API keys
echo  2. Run start.bat to launch the agent
echo ============================================
pause
