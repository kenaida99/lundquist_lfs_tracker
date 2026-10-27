#!/bin/sh
# First-time install on an Ubuntu/Debian VPS, run as root. Expects the code in /tmp/lfs_code.tgz
# (and optionally data/ in /tmp/lfs_data.tgz). Safe to run again: it keeps the existing login.
set -eu
APP=/opt/lfs_tracker

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip curl openssl

id lfs >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/lfs --shell /usr/sbin/nologin lfs
mkdir -p "$APP"
tar -xzf /tmp/lfs_code.tgz -C "$APP"
[ -f /tmp/lfs_data.tgz ] && tar -xzf /tmp/lfs_data.tgz -C "$APP"
mkdir -p "$APP/data"
find "$APP/deploy" -type f | xargs sed -i 's/\r$//'  # files copied from Windows

[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R lfs:lfs "$APP"

if [ ! -f /etc/lfs-tracker.env ]; then
  PW=$(openssl rand -base64 18 | tr -d '/+=')
  (umask 077; printf 'WEB_USERNAME=admin\nWEB_PASSWORD=%s\nPUBLIC_URL=\n' "$PW" > /etc/lfs-tracker.env)
  echo "Generated web login: admin / $PW"
fi

install -m 644 "$APP/deploy/lfs-tracker.service" /etc/systemd/system/lfs-tracker.service
systemctl daemon-reload
systemctl enable --now lfs-tracker
sleep 3
systemctl --no-pager --full status lfs-tracker | head -n 6
