@echo off
title XRPL bots - Stop
echo This stops the bots normally (trades in progress finish first).
echo It is NOT the kill switch - for that, use the red button on the dashboard.
choice /c YN /m "Stop the bots now"
if errorlevel 2 goto end
ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl stop && sudo xrplarb-ctl status"
:end
echo.
pause
