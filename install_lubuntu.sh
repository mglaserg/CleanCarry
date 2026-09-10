#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as your normal sudo-capable user, not root." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip

if ! id cleancarry >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /opt/cleancarry --shell /usr/sbin/nologin cleancarry
fi

sudo mkdir -p /opt/cleancarry
sudo cp -a . /opt/cleancarry/
sudo chown -R cleancarry:cleancarry /opt/cleancarry

sudo -u cleancarry python3 -m venv /opt/cleancarry/.venv
sudo -u cleancarry /opt/cleancarry/.venv/bin/pip install --upgrade pip
sudo -u cleancarry /opt/cleancarry/.venv/bin/pip install -e /opt/cleancarry

if [[ ! -f /opt/cleancarry/.env ]]; then
  sudo -u cleancarry cp /opt/cleancarry/.env.example /opt/cleancarry/.env
fi
sudo chmod 600 /opt/cleancarry/.env

sudo cp /opt/cleancarry/systemd/cleancarry-*.service /etc/systemd/system/
sudo cp /opt/cleancarry/systemd/cleancarry-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

echo
echo "Installed to /opt/cleancarry"
echo "Next: sudoedit /opt/cleancarry/.env"
echo "Test: sudo -u cleancarry /opt/cleancarry/.venv/bin/cleancarry opportunities"
echo "Then enable timers: sudo systemctl enable --now cleancarry-scan.timer cleancarry-account.timer"
