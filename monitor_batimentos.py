import argparse
import asyncio
import html
import json
import os
import re
import shutil
import sys
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from antplus import monitor_ant_heart_rate, scan_ant_devices

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
OVERLAY_HOST = "0.0.0.0"
OVERLAY_PORT = 8765
READING_MAX_AGE_SECONDS = 3
HISTORY_RETENTION_SECONDS = 30 * 60
BLE_CONNECT_TIMEOUT_SECONDS = 30
SENSOR_NAME_MAX_LENGTH = 80


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


class HeartRateHistory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._readings: Dict[str, Deque[Tuple[float, int]]] = {}

    def record(self, device_id: str, bpm: int) -> None:
        timestamp = datetime.now().timestamp()
        cutoff = timestamp - HISTORY_RETENTION_SECONDS
        with self._lock:
            readings = self._readings.setdefault(device_id, deque())
            while readings and readings[0][0] < cutoff:
                readings.popleft()
            readings.append((timestamp, bpm))

    def since(self, device_id: str, minutes: int) -> List[Tuple[float, int]]:
        cutoff = datetime.now().timestamp() - minutes * 60
        with self._lock:
            readings = self._readings.get(device_id, ())
            return [
                (timestamp * 1000, bpm)
                for timestamp, bpm in readings
                if timestamp >= cutoff
            ]


def sensor_name(device_id: str, sensor_names: Dict[str, str]) -> str:
    name = sensor_names.get(device_id.casefold())
    if name:
        return name
    filename_id = Path(output_filename(device_id)).stem.casefold()
    name = sensor_names.get(filename_id)
    if name:
        return name
    for configured_id, configured_name in sensor_names.items():
        if Path(output_filename(configured_id)).stem.casefold() == filename_id:
            return configured_name
    return device_id


def sensor_identity(
    device_id: str, sensor_metadata: Dict[str, Dict[str, str]]
) -> Tuple[str, str]:
    metadata = sensor_metadata.get(device_id, {})
    if metadata:
        return metadata["device_name"], metadata["address"]
    if device_id.startswith("ANT_"):
        return "ANT+ Heart Rate", f"ANT+ ID {device_id[4:]}"
    if re.fullmatch(r"(?:[A-Fa-f0-9]{2}_){5}[A-Fa-f0-9]{2}", device_id):
        return "BLE sensor", device_id.replace("_", ":")
    return device_id, device_id


def register_sensor_aliases(
    sensor_names: Dict[str, str], device_id: str, aliases: List[str]
) -> None:
    name = next(
        (
            sensor_names[alias.casefold()]
            for alias in [device_id, *aliases]
            if alias and alias.casefold() in sensor_names
        ),
        device_id,
    )
    for alias in [device_id, *aliases]:
        if alias:
            sensor_names[alias.casefold()] = name


def parse_sensor_names(values: Optional[List[str]]) -> Dict[str, str]:
    sensor_names: Dict[str, str] = {}
    for value in values or []:
        sensor_id, separator, name = value.partition("=")
        sensor_id = sensor_id.strip()
        name = name.strip()
        if not separator or not sensor_id or not name:
            raise ValueError(
                f"Nome inválido {value!r}. Use o formato ID-DO-SENSOR=NOME."
            )
        key = sensor_id.casefold()
        if key in sensor_names:
            raise ValueError(f"O sensor {sensor_id!r} foi nomeado mais de uma vez.")
        sensor_names[key] = name
    return sensor_names


def load_sensor_names(output_dir: Path, configured_names: Dict[str, str]) -> Dict[str, str]:
    names_path = output_dir / "sensor_names.json"
    sensor_names: Dict[str, str] = {}
    if names_path.exists():
        try:
            saved_names = json.loads(names_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(
                f"Não foi possível carregar os nomes salvos em {names_path}: {error}"
            ) from error
        if not isinstance(saved_names, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not value.strip()
            for key, value in saved_names.items()
        ):
            raise RuntimeError(
                f"O arquivo {names_path} deve conter um objeto JSON de nomes."
            )
        sensor_names.update(
            {key.casefold(): value.strip() for key, value in saved_names.items()}
        )
    sensor_names.update(configured_names)
    return sensor_names


def save_sensor_names(output_dir: Path, sensor_names: Dict[str, str]) -> None:
    names_path = output_dir / "sensor_names.json"
    temporary_path = names_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(sensor_names, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(names_path)


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


def render_history_overlay_page(device_id: str, name: str) -> str:
    api_path = f"/api/{device_id}/history.json"
    escaped_name = html.escape(name)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Histórico cardíaco - {escaped_name}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, Helvetica, sans-serif; }}
    * {{ box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; margin: 0; background: transparent; }}
    body {{ display: grid; place-items: center; color: #fff; }}
    .card {{
      width: min(94vw, 760px); padding: 18px 22px;
      border: 1px solid rgb(255 255 255 / 18%); border-radius: 16px;
      background: rgb(12 16 24 / 88%); box-shadow: 0 8px 32px rgb(0 0 0 / 35%);
    }}
    header {{ display: flex; align-items: center; justify-content: space-between;
              gap: 16px; margin-bottom: 12px; }}
    h1 {{ margin: 0; font-size: 18px; }}
    label {{ display: flex; align-items: center; gap: 10px; color: #d3d9e3;
             font-size: 14px; white-space: nowrap; }}
    input {{ width: 130px; accent-color: #ff4664; }}
    #timeline {{ min-width: 62px; color: #fff; font-variant-numeric: tabular-nums; }}
    canvas {{ display: block; width: 100%; height: 260px; }}
    #status {{ min-height: 18px; margin-top: 5px; color: #aab4c3; font-size: 13px; }}
    @media (max-width: 480px) {{
      .card {{ padding: 14px; }}
      header {{ align-items: flex-start; flex-direction: column; gap: 10px; }}
      canvas {{ height: 210px; }}
    }}
  </style>
</head>
<body>
  <main class="card">
    <header>
      <h1>Batimentos cardíacos · {escaped_name}</h1>
      <label for="minutes">
        Janela
        <input id="minutes" type="range" min="1" max="30" value="30">
        <span id="timeline">30 min</span>
      </label>
    </header>
    <canvas id="chart" role="img" aria-label="Gráfico de batimentos cardíacos"></canvas>
    <div id="status" aria-live="polite">Aguardando leituras do sensor</div>
  </main>
  <script>
    const canvas = document.getElementById("chart");
    const context = canvas.getContext("2d");
    const minutesInput = document.getElementById("minutes");
    const timelineElement = document.getElementById("timeline");
    const statusElement = document.getElementById("status");
    let readings = [];

    function drawChart() {{
      const bounds = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(bounds.width * ratio);
      canvas.height = Math.round(bounds.height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);

      const width = bounds.width;
      const height = bounds.height;
      const padding = {{ left: 42, right: 12, top: 12, bottom: 26 }};
      const chartWidth = width - padding.left - padding.right;
      const chartHeight = height - padding.top - padding.bottom;
      const now = Date.now();
      const start = now - Number(minutesInput.value) * 60 * 1000;
      const visibleReadings = readings.filter(([timestamp]) => timestamp >= start);
      const values = visibleReadings.map(([, bpm]) => bpm);
      const minimum = values.length ? Math.min(...values) : 40;
      const maximum = values.length ? Math.max(...values) : 200;
      const lower = Math.max(0, minimum - 10);
      const upper = Math.max(lower + 20, maximum + 10);

      context.clearRect(0, 0, width, height);
      context.font = "12px Arial, sans-serif";
      context.textBaseline = "middle";
      for (let index = 0; index <= 4; index += 1) {{
        const y = padding.top + chartHeight * index / 4;
        const bpm = Math.round(upper - (upper - lower) * index / 4);
        context.strokeStyle = "rgba(255,255,255,0.12)";
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(width - padding.right, y);
        context.stroke();
        context.fillStyle = "#aab4c3";
        context.textAlign = "right";
        context.fillText(String(bpm), padding.left - 8, y);
      }}

      context.fillStyle = "#aab4c3";
      context.textBaseline = "alphabetic";
      context.textAlign = "left";
      context.fillText(new Date(start).toLocaleTimeString(), padding.left, height - 4);
      context.textAlign = "right";
      context.fillText(new Date(now).toLocaleTimeString(), width - padding.right, height - 4);

      if (!visibleReadings.length) return;
      context.strokeStyle = "#ff4664";
      context.lineWidth = 2.5;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.beginPath();
      visibleReadings.forEach(([timestamp, bpm], index) => {{
        const x = padding.left + (timestamp - start) / (now - start) * chartWidth;
        const y = padding.top + (upper - bpm) / (upper - lower) * chartHeight;
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }});
      context.stroke();
    }}

    async function updateHistory() {{
      const minutes = Number(minutesInput.value);
      timelineElement.textContent = minutes + " min";
      try {{
        const response = await fetch(
          "{api_path}?minutes=" + minutes + "&t=" + Date.now(),
          {{ cache: "no-store" }}
        );
        if (!response.ok) throw new Error("Falha ao consultar o histórico");
        const data = await response.json();
        readings = data.readings;
        statusElement.textContent = readings.length
          ? readings.length + " amostras na janela selecionada"
          : "Aguardando leituras do sensor";
        drawChart();
      }} catch {{
        statusElement.textContent = "Histórico indisponível";
      }}
    }}

    minutesInput.addEventListener("input", updateHistory);
    window.addEventListener("resize", drawChart);
    updateHistory();
    window.setInterval(updateHistory, 5000);
  </script>
</body>
</html>
"""


def render_dashboard_page() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard de atletas</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, Helvetica, sans-serif; }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; margin: 0; background: #10141c; color: #fff; }
    body { padding: clamp(16px, 4vw, 36px); }
    main { width: min(1100px, 100%); margin: 0 auto; }
    header { display: flex; flex-wrap: wrap; align-items: center;
             justify-content: space-between; gap: 14px; margin-bottom: 20px; }
    h1 { margin: 0; font-size: clamp(22px, 4vw, 30px); }
    header a { color: #c9d6ec; }
    .window { display: flex; align-items: center; gap: 10px; color: #d3d9e3; }
    input { accent-color: #ff4664; }
    #minutes { width: 140px; }
    #timeline { min-width: 55px; font-variant-numeric: tabular-nums; }
    #athletes { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
                gap: 12px; margin-bottom: 18px; }
    .athlete { padding: 16px; border: 1px solid rgb(255 255 255 / 12%);
               border-radius: 12px; background: rgb(32 41 56 / 92%); }
    .athlete h2 { overflow: hidden; margin: 0 0 12px; font-size: 16px;
                  text-overflow: ellipsis; white-space: nowrap; }
    .bpm { font-size: 38px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .unit { margin-left: 6px; color: #c3ccda; font-size: 14px; }
    .state { margin-top: 6px; color: #aab4c3; font-size: 13px; }
    .chart-card { padding: 16px; border: 1px solid rgb(255 255 255 / 12%);
                  border-radius: 12px; background: rgb(12 16 24 / 88%); }
    canvas { display: block; width: 100%; height: 340px; }
    #legend { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 12px; }
    .legend-item { display: flex; align-items: center; gap: 7px;
                   color: #d3d9e3; font-size: 13px; }
    .legend-color { width: 10px; height: 10px; border-radius: 50%; }
    #status { min-height: 18px; margin-top: 10px; color: #aab4c3; font-size: 13px; }
    @media (max-width: 480px) { canvas { height: 250px; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Dashboard de atletas</h1>
        <a href="/index.htm">Voltar à página inicial</a>
      </div>
      <label class="window" for="minutes">
        Histórico
        <input id="minutes" type="range" min="1" max="30" value="30">
        <span id="timeline">30 min</span>
      </label>
    </header>
    <section id="athletes" aria-live="polite"></section>
    <section class="chart-card">
      <canvas id="chart" role="img"
              aria-label="Gráfico de batimentos cardíacos de todos os atletas"></canvas>
      <div id="legend"></div>
      <div id="status" aria-live="polite">Conectando aos sensores...</div>
    </section>
  </main>
  <script>
    const palette = ["#ff4664", "#45c4b0", "#6ba7ff", "#ffc857", "#b58cff",
                     "#ff8f5e", "#8bd450", "#f277b5", "#48d7e8", "#d0d46a"];
    const athletesElement = document.getElementById("athletes");
    const minutesInput = document.getElementById("minutes");
    const timelineElement = document.getElementById("timeline");
    const statusElement = document.getElementById("status");
    const legendElement = document.getElementById("legend");
    const canvas = document.getElementById("chart");
    const context = canvas.getContext("2d");
    let athletes = [];
    let histories = [];

    function colorFor(index) {
      if (index < palette.length) return palette[index];
      return "hsl(" + (index * 47 % 360) + ", 78%, 64%)";
    }

    function renderAthletes() {
      athletesElement.replaceChildren();
      athletes.forEach((athlete, index) => {
        const card = document.createElement("article");
        card.className = "athlete";
        const heading = document.createElement("h2");
        heading.textContent = athlete.name;
        const value = document.createElement("span");
        value.className = "bpm";
        value.textContent = athlete.bpm === null ? "--" : athlete.bpm;
        const unit = document.createElement("span");
        unit.className = "unit";
        unit.textContent = "BPM";
        const state = document.createElement("div");
        state.className = "state";
        state.textContent = athlete.bpm === null ? "Sem leitura recente" : "Ao vivo";
        card.style.borderTop = "3px solid " + colorFor(index);
        card.append(heading, value, unit, state);
        athletesElement.append(card);
      });
    }

    function renderLegend() {
      legendElement.replaceChildren();
      histories.forEach((athlete, index) => {
        const item = document.createElement("span");
        item.className = "legend-item";
        const marker = document.createElement("span");
        marker.className = "legend-color";
        marker.style.backgroundColor = colorFor(index);
        const name = document.createElement("span");
        name.textContent = athlete.name;
        item.append(marker, name);
        legendElement.append(item);
      });
    }

    function drawChart() {
      const bounds = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(bounds.width * ratio);
      canvas.height = Math.round(bounds.height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const width = bounds.width;
      const height = bounds.height;
      const padding = { left: 44, right: 14, top: 14, bottom: 28 };
      const chartWidth = width - padding.left - padding.right;
      const chartHeight = height - padding.top - padding.bottom;
      const now = Date.now();
      const start = now - Number(minutesInput.value) * 60 * 1000;
      const visibleHistories = histories.map((athlete) => ({
        ...athlete,
        readings: athlete.readings.filter(([timestamp]) => timestamp >= start)
      }));
      const values = visibleHistories.flatMap((athlete) =>
        athlete.readings.map(([, bpm]) => bpm)
      );
      const minimum = values.length ? Math.min(...values) : 40;
      const maximum = values.length ? Math.max(...values) : 200;
      const lower = Math.max(0, minimum - 10);
      const upper = Math.max(lower + 20, maximum + 10);

      context.clearRect(0, 0, width, height);
      context.font = "12px Arial, sans-serif";
      context.textBaseline = "middle";
      for (let index = 0; index <= 4; index += 1) {
        const y = padding.top + chartHeight * index / 4;
        const bpm = Math.round(upper - (upper - lower) * index / 4);
        context.strokeStyle = "rgba(255,255,255,0.12)";
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(width - padding.right, y);
        context.stroke();
        context.fillStyle = "#aab4c3";
        context.textAlign = "right";
        context.fillText(String(bpm), padding.left - 8, y);
      }

      context.fillStyle = "#aab4c3";
      context.textBaseline = "alphabetic";
      context.textAlign = "left";
      context.fillText(new Date(start).toLocaleTimeString(), padding.left, height - 4);
      context.textAlign = "right";
      context.fillText(new Date(now).toLocaleTimeString(), width - padding.right, height - 4);

      visibleHistories.forEach((athlete, athleteIndex) => {
        if (!athlete.readings.length) return;
        context.strokeStyle = colorFor(athleteIndex);
        context.lineWidth = 2;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.beginPath();
        athlete.readings.forEach(([timestamp, bpm], readingIndex) => {
          const x = padding.left + (timestamp - start) / (now - start) * chartWidth;
          const y = padding.top + (upper - bpm) / (upper - lower) * chartHeight;
          if (readingIndex === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();
      });
    }

    async function updateLiveReadings() {
      try {
        const response = await fetch("/api/dashboard/live.json?t=" + Date.now(),
                                     { cache: "no-store" });
        if (!response.ok) throw new Error("Falha ao consultar batimentos ao vivo");
        athletes = (await response.json()).athletes;
        renderAthletes();
        renderLegend();
        statusElement.textContent = athletes.length
          ? "Atualizado agora · " + athletes.length + " sensores"
          : "Nenhum sensor disponível";
      } catch {
        statusElement.textContent = "Não foi possível atualizar as leituras";
      }
    }

    async function updateHistories() {
      const minutes = Number(minutesInput.value);
      timelineElement.textContent = minutes + " min";
      try {
        const response = await fetch(
          "/api/dashboard/history.json?minutes=" + minutes + "&t=" + Date.now(),
          { cache: "no-store" }
        );
        if (!response.ok) throw new Error("Falha ao consultar o histórico");
        histories = (await response.json()).athletes;
        renderLegend();
        drawChart();
      } catch {
        statusElement.textContent = "Não foi possível atualizar o histórico";
      }
    }

    minutesInput.addEventListener("input", updateHistories);
    window.addEventListener("resize", drawChart);
    updateLiveReadings();
    updateHistories();
    window.setInterval(updateLiveReadings, 1000);
    window.setInterval(updateHistories, 5000);
  </script>
</body>
</html>
"""


def update_device_pages(
    output_dir: Path,
    active_output_paths: List[Path],
    sensor_names: Dict[str, str],
    sensor_metadata: Dict[str, Dict[str, str]],
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
        history_page_path = output_dir / f"{device_id}-history.html"
        history_page_path.write_text(
            render_history_overlay_page(device_id, sensor_name(device_id, sensor_names)),
            encoding="utf-8",
        )

    write_device_index(output_dir, reading_paths, sensor_names, sensor_metadata)
    (output_dir / "dashboard.html").write_text(
        render_dashboard_page(), encoding="utf-8"
    )
    return reading_paths


def read_recent_heart_rate(reading_path: Path) -> Optional[int]:
    content = reading_path.read_text(encoding="utf-8").strip()
    modified_at = reading_path.stat().st_mtime
    is_recent = datetime.now().timestamp() - modified_at <= READING_MAX_AGE_SECONDS
    return int(content) if content and is_recent else None


def write_device_index(
    output_dir: Path,
    reading_paths: Dict[str, Path],
    sensor_names: Dict[str, str],
    sensor_metadata: Dict[str, Dict[str, str]],
) -> None:
    index_entries = "\n".join(
        f'<li><strong class="device-name">{html.escape(sensor_name(device_id, sensor_names))}</strong>'
        f'<dl class="device-details"><dt>Dispositivo</dt><dd>{html.escape(sensor_identity(device_id, sensor_metadata)[0])}</dd>'
        f'<dt>Endereço</dt><dd>{html.escape(sensor_identity(device_id, sensor_metadata)[1])}</dd></dl>'
        f'<form class="name-form" data-device-id="{html.escape(device_id, quote=True)}">'
        f'<label>Nome da pessoa <input name="name" required maxlength="{SENSOR_NAME_MAX_LENGTH}" '
        f'value="{html.escape(sensor_name(device_id, sensor_names), quote=True)}"></label>'
        f'<button type="submit">Salvar nome</button>'
        f'<span class="save-status" aria-live="polite"></span></form>'
        f'<div class="links"><a href="/{html.escape(device_id, quote=True)}.html">'
        f'Leitura atual</a>'
        f'<a href="/history/{html.escape(device_id, quote=True)}.html">'
        f'Histórico (30 min)</a></div></li>'
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
    .dashboard-link {{ display: inline-block; margin-bottom: 12px; padding: 10px 14px;
                       border-radius: 8px; background: #ff4664; color: #fff;
                       font-weight: 700; text-decoration: none; }}
    ul {{ display: grid; gap: 12px; padding: 0; list-style: none; }}
    li {{ padding: 16px; border-radius: 10px; background: #202938; }}
    .name-form {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
                  margin-top: 10px; }}
    .name-form label {{ display: flex; align-items: center; gap: 8px; color: #d3d9e3; }}
    .device-details {{ display: grid; grid-template-columns: auto 1fr; gap: 4px 10px;
                       margin: 10px 0; font-size: 13px; }}
    .device-details dt {{ color: #aab4c3; }}
    .device-details dd {{ overflow-wrap: anywhere; margin: 0; color: #fff; }}
    input {{ width: min(240px, 55vw); padding: 8px; border: 1px solid #52627a;
             border-radius: 6px; background: #10141c; color: #fff; }}
    button {{ padding: 9px 12px; border: 0; border-radius: 6px;
              background: #405879; color: #fff; cursor: pointer; }}
    button:disabled {{ opacity: .6; cursor: wait; }}
    .save-status {{ color: #aab4c3; font-size: 13px; }}
    .links {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }}
    a {{ display: block; padding: 10px 12px; border-radius: 8px;
         background: #30425c; color: #fff; text-decoration: none; }}
    a:hover, a:focus {{ background: #405879; }}
  </style>
</head>
<body>
  <p><a class="dashboard-link" href="/dashboard.html">Dashboard de todos os atletas</a></p>
  <h1>Selecione o dispositivo</h1>
  <ul>{index_entries}</ul>
  <script>
    document.querySelectorAll(".name-form").forEach((form) => {{
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const button = form.querySelector("button");
        const status = form.querySelector(".save-status");
        const input = form.elements.namedItem("name");
        const deviceId = form.dataset.deviceId;
        button.disabled = true;
        status.textContent = "Salvando...";
        try {{
          const response = await fetch(
            "/api/" + encodeURIComponent(deviceId) + "/name.json",
            {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify({{ name: input.value }})
            }}
          );
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || "Falha ao salvar nome");
          form.closest("li").querySelector(".device-name").textContent = result.name;
          status.textContent = "Nome salvo";
        }} catch (error) {{
          status.textContent = error.message || "Falha ao salvar nome";
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
  </script>
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
    sensor_names: Dict[str, str],
    sensor_metadata: Dict[str, Dict[str, str]],
) -> None:
    if device_id in reading_paths:
        return
    reading_paths[device_id] = output_path
    (output_dir / f"{device_id}.html").write_text(
        render_overlay_page(device_id), encoding="utf-8"
    )
    (output_dir / f"{device_id}-history.html").write_text(
        render_history_overlay_page(device_id, sensor_name(device_id, sensor_names)),
        encoding="utf-8",
    )
    write_device_index(output_dir, reading_paths, sensor_names, sensor_metadata)


def create_overlay_server(
    output_dir: Path,
    reading_paths: Dict[str, Path],
    history: HeartRateHistory,
    sensor_names: Dict[str, str],
    sensor_aliases: Dict[str, List[str]],
    sensor_metadata: Dict[str, Dict[str, str]],
    sensor_names_lock: threading.Lock,
) -> ThreadingHTTPServer:
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

            if request_path == "/dashboard.html":
                try:
                    content = (output_dir / "dashboard.html").read_bytes()
                except OSError:
                    self._send_response(
                        500, "text/plain; charset=utf-8", b"Dashboard indisponivel"
                    )
                    return
                self._send_response(200, "text/html; charset=utf-8", content)
                return

            page_path: Optional[Path] = None
            if request_path.startswith("/history/") and request_path.endswith(".html"):
                device_id = request_path[len("/history/") : -len(".html")]
                if device_id in reading_paths:
                    page_path = output_dir / f"{device_id}-history.html"
            elif request_path.startswith("/") and request_path.endswith(".html"):
                device_id = request_path[1:-len(".html")]
                if device_id in reading_paths:
                    page_path = output_dir / f"{device_id}.html"
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

            if request_path == "/api/dashboard/live.json":
                with sensor_names_lock:
                    devices = [
                        (device_id, path, sensor_name(device_id, sensor_names))
                        for device_id, path in reading_paths.items()
                    ]
                try:
                    athletes = [
                        {
                            "id": device_id,
                            "name": name,
                            "bpm": read_recent_heart_rate(path),
                        }
                        for device_id, path, name in devices
                    ]
                except (OSError, ValueError) as error:
                    self._send_response(
                        500,
                        "application/json; charset=utf-8",
                        json.dumps(
                            {"error": f"Falha ao ler batimentos: {error}"}
                        ).encode("utf-8"),
                    )
                    return
                self._send_response(
                    200,
                    "application/json; charset=utf-8",
                    json.dumps({"athletes": athletes}, ensure_ascii=False).encode(
                        "utf-8"
                    ),
                )
                return

            if request_path == "/api/dashboard/history.json":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    minutes = int(query.get("minutes", ["30"])[0])
                except ValueError:
                    minutes = 0
                if not 1 <= minutes <= 30:
                    self._send_response(
                        400,
                        "application/json; charset=utf-8",
                        b'{"error":"A janela deve estar entre 1 e 30 minutos"}',
                    )
                    return

                with sensor_names_lock:
                    devices = [
                        (device_id, sensor_name(device_id, sensor_names))
                        for device_id in reading_paths
                    ]
                athletes = [
                    {
                        "id": device_id,
                        "name": name,
                        "readings": history.since(device_id, minutes),
                    }
                    for device_id, name in devices
                ]
                self._send_response(
                    200,
                    "application/json; charset=utf-8",
                    json.dumps({"athletes": athletes}, ensure_ascii=False).encode(
                        "utf-8"
                    ),
                )
                return

            if request_path.startswith("/api/") and request_path.endswith(
                "/history.json"
            ):
                device_id = request_path[len("/api/") : -len("/history.json")]
                if device_id not in reading_paths:
                    self._send_response(
                        404,
                        "application/json; charset=utf-8",
                        b'{"error":"Dispositivo nao encontrado"}',
                    )
                    return

                query = parse_qs(urlsplit(self.path).query)
                try:
                    minutes = int(query.get("minutes", ["30"])[0])
                except ValueError:
                    minutes = 0
                if not 1 <= minutes <= 30:
                    self._send_response(
                        400,
                        "application/json; charset=utf-8",
                        b'{"error":"A janela deve estar entre 1 e 30 minutos"}',
                    )
                    return

                body = json.dumps(
                    {"readings": history.since(device_id, minutes)}
                ).encode("utf-8")
                self._send_response(200, "application/json; charset=utf-8", body)
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
                    bpm = read_recent_heart_rate(reading_path)
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

        def do_POST(self) -> None:
            request_path = urlsplit(self.path).path
            if not request_path.startswith("/api/") or not request_path.endswith(
                "/name.json"
            ):
                self._send_response(404, "text/plain; charset=utf-8", b"Nao encontrado")
                return

            device_id = unquote(request_path[len("/api/") : -len("/name.json")])
            if device_id not in reading_paths:
                self._send_response(
                    404,
                    "application/json; charset=utf-8",
                    b'{"error":"Dispositivo nao encontrado"}',
                )
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            if not 0 < content_length <= 4096:
                self._send_response(
                    400,
                    "application/json; charset=utf-8",
                    b'{"error":"Corpo da requisicao invalido"}',
                )
                return

            try:
                request_data = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, ValueError):
                self._send_response(
                    400,
                    "application/json; charset=utf-8",
                    b'{"error":"JSON invalido"}',
                )
                return
            if not isinstance(request_data, dict) or not isinstance(
                request_data.get("name"), str
            ):
                self._send_response(
                    400,
                    "application/json; charset=utf-8",
                    b'{"error":"Informe um nome valido"}',
                )
                return

            name = request_data["name"].strip()
            if not name or len(name) > SENSOR_NAME_MAX_LENGTH:
                self._send_response(
                    400,
                    "application/json; charset=utf-8",
                    b'{"error":"O nome deve ter de 1 a 80 caracteres"}',
                )
                return

            try:
                with sensor_names_lock:
                    aliases = sensor_aliases.get(device_id, [device_id])
                    for alias in aliases:
                        if alias:
                            sensor_names[alias.casefold()] = name
                    save_sensor_names(output_dir, sensor_names)
                    history_page_path = output_dir / f"{device_id}-history.html"
                    history_page_path.write_text(
                        render_history_overlay_page(device_id, name),
                        encoding="utf-8",
                    )
                    write_device_index(
                        output_dir, reading_paths, sensor_names, sensor_metadata
                    )
            except OSError as error:
                self._send_response(
                    500,
                    "application/json; charset=utf-8",
                    json.dumps({"error": f"Não foi possível salvar o nome: {error}"}).encode(
                        "utf-8"
                    ),
                )
                return

            self._send_response(
                200,
                "application/json; charset=utf-8",
                json.dumps({"name": name}, ensure_ascii=False).encode("utf-8"),
            )

        def log_message(self, _format: str, *_args: object) -> None:
            del _format, _args

    return ThreadingHTTPServer((OVERLAY_HOST, OVERLAY_PORT), OverlayRequestHandler)


async def monitor_device(
    device: BLEDevice,
    output_path: Path,
    display: LiveStatusDisplay,
    history: HeartRateHistory,
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
                    history.record(output_path.stem, latest_bpm)
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


async def record(
    device_ids: Optional[List[str]],
    output_dir: Path,
    configured_names: Dict[str, str],
) -> None:
    devices = await find_devices(device_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    sensor_names = load_sensor_names(output_dir, configured_names)
    sensor_names_lock = threading.Lock()
    sensor_aliases: Dict[str, List[str]] = {}
    sensor_metadata: Dict[str, Dict[str, str]] = {}
    output_paths = {
        device.address: output_dir / output_filename(device.address)
        for device in devices
    }
    for device in devices:
        output_path = output_paths[device.address]
        aliases = [device.address, device.name or "", output_path.stem]
        sensor_aliases[output_path.stem] = aliases
        sensor_metadata[output_path.stem] = {
            "device_name": device.name or "BLE sensor",
            "address": device.address,
        }
        register_sensor_aliases(
            sensor_names,
            device.address,
            aliases[1:],
        )
    display = LiveStatusDisplay()
    reading_paths = update_device_pages(
        output_dir, list(output_paths.values()), sensor_names, sensor_metadata
    )
    history = HeartRateHistory()
    server = create_overlay_server(
        output_dir,
        reading_paths,
        history,
        sensor_names,
        sensor_aliases,
        sensor_metadata,
        sensor_names_lock,
    )
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Lista de dispositivos: http://{OVERLAY_HOST}:{OVERLAY_PORT}/")
    for device in devices:
        page_name = output_paths[device.address].stem + ".html"
        print(
            f"Overlay {device.name or device.address}: "
            f"http://{OVERLAY_HOST}:{OVERLAY_PORT}/{page_name}"
        )
        print(
            f"Histórico {sensor_name(device.address, sensor_names)}: "
            f"http://{OVERLAY_HOST}:{OVERLAY_PORT}/history/{output_paths[device.address].stem}.html"
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
        with sensor_names_lock:
            sensor_aliases[overlay_id] = [overlay_id]
            sensor_metadata[overlay_id] = {
                "device_name": "ANT+ Heart Rate",
                "address": f"ANT+ ID {device_id}",
            }
            register_device_page(
                output_dir,
                reading_paths,
                overlay_id,
                output_path,
                sensor_names,
                sensor_metadata,
            )
            display.register(
                overlay_id,
                f"{sensor_name(overlay_id, sensor_names)} [ANT+ ID {device_id}]",
            )

    def write_ant_reading(overlay_id: str, device_id: int, bpm: int) -> None:
        output_path = reading_paths.get(overlay_id)
        if output_path is None:
            output_path = output_dir / output_filename(overlay_id)
            with sensor_names_lock:
                sensor_aliases[overlay_id] = [overlay_id]
                sensor_metadata[overlay_id] = {
                    "device_name": "ANT+ Heart Rate",
                    "address": f"ANT+ ID {device_id}",
                }
                register_device_page(
                    output_dir,
                    reading_paths,
                    overlay_id,
                    output_path,
                    sensor_names,
                    sensor_metadata,
                )
                display.register(
                    overlay_id,
                    f"{sensor_name(overlay_id, sensor_names)} [ANT+ ID {device_id}]",
                )
        output_path.write_text(str(bpm), encoding="utf-8")
        history.record(overlay_id, bpm)
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
                    monitor_device(
                        device, output_paths[device.address], display, history
                    )
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
        "--name",
        action="append",
        metavar="ID=NOME",
        help=(
            "Nome da pessoa que usa o sensor, no formato ID-DO-SENSOR=NOME. "
            "Pode ser repetida; para ANT+, use ANT_<id>."
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
        sensor_names = parse_sensor_names(args.name)
    except ValueError as error:
        parser.error(str(error))

    try:
        if args.list:
            asyncio.run(list_available_devices())
            return
        asyncio.run(record(args.device, args.output_dir, sensor_names))
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
