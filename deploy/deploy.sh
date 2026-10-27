#!/bin/sh
# Run on the VPS after the new code is in /opt/lfs_tracker (git pull, or copied over):
# updates the dependencies and restarts the service.
set -eu
cd /opt/lfs_tracker
sudo -u lfs .venv/bin/pip install -q -r requirements.txt
systemctl restart lfs-tracker
sleep 2
systemctl --no-pager --full status lfs-tracker | head -n 5
