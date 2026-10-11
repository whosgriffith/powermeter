"""
TinyTuya crash course, as code.

THE MENTAL MODEL
----------------
Every Tuya device exposes a flat dict of "Data Points" (DPS), keyed by a string
number. Everything you do is reading or writing those keys:

    {"1": True, "9": 0, "18": 1234, "19": 2715, "20": 2301}
      ^switch  ^timer  ^mA        ^W*10       ^V*10

What each number means depends on the device model. Typical smart plug with
energy metering (what you want for a power meter):

    1   switch (bool)            18  current in mA (int)
    9   countdown seconds (int)  19  power in W, x10 (int)  -> 2715 = 271.5 W
    17  energy added (int)       20  voltage in V, x10 (int) -> 2301 = 230.1 V

Some plugs use 4/5/6 instead of 18/19/20. Check yours with
get_device(id).status() or the Tuya IoT platform ("Device Debugging" -> DP instruction set).

TO TALK TO A DEVICE LOCALLY YOU NEED 4 THINGS
---------------------------------------------
    id         device id                       (from the wizard / app)
    ip         LAN address                     (from scan / router)
    local_key  16-char AES key                 (ONLY obtainable via Tuya cloud -> wizard)
    version    protocol 3.1/3.3/3.4/3.5        (from scan; wrong version = garbage/timeouts)

Get them all with:   uv run python -m tinytuya wizard
That asks for your Tuya IoT Cloud API key/secret, then writes devices.json.
This app reads only devices.json and never talks to the cloud; the wizard
(and its tinytuya.json creds) is only needed again if you re-pair a device.
NOTE: the local_key changes every time you re-pair the device in the app.

GOTCHAS
-------
* TinyTuya does NOT raise on device errors. It returns a dict like
  {"Error": "Network Error: Device Unreachable", "Err": "905", "Payload": None}.
  Always check for "Error" (see `check()` below).
* It's all blocking sockets; never call it directly from `async def` code.
* Most devices accept only ONE local TCP connection at a time. If the Smart
  Life app or another script is connected, you'll get timeouts.
* Power readings are cached by many devices; `updatedps([18, 19, 20])` asks
  the device to refresh them before you read.
* Scanning uses UDP broadcast (ports 6666/6667/7000). Under WSL2 NAT you won't
  see broadcasts from your LAN — run the scan on Windows/Linux host or use
  WSL mirrored networking.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import tinytuya

DEVICES_FILE = Path(os.getenv("TUYA_DEVICES_FILE", "devices.json"))


class TuyaError(Exception):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("Error"))


def check(result: Any) -> Any:
    """Turn tinytuya's error-dicts into exceptions."""
    if isinstance(result, dict) and "Error" in result:
        raise TuyaError(result)
    return result


@lru_cache
def load_devices() -> dict[str, dict]:
    """devices.json (from `python -m tinytuya wizard`) indexed by device id."""
    if not DEVICES_FILE.exists():
        return {}
    return {device["id"]: device for device in json.loads(DEVICES_FILE.read_text())}


def get_device(device_id: str, persist: bool = False) -> tinytuya.OutletDevice:
    """
    Build a device handle. Constructing it doesn't open a connection; the
    socket is opened lazily on the first command.

    Device classes:
      tinytuya.Device         generic, works for anything
      tinytuya.OutletDevice   plugs/switches (turn_on/turn_off helpers)
      tinytuya.BulbDevice     bulbs (set_colour, set_brightness_percentage, ...)
      tinytuya.CoverDevice    curtains (open_cover, close_cover)
    """
    info = load_devices().get(device_id)
    if info is None:
        raise KeyError(device_id)

    if not info.get("ip"):
        # tinytuya would fall back to address="Auto" (UDP broadcast scan), which
        # never sees the LAN under WSL2 and just hangs. Fail loudly instead.
        # Find it with: python -m tinytuya scan -force 192.168.x.0/24
        raise TuyaError({"Error": "No ip in devices.json", "Err": "no-ip", "Payload": None})

    device = tinytuya.OutletDevice(
        dev_id=device_id,  # tinytuya's parameter name
        address=info["ip"],
        local_key=info["key"],
        version=float(info.get("version") or 3.3),
        persist=persist,  # keep the TCP socket open between calls
        connection_timeout=3,
        connection_retry_limit=2,
        connection_retry_delay=1,
    )
    return device


def parse_dps(dps: dict, mapping: dict[str, tuple[str, float]]) -> dict:
    """Pick the DPS listed in `mapping` ({index: (name, divisor)}) and scale them."""
    return {name: dps[index] / divisor for index, (name, divisor) in mapping.items() if index in dps}
