#!/bin/bash
ssh -o ServerAliveInterval=15 botadmin@157.245.255.65 "sudo xrplarb-ctl status"
read -n 1 -s -r -p "Press any key to close"
