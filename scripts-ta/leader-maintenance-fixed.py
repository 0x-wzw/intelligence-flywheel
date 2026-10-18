#!/usr/bin/python3
"""Wrapper script for leader-maintenance.py that ensures the correct interpreter is used."""

import subprocess
import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
script_path = os.path.join(script_dir, "leader-maintenance.py")

# Ensure we use the correct interpreter regardless of how this script is invoked
subprocess.run(["/usr/bin/python3", script_path], check=True)