#!/usr/bin/env bash
# ==============================================================================
# Instalador do Monitor Cardíaco para macOS (Duplo Clique no Finder)
# ==============================================================================

cd "$(dirname "$0")"

echo "========================================================"
echo " Instalador do Monitor de Frequência Cardíaca (macOS)"
echo "========================================================"
echo ""

# Garantir que todos os scripts tenham permissão de execução
chmod +x ./*.sh ./*.command 2>/dev/null || true

# Executar a instalação
./install_macos.sh

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "========================================================"
    echo " Tudo pronto! Instalação concluída com sucesso."
    echo " Para iniciar o monitor, basta dar um duplo clique em:"
    echo " -> Iniciar_Monitor_Cardiaco.command"
    echo "========================================================"
else
    echo "--------------------------------------------------------"
    echo " A instalação foi finalizada com o código $EXIT_CODE."
    echo " Verifique as mensagens de aviso acima se necessário."
    echo "--------------------------------------------------------"
fi

echo ""
echo "Pressione [ENTER] para fechar esta janela..."
read -r
