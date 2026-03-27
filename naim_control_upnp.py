#!/usr/bin/env python3
"""
Naim Streamer Control CLI — UPnP/DLNA Protocol
Reverse-engineered from the Naim App Android application.
Protocol: UPnP/DLNA SOAP over HTTP (typically port 8080)

For all Naim network streamers that support standard UPnP/DLNA services.
This includes both legacy devices (SuperUniti, NDS, NDX, UnitiQute) and
newer devices (Uniti series, Mu-so).

This protocol provides:
- Playback control (play, pause, stop, seek, next/prev)
- Volume and mute control
- Device discovery via SSDP
- Media browsing (ContentDirectory)
- Transport state queries

NOTE: For input switching on legacy devices, use naim_control_nstream.py instead.
      For full control on newer devices, use naim_control_rest.py instead.
"""

import argparse
import http.client
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


class UPnPError(Exception):
    """Raised when a UPnP SOAP request fails."""
    pass


UPNP_AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
UPNP_RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
UPNP_CONNECTION_MANAGER = "urn:schemas-upnp-org:service:ConnectionManager:1"
UPNP_CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:1"
UPNP_AV_TRANSPORT_PATH = "/AVTransport/ctrl"
UPNP_RENDERING_CONTROL_PATH = "/RenderingControl/ctrl"
UPNP_CONNECTION_MANAGER_PATH = "/ConnectionManager/ctrl"
UPNP_CONTENT_DIRECTORY_PATH = "/ContentDirectory/ctrl"

# Naim-proprietary service for input/source management (discovered via description.xml)
NAIM_INPUT_SERVICE = "urn:naim-audio-com:service:Inputs:1"
NAIM_INPUT_SERVICE_PATH = "/Inputs/ctrl"

# DM Holdings proprietary service for IR control and device configuration
# Found on legacy Naim devices (SuperUniti, etc.)
DM_HTML_PAGE_HANDLER = "urn:schemas-dm-holdings-com:service:X_HtmlPageHandler:1"
DM_HTML_PAGE_HANDLER_PATH = "/HtmlPageHandler/ctrl"

# ─────────────────────────────────────────────
# DEVICE DISCOVERY
# ─────────────────────────────────────────────

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


def cmd_discover(args):
    """Discover Naim UPnP devices on the local network."""
    timeout = args.timeout if hasattr(args, "timeout") else 5
    devices = {}  # ip -> info dict

    print(f"Scanning for Naim UPnP devices (timeout={timeout}s)...\n")

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
        print(f"      Protocol: UPnP/DLNA (port 8080)")
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
        # Escape XML special characters in values
        if isinstance(v, str):
            v = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
            raise UPnPError(f"UPnP SOAP fault: {fault}")
        else:
            raise UPnPError(f"HTTP {e.code} {e.reason}: {raw.decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise UPnPError(f"Connection error: {e.reason}")
    except http.client.RemoteDisconnected as e:
        raise UPnPError(f"Device closed connection - device may be in standby or rejected the format")


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


def _discover_upnp_services(host, port, discover_all=False):
    """Fetch description.xml and extract control URLs for known services.
    Falls back to hardcoded defaults if discovery fails.

    If discover_all=True, returns ALL services found in description.xml,
    not just the known ones."""
    services = {
        UPNP_AV_TRANSPORT: UPNP_AV_TRANSPORT_PATH,
        UPNP_RENDERING_CONTROL: UPNP_RENDERING_CONTROL_PATH,
        UPNP_CONNECTION_MANAGER: UPNP_CONNECTION_MANAGER_PATH,
        UPNP_CONTENT_DIRECTORY: UPNP_CONTENT_DIRECTORY_PATH,
        NAIM_INPUT_SERVICE: NAIM_INPUT_SERVICE_PATH,
        DM_HTML_PAGE_HANDLER: DM_HTML_PAGE_HANDLER_PATH,
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

    discovered = {}
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
                discovered[stype] = ctrl
                if stype in services:
                    services[stype] = ctrl

    if discover_all:
        # Merge discovered services with defaults
        return {**services, **discovered}
    return services


def _fetch_service_scpd(host, port, scpd_url):
    """Fetch the SCPD (Service Control Protocol Description) XML for a service.
    Returns a dict with action names and their arguments."""
    if not scpd_url.startswith("http"):
        scpd_url = f"http://{host}:{port}{scpd_url}"
    try:
        req = urllib.request.Request(scpd_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
    except Exception:
        return None
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None

    actions = {}
    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "action":
            action_name = None
            args = []
            for child in el:
                ctag = child.tag
                if "}" in ctag:
                    ctag = ctag.split("}", 1)[1]
                if ctag == "name":
                    action_name = child.text
                elif ctag == "argumentList":
                    for arg in child:
                        arg_name = None
                        arg_dir = None
                        for ac in arg:
                            actag = ac.tag
                            if "}" in actag:
                                actag = actag.split("}", 1)[1]
                            if actag == "name":
                                arg_name = ac.text
                            elif actag == "direction":
                                arg_dir = ac.text
                        if arg_name:
                            args.append({"name": arg_name, "direction": arg_dir})
            if action_name:
                actions[action_name] = args
    return actions


def _discover_all_services_with_scpd(host, port):
    """Discover all UPnP services and fetch their SCPD to list available actions."""
    url = f"http://{host}:{port}/description.xml"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
    except Exception:
        return {}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return {}

    services = {}
    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "service":
            stype = None
            ctrl = None
            scpd = None
            for child in el:
                ctag = child.tag
                if "}" in ctag:
                    ctag = ctag.split("}", 1)[1]
                if ctag == "serviceType":
                    stype = child.text
                elif ctag == "controlURL":
                    ctrl = child.text
                elif ctag == "SCPDURL":
                    scpd = child.text
            if stype:
                services[stype] = {
                    "controlURL": ctrl,
                    "SCPDURL": scpd,
                }
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
        raise UPnPError(f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise UPnPError(f"Connection error: {e.reason}")

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
# INPUT / SOURCE MANAGEMENT
# ─────────────────────────────────────────────

def _browse_content_directory(host, port, paths, object_id="0", browse_flag="BrowseDirectChildren"):
    """Browse the ContentDirectory at a given ObjectID.
    Returns a list of items with their metadata."""
    if UPNP_CONTENT_DIRECTORY not in paths:
        return None

    try:
        result = _soap_request(
            host, port, paths[UPNP_CONTENT_DIRECTORY],
            UPNP_CONTENT_DIRECTORY, "Browse",
            {
                "ObjectID": object_id,
                "BrowseFlag": browse_flag,
                "Filter": "*",
                "StartingIndex": "0",
                "RequestedCount": "100",
                "SortCriteria": "",
            })
    except UPnPError:
        return None

    # Parse the DIDL-Lite XML in the Result field
    didl_xml = result.get("Result", "")
    if not didl_xml:
        return {"items": [], "total": result.get("TotalMatches", "0")}

    items = _parse_didl_lite(didl_xml)
    return {
        "items": items,
        "total": result.get("TotalMatches", "0"),
        "returned": result.get("NumberReturned", "0"),
    }


def _parse_didl_lite(didl_xml):
    """Parse DIDL-Lite XML and extract item/container metadata."""
    items = []
    try:
        root = ET.fromstring(didl_xml)
    except ET.ParseError:
        return items

    ns = {
        "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
    }

    for el in root:
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        item = {
            "type": tag,  # "container" or "item"
            "id": el.get("id"),
            "parentID": el.get("parentID"),
            "restricted": el.get("restricted"),
        }

        # Extract common metadata
        for child in el:
            ctag = child.tag
            if "}" in ctag:
                ctag = ctag.split("}", 1)[1]

            if ctag == "title":
                item["title"] = child.text
            elif ctag == "class":
                item["class"] = child.text
            elif ctag == "res":
                # Resource URL (for playback)
                if "res" not in item:
                    item["res"] = []
                res_info = {
                    "url": child.text,
                    "protocolInfo": child.get("protocolInfo"),
                }
                item["res"].append(res_info)
            elif ctag == "albumArtURI":
                item["albumArtURI"] = child.text
            elif ctag == "artist":
                item["artist"] = child.text
            elif ctag == "album":
                item["album"] = child.text
            elif ctag == "genre":
                item["genre"] = child.text

        items.append(item)

    return items


def _get_protocol_info(host, port, paths):
    """Get supported protocols from ConnectionManager."""
    if UPNP_CONNECTION_MANAGER not in paths:
        return None

    try:
        result = _soap_request(
            host, port, paths[UPNP_CONNECTION_MANAGER],
            UPNP_CONNECTION_MANAGER, "GetProtocolInfo",
            {})
        return result
    except UPnPError:
        return None


def cmd_upnp_services(args):
    """List all UPnP services and their available actions."""
    services = _discover_all_services_with_scpd(args.host, args.port)
    if not services:
        print("No services found (device may not be reachable)")
        return

    print(f"UPnP Services on {args.host}:{args.port}:\n")
    for stype, info in services.items():
        print(f"  Service: {stype}")
        print(f"    Control URL: {info['controlURL']}")
        print(f"    SCPD URL:    {info['SCPDURL']}")

        if args.verbose and info['SCPDURL']:
            actions = _fetch_service_scpd(args.host, args.port, info['SCPDURL'])
            if actions:
                print(f"    Actions:")
                for action_name, action_args in actions.items():
                    arg_strs = [f"{a['name']}({a['direction']})" for a in action_args]
                    print(f"      - {action_name}({', '.join(arg_strs)})")
        print()


def cmd_upnp_inputs_list(args):
    """List available inputs by browsing ContentDirectory."""
    paths = _upnp_get_paths(args)

    # First, try to browse the root to find available containers
    root_browse = _browse_content_directory(args.host, args.port, paths, "0")
    if root_browse is None:
        print("ContentDirectory service not available on this device.")
        print("")
        print("This is a legacy Naim device (e.g., SuperUniti) that does not expose")
        print("input information via standard UPnP ContentDirectory service.")
        print("")
        print("For input switching on legacy devices, use naim_control_nstream.py:")
        print("  ./naim_control_nstream.py --host <ip> set-input --input DIGITAL2")
        print("  ./naim_control_nstream.py --host <ip> inputs  # List valid inputs")
        print("")
        print("What this CLI CAN do on your device:")
        print("  - Control playback (play, pause, stop, next, prev)")
        print("  - Control volume (volume-get, volume-set, mute, unmute)")
        print("  - View current transport state (transport-info, position-info)")
        print("  - View device info (info, services)")
        return

    print(f"Available sources on {args.host}:\n")

    # Browse root containers
    for item in root_browse.get("items", []):
        item_type = "[D]" if item["type"] == "container" else "[F]"
        item_id = item.get("id", "?")
        title = item.get("title", "Unknown")
        item_class = item.get("class", "")

        print(f"  {item_type} [{item_id}] {title}")
        if item_class:
            print(f"       Class: {item_class}")

        # If it's a container, optionally browse its children
        if item["type"] == "container" and args.recursive:
            children = _browse_content_directory(args.host, args.port, paths, item_id)
            if children and children.get("items"):
                for child in children["items"]:
                    child_type = "[D]" if child["type"] == "container" else "[F]"
                    child_id = child.get("id", "?")
                    child_title = child.get("title", "Unknown")
                    print(f"       {child_type} [{child_id}] {child_title}")


def cmd_upnp_input_browse(args):
    """Browse a specific input/container by ObjectID."""
    paths = _upnp_get_paths(args)

    result = _browse_content_directory(args.host, args.port, paths, args.object_id)
    if result is None:
        print("ContentDirectory service not available or browse failed.")
        return

    print(f"Contents of ObjectID '{args.object_id}':")
    print(f"  Total: {result.get('total', '?')} items\n")

    for item in result.get("items", []):
        item_type = "[D]" if item["type"] == "container" else "[F]"
        item_id = item.get("id", "?")
        title = item.get("title", "Unknown")
        item_class = item.get("class", "")

        print(f"  {item_type} [{item_id}] {title}")
        if item_class:
            print(f"       Class: {item_class}")

        # Show resource URLs for items
        if item.get("res"):
            for res in item["res"]:
                print(f"       URL: {res.get('url', 'N/A')}")
                if res.get("protocolInfo"):
                    print(f"       Protocol: {res['protocolInfo']}")


def cmd_upnp_input_select(args):
    """Select an input by setting the AVTransport URI."""
    paths = _upnp_get_paths(args)

    # If user provided a direct URI, use it
    uri = args.uri

    # If user provided an ObjectID, browse it to get the resource URI
    if args.object_id and not uri:
        result = _browse_content_directory(
            args.host, args.port, paths, args.object_id, "BrowseMetadata")
        if result and result.get("items"):
            item = result["items"][0]
            if item.get("res"):
                uri = item["res"][0].get("url")
                print(f"Selected: {item.get('title', 'Unknown')}")
                print(f"URI: {uri}")

    if not uri:
        print("No URI specified or could not resolve ObjectID to a playable URI.")
        print("Use --uri to specify a direct URI or --object-id to browse.")
        return

    # Build DIDL-Lite metadata (minimal required format)
    metadata = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        '<dc:title>Selected Input</dc:title>'
        '<upnp:class>object.item.audioItem</upnp:class>'
        f'<res>{uri}</res>'
        '</item>'
        '</DIDL-Lite>'
    )

    result = _soap_request(
        args.host, args.port, paths[UPNP_AV_TRANSPORT],
        UPNP_AV_TRANSPORT, "SetAVTransportURI",
        {
            "InstanceID": "0",
            "CurrentURI": uri,
            "CurrentURIMetaData": metadata,
        })
    pretty(result)

    # Optionally start playback immediately
    if args.play:
        _soap_request(
            args.host, args.port, paths[UPNP_AV_TRANSPORT],
            UPNP_AV_TRANSPORT, "Play",
            {"InstanceID": "0", "Speed": "1"})
        print("Playback started.")


def cmd_upnp_current_input(args):
    """Show the current input/source being played."""
    paths = _upnp_get_paths(args)

    print("Current Input/Source Status:")
    print("-" * 40)

    # Get media info to see current URI (for UPnP/network sources)
    result = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                           UPNP_AV_TRANSPORT, "GetMediaInfo",
                           {"InstanceID": "0"})

    current_uri = result.get("CurrentURI", "")
    metadata = result.get("CurrentURIMetaData", "")
    play_medium = result.get("PlayMedium", "")

    # Get transport state
    transport = _soap_request(args.host, args.port, paths[UPNP_AV_TRANSPORT],
                              UPNP_AV_TRANSPORT, "GetTransportInfo",
                              {"InstanceID": "0"})
    transport_state = transport.get('CurrentTransportState', 'Unknown')

    # Get connection info from ConnectionManager
    try:
        conn_ids = _soap_request(args.host, args.port, paths[UPNP_CONNECTION_MANAGER],
                                 UPNP_CONNECTION_MANAGER, "GetCurrentConnectionIDs",
                                 {})
        connection_id = conn_ids.get("ConnectionIDs", "").split(",")[0].strip()
        if connection_id:
            conn_info = _soap_request(args.host, args.port, paths[UPNP_CONNECTION_MANAGER],
                                      UPNP_CONNECTION_MANAGER, "GetCurrentConnectionInfo",
                                      {"ConnectionID": connection_id})
        else:
            conn_info = {}
    except UPnPError:
        conn_info = {}

    # Determine input type
    conn_direction = conn_info.get("Direction", "")
    conn_status = conn_info.get("Status", "")
    protocol_info = conn_info.get("ProtocolInfo", "")

    if current_uri:
        # UPnP/Network streaming
        print(f"  Source Type: Network/UPnP")
        print(f"  URI: {current_uri}")
        if metadata:
            items = _parse_didl_lite(metadata)
            if items:
                item = items[0]
                if item.get("title"):
                    print(f"  Title: {item['title']}")
                if item.get("artist"):
                    print(f"  Artist: {item['artist']}")
    elif conn_direction == "Input" and conn_status == "OK":
        # Physical input active (but we don't know which one)
        print(f"  Source Type: Physical Input (external)")
        print(f"  Note: Device is using a physical input (Digital/Analog)")
        print(f"        Use naim_control_nstream.py to switch inputs.")
    else:
        print(f"  Source Type: Unknown")
        print(f"  URI: (none)")

    print(f"  Transport: {transport_state}")
    print(f"  Play Medium: {play_medium or 'NONE'}")

    if conn_info:
        print(f"\nConnection Info:")
        print(f"  Direction: {conn_direction}")
        print(f"  Status: {conn_status}")
        if protocol_info:
            print(f"  Protocol: {protocol_info}")


def cmd_upnp_protocol_info(args):
    """Show supported protocols from ConnectionManager."""
    paths = _upnp_get_paths(args)
    result = _get_protocol_info(args.host, args.port, paths)

    if result is None:
        print("ConnectionManager service not available.")
        return

    print("Supported Protocols:\n")

    source = result.get("Source", "")
    sink = result.get("Sink", "")

    if source:
        print("Source (can send):")
        for proto in source.split(","):
            if proto.strip():
                print(f"  - {proto.strip()}")

    if sink:
        print("\nSink (can receive):")
        for proto in sink.split(","):
            if proto.strip():
                print(f"  - {proto.strip()}")


# ─────────────────────────────────────────────
# MEDIA SERVER BROWSING
# ─────────────────────────────────────────────

def _discover_media_servers(timeout=5):
    """Discover UPnP Media Servers on the local network.
    Returns a dict of location_url -> device_info."""
    servers = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    # Search specifically for MediaServer devices
    search_targets = [
        "urn:schemas-upnp-org:device:MediaServer:1",
        "urn:schemas-upnp-org:device:MediaServer:2",
        "urn:schemas-upnp-org:device:MediaServer:3",
        "urn:schemas-upnp-org:device:MediaServer:4",
    ]

    for st in search_targets:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_MULTICAST_ADDR}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 3\r\n"
            f"ST: {st}\r\n"
            "USER-AGENT: UPnP/1.0 NaimControl/1.0\r\n"
            "\r\n"
        )
        sock.sendto(msg.encode(), (SSDP_MULTICAST_ADDR, SSDP_PORT))

    deadline = time.time() + timeout
    locations = {}
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

    # Parse each discovered location to get device info
    for location, ip in locations.items():
        try:
            req = urllib.request.Request(location, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}

            device = root.find(".//upnp:device", ns)
            if device is None:
                device = root.find(".//{urn:schemas-upnp-org:device-1-0}device")
            if device is None:
                continue

            def txt(tag):
                el = device.find(f"upnp:{tag}", ns)
                if el is None:
                    el = device.find(f"{{urn:schemas-upnp-org:device-1-0}}{tag}")
                return el.text.strip() if el is not None and el.text else None

            device_type = txt("deviceType") or ""
            if "MediaServer" not in device_type:
                continue

            # Find ContentDirectory service
            content_dir_url = None
            for service in root.iter():
                if service.tag.endswith("}service") or service.tag == "service":
                    stype = None
                    ctrl = None
                    for child in service:
                        ctag = child.tag
                        if "}" in ctag:
                            ctag = ctag.split("}", 1)[1]
                        if ctag == "serviceType" and child.text:
                            if "ContentDirectory" in child.text:
                                stype = child.text
                        elif ctag == "controlURL":
                            ctrl = child.text
                    if stype and ctrl:
                        content_dir_url = ctrl
                        break

            # Extract base URL from location
            from urllib.parse import urlparse
            parsed = urlparse(location)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            servers[location] = {
                "ip": ip,
                "location": location,
                "base_url": base_url,
                "friendlyName": txt("friendlyName"),
                "manufacturer": txt("manufacturer"),
                "modelName": txt("modelName"),
                "UDN": txt("UDN"),
                "contentDirectoryURL": content_dir_url,
            }
        except Exception:
            continue

    return servers


def _browse_media_server(base_url, control_url, object_id="0", start_index=0, count=30):
    """Browse a UPnP Media Server's ContentDirectory.

    Args:
        base_url: The base URL of the server (e.g., http://192.168.1.100:9000)
        control_url: The ContentDirectory control URL (e.g., /ContentDirectory/control)
        object_id: The container ObjectID to browse (default: "0" = root)
        start_index: Starting index for pagination
        count: Number of items to request

    Returns:
        Dict with items, total count, etc.
    """
    # Build full control URL
    if control_url.startswith("http"):
        full_url = control_url
    else:
        full_url = f"{base_url}{control_url}"

    # Parse host and port from URL
    from urllib.parse import urlparse
    parsed = urlparse(full_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path

    # Default filter for audio metadata
    default_filter = (
        "dc:date,upnp:genre,res,res@duration,res@size,upnp:albumArtURI,"
        "upnp:album,upnp:artist,upnp:author,dc:creator,upnp:originalTrackNumber"
    )

    try:
        result = _soap_request(
            host, port, path,
            UPNP_CONTENT_DIRECTORY, "Browse",
            {
                "ObjectID": object_id,
                "BrowseFlag": "BrowseDirectChildren",
                "Filter": default_filter,
                "StartingIndex": str(start_index),
                "RequestedCount": str(count),
                "SortCriteria": "",
            })
    except UPnPError as e:
        return {"error": str(e), "items": []}

    # Parse the DIDL-Lite XML in the Result field
    didl_xml = result.get("Result", "")
    if not didl_xml:
        return {
            "items": [],
            "total": result.get("TotalMatches", "0"),
            "returned": result.get("NumberReturned", "0"),
        }

    items = _parse_didl_lite(didl_xml)
    return {
        "items": items,
        "total": result.get("TotalMatches", "0"),
        "returned": result.get("NumberReturned", "0"),
        "updateID": result.get("UpdateID", "0"),
    }


def _search_media_server(base_url, control_url, container_id="0", search_criteria="*",
                         start_index=0, count=30):
    """Search a UPnP Media Server's ContentDirectory.

    Args:
        base_url: The base URL of the server
        control_url: The ContentDirectory control URL
        container_id: The container to search within (default: "0" = all)
        search_criteria: UPnP search criteria string
        start_index: Starting index for pagination
        count: Number of items to request

    Returns:
        Dict with items, total count, etc.
    """
    # Build full control URL
    if control_url.startswith("http"):
        full_url = control_url
    else:
        full_url = f"{base_url}{control_url}"

    from urllib.parse import urlparse
    parsed = urlparse(full_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path

    default_filter = (
        "dc:date,upnp:genre,res,res@duration,res@size,upnp:albumArtURI,"
        "upnp:album,upnp:artist,upnp:author,dc:creator,upnp:originalTrackNumber"
    )

    try:
        result = _soap_request(
            host, port, path,
            UPNP_CONTENT_DIRECTORY, "Search",
            {
                "ContainerID": container_id,
                "SearchCriteria": search_criteria,
                "Filter": default_filter,
                "StartingIndex": str(start_index),
                "RequestedCount": str(count),
                "SortCriteria": "",
            })
    except UPnPError as e:
        return {"error": str(e), "items": []}

    didl_xml = result.get("Result", "")
    if not didl_xml:
        return {
            "items": [],
            "total": result.get("TotalMatches", "0"),
            "returned": result.get("NumberReturned", "0"),
        }

    items = _parse_didl_lite(didl_xml)
    return {
        "items": items,
        "total": result.get("TotalMatches", "0"),
        "returned": result.get("NumberReturned", "0"),
    }


def cmd_media_servers(args):
    """Discover UPnP Media Servers on the local network."""
    timeout = args.timeout if hasattr(args, "timeout") else 5
    print(f"Scanning for UPnP Media Servers (timeout={timeout}s)...\n")

    servers = _discover_media_servers(timeout)

    if not servers:
        print("No UPnP Media Servers found on the network.")
        print("\nMake sure you have a DLNA/UPnP server running, such as:")
        print("  - MiniDLNA / ReadyMedia")
        print("  - Plex Media Server")
        print("  - Jellyfin")
        print("  - Roon Server")
        print("  - Asset UPnP")
        print("  - Synology Media Server")
        return

    print(f"Found {len(servers)} Media Server(s):\n")
    for i, (location, info) in enumerate(servers.items(), 1):
        name = info.get("friendlyName") or info.get("modelName") or "Unknown"
        manufacturer = info.get("manufacturer") or ""
        ip = info.get("ip", "?")
        udn = info.get("UDN", "")

        print(f"  [{i}] {name}")
        print(f"      IP: {ip}")
        if manufacturer:
            print(f"      Manufacturer: {manufacturer}")
        print(f"      Location: {location}")
        if info.get("contentDirectoryURL"):
            print(f"      ContentDirectory: {info['contentDirectoryURL']}")
        print()


def cmd_server_browse(args):
    """Browse a UPnP Media Server by its IP address."""
    server_ip = args.server
    object_id = args.object_id
    start = args.start
    count = args.count

    print(f"Discovering Media Server at {server_ip}...")

    # First discover the server to get its ContentDirectory URL
    servers = _discover_media_servers(timeout=3)

    # Find the server matching the IP
    server_info = None
    for loc, info in servers.items():
        if info.get("ip") == server_ip:
            server_info = info
            break

    if not server_info:
        print(f"No Media Server found at {server_ip}")
        print("Use 'media-servers' command to discover available servers.")
        return

    base_url = server_info.get("base_url")
    control_url = server_info.get("contentDirectoryURL")

    if not control_url:
        print("Server does not expose ContentDirectory service.")
        return

    server_name = server_info.get("friendlyName") or server_ip
    print(f"Browsing '{server_name}' (ObjectID: {object_id})\n")

    result = _browse_media_server(base_url, control_url, object_id, start, count)

    if result.get("error"):
        print(f"Error: {result['error']}")
        return

    total = result.get("total", "?")
    returned = result.get("returned", "?")
    print(f"Items {start+1}-{start+int(returned)} of {total}:\n")

    for item in result.get("items", []):
        item_type = "[D]" if item["type"] == "container" else "[F]"
        item_id = item.get("id", "?")
        title = item.get("title", "Unknown")

        print(f"  {item_type} [{item_id}] {title}")

        # Show metadata for tracks
        if item["type"] == "item":
            if item.get("artist"):
                print(f"       Artist: {item['artist']}")
            if item.get("album"):
                print(f"       Album: {item['album']}")
            if item.get("res"):
                res = item["res"][0]
                url = res.get("url", "")
                protocol = res.get("protocolInfo", "")
                # Truncate long URLs
                if len(url) > 60:
                    url = url[:57] + "..."
                print(f"       URL: {url}")

    if int(returned) < int(total):
        next_start = start + int(returned)
        print(f"\n  (Use --start {next_start} to see more)")


def cmd_server_search(args):
    """Search a UPnP Media Server for content."""
    server_ip = args.server
    query = args.query

    print(f"Discovering Media Server at {server_ip}...")

    servers = _discover_media_servers(timeout=3)
    server_info = None
    for loc, info in servers.items():
        if info.get("ip") == server_ip:
            server_info = info
            break

    if not server_info:
        print(f"No Media Server found at {server_ip}")
        return

    base_url = server_info.get("base_url")
    control_url = server_info.get("contentDirectoryURL")

    if not control_url:
        print("Server does not expose ContentDirectory service.")
        return

    # Build UPnP search criteria
    # Common search criteria examples:
    # - dc:title contains "keyword"
    # - upnp:artist contains "artist"
    # - upnp:album contains "album"
    # - upnp:class derivedfrom "object.item.audioItem"
    search_criteria = f'dc:title contains "{query}" or upnp:artist contains "{query}" or upnp:album contains "{query}"'

    server_name = server_info.get("friendlyName") or server_ip
    print(f"Searching '{server_name}' for: {query}\n")

    result = _search_media_server(base_url, control_url, "0", search_criteria, 0, 50)

    if result.get("error"):
        # Search might not be supported, fall back to message
        print(f"Search not supported or failed: {result['error']}")
        print("\nTry browsing instead: server-browse --server <ip>")
        return

    total = result.get("total", "0")
    print(f"Found {total} result(s):\n")

    for item in result.get("items", []):
        item_type = "[D]" if item["type"] == "container" else "[F]"
        item_id = item.get("id", "?")
        title = item.get("title", "Unknown")

        print(f"  {item_type} [{item_id}] {title}")
        if item.get("artist"):
            print(f"       Artist: {item['artist']}")
        if item.get("album"):
            print(f"       Album: {item['album']}")


def cmd_server_play(args):
    """Play a track from a UPnP Media Server on a Naim device.

    This command:
    1. Gets the track's resource URL from the Media Server
    2. Sets it as the AVTransport URI on the Naim renderer
    3. Starts playback
    """
    server_ip = args.server
    object_id = args.object_id
    renderer_ip = args.host
    renderer_port = args.port

    if not renderer_ip:
        print("Error: --host is required to specify the Naim renderer")
        return

    print(f"Getting track info from Media Server at {server_ip}...")

    # Discover the server
    servers = _discover_media_servers(timeout=3)
    server_info = None
    for loc, info in servers.items():
        if info.get("ip") == server_ip:
            server_info = info
            break

    if not server_info:
        print(f"No Media Server found at {server_ip}")
        return

    base_url = server_info.get("base_url")
    control_url = server_info.get("contentDirectoryURL")

    if not control_url:
        print("Server does not expose ContentDirectory service.")
        return

    # Build full control URL
    if control_url.startswith("http"):
        full_url = control_url
    else:
        full_url = f"{base_url}{control_url}"

    from urllib.parse import urlparse
    parsed = urlparse(full_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path

    # Get metadata for the specific object
    try:
        result = _soap_request(
            host, port, path,
            UPNP_CONTENT_DIRECTORY, "Browse",
            {
                "ObjectID": object_id,
                "BrowseFlag": "BrowseMetadata",
                "Filter": "*",
                "StartingIndex": "0",
                "RequestedCount": "1",
                "SortCriteria": "",
            })
    except UPnPError as e:
        print(f"Error browsing server: {e}")
        return

    didl_xml = result.get("Result", "")
    if not didl_xml:
        print("No item found with that ObjectID")
        return

    items = _parse_didl_lite(didl_xml)
    if not items:
        print("Could not parse item metadata")
        return

    item = items[0]
    title = item.get("title", "Unknown")
    artist = item.get("artist", "")

    if not item.get("res"):
        print(f"Item '{title}' has no playable resource URL")
        if item["type"] == "container":
            print("This is a container (folder), not a playable item.")
            print(f"Use: server-browse --server {server_ip} --object-id {object_id}")
        return

    res_info = item["res"][0]
    resource_url = res_info.get("url")
    protocol_info = res_info.get("protocolInfo", "")
    sample_freq = res_info.get("sampleFrequency")

    print(f"Track: {title}")
    if artist:
        print(f"Artist: {artist}")
    print(f"URL: {resource_url}")
    if protocol_info:
        print(f"Format: {protocol_info}")
    if sample_freq:
        print(f"Sample Rate: {sample_freq} Hz")

    # Check format compatibility for SuperUniti
    url_lower = resource_url.lower() if resource_url else ""
    is_dsd = ".dsf" in url_lower or ".dff" in url_lower or "dsd" in protocol_info.lower()

    if is_dsd and sample_freq:
        try:
            freq = int(sample_freq)
            # DSD64 = 2.8MHz, DSD128 = 5.6MHz, DSD256 = 11.2MHz+
            if freq > 6144000:  # Above DSD128
                print(f"\n[WARNING] This DSD file ({freq} Hz) may be DSD256 or higher!")
                print("SuperUniti only supports DSD64 and DSD128.")
                print("Attempting playback anyway - device may reject it.\n")
        except ValueError:
            pass

    # Set AVTransport URI on the Naim renderer
    print(f"\nSending to Naim renderer at {renderer_ip}...")

    paths = _discover_upnp_services(renderer_ip, renderer_port)

    # XML escape helper
    def xml_escape(s):
        if not s:
            return ""
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 .replace("'", "&apos;"))

    # Build DIDL-Lite metadata with proper escaping
    metadata = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="{xml_escape(object_id)}" parentID="0" restricted="1">'
        f'<dc:title>{xml_escape(title)}</dc:title>'
        '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
    )
    if artist:
        metadata += f'<upnp:artist>{xml_escape(artist)}</upnp:artist>'
    if item.get("album"):
        metadata += f'<upnp:album>{xml_escape(item["album"])}</upnp:album>'
    if item.get("albumArtURI"):
        metadata += f'<upnp:albumArtURI>{xml_escape(item["albumArtURI"])}</upnp:albumArtURI>'
    # Add resource with escaped URL
    if not protocol_info:
        protocol_info = "http-get:*:audio/mpeg:*"
    metadata += f'<res protocolInfo="{xml_escape(protocol_info)}">{xml_escape(resource_url)}</res>'
    metadata += '</item></DIDL-Lite>'

    try:
        _soap_request(
            renderer_ip, renderer_port, paths[UPNP_AV_TRANSPORT],
            UPNP_AV_TRANSPORT, "SetAVTransportURI",
            {
                "InstanceID": "0",
                "CurrentURI": resource_url,
                "CurrentURIMetaData": metadata,
            })
        print("URI set successfully!")
    except UPnPError as e:
        print(f"Error setting URI: {e}")
        return

    # Start playback
    try:
        _soap_request(
            renderer_ip, renderer_port, paths[UPNP_AV_TRANSPORT],
            UPNP_AV_TRANSPORT, "Play",
            {"InstanceID": "0", "Speed": "1"})
        print("Playback started!")
    except UPnPError as e:
        print(f"Error starting playback: {e}")


# ─────────────────────────────────────────────
# CLI SETUP
# ─────────────────────────────────────────────

def add_host_args(p):
    p.add_argument("--host", default=None, help="Naim device IP address")
    p.add_argument("--port", type=int, default=UPNP_DEFAULT_PORT,
                   help=f"UPnP port (default: {UPNP_DEFAULT_PORT})")


def main():
    parser = argparse.ArgumentParser(
        description="Naim Streamer Control CLI — UPnP/DLNA Protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This tool controls Naim devices via standard UPnP/DLNA protocol (port 8080).
Works with all Naim network streamers (both legacy and newer devices).

Examples:
  %(prog)s discover                                  # Find Naim devices
  %(prog)s discover --timeout 10
  %(prog)s --host 192.168.1.21 info                  # Device info
  %(prog)s --host 192.168.1.21 play                  # Playback control
  %(prog)s --host 192.168.1.21 pause
  %(prog)s --host 192.168.1.21 stop
  %(prog)s --host 192.168.1.21 next
  %(prog)s --host 192.168.1.21 prev
  %(prog)s --host 192.168.1.21 volume-get            # Volume control
  %(prog)s --host 192.168.1.21 volume-set --level 50
  %(prog)s --host 192.168.1.21 mute
  %(prog)s --host 192.168.1.21 unmute
  %(prog)s --host 192.168.1.21 transport-info        # Status
  %(prog)s --host 192.168.1.21 position-info
  %(prog)s --host 192.168.1.21 services -v           # List services

For input switching on legacy devices (SuperUniti, NDS, NDX, etc.):
  Use naim_control_nstream.py instead (n-Stream protocol on port 15555)

For newer devices (Uniti series, Mu-so 2nd gen):
  Use naim_control_rest.py for full device control (REST API on port 15081)
        """,
    )
    add_host_args(parser)

    sub = parser.add_subparsers(title="commands", dest="command", required=True)

    # ── DISCOVER ──
    p = sub.add_parser("discover", help="Discover Naim UPnP devices on the local network")
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

    # ── INPUT / SOURCE COMMANDS ──
    p = sub.add_parser("services", help="List all UPnP services and their actions")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed action information from SCPD")
    p.set_defaults(func=cmd_upnp_services)

    p = sub.add_parser("inputs-list", help="List available inputs via ContentDirectory")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="Recursively browse child containers")
    p.set_defaults(func=cmd_upnp_inputs_list)

    p = sub.add_parser("input-browse", help="Browse a specific input/container by ObjectID")
    p.add_argument("--object-id", default="0",
                   help="ObjectID to browse (default: 0 = root)")
    p.set_defaults(func=cmd_upnp_input_browse)

    p = sub.add_parser("input-select", help="Select an input by URI or ObjectID")
    p.add_argument("--uri", help="Direct URI to set as AVTransport URI")
    p.add_argument("--object-id", help="ObjectID to browse and select")
    p.add_argument("--play", action="store_true",
                   help="Start playback immediately after selection")
    p.set_defaults(func=cmd_upnp_input_select)

    p = sub.add_parser("current-input", help="Show the current input/source being played")
    p.set_defaults(func=cmd_upnp_current_input)

    p = sub.add_parser("protocol-info", help="Show supported protocols (ConnectionManager)")
    p.set_defaults(func=cmd_upnp_protocol_info)

    # ── MEDIA SERVER BROWSING ──
    p = sub.add_parser("media-servers", help="Discover UPnP Media Servers on the network")
    p.add_argument("--timeout", type=int, default=5,
                   help="Discovery timeout in seconds (default: 5)")
    p.set_defaults(func=cmd_media_servers)

    p = sub.add_parser("server-browse", help="Browse a UPnP Media Server's content")
    p.add_argument("--server", "-s", required=True,
                   help="Media Server IP address")
    p.add_argument("--object-id", "-o", default="0",
                   help="ObjectID to browse (default: 0 = root)")
    p.add_argument("--start", type=int, default=0,
                   help="Starting index for pagination (default: 0)")
    p.add_argument("--count", type=int, default=30,
                   help="Number of items to retrieve (default: 30)")
    p.set_defaults(func=cmd_server_browse)

    p = sub.add_parser("server-search", help="Search a UPnP Media Server for content")
    p.add_argument("--server", "-s", required=True,
                   help="Media Server IP address")
    p.add_argument("--query", "-q", required=True,
                   help="Search query (matches title, artist, album)")
    p.set_defaults(func=cmd_server_search)

    p = sub.add_parser("server-play", help="Play a track from a Media Server on a Naim renderer")
    p.add_argument("--server", "-s", required=True,
                   help="Media Server IP address")
    p.add_argument("--object-id", "-o", required=True,
                   help="ObjectID of the track to play")
    p.set_defaults(func=cmd_server_play)

    args = parser.parse_args()
    # --host is required for all commands except these
    commands_without_host = {"discover"}
    if args.command not in commands_without_host and not args.host:
        parser.error("--host is required for this command")
    try:
        args.func(args)
    except UPnPError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
