import threading
import time
from typing import Callable, List, Optional, Tuple

import usb.core
from openant.base.driver import DriverNotFound
from openant.devices import ANTPLUS_NETWORK_KEY
from openant.devices.common import DeviceType
from openant.devices.heart_rate import HeartRate
from openant.devices.scanner import Scanner
from openant.easy.node import Node

AntDevice = Tuple[int, int, int]
DeviceFoundCallback = Callable[[AntDevice], None]
HeartRateCallback = Callable[[str, int, int], None]
StatusCallback = Callable[[str], None]


def _create_scanner():
    node = Node()
    try:
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        scanner = Scanner(node)
    except Exception:
        node.stop()
        raise
    return node, scanner


def _close_channel(channel_owner: object, label: str, on_error: StatusCallback) -> None:
    try:
        channel_owner.close_channel()
    except Exception as error:
        on_error(f"Falha ao encerrar canal ANT+ de {label}: {error}")


def scan_ant_devices(
    timeout: int = 10,
) -> Tuple[List[AntDevice], Optional[str]]:
    try:
        node, scanner = _create_scanner()
    except (DriverNotFound, usb.core.USBError, usb.core.NoBackendError) as error:
        return [], str(error) or "dongle ANT+ compatível não encontrado"
    except Exception as error:
        return [], f"falha ao inicializar o dongle ANT+: {error}"

    found: List[AntDevice] = []
    scanner.on_found = found.append
    worker_errors: List[Exception] = []

    def run_node() -> None:
        try:
            node.start()
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(target=run_node, name="antplus-scan", daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + timeout
        while worker.is_alive() and time.monotonic() < deadline:
            worker.join(timeout=0.1)
    finally:
        _close_channel(scanner, "scanner", lambda message: print(message))
        node.stop()
        worker.join(timeout=2)

    if worker_errors:
        return found, str(worker_errors[0])
    return found, None


def monitor_ant_heart_rate(
    stop_event: threading.Event,
    on_device_found: DeviceFoundCallback,
    on_heart_rate: HeartRateCallback,
    on_status: StatusCallback,
) -> None:
    try:
        node, scanner = _create_scanner()
    except (DriverNotFound, usb.core.USBError, usb.core.NoBackendError) as error:
        detail = str(error) or "dongle ANT+ compatível não encontrado"
        on_status(
            "ANT+: não foi possível ler dispositivos ANT+; "
            f"{detail}. O BLE continuará normalmente."
        )
        return

    profiles: List[HeartRate] = []
    profile_ids = set()
    profiles_lock = threading.Lock()
    worker_errors: List[Exception] = []

    def found(device: AntDevice) -> None:
        device_id, device_type, transmission_type = device
        if device_type != DeviceType.HeartRate.value:
            return

        with profiles_lock:
            if stop_event.is_set() or device_id in profile_ids:
                return
            try:
                profile = HeartRate(
                    node,
                    device_id=device_id,
                    name=f"ANT_HR_{device_id}",
                    trans_type=transmission_type,
                )

                def on_data(_page: int, page_name: str, data: object) -> None:
                    if page_name != "heart_rate":
                        return
                    bpm = getattr(data, "heart_rate", None)
                    if isinstance(bpm, int):
                        on_heart_rate(f"ANT_{device_id:05d}", device_id, bpm)

                profile.on_device_data = on_data
                profiles.append(profile)
                profile_ids.add(device_id)
            except Exception as error:
                on_status(
                    f"ANT+: não foi possível iniciar o sensor cardíaco "
                    f"{device_id}: {error}"
                )
                return

        on_device_found(device)

    scanner.on_found = found

    def run_node() -> None:
        try:
            node.start()
        except Exception as error:
            worker_errors.append(error)

    worker = threading.Thread(target=run_node, name="antplus-monitor", daemon=True)
    worker.start()
    on_status("ANT+: dongle detectado; procurando sensores cardíacos.")
    try:
        while worker.is_alive() and not stop_event.wait(0.2):
            pass
    finally:
        with profiles_lock:
            active_profiles = list(profiles)
        for profile in active_profiles:
            _close_channel(profile, str(profile), on_status)
        _close_channel(scanner, "scanner", on_status)
        node.stop()
        worker.join(timeout=2)

    if worker_errors:
        on_status(f"ANT+: erro durante a leitura: {worker_errors[0]}")
