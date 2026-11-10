#!/usr/bin/env bash
# ==============================================================================
# Atalho do macOS para Iniciar o Monitor Cardíaco (Clique Duplo no Finder)
# ==============================================================================

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
