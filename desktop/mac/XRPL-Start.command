#!/bin/bash
echo "Starting the bots on the server..."
ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl start && sudo xrplarb-ctl status"
read -n 1 -s -r -p "Press any key to close"
