#!/usr/bin/env bash
# ==============================================================================
# Jarvis — Tek Komutla macOS Kurulum Betiği (One-Line Installer)
# ==============================================================================
# Kullanım:
#   curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
#   veya repo içinde:
#   ./install.sh
# ==============================================================================

set -eo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
RED="\033[0;31m"
RESET="\033[0m"

log_info() { echo -e "${BLUE}[INFO]${RESET} $*"; }
log_ok() { echo -e "${GREEN}[OK]${RESET} $*"; }
log_warn() { echo -e "${YELLOW}[UYARI]${RESET} $*"; }
log_error() { echo -e "${RED}[HATA]${RESET} $*"; }

echo -e "${BOLD}"
echo "=========================================================="
echo "          🤖 Jarvis Kurulumu"
echo "      macOS Yerel Yapay Zekâ Sesli Masaüstü Asistanı"
echo "=========================================================="
echo -e "${RESET}"

# 1. macOS Denetimi
if [[ "$(uname)" != "Darwin" ]]; then
    log_error "Jarvis yalnızca macOS üzerinde çalışır."
    exit 1
fi

ARCH="$(uname -m)"
log_info "Sistem: macOS ($(sw_vers -productVersion)), Mimari: $ARCH"
if [[ "$ARCH" == "arm64" ]]; then
    log_ok "Apple Silicon tespit edildi (MLX hızlandırması tam uyumlu)."
else
    log_warn "Intel x86_64 tespit edildi. Apple Silicon (M serisi) önerilir."
fi

# 2. Xcode Command Line Tools
if ! xcode-select -p &>/dev/null; then
    log_info "Xcode Command Line Tools kuruluyor..."
    xcode-select --install || true
    echo "Lütfen açılan penceredeki kurulumu tamamlayıp bu betiği tekrar çalıştırın."
    exit 0
else
    log_ok "Xcode Command Line Tools mevcut."
fi

# 3. Homebrew & ffmpeg Denetimi
if ! command -v brew &>/dev/null; then
    log_warn "Homebrew bulunamadı. Lütfen Homebrew kurun: https://brew.sh"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
else
    log_ok "Homebrew mevcut."
    if ! command -v ffmpeg &>/dev/null; then
        log_info "ffmpeg kuruluyor (ses işleme için zorunlu)..."
        brew install ffmpeg
    else
        log_ok "ffmpeg kurulu."
    fi
fi

# 4. Ollama Denetimi
if ! command -v ollama &>/dev/null; then
    log_warn "Ollama bulunamadı. Kurulum önerilir:"
    echo "  brew install --cask ollama"
    echo "  veya https://ollama.com adresinden indirin."
else
    log_ok "Ollama mevcut."
    # Ollama servisi çalışıyor mu?
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        log_ok "Ollama servisi çalışıyor."
        log_info "qwen3:8b modeli indiriliyor/kontrol ediliyor..."
        ollama pull qwen3:8b || log_warn "qwen3:8b modeli çekilemedi (daha sonra: ollama pull qwen3:8b yapabilirsiniz)."
    else
        log_warn "Ollama uygulaması açık değil. Lütfen Ollama'yı başlatın."
    fi
fi

# 5. Dizin ve Repo Durumu
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# 6. Python Sürümü ve Virtual Environment
PYTHON_BIN=""
for cmd in python3.11 python3.12 python3; do
    if command -v $cmd &>/dev/null; then
        VER=$($cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        # MLX ve C-bağımlılıkları için Python 3.10-3.12 en kararlı sürümlerdir
        if [[ "$VER" == "3.10" || "$VER" == "3.11" || "$VER" == "3.12" ]]; then
            PYTHON_BIN="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    if command -v brew &>/dev/null; then
        log_info "Python 3.11 kuruluyor..."
        brew install python@3.11
        PYTHON_BIN="/opt/homebrew/opt/python@3.11/bin/python3.11"
    else
        PYTHON_BIN="python3"
    fi
fi

log_info "Kullanılan Python: $($PYTHON_BIN --version) ($PYTHON_BIN)"

if [[ ! -d ".venv" ]]; then
    log_info "Sanal ortam (.venv) oluşturuluyor..."
    "$PYTHON_BIN" -m venv .venv
fi
log_ok "Sanal ortam hazır."

# 7. Paket Kurulumu
log_info "Bağımlılıklar kuruluyor..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
.venv/bin/pip install -e . -q
log_ok "Python paketleri kuruldu."

# 8. Modeller (Vosk & openWakeWord)
log_info "Gereken modeller kontrol ediliyor..."
make models-vosk || true

# 9. config.local.yaml
if [[ ! -f "config.local.yaml" ]]; then
    log_info "config.local.yaml oluşturuluyor..."
    VAULT_DIR="$HOME/Documents/JarvisVault"
    mkdir -p "$VAULT_DIR/Inbox" "$VAULT_DIR/Daily" "$VAULT_DIR/Tasks"
    sed "s|path: .*|path: $VAULT_DIR|g" config.local.example.yaml > config.local.yaml
    log_ok "config.local.yaml oluşturuldu (Vault: $VAULT_DIR)."
else
    log_ok "config.local.yaml zaten mevcut."
fi

# 10. macOS İzinleri Denetimi
echo ""
log_info "macOS İzinleri denetleniyor..."
.venv/bin/python -c 'from arda.permissions import permission_report; print(permission_report())' || true

# 11. Bitiş ve Çalıştırma Rehberi
echo ""
echo -e "${GREEN}${BOLD}==========================================================${RESET}"
echo -e "${GREEN}${BOLD}      🎉 Jarvis Kurulumu Başarıyla Tamamlandı!           ${RESET}"
echo -e "${GREEN}${BOLD}==========================================================${RESET}"
echo ""
echo "Uygulamayı başlatmak için:"
echo -e "  ${BOLD}make run${RESET}           (Terminalden menü çubuğu asistanını başlatır)"
echo ""
echo "macOS .app paketi oluşturmak için:"
echo -e "  ${BOLD}make app${RESET}           (dist/Jarvis.app üretir, çift tıklayarak açabilirsiniz)"
echo ""
echo "Kısayollar:"
echo "  • ⌘⇧A (Cmd+Shift+A) : Jarvis Sesli Komut / Dinleme"
echo "  • ⌘⇧D (Cmd+Shift+D) : Anında Dikte (İmlecin olduğu yere yazar)"
echo ""
