#!/bin/bash
echo "This stops the bots normally (trades in progress finish first)."
echo "It is NOT the kill switch - for that, use the red button on the dashboard."
read -r -p "Stop the bots now? [y/N] " a
[[ "$a" =~ ^[Yy]$ ]] && ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl stop && sudo xrplarb-ctl status"
read -n 1 -s -r -p "Press any key to close"
