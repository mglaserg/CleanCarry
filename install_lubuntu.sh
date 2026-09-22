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

if systemctl is-active --quiet cleancarry-live.service; then
  echo "CleanCarry live is active. Inspect open spot/perp exposure and stop the service before upgrading." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl rsync

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

if [[ ! -e "$APP_DIR/.env" ]]; then
  sudo install -o "$APP_USER" -g "$APP_USER" -m 600 \
    "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env from .env.example"
elif ! sudo test -s "$APP_DIR/.env"; then
  sudo install -o "$APP_USER" -g "$APP_USER" -m 600 \
    "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Initialized empty $APP_DIR/.env from .env.example"
else
  echo "Preserved existing nonempty $APP_DIR/.env"
fi
sudo chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
sudo chmod 600 "$APP_DIR/.env"
sudo test -f "$APP_DIR/.env" || {
  echo "Missing regular configuration file: $APP_DIR/.env" >&2
  exit 1
}
sudo test -s "$APP_DIR/.env" || {
  echo "Configuration file is empty: $APP_DIR/.env" >&2
  exit 1
}

if [[ ! -x "$UV_BIN" ]]; then
  uv_installer="$(mktemp)"
  trap 'rm -f "$uv_installer"' EXIT
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$uv_installer"
  sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh "$uv_installer"
  rm -f "$uv_installer"
  trap - EXIT
fi

"$UV_BIN" --version

sudo -u "$APP_USER" env \
  HOME="$APP_DIR" \
  UV_CACHE_DIR="$APP_DIR/.cache/uv" \
  "$UV_BIN" sync --project "$APP_DIR" --python 3.12 --locked --no-dev \
    --no-editable --no-build-package pyarrow

sudo cp "$APP_DIR"/systemd/cleancarry-*.service /etc/systemd/system/
sudo cp "$APP_DIR"/systemd/cleancarry-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u "$APP_USER" test -r "$APP_DIR/.env" || {
  echo "Configuration is not readable by $APP_USER: $APP_DIR/.env" >&2
  exit 1
}

echo
echo "Installed to $APP_DIR with $($UV_BIN --version)"
sudo stat -c 'Configuration verified: %n (mode %a, owner %U:%G)' "$APP_DIR/.env"
echo "Next: sudoedit $APP_DIR/.env"
echo "Test: sudo -u $APP_USER env HOME=$APP_DIR UV_CACHE_DIR=$APP_DIR/.cache/uv $UV_BIN run --project $APP_DIR --frozen --no-sync cleancarry opportunities"
echo "Then enable timers: sudo systemctl enable --now cleancarry-scan.timer cleancarry-account.timer"
