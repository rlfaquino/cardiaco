#!/usr/bin/env bash
# ==============================================================================
# Script de Execução do Monitor de Frequência Cardíaca (BLE + ANT+)
# Compatível com Linux e macOS
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

# No macOS, garantir que caminhos de bibliotecas do Homebrew fiquem acessíveis
if [ "$(uname -s)" = "Darwin" ]; then
    export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
fi

if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$SCRIPT_DIR/monitor_batimentos.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/monitor_batimentos.py" "$@"
else
    echo "Erro: Interpretador Python não encontrado. Execute ./install.sh ou ./install_macos.sh primeiro." >&2
    exit 1
fi
