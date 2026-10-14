#!/usr/bin/env bash
set -eu

if [ "$(uname -s)" != "Linux" ]; then
  echo "Bu betik yalnızca Linux/Raspberry Pi üzerinde çalışır." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/jarvis.service"

if [ ! -f "$ROOT_DIR/config.local.yaml" ]; then
  echo "config.local.yaml bulunamadı; önce vault.path ve RPi ayarlarını oluşturun." >&2
  exit 1
fi
if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo ".venv/bin/python bulunamadı; önce bağımlılıkları yükleyin." >&2
  exit 1
fi
if [ ! -x "$ROOT_DIR/scripts/rpi_preflight.sh" ]; then
  echo "scripts/rpi_preflight.sh bulunamadı veya çalıştırılabilir değil." >&2
  exit 1
fi

"$ROOT_DIR/scripts/rpi_preflight.sh"

mkdir -p "$UNIT_DIR"
UNIT_TMP=$(mktemp)
trap 'rm -f "$UNIT_TMP"' EXIT
sed \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=$ROOT_DIR|" \
  -e "s|^ExecStart=.*|ExecStart=$ROOT_DIR/.venv/bin/python -m arda|" \
  "$ROOT_DIR/deploy/jarvis.service" > "$UNIT_TMP"
install -m 0644 "$UNIT_TMP" "$UNIT_PATH"
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" >/dev/null 2>&1 || \
    echo "Uyarı: loginctl enable-linger başarısız; servis login olmadan başlamayabilir." >&2
fi
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
echo "Jarvis systemd servisi etkin: $UNIT_PATH"
systemctl --user --no-pager --full status jarvis.service || true
