#!/bin/bash
echo "Opening a private, encrypted tunnel to the dashboard."
echo "KEEP THIS WINDOW OPEN while you use it. Close it when you're done."
(sleep 4; open http://localhost:8080) &
ssh -N -L 8080:127.0.0.1:8080 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 botadmin@157.245.255.65
