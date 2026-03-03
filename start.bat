@echo off
echo ============================================
echo  SaranshDesigns AI Agent - Starting...
echo ============================================

call venv\Scripts\activate.bat

echo.
echo Agent is starting on http://localhost:8000
echo Press Ctrl+C to stop.
echo.

python main.py
pause
