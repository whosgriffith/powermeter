import logging
import os

from prometheus_client import REGISTRY, start_http_server

from .exporter import DEVICE_ID, Plug
from .tuya import load_devices

PORT = int(os.getenv("PORT", "9850"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if DEVICE_ID not in load_devices():
        raise SystemExit(f"Device {DEVICE_ID} not found in devices.json (re-run the tinytuya wizard)")
    plug = Plug(DEVICE_ID)
    REGISTRY.register(plug)
    start_http_server(PORT)  # serves /metrics from a background thread
    logging.info("metrics on http://localhost:%d/metrics", PORT)
    try:
        plug.run()
    except KeyboardInterrupt:
        pass
