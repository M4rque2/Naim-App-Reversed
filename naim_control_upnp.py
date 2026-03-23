#!/usr/bin/env python3
"""
Naim Streamer Control CLI — UPnP/DLNA (legacy devices)
Reverse-engineered from the Naim App Android application.
Protocol: UPnP/DLNA SOAP over HTTP (typically port 8080)

For legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, etc.) that
use standard UPnP/DLNA AVTransport and RenderingControl services.
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

UPNP_DEFAULT_PORT = 8080

UPNP_AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
UPNP_RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
UPNP_AV_TRANSPORT_PATH = "/AVTransport/ctrl"
UPNP_RENDERING_CONTROL_PATH = "/RenderingControl/ctrl"

# ─────────────────────────────────────────────
# DEVICE DISCOVERY
# ─────────────────────────────────────────────
# Legacy Naim devices are discovered via SSDP and optionally mDNS.
# Unlike newer devices, they do NOT have a REST API on port 15081.

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
    """Discover legacy Naim UPnP devices on the local network."""
    timeout = args.timeout if hasattr(args, "timeout") else 5
    devices = {}  # ip -> info dict

    print(f"Scanning for legacy Naim UPnP devices (timeout={timeout}s)...\n")

    # Run SSDP and mDNS in parallel
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

    if not devices:
        print("No Naim UPnP devices found on the network.")
        return

    print(f"Found {len(devices)} Naim device(s):\n")
    for i, (ip, dev) in enumerate(devices.items(), 1):
        model = dev.get("modelName", "Unknown")
        name = dev.get("friendlyName") or dev.get("name") or model
        source = dev.get("source", "")
        print(f"  [{i}] {name}")
        print(f"      IP:       {ip}")
        print(f"      Model:    {model}")
        if dev.get("serialNumber"):
            print(f"      Serial:   {dev['serialNumber']}")
        print(f"      Protocol: UPnP/DLNA")
        print(f"      Found via: {source}")
        print()


# ─────────────────────────────────────────────
# UPnP / DLNA SOAP CLIENT
# ─────────────────────────────────────────────

def _build_soap_envelope(service_type, action, args=None):
    """Build a SOAP XML envelope for a UPnP action."""
    args = args or {}
    arg_xml = ""
    for k, v in args.items():
        arg_xml += f"<{k}>{v}</{k}>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{service_type}">'
        f"{arg_xml}"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )


def _soap_request(host, port, path, service, action, args=None):
    """Send a SOAP POST request and return the parsed response dict."""
    envelope = _build_soap_envelope(service, action, args)
    url = f"http://{host}:{port}{path}"
    data = envelope.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", 'text/xml; charset="utf-8"')
    req.add_header("SOAPAction", f'"{service}#{action}"')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
            return _parse_soap_response(xml_data, action)
    except urllib.error.HTTPError as e:
        raw = e.read()
        fault = _parse_soap_fault(raw)
        if fault:
            print(f"UPnP SOAP fault: {fault}", file=sys.stderr)
        else:
            print(f"HTTP {e.code} {e.reason}: {raw.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _parse_soap_response(xml_data, action):
    """Extract output arguments from a SOAP response envelope."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return xml_data.decode(errors="replace")
    # Find the action response element (any namespace)
    body = None
    for el in root.iter():
        if el.tag.endswith("}Body") or el.tag == "Body":
            body = el
            break
    if body is None:
        body = root
    result = {}
    for resp_el in body:
        # The response element is e.g. <u:PlayResponse ...>
        for child in resp_el:
            tag = child.tag
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            result[tag] = child.text or ""
    return result


def _parse_soap_fault(xml_data):
    """Parse a UPnP SOAP fault response and return a description string."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None
    return _parse_soap_fault_element(root)


def _parse_soap_fault_element(root):
    """Extract fault details from a parsed SOAP XML tree."""
    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "UPnPError" or tag == "errorDescription":
            desc_el = None
            code_el = None
            if tag == "UPnPError":
                for child in el:
                    ctag = child.tag
                    if "}" in ctag:
                        ctag = ctag.split("}", 1)[1]
                    if ctag == "errorCode":
                        code_el = child
                    elif ctag == "errorDescription":
                        desc_el = child
                code = code_el.text if code_el is not None else "?"
                desc = desc_el.text if desc_el is not None else "unknown"
                return f"[{code}] {desc}"
            else:
                return el.text or "unknown error"
    # Fall back to faultstring
    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "faultstring":
            return el.text or "unknown fault"
    return None


def _discover_upnp_services(host, port):
    """Fetch description.xml and extract control URLs for known services.
    Falls back to hardcoded defaults if discovery fails."""
    services = {
        UPNP_AV_TRANSPORT: UPNP_AV_TRANSPORT_PATH,
        UPNP_RENDERING_CONTROL: UPNP_RENDERING_CONTROL_PATH,
    }
    url = f"http://{host}:{port}/description.xml"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
    except Exception:
        return services
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return services
    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "service":
            stype = None
            ctrl = None
            for child in el:
                ctag = child.tag
                if "}" in ctag:
                    ctag = ctag.split("}", 1)[1]
                if ctag == "serviceType":
                    stype = child.text
                elif ctag == "controlURL":
                    ctrl = child.text
            if stype and ctrl:
                if stype in services:
                    services[stype] = ctrl
    return services


def pretty(data):
    """Display a SOAP response as key-value pairs."""
    if isinstance(data, dict):
        if not data:
            print("(empty response)")
            return
        max_key = max(len(k) for k in data)
        for k, v in data.items():
            print(f"  {k:<{max_key}}  {v}")
    elif isinstance(data, str):
        print(data)
    else:
        print(data)


def pretty_json(data):
    """Display data as formatted JSON."""
    if isinstance(data, dict):
        print(json.dumps(data, indent=2))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


# ─────────────────────────────────────────────
# UPnP COMMAND HANDLERS
# ─────────────────────────────────────────────

def _upnp_get_paths(args):
    """Discover service control URLs for the target host."""
    return _discover_upnp_services(args.host, args.port)


def cmd_upnp_info(args):
    """Fetch and display UPnP description.xml from the device."""
    url = f"http://{args.host}:{args.port}/description.xml"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        print(xml_data.decode(errors="replace"))
        return

    info = {}
    ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
    device = root.find(".//upnp:device", ns)
    if device is None:
        device = root.find(".//{urn:schemas-upnp-org:device-1-0}device")
    if device is not None:
        for tag in ("friendlyName", "manufacturer", "modelName", "modelNumber",
                     "serialNumber", "UDN", "deviceType"):
            el = device.find(f"upnp:{tag}", ns)
            if el is None:
                el = device.find(f"{{urn:schemas-upnp-org:device-1-0}}{tag}")
            if el is not None and el.text:
                info[tag] = el.text.strip()

    services = _discover_upnp_services(args.host, args.port)
    info["services"] = {k: v for k, v in services.items()}

    pretty_json(info)


def cmd_upnp_play(args):
    """UPnP AVTransport Play"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Play",
                           {"InstanceID": "0", "Speed": "1"})
    pretty(result)


def cmd_upnp_pause(args):
    """UPnP AVTransport Pause"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Pause",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_stop(args):
    """UPnP AVTransport Stop"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Stop",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_next(args):
    """UPnP AVTransport Next"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Next",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_prev(args):
    """UPnP AVTransport Previous"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Previous",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_seek(args):
    """UPnP AVTransport Seek"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "Seek",
                           {"InstanceID": "0", "Unit": "REL_TIME",
                            "Target": args.target})
    pretty(result)


def cmd_upnp_transport_info(args):
    """UPnP AVTransport GetTransportInfo"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "GetTransportInfo",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_position_info(args):
    """UPnP AVTransport GetPositionInfo"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "GetPositionInfo",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_media_info(args):
    """UPnP AVTransport GetMediaInfo"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "GetMediaInfo",
                           {"InstanceID": "0"})
    pretty(result)


def cmd_upnp_volume_get(args):
    """UPnP RenderingControl GetVolume"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_RENDERING_CONTROL],
                           UPNP_RENDERING_CONTROL, "GetVolume",
                           {"InstanceID": "0", "Channel": "Master"})
    pretty(result)


def cmd_upnp_volume_set(args):
    """UPnP RenderingControl SetVolume"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_RENDERING_CONTROL],
                           UPNP_RENDERING_CONTROL, "SetVolume",
                           {"InstanceID": "0", "Channel": "Master",
                            "DesiredVolume": str(args.level)})
    pretty(result)


def cmd_upnp_mute(args):
    """UPnP RenderingControl SetMute (on)"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_RENDERING_CONTROL],
                           UPNP_RENDERING_CONTROL, "SetMute",
                           {"InstanceID": "0", "Channel": "Master",
                            "DesiredMute": "1"})
    pretty(result)


def cmd_upnp_unmute(args):
    """UPnP RenderingControl SetMute (off)"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_RENDERING_CONTROL],
                           UPNP_RENDERING_CONTROL, "SetMute",
                           {"InstanceID": "0", "Channel": "Master",
                            "DesiredMute": "0"})
    pretty(result)


def cmd_upnp_mute_get(args):
    """UPnP RenderingControl GetMute"""
    paths = _upnp_get_paths(args)
    result = _soap_request(args.host, args.port, paths[UPNP_RENDERING_CONTROL],
                           UPNP_RENDERING_CONTROL, "GetMute",
                           {"InstanceID": "0", "Channel": "Master"})
    pretty(result)


# ─────────────────────────────────────────────
# CLI SETUP
# ─────────────────────────────────────────────

def add_host_args(p):
    p.add_argument("--host", default=None, help="Naim device IP address")
    p.add_argument("--port", type=int, default=UPNP_DEFAULT_PORT,
                   help=f"UPnP port (default: {UPNP_DEFAULT_PORT})")


def main():
    parser = argparse.ArgumentParser(
        description="Naim Streamer Control CLI — UPnP/DLNA for legacy devices (SuperUniti, NDS, NDX, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s discover
  %(prog)s discover --timeout 10
  %(prog)s --host 192.168.1.21 info
  %(prog)s --host 192.168.1.21 play
  %(prog)s --host 192.168.1.21 pause
  %(prog)s --host 192.168.1.21 volume-get
  %(prog)s --host 192.168.1.21 volume-set --level 50
  %(prog)s --host 192.168.1.21 transport-info
  %(prog)s --host 192.168.1.21 position-info

For newer devices (Uniti series, Mu-so 2nd gen), use naim_control_rest.py instead.
        """,
    )
    add_host_args(parser)

    sub = parser.add_subparsers(title="commands", dest="command", required=True)

    # ── DISCOVER ──
    p = sub.add_parser("discover", help="Discover legacy Naim UPnP devices on the local network")
    p.add_argument("--timeout", type=int, default=5, help="Discovery timeout in seconds (default: 5)")
    p.set_defaults(func=cmd_discover)

    # ── DEVICE INFO ──
    p = sub.add_parser("info", help="Fetch device description.xml")
    p.set_defaults(func=cmd_upnp_info)

    # ── AVTransport ──
    p = sub.add_parser("play", help="Start playback (AVTransport)")
    p.set_defaults(func=cmd_upnp_play)

    p = sub.add_parser("pause", help="Pause playback (AVTransport)")
    p.set_defaults(func=cmd_upnp_pause)

    p = sub.add_parser("stop", help="Stop playback (AVTransport)")
    p.set_defaults(func=cmd_upnp_stop)

    p = sub.add_parser("next", help="Next track (AVTransport)")
    p.set_defaults(func=cmd_upnp_next)

    p = sub.add_parser("prev", help="Previous track (AVTransport)")
    p.set_defaults(func=cmd_upnp_prev)

    p = sub.add_parser("seek", help="Seek to position (AVTransport)")
    p.add_argument("--target", required=True, help="Target time in HH:MM:SS format")
    p.set_defaults(func=cmd_upnp_seek)

    p = sub.add_parser("transport-info", help="Get transport state (AVTransport)")
    p.set_defaults(func=cmd_upnp_transport_info)

    p = sub.add_parser("position-info", help="Get current position (AVTransport)")
    p.set_defaults(func=cmd_upnp_position_info)

    p = sub.add_parser("media-info", help="Get media info (AVTransport)")
    p.set_defaults(func=cmd_upnp_media_info)

    # ── RenderingControl ──
    p = sub.add_parser("volume-get", help="Get volume (RenderingControl)")
    p.set_defaults(func=cmd_upnp_volume_get)

    p = sub.add_parser("volume-set", help="Set volume (RenderingControl)")
    p.add_argument("--level", type=int, required=True, help="Volume level")
    p.set_defaults(func=cmd_upnp_volume_set)

    p = sub.add_parser("mute", help="Mute (RenderingControl)")
    p.set_defaults(func=cmd_upnp_mute)

    p = sub.add_parser("unmute", help="Unmute (RenderingControl)")
    p.set_defaults(func=cmd_upnp_unmute)

    p = sub.add_parser("mute-get", help="Get mute state (RenderingControl)")
    p.set_defaults(func=cmd_upnp_mute_get)

    args = parser.parse_args()
    # --host is required for all commands except 'discover'
    if args.command != "discover" and not args.host:
        parser.error("--host is required for this command")
    args.func(args)


if __name__ == "__main__":
    main()
