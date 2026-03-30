#!/usr/bin/env python3
"""
Naim Streamer Control CLI — REST API (newer devices)
Reverse-engineered from the Naim App Android application.
Protocol: HTTP REST API on port 15081

For newer Naim devices (Uniti series, Mu-so 2nd gen, etc.) that expose
a JSON REST API on port 15081.
"""

import argparse
import json
import socket
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

DEFAULT_PORT = 15081

# ─────────────────────────────────────────────
# DEVICE DISCOVERY
# ─────────────────────────────────────────────
# The Naim app uses four parallel discovery mechanisms (from decompiled APK):
#   1. SSDP/UPnP  – M-SEARCH on 239.255.255.250:1900
#   2. mDNS/DNS-SD – browse _leo._tcp, _Naim-Updater._tcp, _sueS800Device._tcp
#   3. TCP port scan – scan local subnet for port 15081
#   4. Android NsdManager – secondary mDNS (Android-specific)
#
# We implement (1) SSDP, (2) mDNS (via zeroconf if available), and (3) port scan.
# Discovered devices are verified by fetching GET /system on port 15081.

SSDP_MULTICAST_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_SEARCH_TARGETS = [
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
]
MDNS_SERVICE_TYPES = [
    "_leo._tcp.local.",
    "_Naim-Updater._tcp.local.",
    "_sueS800Device._tcp.local.",
    "_sueGrouping._tcp.local.",
]


def _ssdp_discover(timeout=5):
    """Send SSDP M-SEARCH and collect responding device locations."""
    locations = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    for st in SSDP_SEARCH_TARGETS:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_MULTICAST_ADDR}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 3\r\n"
            f"ST: {st}\r\n"
            "USER-AGENT: UPnP/1.0 mmupnp/3.0.0\r\n"
            "\r\n"
        )
        sock.sendto(msg.encode(), (SSDP_MULTICAST_ADDR, SSDP_PORT))

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            text = data.decode(errors="replace")
            for line in text.splitlines():
                low = line.lower()
                if low.startswith("location:"):
                    loc = line.split(":", 1)[1].strip()
                    locations[loc] = addr[0]
        except socket.timeout:
            break
    sock.close()
    return locations


def _parse_upnp_description(url):
    """Fetch a UPnP device description XML and extract device info."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
    except Exception:
        return None

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None

    ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
    device = root.find(".//upnp:device", ns)
    if device is None:
        # try without namespace
        device = root.find(".//{urn:schemas-upnp-org:device-1-0}device")
    if device is None:
        return None

    def txt(tag):
        el = device.find(f"upnp:{tag}", ns)
        if el is None:
            el = device.find(f"{{urn:schemas-upnp-org:device-1-0}}{tag}")
        return el.text.strip() if el is not None and el.text else None

    return {
        "friendlyName": txt("friendlyName"),
        "manufacturer": txt("manufacturer"),
        "modelName": txt("modelName"),
        "UDN": txt("UDN"),
        "serialNumber": txt("serialNumber"),
    }


def _verify_naim_device(ip, port=DEFAULT_PORT):
    """Verify a host is a Naim device by querying GET /system."""
    url = f"http://{ip}:{port}/system"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data
    except Exception:
        return None


def _mdns_discover(timeout=5):
    """Discover Naim devices via mDNS/DNS-SD using the zeroconf library."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        return {}

    found = {}
    lock = threading.Lock()

    class Listener:
        def add_service(self, zc, stype, name):
            info = zc.get_service_info(stype, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                with lock:
                    found[ip] = {
                        "name": info.server or name,
                        "service_type": stype,
                        "port": info.port,
                    }

        def remove_service(self, zc, stype, name):
            pass

        def update_service(self, zc, stype, name):
            pass

    zc = Zeroconf()
    listener = Listener()
    browsers = []
    for stype in MDNS_SERVICE_TYPES:
        browsers.append(ServiceBrowser(zc, stype, listener))

    time.sleep(timeout)
    zc.close()
    return found


def _portscan_discover(timeout=2):
    """Scan local subnet for TCP port 15081 (as the Naim app does)."""
    found = []
    local_ip = _get_local_ip()
    if not local_ip:
        return found

    # derive /24 subnet
    parts = local_ip.rsplit(".", 1)
    if len(parts) != 2:
        return found
    prefix = parts[0]

    lock = threading.Lock()

    def probe(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, DEFAULT_PORT))
            s.close()
            with lock:
                found.append(ip)
        except Exception:
            pass

    threads = []
    for i in range(1, 255):
        ip = f"{prefix}.{i}"
        t = threading.Thread(target=probe, args=(ip,))
        t.daemon = True
        threads.append(t)
        t.start()
        # limit concurrency per Naim app config (5 targets per batch with 40ms delay)
        if len(threads) % 50 == 0:
            time.sleep(0.04)

    for t in threads:
        t.join(timeout=timeout + 1)
    return found


def _get_local_ip():
    """Get local IP address by creating a UDP socket."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def cmd_discover(args):
    """Discover Naim devices on the local network."""
    timeout = args.timeout if hasattr(args, "timeout") else 5
    devices = {}  # ip -> info dict

    print(f"Scanning for Naim devices (timeout={timeout}s)...\n")

    # Phase 1: Run SSDP and mDNS in parallel
    ssdp_result = {}
    mdns_result = {}

    def run_ssdp():
        nonlocal ssdp_result
        ssdp_result = _ssdp_discover(timeout)

    def run_mdns():
        nonlocal mdns_result
        mdns_result = _mdns_discover(timeout)

    t_ssdp = threading.Thread(target=run_ssdp)
    t_mdns = threading.Thread(target=run_mdns)
    t_ssdp.start()
    t_mdns.start()
    t_ssdp.join()
    t_mdns.join()

    # Process SSDP results – fetch UPnP description and filter for Naim
    for location, ip in ssdp_result.items():
        if ip in devices:
            continue
        desc = _parse_upnp_description(location)
        if desc and desc.get("manufacturer") and "naim" in desc["manufacturer"].lower():
            devices[ip] = {
                "ip": ip,
                "source": "ssdp",
                "friendlyName": desc.get("friendlyName"),
                "manufacturer": desc.get("manufacturer"),
                "modelName": desc.get("modelName"),
                "serialNumber": desc.get("serialNumber"),
                "UDN": desc.get("UDN"),
            }

    # Process mDNS results
    for ip, info in mdns_result.items():
        if ip not in devices:
            devices[ip] = {
                "ip": ip,
                "source": "mdns",
                "service_type": info.get("service_type"),
                "name": info.get("name"),
            }

    # Phase 2: Port scan as fallback (like PortScanDiscoveryManager)
    if not devices:
        print("No devices found via SSDP/mDNS, falling back to port scan...")
        portscan_ips = _portscan_discover(timeout=2)
        for ip in portscan_ips:
            if ip not in devices:
                devices[ip] = {"ip": ip, "source": "portscan"}

    # Phase 3: Verify each candidate via GET /system on port 15081
    verified = []
    for ip, info in devices.items():
        sys_info = _verify_naim_device(ip)
        if sys_info:
            info["system"] = sys_info
            info["model"] = sys_info.get("model", info.get("modelName", "Unknown"))
            info["hostname"] = sys_info.get("hostname", "Unknown")
            info["platform"] = "leo"
            verified.append(info)

    if not verified:
        print("No Naim devices (with REST API on port 15081) found on the network.")
        print("For legacy devices (SuperUniti, NDS, NDX, etc.), use naim_control_upnp.py.")
        return

    print(f"Found {len(verified)} Naim device(s) with REST API:\n")
    for i, dev in enumerate(verified, 1):
        model = dev.get("model", dev.get("modelName", "Unknown"))
        hostname = dev.get("hostname", "")
        name = dev.get("friendlyName") or hostname or model
        source = dev.get("source", "")
        print(f"  [{i}] {name}")
        print(f"      IP:       {dev['ip']}")
        print(f"      Model:    {model}")
        if dev.get("serialNumber"):
            print(f"      Serial:   {dev['serialNumber']}")
        print(f"      Platform: REST API :15081")
        print(f"      Found via: {source}")
        print()


def make_url(host, port, path, params=None):
    base = f"http://{host}:{port}/{path.lstrip('/')}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            base += "?" + urllib.parse.urlencode(filtered)
    return base


def request(method, host, port, path, params=None, body=None):
    url = make_url(host, port, path, params)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw.decode(errors="replace")
            return {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            err = json.loads(raw)
        except Exception:
            err = raw.decode(errors="replace")
        print(f"HTTP {e.code} {e.reason}: {err}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def GET(host, port, path, params=None):
    return request("GET", host, port, path, params)


def PUT(host, port, path, params=None):
    return request("PUT", host, port, path, params)


def POST(host, port, path, params=None, body=None):
    return request("POST", host, port, path, params, body)


def DELETE(host, port, path):
    return request("DELETE", host, port, path)


def pretty(data):
    if isinstance(data, dict):
        print(json.dumps(data, indent=2))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


# ─────────────────────────────────────────────
# SYSTEM
# ─────────────────────────────────────────────

def cmd_system_info(args):
    """GET /system – device information"""
    pretty(GET(args.host, args.port, "system"))


def cmd_system_usage(args):
    """GET /system/usage"""
    pretty(GET(args.host, args.port, "system/usage"))


def cmd_system_datetime(args):
    """GET /system/datetime"""
    pretty(GET(args.host, args.port, "system/datetime"))


def cmd_system_reboot(args):
    """GET /system?cmd=reboot"""
    pretty(GET(args.host, args.port, "system", {"cmd": "reboot"}))


def cmd_system_keepawake(args):
    """GET /system?cmd=kick – prevent standby"""
    pretty(GET(args.host, args.port, "system", {"cmd": "kick"}))


def cmd_system_firstsetup(args):
    """PUT /system?firstTimeSetupComplete=true/false"""
    pretty(PUT(args.host, args.port, "system", {"firstTimeSetupComplete": str(args.complete).lower()}))


# ─────────────────────────────────────────────
# POWER
# ─────────────────────────────────────────────

def cmd_power_get(args):
    """GET /power"""
    pretty(GET(args.host, args.port, "power"))


def cmd_power_set(args):
    """PUT /power?system=on|off|lona|loff"""
    pretty(PUT(args.host, args.port, "power", {"system": args.state}))


def cmd_power_server(args):
    """PUT /power?serverMode=true|false"""
    pretty(PUT(args.host, args.port, "power", {"serverMode": str(args.enable).lower()}))


def cmd_power_timeout(args):
    """PUT /power?standbyTimeout=<minutes>"""
    pretty(PUT(args.host, args.port, "power", {"standbyTimeout": args.minutes}))


# ─────────────────────────────────────────────
# NOW PLAYING / PLAYBACK
# ─────────────────────────────────────────────

def cmd_nowplaying(args):
    """GET /nowplaying – current playback state"""
    pretty(GET(args.host, args.port, "nowplaying"))


def cmd_play(args):
    """GET /nowplaying?cmd=play"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "play"}))


def cmd_pause(args):
    """GET /nowplaying?cmd=pause"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "pause"}))


def cmd_stop(args):
    """GET /nowplaying?cmd=stop"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "stop"}))


def cmd_resume(args):
    """GET /nowplaying?cmd=resume"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "resume"}))


def cmd_next(args):
    """GET /nowplaying?cmd=next"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "next"}))


def cmd_prev(args):
    """GET /nowplaying?cmd=prev"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "prev"}))


def cmd_toggle(args):
    """GET /nowplaying?cmd=playpause"""
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "playpause"}))


def cmd_seek(args):
    """GET /nowplaying?cmd=seek&position=<milliseconds>"""
    # Convert seconds to milliseconds for the API
    position_ms = int(args.position * 1000)
    pretty(GET(args.host, args.port, "nowplaying", {"cmd": "seek", "position": position_ms}))


def cmd_repeat(args):
    """PUT /nowplaying?repeat=<0|1|2>  (0=off, 1=all, 2=one)"""
    pretty(PUT(args.host, args.port, "nowplaying", {"repeat": args.mode}))


def cmd_shuffle(args):
    """PUT /nowplaying?shuffle=true|false"""
    pretty(PUT(args.host, args.port, "nowplaying", {"shuffle": str(args.enable).lower()}))


# ─────────────────────────────────────────────
# VOLUME / LEVELS
# ─────────────────────────────────────────────

def cmd_levels_get(args):
    """GET /levels – main volume info"""
    pretty(GET(args.host, args.port, "levels"))


def cmd_levels_room(args):
    """GET /levels/room"""
    pretty(GET(args.host, args.port, "levels/room"))


def cmd_levels_group(args):
    """GET /levels/group"""
    pretty(GET(args.host, args.port, "levels/group"))


def cmd_levels_bluetooth(args):
    """GET /levels/bluetooth"""
    pretty(GET(args.host, args.port, "levels/bluetooth"))


def cmd_volume_set(args):
    """PUT /<ussi>?volume=<0-100>  (ussi defaults to 'levels')"""
    ussi = args.ussi or "levels"
    pretty(PUT(args.host, args.port, ussi, {"volume": args.level}))


def cmd_mute(args):
    """PUT /<ussi>?mute=1"""
    ussi = args.ussi or "levels/room"
    pretty(PUT(args.host, args.port, ussi, {"mute": 1}))


def cmd_unmute(args):
    """PUT /<ussi>?mute=0"""
    ussi = args.ussi or "levels/room"
    pretty(PUT(args.host, args.port, ussi, {"mute": 0}))


def cmd_balance(args):
    """PUT /<ussi>?balance=<value>"""
    ussi = args.ussi or "levels"
    pretty(PUT(args.host, args.port, ussi, {"balance": args.value}))


def cmd_volume_mode(args):
    """PUT /<ussi>?mode=<0|1|2>  (0=Variable, 1=Hybrid, 2=Fixed)"""
    ussi = args.ussi or "levels"
    pretty(PUT(args.host, args.port, ussi, {"mode": args.mode}))


# ─────────────────────────────────────────────
# INPUTS
# ─────────────────────────────────────────────

def cmd_inputs_list(args):
    """GET /inputs – list all inputs"""
    pretty(GET(args.host, args.port, "inputs"))


def cmd_input_details(args):
    """GET /<ussi> – details of a specific input"""
    pretty(GET(args.host, args.port, args.ussi))


def cmd_input_select(args):
    """GET /<ussi>?cmd=select"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "select"}))


def cmd_input_play(args):
    """GET /<ussi>?cmd=play"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "play"}))


def cmd_input_resume(args):
    """GET /<ussi>?cmd=resume"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "resume"}))


def cmd_input_rename(args):
    """PUT /<ussi>?alias=<name>"""
    pretty(PUT(args.host, args.port, args.ussi, {"alias": args.name}))


def cmd_input_disable(args):
    """PUT /<ussi>?disabled=true|false"""
    pretty(PUT(args.host, args.port, args.ussi, {"disabled": str(args.disabled).lower()}))


def cmd_input_trim(args):
    """PUT /<ussi>?trim=<dB value>"""
    pretty(PUT(args.host, args.port, args.ussi, {"trim": args.value}))


def cmd_input_sensitivity(args):
    """PUT /<ussi>?sensitivity=<value>"""
    pretty(PUT(args.host, args.port, args.ussi, {"sensitivity": args.value}))


def cmd_input_unity_gain(args):
    """PUT /<ussi>?unityGain=true|false"""
    pretty(PUT(args.host, args.port, args.ussi, {"unityGain": str(args.enable).lower()}))


# ─────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────

def cmd_outputs_list(args):
    """GET /outputs"""
    pretty(GET(args.host, args.port, "outputs"))


def cmd_output_details(args):
    """GET /<ussi>"""
    pretty(GET(args.host, args.port, args.ussi))


def cmd_output_enabled(args):
    """PUT /<ussi>?enabled=true|false"""
    pretty(PUT(args.host, args.port, args.ussi, {"enabled": str(args.enable).lower()}))


def cmd_output_max_volume(args):
    """PUT /<ussi>?maxVolume=<value>"""
    pretty(PUT(args.host, args.port, args.ussi, {"maxVolume": args.value}))


def cmd_loudness(args):
    """PUT /outputs?loudness=<value>"""
    pretty(PUT(args.host, args.port, "outputs", {"loudness": args.value}))


def cmd_loudness_enabled(args):
    """PUT /outputs?loudnessEnabled=true|false"""
    pretty(PUT(args.host, args.port, "outputs", {"loudnessEnabled": str(args.enable).lower()}))


def cmd_room_position(args):
    """PUT /outputs?position=<0-2>"""
    pretty(PUT(args.host, args.port, "outputs", {"position": args.position}))


def cmd_dsd_mode(args):
    """PUT /outputs/digital?dsdMode=<value>"""
    pretty(PUT(args.host, args.port, "outputs/digital", {"dsdMode": args.mode}))


# ─────────────────────────────────────────────
# BLUETOOTH
# ─────────────────────────────────────────────

def cmd_bt_pair(args):
    """GET /inputs/bluetooth?cmd=pair – start pairing"""
    pretty(GET(args.host, args.port, "inputs/bluetooth", {"cmd": "pair"}))


def cmd_bt_stop_pair(args):
    """GET /inputs/bluetooth?cmd=pair&action=stop"""
    pretty(GET(args.host, args.port, "inputs/bluetooth", {"cmd": "pair", "action": "stop"}))


def cmd_bt_clear_history(args):
    """GET /inputs/bluetooth?cmd=pair&action=clear"""
    pretty(GET(args.host, args.port, "inputs/bluetooth", {"cmd": "pair", "action": "clear"}))


def cmd_bt_drop(args):
    """GET /inputs/bluetooth?cmd=drop – disconnect current device"""
    pretty(GET(args.host, args.port, "inputs/bluetooth", {"cmd": "drop"}))


def cmd_bt_forget(args):
    """GET /inputs/bluetooth?cmd=forget – clear all pairing history"""
    pretty(GET(args.host, args.port, "inputs/bluetooth", {"cmd": "forget"}))


def cmd_bt_auto_pair(args):
    """PUT /inputs/bluetooth?open=true|false"""
    pretty(PUT(args.host, args.port, "inputs/bluetooth", {"open": str(args.enable).lower()}))


# ─────────────────────────────────────────────
# STREAMING SERVICES
# ─────────────────────────────────────────────

def cmd_qobuz_login(args):
    """GET /inputs/qobuz?cmd=login&username=...&password=..."""
    pretty(GET(args.host, args.port, "inputs/qobuz",
               {"cmd": "login", "username": args.username, "password": args.password}))


def cmd_qobuz_quality(args):
    """PUT /inputs/qobuz?quality=<value>  (5=320kbps, 6=FLAC 16bit, 7=FLAC 24bit, 27=FLAC 24bit HiRes)"""
    pretty(PUT(args.host, args.port, "inputs/qobuz", {"quality": args.quality}))


def cmd_qobuz_logout(args):
    """GET /inputs/qobuz?cmd=logout"""
    pretty(GET(args.host, args.port, "inputs/qobuz", {"cmd": "logout"}))


def cmd_tidal_login(args):
    """GET /inputs/tidal?cmd=oauthLogin&accessToken=...&refreshToken=...&oauthIdent=..."""
    params = {"cmd": "oauthLogin", "accessToken": args.access_token, "refreshToken": args.refresh_token}
    if args.oauth_ident:
        params["oauthIdent"] = args.oauth_ident
    pretty(GET(args.host, args.port, "inputs/tidal", params))


def cmd_tidal_logout(args):
    """GET /inputs/tidal?cmd=oauthLogout"""
    pretty(GET(args.host, args.port, "inputs/tidal", {"cmd": "oauthLogout"}))


def cmd_spotify_bitrate(args):
    """PUT /inputs/spotify?spotifyBitrate=<normal|high|very_high>"""
    pretty(PUT(args.host, args.port, "inputs/spotify", {"spotifyBitrate": args.bitrate}))


def cmd_spotify_gain_norm(args):
    """PUT /inputs/spotify?gainNormalisation=true|false"""
    pretty(PUT(args.host, args.port, "inputs/spotify", {"gainNormalisation": str(args.enable).lower()}))


def cmd_spotify_presets(args):
    """GET /inputs/spotify/presets"""
    pretty(GET(args.host, args.port, "inputs/spotify/presets"))


def cmd_spotify_preset_save(args):
    """GET /inputs/spotify/presets?cmd=save&presetID=<n>"""
    pretty(GET(args.host, args.port, "inputs/spotify/presets", {"cmd": "save", "presetID": args.preset_id}))


# ─────────────────────────────────────────────
# IRADIO
# ─────────────────────────────────────────────

def cmd_iradio_browse(args):
    """GET /inputs/radio – browse iRadio"""
    pretty(GET(args.host, args.port, "inputs/radio"))


def cmd_iradio_scan(args):
    """GET /<ussi>?cmd=scanStations"""
    ussi = args.ussi or "inputs/radio"
    pretty(GET(args.host, args.port, ussi, {"cmd": "scanStations"}))


def cmd_iradio_scan_up(args):
    """GET /<ussi>?cmd=scanUp"""
    ussi = args.ussi or "inputs/dab"
    pretty(GET(args.host, args.port, ussi, {"cmd": "scanUp"}))


def cmd_iradio_scan_down(args):
    """GET /<ussi>?cmd=scanDown"""
    ussi = args.ussi or "inputs/dab"
    pretty(GET(args.host, args.port, ussi, {"cmd": "scanDown"}))


def cmd_iradio_scan_stop(args):
    """GET /<ussi>?cmd=scanStop"""
    ussi = args.ussi or "inputs/dab"
    pretty(GET(args.host, args.port, ussi, {"cmd": "scanStop"}))


def cmd_iradio_step_up(args):
    """GET /<ussi>?cmd=stepUp"""
    ussi = args.ussi or "inputs/fm"
    pretty(GET(args.host, args.port, ussi, {"cmd": "stepUp"}))


def cmd_iradio_step_down(args):
    """GET /<ussi>?cmd=stepDown"""
    ussi = args.ussi or "inputs/fm"
    pretty(GET(args.host, args.port, ussi, {"cmd": "stepDown"}))


def cmd_iradio_play(args):
    """GET /<ussi>?cmd=play&stationKey=<key>"""
    params = {"cmd": "play"}
    if args.station_key:
        params["stationKey"] = args.station_key
    pretty(GET(args.host, args.port, args.ussi, params))


def cmd_iradio_add_station(args):
    """POST /inputs/radio/user?name=...&stationKey=...&genre=...&location=...&bitRate=..."""
    params = {"name": args.name, "stationKey": args.station_key}
    if args.genre:
        params["genre"] = args.genre
    if args.location:
        params["location"] = args.location
    if args.bitrate:
        params["bitRate"] = args.bitrate
    if args.artwork:
        params["artwork"] = args.artwork
    pretty(POST(args.host, args.port, "inputs/radio/user", params))


def cmd_iradio_delete_station(args):
    """DELETE /<ussi>"""
    pretty(DELETE(args.host, args.port, args.ussi))


# ─────────────────────────────────────────────
# PLAY QUEUE
# ─────────────────────────────────────────────

def cmd_playqueue_get(args):
    """GET /inputs/playqueue"""
    pretty(GET(args.host, args.port, "inputs/playqueue"))


def cmd_playqueue_clear(args):
    """POST /inputs/playqueue?clear=true"""
    pretty(POST(args.host, args.port, "inputs/playqueue", {"clear": "true"}))


def cmd_playqueue_move(args):
    """GET /inputs/playqueue?cmd=move&what=<ussi>&where=<ussi>"""
    params = {"cmd": "move", "what": args.what}
    if args.where:
        params["where"] = args.where
    pretty(GET(args.host, args.port, "inputs/playqueue", params))


def cmd_playqueue_set_current(args):
    """PUT /inputs/playqueue?current=<ussi>"""
    pretty(PUT(args.host, args.port, "inputs/playqueue", {"current": args.ussi}))


def cmd_playqueue_track(args):
    """GET /<track_ussi> – track details"""
    pretty(GET(args.host, args.port, args.track_ussi))


# ─────────────────────────────────────────────
# FAVOURITES / PRESETS
# ─────────────────────────────────────────────

def cmd_favourites_list(args):
    """GET /favourites"""
    params = {}
    if args.presets_only:
        params["presetsOnly"] = "true"
    if args.available_only:
        params["availableOnly"] = "true"
    pretty(GET(args.host, args.port, "favourites", params or None))


def cmd_favourite_details(args):
    """GET /<ussi>"""
    pretty(GET(args.host, args.port, args.ussi))


def cmd_favourite_play(args):
    """GET /<ussi>?cmd=play"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "play"}))


def cmd_favourite_delete(args):
    """DELETE /<ussi>"""
    pretty(DELETE(args.host, args.port, args.ussi))


def cmd_preset_assign(args):
    """GET /<ussi>?cmd=assign&presetID=<n>"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "assign", "presetID": args.preset_id}))


def cmd_preset_deassign(args):
    """GET /<ussi>?cmd=deassign"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "deassign"}))


def cmd_preset_move(args):
    """GET /favourites?cmd=move_preset&what=<from>&where=<to>"""
    pretty(GET(args.host, args.port, "favourites",
               {"cmd": "move_preset", "what": args.from_pos, "where": args.to_pos}))


# ─────────────────────────────────────────────
# MULTIROOM
# ─────────────────────────────────────────────

def cmd_multiroom_get(args):
    """GET /multiroom"""
    pretty(GET(args.host, args.port, "multiroom"))


def cmd_multiroom_add(args):
    """PUT /<ussi>?cmd=addToGroup"""
    pretty(PUT(args.host, args.port, args.ussi, {"cmd": "addToGroup"}))


def cmd_multiroom_remove(args):
    """PUT /<ussi>?cmd=leaveGroup"""
    pretty(PUT(args.host, args.port, args.ussi, {"cmd": "leaveGroup"}))


# ─────────────────────────────────────────────
# CD
# ─────────────────────────────────────────────

def cmd_cd_info(args):
    """GET /inputs/cd"""
    pretty(GET(args.host, args.port, "inputs/cd"))


def cmd_cd_eject(args):
    """GET /inputs/cd?cmd=eject"""
    pretty(GET(args.host, args.port, "inputs/cd", {"cmd": "eject"}))


def cmd_cd_play(args):
    """PUT /inputs/cd?current=/inputs/cd/0"""
    pretty(PUT(args.host, args.port, "inputs/cd", {"current": "/inputs/cd/0"}))


def cmd_cd_insert_action(args):
    """PUT /inputs/cd?action=<0|1|2>  (0=nothing, 1=play, 2=rip)"""
    pretty(PUT(args.host, args.port, "inputs/cd", {"action": args.action}))


# ─────────────────────────────────────────────
# ALARMS / SLEEP TIMER
# ─────────────────────────────────────────────

def cmd_alarm_list(args):
    """GET /alarms"""
    pretty(GET(args.host, args.port, "alarms"))


def cmd_alarm_details(args):
    """GET /<ussi>"""
    pretty(GET(args.host, args.port, args.ussi))


def cmd_alarm_set(args):
    """POST /alarms?name=...&source=...&hours=...&minutes=...&recurrenceDays=...&enabled=..."""
    params = {"name": args.name, "source": args.source}
    if args.hours is not None:
        params["hours"] = args.hours
    if args.minutes is not None:
        params["minutes"] = args.minutes
    if args.days is not None:
        params["recurrenceDays"] = args.days
    params["enabled"] = str(args.enabled).lower() if args.enabled is not None else "true"
    pretty(POST(args.host, args.port, "alarms", params))


def cmd_alarm_enable(args):
    """PUT /<ussi>?enabled=true|false"""
    pretty(PUT(args.host, args.port, args.ussi, {"enabled": str(args.enable).lower()}))


def cmd_alarm_delete(args):
    """DELETE /<ussi>"""
    pretty(DELETE(args.host, args.port, args.ussi))


def cmd_sleep_start(args):
    """GET /alarms?cmd=sleep&sleepPeriod=<minutes>"""
    pretty(GET(args.host, args.port, "alarms", {"cmd": "sleep", "sleepPeriod": args.minutes}))


def cmd_sleep_stop(args):
    """GET /alarms?cmd=cancelSleep"""
    pretty(GET(args.host, args.port, "alarms", {"cmd": "cancelSleep"}))


# ─────────────────────────────────────────────
# NETWORK
# ─────────────────────────────────────────────

def cmd_network_get(args):
    """GET /network"""
    pretty(GET(args.host, args.port, "network"))


def cmd_network_hostname(args):
    """PUT /network?hostname=<name>"""
    pretty(PUT(args.host, args.port, "network", {"hostname": args.hostname}))


def cmd_network_scan_wifi(args):
    """GET /network/wireless?cmd=scan"""
    pretty(GET(args.host, args.port, "network/wireless", {"cmd": "scan"}))


def cmd_network_setup_wifi(args):
    """PUT /network/wireless?wifiSsid=...&wifiKey=..."""
    pretty(PUT(args.host, args.port, "network/wireless",
               {"wifiSsid": args.ssid, "wifiKey": args.key}))


def cmd_network_dhcp(args):
    """PUT /<networkType>?dhcp=1"""
    pretty(PUT(args.host, args.port, args.iface, {"dhcp": "1"}))


def cmd_network_static(args):
    """PUT /<networkType>/?dhcp=0&ipAddress=...&netmask=...&gateway=...&dns1=...&dns2=..."""
    params = {
        "dhcp": "0",
        "ipAddress": args.ip,
        "netmask": args.netmask,
        "gateway": args.gateway,
        "dns1": args.dns1,
    }
    if args.dns2:
        params["dns2"] = args.dns2
    pretty(PUT(args.host, args.port, args.iface, params))


def cmd_network_samba(args):
    """PUT /network?sambaSMB1=true|false"""
    pretty(PUT(args.host, args.port, "network", {"sambaSMB1": str(args.enable).lower()}))


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────

def cmd_update_get(args):
    """GET /update"""
    pretty(GET(args.host, args.port, "update"))


def cmd_update_check(args):
    """GET /update?cmd=get_versions"""
    pretty(GET(args.host, args.port, "update", {"cmd": "get_versions"}))


def cmd_update_start(args):
    """GET /update?cmd=start_update"""
    pretty(GET(args.host, args.port, "update", {"cmd": "start_update"}))


# ─────────────────────────────────────────────
# BROWSE (Local library)
# ─────────────────────────────────────────────

def cmd_browse_tracks(args):
    """GET /tracks?offset=<n>&limit=<n>"""
    params = {}
    if args.offset:
        params["offset"] = args.offset
    if args.limit:
        params["limit"] = args.limit
    pretty(GET(args.host, args.port, "tracks", params or None))


def cmd_browse_albums(args):
    """GET /albums?offset=<n>&limit=<n>"""
    params = {}
    if args.offset:
        params["offset"] = args.offset
    if args.limit:
        params["limit"] = args.limit
    pretty(GET(args.host, args.port, "albums", params or None))


def cmd_browse_artists(args):
    """GET /artists?offset=<n>&limit=<n>"""
    params = {}
    if args.offset:
        params["offset"] = args.offset
    if args.limit:
        params["limit"] = args.limit
    pretty(GET(args.host, args.port, "artists", params or None))


def cmd_browse_play(args):
    """GET /<ussi>?cmd=play"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "play"}))


def cmd_browse_play_next(args):
    """GET /<ussi>?cmd=playNext"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "playNext"}))


def cmd_browse_play_last(args):
    """GET /<ussi>?cmd=playLast"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "playLast"}))


def cmd_browse_refresh(args):
    """GET /<ussi>?cmd=refresh"""
    pretty(GET(args.host, args.port, args.ussi, {"cmd": "refresh"}))


# ─────────────────────────────────────────────
# API INFO
# ─────────────────────────────────────────────

def cmd_api_info(args):
    """GET /api – supported API versions"""
    pretty(GET(args.host, args.port, "api"))


# ─────────────────────────────────────────────
# SSE MONITOR  (real-time push via /notify)
# ─────────────────────────────────────────────
# The Naim app opens a persistent SSE stream to GET /notify with
# Accept: text/event-stream.  Each event is a JSON object:
#   { "version": "1", "ussi": "nowplaying", "id": 5,
#     "parameters": { "transportState": "Playing", "seekPosition": 45000 } }
# The "ussi" field matches the REST path of the changed resource.
# "parameters" contains only the fields that changed (partial update).

def cmd_monitor(args):
    """Stream real-time SSE events from GET /notify on port 15081."""
    url = f"http://{args.host}:{args.port}/notify"
    ussi_filter = args.ussi.lower() if args.ussi else None

    print(f"Connecting to SSE stream at {url}")
    if ussi_filter:
        print(f"Filtering for ussi containing: {ussi_filter!r}")
    print("Press Ctrl-C to stop.\n")

    req = urllib.request.Request(url)
    req.add_header("Accept", "text/event-stream")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("Connection", "keep-alive")

    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            # SSE wire format: lines of "data: <json>\n" separated by blank lines.
            # Accumulate a single event's "data:" lines between blank lines.
            data_lines = []
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")

                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                elif line == "":
                    # Blank line = end of one event
                    if data_lines:
                        payload = "".join(data_lines)
                        data_lines = []
                        _sse_display(payload, ussi_filter, args.raw)
                # (ignore "id:", "event:", "retry:", and comment lines)

    except KeyboardInterrupt:
        print("\nStopped.")
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to {url}: {e.reason}")
        sys.exit(1)


def _sse_display(payload: str, ussi_filter, raw: bool):
    """Parse and print a single SSE data payload."""
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        # Not valid JSON — print raw and move on
        print(f"[raw] {payload}")
        return

    ussi = event.get("ussi", "")

    # Apply optional ussi filter
    if ussi_filter and ussi_filter not in ussi.lower():
        return

    if raw:
        print(json.dumps(event, indent=2))
        print()
        return

    # Formatted output
    ts = time.strftime("%H:%M:%S")
    params = event.get("parameters", {})
    event_id = event.get("id", "")
    print(f"[{ts}] {ussi}  (id={event_id})")
    if params:
        max_k = max(len(k) for k in params)
        for k, v in params.items():
            print(f"  {k:<{max_k}}  {v}")
    print()


# ─────────────────────────────────────────────
# CLI SETUP
# ─────────────────────────────────────────────

def add_host_args(p):
    p.add_argument("--host", default=None, help="Naim device IP address")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"API port (default: {DEFAULT_PORT})")


def main():
    parser = argparse.ArgumentParser(
        description="Naim Streamer Control CLI — REST API (port 15081) for newer devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s discover
  %(prog)s discover --timeout 10
  %(prog)s --host 192.168.1.50 nowplaying
  %(prog)s --host 192.168.1.50 play
  %(prog)s --host 192.168.1.50 volume-set --level 40
  %(prog)s --host 192.168.1.50 input-select --ussi inputs/tidal
  %(prog)s --host 192.168.1.50 alarm-list
  %(prog)s --host 192.168.1.50 monitor              # SSE real-time events (/notify)
  %(prog)s --host 192.168.1.50 monitor --ussi nowplaying
  %(prog)s --host 192.168.1.50 monitor --raw

For legacy devices (SuperUniti, NDS, NDX, etc.), use naim_control_upnp.py instead.
        """,
    )
    add_host_args(parser)

    sub = parser.add_subparsers(title="commands", dest="command", required=True)

    # ── DISCOVER ──
    p = sub.add_parser("discover", help="Discover Naim devices on the local network")
    p.add_argument("--timeout", type=int, default=5, help="Discovery timeout in seconds (default: 5)")
    p.set_defaults(func=cmd_discover)

    # ── api-info ──
    p = sub.add_parser("api-info", help="Query supported API versions")
    p.set_defaults(func=cmd_api_info)

    # ── MONITOR (SSE) ──
    p = sub.add_parser("monitor", help="Stream real-time SSE events from GET /notify")
    p.add_argument("--ussi", default=None,
                   help="Filter events to a specific resource, e.g. 'nowplaying' or 'levels'")
    p.add_argument("--raw", action="store_true",
                   help="Print full JSON event instead of formatted output")
    p.set_defaults(func=cmd_monitor)

    # ── SYSTEM ──
    p = sub.add_parser("system-info", help="Device information")
    p.set_defaults(func=cmd_system_info)

    p = sub.add_parser("system-usage", help="System usage statistics")
    p.set_defaults(func=cmd_system_usage)

    p = sub.add_parser("system-datetime", help="Device date/time")
    p.set_defaults(func=cmd_system_datetime)

    p = sub.add_parser("system-reboot", help="Reboot device")
    p.set_defaults(func=cmd_system_reboot)

    p = sub.add_parser("system-keepawake", help="Prevent standby (kick)")
    p.set_defaults(func=cmd_system_keepawake)

    p = sub.add_parser("system-firstsetup", help="Mark first-time setup complete")
    p.add_argument("--complete", type=lambda x: x.lower() == "true", default=True)
    p.set_defaults(func=cmd_system_firstsetup)

    # ── POWER ──
    p = sub.add_parser("power-get", help="Get power state")
    p.set_defaults(func=cmd_power_get)

    p = sub.add_parser("power-set", help="Set power state (on/off/lona/loff)")
    p.add_argument("--state", required=True, choices=["on", "off", "lona", "loff"])
    p.set_defaults(func=cmd_power_set)

    p = sub.add_parser("power-server", help="Enable/disable server mode")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_power_server)

    p = sub.add_parser("power-timeout", help="Set standby timeout in minutes")
    p.add_argument("--minutes", type=int, required=True)
    p.set_defaults(func=cmd_power_timeout)

    # ── NOW PLAYING ──
    p = sub.add_parser("nowplaying", help="Get current playback state")
    p.set_defaults(func=cmd_nowplaying)

    p = sub.add_parser("play", help="Start playback")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("pause", help="Pause playback")
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("stop", help="Stop playback")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("resume", help="Resume playback")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("next", help="Skip to next track")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("prev", help="Previous track")
    p.set_defaults(func=cmd_prev)

    p = sub.add_parser("toggle", help="Toggle play/pause")
    p.set_defaults(func=cmd_toggle)

    p = sub.add_parser("seek", help="Seek to position in seconds")
    p.add_argument("--position", type=float, required=True, help="Position in seconds (converted to ms for API)")
    p.set_defaults(func=cmd_seek)

    p = sub.add_parser("repeat", help="Set repeat mode (0=off, 1=all, 2=one)")
    p.add_argument("--mode", type=int, required=True, choices=[0, 1, 2])
    p.set_defaults(func=cmd_repeat)

    p = sub.add_parser("shuffle", help="Set shuffle mode")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_shuffle)

    # ── VOLUME ──
    p = sub.add_parser("levels-get", help="Get volume/levels info")
    p.set_defaults(func=cmd_levels_get)

    p = sub.add_parser("levels-room", help="Get room levels")
    p.set_defaults(func=cmd_levels_room)

    p = sub.add_parser("levels-group", help="Get group levels")
    p.set_defaults(func=cmd_levels_group)

    p = sub.add_parser("levels-bluetooth", help="Get Bluetooth levels")
    p.set_defaults(func=cmd_levels_bluetooth)

    p = sub.add_parser("volume-set", help="Set volume level (0-100)")
    p.add_argument("--level", type=int, required=True)
    p.add_argument("--ussi", default=None, help="Target USSI (default: levels)")
    p.set_defaults(func=cmd_volume_set)

    p = sub.add_parser("mute", help="Mute audio")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_mute)

    p = sub.add_parser("unmute", help="Unmute audio")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_unmute)

    p = sub.add_parser("balance", help="Set balance")
    p.add_argument("--value", type=int, required=True, help="Balance value")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("volume-mode", help="Set volume mode (0=Variable, 1=Hybrid, 2=Fixed)")
    p.add_argument("--mode", type=int, required=True, choices=[0, 1, 2])
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_volume_mode)

    # ── INPUTS ──
    p = sub.add_parser("inputs-list", help="List all available inputs")
    p.set_defaults(func=cmd_inputs_list)

    p = sub.add_parser("input-details", help="Get details of an input")
    p.add_argument("--ussi", required=True, help="Input USSI (e.g. inputs/tidal)")
    p.set_defaults(func=cmd_input_details)

    p = sub.add_parser("input-select", help="Select/activate an input")
    p.add_argument("--ussi", required=True, help="Input USSI (e.g. inputs/spotify)")
    p.set_defaults(func=cmd_input_select)

    p = sub.add_parser("input-play", help="Play from an input")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_input_play)

    p = sub.add_parser("input-resume", help="Resume from an input")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_input_resume)

    p = sub.add_parser("input-rename", help="Rename an input")
    p.add_argument("--ussi", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_input_rename)

    p = sub.add_parser("input-disable", help="Enable/disable an input")
    p.add_argument("--ussi", required=True)
    p.add_argument("--disabled", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_input_disable)

    p = sub.add_parser("input-trim", help="Set input trim level")
    p.add_argument("--ussi", required=True)
    p.add_argument("--value", type=int, required=True)
    p.set_defaults(func=cmd_input_trim)

    p = sub.add_parser("input-sensitivity", help="Set input sensitivity")
    p.add_argument("--ussi", required=True)
    p.add_argument("--value", type=int, required=True)
    p.set_defaults(func=cmd_input_sensitivity)

    p = sub.add_parser("input-unity-gain", help="Set unity gain for input")
    p.add_argument("--ussi", required=True)
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_input_unity_gain)

    # ── OUTPUTS ──
    p = sub.add_parser("outputs-list", help="List all outputs")
    p.set_defaults(func=cmd_outputs_list)

    p = sub.add_parser("output-details", help="Get output details")
    p.add_argument("--ussi", required=True, help="Output USSI (e.g. outputs/analogue)")
    p.set_defaults(func=cmd_output_details)

    p = sub.add_parser("output-enabled", help="Enable/disable an output")
    p.add_argument("--ussi", required=True)
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_output_enabled)

    p = sub.add_parser("output-max-volume", help="Set maximum volume for output")
    p.add_argument("--ussi", required=True)
    p.add_argument("--value", type=int, required=True)
    p.set_defaults(func=cmd_output_max_volume)

    p = sub.add_parser("loudness", help="Set loudness compensation level")
    p.add_argument("--value", type=int, required=True)
    p.set_defaults(func=cmd_loudness)

    p = sub.add_parser("loudness-enabled", help="Enable/disable loudness compensation")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_loudness_enabled)

    p = sub.add_parser("room-position", help="Set room position (0=floorstanding, 1=wall, 2=corner)")
    p.add_argument("--position", type=int, required=True, choices=[0, 1, 2])
    p.set_defaults(func=cmd_room_position)

    p = sub.add_parser("dsd-mode", help="Set DSD output mode for digital output")
    p.add_argument("--mode", type=int, required=True)
    p.set_defaults(func=cmd_dsd_mode)

    # ── BLUETOOTH ──
    p = sub.add_parser("bt-pair", help="Start Bluetooth pairing")
    p.set_defaults(func=cmd_bt_pair)

    p = sub.add_parser("bt-stop-pair", help="Stop Bluetooth pairing")
    p.set_defaults(func=cmd_bt_stop_pair)

    p = sub.add_parser("bt-clear-history", help="Clear Bluetooth pairing history")
    p.set_defaults(func=cmd_bt_clear_history)

    p = sub.add_parser("bt-drop", help="Disconnect current Bluetooth device")
    p.set_defaults(func=cmd_bt_drop)

    p = sub.add_parser("bt-forget", help="Forget all Bluetooth pairings")
    p.set_defaults(func=cmd_bt_forget)

    p = sub.add_parser("bt-auto-pair", help="Enable/disable automatic Bluetooth pairing")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_bt_auto_pair)

    # ── STREAMING SERVICES ──
    p = sub.add_parser("qobuz-login", help="Login to Qobuz")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.set_defaults(func=cmd_qobuz_login)

    p = sub.add_parser("qobuz-quality", help="Set Qobuz streaming quality")
    p.add_argument("--quality", required=True,
                   help="5=320kbps, 6=FLAC 16bit, 7=FLAC 24bit, 27=FLAC 24bit HiRes")
    p.set_defaults(func=cmd_qobuz_quality)

    p = sub.add_parser("qobuz-logout", help="Logout from Qobuz")
    p.set_defaults(func=cmd_qobuz_logout)

    p = sub.add_parser("tidal-login", help="Login to Tidal via OAuth tokens")
    p.add_argument("--access-token", required=True)
    p.add_argument("--refresh-token", required=True)
    p.add_argument("--oauth-ident", default=None)
    p.set_defaults(func=cmd_tidal_login)

    p = sub.add_parser("tidal-logout", help="Logout from Tidal")
    p.set_defaults(func=cmd_tidal_logout)

    p = sub.add_parser("spotify-bitrate", help="Set Spotify bitrate (normal/high/very_high)")
    p.add_argument("--bitrate", required=True, choices=["normal", "high", "very_high"])
    p.set_defaults(func=cmd_spotify_bitrate)

    p = sub.add_parser("spotify-gain-norm", help="Enable/disable Spotify gain normalisation")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_spotify_gain_norm)

    p = sub.add_parser("spotify-presets", help="List Spotify presets")
    p.set_defaults(func=cmd_spotify_presets)

    p = sub.add_parser("spotify-preset-save", help="Save current Spotify track as preset")
    p.add_argument("--preset-id", type=int, required=True)
    p.set_defaults(func=cmd_spotify_preset_save)

    # ── IRADIO ──
    p = sub.add_parser("iradio-browse", help="Browse iRadio stations")
    p.set_defaults(func=cmd_iradio_browse)

    p = sub.add_parser("iradio-scan", help="Scan for radio stations")
    p.add_argument("--ussi", default=None, help="USSI (default: inputs/radio)")
    p.set_defaults(func=cmd_iradio_scan)

    p = sub.add_parser("iradio-scan-up", help="Scan up for station (FM/DAB)")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_iradio_scan_up)

    p = sub.add_parser("iradio-scan-down", help="Scan down for station (FM/DAB)")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_iradio_scan_down)

    p = sub.add_parser("iradio-scan-stop", help="Stop radio scanning")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_iradio_scan_stop)

    p = sub.add_parser("iradio-step-up", help="Step up one channel")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_iradio_step_up)

    p = sub.add_parser("iradio-step-down", help="Step down one channel")
    p.add_argument("--ussi", default=None)
    p.set_defaults(func=cmd_iradio_step_down)

    p = sub.add_parser("iradio-play", help="Play a radio station by USSI/key")
    p.add_argument("--ussi", required=True)
    p.add_argument("--station-key", default=None)
    p.set_defaults(func=cmd_iradio_play)

    p = sub.add_parser("iradio-add-station", help="Add user-defined radio station")
    p.add_argument("--name", required=True)
    p.add_argument("--station-key", required=True, help="Stream URL")
    p.add_argument("--genre", default=None)
    p.add_argument("--location", default=None)
    p.add_argument("--bitrate", type=int, default=None)
    p.add_argument("--artwork", default=None)
    p.set_defaults(func=cmd_iradio_add_station)

    p = sub.add_parser("iradio-delete-station", help="Delete a user radio station")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_iradio_delete_station)

    # ── PLAY QUEUE ──
    p = sub.add_parser("playqueue-get", help="Get play queue")
    p.set_defaults(func=cmd_playqueue_get)

    p = sub.add_parser("playqueue-clear", help="Clear play queue")
    p.set_defaults(func=cmd_playqueue_clear)

    p = sub.add_parser("playqueue-move", help="Move track in play queue")
    p.add_argument("--what", required=True, help="Track USSI to move")
    p.add_argument("--where", default=None, help="Target USSI/position")
    p.set_defaults(func=cmd_playqueue_move)

    p = sub.add_parser("playqueue-set-current", help="Set current track in queue")
    p.add_argument("--ussi", required=True, help="Track USSI to play")
    p.set_defaults(func=cmd_playqueue_set_current)

    p = sub.add_parser("playqueue-track", help="Get play queue track details")
    p.add_argument("--track-ussi", required=True)
    p.set_defaults(func=cmd_playqueue_track)

    # ── FAVOURITES / PRESETS ──
    p = sub.add_parser("favourites-list", help="List favourites/presets")
    p.add_argument("--presets-only", action="store_true")
    p.add_argument("--available-only", action="store_true")
    p.set_defaults(func=cmd_favourites_list)

    p = sub.add_parser("favourite-details", help="Get favourite details")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_favourite_details)

    p = sub.add_parser("favourite-play", help="Play a favourite")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_favourite_play)

    p = sub.add_parser("favourite-delete", help="Delete a favourite")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_favourite_delete)

    p = sub.add_parser("preset-assign", help="Assign current source to preset slot")
    p.add_argument("--ussi", required=True)
    p.add_argument("--preset-id", type=int, required=True)
    p.set_defaults(func=cmd_preset_assign)

    p = sub.add_parser("preset-deassign", help="Remove preset assignment")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_preset_deassign)

    p = sub.add_parser("preset-move", help="Move preset position")
    p.add_argument("--from-pos", type=int, required=True)
    p.add_argument("--to-pos", type=int, required=True)
    p.set_defaults(func=cmd_preset_move)

    # ── MULTIROOM ──
    p = sub.add_parser("multiroom-get", help="Get multiroom state")
    p.set_defaults(func=cmd_multiroom_get)

    p = sub.add_parser("multiroom-add", help="Add device to multiroom group")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_multiroom_add)

    p = sub.add_parser("multiroom-remove", help="Remove device from multiroom group")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_multiroom_remove)

    # ── CD ──
    p = sub.add_parser("cd-info", help="Get CD player status")
    p.set_defaults(func=cmd_cd_info)

    p = sub.add_parser("cd-eject", help="Eject CD")
    p.set_defaults(func=cmd_cd_eject)

    p = sub.add_parser("cd-play", help="Play CD (first track)")
    p.set_defaults(func=cmd_cd_play)

    p = sub.add_parser("cd-insert-action", help="Set CD insert action (0=nothing, 1=play, 2=rip)")
    p.add_argument("--action", type=int, required=True, choices=[0, 1, 2])
    p.set_defaults(func=cmd_cd_insert_action)

    # ── ALARMS / SLEEP ──
    p = sub.add_parser("alarm-list", help="List all alarms")
    p.set_defaults(func=cmd_alarm_list)

    p = sub.add_parser("alarm-details", help="Get alarm details")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_alarm_details)

    p = sub.add_parser("alarm-set", help="Create/update alarm")
    p.add_argument("--name", required=True)
    p.add_argument("--source", required=True, help="Source USSI (e.g. inputs/radio)")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--minutes", type=int, default=None)
    p.add_argument("--days", type=int, default=None, help="Recurrence bitmask (1=Mon ... 64=Sun)")
    p.add_argument("--enabled", type=lambda x: x.lower() == "true", default=None)
    p.set_defaults(func=cmd_alarm_set)

    p = sub.add_parser("alarm-enable", help="Enable/disable alarm")
    p.add_argument("--ussi", required=True)
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_alarm_enable)

    p = sub.add_parser("alarm-delete", help="Delete alarm")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_alarm_delete)

    p = sub.add_parser("sleep-start", help="Start sleep timer")
    p.add_argument("--minutes", type=int, required=True)
    p.set_defaults(func=cmd_sleep_start)

    p = sub.add_parser("sleep-stop", help="Cancel sleep timer")
    p.set_defaults(func=cmd_sleep_stop)

    # ── NETWORK ──
    p = sub.add_parser("network-get", help="Get network configuration")
    p.set_defaults(func=cmd_network_get)

    p = sub.add_parser("network-hostname", help="Set device hostname")
    p.add_argument("--hostname", required=True)
    p.set_defaults(func=cmd_network_hostname)

    p = sub.add_parser("network-scan-wifi", help="Scan for WiFi networks")
    p.set_defaults(func=cmd_network_scan_wifi)

    p = sub.add_parser("network-setup-wifi", help="Connect to WiFi network")
    p.add_argument("--ssid", required=True)
    p.add_argument("--key", required=True, help="WiFi password")
    p.set_defaults(func=cmd_network_setup_wifi)

    p = sub.add_parser("network-dhcp", help="Enable DHCP on network interface")
    p.add_argument("--iface", required=True,
                   help="Interface path (e.g. network/ethernet, network/wireless)")
    p.set_defaults(func=cmd_network_dhcp)

    p = sub.add_parser("network-static", help="Set static IP on network interface")
    p.add_argument("--iface", required=True,
                   help="Interface path (e.g. network/ethernet)")
    p.add_argument("--ip", required=True)
    p.add_argument("--netmask", required=True)
    p.add_argument("--gateway", required=True)
    p.add_argument("--dns1", required=True)
    p.add_argument("--dns2", default=None)
    p.set_defaults(func=cmd_network_static)

    p = sub.add_parser("network-samba", help="Enable/disable Samba SMB1")
    p.add_argument("--enable", type=lambda x: x.lower() == "true", required=True)
    p.set_defaults(func=cmd_network_samba)

    # ── UPDATE ──
    p = sub.add_parser("update-get", help="Get firmware update status")
    p.set_defaults(func=cmd_update_get)

    p = sub.add_parser("update-check", help="Check for firmware updates")
    p.set_defaults(func=cmd_update_check)

    p = sub.add_parser("update-start", help="Start firmware update")
    p.set_defaults(func=cmd_update_start)

    # ── BROWSE (local library) ──
    p = sub.add_parser("browse-tracks", help="Browse local library tracks")
    p.add_argument("--offset", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_browse_tracks)

    p = sub.add_parser("browse-albums", help="Browse local library albums")
    p.add_argument("--offset", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_browse_albums)

    p = sub.add_parser("browse-artists", help="Browse local library artists")
    p.add_argument("--offset", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_browse_artists)

    p = sub.add_parser("browse-play", help="Play item from library by USSI")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_browse_play)

    p = sub.add_parser("browse-play-next", help="Play library item next")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_browse_play_next)

    p = sub.add_parser("browse-play-last", help="Add library item to end of queue")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_browse_play_last)

    p = sub.add_parser("browse-refresh", help="Refresh library entry by USSI")
    p.add_argument("--ussi", required=True)
    p.set_defaults(func=cmd_browse_refresh)

    args = parser.parse_args()
    # --host is required for all commands except 'discover'
    if args.command != "discover" and not args.host:
        parser.error("--host is required for this command")
    args.func(args)


if __name__ == "__main__":
    main()
