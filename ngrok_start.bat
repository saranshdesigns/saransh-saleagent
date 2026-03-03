@echo off
echo ============================================
echo  SaranshDesigns AI Agent - ngrok Tunnel
echo  (Required for Meta Webhook testing)
echo ============================================
echo.
echo Starting ngrok on port 8000...
echo Copy the HTTPS URL and paste it in Meta Developer Portal
echo as: https://YOUR-URL.ngrok-free.app/webhook
echo.
ngrok http 8000
pause
