# Monitor de frequência cardíaca BLE e Ant+ de multiplos atletas 

O script `monitor_batimentos.py` lê multiplos sensores de frequência cardíaca BLE (serviço padrão `180D`) e ANT+ (perfil Heart Rate) ao mesmo tempo e gera overlay para uso no OBS Studio.

## Instalação Rápida (Linux e macOS)

O projeto conta com scripts automáticos de instalação preparados para configurar todo o ambiente:

### No Linux (Ubuntu, Debian, Fedora, Arch, openSUSE, etc.)

```bash
./install.sh
```

O script instala dependências de sistema (`python3-venv`, `libusb`, `bluez`, `udev`), adiciona o usuário aos grupos de hardware (`plugdev`), instala regras udev para o dongle ANT+, ativa o Bluetooth e cria o ambiente virtual `.venv`.

### No macOS (Apple Silicon M1/M2/M3/M4 e Intel)

```bash
./install_macos.sh
# ou simplesmente: ./install.sh (ele detecta o macOS automaticamente)
```

O instalador para macOS:
1. Verifica e utiliza o **Homebrew** (`brew`) para instalar `libusb` e `python3` (se necessário).
2. Cria o ambiente virtual Python (`.venv`) e instala `bleak`, `openant` e `pyusb`.
3. Ajusta a descoberta de bibliotecas dinâmicas do `libusb` para Apple Silicon (`/opt/homebrew/lib`) e Intel (`/usr/local/lib`).
4. Testa a permissão de Bluetooth do sistema (**TCC**) e auxilia na autorização do Terminal nos **Ajustes do Sistema**.
5. Cria o atalho executável `Iniciar_Monitor_Cardiaco.command` (permite iniciar com **duplo clique no Finder**).

> **Atenção no macOS (Permissão de Bluetooth):** O macOS exige autorização explícita para o aplicativo de Terminal usar Bluetooth. Se solicitado na primeira execução, clique em **Permitir**. Caso necessário, ative em:  
> *Ajustes do Sistema > Privacidade e Segurança > Bluetooth > Ativar para seu Terminal*.

---

### Executando o Aplicativo

Após a instalação (em qualquer sistema operacional):

```bash
./run.sh --list     # Listar sensores BLE e ANT+ disponíveis
./run.sh            # Iniciar o monitoramento interativo
```

No macOS, você também pode simplesmente dar um **duplo clique no arquivo `Iniciar_Monitor_Cardiaco.command`** pelo Finder.

---

## Instalação Manual (Opcional)

Requer Python 3.8 a 3.13, Bluetooth habilitado para BLE e, opcionalmente,
um dongle USB ANT+ para sensores ANT+. O projeto usa Bleak 0.22.x para manter
compatibilidade com o BlueZ 5.53 do Ubuntu 20.04, além de OpenANT e PyUSB para
ANT+:

Se você usa `pyenv` e recebeu `pyenv: python: command not found`, selecione a
versão instalada na pasta do projeto antes de continuar:

```bash
pyenv local 3.13.13
```

Esse comando cria um arquivo `.python-version` local. Depois, abra um novo
terminal nessa pasta ou execute `pyenv rehash`; confirme com `python --version`.

Instale a dependência:

```bash
python -m pip install -r requirements.txt
```

## Uso

Para buscar e listar dispositivos BLE e ANT+ próximos, sem conectar:

```bash
python monitor_batimentos.py --list
```

O comando exibe o nome e o endereço Bluetooth de cada dispositivo encontrado.
Os que anunciarem o serviço padrão de frequência cardíaca serão identificados
na lista.

A listagem exibe seções separadas para BLE e ANT+. Se não houver um dongle ANT+
compatível/conectado, a seção ANT+ informa isso e a busca BLE também é exibida.

Para procurar sensores compatíveis:

```bash
python monitor_batimentos.py
```

Sem `--device`, o script lista os sensores BLE encontrados e permite selecionar
vários números separados por vírgula (por exemplo, `1,3`). Para escolher
dispositivos pelo endereço Bluetooth ou pelo nome, repita a opção `--device`:

```bash
python monitor_batimentos.py --device "NOME-DO-SENSOR-1" --device "ENDERECO-BLUETOOTH-DO-SENSOR-2"
```

Use `--name ID=NOME` para identificar quem está usando cada sensor. O ID pode
ser o endereço Bluetooth, o nome Bluetooth do dispositivo ou o identificador
ANT+ (`ANT_<id>`). Repita a opção para nomear vários sensores:

```bash
python monitor_batimentos.py \
  --name "AA:BB:CC:DD:EE:FF=Ana" \
  --name "ANT_12345=Bruno"
```

O nome informado aparece no título do gráfico histórico e no índice de
overlays, no lugar do identificador do sensor. Sem `--name`, o comportamento
continua o mesmo e o identificador é exibido.

Também é possível alterar o nome diretamente na página inicial
`http://127.0.0.1:8765/index.htm`: edite o campo do sensor e clique em
**Salvar nome**. A mudança atualiza o índice e o título do gráfico histórico e
fica salva em `sensor_names.json` na pasta de saída, inclusive entre execuções.
Um nome passado com `--name` continua tendo prioridade ao iniciar o monitor.

Cada sensor conecta e atualiza seu próprio arquivo `.txt` e página de overlay
simultaneamente. A lista em `index.htm` inclui os dispositivos encontrados na
pasta de saída.

Sensores cardíacos ANT+ são detectados automaticamente quando há um dongle
compatível conectado. Cada sensor ANT+ recebe um arquivo e um overlay próprios,
com identificador `ANT_<id>`.

Para gravar os arquivos em outra pasta:

```bash
python monitor_batimentos.py --output-dir ./dados
```

Use `Ctrl+C` para encerrar. Se o sensor não anunciar o serviço padrão de
frequência cardíaca, o script listará os dispositivos BLE encontrados para
você escolher manualmente. O sistema operacional também pode exigir permissões
de acesso ao Bluetooth.

## Overlay para OBS

Com o script em execução, a lista de dispositivos fica disponível em:

```text
http://127.0.0.1:8765/index.htm
```

O arquivo `index.htm` na pasta de saída é atualizado a cada execução e lista os
dispositivos disponíveis, com links separados para a leitura atual e para o
histórico de cada sensor, além de um link para o dashboard conjunto. O
cartão de cada sensor identifica separadamente o nome anunciado pelo
dispositivo, seu endereço MAC ou ID ANT+ e o nome da pessoa associada. O
dashboard mostra o BPM ao vivo de todos os sensores e um gráfico com o
histórico de cada atleta, identificados pelos nomes configurados. As leituras
ao vivo e o gráfico atualizam a cada segundo; após a carga inicial, o gráfico
recebe somente as novas amostras. A janela
do histórico pode ser ajustada entre 1 e 30 minutos.
O dashboard também pode ser aberto diretamente em
`http://127.0.0.1:8765/dashboard.html`.

Para exibir a leitura atual, adicione uma fonte
**Navegador** no OBS usando a URL da página correspondente, por exemplo:

```text
http://127.0.0.1:8765/ENDERECO_DO_DISPOSITIVO.html
```

O segundo overlay mostra o gráfico de batimentos da última meia hora. Use o
controle **Janela** para ajustar o intervalo exibido entre 1 e 30 minutos; sua
URL tem o formato:

```text
http://127.0.0.1:8765/history/ENDERECO_DO_DISPOSITIVO.html
```

Cada gráfico atualiza a cada segundo e mantém o histórico em memória
enquanto o monitor está rodando. O histórico começa a ser coletado quando o
sensor é monitorado e não é preservado após reiniciar o script. A página de
leitura atual continua consultando a cada segundo; dispositivos sem leitura
recente mostram `--`. O arquivo `.txt` contém somente o número do BPM mais
recente.

O suporte ANT+ depende do modelo do dongle. A versão atual do OpenANT procura
sticks compatíveis com os IDs USB Dynastream `0fcf:1008` (ANTUSB2) e `0fcf:1009`
(ANTUSB-m). Alguns sticks genéricos podem não ser reconhecidos mesmo que sejam
vendidos como ANT+. No Linux, conecte o dongle, confira o ID com `lsusb` e, se
necessário, configure uma regra `udev` para permitir acesso USB sem `sudo`.
Sem o dongle, o monitor BLE continua funcionando normalmente.

Para os dois IDs compatíveis acima, o projeto inclui uma regra `udev`. Instale-a
somente depois de confirmar que o dongle aparece com um desses IDs:

```bash
sudo cp 42-ant-usb-sticks.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
```

Desconecte e reconecte o dongle depois. A regra dá acesso ao usuário da sessão
e ao grupo `plugdev`; se `lsusb` mostrar outro fabricante/produto, este driver
OpenANT não reconhecerá o dispositivo automaticamente.

Para servir os arquivos gerados com o servidor HTTP simples do Python (por
exemplo, para visualizar o índice sem iniciar o monitor), abra outro terminal
na pasta de saída e execute:

```bash
python -m http.server 8000
```

Depois, abra `http://127.0.0.1:8000/index.htm`. Esse servidor simples serve
arquivos estáticos; para atualizar os batimentos ao vivo no overlay, inicie
`monitor_batimentos.py` e use a URL na porta `8765`, pois o próprio script
fornece a API das leituras.

## Solução de problemas no Linux

O Bleak 1.x exige BlueZ 5.55 ou superior, mas o projeto fixa Bleak 0.22.x,
compatível com BlueZ 5.53 do Ubuntu 20.04. Se ainda aparecer uma mensagem sobre
BlueZ 5.55, confira se está usando o Python do projeto com `python --version` e
reinstale as dependências com `python -m pip install -r requirements.txt`.

Confira a versão do BlueZ com:

```bash
bluetoothd --version
```

No Ubuntu 20.04, o BlueZ padrão é 5.53. Não é necessário atualizar o BlueZ para
usar a versão do Bleak fixada neste projeto.

Se a conexão ou descoberta dos serviços do sensor exceder o tempo limite,
aproxime e acorde o sensor, feche aplicativos que possam estar conectados a ele
(como o aplicativo do celular) e tente novamente. O script aguarda até 30
segundos pela conexão e descoberta dos serviços.
