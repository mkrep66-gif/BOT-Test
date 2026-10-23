@echo off
title XRPL bots - Dashboard (keep this window open)
echo Opening a private, encrypted tunnel to the dashboard.
echo The dashboard opens in your browser in a few seconds.
echo KEEP THIS WINDOW OPEN while you use it. Close it when you're done.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8080"
ssh -N -L 8080:127.0.0.1:8080 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 botadmin@157.245.255.65
echo.
echo Tunnel closed.
pause
