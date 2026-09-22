#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/cleancarry
APP_USER=cleancarry
UV_BIN=/usr/local/bin/uv
UV_VERSION="${UV_VERSION:-0.12.17}"

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as your normal sudo-capable user, not root." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl rsync

if [[ ! -x "$UV_BIN" ]]; then
  uv_installer="$(mktemp)"
  trap 'rm -f "$uv_installer"' EXIT
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$uv_installer"
  sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh "$uv_installer"
  rm -f "$uv_installer"
  trap - EXIT
fi

"$UV_BIN" --version

if ! id "$APP_USER" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

sudo install -d -o "$APP_USER" -g "$APP_USER" \
  "$APP_DIR" "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/state" "$APP_DIR/.cache/uv"

# Preserve operator configuration, uv's environment/cache, and runtime evidence across upgrades.
sudo rsync -a \
  --exclude '.env' \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.cache/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'state/' \
  ./ "$APP_DIR/"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" env \
  HOME="$APP_DIR" \
  UV_CACHE_DIR="$APP_DIR/.cache/uv" \
  "$UV_BIN" sync --project "$APP_DIR" --locked --no-dev --no-editable

if [[ ! -f "$APP_DIR/.env" ]]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
sudo chmod 600 "$APP_DIR/.env"

sudo cp "$APP_DIR"/systemd/cleancarry-*.service /etc/systemd/system/
sudo cp "$APP_DIR"/systemd/cleancarry-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

echo
echo "Installed to $APP_DIR with $($UV_BIN --version)"
echo "Next: sudoedit $APP_DIR/.env"
echo "Test: sudo -u $APP_USER env HOME=$APP_DIR UV_CACHE_DIR=$APP_DIR/.cache/uv $UV_BIN run --project $APP_DIR --frozen --no-sync cleancarry opportunities"
echo "Then enable timers: sudo systemctl enable --now cleancarry-scan.timer cleancarry-account.timer"
