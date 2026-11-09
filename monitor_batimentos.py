import argparse
import asyncio
import html
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from antplus import monitor_ant_heart_rate, scan_ant_devices

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
OVERLAY_HOST = "127.0.0.1"
OVERLAY_PORT = 8765
READING_MAX_AGE_SECONDS = 3
BLE_CONNECT_TIMEOUT_SECONDS = 30


class LiveStatusDisplay:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream = sys.stdout
        self._rows: Dict[str, Tuple[str, str]] = {}
        self._rendered_rows = 0

    def register(self, sensor_id: str, label: str) -> None:
        with self._lock:
            if sensor_id in self._rows:
                return
            self._rows[sensor_id] = (label, "Aguardando leitura")
            self._redraw()

    def update(self, sensor_id: str, status: str) -> None:
        with self._lock:
            row = self._rows.get(sensor_id)
            if row is None:
                return
            self._rows[sensor_id] = (row[0], status)
            row_index = list(self._rows).index(sensor_id)
            if not self._rendered_rows:
                self._redraw()
                return

            self._move_to_row(row_index)
            self._write_row(*self._rows[sensor_id], newline=False)
            self._move_to_row_end(row_index)

    def _redraw(self) -> None:
        if self._rendered_rows:
            self._stream.write(f"\x1b[{self._rendered_rows}A")
        for label, status in self._rows.values():
            self._write_row(label, status, newline=True)
        self._rendered_rows = len(self._rows)
        self._stream.flush()

    def _move_to_row(self, row_index: int) -> None:
        distance = self._rendered_rows - row_index
        if distance:
            self._stream.write(f"\x1b[{distance}A")
        self._stream.write("\r")

    def _move_to_row_end(self, row_index: int) -> None:
        distance = self._rendered_rows - row_index
        if distance:
            self._stream.write(f"\x1b[{distance}B")
        self._stream.write("\r")
        self._stream.flush()

    def _write_row(self, label: str, status: str, newline: bool) -> None:
        width = max(1, shutil.get_terminal_size(fallback=(120, 24)).columns - 1)
        text = f"{label}: {status}"
        self._stream.write(f"\x1b[2K{text[:width]}")
        if newline:
            self._stream.write("\n")


def parse_heart_rate(data: bytearray) -> int:
    if len(data) < 2:
        raise ValueError("Notificação de frequência cardíaca incompleta")

    flags = data[0]
    value_size = 2 if flags & 0x01 else 1
    if len(data) < 1 + value_size:
        raise ValueError("Notificação de frequência cardíaca incompleta")

    return int.from_bytes(data[1 : 1 + value_size], byteorder="little")


async def list_available_devices() -> None:
    print("=== Dispositivos BLE ===")
    try:
        discovered = await BleakScanner.discover(timeout=10, return_adv=True)
    except (BleakError, OSError) as error:
        print(f"Não foi possível buscar dispositivos BLE: {error}")
    else:
        devices = sorted(
            discovered.values(),
            key=lambda item: (item[0].name or "").casefold(),
        )
        if not devices:
            print("Nenhum dispositivo BLE encontrado.")
        else:
            print(f"Dispositivos encontrados: {len(devices)}")
            for device, advertisement in devices:
                has_heart_rate_service = any(
                    uuid.lower() == HEART_RATE_SERVICE_UUID
                    for uuid in advertisement.service_uuids
                )
                heart_rate_label = (
                    " [serviço cardíaco]" if has_heart_rate_service else ""
                )
                print(
                    f"- {device.name or 'Sem nome'} "
                    f"[{device.address}]{heart_rate_label}"
                )

    print("\n=== Dispositivos ANT+ ===")
    devices, error = await asyncio.get_running_loop().run_in_executor(
        None, scan_ant_devices, 10
    )
    if error:
        print(
            "Não foi possível ler dispositivos ANT+: "
            f"{error}. Verifique se o dongle USB ANT+ está conectado e acessível."
        )
    elif not devices:
        print("Nenhum dispositivo ANT+ encontrado.")
    else:
        print(f"Dispositivos encontrados: {len(devices)}")
        for device_id, device_type, transmission_type in devices:
            device_label = (
                "Frequência cardíaca"
                if device_type == 120
                else f"Tipo ANT+ {device_type}"
            )
            print(
                f"- {device_label} [ID {device_id}, "
                f"transmissão {transmission_type}]"
            )


async def find_devices(device_ids: Optional[List[str]]) -> List[BLEDevice]:
    print("Procurando sensores BLE...")
    discovered = await BleakScanner.discover(timeout=10, return_adv=True)
    devices = [advertisement[0] for advertisement in discovered.values()]
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
            print("Nenhum dispositivo BLE encontrado; continuando busca ANT+.")
            return []
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
) -> Dict[str, Path]:
    reading_paths: Dict[str, Path] = {}
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

    write_device_index(output_dir, reading_paths)
    return reading_paths


def write_device_index(output_dir: Path, reading_paths: Dict[str, Path]) -> None:
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


def register_device_page(
    output_dir: Path,
    reading_paths: Dict[str, Path],
    device_id: str,
    output_path: Path,
) -> None:
    if device_id in reading_paths:
        return
    reading_paths[device_id] = output_path
    (output_dir / f"{device_id}.html").write_text(
        render_overlay_page(device_id), encoding="utf-8"
    )
    write_device_index(output_dir, reading_paths)


def create_overlay_server(
    output_dir: Path, reading_paths: Dict[str, Path]
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
            del _format, _args

    return ThreadingHTTPServer((OVERLAY_HOST, OVERLAY_PORT), OverlayRequestHandler)


async def monitor_device(
    device: BLEDevice, output_path: Path, display: LiveStatusDisplay
) -> bool:
    latest_bpm: Optional[int] = None

    def handle_measurement(_sender: int, data: bytearray) -> None:
        del _sender
        nonlocal latest_bpm
        latest_bpm = parse_heart_rate(data)

    display.update(device.address, "Conectando...")
    try:
        async with BleakClient(device, timeout=BLE_CONNECT_TIMEOUT_SECONDS) as client:
            await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_measurement)
            output_path.write_text("", encoding="utf-8")
            display.update(device.address, "Conectado; aguardando leitura")
            loop = asyncio.get_running_loop()
            next_sample = loop.time() + 1
            while client.is_connected:
                await asyncio.sleep(max(0, next_sample - loop.time()))
                if latest_bpm is not None:
                    output_path.write_text(str(latest_bpm), encoding="utf-8")
                    display.update(device.address, f"{latest_bpm} bpm")

                next_sample += 1
                if next_sample <= loop.time():
                    next_sample = loop.time() + 1
        display.update(device.address, "Conexão encerrada")
        return True
    except asyncio.TimeoutError:
        display.update(
            device.address,
            f"Erro: conexão/descoberta excedeu "
            f"{BLE_CONNECT_TIMEOUT_SECONDS}s. Aproxime e acorde o sensor e "
            "feche outros aplicativos conectados a ele.",
        )
    except BleakError as error:
        display.update(device.address, f"Erro Bluetooth: {error}")
    return False


async def record(device_ids: Optional[List[str]], output_dir: Path) -> None:
    devices = await find_devices(device_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        device.address: output_dir / output_filename(device.address)
        for device in devices
    }
    display = LiveStatusDisplay()
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
        display.register(
            device.address, f"{device.name or 'sensor'} [{device.address}]"
        )

    loop = asyncio.get_running_loop()
    ant_stop_event = threading.Event()
    ant_started_event = threading.Event()

    def report_ant_status(message: str) -> None:
        if message.startswith("ANT+: dongle detectado"):
            ant_started_event.set()
            return
        loop.call_soon_threadsafe(sys.stderr.write, f"{message}\n")

    def register_ant_device(device: Tuple[int, int, int]) -> None:
        device_id = device[0]
        overlay_id = f"ANT_{device_id:05d}"
        output_path = output_dir / output_filename(overlay_id)
        register_device_page(output_dir, reading_paths, overlay_id, output_path)
        display.register(overlay_id, f"ANT+ ID {device_id}")

    def write_ant_reading(overlay_id: str, device_id: int, bpm: int) -> None:
        output_path = reading_paths.get(overlay_id)
        if output_path is None:
            output_path = output_dir / output_filename(overlay_id)
            register_device_page(output_dir, reading_paths, overlay_id, output_path)
            display.register(overlay_id, f"ANT+ ID {device_id}")
        output_path.write_text(str(bpm), encoding="utf-8")
        display.update(overlay_id, f"{bpm} bpm")

    def run_ant_monitor() -> None:
        try:
            monitor_ant_heart_rate(
                ant_stop_event,
                lambda device: loop.call_soon_threadsafe(register_ant_device, device),
                lambda overlay_id, device_id, bpm: loop.call_soon_threadsafe(
                    write_ant_reading, overlay_id, device_id, bpm
                ),
                report_ant_status,
            )
        except Exception as error:
            report_ant_status(f"ANT+: erro durante a inicialização/leitura: {error}")

    ant_thread = threading.Thread(
        target=run_ant_monitor, name="antplus-monitor", daemon=True
    )
    ant_thread.start()

    try:
        ble_monitor_succeeded = False
        if devices:
            results = await asyncio.gather(
                *(
                    monitor_device(device, output_paths[device.address], display)
                    for device in devices
                ),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            ble_monitor_succeeded = any(result is True for result in results)
            if not ble_monitor_succeeded and ant_thread.is_alive():
                await asyncio.get_running_loop().run_in_executor(
                    None, ant_thread.join
                )
        else:
            print("Nenhum sensor BLE selecionado; aguardando sensores ANT+.")
            await asyncio.get_running_loop().run_in_executor(
                None, ant_thread.join
            )

        if not ant_started_event.is_set() and not ble_monitor_succeeded:
            print("Nenhum sensor BLE ou dongle ANT+ disponível para monitoramento.")
    finally:
        ant_stop_event.set()
        ant_thread.join(timeout=10)
        if ant_thread.is_alive():
            print("ANT+: encerramento ainda em andamento.")
        server.shutdown()
        server.server_close()
        server_thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Lê batimentos de sensores BLE e ANT+ e grava uma amostra por segundo."
        )
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
        "--list",
        action="store_true",
        help="Busca BLE e ANT+ e lista os dispositivos próximos, sem conectar.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Pasta dos arquivos de saída (padrão: pasta atual).",
    )
    args = parser.parse_args()

    try:
        if args.list:
            asyncio.run(list_available_devices())
            return
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
