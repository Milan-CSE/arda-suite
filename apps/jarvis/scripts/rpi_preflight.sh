#!/usr/bin/env bash
set -eu

if [ "$(uname -s)" != "Linux" ]; then
  echo "rpi-check yalnızca Linux/Raspberry Pi üzerinde kullanılabilir." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
errors=0
config_mode=""
config_backend=""
config_model="models/vosk-en"
config_wake_enabled=""
config_wake_backend=""

ok() { printf '  ✅ %s\n' "$1"; }
warn() { printf '  ⚠️  %s\n' "$1"; }
fail() { printf '  ❌ %s\n' "$1"; errors=$((errors + 1)); }

printf 'Jarvis RPi ön kontrolü\n'

if [ -f "$ROOT_DIR/config.local.yaml" ]; then
  ok "config.local.yaml bulundu"
else
  fail "config.local.yaml bulunamadı"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  ok ".venv/bin/python bulundu"
else
  fail ".venv/bin/python bulunamadı"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ] && [ -f "$ROOT_DIR/config.yaml" ]; then
  if config_output=$(cd "$ROOT_DIR" && .venv/bin/python - <<'PY'
from arda.config import load_config

cfg = load_config()
app_mode = cfg.get("app", {}).get("mode")
stt_backend = cfg.get("stt", {}).get("backend")
stt = cfg.get("stt", {})
wake = cfg.get("wake", {})
model = stt.get("vosk_model") or wake.get("vosk_model") or "models/vosk-en"
wake_enabled = cfg.get("app", {}).get("wake_enabled", False)
wake_backend = wake.get("backend") or ""
print(f"{app_mode or ''}|{stt_backend or ''}|{model}|{str(wake_enabled).lower()}|{wake_backend}")
PY
  ); then
    IFS='|' read -r config_mode config_backend config_model config_wake_enabled config_wake_backend <<EOF
$config_output
EOF
    ok "YAML yapılandırması okunuyor"
    if [ "$config_mode" = "daemon" ]; then
      ok "app.mode=daemon"
    else
      warn "app.mode=$config_mode; RPi için daemon önerilir"
    fi
    if [ "$config_backend" = "vosk" ]; then
      ok "stt.backend=vosk"
    else
      warn "stt.backend=$config_backend; RPi için Vosk önerilir"
    fi
    if [ "$config_mode" = "daemon" ] && [ "$config_wake_enabled" != "true" ]; then
      warn "daemon wake_enabled=$config_wake_enabled; headless kullanım için true gerekli"
    fi
    if [ "$config_mode" = "daemon" ] && [ "$config_wake_backend" != "vosk" ]; then
      warn "wake.backend=$config_wake_backend; RPi için vosk önerilir"
    fi
  else
    fail "YAML yapılandırması okunamadı veya geçersiz"
  fi
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  if (cd "$ROOT_DIR" && .venv/bin/python - <<'PY'
import sounddevice as sd

devices = sd.query_devices()
has_input = any(float(device.get("max_input_channels", 0)) > 0 for device in devices)
has_output = any(float(device.get("max_output_channels", 0)) > 0 for device in devices)
raise SystemExit(0 if has_input and has_output else 1)
PY
  ); then
    ok "Ses giriş/çıkış cihazı bulundu"
  else
    fail "Ses giriş/çıkış cihazı bulunamadı; ALSA/USB mikrofon ve hoparlörü kontrol edin"
  fi
fi

if case "$config_model" in /*) [ -d "$config_model" ];; *) [ -d "$ROOT_DIR/$config_model" ];; esac; then
  if case "$config_model" in /*) find "$config_model" -type f -print -quit;; *) find "$ROOT_DIR/$config_model" -type f -print -quit;; esac | grep -q .; then
    ok "Vosk modeli bulundu: $config_model"
  else
    fail "Vosk model klasörü boş: $config_model"
  fi
else
  fail "Vosk modeli bulunamadı: $config_model"
fi

if command -v systemctl >/dev/null 2>&1; then
  ok "systemctl bulundu"
else
  fail "systemctl bulunamadı"
fi

if command -v journalctl >/dev/null 2>&1; then
  ok "journalctl bulundu"
else
  warn "journalctl bulunamadı; servis logları izlenemez"
fi

if command -v espeak-ng >/dev/null 2>&1 || command -v espeak >/dev/null 2>&1; then
  ok "Linux TTS sağlayıcısı bulundu"
else
  fail "espeak-ng/espeak bulunamadı; apt install espeak-ng çalıştırın"
fi

if command -v ollama >/dev/null 2>&1; then
  ok "Ollama CLI bulundu"
else
  warn "Ollama CLI bulunamadı; LLM çağrıları çalışmayacak"
fi

if [ "$errors" -eq 0 ]; then
  echo "✅ Ön kontrol başarılı; servis kurulabilir."
else
  echo "❌ Ön kontrol $errors hata ile tamamlandı." >&2
  exit 1
fi
