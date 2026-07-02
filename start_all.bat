@echo off
cd /d "C:\Users\Alon\OneDrive - Alon Control\allonsystem\קלוד\ALONGSI"

:: Kill old instances
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
taskkill /F /IM ngrok.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Start ALONGSI server
start "ALONGSI" /MIN "C:\Users\Alon\AppData\Local\Programs\Python\Python312\python.exe" main.py

:: Wait for server to start
timeout /t 8 /nobreak >nul

:: Start ngrok tunnel
start "ngrok" /MIN "C:\Users\Alon\AppData\Local\Programs\ngrok\ngrok.exe" http 5000 --log "C:\Users\Alon\AppData\Local\Programs\ngrok\ngrok.log" --log-format json
