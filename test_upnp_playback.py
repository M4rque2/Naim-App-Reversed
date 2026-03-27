#!/usr/bin/env python3
"""
UPnP Media Server Browse and Play Test Script

This script tests the full workflow:
1. Discover Naim renderer devices on the network
2. Discover UPnP Media Servers on the network
3. Browse the media server content
4. Play a compatible audio file on the Naim device

Compatible formats for Naim SuperUniti:
- PCM: FLAC, WAV, AIFF (up to 24-bit/192kHz, NOT 384kHz DXD)
- DSD: DSD64, DSD128 only (NOT DSD256/512/1024)
- Compressed: MP3, AAC, OGG, WMA

Usage:
    python3 test_upnp_playback.py
    python3 test_upnp_playback.py --discover-only
    python3 test_upnp_playback.py --browse-only
    python3 test_upnp_playback.py --play-first
"""

import argparse
import re
import socket
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

SSDP_MULTICAST_ADDR = "239.255.255.250"
SSDP_PORT = 1900
DEFAULT_TIMEOUT = 5

UPNP_AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
UPNP_CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:1"

# Formats supported by Naim SuperUniti
# Exclude: DXD (384kHz), DSD256, DSD512, DSD1024
SUPPORTED_FORMATS = {
    # Lossless PCM (up to 192kHz)
    'audio/flac': True,
    'audio/x-flac': True,
    'audio/wav': True,
    'audio/x-wav': True,
    'audio/aiff': True,
    'audio/x-aiff': True,
    'audio/L16': True,
    'audio/L24': True,
    # Compressed
    'audio/mpeg': True,
    'audio/mp3': True,
    'audio/mp4': True,
    'audio/x-m4a': True,
    'audio/aac': True,
    'audio/ogg': True,
    'audio/x-ogg': True,
    'audio/vorbis': True,
    'audio/wma': True,
    'audio/x-ms-wma': True,
    # DSD (only 64 and 128)
    'audio/x-dsd': True,  # Need to check sample rate
    'audio/dsd': True,
    'audio/dsf': True,
    'audio/x-dsf': True,
    'audio/dff': True,
    'audio/x-dff': True,
}

# Sample rates to reject (DXD and high DSD)
UNSUPPORTED_SAMPLE_RATES = {
    352800,   # DXD
    384000,   # DXD
    705600,   # DXD
    768000,   # DXD
    11289600, # DSD256
    12288000, # DSD256
    22579200, # DSD512
    24576000, # DSD512
    45158400, # DSD1024
    49152000, # DSD1024
}


class UPnPError(Exception):
    """Raised when a UPnP operation fails."""
    pass


# ─────────────────────────────────────────────
# SSDP DISCOVERY
# ─────────────────────────────────────────────

def ssdp_discover(search_target, timeout=DEFAULT_TIMEOUT):
    """Send SSDP M-SEARCH and collect responding device locations."""
    locations = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MULTICAST_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        f"ST: {search_target}\r\n"
        "USER-AGENT: UPnP/1.0 NaimTest/1.0\r\n"
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


def parse_device_description(url):
    """Fetch a UPnP device description XML and extract device info."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
    except Exception as e:
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

    # Find services
    services = {}
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
                services[stype] = ctrl

    return {
        "friendlyName": txt("friendlyName"),
        "manufacturer": txt("manufacturer"),
        "modelName": txt("modelName"),
        "deviceType": txt("deviceType"),
        "UDN": txt("UDN"),
        "services": services,
    }


def discover_naim_renderers(timeout=DEFAULT_TIMEOUT):
    """Discover Naim MediaRenderer devices on the network."""
    print(f"[*] Searching for Naim MediaRenderer devices (timeout={timeout}s)...")

    locations = ssdp_discover("urn:schemas-upnp-org:device:MediaRenderer:1", timeout)

    naim_devices = []
    for location, ip in locations.items():
        desc = parse_device_description(location)
        if desc and desc.get("manufacturer"):
            mfg = desc["manufacturer"].lower()
            if "naim" in mfg:
                # Extract port from location URL
                parsed = urllib.parse.urlparse(location)
                port = parsed.port or 80

                naim_devices.append({
                    "ip": ip,
                    "port": port,
                    "location": location,
                    "friendlyName": desc.get("friendlyName"),
                    "modelName": desc.get("modelName"),
                    "services": desc.get("services", {}),
                })

    return naim_devices


def discover_media_servers(timeout=DEFAULT_TIMEOUT):
    """Discover UPnP Media Servers on the network."""
    print(f"[*] Searching for UPnP Media Servers (timeout={timeout}s)...")

    servers = []

    # Search for MediaServer devices
    for version in ["1", "2", "3", "4"]:
        locations = ssdp_discover(f"urn:schemas-upnp-org:device:MediaServer:{version}", timeout)

        for location, ip in locations.items():
            # Skip if we already have this IP
            if any(s["ip"] == ip for s in servers):
                continue

            desc = parse_device_description(location)
            if desc and "MediaServer" in desc.get("deviceType", ""):
                parsed = urllib.parse.urlparse(location)
                port = parsed.port or 80
                base_url = f"{parsed.scheme}://{parsed.netloc}"

                # Find ContentDirectory control URL
                content_dir_url = None
                for stype, ctrl in desc.get("services", {}).items():
                    if "ContentDirectory" in stype:
                        content_dir_url = ctrl
                        break

                servers.append({
                    "ip": ip,
                    "port": port,
                    "base_url": base_url,
                    "location": location,
                    "friendlyName": desc.get("friendlyName"),
                    "modelName": desc.get("modelName"),
                    "manufacturer": desc.get("manufacturer"),
                    "contentDirectoryURL": content_dir_url,
                })

    return servers


# ─────────────────────────────────────────────
# SOAP OPERATIONS
# ─────────────────────────────────────────────

def build_soap_envelope(service_type, action, args=None):
    """Build a SOAP XML envelope for a UPnP action."""
    args = args or {}
    arg_xml = ""
    for k, v in args.items():
        # Escape XML special characters
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


def soap_request(host, port, path, service, action, args=None):
    """Send a SOAP POST request and return the parsed response dict."""
    envelope = build_soap_envelope(service, action, args)
    url = f"http://{host}:{port}{path}"
    data = envelope.encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", 'text/xml; charset="utf-8"')
    req.add_header("SOAPAction", f'"{service}#{action}"')

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
            return parse_soap_response(xml_data, action)
    except urllib.error.HTTPError as e:
        raw = e.read()
        fault = parse_soap_fault(raw)
        if fault:
            raise UPnPError(f"UPnP SOAP fault: {fault}")
        else:
            raise UPnPError(f"HTTP {e.code} {e.reason}: {raw.decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise UPnPError(f"Connection error: {e.reason}")


def parse_soap_response(xml_data, action):
    """Extract output arguments from a SOAP response envelope."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return xml_data.decode(errors="replace")

    body = None
    for el in root.iter():
        if el.tag.endswith("}Body") or el.tag == "Body":
            body = el
            break
    if body is None:
        body = root

    result = {}
    for resp_el in body:
        for child in resp_el:
            tag = child.tag
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            result[tag] = child.text or ""
    return result


def parse_soap_fault(xml_data):
    """Parse a UPnP SOAP fault response."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None

    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "UPnPError" or tag == "errorDescription":
            if tag == "UPnPError":
                desc_el = None
                code_el = None
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

    for el in root.iter():
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "faultstring":
            return el.text or "unknown fault"
    return None


# ─────────────────────────────────────────────
# CONTENT DIRECTORY BROWSING
# ─────────────────────────────────────────────

def parse_didl_lite(didl_xml):
    """Parse DIDL-Lite XML and extract item/container metadata."""
    items = []
    try:
        root = ET.fromstring(didl_xml)
    except ET.ParseError:
        return items

    for el in root:
        tag = el.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        item = {
            "type": tag,  # "container" or "item"
            "id": el.get("id"),
            "parentID": el.get("parentID"),
        }

        for child in el:
            ctag = child.tag
            if "}" in ctag:
                ctag = ctag.split("}", 1)[1]

            if ctag == "title":
                item["title"] = child.text
            elif ctag == "class":
                item["class"] = child.text
            elif ctag == "res":
                if "res" not in item:
                    item["res"] = []
                res_info = {
                    "url": child.text,
                    "protocolInfo": child.get("protocolInfo"),
                    "sampleFrequency": child.get("sampleFrequency"),
                    "bitsPerSample": child.get("bitsPerSample"),
                    "nrAudioChannels": child.get("nrAudioChannels"),
                    "duration": child.get("duration"),
                    "size": child.get("size"),
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


def browse_content_directory(base_url, control_url, object_id="0", start_index=0, count=50):
    """Browse a UPnP Media Server's ContentDirectory."""
    if control_url.startswith("http"):
        full_url = control_url
    else:
        full_url = f"{base_url}{control_url}"

    parsed = urllib.parse.urlparse(full_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path

    default_filter = (
        "dc:date,upnp:genre,res,res@duration,res@size,upnp:albumArtURI,"
        "upnp:album,upnp:artist,upnp:author,dc:creator,upnp:originalTrackNumber,"
        "res@sampleFrequency,res@bitsPerSample,res@nrAudioChannels"
    )

    try:
        result = soap_request(
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

    didl_xml = result.get("Result", "")
    if not didl_xml:
        return {
            "items": [],
            "total": result.get("TotalMatches", "0"),
            "returned": result.get("NumberReturned", "0"),
        }

    items = parse_didl_lite(didl_xml)
    return {
        "items": items,
        "total": result.get("TotalMatches", "0"),
        "returned": result.get("NumberReturned", "0"),
    }


def get_item_metadata(base_url, control_url, object_id):
    """Get metadata for a specific item by ObjectID."""
    if control_url.startswith("http"):
        full_url = control_url
    else:
        full_url = f"{base_url}{control_url}"

    parsed = urllib.parse.urlparse(full_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path

    try:
        result = soap_request(
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
        return None

    didl_xml = result.get("Result", "")
    if not didl_xml:
        return None

    items = parse_didl_lite(didl_xml)
    return items[0] if items else None


# ─────────────────────────────────────────────
# FORMAT COMPATIBILITY CHECK
# ─────────────────────────────────────────────

def is_format_compatible(res_info):
    """Check if an audio resource is compatible with Naim SuperUniti.

    Returns tuple: (is_compatible, reason)
    """
    protocol_info = res_info.get("protocolInfo", "")
    sample_freq = res_info.get("sampleFrequency")
    url = res_info.get("url", "")

    # Parse MIME type from protocol info
    # Format: http-get:*:audio/flac:*
    mime_type = None
    if protocol_info:
        parts = protocol_info.split(":")
        if len(parts) >= 3:
            mime_type = parts[2]

    # Try to detect from URL extension if no mime type
    if not mime_type:
        url_lower = url.lower()
        if ".flac" in url_lower:
            mime_type = "audio/flac"
        elif ".wav" in url_lower:
            mime_type = "audio/wav"
        elif ".mp3" in url_lower:
            mime_type = "audio/mpeg"
        elif ".dsf" in url_lower:
            mime_type = "audio/dsf"
        elif ".dff" in url_lower:
            mime_type = "audio/dff"
        elif ".aiff" in url_lower or ".aif" in url_lower:
            mime_type = "audio/aiff"
        elif ".m4a" in url_lower:
            mime_type = "audio/mp4"
        elif ".ogg" in url_lower:
            mime_type = "audio/ogg"

    # Check sample rate
    if sample_freq:
        try:
            freq = int(sample_freq)
            if freq in UNSUPPORTED_SAMPLE_RATES:
                return False, f"Unsupported sample rate: {freq}Hz (DXD or high DSD)"
            # DSD sample rates
            if freq >= 5644800:  # DSD128 = 5.6MHz
                if freq > 6144000:  # Above DSD128
                    return False, f"DSD rate too high: {freq}Hz (max DSD128)"
        except ValueError:
            pass

    # Check for DXD/high-res indicators in protocol info
    protocol_lower = protocol_info.lower()
    if "dxd" in protocol_lower:
        return False, "DXD format not supported"
    if "dsd256" in protocol_lower or "dsd512" in protocol_lower or "dsd1024" in protocol_lower:
        return False, "High DSD rate not supported (max DSD128)"

    # Check MIME type
    if mime_type:
        mime_lower = mime_type.lower()
        # Generic audio check
        if mime_lower.startswith("audio/"):
            # Check for unsupported specific types
            if "dsd256" in mime_lower or "dsd512" in mime_lower:
                return False, f"Unsupported DSD format: {mime_type}"
            return True, f"Compatible: {mime_type}"

    # If we can't determine, assume compatible but warn
    return True, "Format unknown, assuming compatible"


def find_compatible_tracks(server, max_depth=3, max_tracks=10):
    """Recursively browse a media server to find compatible audio tracks.

    Returns a list of compatible tracks with their metadata.
    """
    base_url = server["base_url"]
    control_url = server["contentDirectoryURL"]

    if not control_url:
        print(f"    [!] Server has no ContentDirectory service")
        return []

    compatible_tracks = []
    containers_to_browse = [("0", 0)]  # (object_id, depth)
    visited = set()

    while containers_to_browse and len(compatible_tracks) < max_tracks:
        object_id, depth = containers_to_browse.pop(0)

        if object_id in visited:
            continue
        visited.add(object_id)

        if depth > max_depth:
            continue

        result = browse_content_directory(base_url, control_url, object_id)

        if result.get("error"):
            continue

        for item in result.get("items", []):
            if item["type"] == "container":
                # Add container to browse queue
                if item.get("id"):
                    containers_to_browse.append((item["id"], depth + 1))
            elif item["type"] == "item":
                # Check if it's an audio item with resources
                item_class = item.get("class", "")
                if "audioItem" in item_class and item.get("res"):
                    # Check each resource for compatibility
                    for res in item["res"]:
                        is_compat, reason = is_format_compatible(res)
                        if is_compat:
                            compatible_tracks.append({
                                "id": item.get("id"),
                                "title": item.get("title", "Unknown"),
                                "artist": item.get("artist", ""),
                                "album": item.get("album", ""),
                                "url": res.get("url"),
                                "protocolInfo": res.get("protocolInfo"),
                                "sampleFrequency": res.get("sampleFrequency"),
                                "bitsPerSample": res.get("bitsPerSample"),
                                "duration": res.get("duration"),
                                "reason": reason,
                            })
                            if len(compatible_tracks) >= max_tracks:
                                return compatible_tracks
                            break  # Only add one resource per item

    return compatible_tracks


# ─────────────────────────────────────────────
# PLAYBACK CONTROL
# ─────────────────────────────────────────────

def get_av_transport_url(renderer):
    """Get the AVTransport control URL for a renderer."""
    services = renderer.get("services", {})
    for stype, ctrl in services.items():
        if "AVTransport" in stype:
            return ctrl
    return "/AVTransport/ctrl"  # Default fallback


def set_av_transport_uri(renderer, track):
    """Set the AVTransport URI on the renderer to play a track."""
    host = renderer["ip"]
    port = renderer["port"]
    av_path = get_av_transport_url(renderer)

    resource_url = track["url"]
    title = track.get("title", "Unknown")
    artist = track.get("artist", "")
    album = track.get("album", "")
    object_id = track.get("id", "0")
    protocol_info = track.get("protocolInfo", "http-get:*:audio/mpeg:*")

    # Build DIDL-Lite metadata
    # Need to escape special characters for XML
    def xml_escape(s):
        if not s:
            return ""
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 .replace("'", "&apos;"))

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
    if album:
        metadata += f'<upnp:album>{xml_escape(album)}</upnp:album>'
    metadata += f'<res protocolInfo="{xml_escape(protocol_info)}">{xml_escape(resource_url)}</res>'
    metadata += '</item></DIDL-Lite>'

    print(f"    [*] Setting AVTransport URI...")
    print(f"        URL: {resource_url[:80]}{'...' if len(resource_url) > 80 else ''}")

    try:
        result = soap_request(
            host, port, av_path,
            UPNP_AV_TRANSPORT, "SetAVTransportURI",
            {
                "InstanceID": "0",
                "CurrentURI": resource_url,
                "CurrentURIMetaData": metadata,
            })
        print(f"    [+] URI set successfully")
        return True
    except UPnPError as e:
        print(f"    [!] Failed to set URI: {e}")
        return False


def play(renderer):
    """Start playback on the renderer."""
    host = renderer["ip"]
    port = renderer["port"]
    av_path = get_av_transport_url(renderer)

    print(f"    [*] Sending Play command...")

    try:
        result = soap_request(
            host, port, av_path,
            UPNP_AV_TRANSPORT, "Play",
            {
                "InstanceID": "0",
                "Speed": "1",
            })
        print(f"    [+] Play command sent successfully")
        return True
    except UPnPError as e:
        print(f"    [!] Failed to play: {e}")
        return False


def stop(renderer):
    """Stop playback on the renderer."""
    host = renderer["ip"]
    port = renderer["port"]
    av_path = get_av_transport_url(renderer)

    try:
        soap_request(
            host, port, av_path,
            UPNP_AV_TRANSPORT, "Stop",
            {"InstanceID": "0"})
        return True
    except UPnPError:
        return False


def get_transport_info(renderer):
    """Get current transport state from the renderer."""
    host = renderer["ip"]
    port = renderer["port"]
    av_path = get_av_transport_url(renderer)

    try:
        result = soap_request(
            host, port, av_path,
            UPNP_AV_TRANSPORT, "GetTransportInfo",
            {"InstanceID": "0"})
        return result
    except UPnPError:
        return None


def get_position_info(renderer):
    """Get current position info from the renderer."""
    host = renderer["ip"]
    port = renderer["port"]
    av_path = get_av_transport_url(renderer)

    try:
        result = soap_request(
            host, port, av_path,
            UPNP_AV_TRANSPORT, "GetPositionInfo",
            {"InstanceID": "0"})
        return result
    except UPnPError:
        return None


# ─────────────────────────────────────────────
# MAIN TEST WORKFLOW
# ─────────────────────────────────────────────

def print_separator(char="-", length=60):
    print(char * length)


def main():
    parser = argparse.ArgumentParser(
        description="UPnP Media Server Browse and Play Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script tests UPnP media browsing and playback with Naim devices.

Compatible formats for Naim SuperUniti:
  - PCM: FLAC, WAV, AIFF (up to 24-bit/192kHz)
  - DSD: DSD64, DSD128 only
  - Compressed: MP3, AAC, OGG, WMA

NOT supported (automatically skipped):
  - DXD (352.8kHz, 384kHz)
  - DSD256, DSD512, DSD1024

Examples:
  python3 test_upnp_playback.py                    # Full test
  python3 test_upnp_playback.py --discover-only   # Just discover devices
  python3 test_upnp_playback.py --browse-only     # Discover and browse
  python3 test_upnp_playback.py --play-first      # Play first compatible track
        """,
    )
    parser.add_argument("--discover-only", action="store_true",
                        help="Only discover devices, don't browse or play")
    parser.add_argument("--browse-only", action="store_true",
                        help="Discover and browse, but don't play")
    parser.add_argument("--play-first", action="store_true",
                        help="Automatically play the first compatible track found")
    parser.add_argument("--timeout", type=int, default=5,
                        help="Discovery timeout in seconds (default: 5)")
    parser.add_argument("--max-tracks", type=int, default=10,
                        help="Maximum number of tracks to find (default: 10)")

    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  UPnP Media Server Browse and Play Test")
    print("  For Naim SuperUniti and compatible devices")
    print("=" * 60)
    print()

    # Step 1: Discover Naim renderers
    print_separator("=")
    print("STEP 1: Discovering Naim Renderer Devices")
    print_separator("=")

    naim_devices = discover_naim_renderers(args.timeout)

    if not naim_devices:
        print("[!] No Naim devices found on the network.")
        print("    Make sure your Naim device is powered on.")
        sys.exit(1)

    print(f"\n[+] Found {len(naim_devices)} Naim device(s):\n")
    for i, dev in enumerate(naim_devices, 1):
        print(f"    [{i}] {dev['friendlyName']} ({dev['modelName']})")
        print(f"        IP: {dev['ip']}:{dev['port']}")

    # Select the first Naim device
    renderer = naim_devices[0]
    print(f"\n[*] Using: {renderer['friendlyName']}")

    # Step 2: Discover Media Servers
    print()
    print_separator("=")
    print("STEP 2: Discovering UPnP Media Servers")
    print_separator("=")

    servers = discover_media_servers(args.timeout)

    if not servers:
        print("[!] No UPnP Media Servers found on the network.")
        print("    Make sure you have a DLNA/UPnP server running.")
        sys.exit(1)

    print(f"\n[+] Found {len(servers)} Media Server(s):\n")
    for i, srv in enumerate(servers, 1):
        print(f"    [{i}] {srv['friendlyName']}")
        print(f"        IP: {srv['ip']}:{srv['port']}")
        if srv.get('manufacturer'):
            print(f"        Manufacturer: {srv['manufacturer']}")

    if args.discover_only:
        print("\n[*] Discovery complete (--discover-only mode)")
        sys.exit(0)

    # Step 3: Browse Media Server for compatible tracks
    print()
    print_separator("=")
    print("STEP 3: Browsing Media Servers for Compatible Tracks")
    print_separator("=")

    all_tracks = []

    for srv in servers:
        print(f"\n[*] Browsing: {srv['friendlyName']}...")
        tracks = find_compatible_tracks(srv, max_tracks=args.max_tracks)

        if tracks:
            print(f"    [+] Found {len(tracks)} compatible track(s)")
            for track in tracks:
                all_tracks.append({**track, "server": srv})
        else:
            print(f"    [-] No compatible tracks found")

    if not all_tracks:
        print("\n[!] No compatible audio tracks found on any server.")
        sys.exit(1)

    print(f"\n[+] Total compatible tracks found: {len(all_tracks)}\n")
    print_separator("-")
    print("Compatible Tracks:")
    print_separator("-")

    for i, track in enumerate(all_tracks[:20], 1):  # Show max 20
        title = track.get("title", "Unknown")[:40]
        artist = track.get("artist", "Unknown")[:20]
        freq = track.get("sampleFrequency", "?")
        bits = track.get("bitsPerSample", "?")
        reason = track.get("reason", "")

        print(f"  [{i:2}] {title}")
        print(f"       Artist: {artist} | {bits}bit/{freq}Hz")
        print(f"       {reason}")

    if len(all_tracks) > 20:
        print(f"  ... and {len(all_tracks) - 20} more")

    if args.browse_only:
        print("\n[*] Browse complete (--browse-only mode)")
        sys.exit(0)

    # Step 4: Play a track
    print()
    print_separator("=")
    print("STEP 4: Playback Test")
    print_separator("=")

    if args.play_first:
        track_idx = 0
    else:
        print("\nSelect a track to play (1-{0}), or 'q' to quit:".format(min(len(all_tracks), 20)))
        try:
            choice = input("> ").strip()
            if choice.lower() == 'q':
                print("[*] Exiting")
                sys.exit(0)
            track_idx = int(choice) - 1
            if track_idx < 0 or track_idx >= len(all_tracks):
                print("[!] Invalid selection")
                sys.exit(1)
        except (ValueError, EOFError):
            print("[!] Invalid input")
            sys.exit(1)

    track = all_tracks[track_idx]

    print(f"\n[*] Playing: {track['title']}")
    print(f"    Artist: {track.get('artist', 'Unknown')}")
    print(f"    Album: {track.get('album', 'Unknown')}")
    print(f"    Format: {track.get('reason', 'Unknown')}")
    print(f"    On: {renderer['friendlyName']}")
    print()

    # Stop any current playback
    stop(renderer)
    time.sleep(0.5)

    # Set URI and play
    if set_av_transport_uri(renderer, track):
        time.sleep(0.5)
        if play(renderer):
            print()
            print("[+] Playback started!")

            # Wait a moment and check status
            time.sleep(2)

            transport = get_transport_info(renderer)
            if transport:
                state = transport.get("CurrentTransportState", "Unknown")
                print(f"\n[*] Transport state: {state}")

            position = get_position_info(renderer)
            if position:
                track_name = position.get("TrackURI", "")[:60]
                rel_time = position.get("RelTime", "0:00:00")
                duration = position.get("TrackDuration", "0:00:00")
                print(f"[*] Position: {rel_time} / {duration}")
        else:
            print("\n[!] Playback failed - Play command rejected")
    else:
        print("\n[!] Playback failed - Could not set URI")

    print()
    print_separator("=")
    print("Test Complete")
    print_separator("=")


if __name__ == "__main__":
    main()
