#!/usr/bin/env bash
set -eu

if [ "$(uname -s)" != "Linux" ]; then
  echo "rpi-e2e-smoke yalnızca Linux/Raspberry Pi üzerinde kullanılabilir." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
listen=0
if [ "${1:-}" = "--listen" ]; then
  listen=1
elif [ "${1:-}" != "" ]; then
  echo "Kullanım: $0 [--listen]" >&2
  exit 2
fi

echo "Jarvis RPi E2E smoke kontrolü"
"$ROOT_DIR/scripts/rpi_preflight.sh"

if ! systemctl --user is-active --quiet jarvis.service; then
  echo "❌ jarvis.service aktif değil." >&2
  systemctl --user --no-pager --full status jarvis.service || true
  exit 1
fi
echo "✅ jarvis.service aktif"

if ! curl -fsS --max-time 3 http://localhost:11434/api/version >/dev/null; then
  echo "❌ Ollama /api/version erişilemiyor." >&2
  exit 1
fi
echo "✅ Ollama erişilebilir"

if ! curl -fsS --max-time 3 http://localhost:6333/collections/mem0_local >/dev/null; then
  echo "⚠️ Qdrant/mem0_local erişilemiyor; memory E2E devre dışı kalır." >&2
else
  echo "✅ Qdrant mem0_local erişilebilir"
fi

if [ "$listen" -eq 1 ]; then
  echo "Dinleme tetiği gönderiliyor; konuşma sonrası journalctl ile akışı kontrol edin."
  "$ROOT_DIR/scripts/jarvisctl" listen
  echo "✅ Dinleme tetiği gönderildi"
else
  echo "ℹ️ Ses döngüsü tetiklenmedi. Gerçek akış için: $0 --listen"
fi

echo "✅ RPi smoke kontrolü tamamlandı"
