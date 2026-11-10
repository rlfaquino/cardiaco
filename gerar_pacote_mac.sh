#!/usr/bin/env bash
# ==============================================================================
# Script para gerar o pacote ZIP limpo pronto para envio a usuários do macOS
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_ZIP="$SCRIPT_DIR/MonitorCardiaco_macOS.zip"
STAGE_DIR="/tmp/MonitorCardiaco_macOS"

echo "Gerando pacote para macOS em: $OUTPUT_ZIP..."

# Limpar staging anterior
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

# Garantir permissões de execução
chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.command 2>/dev/null || true

# Copiar apenas os arquivos necessários para o macOS (sem lixo, testes ou .git)
FILES=(
    "COMO_INSTALAR_MAC.txt"
    "INSTALAR_NO_MAC.command"
    "Iniciar_Monitor_Cardiaco.command"
    "install_macos.sh"
    "run.sh"
    "monitor_batimentos.py"
    "antplus.py"
    "dashboard.html"
    "index.htm"
    "requirements.txt"
    "sensor_names.json"
    "README.md"
)

for file in "${FILES[@]}"; do
    if [ -f "$SCRIPT_DIR/$file" ]; then
        cp "$SCRIPT_DIR/$file" "$STAGE_DIR/"
    else
        echo "Aviso: arquivo $file não encontrado, pulando..."
    fi
done

# Criar ZIP a partir do diretório /tmp
rm -f "$OUTPUT_ZIP"
(cd /tmp && zip -r -q "$OUTPUT_ZIP" MonitorCardiaco_macOS/)
rm -rf "$STAGE_DIR"

echo "=========================================================="
echo "Pacote gerado com sucesso!"
echo "Arquivo: $OUTPUT_ZIP ($(du -h "$OUTPUT_ZIP" | cut -f1))"
echo "Envie esse arquivo .zip para seu amigo."
echo "=========================================================="
