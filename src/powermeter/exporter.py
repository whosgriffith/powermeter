"""
Prometheus exporter for the PC's smart plug.

The main loop polls the plug every POLL_SECONDS and keeps the latest reading.
Prometheus scrapes http://<host>:PORT/metrics and stores the time series
(see prometheus.yml). Nothing is stored here.

Metrics (base units, per Prometheus naming conventions):
    powermeter_up                    1 if the plug answered within the last 3 polls
    powermeter_power_watts           only exported while up
    powermeter_voltage_volts         only exported while up
    powermeter_current_amperes       only exported while up
    powermeter_poll_errors_total     failed polls of the plug

Energy is not computed here; derive it from the power samples in Prometheus/Grafana.
"""

import logging
import os
import time
from dataclasses import dataclass

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from .tuya import TuyaError, check, get_device, parse_dps

POLL_SECONDS = float(os.getenv("POLL_SECONDS", "5"))
STALE_SECONDS = POLL_SECONDS * 3
# The PC's smart plug. Its id survives re-pairing (only the local_key changes).
DEVICE_ID = "ebabc617e6c2fbba71dnee"

# What this plug reports: DPS index -> (name, divisor). See the DPS notes in tuya.py.
ENERGY_DPS = {
    "18": ("current_ma", 1),
    "19": ("power_w", 10),  # reported as W x10
    "20": ("voltage_v", 10),  # reported as V x10
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reading:
    taken_at: float  # time.monotonic(): only for durations, never jumps with clock changes
    power_w: float | None
    voltage_v: float | None
    current_ma: float | None


class Plug(Collector):
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.latest: Reading | None = None
        self.errors = 0

    def run(self) -> None:
        """Poll forever (blocking). Reconnects after any device error."""
        device = None
        failing_since = None  # only log when the plug goes down and when it comes back
        while True:
            started = time.monotonic()
            try:
                if device is None:
                    device = get_device(self.device_id, persist=True)
                device.updatedps([int(index) for index in ENERGY_DPS])  # ask the plug to refresh its cached readings
                data = check(device.status())
                self._record(parse_dps(data.get("dps", {}), ENERGY_DPS))
                if failing_since is not None:
                    log.info("plug reachable again after %.0fs", time.monotonic() - failing_since)
                    failing_since = None
            except TuyaError as error:
                self.errors += 1
                if failing_since is None:
                    failing_since = started
                    log.warning("plug unreachable, retrying every %.0fs: %s: %s", POLL_SECONDS, error.payload.get("Err"), error.payload.get("Error"))
                if device is not None:
                    device.close()
                device = None
            time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))

    def _record(self, values: dict) -> None:
        self.latest = Reading(time.monotonic(), values.get("power_w"), values.get("voltage_v"), values.get("current_ma"))

    def collect(self):
        """Called by prometheus_client on every scrape (from its HTTP thread)."""
        reading = self.latest
        up = reading is not None and time.monotonic() - reading.taken_at < STALE_SECONDS
        yield GaugeMetricFamily("powermeter_up", "1 if the plug answered within the last 3 polls", value=int(up))
        if up:
            for name, description, value in (
                ("powermeter_power_watts", "Active power", reading.power_w),
                ("powermeter_voltage_volts", "Mains voltage", reading.voltage_v),
                ("powermeter_current_amperes", "Current", None if reading.current_ma is None else reading.current_ma / 1000),
            ):
                if value is not None:
                    yield GaugeMetricFamily(name, description, value=value)
        yield CounterMetricFamily("powermeter_poll_errors", "Failed polls of the plug", value=self.errors)
