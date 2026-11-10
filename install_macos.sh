#!/usr/bin/env bash
# ==============================================================================
# Script de Instalação e Adequação para macOS (BLE + ANT+)
# Compatível com Apple Silicon (M1/M2/M3/M4) e Intel (x86_64)
# macOS Monterey, Ventura, Sonoma, Sequoia e superiores
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Estilos e Cores para o terminal
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    BOLD=''
    NC=''
fi

info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

warning() {
    echo -e "${YELLOW}[AVISO]${NC} $*"
}

error() {
    echo -e "${RED}[ERRO]${NC} $*" >&2
}

echo -e "${BOLD}${CYAN}================================================================${NC}"
echo -e "${BOLD}${CYAN} Instalador do Monitor de Frequência Cardíaca no macOS${NC}"
echo -e "${BOLD}${CYAN}================================================================${NC}"
echo ""

# ------------------------------------------------------------------------------
# 1. Verificar Sistema Operacional e Arquitetura
# ------------------------------------------------------------------------------
if [ "$(uname -s)" != "Darwin" ]; then
    error "Este instalador é exclusivo para macOS. Para distribuições Linux, utilize ./install.sh."
    exit 1
fi

ARCH="$(uname -m)"
MACOS_VER="$(sw_vers -productVersion 2>/dev/null || echo "desconhecida")"

info "macOS detectado: versão ${BOLD}${MACOS_VER}${NC} (Arquitetura: ${BOLD}${ARCH}${NC})"

# Definir caminhos padrão do Homebrew de acordo com a arquitetura
if [ "$ARCH" = "arm64" ]; then
    BREW_DEFAULT_PREFIX="/opt/homebrew"
else
    BREW_DEFAULT_PREFIX="/usr/local"
fi

# ------------------------------------------------------------------------------
# 2. Verificar ou Configurar o Homebrew
# ------------------------------------------------------------------------------
info "Verificando instalação do Homebrew..."

if ! command -v brew >/dev/null 2>&1; then
    # Checar se brew está nos diretórios padrão mas fora do PATH da sessão
    if [ -x "${BREW_DEFAULT_PREFIX}/bin/brew" ]; then
        eval "$("${BREW_DEFAULT_PREFIX}/bin/brew" shellenv)"
    fi
fi

if ! command -v brew >/dev/null 2>&1; then
    warning "Homebrew não encontrado. O Homebrew é necessário para instalar Python e libusb."
    echo ""
    echo "Deseja instalar o Homebrew agora automaticamente? (s/n)"
    read -r resp
    if [[ "$resp" =~ ^[sSyY]$ ]]; then
        info "Instalando Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [ -x "${BREW_DEFAULT_PREFIX}/bin/brew" ]; then
            eval "$("${BREW_DEFAULT_PREFIX}/bin/brew" shellenv)"
        fi
    else
        error "Instale o Homebrew manualmente (https://brew.sh) e execute este script novamente."
        exit 1
    fi
fi

BREW_PREFIX="$(brew --prefix 2>/dev/null || echo "$BREW_DEFAULT_PREFIX")"
success "Homebrew operacional (Prefix: $BREW_PREFIX)"

# ------------------------------------------------------------------------------
# 3. Instalar Dependências via Homebrew (libusb e Python)
# ------------------------------------------------------------------------------
info "Instalando biblioteca libusb (necessária para dongles ANT+)..."
if ! brew list libusb >/dev/null 2>&1; then
    brew install libusb
else
    success "libusb já está instalada via Homebrew."
fi

# Verificar Python 3
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
    PY_MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
    PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"
    if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 8 ]; then
        PYTHON_BIN="$(command -v python3)"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    info "Python >= 3.8 não encontrado. Instalando via Homebrew..."
    brew install python
    PYTHON_BIN="$(brew --prefix)/bin/python3"
fi

PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
success "Python utilizado: $PYTHON_BIN (versão $PY_VER)"

# ------------------------------------------------------------------------------
# 4. Configurar Ambiente Virtual Python (.venv) e Dependências
# ------------------------------------------------------------------------------
info "Configurando ambiente virtual Python (.venv)..."

VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV_DIR/bin/python" ]; then
    rm -rf "$VENV_DIR" 2>/dev/null || true
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

info "Atualizando pip, setuptools e wheel..."
"$VENV_PIP" install --upgrade pip setuptools wheel --quiet

info "Instalando pacotes Python do projeto (Bleak e OpenANT)..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$VENV_PIP" install -r "$SCRIPT_DIR/requirements.txt"
else
    "$VENV_PIP" install "bleak>=0.22.3,<0.23" "openant>=1.3.4,<2"
fi

success "Ambiente Python virtual (.venv) configurado com sucesso!"

# ------------------------------------------------------------------------------
# 5. Criar Atalhos de Execução
# ------------------------------------------------------------------------------
# run.sh
RUN_SCRIPT="$SCRIPT_DIR/run.sh"
cat << 'EOF' > "$RUN_SCRIPT"
#!/usr/bin/env bash
# Atalho para executar o monitor de frequência cardíaca no ambiente virtual correto

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ "$(uname -s)" = "Darwin" ]; then
    export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
fi

if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$SCRIPT_DIR/monitor_batimentos.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/monitor_batimentos.py" "$@"
else
    echo "Erro: Python não encontrado." >&2
    exit 1
fi
EOF
chmod +x "$RUN_SCRIPT"

# Iniciar_Monitor_Cardiaco.command (atalho de duplo clique no Finder)
COMMAND_SCRIPT="$SCRIPT_DIR/Iniciar_Monitor_Cardiaco.command"
cat << 'EOF' > "$COMMAND_SCRIPT"
#!/usr/bin/env bash
# Atalho do macOS para Iniciar o Monitor Cardíaco (Clique Duplo no Finder)

cd "$(dirname "$0")"

echo "========================================================"
echo " Iniciando Monitor de Frequência Cardíaca (macOS)"
echo "========================================================"
echo ""

./run.sh "$@"

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "[AVISO] O processo foi encerrado com código $EXIT_CODE."
    echo "Pressione [ENTER] para fechar esta janela..."
    read -r
fi
EOF
chmod +x "$COMMAND_SCRIPT"

success "Atalhos './run.sh' e 'Iniciar_Monitor_Cardiaco.command' criados e executáveis."

# ------------------------------------------------------------------------------
# 6. Verificação de Permissões Bluetooth (macOS TCC)
# ------------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${CYAN}--- Verificação de Permissões Bluetooth no macOS ---${NC}"
info "No macOS, o aplicativo de terminal precisa de permissão de acesso ao Bluetooth."
info "Disparando teste de Bluetooth (se aparecer um pop-up do sistema, clique em 'Permitir')..."

BT_TEST_OUTPUT="$("$VENV_PYTHON" -c "
import asyncio, sys
from bleak import BleakScanner

async def test():
    try:
        await BleakScanner.discover(timeout=2)
        print('AUTH_OK')
    except Exception as e:
        msg = str(e).lower()
        if 'not authorized' in msg or 'authorized' in msg:
            print('NOT_AUTHORIZED')
        elif 'powered off' in msg:
            print('POWERED_OFF')
        else:
            print(f'ERR:{e}')

asyncio.run(test())
" 2>&1 || true)"

if echo "$BT_TEST_OUTPUT" | grep -q "AUTH_OK"; then
    success "Acesso ao Bluetooth concedido e funcionando perfeitamente!"
elif echo "$BT_TEST_OUTPUT" | grep -q "POWERED_OFF"; then
    warning "O Bluetooth do seu Mac parece estar desligado. Ative o Bluetooth na Central de Controle."
elif echo "$BT_TEST_OUTPUT" | grep -q "NOT_AUTHORIZED"; then
    error "Permissão de Bluetooth NÃO concedida para o Terminal!"
    echo ""
    echo -e "${YELLOW}Como resolver:${NC}"
    echo "1. Abra Ajustes do Sistema > Privacidade e Segurança > Bluetooth."
    echo "2. Ative a permissão para o seu aplicativo de Terminal (Terminal, iTerm2, VS Code, etc.)."
    echo ""
    echo "Deseja abrir os Ajustes de Bluetooth agora? (s/n)"
    read -r open_pref
    if [[ "$open_pref" =~ ^[sSyY]$ ]]; then
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth" 2>/dev/null || open /System/Library/PreferencePanes/Security.prefPane 2>/dev/null || true
    fi
else
    info "Teste de descoberta Bluetooth executado. Resposta: $BT_TEST_OUTPUT"
    echo "       (Se for a primeira execução, autorize o Terminal caso solicitado pelo macOS)."
fi

# ------------------------------------------------------------------------------
# 7. Diagnóstico do Suporte ANT+ (PyUSB + libusb)
# ------------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${CYAN}--- Verificação do Suporte ANT+ USB ---${NC}"

LIBUSB_STATUS="$("$VENV_PYTHON" -c "
import antplus
import usb.core
try:
    usb.core.find()
    print('OK')
except usb.core.NoBackendError:
    print('NO_BACKEND')
except Exception:
    print('OK')
" 2>/dev/null || echo "ERROR")"

if [ "$LIBUSB_STATUS" = "OK" ]; then
    success "Backend libusb (PyUSB) carregado com sucesso para sensores ANT+."
else
    warning "Aviso: libusb não pôde ser vinculado pelo PyUSB. Verifique 'brew install libusb'."
fi

# Checar dongle USB físico conectado
ANT_DEVICE_FOUND=false
if system_profiler SPUSBDataType 2>/dev/null | grep -qiE "0x0fcf|1008|1009|Dynastream|ANTUSB"; then
    ANT_DEVICE_FOUND=true
fi

if [ "$ANT_DEVICE_FOUND" = true ]; then
    success "Dongle USB ANT+ conectado e reconhecido pelo macOS!"
else
    info "Nenhum dongle ANT+ USB conectado no momento."
    echo "       (Sensores BLE funcionarão normalmente. Ao plugar o dongle ANT+, ele será reconhecido automaticamente)."
fi

echo ""
echo -e "${BOLD}${GREEN}================================================================${NC}"
echo -e "${BOLD}${GREEN} Instalação no macOS concluída com sucesso!${NC}"
echo -e "${BOLD}${GREEN}================================================================${NC}"
echo ""
echo "Você pode iniciar o monitor de 3 formas:"
echo -e "  1. No Finder: dê um duplo clique em ${BOLD}Iniciar_Monitor_Cardiaco.command${NC}"
echo -e "  2. No Terminal: ${BOLD}./run.sh --list${NC}  (para listar sensores próximos)"
echo -e "  3. No Terminal: ${BOLD}./run.sh${NC}         (para monitorar)"
echo ""
echo -e "Dashboard OBS Studio: ${CYAN}http://127.0.0.1:8765/index.htm${NC}"
echo ""
