#!/usr/bin/env bash
# ==============================================================================
# Script de Instalação e Adequação do Sistema para o Monitor Cardíaco (BLE + ANT+)
# Compatível com as principais distribuições Linux:
# Debian, Ubuntu, Linux Mint, Pop!_OS, Fedora, RHEL, CentOS, Rocky, AlmaLinux,
# Arch Linux, Manjaro, openSUSE, Alpine, Void Linux, etc.
# ==============================================================================

set -uo pipefail

# Diretório base do repositório
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
echo -e "${BOLD}${CYAN} Instalador do Monitor de Frequência Cardíaca BLE e ANT+${NC}"
echo -e "${BOLD}${CYAN}================================================================${NC}"
echo ""

# Redirecionar para o instalador dedicado caso esteja rodando no macOS
if [ "$(uname -s)" = "Darwin" ]; then
    if [ -f "$SCRIPT_DIR/install_macos.sh" ]; then
        info "Sistema macOS detectado. Redirecionando para install_macos.sh..."
        exec "$SCRIPT_DIR/install_macos.sh" "$@"
    fi
fi

# ------------------------------------------------------------------------------
# 1. Identificar Usuário e Nível de Privilégios
# ------------------------------------------------------------------------------
# Detectar o usuário real caso o script tenha sido executado via sudo
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER="$(id -un)"
fi

ACTUAL_GROUP="$(id -gn "$ACTUAL_USER" 2>/dev/null || echo "$ACTUAL_USER")"

CAN_SUDO=false
if [ "$EUID" -eq 0 ]; then
    CAN_SUDO=true
elif command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
        CAN_SUDO=true
    else
        info "Solicitando privilégios de administrador (sudo) para instalar pacotes e regras de hardware..."
        if sudo -v; then
            CAN_SUDO=true
        fi
    fi
fi

run_as_root() {
    if [ "$EUID" -eq 0 ]; then
        "$@"
    elif [ "$CAN_SUDO" = true ]; then
        sudo "$@"
    else
        error "Permissão negada. Comando requer privilégios de root: $*"
        return 1
    fi
}

run_as_user() {
    if [ "$EUID" -eq 0 ] && [ "$ACTUAL_USER" != "root" ]; then
        sudo -u "$ACTUAL_USER" "$@"
    else
        "$@"
    fi
}

info "Usuário para configuração do ambiente e permissões: ${BOLD}${ACTUAL_USER}${NC}"

# ------------------------------------------------------------------------------
# 2. Identificar Distribuição Linux e Gerenciador de Pacotes
# ------------------------------------------------------------------------------
DISTRO_ID="desconhecida"
DISTRO_NAME="Linux"

if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO_ID="${ID:-linux}"
    DISTRO_NAME="${PRETTY_NAME:-$NAME}"
elif [ -f /etc/issue ]; then
    DISTRO_NAME="$(head -n1 /etc/issue | cut -d'\' -f1 | tr -d ' ')"
fi

PKG_MANAGER="desconhecido"
if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
elif command -v pacman >/dev/null 2>&1; then
    PKG_MANAGER="pacman"
elif command -v zypper >/dev/null 2>&1; then
    PKG_MANAGER="zypper"
elif command -v apk >/dev/null 2>&1; then
    PKG_MANAGER="apk"
elif command -v xbps-install >/dev/null 2>&1; then
    PKG_MANAGER="xbps"
fi

info "Distribuição: ${BOLD}${DISTRO_NAME}${NC} (Gerenciador: ${PKG_MANAGER})"

# ------------------------------------------------------------------------------
# 3. Instalar Dependências do Sistema
# ------------------------------------------------------------------------------
info "Verificando e instalando pacotes de sistema necessários..."

if [ "$CAN_SUDO" = false ]; then
    warning "Sem permissões de root/sudo. Pulando instalação de pacotes do sistema."
    warning "Certifique-se de que Python 3, venv, libusb, bluez e udev estão instalados."
else
    case "$PKG_MANAGER" in
        apt)
            export DEBIAN_FRONTEND=noninteractive
            run_as_root apt-get update -qq || true
            run_as_root apt-get install -y --no-install-recommends \
                python3 \
                python3-pip \
                python3-venv \
                python3-dev \
                libusb-1.0-0 \
                libusb-1.0-0-dev \
                bluez \
                bluez-tools \
                rfkill \
                udev \
                usbutils
            ;;
        dnf)
            run_as_root dnf install -y \
                python3 \
                python3-pip \
                python3-devel \
                libusb1 \
                libusb1-devel \
                bluez \
                bluez-tools \
                rfkill \
                systemd-udev \
                usbutils
            ;;
        yum)
            run_as_root yum install -y \
                python3 \
                python3-pip \
                python3-devel \
                libusb1 \
                libusb1-devel \
                bluez \
                rfkill \
                usbutils
            ;;
        pacman)
            run_as_root pacman -Sy --noconfirm --needed \
                python \
                python-pip \
                libusb \
                bluez \
                bluez-utils \
                util-linux \
                usbutils
            ;;
        zypper)
            run_as_root zypper --non-interactive install \
                python3 \
                python3-pip \
                python3-devel \
                libusb-1_0-0 \
                libusb-1_0-devel \
                bluez \
                bluez-test-scripts \
                rfkill \
                usbutils
            ;;
        apk)
            run_as_root apk add --no-cache \
                python3 \
                py3-pip \
                py3-virtualenv \
                libusb \
                libusb-dev \
                bluez \
                rfkill \
                usbutils \
                eudev
            ;;
        xbps)
            run_as_root xbps-install -Sy -y \
                python3 \
                python3-pip \
                libusb \
                libusb-devel \
                bluez \
                rfkill \
                usbutils
            ;;
        *)
            warning "Gerenciador de pacotes não identificado. Instale manualmente:"
            warning "  - Python 3.8+ com venv e pip"
            warning "  - libusb-1.0 (biblioteca necessária para dongle ANT+ via PyUSB)"
            warning "  - BlueZ (daemon e ferramentas Bluetooth para Bleak)"
            warning "  - rfkill, udev e usbutils"
            ;;
    esac
    success "Pacotes de sistema verificados."
fi

# ------------------------------------------------------------------------------
# 4. Configurar Regras udev e Permissões de Usuário para ANT+ USB
# ------------------------------------------------------------------------------
info "Configurando regras udev para dongle ANT+ USB (0fcf:1008 e 0fcf:1009)..."

UDEV_RULES_DIR="/etc/udev/rules.d"
UDEV_TARGET="$UDEV_RULES_DIR/42-ant-usb-sticks.rules"
SRC_RULES="$SCRIPT_DIR/42-ant-usb-sticks.rules"

if [ "$CAN_SUDO" = true ]; then
    # Garantir que o grupo plugdev exista (comum em regras USB)
    if ! getent group plugdev >/dev/null 2>&1; then
        run_as_root groupadd -f plugdev 2>/dev/null || run_as_root groupadd plugdev 2>/dev/null || true
    fi

    # Adicionar o usuário aos grupos de acesso a dispositivos relevantes
    for grp in plugdev dialout bluetooth; do
        if getent group "$grp" >/dev/null 2>&1; then
            run_as_root usermod -aG "$grp" "$ACTUAL_USER" 2>/dev/null || true
        fi
    done

    # Instalar a regra udev
    if [ -d "$UDEV_RULES_DIR" ]; then
        if [ -f "$SRC_RULES" ]; then
            run_as_root cp "$SRC_RULES" "$UDEV_TARGET"
        else
            run_as_root bash -c "cat << 'EOF' > '$UDEV_TARGET'
ACTION!=\"add\", GOTO=\"cardiaco_ant_rules_end\"
SUBSYSTEM!=\"usb\", GOTO=\"cardiaco_ant_rules_end\"

ATTR{idVendor}==\"0fcf\", ATTR{idProduct}==\"1008\", TAG+=\"uaccess\", GROUP=\"plugdev\", MODE=\"0660\"
ATTR{idVendor}==\"0fcf\", ATTR{idProduct}==\"1009\", TAG+=\"uaccess\", GROUP=\"plugdev\", MODE=\"0660\"

LABEL=\"cardiaco_ant_rules_end\"
EOF"
        fi
        run_as_root chmod 644 "$UDEV_TARGET"

        # Recarregar udev
        if command -v udevadm >/dev/null 2>&1; then
            run_as_root udevadm control --reload-rules 2>/dev/null || true
            run_as_root udevadm trigger --subsystem-match=usb 2>/dev/null || run_as_root udevadm trigger 2>/dev/null || true
        fi
        success "Regra udev instalada em $UDEV_TARGET e udev recarregado."
    else
        warning "Diretório $UDEV_RULES_DIR não encontrado. Regra udev não foi instalada."
    fi
else
    warning "Sem privilégios de root para instalar regra udev em $UDEV_TARGET."
fi

# ------------------------------------------------------------------------------
# 5. Ativação e Desbloqueio do Serviço Bluetooth
# ------------------------------------------------------------------------------
info "Verificando serviço e adaptadores Bluetooth..."

if [ "$CAN_SUDO" = true ]; then
    # Desbloquear caso bloqueado por rfkill
    if command -v rfkill >/dev/null 2>&1; then
        run_as_root rfkill unblock bluetooth 2>/dev/null || true
    fi

    # Ativar serviço Bluetooth se o sistema for baseado em systemd
    if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl enable bluetooth 2>/dev/null || true
        run_as_root systemctl start bluetooth 2>/dev/null || true
    elif command -v service >/dev/null 2>&1; then
        run_as_root service bluetooth start 2>/dev/null || true
    elif [ -x /etc/init.d/bluetooth ]; then
        run_as_root /etc/init.d/bluetooth start 2>/dev/null || true
    fi
fi

# ------------------------------------------------------------------------------
# 6. Configurar Ambiente Virtual Python (.venv) e Dependências
# ------------------------------------------------------------------------------
info "Configurando ambiente Python dedicado (.venv)..."

# Detectar interpretador Python 3 disponível
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
fi

if [ -z "$PYTHON_BIN" ]; then
    error "Interpretador Python não encontrado no sistema. Por favor, instale o Python 3.8 a 3.13."
    exit 1
fi

PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"

info "Python detectado: versão $PY_VER ($PYTHON_BIN)"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    error "O app requer Python >= 3.8. Versão encontrada: $PY_VER"
    exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"

# Criar ambiente virtual se não existir
if [ ! -f "$VENV_DIR/bin/python" ]; then
    info "Criando ambiente virtual em $VENV_DIR..."
    rm -rf "$VENV_DIR" 2>/dev/null || true
    
    VENV_CREATED=false
    if run_as_user "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null; then
        VENV_CREATED=true
    elif command -v virtualenv >/dev/null 2>&1; then
        if run_as_user virtualenv -p "$PYTHON_BIN" "$VENV_DIR" 2>/dev/null; then
            VENV_CREATED=true
        fi
    fi

    if [ "$VENV_CREATED" = false ]; then
        error "Falha ao criar o virtualenv."
        error "Se estiver no Ubuntu/Debian, instale: sudo apt install python3-venv python3-pip"
        error "Se estiver no Fedora/RHEL, instale: sudo dnf install python3-pip"
        exit 1
    fi
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Atualizar pip, setuptools e wheel dentro do venv
info "Atualizando pip, setuptools e wheel..."
run_as_user "$VENV_PIP" install --upgrade pip setuptools wheel --quiet 2>/dev/null || true

# Instalar dependências listadas em requirements.txt
info "Instalando dependências do aplicativo (Bleak e OpenANT)..."
REQ_FILE="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    if ! run_as_user "$VENV_PIP" install -r "$REQ_FILE"; then
        warning "Falha no pip install via requirements.txt. Tentando com opções diretas..."
        run_as_user "$VENV_PIP" install "bleak>=0.22.3,<0.23" "openant>=1.3.4,<2" || {
            error "Não foi possível instalar as dependências Python via pip."
            exit 1
        }
    fi
else
    run_as_user "$VENV_PIP" install "bleak>=0.22.3,<0.23" "openant>=1.3.4,<2"
fi

# Garantir permissões de propriedade do .venv para o usuário comum caso tenha rodado via sudo
if [ "$EUID" -eq 0 ] && [ "$ACTUAL_USER" != "root" ]; then
    chown -R "$ACTUAL_USER":"$ACTUAL_GROUP" "$VENV_DIR" 2>/dev/null || true
fi

success "Ambiente virtual Python configurado com sucesso!"

# ------------------------------------------------------------------------------
# 7. Criar Script Atalho de Execução (run.sh)
# ------------------------------------------------------------------------------
RUN_SCRIPT="$SCRIPT_DIR/run.sh"
cat << 'EOF' > "$RUN_SCRIPT"
#!/usr/bin/env bash
# Atalho para executar o monitor de frequência cardíaca no ambiente virtual correto

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

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
if [ "$EUID" -eq 0 ] && [ "$ACTUAL_USER" != "root" ]; then
    chown "$ACTUAL_USER":"$ACTUAL_GROUP" "$RUN_SCRIPT" 2>/dev/null || true
fi
success "Atalho './run.sh' criado e tornado executável."

# ------------------------------------------------------------------------------
# 8. Verificação e Diagnóstico de Funcionamento
# ------------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${CYAN}--- Verificação e Diagnóstico ---${NC}"

# Teste de importação de bibliotecas Python
if "$VENV_PYTHON" -c "import bleak, openant, usb.core" 2>/dev/null; then
    BLEAK_VER="$("$VENV_PYTHON" -c 'import bleak; print(bleak.__version__)')"
    success "Bibliotecas Python carregadas: Bleak ${BLEAK_VER}, OpenANT e PyUSB OK."
else
    warning "Aviso ao carregar módulos Python no virtualenv. Teste com: ./run.sh --list"
fi

# Teste de backend USB (PyUSB + libusb)
LIBUSB_STATUS="$("$VENV_PYTHON" -c "
import sys
try:
    import usb.core
    # find() sem argumentos lista o primeiro dispositivo ou retorna None se não houver
    # mas se o libusb não estiver instalado, gera NoBackendError
    usb.core.find()
    print('OK')
except usb.core.NoBackendError:
    print('NO_BACKEND')
except Exception:
    print('OK')
" 2>/dev/null || echo "ERROR")"

if [ "$LIBUSB_STATUS" = "OK" ]; then
    success "Backend libusb (PyUSB) operacional para suporte ANT+."
elif [ "$LIBUSB_STATUS" = "NO_BACKEND" ]; then
    warning "Backend libusb não encontrado pelo PyUSB. Para usar sensores ANT+, instale a biblioteca libusb do seu sistema."
fi

# Verificação do Bluetooth
BT_READY=false
if command -v bluetoothctl >/dev/null 2>&1; then
    if (command -v timeout >/dev/null 2>&1 && timeout 2 bluetoothctl list 2>/dev/null || bluetoothctl list 2>/dev/null) | grep -qi "Controller"; then
        BT_READY=true
    fi
fi

if [ "$BT_READY" = true ]; then
    success "Adaptador Bluetooth ativo e detectado."
else
    info "Nenhum adaptador Bluetooth ativo listado no momento."
    echo "       (Certifique-se de que o Bluetooth do computador está ligado ou conecte um dongle BLE)."
fi

# Verificação de Dongle ANT+ USB
ANT_READY=false
if command -v lsusb >/dev/null 2>&1; then
    if lsusb 2>/dev/null | grep -qiE "0fcf:1008|0fcf:1009"; then
        ANT_READY=true
    fi
fi

if [ "$ANT_READY" = true ]; then
    success "Dongle USB ANT+ (Dynastream) compatível detectado e conectado."
else
    info "Dongle USB ANT+ não detectado no momento."
    echo "       (Sensores BLE funcionarão normalmente. Ao conectar o dongle ANT+, ele será detectado automaticamente)."
fi

echo ""
echo -e "${BOLD}${GREEN}================================================================${NC}"
echo -e "${BOLD}${GREEN} Instalação concluída com sucesso!${NC}"
echo -e "${BOLD}${GREEN}================================================================${NC}"
echo ""
echo -e "Para iniciar o monitor:"
echo -e "  ${BOLD}./run.sh --list${NC}                 # Listar sensores BLE e ANT+ próximos"
echo -e "  ${BOLD}./run.sh${NC}                        # Iniciar monitoramento interativo"
echo -e "  ${BOLD}./run.sh --device \"NomeOuMAC\"${NC}   # Conectar diretamente a sensores específicos"
echo ""
echo -e "URL do dashboard para OBS Studio: ${CYAN}http://127.0.0.1:8765/index.htm${NC}"
echo ""

if [ "$CAN_SUDO" = true ]; then
    echo -e "${YELLOW}Observação importante sobre permissões:${NC}"
    echo "Se o seu usuário (${ACTUAL_USER}) foi adicionado ao grupo 'plugdev', pode ser necessário"
    echo "fazer logout e login novamente no Linux (ou reiniciar a sessão) para que as permissões"
    echo "de acesso USB sem root entrem em vigor."
    echo ""
fi
