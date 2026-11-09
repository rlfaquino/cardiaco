import argparse
import asyncio
import html
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import List, Optional
from urllib.parse import urlsplit

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
OVERLAY_HOST = "127.0.0.1"
OVERLAY_PORT = 8765
READING_MAX_AGE_SECONDS = 3
BLE_CONNECT_TIMEOUT_SECONDS = 30


def parse_heart_rate(data: bytearray) -> int:
    if len(data) < 2:
        raise ValueError("Notificação de frequência cardíaca incompleta")

    flags = data[0]
    value_size = 2 if flags & 0x01 else 1
    if len(data) < 1 + value_size:
        raise ValueError("Notificação de frequência cardíaca incompleta")

    return int.from_bytes(data[1 : 1 + value_size], byteorder="little")


async def find_devices(device_ids: Optional[List[str]]) -> List[BLEDevice]:
    print("Procurando sensores BLE...")
    discovered = await BleakScanner.discover(timeout=10, return_adv=True)
    devices = [device for device, _advertisement in discovered.values()]
    candidates: List[BLEDevice] = []

    if device_ids:
        selected_devices: List[BLEDevice] = []
        seen_addresses = set()
        for device_id in device_ids:
            matches = [
                device
                for device in devices
                if device.address.casefold() == device_id.casefold()
                or (device.name and device.name.casefold() == device_id.casefold())
            ]
            if not matches:
                raise RuntimeError(
                    f"Nenhum dispositivo com endereço ou nome {device_id!r} foi encontrado."
                )
            if len(matches) > 1:
                raise RuntimeError(
                    f"O nome {device_id!r} corresponde a vários dispositivos. "
                    "Use o endereço Bluetooth para selecionar um deles."
                )
            device = matches[0]
            if device.address.casefold() not in seen_addresses:
                selected_devices.append(device)
                seen_addresses.add(device.address.casefold())
        return selected_devices

    if not device_ids:
        for device, advertisement in discovered.values():
            has_heart_rate_service = any(
                uuid.lower() == HEART_RATE_SERVICE_UUID
                for uuid in advertisement.service_uuids
            )
            if has_heart_rate_service:
                candidates.append(device)

    if not candidates:
        if not devices:
            raise RuntimeError(
                "Nenhum dispositivo BLE foi encontrado. Verifique se o sensor "
                "está ligado e próximo, e se o Bluetooth está habilitado."
            )
        candidates = devices
        print(
            "Nenhum dispositivo anunciou o serviço cardíaco. "
            "Escolha o sensor na lista para tentar conectar."
        )

    if len(candidates) == 1:
        return candidates

    print("Sensores encontrados:")
    for index, device in enumerate(candidates, start=1):
        print(f"  {index}. {device.name or 'Sem nome'} [{device.address}]")

    while True:
        selection = input(
            "Escolha um ou mais números separados por vírgula (ex.: 1,3): "
        ).strip()
        try:
            selected_indices = [int(value.strip()) for value in selection.split(",")]
        except ValueError:
            selected_indices = []
        if (
            selected_indices
            and len(set(selected_indices)) == len(selected_indices)
            and all(1 <= index <= len(candidates) for index in selected_indices)
        ):
            return [candidates[index - 1] for index in selected_indices]
        print(
            f"Digite números únicos entre 1 e {len(candidates)}, separados por vírgula."
        )


def output_filename(device_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", device_id)
    return f"{safe_id}.txt"


def render_overlay_page(device_id: str) -> str:
    api_path = f"/api/{device_id}.txt"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Batimentos cardíacos - {html.escape(device_id)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, Helvetica, sans-serif; }}
    * {{ box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; margin: 0; background: transparent; }}
    body {{ display: grid; place-items: center; color: #fff; }}
    .card {{
      display: flex; align-items: center; gap: 18px; padding: 18px 26px;
      border: 1px solid rgb(255 255 255 / 18%); border-radius: 20px;
      background: rgb(12 16 24 / 82%); box-shadow: 0 8px 32px rgb(0 0 0 / 35%);
    }}
    .heart {{ color: #ff4664; font-size: 58px; line-height: 1; transform-origin: center; }}
    .heart.beating {{ animation: beat 850ms ease-in-out infinite; }}
    .reading {{ display: flex; align-items: baseline; gap: 8px; min-width: 150px; }}
    #bpm {{ font-size: 64px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; }}
    .unit {{ color: #d3d9e3; font-size: 19px; font-weight: 600; }}
    #status {{ margin-top: 7px; color: #aab4c3; font-size: 14px; }}
    @keyframes beat {{
      0%, 100% {{ transform: scale(1); }}
      12% {{ transform: scale(1.16); }}
      24% {{ transform: scale(1); }}
      36% {{ transform: scale(1.1); }}
      52% {{ transform: scale(1); }}
    }}
  </style>
</head>
<body>
  <main class="card" aria-live="polite">
    <div id="heart" class="heart" aria-hidden="true">♥</div>
    <div>
      <div class="reading"><span id="bpm">--</span><span class="unit">BPM</span></div>
      <div id="status">Aguardando leitura do sensor</div>
    </div>
  </main>
  <script>
    const bpmElement = document.getElementById("bpm");
    const statusElement = document.getElementById("status");
    const heartElement = document.getElementById("heart");

    async function updateReading() {{
      try {{
        const response = await fetch("{api_path}?t=" + Date.now(), {{ cache: "no-store" }});
        if (!response.ok) throw new Error("Falha ao consultar a leitura");
        const data = await response.json();
        if (Number.isInteger(data.bpm)) {{
          bpmElement.textContent = data.bpm;
          statusElement.textContent = "Frequência cardíaca";
          heartElement.classList.add("beating");
        }} else {{
          bpmElement.textContent = "--";
          statusElement.textContent = "Sem leitura recente";
          heartElement.classList.remove("beating");
        }}
      }} catch {{
        bpmElement.textContent = "--";
        statusElement.textContent = "Conexão indisponível";
        heartElement.classList.remove("beating");
      }}
    }}

    updateReading();
    window.setInterval(updateReading, 1000);
  </script>
</body>
</html>
"""


def update_device_pages(
    output_dir: Path, active_output_paths: List[Path]
) -> dict[str, Path]:
    reading_paths: dict[str, Path] = {}
    for path in output_dir.glob("*.txt"):
        if not path.is_file():
            continue
        try:
            previous_mtime = path.stat().st_mtime
            contents = path.read_text(encoding="utf-8").strip()
            last_reading = contents.splitlines()[-1] if contents else ""
            bpm = int(last_reading.rsplit(",", 1)[-1])
        except (OSError, ValueError, IndexError):
            continue
        path.write_text(str(bpm), encoding="utf-8")
        os.utime(path, (previous_mtime, previous_mtime))
        reading_paths[path.stem] = path

    for output_path in active_output_paths:
        reading_paths[output_path.stem] = output_path

    for device_id in reading_paths:
        page_path = output_dir / f"{device_id}.html"
        page_path.write_text(render_overlay_page(device_id), encoding="utf-8")

    index_entries = "\n".join(
        f'<li><a href="/{html.escape(device_id, quote=True)}.html">'
        f'{html.escape(device_id)}</a></li>'
        for device_id in sorted(reading_paths, key=str.casefold)
    )
    index_path = output_dir / "index.htm"
    index_path.write_text(
        f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Overlays de frequência cardíaca</title>
  <style>
    body {{ margin: 0; padding: 32px; background: #10141c; color: #fff;
           font: 16px Arial, Helvetica, sans-serif; }}
    h1 {{ font-size: 24px; }}
    ul {{ display: grid; gap: 12px; padding: 0; list-style: none; }}
    a {{ display: block; padding: 16px; border-radius: 10px;
         background: #202938; color: #fff; text-decoration: none; }}
    a:hover, a:focus {{ background: #30425c; }}
  </style>
</head>
<body>
  <h1>Selecione o dispositivo</h1>
  <ul>{index_entries}</ul>
</body>
</html>
""",
        encoding="utf-8",
    )
    return reading_paths


def create_overlay_server(
    output_dir: Path, reading_paths: dict[str, Path]
) -> ThreadingHTTPServer:
    page_paths = {
        f"/{device_id}.html": output_dir / f"{device_id}.html"
        for device_id in reading_paths
    }

    class OverlayRequestHandler(BaseHTTPRequestHandler):
        def _send_response(self, status: int, content_type: str, content: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            request_path = urlsplit(self.path).path
            if request_path in ("/", "/index.htm"):
                try:
                    content = (output_dir / "index.htm").read_bytes()
                except OSError:
                    self._send_response(500, "text/plain; charset=utf-8", b"Indice indisponivel")
                    return
                self._send_response(200, "text/html; charset=utf-8", content)
                return

            page_path = page_paths.get(request_path)
            if page_path:
                try:
                    content = page_path.read_bytes()
                except OSError:
                    self._send_response(
                        404, "text/plain; charset=utf-8", b"Pagina nao encontrada"
                    )
                    return
                self._send_response(200, "text/html; charset=utf-8", content)
                return

            if request_path.startswith("/api/") and request_path.endswith(".txt"):
                device_id = request_path[len("/api/") : -len(".txt")]
                reading_path = reading_paths.get(device_id)
                if reading_path is None:
                    self._send_response(
                        404,
                        "application/json; charset=utf-8",
                        b'{"error":"Dispositivo nao encontrado"}',
                    )
                    return

                try:
                    content = reading_path.read_text(encoding="utf-8").strip()
                    modified_at = reading_path.stat().st_mtime
                    is_recent = (
                        datetime.now().timestamp() - modified_at <= READING_MAX_AGE_SECONDS
                    )
                    bpm = int(content) if content and is_recent else None
                except (OSError, ValueError):
                    self._send_response(
                        500,
                        "application/json; charset=utf-8",
                        b'{"error":"Falha ao ler a ultima leitura"}',
                    )
                    return

                body = json.dumps({"bpm": bpm}).encode("utf-8")
                self._send_response(200, "application/json; charset=utf-8", body)
                return

            self._send_response(404, "text/plain; charset=utf-8", b"Nao encontrado")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((OVERLAY_HOST, OVERLAY_PORT), OverlayRequestHandler)


async def monitor_device(device: BLEDevice, output_path: Path) -> bool:
    latest_bpm: Optional[int] = None

    def handle_measurement(_sender: int, data: bytearray) -> None:
        nonlocal latest_bpm
        latest_bpm = parse_heart_rate(data)

    label = f"{device.name or 'sensor'} [{device.address}]"
    print(f"Conectando a {label}...")
    try:
        async with BleakClient(device, timeout=BLE_CONNECT_TIMEOUT_SECONDS) as client:
            await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_measurement)
            output_path.write_text("", encoding="utf-8")
            print(f"Conectado a {label}; gravando em {output_path}.")
            loop = asyncio.get_running_loop()
            next_sample = loop.time() + 1
            while client.is_connected:
                await asyncio.sleep(max(0, next_sample - loop.time()))
                if latest_bpm is not None:
                    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                    output_path.write_text(str(latest_bpm), encoding="utf-8")
                    print(f"{timestamp}  {label}: {latest_bpm} bpm")

                next_sample += 1
                if next_sample <= loop.time():
                    next_sample = loop.time() + 1
        print(f"Conexão encerrada com {label}.")
        return True
    except asyncio.TimeoutError:
        print(
            f"Erro Bluetooth em {label}: conexão/descoberta excedeu "
            f"{BLE_CONNECT_TIMEOUT_SECONDS}s. Aproxime e acorde o sensor e "
            "feche outros aplicativos conectados a ele."
        )
    except BleakError as error:
        print(f"Erro Bluetooth em {label}: {error}")
    return False


async def record(device_ids: Optional[List[str]], output_dir: Path) -> None:
    devices = await find_devices(device_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        device.address: output_dir / output_filename(device.address)
        for device in devices
    }
    reading_paths = update_device_pages(output_dir, list(output_paths.values()))
    server = create_overlay_server(output_dir, reading_paths)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Lista de dispositivos: http://{OVERLAY_HOST}:{OVERLAY_PORT}/")
    for device in devices:
        page_name = output_paths[device.address].stem + ".html"
        print(
            f"Overlay {device.name or device.address}: "
            f"http://{OVERLAY_HOST}:{OVERLAY_PORT}/{page_name}"
        )

    try:
        results = await asyncio.gather(
            *(
                monitor_device(device, output_paths[device.address])
                for device in devices
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        if not any(result is True for result in results):
            raise RuntimeError("Nenhum dos sensores selecionados conseguiu conectar.")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lê batimentos de um sensor BLE e grava uma amostra por segundo."
    )
    parser.add_argument(
        "--device",
        action="append",
        help=(
            "ID/endereço Bluetooth ou nome do sensor; informe a opção mais de "
            "uma vez para monitorar vários. Sem esta opção, permite selecionar "
            "vários sensores encontrados."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Pasta dos arquivos de saída (padrão: pasta atual).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(record(args.device, args.output_dir))
    except KeyboardInterrupt:
        print("\nMonitoramento encerrado.")
    except asyncio.TimeoutError:
        parser.exit(1, "Erro: operação Bluetooth excedeu o tempo limite.\n")
    except BleakError as error:
        message = str(error)
        if "requires BlueZ" in message:
            message += (
                " Atualize o BlueZ para a versão exigida pelo Bleak "
                "(este ambiente reportou BlueZ 5.53)."
            )
        parser.exit(1, f"Erro Bluetooth: {message}\n")
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Erro: {error}\n")


if __name__ == "__main__":
    main()
