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
