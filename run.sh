#!/bin/bash
echo "============================================================"
echo "Infocar-Eurotax Mapping Desktop App v4"
echo "OEM as Scoring Field - Make+Model Candidate Selection"
echo "============================================================"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed or not in PATH"
    exit 1
fi

# Check VPN connection
echo "Checking VPN connection..."
if ! ping -c 1 x-catalogue.motork.io &> /dev/null; then
    echo "WARNING: Cannot reach x-catalogue.motork.io"
    echo "Please ensure you are connected to the MotorK VPN."
    echo ""
fi

echo "Starting v4 application..."
python3 main.py
