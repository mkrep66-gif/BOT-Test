@echo off
title XRPL bots - Status
ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl status"
echo.
pause
