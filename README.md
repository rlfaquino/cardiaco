# Monitor de frequência cardíaca BLE

O script `monitor_batimentos.py` lê sensores que implementam o serviço BLE padrão
de frequência cardíaca (`180D`) e atualiza `<id-do-dispositivo>.txt` com apenas
a última leitura de BPM, uma vez por segundo. O horário e o BPM continuam sendo
exibidos no terminal.

## Instalação

Requer Python 3.8 a 3.13 e Bluetooth habilitado. O projeto usa Bleak 0.22.x
para manter compatibilidade com o BlueZ 5.53 do Ubuntu 20.04:

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

Para procurar sensores compatíveis:

```bash
python monitor_batimentos.py
```

Sem `--device`, o script lista os sensores encontrados e permite selecionar
vários números separados por vírgula (por exemplo, `1,3`). Para escolher
dispositivos pelo endereço Bluetooth ou pelo nome, repita a opção `--device`:

```bash
python monitor_batimentos.py --device "NOME-DO-SENSOR-1" --device "ENDERECO-BLUETOOTH-DO-SENSOR-2"
```

Cada sensor conecta e atualiza seu próprio arquivo `.txt` e página de overlay
simultaneamente. A lista em `index.htm` inclui os dispositivos encontrados na
pasta de saída.

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
dispositivos disponíveis. Selecione um dispositivo para abrir sua página
`<id-do-dispositivo>.html`. Para exibir vários ao mesmo tempo, adicione uma
fonte **Navegador** no OBS para cada sensor, usando a URL da página
correspondente, por exemplo:

```text
http://127.0.0.1:8765/ENDERECO_DO_DISPOSITIVO.html
```

Cada página consulta a leitura a cada segundo. Mantenha o script rodando para
atualizar os batimentos; páginas de dispositivos sem leitura recente mostram
`--`. O arquivo `.txt` contém somente o número do BPM mais recente.

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
