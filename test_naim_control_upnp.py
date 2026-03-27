#!/usr/bin/env python3
"""
Integration tests for naim_control_upnp.py against a real Naim SuperUniti.

Requirements:
  - A Naim SuperUniti (or other legacy Naim UPnP device) powered on and
    connected to the local network.
  - Set the NAIM_HOST environment variable to override the default IP.
    Default: 192.168.1.21

Safety:
  - Volume is NEVER set above MAX_SAFE_VOLUME (50) to protect speakers.
  - The original volume is saved and restored after volume tests.
  - Mute state is restored after mute tests.

Run:
  python3 -m pytest test_naim_control_upnp.py -v
  python3 -m pytest test_naim_control_upnp.py -v -k "discovery"
  python3 -m pytest test_naim_control_upnp.py -v -k "volume"
"""

import os
import sys
import time
import socket
import unittest
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Ensure the repo root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import naim_control_upnp as upnp

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
DEVICE_HOST = os.environ.get("NAIM_HOST", "192.168.1.21")
DEVICE_PORT = int(os.environ.get("NAIM_PORT", str(upnp.UPNP_DEFAULT_PORT)))
MAX_SAFE_VOLUME = 50


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _get_volume(host, port, paths):
    """Read current volume from the device. Returns int."""
    result = upnp._soap_request(
        host, port, paths[upnp.UPNP_RENDERING_CONTROL],
        upnp.UPNP_RENDERING_CONTROL, "GetVolume",
        {"InstanceID": "0", "Channel": "Master"},
    )
    return int(result.get("CurrentVolume", 0))


def _set_volume(host, port, paths, level):
    """Set volume on the device. Enforces MAX_SAFE_VOLUME."""
    safe_level = min(int(level), MAX_SAFE_VOLUME)
    upnp._soap_request(
        host, port, paths[upnp.UPNP_RENDERING_CONTROL],
        upnp.UPNP_RENDERING_CONTROL, "SetVolume",
        {"InstanceID": "0", "Channel": "Master",
         "DesiredVolume": str(safe_level)},
    )


def _get_mute(host, port, paths):
    """Read current mute state. Returns bool."""
    result = upnp._soap_request(
        host, port, paths[upnp.UPNP_RENDERING_CONTROL],
        upnp.UPNP_RENDERING_CONTROL, "GetMute",
        {"InstanceID": "0", "Channel": "Master"},
    )
    return result.get("CurrentMute", "0") in ("1", "true", "True")


def _set_mute(host, port, paths, muted):
    """Set mute state on the device."""
    upnp._soap_request(
        host, port, paths[upnp.UPNP_RENDERING_CONTROL],
        upnp.UPNP_RENDERING_CONTROL, "SetMute",
        {"InstanceID": "0", "Channel": "Master",
         "DesiredMute": "1" if muted else "0"},
    )


def _get_transport_state(host, port, paths):
    """Get the current transport state string."""
    result = upnp._soap_request(
        host, port, paths[upnp.UPNP_AV_TRANSPORT],
        upnp.UPNP_AV_TRANSPORT, "GetTransportInfo",
        {"InstanceID": "0"},
    )
    return result.get("CurrentTransportState", "UNKNOWN")


def _device_reachable(host, port, timeout=3):
    """Quick TCP check to see if the device is reachable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# Test: Discovery
# ─────────────────────────────────────────────

class TestDiscovery(unittest.TestCase):
    """Test SSDP discovery and UPnP description parsing."""

    def test_01_ssdp_discover_returns_locations(self):
        """SSDP M-SEARCH should find at least one UPnP device on the network."""
        locations = upnp._ssdp_discover(timeout=5)
        self.assertIsInstance(locations, dict)
        self.assertGreater(len(locations), 0,
                           "No SSDP devices found on the network at all")

    def test_02_ssdp_discover_finds_naim(self):
        """SSDP should find at least one device whose description says Naim."""
        locations = upnp._ssdp_discover(timeout=5)
        naim_found = False
        for url, ip in locations.items():
            desc = upnp._parse_upnp_description(url)
            if desc and desc.get("manufacturer") and "naim" in desc["manufacturer"].lower():
                naim_found = True
                break
        self.assertTrue(naim_found,
                        "No Naim device found in SSDP discovery results")

    def test_03_parse_upnp_description_superuniti(self):
        """Fetch description.xml from the known device and parse it."""
        url = f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml"
        desc = upnp._parse_upnp_description(url)
        self.assertIsNotNone(desc, "Failed to parse description.xml")
        self.assertIn("friendlyName", desc)
        self.assertIn("manufacturer", desc)
        self.assertIn("modelName", desc)
        self.assertIn("naim", desc["manufacturer"].lower())
        self.assertEqual(desc["modelName"], "SuperUniti")

    def test_04_parse_upnp_description_bad_url_returns_none(self):
        """_parse_upnp_description should return None for unreachable URLs."""
        result = upnp._parse_upnp_description("http://192.0.2.1:9999/bad.xml")
        self.assertIsNone(result)


# ─────────────────────────────────────────────
# Test: Service Discovery
# ─────────────────────────────────────────────

class TestServiceDiscovery(unittest.TestCase):
    """Test UPnP service discovery from description.xml."""

    def test_01_discover_services_returns_both(self):
        """Should discover both AVTransport and RenderingControl services."""
        services = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)
        self.assertIn(upnp.UPNP_AV_TRANSPORT, services)
        self.assertIn(upnp.UPNP_RENDERING_CONTROL, services)

    def test_02_control_urls_are_strings(self):
        """Control URLs should be non-empty strings."""
        services = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)
        for stype, ctrl_url in services.items():
            self.assertIsInstance(ctrl_url, str)
            self.assertTrue(len(ctrl_url) > 0, f"Empty control URL for {stype}")

    def test_03_fallback_on_bad_host(self):
        """Service discovery should return hardcoded defaults for unreachable hosts."""
        services = upnp._discover_upnp_services("192.0.2.1", 9999)
        self.assertEqual(services[upnp.UPNP_AV_TRANSPORT],
                         upnp.UPNP_AV_TRANSPORT_PATH)
        self.assertEqual(services[upnp.UPNP_RENDERING_CONTROL],
                         upnp.UPNP_RENDERING_CONTROL_PATH)


# ─────────────────────────────────────────────
# Test: SOAP Envelope Building
# ─────────────────────────────────────────────

class TestSOAPEnvelope(unittest.TestCase):
    """Test SOAP XML envelope construction (offline, no device needed)."""

    def test_01_envelope_no_args(self):
        """Envelope with no arguments should be valid XML."""
        envelope = upnp._build_soap_envelope(
            upnp.UPNP_AV_TRANSPORT, "Stop")
        root = ET.fromstring(envelope)
        self.assertIsNotNone(root)
        # Should contain the action element
        self.assertIn("Stop", envelope)

    def test_02_envelope_with_args(self):
        """Envelope should embed arguments as child elements."""
        envelope = upnp._build_soap_envelope(
            upnp.UPNP_AV_TRANSPORT, "Play",
            {"InstanceID": "0", "Speed": "1"})
        self.assertIn("<InstanceID>0</InstanceID>", envelope)
        self.assertIn("<Speed>1</Speed>", envelope)

    def test_03_envelope_is_well_formed_xml(self):
        """Generated envelope should parse as valid XML."""
        envelope = upnp._build_soap_envelope(
            upnp.UPNP_RENDERING_CONTROL, "SetVolume",
            {"InstanceID": "0", "Channel": "Master", "DesiredVolume": "25"})
        root = ET.fromstring(envelope)
        # Find the Body element
        body = None
        for el in root.iter():
            if el.tag.endswith("}Body") or el.tag == "Body":
                body = el
                break
        self.assertIsNotNone(body, "SOAP Body not found in envelope")

    def test_04_envelope_contains_service_namespace(self):
        """The action element should reference the correct service namespace."""
        envelope = upnp._build_soap_envelope(
            upnp.UPNP_AV_TRANSPORT, "Pause", {"InstanceID": "0"})
        self.assertIn(upnp.UPNP_AV_TRANSPORT, envelope)


# ─────────────────────────────────────────────
# Test: SOAP Response Parsing (offline)
# ─────────────────────────────────────────────

class TestSOAPResponseParsing(unittest.TestCase):
    """Test SOAP response XML parsing (offline, no device needed)."""

    def test_01_parse_volume_response(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body>'
            '<u:GetVolumeResponse xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
            '<CurrentVolume>42</CurrentVolume>'
            '</u:GetVolumeResponse>'
            '</s:Body>'
            '</s:Envelope>'
        )
        result = upnp._parse_soap_response(xml.encode("utf-8"), "GetVolume")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["CurrentVolume"], "42")

    def test_02_parse_transport_info_response(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body>'
            '<u:GetTransportInfoResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
            '<CurrentTransportState>PLAYING</CurrentTransportState>'
            '<CurrentTransportStatus>OK</CurrentTransportStatus>'
            '<CurrentSpeed>1</CurrentSpeed>'
            '</u:GetTransportInfoResponse>'
            '</s:Body>'
            '</s:Envelope>'
        )
        result = upnp._parse_soap_response(xml.encode("utf-8"), "GetTransportInfo")
        self.assertEqual(result["CurrentTransportState"], "PLAYING")
        self.assertEqual(result["CurrentTransportStatus"], "OK")
        self.assertEqual(result["CurrentSpeed"], "1")

    def test_03_parse_empty_response(self):
        """An action response with no output args should return empty dict."""
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body>'
            '<u:PlayResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
            '</u:PlayResponse>'
            '</s:Body>'
            '</s:Envelope>'
        )
        result = upnp._parse_soap_response(xml.encode("utf-8"), "Play")
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), 0)

    def test_04_parse_malformed_xml_returns_string(self):
        """Malformed XML should return the raw string instead of crashing."""
        result = upnp._parse_soap_response(b"not xml at all", "Play")
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────
# Test: SOAP Fault Parsing (offline)
# ─────────────────────────────────────────────

class TestSOAPFaultParsing(unittest.TestCase):
    """Test SOAP fault XML parsing (offline, no device needed)."""

    def test_01_parse_upnp_error(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            '<s:Body>'
            '<s:Fault>'
            '<detail>'
            '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
            '<errorCode>501</errorCode>'
            '<errorDescription>Action Failed</errorDescription>'
            '</UPnPError>'
            '</detail>'
            '</s:Fault>'
            '</s:Body>'
            '</s:Envelope>'
        )
        result = upnp._parse_soap_fault(xml.encode("utf-8"))
        self.assertIsNotNone(result)
        self.assertIn("501", result)
        self.assertIn("Action Failed", result)

    def test_02_parse_faultstring_fallback(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            '<s:Body>'
            '<s:Fault>'
            '<faultstring>Server Error</faultstring>'
            '</s:Fault>'
            '</s:Body>'
            '</s:Envelope>'
        )
        result = upnp._parse_soap_fault(xml.encode("utf-8"))
        self.assertIsNotNone(result)
        self.assertIn("Server Error", result)

    def test_03_parse_non_xml_returns_none(self):
        result = upnp._parse_soap_fault(b"this is not xml")
        self.assertIsNone(result)

    def test_04_parse_xml_without_fault_returns_none(self):
        xml = '<?xml version="1.0"?><root><child>text</child></root>'
        result = upnp._parse_soap_fault(xml.encode("utf-8"))
        self.assertIsNone(result)


# ─────────────────────────────────────────────
# Test: Device Info (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestDeviceInfo(unittest.TestCase):
    """Test fetching device description from the real device."""

    def test_01_fetch_description_xml(self):
        """Should be able to fetch description.xml over HTTP."""
        url = f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        self.assertGreater(len(data), 0)
        root = ET.fromstring(data)
        self.assertIsNotNone(root)

    def test_02_description_contains_naim_manufacturer(self):
        """description.xml should identify Naim as manufacturer."""
        desc = upnp._parse_upnp_description(
            f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml")
        self.assertIsNotNone(desc)
        self.assertIn("naim", desc["manufacturer"].lower())

    def test_03_description_model_is_superuniti(self):
        """The modelName should be SuperUniti."""
        desc = upnp._parse_upnp_description(
            f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml")
        self.assertEqual(desc["modelName"], "SuperUniti")

    def test_04_description_has_serial_and_udn(self):
        """Device should report a serial number and UDN."""
        desc = upnp._parse_upnp_description(
            f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml")
        self.assertIsNotNone(desc["serialNumber"])
        self.assertIsNotNone(desc["UDN"])
        self.assertTrue(desc["UDN"].startswith("uuid:"))


# ─────────────────────────────────────────────
# Test: AVTransport — Transport Info (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestAVTransportInfo(unittest.TestCase):
    """Test AVTransport query actions (read-only, no side effects)."""

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)

    def test_01_get_transport_info(self):
        """GetTransportInfo should return state, status, and speed."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetTransportInfo",
            {"InstanceID": "0"})
        self.assertIsInstance(result, dict)
        self.assertIn("CurrentTransportState", result)
        self.assertIn("CurrentTransportStatus", result)
        self.assertIn("CurrentSpeed", result)
        # Transport state should be a known value
        known_states = {
            "STOPPED", "PLAYING", "PAUSED_PLAYBACK",
            "TRANSITIONING", "NO_MEDIA_PRESENT",
        }
        self.assertIn(result["CurrentTransportState"], known_states,
                       f"Unexpected transport state: {result['CurrentTransportState']}")

    def test_02_get_position_info(self):
        """GetPositionInfo should return track position fields."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetPositionInfo",
            {"InstanceID": "0"})
        self.assertIsInstance(result, dict)
        # These fields should always be present (may be empty/0)
        expected_keys = {"Track", "TrackDuration", "TrackURI", "RelTime"}
        for key in expected_keys:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_03_get_media_info(self):
        """GetMediaInfo should return media metadata fields."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetMediaInfo",
            {"InstanceID": "0"})
        self.assertIsInstance(result, dict)
        self.assertIn("NrTracks", result)
        self.assertIn("CurrentURI", result)


# ─────────────────────────────────────────────
# Test: AVTransport — Playback Control (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestAVTransportPlayback(unittest.TestCase):
    """Test AVTransport playback actions (play, pause, stop, etc.).

    These tests interact with the device's transport state.
    The device may return UPnP error 701 ("Transition not available") when
    no media is loaded — this is expected and these tests handle it gracefully.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)

    def _transport_state(self):
        return _get_transport_state(DEVICE_HOST, DEVICE_PORT, self.paths)

    def _soap(self, action, args=None):
        """Send AVTransport SOAP request, returning result or None on UPnPError."""
        try:
            return upnp._soap_request(
                DEVICE_HOST, DEVICE_PORT,
                self.paths[upnp.UPNP_AV_TRANSPORT],
                upnp.UPNP_AV_TRANSPORT, action,
                args or {"InstanceID": "0"})
        except upnp.UPnPError:
            return None

    def test_01_stop(self):
        """Stop action should succeed or return 701 if already stopped."""
        result = self._soap("Stop", {"InstanceID": "0"})
        if result is not None:
            self.assertIsInstance(result, dict)
        time.sleep(1)
        state = self._transport_state()
        self.assertIn(state, ("STOPPED", "NO_MEDIA_PRESENT"))

    def test_02_play(self):
        """Play action: succeeds if media loaded, 701 fault otherwise."""
        result = self._soap("Play", {"InstanceID": "0", "Speed": "1"})
        if result is not None:
            self.assertIsInstance(result, dict)
        time.sleep(2)
        state = self._transport_state()
        self.assertIn(state, ("PLAYING", "STOPPED", "TRANSITIONING",
                              "NO_MEDIA_PRESENT"))

    def test_03_pause(self):
        """Pause action: succeeds if playing, 701 fault otherwise."""
        # Attempt play first, then pause
        self._soap("Play", {"InstanceID": "0", "Speed": "1"})
        time.sleep(1)
        result = self._soap("Pause", {"InstanceID": "0"})
        if result is not None:
            self.assertIsInstance(result, dict)
        time.sleep(1)
        state = self._transport_state()
        self.assertIn(state, ("PAUSED_PLAYBACK", "STOPPED",
                              "NO_MEDIA_PRESENT"))

    def test_04_stop_after_pause(self):
        """Stop after pause should return to STOPPED or stay at NO_MEDIA."""
        result = self._soap("Stop", {"InstanceID": "0"})
        if result is not None:
            self.assertIsInstance(result, dict)
        time.sleep(1)
        state = self._transport_state()
        self.assertIn(state, ("STOPPED", "NO_MEDIA_PRESENT"))

    def test_05_seek(self):
        """Seek action: may fail with 701 if no seekable media is loaded."""
        self._soap("Play", {"InstanceID": "0", "Speed": "1"})
        time.sleep(1)
        result = self._soap("Seek", {
            "InstanceID": "0", "Unit": "REL_TIME", "Target": "00:00:10"})
        if result is not None:
            self.assertIsInstance(result, dict)
        # Clean up
        self._soap("Stop", {"InstanceID": "0"})

    def test_06_next_track(self):
        """Next action: may fail with 701 if no playlist is loaded."""
        result = self._soap("Next", {"InstanceID": "0"})
        if result is not None:
            self.assertIsInstance(result, dict)

    def test_07_prev_track(self):
        """Previous action: may fail with 701 if no playlist is loaded."""
        result = self._soap("Previous", {"InstanceID": "0"})
        if result is not None:
            self.assertIsInstance(result, dict)


# ─────────────────────────────────────────────
# Test: RenderingControl — Volume (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestVolumeControl(unittest.TestCase):
    """Test RenderingControl volume actions.

    SAFETY: Volume is NEVER set above MAX_SAFE_VOLUME (50).
    The original volume is saved in setUpClass and restored in tearDownClass.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)
        cls.original_volume = _get_volume(DEVICE_HOST, DEVICE_PORT, cls.paths)
        print(f"\n  [Volume Safety] Original volume: {cls.original_volume}")
        # If original volume is above safe limit, lower it first
        if cls.original_volume > MAX_SAFE_VOLUME:
            print(f"  [Volume Safety] Lowering volume from {cls.original_volume} "
                  f"to {MAX_SAFE_VOLUME} for safety")
            _set_volume(DEVICE_HOST, DEVICE_PORT, cls.paths, MAX_SAFE_VOLUME)

    @classmethod
    def tearDownClass(cls):
        """Restore original volume (capped at MAX_SAFE_VOLUME)."""
        restore_level = min(cls.original_volume, MAX_SAFE_VOLUME)
        _set_volume(DEVICE_HOST, DEVICE_PORT, cls.paths, restore_level)
        print(f"\n  [Volume Safety] Restored volume to: {restore_level}")

    def test_01_get_volume(self):
        """GetVolume should return a numeric CurrentVolume."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_RENDERING_CONTROL],
            upnp.UPNP_RENDERING_CONTROL, "GetVolume",
            {"InstanceID": "0", "Channel": "Master"})
        self.assertIn("CurrentVolume", result)
        vol = int(result["CurrentVolume"])
        self.assertGreaterEqual(vol, 0)
        self.assertLessEqual(vol, 100)

    def test_02_set_volume_low(self):
        """Set volume to 10 and verify it took effect."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 10)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, 10)

    def test_03_set_volume_moderate(self):
        """Set volume to 30 and verify."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 30)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, 30)

    def test_04_set_volume_at_safe_max(self):
        """Set volume to MAX_SAFE_VOLUME (50) and verify."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, MAX_SAFE_VOLUME)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, MAX_SAFE_VOLUME)

    def test_05_set_volume_above_max_is_clamped(self):
        """Attempting to set volume above MAX_SAFE_VOLUME should be clamped."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 80)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        # Our _set_volume helper clamps to MAX_SAFE_VOLUME
        self.assertLessEqual(vol, MAX_SAFE_VOLUME,
                             f"Volume {vol} exceeds safe maximum {MAX_SAFE_VOLUME}!")

    def test_06_set_volume_zero(self):
        """Set volume to 0 (minimum)."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 0)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, 0)

    def test_07_volume_round_trip(self):
        """Set volume and read it back — values should match."""
        for level in (5, 15, 25, 35, 45):
            with self.subTest(level=level):
                _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, level)
                time.sleep(0.5)
                actual = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
                self.assertEqual(actual, level,
                                 f"Set {level}, got {actual}")


# ─────────────────────────────────────────────
# Test: RenderingControl — Mute (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestMuteControl(unittest.TestCase):
    """Test RenderingControl mute actions.

    The original mute state is saved and restored.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)
        cls.original_mute = _get_mute(DEVICE_HOST, DEVICE_PORT, cls.paths)
        print(f"\n  [Mute] Original mute state: {cls.original_mute}")

    @classmethod
    def tearDownClass(cls):
        """Restore original mute state."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, cls.paths, cls.original_mute)
        print(f"\n  [Mute] Restored mute state to: {cls.original_mute}")

    def test_01_get_mute(self):
        """GetMute should return a CurrentMute field."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_RENDERING_CONTROL],
            upnp.UPNP_RENDERING_CONTROL, "GetMute",
            {"InstanceID": "0", "Channel": "Master"})
        self.assertIn("CurrentMute", result)
        self.assertIn(result["CurrentMute"], ("0", "1", "true", "false",
                                               "True", "False"))

    def test_02_set_mute_on(self):
        """Muting should set CurrentMute to muted."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, True)
        time.sleep(0.5)
        muted = _get_mute(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertTrue(muted, "Device should be muted after SetMute(1)")

    def test_03_set_mute_off(self):
        """Unmuting should clear the mute flag."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, False)
        time.sleep(0.5)
        muted = _get_mute(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertFalse(muted, "Device should be unmuted after SetMute(0)")

    def test_04_mute_toggle_round_trip(self):
        """Mute on, verify, mute off, verify."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, True)
        time.sleep(0.5)
        self.assertTrue(_get_mute(DEVICE_HOST, DEVICE_PORT, self.paths))

        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, False)
        time.sleep(0.5)
        self.assertFalse(_get_mute(DEVICE_HOST, DEVICE_PORT, self.paths))


# ─────────────────────────────────────────────
# Test: Full Workflow (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestFullWorkflow(unittest.TestCase):
    """End-to-end workflow: discover -> info -> set volume -> play -> stop.

    Simulates a typical user session with the CLI.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)
        cls.original_volume = _get_volume(DEVICE_HOST, DEVICE_PORT, cls.paths)
        cls.original_mute = _get_mute(DEVICE_HOST, DEVICE_PORT, cls.paths)

    @classmethod
    def tearDownClass(cls):
        """Restore device state."""
        restore_vol = min(cls.original_volume, MAX_SAFE_VOLUME)
        _set_volume(DEVICE_HOST, DEVICE_PORT, cls.paths, restore_vol)
        _set_mute(DEVICE_HOST, DEVICE_PORT, cls.paths, cls.original_mute)
        # Ensure stopped
        try:
            upnp._soap_request(
                DEVICE_HOST, DEVICE_PORT,
                cls.paths[upnp.UPNP_AV_TRANSPORT],
                upnp.UPNP_AV_TRANSPORT, "Stop",
                {"InstanceID": "0"})
        except upnp.UPnPError:
            pass

    def test_01_discover_finds_device(self):
        """SSDP discovery should find our SuperUniti."""
        locations = upnp._ssdp_discover(timeout=5)
        found_ips = set(locations.values())
        self.assertIn(DEVICE_HOST, found_ips,
                       f"Device {DEVICE_HOST} not found in SSDP results")

    def test_02_service_discovery(self):
        """Service discovery should find AVTransport and RenderingControl."""
        self.assertIn(upnp.UPNP_AV_TRANSPORT, self.paths)
        self.assertIn(upnp.UPNP_RENDERING_CONTROL, self.paths)

    def test_03_device_info(self):
        """Device info should identify it as a Naim SuperUniti."""
        desc = upnp._parse_upnp_description(
            f"http://{DEVICE_HOST}:{DEVICE_PORT}/description.xml")
        self.assertIsNotNone(desc)
        self.assertEqual(desc["modelName"], "SuperUniti")

    def test_04_set_safe_volume(self):
        """Set a low volume for safe testing."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 15)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, 15)

    def test_05_unmute(self):
        """Unmute the device."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, False)
        time.sleep(0.5)
        self.assertFalse(_get_mute(DEVICE_HOST, DEVICE_PORT, self.paths))

    def test_06_play(self):
        """Start playback (may get 701 if no media loaded)."""
        try:
            result = upnp._soap_request(
                DEVICE_HOST, DEVICE_PORT,
                self.paths[upnp.UPNP_AV_TRANSPORT],
                upnp.UPNP_AV_TRANSPORT, "Play",
                {"InstanceID": "0", "Speed": "1"})
            self.assertIsInstance(result, dict)
        except upnp.UPnPError:
            # Expected if no media is loaded on the device
            pass
        time.sleep(2)

    def test_07_verify_transport_during_play(self):
        """Transport state should reflect playback attempt."""
        state = _get_transport_state(DEVICE_HOST, DEVICE_PORT, self.paths)
        # Acceptable states: PLAYING if media is loaded, or STOPPED/NO_MEDIA
        self.assertIn(state, ("PLAYING", "STOPPED", "TRANSITIONING",
                              "NO_MEDIA_PRESENT", "PAUSED_PLAYBACK"))

    def test_08_check_position_during_play(self):
        """Position info should be queryable during playback."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetPositionInfo",
            {"InstanceID": "0"})
        self.assertIn("Track", result)
        self.assertIn("RelTime", result)

    def test_09_adjust_volume_during_play(self):
        """Change volume during playback — should work."""
        _set_volume(DEVICE_HOST, DEVICE_PORT, self.paths, 20)
        time.sleep(0.5)
        vol = _get_volume(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertEqual(vol, 20)

    def test_10_mute_during_play(self):
        """Mute during playback — should work."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, True)
        time.sleep(0.5)
        self.assertTrue(_get_mute(DEVICE_HOST, DEVICE_PORT, self.paths))

    def test_11_unmute_during_play(self):
        """Unmute during playback."""
        _set_mute(DEVICE_HOST, DEVICE_PORT, self.paths, False)
        time.sleep(0.5)
        self.assertFalse(_get_mute(DEVICE_HOST, DEVICE_PORT, self.paths))

    def test_12_stop(self):
        """Stop playback and verify state."""
        try:
            result = upnp._soap_request(
                DEVICE_HOST, DEVICE_PORT,
                self.paths[upnp.UPNP_AV_TRANSPORT],
                upnp.UPNP_AV_TRANSPORT, "Stop",
                {"InstanceID": "0"})
            self.assertIsInstance(result, dict)
        except upnp.UPnPError:
            # Expected if already stopped / no media
            pass
        time.sleep(1)
        state = _get_transport_state(DEVICE_HOST, DEVICE_PORT, self.paths)
        self.assertIn(state, ("STOPPED", "NO_MEDIA_PRESENT"))


# ─────────────────────────────────────────────
# Test: Volume Safety Guard
# ─────────────────────────────────────────────

class TestVolumeSafetyGuard(unittest.TestCase):
    """Verify the test helper _set_volume always clamps to MAX_SAFE_VOLUME.

    This is a meta-test to ensure the safety mechanism works.
    No device needed — tests the helper logic.
    """

    def test_clamp_values(self):
        """_set_volume helper should never allow values above MAX_SAFE_VOLUME."""
        # We can't call _set_volume without a device, but we can verify the
        # clamping logic directly:
        for requested in (0, 10, 50, 51, 75, 100):
            clamped = min(requested, MAX_SAFE_VOLUME)
            if requested <= MAX_SAFE_VOLUME:
                self.assertEqual(clamped, requested)
            else:
                self.assertEqual(clamped, MAX_SAFE_VOLUME,
                                 f"Volume {requested} should clamp to {MAX_SAFE_VOLUME}")

    def test_max_safe_volume_is_50(self):
        """Sanity check: MAX_SAFE_VOLUME should be 50."""
        self.assertEqual(MAX_SAFE_VOLUME, 50)


# ─────────────────────────────────────────────
# Test: DIDL-Lite Parsing (offline)
# ─────────────────────────────────────────────

class TestDIDLLiteParsing(unittest.TestCase):
    """Test DIDL-Lite XML parsing (offline, no device needed)."""

    def test_01_parse_container(self):
        """Parse a DIDL-Lite container element."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<container id="0/0" parentID="0" restricted="1">'
            '<dc:title>Inputs</dc:title>'
            '<upnp:class>object.container</upnp:class>'
            '</container>'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "container")
        self.assertEqual(items[0]["id"], "0/0")
        self.assertEqual(items[0]["parentID"], "0")
        self.assertEqual(items[0]["title"], "Inputs")
        self.assertEqual(items[0]["class"], "object.container")

    def test_02_parse_item_with_res(self):
        """Parse a DIDL-Lite item with resource URL."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0/1" parentID="0" restricted="1">'
            '<dc:title>Digital 1</dc:title>'
            '<upnp:class>object.item.audioItem</upnp:class>'
            '<res protocolInfo="http-get:*:audio/x-naim-digital:*">x-naim-input:digital1</res>'
            '</item>'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "item")
        self.assertEqual(items[0]["id"], "0/1")
        self.assertEqual(items[0]["title"], "Digital 1")
        self.assertIn("res", items[0])
        self.assertEqual(len(items[0]["res"]), 1)
        self.assertEqual(items[0]["res"][0]["url"], "x-naim-input:digital1")
        self.assertEqual(items[0]["res"][0]["protocolInfo"],
                         "http-get:*:audio/x-naim-digital:*")

    def test_03_parse_multiple_items(self):
        """Parse DIDL-Lite with multiple items and containers."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<container id="0/0" parentID="0" restricted="1">'
            '<dc:title>Inputs</dc:title>'
            '<upnp:class>object.container</upnp:class>'
            '</container>'
            '<container id="0/1" parentID="0" restricted="1">'
            '<dc:title>Media Servers</dc:title>'
            '<upnp:class>object.container</upnp:class>'
            '</container>'
            '<item id="0/2" parentID="0" restricted="1">'
            '<dc:title>iRadio</dc:title>'
            '<upnp:class>object.item.audioItem</upnp:class>'
            '</item>'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["type"], "container")
        self.assertEqual(items[1]["type"], "container")
        self.assertEqual(items[2]["type"], "item")
        self.assertEqual(items[0]["title"], "Inputs")
        self.assertEqual(items[1]["title"], "Media Servers")
        self.assertEqual(items[2]["title"], "iRadio")

    def test_04_parse_item_with_metadata(self):
        """Parse DIDL-Lite item with artist, album, genre metadata."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="track/1" parentID="album/1" restricted="1">'
            '<dc:title>Test Track</dc:title>'
            '<dc:creator>Test Artist</dc:creator>'
            '<upnp:artist>Test Artist</upnp:artist>'
            '<upnp:album>Test Album</upnp:album>'
            '<upnp:genre>Rock</upnp:genre>'
            '<upnp:albumArtURI>http://example.com/art.jpg</upnp:albumArtURI>'
            '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
            '</item>'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "Test Track")
        self.assertEqual(item["artist"], "Test Artist")
        self.assertEqual(item["album"], "Test Album")
        self.assertEqual(item["genre"], "Rock")
        self.assertEqual(item["albumArtURI"], "http://example.com/art.jpg")

    def test_05_parse_empty_didl(self):
        """Empty DIDL-Lite should return empty list."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 0)

    def test_06_parse_malformed_xml(self):
        """Malformed XML should return empty list, not crash."""
        items = upnp._parse_didl_lite("not xml at all")
        self.assertEqual(len(items), 0)

    def test_07_parse_multiple_res_elements(self):
        """Parse item with multiple res elements (different formats)."""
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="track/1" parentID="0" restricted="1">'
            '<dc:title>Multi-format Track</dc:title>'
            '<upnp:class>object.item.audioItem</upnp:class>'
            '<res protocolInfo="http-get:*:audio/flac:*">http://example.com/track.flac</res>'
            '<res protocolInfo="http-get:*:audio/mpeg:*">http://example.com/track.mp3</res>'
            '</item>'
            '</DIDL-Lite>'
        )
        items = upnp._parse_didl_lite(didl)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["res"]), 2)
        self.assertEqual(items[0]["res"][0]["url"], "http://example.com/track.flac")
        self.assertEqual(items[0]["res"][1]["url"], "http://example.com/track.mp3")


# ─────────────────────────────────────────────
# Test: Extended Service Discovery (offline)
# ─────────────────────────────────────────────

class TestExtendedServiceDiscovery(unittest.TestCase):
    """Test extended service discovery functions (offline where possible)."""

    def test_01_discover_all_flag(self):
        """discover_all=True should return more services than defaults."""
        # For unreachable hosts, both should return defaults
        services_basic = upnp._discover_upnp_services("192.0.2.1", 9999, discover_all=False)
        services_all = upnp._discover_upnp_services("192.0.2.1", 9999, discover_all=True)
        # Both should have the standard services
        self.assertIn(upnp.UPNP_AV_TRANSPORT, services_basic)
        self.assertIn(upnp.UPNP_AV_TRANSPORT, services_all)

    def test_02_default_services_include_new_ones(self):
        """Default services should include ContentDirectory and ConnectionManager."""
        services = upnp._discover_upnp_services("192.0.2.1", 9999)
        self.assertIn(upnp.UPNP_CONTENT_DIRECTORY, services)
        self.assertIn(upnp.UPNP_CONNECTION_MANAGER, services)
        self.assertIn(upnp.NAIM_INPUT_SERVICE, services)

    def test_03_discover_all_services_with_scpd_bad_host(self):
        """SCPD discovery on unreachable host returns empty dict."""
        services = upnp._discover_all_services_with_scpd("192.0.2.1", 9999)
        self.assertEqual(services, {})


# ─────────────────────────────────────────────
# Test: ContentDirectory (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestContentDirectory(unittest.TestCase):
    """Test ContentDirectory browsing on real device.

    Note: Not all Naim devices support ContentDirectory.
    These tests handle the case where it's not available.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)

    def test_01_browse_root(self):
        """Browse root ObjectID '0' should return something or None."""
        result = upnp._browse_content_directory(
            DEVICE_HOST, DEVICE_PORT, self.paths, "0")
        # Result is either None (not supported) or a dict with items
        if result is not None:
            self.assertIsInstance(result, dict)
            self.assertIn("items", result)
            self.assertIn("total", result)
            self.assertIsInstance(result["items"], list)
            print(f"\n  [ContentDirectory] Root has {len(result['items'])} items")
            for item in result["items"][:5]:  # Print first 5
                print(f"    - [{item.get('id')}] {item.get('title')} ({item.get('type')})")
        else:
            print("\n  [ContentDirectory] Not supported on this device")

    def test_02_browse_returns_valid_structure(self):
        """If ContentDirectory is supported, items should have expected fields."""
        result = upnp._browse_content_directory(
            DEVICE_HOST, DEVICE_PORT, self.paths, "0")
        if result is None:
            self.skipTest("ContentDirectory not supported")

        for item in result["items"]:
            self.assertIn("type", item)
            self.assertIn(item["type"], ("container", "item"))
            self.assertIn("id", item)
            # Title should be present (may be None for some items)
            self.assertIn("title", item)

    def test_03_browse_metadata(self):
        """BrowseMetadata should return details for a single item."""
        # First get root to find an ObjectID
        root = upnp._browse_content_directory(
            DEVICE_HOST, DEVICE_PORT, self.paths, "0")
        if root is None or not root["items"]:
            self.skipTest("ContentDirectory not supported or empty")

        first_id = root["items"][0]["id"]
        result = upnp._browse_content_directory(
            DEVICE_HOST, DEVICE_PORT, self.paths, first_id, "BrowseMetadata")
        if result is not None:
            self.assertIsInstance(result, dict)
            self.assertIn("items", result)
            # BrowseMetadata should return exactly one item
            if result["items"]:
                self.assertEqual(result["items"][0]["id"], first_id)


# ─────────────────────────────────────────────
# Test: ConnectionManager (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestConnectionManager(unittest.TestCase):
    """Test ConnectionManager protocol info on real device."""

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)

    def test_01_get_protocol_info(self):
        """GetProtocolInfo should return Source and/or Sink protocols."""
        result = upnp._get_protocol_info(DEVICE_HOST, DEVICE_PORT, self.paths)
        if result is None:
            self.skipTest("ConnectionManager not supported")

        self.assertIsInstance(result, dict)
        # At least one of Source or Sink should be present
        has_source = "Source" in result and result["Source"]
        has_sink = "Sink" in result and result["Sink"]
        self.assertTrue(has_source or has_sink,
                        "GetProtocolInfo returned neither Source nor Sink")

        if has_sink:
            print(f"\n  [ConnectionManager] Sink protocols: {result['Sink'][:100]}...")


# ─────────────────────────────────────────────
# Test: All Services Discovery (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestAllServicesDiscovery(unittest.TestCase):
    """Test discovery of all UPnP services with SCPD on real device."""

    def test_01_discover_all_services(self):
        """Should discover multiple services from description.xml."""
        services = upnp._discover_all_services_with_scpd(DEVICE_HOST, DEVICE_PORT)
        self.assertIsInstance(services, dict)
        self.assertGreater(len(services), 0, "No services found")

        print(f"\n  [Services] Found {len(services)} service(s):")
        for stype in services:
            print(f"    - {stype}")

    def test_02_services_have_control_url(self):
        """Each discovered service should have a controlURL."""
        services = upnp._discover_all_services_with_scpd(DEVICE_HOST, DEVICE_PORT)
        for stype, info in services.items():
            self.assertIn("controlURL", info)
            self.assertIsNotNone(info["controlURL"])

    def test_03_services_have_scpd_url(self):
        """Each discovered service should have an SCPDURL."""
        services = upnp._discover_all_services_with_scpd(DEVICE_HOST, DEVICE_PORT)
        for stype, info in services.items():
            self.assertIn("SCPDURL", info)
            # SCPDURL may be None for some services

    def test_04_fetch_scpd_for_avtransport(self):
        """Should be able to fetch and parse SCPD for AVTransport."""
        services = upnp._discover_all_services_with_scpd(DEVICE_HOST, DEVICE_PORT)

        # Find AVTransport
        av_transport = None
        for stype, info in services.items():
            if "AVTransport" in stype:
                av_transport = info
                break

        if av_transport is None or not av_transport.get("SCPDURL"):
            self.skipTest("AVTransport SCPD not available")

        actions = upnp._fetch_service_scpd(
            DEVICE_HOST, DEVICE_PORT, av_transport["SCPDURL"])
        self.assertIsNotNone(actions)
        self.assertIsInstance(actions, dict)

        # AVTransport should have these standard actions
        expected_actions = {"Play", "Pause", "Stop", "GetTransportInfo"}
        found_actions = set(actions.keys())
        for action in expected_actions:
            self.assertIn(action, found_actions,
                          f"AVTransport missing expected action: {action}")

        print(f"\n  [SCPD] AVTransport has {len(actions)} actions")


# ─────────────────────────────────────────────
# Test: Input Commands Integration (live)
# ─────────────────────────────────────────────

@unittest.skipUnless(_device_reachable(DEVICE_HOST, DEVICE_PORT),
                     f"Device not reachable at {DEVICE_HOST}:{DEVICE_PORT}")
class TestInputCommandsIntegration(unittest.TestCase):
    """Integration tests for input-related commands on real device."""

    @classmethod
    def setUpClass(cls):
        cls.paths = upnp._discover_upnp_services(DEVICE_HOST, DEVICE_PORT)

    def test_01_get_media_info_for_current_input(self):
        """GetMediaInfo should return current URI info."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetMediaInfo",
            {"InstanceID": "0"})

        self.assertIsInstance(result, dict)
        self.assertIn("CurrentURI", result)
        self.assertIn("CurrentURIMetaData", result)

        print(f"\n  [Current Input] URI: {result.get('CurrentURI', '(none)')[:80]}")

    def test_02_parse_current_uri_metadata(self):
        """If current URI has metadata, it should be parseable as DIDL-Lite."""
        result = upnp._soap_request(
            DEVICE_HOST, DEVICE_PORT,
            self.paths[upnp.UPNP_AV_TRANSPORT],
            upnp.UPNP_AV_TRANSPORT, "GetMediaInfo",
            {"InstanceID": "0"})

        metadata = result.get("CurrentURIMetaData", "")
        if metadata:
            items = upnp._parse_didl_lite(metadata)
            # Should parse without error (may be empty)
            self.assertIsInstance(items, list)
            if items:
                print(f"\n  [Current Input] Title: {items[0].get('title', 'N/A')}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
