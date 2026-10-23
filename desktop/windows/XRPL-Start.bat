@echo off
title XRPL bots - Start
echo Starting the bots on the server...
ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl start && sudo xrplarb-ctl status"
echo.
pause
