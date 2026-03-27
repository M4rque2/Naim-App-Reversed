#!/usr/bin/env python3
"""
Naim Device Emulator
Emulates legacy Naim audio devices for testing client tools without real hardware.

Implements:
  - n-Stream/BridgeCo protocol  (TCP port 15555)
  - UPnP/DLNA                   (HTTP port 8080)
  - SSDP responder              (UDP 239.255.255.250:1900)

Usage:
  ./naim_emulator.py --model superuniti
  ./naim_emulator.py --model superuniti --verbose
  ./naim_emulator.py --model superuniti --debug      # raw-bytes + full XML dump
  ./naim_emulator.py --profile device_profiles/superuniti.json

Then test with the CLI clients:
  ./naim_control_nstream.py --host 127.0.0.1 inputs
  ./naim_control_upnp.py    --host 127.0.0.1 info
"""

import argparse
import base64
import datetime
import json
import re
import shlex
import socket
import socketserver
import struct
import sys
import threading
import traceback
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


NSTREAM_DEFAULT_PORT = 15555
UPNP_DEFAULT_PORT    = 8080
SSDP_MCAST_ADDR      = "239.255.255.250"
SSDP_PORT            = 1900
SSDP_TTL             = 1800   # seconds — how long app caches the device
SSDP_ALIVE_INTERVAL  = 60     # seconds between periodic ssdp:alive broadcasts
MAX_BUF_WARN         = 64 * 1024  # warn if n-Stream buffer exceeds 64 KB


# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    """Short timestamp for log lines."""
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _hexdump(data: bytes, prefix: str = "") -> str:
    """
    Format bytes as printable text with non-printable chars shown as <XX>.
    Groups of 16 bytes per line, with the prefix on the first line.
    """
    lines = []
    for i in range(0, len(data), 64):
        chunk = data[i:i + 64]
        parts = []
        for b in chunk:
            if 0x20 <= b < 0x7F:
                parts.append(chr(b))
            else:
                parts.append(f"<{b:02x}>")
        lines.append(f"{prefix}{''.join(parts)}")
        prefix = " " * len(prefix)  # indent continuation lines
    return "\n".join(lines) if lines else f"{prefix}(empty)"


def _get_local_ip() -> str:
    """Best-effort: return the machine's outbound LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
# Built-in device profiles (also loadable from device_profiles/*.json)
# ─────────────────────────────────────────────────────────────────────────────

_BUILTIN_SUPERUNITI = {
    "model": "SuperUniti",
    "product_code": "SUPER_UNITI",
    "firmware_version": "3.21.000 14171",
    "mac": ["00", "1A", "D0", "AB", "CD", "EF"],
    "room_name": "Living Room",
    "friendly_name": "Naim SuperUniti",
    "serial": "EMU000001",
    "initial_volume": 30,
    "max_amp_volume": 100,
    "max_head_volume": 75,
    "initial_input": "UPNP",
    "auto_standby_period": 20,
    "unsupported_nvm": [
        "GETILLUM", "SETILLUM",
        "GETAUTOSTANDBYPERIOD", "SETAUTOSTANDBYPERIOD",
        "GETSERIALNUM",
    ],
    "inputs": [
        {"id": "FM",        "name": "FM",         "enabled": False},
        {"id": "DAB",       "name": "DAB",        "enabled": False},
        {"id": "IRADIO",    "name": "iRadio",     "enabled": True},
        {"id": "MULTIROOM", "name": "Multiroom",  "enabled": False},
        {"id": "UPNP",      "name": "UPnP",       "enabled": True},
        {"id": "BLUETOOTH", "name": "Bluetooth",  "enabled": True},
        {"id": "SPOTIFY",   "name": "Spotify",    "enabled": True},
        {"id": "TIDAL",     "name": "Tidal",      "enabled": True},
        {"id": "AIRPLAY",   "name": "AirPlay",    "enabled": True},
        {"id": "DIGITAL1",  "name": "Digital 1",  "enabled": True},
        {"id": "DIGITAL2",  "name": "Digital 2",  "enabled": True},
        {"id": "DIGITAL3",  "name": "Digital 3",  "enabled": True},
        {"id": "DIGITAL4",  "name": "Digital 4",  "enabled": False},
        {"id": "ANALOGUE1", "name": "Analogue 1", "enabled": True},
        {"id": "ANALOGUE2", "name": "Analogue 2", "enabled": True},
        {"id": "PHONO",     "name": "Phono",      "enabled": False},
        {"id": "USB",       "name": "USB",        "enabled": True},
        {"id": "FRONT",     "name": "Front",      "enabled": False},
    ],
}

BUILTIN_PROFILES = {
    "superuniti": _BUILTIN_SUPERUNITI,
    "super_uniti": _BUILTIN_SUPERUNITI,
}


# ─────────────────────────────────────────────────────────────────────────────
# Device State
# ─────────────────────────────────────────────────────────────────────────────

class DeviceState:
    """Thread-safe in-memory state for the emulated device."""

    def __init__(self, profile):
        self._lock = threading.Lock()

        self.product_code       = profile["product_code"]
        self.firmware_version   = profile.get("firmware_version", "1.0.0 0")
        self.mac                = profile.get("mac", ["00", "11", "22", "33", "44", "55"])
        self.room_name          = profile.get("room_name", "Emulated Naim")
        self.serial             = profile.get("serial", "EMU000001")
        self.friendly_name      = profile.get("friendly_name", profile.get("model", "Naim"))

        self.volume             = profile.get("initial_volume", 30)
        self.muted              = False
        self.balance            = 0
        self.max_amp_volume     = profile.get("max_amp_volume", 100)
        self.max_head_volume    = profile.get("max_head_volume", 75)
        self.standby            = False
        self.auto_standby_mins  = profile.get("auto_standby_period", 20)
        self.illumination       = 3

        # Bluetooth
        self.bt_name            = profile.get("model", "Naim")
        self.bt_status          = "INACTIVE"
        self.bt_security        = "OPEN"
        self.bt_auto_reconnect  = "OFF"
        self.bt_auto_play       = "OFF"

        # Playback
        self.transport_state    = "STOPPED"
        self.repeat             = "OFF"
        self.random_mode        = "OFF"

        # Commands that return error 4 on this device model
        self.unsupported_nvm = set(
            c.upper() for c in profile.get("unsupported_nvm", [])
        )

        # Inputs: ordered list and lookup dict
        self._input_order: list[str] = []
        self._inputs: dict[str, dict] = {}
        for inp in profile.get("inputs", []):
            iid = inp["id"]
            self._input_order.append(iid)
            self._inputs[iid] = {
                "name":    inp["name"],
                "enabled": bool(inp.get("enabled", True)),
                "trim":    int(inp.get("trim", 0)),
            }

        self.current_input = profile.get(
            "initial_input",
            self._input_order[0] if self._input_order else "UPNP"
        )

    # ── Inputs ──────────────────────────────────────────────────────────────

    def get_inputs_snapshot(self):
        """Returns (ordered_ids, inputs_dict) — copies, not references."""
        with self._lock:
            return list(self._input_order), {k: dict(v) for k, v in self._inputs.items()}

    def get_current_input(self):
        with self._lock:
            return self.current_input

    def set_input(self, input_id):
        with self._lock:
            if input_id in self._inputs:
                self.current_input = input_id
                return True
            return False

    def cycle_input(self, direction=1):
        """Cycle to next (+1) or previous (-1) enabled input."""
        with self._lock:
            enabled = [i for i in self._input_order if self._inputs[i]["enabled"]]
            if not enabled:
                return self.current_input
            try:
                idx = enabled.index(self.current_input)
            except ValueError:
                idx = 0
            idx = (idx + direction) % len(enabled)
            self.current_input = enabled[idx]
            return self.current_input

    def set_input_enabled(self, input_id, enabled):
        with self._lock:
            if input_id not in self._inputs:
                return False
            self._inputs[input_id]["enabled"] = enabled
            return True

    def get_input_enabled(self, input_id):
        with self._lock:
            if input_id not in self._inputs:
                return None
            return self._inputs[input_id]["enabled"]

    def set_input_name(self, input_id, name):
        with self._lock:
            if input_id not in self._inputs:
                return False
            self._inputs[input_id]["name"] = name
            return True

    def get_input_name(self, input_id):
        with self._lock:
            if input_id not in self._inputs:
                return None
            return self._inputs[input_id]["name"]

    def get_input_trim(self, input_id):
        with self._lock:
            if input_id not in self._inputs:
                return None
            return self._inputs[input_id]["trim"]

    def set_input_trim(self, input_id, level):
        with self._lock:
            if input_id not in self._inputs:
                return False
            self._inputs[input_id]["trim"] = level
            return True

    # ── Volume / preamp ─────────────────────────────────────────────────────

    def volume_up(self):
        with self._lock:
            self.volume = min(self.max_amp_volume, self.volume + 1)
            return self.volume

    def volume_down(self):
        with self._lock:
            self.volume = max(0, self.volume - 1)
            return self.volume

    def set_volume(self, level):
        with self._lock:
            self.volume = max(0, min(100, level))

    def set_muted(self, muted):
        with self._lock:
            self.muted = muted

    def set_balance(self, level):
        with self._lock:
            self.balance = level

    def get_preamp_snapshot(self):
        with self._lock:
            name = self._inputs.get(self.current_input, {}).get("name", self.current_input)
            return {
                "volume":  self.volume,
                "mute":    1 if self.muted else 0,
                "balance": self.balance,
                "input":   self.current_input,
                "name":    name,
            }

    # ── Power ────────────────────────────────────────────────────────────────

    def set_standby(self, on):
        with self._lock:
            self.standby = on

    def get_standby(self):
        with self._lock:
            return self.standby

    # ── UPnP helpers ─────────────────────────────────────────────────────────

    def get_volume_snapshot(self):
        with self._lock:
            return self.volume, self.muted

    def set_transport_state(self, state):
        with self._lock:
            self.transport_state = state

    def get_transport_state(self):
        with self._lock:
            return self.transport_state


# ─────────────────────────────────────────────────────────────────────────────
# NVM Command Handler
# ─────────────────────────────────────────────────────────────────────────────

def handle_nvm_command(state: DeviceState, raw: str) -> list[str]:
    """
    Parse one NVM command string and execute it against ``state``.

    Accepts both ``*NVM CMD`` (unicast) and ``**NVM CMD`` (broadcast/multicast)
    prefixes — the Naim app sends ``**NVM GETSEDMPCAPS`` with a double asterisk.

    Returns a list of NVM response body strings (the part after ``#NVM ``).
    The caller prepends ``#NVM `` and adds ``\\r\\n``.

    Error responses use the format ``ERROR: CMD CODE`` (with colon) so that
    TunnelQueue.handleTunnelRow's ``"ERROR:"`` check unblocks the conversation
    immediately instead of waiting for a 20-second timeout.
    """
    raw = raw.strip().rstrip("\r")

    # Strip optional extra leading * (app sends **NVM for some broadcast commands)
    if raw.startswith("**NVM "):
        raw = raw[1:]

    if not raw.startswith("*NVM "):
        return ["ERROR: UNKNOWN 4"]

    rest = raw[5:].strip()

    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split()

    if not parts:
        return ["ERROR: UNKNOWN 4"]

    cmd  = parts[0].upper()
    args = parts[1:]

    # Check device-model capability
    if cmd in state.unsupported_nvm:
        return [f"ERROR: {cmd} 4"]

    # ── System ───────────────────────────────────────────────────────────────
    if cmd == "SETUNSOLICITED":
        return ["SETUNSOLICITED OK"]

    if cmd == "SYNCDISP":
        return ["SYNCDISP OK"]

    if cmd == "DEBUG":
        return ["DEBUG OK"]

    if cmd == "GETCHGTRACKERS":
        return ["GETCHGTRACKERS 0"]

    # ── Inputs ────────────────────────────────────────────────────────────────
    if cmd == "GETINPUTBLK":
        order, inputs = state.get_inputs_snapshot()
        total = len(order)
        responses = []
        for i, iid in enumerate(order, 1):
            active = 1 if inputs[iid]["enabled"] else 0
            name   = inputs[iid]["name"]
            responses.append(f'GETINPUTBLK {i} {total} {active} {iid} "{name}"')
        return responses

    if cmd == "GETINPUT":
        return [f"GETINPUT {state.get_current_input()}"]

    if cmd == "SETINPUT":
        if not args:
            return ["ERROR: SETINPUT 401"]
        iid = args[0].upper()
        if state.set_input(iid):
            return ["SETINPUT OK"]
        return ["ERROR: SETINPUT 403"]

    if cmd == "INPUT+":
        return [f"INPUT+ {state.cycle_input(+1)}"]

    if cmd == "INPUT-":
        return [f"INPUT- {state.cycle_input(-1)}"]

    if cmd == "SETINPUTENABLED":
        if len(args) < 2:
            return ["ERROR: SETINPUTENABLED 401"]
        iid     = args[0].upper()
        enabled = args[1].upper() == "ON"
        if state.set_input_enabled(iid, enabled):
            return ["SETINPUTENABLED OK"]
        return ["ERROR: SETINPUTENABLED 403"]

    if cmd == "GETINPUTENABLED":
        if not args:
            return ["ERROR: GETINPUTENABLED 401"]
        iid = args[0].upper()
        val = state.get_input_enabled(iid)
        if val is None:
            return ["ERROR: GETINPUTENABLED 403"]
        return [f"GETINPUTENABLED {iid} {'ON' if val else 'OFF'}"]

    if cmd == "SETINPUTNAME":
        if len(args) < 2:
            return ["ERROR: SETINPUTNAME 401"]
        iid  = args[0].upper()
        name = args[1]  # shlex already unquoted this
        if state.set_input_name(iid, name):
            return ["SETINPUTNAME OK"]
        return ["ERROR: SETINPUTNAME 403"]

    if cmd == "GETINPUTNAME":
        if not args:
            return ["ERROR: GETINPUTNAME 401"]
        iid  = args[0].upper()
        name = state.get_input_name(iid)
        if name is None:
            return ["ERROR: GETINPUTNAME 403"]
        return [f'GETINPUTNAME {iid} "{name}"']

    if cmd == "GETINPUTTRIM":
        if not args:
            return ["ERROR: GETINPUTTRIM 401"]
        iid  = args[0].upper()
        trim = state.get_input_trim(iid)
        if trim is None:
            return ["ERROR: GETINPUTTRIM 403"]
        return [f"GETINPUTTRIM {iid} {trim}"]

    if cmd == "SETINPUTTRIM":
        if len(args) < 2:
            return ["ERROR: SETINPUTTRIM 401"]
        iid = args[0].upper()
        try:
            level = int(args[1])
        except ValueError:
            return ["ERROR: SETINPUTTRIM 401"]
        if state.set_input_trim(iid, level):
            return ["SETINPUTTRIM OK"]
        return ["ERROR: SETINPUTTRIM 403"]

    # ── Volume / preamp ───────────────────────────────────────────────────────
    if cmd == "VOL+":
        return [f"VOL+ {state.volume_up()}"]

    if cmd == "VOL-":
        return [f"VOL- {state.volume_down()}"]

    if cmd == "SETRVOL":
        if not args:
            return ["ERROR: SETRVOL 401"]
        try:
            state.set_volume(int(args[0]))
        except ValueError:
            return ["ERROR: SETRVOL 401"]
        return ["SETRVOL OK"]

    if cmd == "SETMUTE":
        if not args:
            return ["ERROR: SETMUTE 401"]
        state.set_muted(args[0].upper() == "ON")
        return ["SETMUTE OK"]

    if cmd == "GETPREAMP":
        p = state.get_preamp_snapshot()
        # Format: PREAMP vol mute balance input_id a5 a6 a7 a8 "input_name" a10
        return [
            f'PREAMP {p["volume"]} {p["mute"]} {p["balance"]} {p["input"]}'
            f' 0 0 0 0 "{p["name"]}" 0'
        ]

    if cmd == "GETAMPMAXVOL":
        with state._lock:
            return [f"GETAMPMAXVOL {state.max_amp_volume}"]

    if cmd == "SETAMPMAXVOL":
        if not args:
            return ["ERROR: SETAMPMAXVOL 401"]
        try:
            level = max(0, min(100, int(args[0])))
        except ValueError:
            return ["ERROR: SETAMPMAXVOL 401"]
        with state._lock:
            state.max_amp_volume = level
        return ["SETAMPMAXVOL OK"]

    if cmd == "GETHEADMAXVOL":
        with state._lock:
            return [f"GETHEADMAXVOL {state.max_head_volume}"]

    if cmd == "GETBAL":
        with state._lock:
            return [f"GETBAL {state.balance}"]

    if cmd == "SETBAL":
        if not args:
            return ["ERROR: SETBAL 401"]
        try:
            state.set_balance(int(args[0]))
        except ValueError:
            return ["ERROR: SETBAL 401"]
        return ["SETBAL OK"]

    # ── Standby / power ───────────────────────────────────────────────────────
    if cmd == "SETSTANDBY":
        if not args:
            return ["ERROR: SETSTANDBY 401"]
        state.set_standby(args[0].upper() == "ON")
        return ["SETSTANDBY OK"]

    if cmd == "GETSTANDBYSTATUS":
        return [f"GETSTANDBYSTATUS {'ON' if state.get_standby() else 'OFF'}"]

    # GETILLUM / SETILLUM / GETAUTOSTANDBYPERIOD / SETAUTOSTANDBYPERIOD handled
    # above by the unsupported_nvm check for SuperUniti.
    # For devices that DO support them:
    if cmd == "GETILLUM":
        with state._lock:
            return [f"GETILLUM {state.illumination}"]

    if cmd == "SETILLUM":
        if not args:
            return ["ERROR: SETILLUM 401"]
        try:
            with state._lock:
                state.illumination = int(args[0])
        except ValueError:
            return ["ERROR: SETILLUM 401"]
        return ["SETILLUM OK"]

    if cmd == "GETAUTOSTANDBYPERIOD":
        with state._lock:
            return [f"GETAUTOSTANDBYPERIOD {state.auto_standby_mins}"]

    if cmd == "SETAUTOSTANDBYPERIOD":
        if not args:
            return ["ERROR: SETAUTOSTANDBYPERIOD 401"]
        try:
            with state._lock:
                state.auto_standby_mins = int(args[0])
        except ValueError:
            return ["ERROR: SETAUTOSTANDBYPERIOD 401"]
        return ["SETAUTOSTANDBYPERIOD OK"]

    # ── Room / device name ────────────────────────────────────────────────────
    if cmd == "GETROOMNAME":
        with state._lock:
            return [f'GETROOMNAME "{state.room_name}"']

    if cmd == "SETROOMNAME":
        if not args:
            return ["ERROR: SETROOMNAME 401"]
        with state._lock:
            state.room_name = args[0]
        return ["SETROOMNAME OK"]

    # ── Device info ───────────────────────────────────────────────────────────
    if cmd == "PRODUCT":
        return [f"PRODUCT {state.product_code}"]

    if cmd == "VERSION":
        with state._lock:
            return [f"VERSION {state.firmware_version}"]

    if cmd == "GETMAC":
        with state._lock:
            return [f"GETMAC {' '.join(state.mac)}"]

    if cmd == "GETSERIALNUM":
        # Handled by unsupported_nvm for SuperUniti; here for other models:
        with state._lock:
            return [f"GETSERIALNUM {state.serial}"]

    if cmd == "GETSEDMPTYPE":
        # Hardware platform type — SEDMP2D = SuperUniti/NDX/NDS class (stream platform 2nd gen)
        return ["GETSEDMPTYPE SEDMP2D"]

    if cmd == "GETSEDMPCAPS":
        # DMP capability flags. +PC/+PM = multiroom client/master, +SM = Spotify multiroom, +VS = vol scaling
        return ["GETSEDMPCAPS +PC +PM +SM +VS"]

    if cmd == "GETLANG":
        return ["GETLANG 0"]

    if cmd == "GETBSLVER":
        return ["GETBSLVER 1.0"]

    # ── Playback ──────────────────────────────────────────────────────────────
    if cmd == "PLAY":
        state.set_transport_state("PLAYING")
        return ["PLAY OK"]

    if cmd == "STOP":
        state.set_transport_state("STOPPED")
        return ["STOP OK"]

    if cmd == "PAUSE":
        arg = args[0].upper() if args else "TOGGLE"
        with state._lock:
            if arg == "TOGGLE":
                state.transport_state = (
                    "PAUSED" if state.transport_state == "PLAYING" else "PLAYING"
                )
            elif arg == "ON":
                state.transport_state = "PAUSED"
            elif arg == "OFF":
                state.transport_state = "PLAYING"
        return ["PAUSE OK"]

    if cmd == "NEXTTRACK":
        return ["NEXTTRACK OK"]

    if cmd == "PREVTRACK":
        return ["PREVTRACK OK"]

    if cmd == "FF":
        return ["FF OK"]

    if cmd == "FR":
        return ["FR OK"]

    if cmd == "REPEAT":
        with state._lock:
            state.repeat = args[0].upper() if args else "OFF"
        return ["REPEAT OK"]

    if cmd == "RANDOM":
        with state._lock:
            state.random_mode = args[0].upper() if args else "OFF"
        return ["RANDOM OK"]

    # ── Bluetooth ─────────────────────────────────────────────────────────────
    if cmd == "BTSTATUS":
        with state._lock:
            return [f"BTSTATUS {state.bt_status}"]

    if cmd == "BTPAIR":
        return ["BTPAIR OK"]

    if cmd == "BTDROPLINK":
        return ["BTDROPLINK OK"]

    if cmd == "BTRECONNECT":
        return ["BTRECONNECT OK"]

    if cmd == "BTRESET":
        return ["BTRESET OK"]

    if cmd == "GETBTNAME":
        with state._lock:
            return [f'GETBTNAME "{state.bt_name}"']

    if cmd == "SETBTNAME":
        if not args:
            return ["ERROR: SETBTNAME 401"]
        with state._lock:
            state.bt_name = args[0]
        return ["SETBTNAME OK"]

    if cmd == "GETBTSECURITY":
        with state._lock:
            return [f"GETBTSECURITY {state.bt_security}"]

    if cmd == "SETBTSECURITY":
        if not args:
            return ["ERROR: SETBTSECURITY 401"]
        with state._lock:
            state.bt_security = args[0].upper()
        return ["SETBTSECURITY OK"]

    if cmd == "GETBTAUTORECONNECT":
        with state._lock:
            return [f"GETBTAUTORECONNECT {state.bt_auto_reconnect}"]

    if cmd == "SETBTAUTORECONNECT":
        if not args:
            return ["ERROR: SETBTAUTORECONNECT 401"]
        with state._lock:
            state.bt_auto_reconnect = args[0].upper()
        return ["SETBTAUTORECONNECT OK"]

    if cmd == "GETBTAUTOPLAY":
        with state._lock:
            return [f"GETBTAUTOPLAY {state.bt_auto_play}"]

    if cmd == "SETBTAUTOPLAY":
        if not args:
            return ["ERROR: SETBTAUTOPLAY 401"]
        with state._lock:
            state.bt_auto_play = args[0].upper()
        return ["SETBTAUTOPLAY OK"]

    # ── Presets ───────────────────────────────────────────────────────────────
    if cmd == "GETTOTALPRESETS":
        return ["GETTOTALPRESETS 0"]

    if cmd in ("GETPRESET", "SETPRESET", "GETPRESETBLK", "RENAMEPRESET",
               "CLEARPRESET", "GOTOPRESET", "PRESET+", "PRESET-"):
        return [f"ERROR: {cmd} 4"]

    # ── Alarm / radio stubs ───────────────────────────────────────────────────
    if cmd in ("GETALARMSTATE", "GETALARMACTIVE", "ALARMSTATE"):
        return ["ALARMSTATE 0"]

    if cmd == "GETIRADIOHIDDENROWS":
        # No iRadio categories are hidden
        return ["GETIRADIOHIDDENROWS 0"]

    if cmd == "GETBLASTCAPS":
        # IR blaster capabilities: 6 space-sep tokens
        # [1]=preamp_automation [2]=DAC_auto [3]=CD_auto [4]=CDinput [5]=NDXinput
        # "0" values mean those automations are disabled
        return ["GETBLASTCAPS 0 0 0 0 0 0"]

    if cmd == "GETDATETIME":
        import datetime as _dt
        now = _dt.datetime.now()
        return [f"GETDATETIME {now.year} {now.month:02d} {now.day:02d} "
                f"{now.hour:02d} {now.minute:02d} {now.second:02d}"]

    if cmd == "GETINITIALINFO":
        # Combined startup info — the app parses this instead of individual queries
        # Field layout (space-separated tokens after GETINITIALINFO command):
        #   [1] Y|N           — alarmFeatureSupported
        #   [2] <rows>        — iRadio hidden rows (underscore-separated list)
        #   [3] <int>         — language index (0 = English)
        #   [4] <product>     — product type string
        #   [5] t0_t1_t2_t3   — change trackers: preamp_dab_presets_playqueue (4 required)
        #   [6] <input>       — current input identifier
        #   [7] <ircaps>      — IR capabilities; "NA" = unknown/none (skips automation setup)
        #   [8] <ignored>     — unused field
        #   [9] <int>         — max multiroom clients
        with state._lock:
            current = state.current_input
            product = state.product_code
        return [f"GETINITIALINFO Y 0 0 {product} 0_0_0_0 {current} NA 0 0"]


# ─────────────────────────────────────────────────────────────────────────────
# n-Stream TCP Server
# ─────────────────────────────────────────────────────────────────────────────

class NStreamHandler(socketserver.BaseRequestHandler):
    """Handles one TCP connection on the n-Stream/BridgeCo port (15555)."""

    def setup(self):
        self._buf    = b""
        self._state: DeviceState = self.server.device_state
        self._verbose: bool      = self.server.verbose
        self._debug: bool        = self.server.debug
        self._peer               = self.client_address[0]
        self._conn_id            = id(self) & 0xFFFF  # short unique ID for log correlation

    def handle(self):
        print(f"[{_ts()}] [nStream] #{self._conn_id:04x} CONNECT  {self._peer}")
        try:
            self._run()
        except Exception as exc:
            # Catch any unexpected exception so it is always logged (not silently dropped)
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} EXCEPTION {type(exc).__name__}: {exc}")
            if self._verbose:
                traceback.print_exc()
        finally:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} CLOSE    {self._peer}  "
                  f"(buf remaining: {len(self._buf)} bytes)")
            if self._debug and self._buf:
                print(_hexdump(self._buf, f"[{_ts()}] [nStream] #{self._conn_id:04x} BUF_LEFT "))

    def _run(self):
        while True:
            try:
                chunk = self.request.recv(4096)
            except socket.error as exc:
                if self._verbose:
                    print(f"[{_ts()}] [nStream] #{self._conn_id:04x} recv error: {exc}")
                break
            if not chunk:
                if self._debug:
                    print(f"[{_ts()}] [nStream] #{self._conn_id:04x} EOF (peer closed connection)")
                break

            if self._debug:
                print(_hexdump(chunk, f"[{_ts()}] [nStream] #{self._conn_id:04x} RAW<<  "))

            self._buf += chunk

            # Warn if buffer is growing uncontrolled (malformed XML from app)
            if len(self._buf) > MAX_BUF_WARN:
                print(f"[{_ts()}] [nStream] #{self._conn_id:04x} WARNING: buffer={len(self._buf)} bytes "
                      f"without complete command — possible framing problem")
                if self._debug:
                    print(_hexdump(self._buf[:256],
                                   f"[{_ts()}] [nStream] #{self._conn_id:04x} BUF_HEAD "))

            self._drain_commands()

    def _drain_commands(self):
        """Extract and process all complete XML commands from the buffer."""
        while True:
            xml_str = self._extract_command()
            if xml_str is None:
                break
            self._dispatch(xml_str)

    def _extract_command(self):
        """
        Pull the next complete XML command from ``self._buf``.

        Handles two forms:
          - Regular:     <command>...</command>
          - Self-closing: <command id="0" name="Disconnect"/>

        Always processes whichever complete command appears *earliest* in the
        buffer (fixes the ordering bug where re.search() could find a later
        self-closing Disconnect before an earlier regular command).
        """
        text = self._buf.decode("utf-8", errors="replace")

        # Position where a regular command ends
        end_tag = text.find("</command>")
        reg_end = (end_tag + len("</command>")) if end_tag >= 0 else -1

        # Position where a self-closing command starts
        sc_match = re.search(r"<command\b[^>]*/\s*>", text)
        sc_start = sc_match.start() if sc_match else -1

        # No complete command available yet
        if reg_end < 0 and sc_start < 0:
            return None

        # Choose whichever comes first in the stream
        take_sc = (
            sc_start >= 0 and
            (reg_end < 0 or sc_start < reg_end)
        )

        if take_sc:
            cmd = sc_match.group()
            self._buf = text[sc_match.end():].encode("utf-8")
        else:
            cmd = text[:reg_end]
            self._buf = text[reg_end:].encode("utf-8")

        return cmd

    def _dispatch(self, xml_str: str):
        if self._verbose:
            snippet = xml_str[:120].replace("\n", " ")
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} XML<<  "
                  f"{snippet}{'...' if len(xml_str) > 120 else ''}")
        elif self._debug:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} XML<<  {xml_str}")

        # Disconnect (self-closing)
        if re.search(r'name=["\']Disconnect["\']', xml_str) or (
            "Disconnect" in xml_str and "/>" in xml_str
        ):
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} DISCONNECT signal received")
            return

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as exc:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} XML PARSE ERROR: {exc}")
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} FAILED XML: {xml_str!r}")
            return

        name_el  = root.find("name")
        id_el    = root.find("id")
        cmd_name = name_el.text.strip() if name_el is not None and name_el.text else ""
        msg_id   = id_el.text.strip()   if id_el   is not None and id_el.text   else "0"

        if cmd_name == "RequestAPIVersion":
            self._reply_api_version(msg_id)
        elif cmd_name == "GetBridgeCoAppVersions":
            # BC startup step 2: app needs appVersion/bslVersion/cneVersion
            self._reply_get_bridgeco_versions(msg_id)
        elif cmd_name == "SetHeartbeatTimeout":
            # BC startup step 3: just ack — fires BC_SETHEARTBEATTIMEOUT → bcStartSequenceEnded(true)
            self._reply_bc_ack("SetHeartbeatTimeout", msg_id)
        elif cmd_name == "Ping":
            # Keepalive every 5 s — must reply or the app times out and reconnects
            self._reply_bc_ack("Ping", msg_id)
        elif cmd_name == "TunnelToHost":
            self._handle_tunnel(root, msg_id)
        else:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} UNKNOWN BC cmd: {cmd_name!r}  "
                  f"(full XML: {xml_str!r})")

    def _reply_api_version(self, msg_id: str):
        """Acknowledge the API version negotiation.

        VisitorBCMessage reads commandName and messageID from ATTRIBUTES on <reply>,
        not from child elements. Format must be: <reply name="..." id="...">
        """
        reply = (
            f'<reply name="RequestAPIVersion" id="{msg_id}">'
            f'<map>'
            f'<item><name>result</name><string>OK</string></item>'
            f'<item><name>version</name><string>1</string></item>'
            f'</map>'
            f'</reply>'
        )
        self._send(reply)
        if self._verbose:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} >> RequestAPIVersion OK (id={msg_id})")

    def _reply_get_bridgeco_versions(self, msg_id: str):
        """Reply to GetBridgeCoAppVersions with legacy-device version numbers.

        VisitorBCGetBridgeCoAppVersions reads version info from <item name="app" string="X..."/>
        attributes inside <map>. The visitor strips the first character of the string
        attribute value (e.g. "01000" → "1000"), then parseInt → 1000 < 10000 triggers
        the legacy device path: sends SetHeartbeatTimeout and sets playlistsSupported=true.
        """
        reply = (
            f'<reply name="GetBridgeCoAppVersions" id="{msg_id}">'
            f'<map>'
            f'<item name="app" string="01000"/>'
            f'<item name="bsl" string="01000"/>'
            f'<item name="cne" string="01000"/>'
            f'</map>'
            f'</reply>'
        )
        self._send(reply)
        if self._verbose:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} >> GetBridgeCoAppVersions (app=1000, bsl=1000, cne=1000) (id={msg_id})")

    def _reply_bc_ack(self, cmd_name: str, msg_id: str):
        """Send a minimal <reply name="..." id="..."> acknowledgement.

        Used for SetHeartbeatTimeout and Ping — these have no VisitorBC* class
        so only the name+id attributes are needed for the BCQueue to unblock.
        """
        reply = f'<reply name="{cmd_name}" id="{msg_id}"></reply>'
        self._send(reply)
        if self._verbose:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} >> {cmd_name} ack (id={msg_id})")

    def _handle_tunnel(self, root: ET.Element, msg_id: str):
        """Decode a TunnelToHost NVM command, execute it, send TunnelFromHost reply."""
        b64_el = root.find(".//base64")
        if b64_el is None or not b64_el.text:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} TunnelToHost missing base64 payload")
            return

        try:
            nvm_raw = base64.b64decode(b64_el.text.strip()).decode("utf-8", errors="replace")
        except Exception as exc:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} base64 decode error: {exc}")
            return

        nvm_raw = nvm_raw.strip()
        if self._verbose:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} NVM<< {nvm_raw!r}")

        responses = handle_nvm_command(self._state, nvm_raw)

        for resp in responses:
            line = f"#NVM {resp}\r\n"
            if self._verbose:
                print(f"[{_ts()}] [nStream] #{self._conn_id:04x} NVM>> {line.strip()!r}")
            self._send_tunnel_reply(msg_id, line)

    def _send_tunnel_reply(self, msg_id: str, data: str):
        """Send a TunnelFromHost <reply> with base64-encoded NVM response.

        The reply tag uses name and id as ATTRIBUTES (required by VisitorBCMessage
        which only reads attributes, not child elements, for commandName/messageID).
        VisitorBCTunnelFromHost then does a second pass and reads the <base64> child.
        """
        b64 = base64.b64encode(data.encode("utf-8")).decode("ascii")
        xml = (
            f'<reply name="TunnelFromHost" id="{msg_id}">'
            f'<map><item><name>data</name><base64>{b64}</base64></item></map>'
            f'</reply>'
        )
        self._send(xml)

    def _send(self, xml: str):
        if self._debug:
            print(_hexdump(xml.encode("utf-8"),
                           f"[{_ts()}] [nStream] #{self._conn_id:04x} RAW>>  "))
        try:
            self.request.sendall(xml.encode("utf-8"))
        except socket.error as exc:
            print(f"[{_ts()}] [nStream] #{self._conn_id:04x} SEND ERROR: {exc}")


class NStreamServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads      = True

    def __init__(self, host, port, device_state: DeviceState, verbose: bool, debug: bool):
        self.device_state = device_state
        self.verbose      = verbose
        self.debug        = debug
        super().__init__((host, port), NStreamHandler)


# ─────────────────────────────────────────────────────────────────────────────
# UPnP / DLNA HTTP Server
# ─────────────────────────────────────────────────────────────────────────────

_SUPPORTED_PROTOCOLS = ",".join([
    "http-get:*:audio/flac:*",
    "http-get:*:audio/mpeg:*",
    "http-get:*:audio/x-wav:*",
    "http-get:*:audio/x-aiff:*",
    "http-get:*:audio/ogg:*",
    "http-get:*:audio/aac:*",
])


def _build_description_xml(state: DeviceState, port: int) -> str:
    udn = f"uuid:emulated-naim-{state.product_code.lower()}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>{state.friendly_name}</friendlyName>
    <manufacturer>Naim Audio Ltd.</manufacturer>
    <modelName>{state.product_code}</modelName>
    <serialNumber>{state.serial}</serialNumber>
    <UDN>{udn}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
        <SCPDURL>/AVTransport/scpd.xml</SCPDURL>
        <controlURL>/AVTransport/ctrl</controlURL>
        <eventSubURL>/AVTransport/evt</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
        <SCPDURL>/RenderingControl/scpd.xml</SCPDURL>
        <controlURL>/RenderingControl/ctrl</controlURL>
        <eventSubURL>/RenderingControl/evt</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/ConnectionManager/scpd.xml</SCPDURL>
        <controlURL>/ConnectionManager/ctrl</controlURL>
        <eventSubURL>/ConnectionManager/evt</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""


def _soap_response(service_type: str, action: str, fields: dict) -> bytes:
    """Build a minimal SOAP response envelope."""
    body = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action}Response xmlns:u="{service_type}">'
        f"{body}"
        f"</u:{action}Response>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return xml.encode("utf-8")


def _soap_fault(code: int, desc: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><s:Fault><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{code}</errorCode>"
        f"<errorDescription>{desc}</errorDescription>"
        "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    )
    return xml.encode("utf-8")


def _parse_soap_action(body: bytes) -> tuple[str, str, dict]:
    """
    Parse a SOAP request body.
    Returns (service_type, action_name, args_dict).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "", "", {}

    # Find the action element inside Body
    soap_body = None
    for el in root:
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "Body":
            soap_body = el
            break
    if soap_body is None:
        return "", "", {}

    action_el = None
    for el in soap_body:
        action_el = el
        break
    if action_el is None:
        return "", "", {}

    # Extract namespace and action name
    tag = action_el.tag
    if "}" in tag:
        service_type = tag[1:tag.index("}")]
        action_name  = tag[tag.index("}") + 1:]
    else:
        service_type = ""
        action_name  = tag

    args = {}
    for child in action_el:
        k = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        args[k] = child.text or ""

    return service_type, action_name, args


class UPnPHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the UPnP/DLNA service."""

    # Suppress default request log unless verbose/debug
    def log_message(self, fmt, *args):
        if self.server.verbose or self.server.debug:
            print(f"[{_ts()}] [UPnP]    {self.client_address[0]} {fmt % args}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/description.xml":
            xml = _build_description_xml(
                self.server.device_state, self.server.server_address[1]
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(xml)))
            self.end_headers()
            self.wfile.write(xml)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""

        _svc, action, args = _parse_soap_action(body)

        handler_map = {
            "/AVTransport/ctrl":      self._handle_av_transport,
            "/RenderingControl/ctrl": self._handle_rendering_control,
            "/ConnectionManager/ctrl": self._handle_connection_manager,
        }

        handler = handler_map.get(path)
        if handler is None:
            self.send_response(404)
            self.end_headers()
            return

        if self.server.verbose or self.server.debug:
            print(f"[{_ts()}] [UPnP]    SOAP {path} action={action!r} args={args}")

        handler(action, args)

    # ── AVTransport ───────────────────────────────────────────────────────────

    def _handle_av_transport(self, action: str, args: dict):
        svc = "urn:schemas-upnp-org:service:AVTransport:1"
        state: DeviceState = self.server.device_state

        if action == "Play":
            state.set_transport_state("PLAYING")
            self._send_soap(svc, action, {})

        elif action == "Pause":
            state.set_transport_state("PAUSED")
            self._send_soap(svc, action, {})

        elif action == "Stop":
            state.set_transport_state("STOPPED")
            self._send_soap(svc, action, {})

        elif action in ("Next", "Previous"):
            self._send_soap(svc, action, {})

        elif action == "Seek":
            self._send_soap(svc, action, {})

        elif action == "SetAVTransportURI":
            self._send_soap(svc, action, {})

        elif action == "GetTransportInfo":
            ts = state.get_transport_state()
            self._send_soap(svc, action, {
                "CurrentTransportState":  ts,
                "CurrentTransportStatus": "OK",
                "CurrentSpeed":           "1",
            })

        elif action == "GetPositionInfo":
            self._send_soap(svc, action, {
                "Track":         "1",
                "TrackDuration": "0:00:00",
                "TrackMetaData": "",
                "TrackURI":      "",
                "RelTime":       "0:00:00",
                "AbsTime":       "0:00:00",
                "RelCount":      "0",
                "AbsCount":      "0",
            })

        elif action == "GetMediaInfo":
            ts = state.get_transport_state()
            self._send_soap(svc, action, {
                "NrTracks":           "0",
                "MediaDuration":      "0:00:00",
                "CurrentURI":         "",
                "CurrentURIMetaData": "",
                "NextURI":            "",
                "NextURIMetaData":    "",
                "PlayMedium":         "NONE" if ts == "STOPPED" else "NETWORK",
                "RecordMedium":       "NOT_IMPLEMENTED",
                "WriteStatus":        "NOT_IMPLEMENTED",
            })

        else:
            self._send_fault(401, "Invalid Action")

    # ── RenderingControl ──────────────────────────────────────────────────────

    def _handle_rendering_control(self, action: str, args: dict):
        svc   = "urn:schemas-upnp-org:service:RenderingControl:1"
        state: DeviceState = self.server.device_state

        if action == "GetVolume":
            vol, _ = state.get_volume_snapshot()
            self._send_soap(svc, action, {"CurrentVolume": str(vol)})

        elif action == "SetVolume":
            try:
                state.set_volume(int(args.get("DesiredVolume", 0)))
            except ValueError:
                pass
            self._send_soap(svc, action, {})

        elif action == "GetMute":
            _, muted = state.get_volume_snapshot()
            self._send_soap(svc, action, {"CurrentMute": "1" if muted else "0"})

        elif action == "SetMute":
            state.set_muted(args.get("DesiredMute", "0") not in ("0", "false", "False"))
            self._send_soap(svc, action, {})

        else:
            self._send_fault(401, "Invalid Action")

    # ── ConnectionManager ─────────────────────────────────────────────────────

    def _handle_connection_manager(self, action: str, args: dict):
        svc = "urn:schemas-upnp-org:service:ConnectionManager:1"

        if action == "GetProtocolInfo":
            self._send_soap(svc, action, {
                "Source": "",
                "Sink":   _SUPPORTED_PROTOCOLS,
            })

        elif action == "GetCurrentConnectionIDs":
            self._send_soap(svc, action, {"ConnectionIDs": "0"})

        elif action == "GetCurrentConnectionInfo":
            self._send_soap(svc, action, {
                "RcsID":            "0",
                "AVTransportID":    "0",
                "ProtocolInfo":     "",
                "PeerConnectionManager": "",
                "PeerConnectionID": "-1",
                "Direction":        "Input",
                "Status":           "OK",
            })

        else:
            self._send_fault(401, "Invalid Action")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _send_soap(self, svc: str, action: str, fields: dict):
        body = _soap_response(svc, action, fields)
        self.send_response(200)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_fault(self, code: int, desc: str):
        body = _soap_fault(code, desc)
        self.send_response(500)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UPnPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, host, port, device_state: DeviceState, verbose: bool, debug: bool):
        self.device_state = device_state
        self.verbose      = verbose
        self.debug        = debug
        super().__init__((host, port), UPnPHandler)


# ─────────────────────────────────────────────────────────────────────────────
# SSDP Responder
# ─────────────────────────────────────────────────────────────────────────────

class SSDPServer(threading.Thread):
    """
    Listens on the SSDP multicast group (239.255.255.250:1900) and:
      1. Answers M-SEARCH requests from the Naim app (so it can re-discover us)
      2. Periodically broadcasts ssdp:alive NOTIFYs (keepalive)

    Without this, the app's UPnP cache expires after SSDP_TTL seconds and
    the device appears to go offline.
    """

    daemon = True

    def __init__(self, local_ip: str, upnp_port: int, state: DeviceState,
                 verbose: bool, debug: bool):
        super().__init__(daemon=True, name="SSDPServer")
        self._ip        = local_ip
        self._port      = upnp_port
        self._state     = state
        self._verbose   = verbose
        self._debug     = debug
        self._location  = f"http://{local_ip}:{upnp_port}/description.xml"
        self._udn       = f"uuid:emulated-naim-{state.product_code.lower()}"
        self._stop_evt  = threading.Event()
        self._sock      = None

    def stop(self):
        self._stop_evt.set()

    def _make_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # not available on all platforms
        sock.bind(("", SSDP_PORT))
        mreq = struct.pack("4sL", socket.inet_aton(SSDP_MCAST_ADDR), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        return sock

    def _nt_usn_pairs(self):
        """Return (NT, USN) pairs for all the service types we advertise."""
        return [
            ("upnp:rootdevice",
             f"{self._udn}::upnp:rootdevice"),
            (self._udn,
             self._udn),
            ("urn:schemas-upnp-org:device:MediaRenderer:1",
             f"{self._udn}::urn:schemas-upnp-org:device:MediaRenderer:1"),
        ]

    def _response_for(self, st: str) -> str | None:
        """Build a 200-OK SSDP response for the given search target, or None if not ours."""
        matched_usn = None
        for nt, usn in self._nt_usn_pairs():
            if st in ("ssdp:all", nt):
                matched_usn = usn
                break
        if matched_usn is None:
            return None

        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        return (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={SSDP_TTL}\r\n"
            f"DATE: {date_str}\r\n"
            "EXT:\r\n"
            f"LOCATION: {self._location}\r\n"
            "SERVER: Linux/3.0 UPnP/1.0 Naim/1.0\r\n"
            f"ST: {st}\r\n"
            f"USN: {matched_usn}\r\n"
            "\r\n"
        )

    def _notify(self, sock, nts: str = "ssdp:alive"):
        """Send multicast NOTIFY for all of our service types."""
        for nt, usn in self._nt_usn_pairs():
            if nts == "ssdp:byebye":
                body = (
                    "NOTIFY * HTTP/1.1\r\n"
                    f"HOST: {SSDP_MCAST_ADDR}:{SSDP_PORT}\r\n"
                    f"NT: {nt}\r\n"
                    "NTS: ssdp:byebye\r\n"
                    f"USN: {usn}\r\n"
                    "\r\n"
                )
            else:
                body = (
                    "NOTIFY * HTTP/1.1\r\n"
                    f"HOST: {SSDP_MCAST_ADDR}:{SSDP_PORT}\r\n"
                    f"CACHE-CONTROL: max-age={SSDP_TTL}\r\n"
                    f"LOCATION: {self._location}\r\n"
                    f"NT: {nt}\r\n"
                    "NTS: ssdp:alive\r\n"
                    "SERVER: Linux/3.0 UPnP/1.0 Naim/1.0\r\n"
                    f"USN: {usn}\r\n"
                    "\r\n"
                )
            try:
                sock.sendto(body.encode(), (SSDP_MCAST_ADDR, SSDP_PORT))
            except socket.error as exc:
                if self._verbose:
                    print(f"[{_ts()}] [SSDP]    notify error: {exc}")

    def run(self):
        try:
            self._sock = self._make_socket()
        except Exception as exc:
            print(f"[{_ts()}] [SSDP]    FAILED to bind: {exc} — SSDP disabled")
            return

        print(f"[{_ts()}] [SSDP]    listening on {SSDP_MCAST_ADDR}:{SSDP_PORT}  "
              f"location={self._location}")

        # Send initial alive announcement
        self._notify(self._sock)

        last_alive = _monotonic()
        while not self._stop_evt.is_set():
            # Periodic keepalive
            if _monotonic() - last_alive >= SSDP_ALIVE_INTERVAL:
                if self._verbose:
                    print(f"[{_ts()}] [SSDP]    sending ssdp:alive")
                self._notify(self._sock)
                last_alive = _monotonic()

            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except socket.error as exc:
                if not self._stop_evt.is_set():
                    print(f"[{_ts()}] [SSDP]    recv error: {exc}")
                break

            text = data.decode("utf-8", errors="replace")

            if not text.startswith("M-SEARCH"):
                continue

            # Extract ST header
            st = ""
            for line in text.splitlines():
                if line.upper().startswith("ST:"):
                    st = line[3:].strip()
                    break

            if self._verbose or self._debug:
                print(f"[{_ts()}] [SSDP]    M-SEARCH from {addr[0]}  ST={st!r}")

            resp = self._response_for(st)
            if resp:
                try:
                    self._sock.sendto(resp.encode(), addr)
                    if self._debug:
                        print(f"[{_ts()}] [SSDP]    -> responded to {addr[0]} ST={st!r}")
                except socket.error as exc:
                    print(f"[{_ts()}] [SSDP]    response error: {exc}")
            elif self._debug:
                print(f"[{_ts()}] [SSDP]    (ignored, ST {st!r} not ours)")

        # Shutdown: announce byebye
        try:
            self._notify(self._sock, nts="ssdp:byebye")
        except Exception:
            pass
        self._sock.close()


def _monotonic():
    import time
    return time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# Profile loading
# ─────────────────────────────────────────────────────────────────────────────

def load_profile(model_or_path: str | None, profile_path: str | None) -> dict:
    """
    Load a device profile.  Priority:
    1. --profile <path>  (explicit JSON file)
    2. --model <name>    (built-in profile or device_profiles/<name>.json)
    """
    if profile_path:
        p = Path(profile_path)
        if not p.exists():
            sys.exit(f"Error: profile file not found: {profile_path}")
        with p.open() as f:
            return json.load(f)

    if model_or_path:
        key = model_or_path.lower().replace("-", "_")
        if key in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[key]

        # Try device_profiles/<model>.json relative to this script
        script_dir = Path(__file__).parent
        candidates = [
            script_dir / "device_profiles" / f"{model_or_path}.json",
            script_dir / "device_profiles" / f"{key}.json",
            Path(model_or_path),
        ]
        for p in candidates:
            if p.exists():
                with p.open() as f:
                    return json.load(f)

        sys.exit(
            f"Error: unknown model {model_or_path!r}.\n"
            f"Available built-in models: {', '.join(BUILTIN_PROFILES)}\n"
            f"Or use --profile <path> to load a JSON file."
        )

    # Default to SuperUniti
    return _BUILTIN_SUPERUNITI


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Naim Device Emulator — emulates legacy Naim audio devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --model superuniti
  %(prog)s --model superuniti --verbose
  %(prog)s --profile device_profiles/superuniti.json
  %(prog)s --model superuniti --nstream-port 15555 --upnp-port 8080

Then test with:
  ./naim_control_nstream.py --host 127.0.0.1 inputs
  ./naim_control_nstream.py --host 127.0.0.1 product
  ./naim_control_upnp.py    --host 127.0.0.1 info
  ./naim_control_upnp.py    --host 127.0.0.1 volume-get
        """,
    )
    parser.add_argument(
        "--model", "-m",
        metavar="NAME",
        help=f"Built-in device model (available: {', '.join(BUILTIN_PROFILES)})",
    )
    parser.add_argument(
        "--profile", "-p",
        metavar="FILE",
        help="Path to a JSON device profile file",
    )
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        metavar="HOST",
        help="Address to bind servers on (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--nstream-port",
        type=int,
        default=NSTREAM_DEFAULT_PORT,
        metavar="PORT",
        help=f"n-Stream TCP port (default: {NSTREAM_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--upnp-port",
        type=int,
        default=UPNP_DEFAULT_PORT,
        metavar="PORT",
        help=f"UPnP HTTP port (default: {UPNP_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-upnp",
        action="store_true",
        help="Disable the UPnP/DLNA server (only run n-Stream)",
    )
    parser.add_argument(
        "--no-ssdp",
        action="store_true",
        help="Disable the SSDP responder (device won't be auto-discoverable)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Log all protocol messages (parsed commands and responses)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Raw-bytes dump + full XML logging (implies --verbose; very noisy)",
    )

    args = parser.parse_args()

    # --debug implies --verbose
    if args.debug:
        args.verbose = True

    profile = load_profile(args.model, args.profile)
    state   = DeviceState(profile)

    model_name = profile.get("model", profile.get("product_code", "Unknown"))
    local_ip   = _get_local_ip()

    print(f"Naim Emulator — {model_name}")
    print(f"  n-Stream : {args.bind}:{args.nstream_port}  (local IP: {local_ip})")

    # Start n-Stream server
    nstream = NStreamServer(args.bind, args.nstream_port, state, args.verbose, args.debug)
    t_nstream = threading.Thread(target=nstream.serve_forever, daemon=True)
    t_nstream.start()

    # Start UPnP server (optional)
    upnp   = None
    t_upnp = None
    if not args.no_upnp:
        print(f"  UPnP     : {args.bind}:{args.upnp_port}")
        upnp   = UPnPServer(args.bind, args.upnp_port, state, args.verbose, args.debug)
        t_upnp = threading.Thread(target=upnp.serve_forever, daemon=True)
        t_upnp.start()

    # Start SSDP responder (optional — requires UPnP port to be known)
    ssdp = None
    if not args.no_upnp and not args.no_ssdp:
        ssdp = SSDPServer(local_ip, args.upnp_port, state, args.verbose, args.debug)
        ssdp.start()
        print(f"  SSDP     : {SSDP_MCAST_ADDR}:{SSDP_PORT}  (location → http://{local_ip}:{args.upnp_port}/description.xml)")
    else:
        print(f"  SSDP     : disabled")

    print(f"  Inputs   : {len(profile.get('inputs', []))} configured")
    print(f"  Current  : {state.current_input}")
    if args.debug:
        print("  Mode     : DEBUG (raw bytes + full XML)")
    elif args.verbose:
        print("  Mode     : VERBOSE")
    print("Ready. Press Ctrl-C to stop.\n")

    try:
        while True:
            threading.Event().wait(timeout=1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        if ssdp:
            ssdp.stop()
        nstream.shutdown()
        if upnp:
            upnp.shutdown()


if __name__ == "__main__":
    main()
