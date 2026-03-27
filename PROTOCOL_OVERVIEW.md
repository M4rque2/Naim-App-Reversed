# Naim Device Control Protocols — Overview

This document provides a high-level overview of the communication protocols used by Naim audio devices, as reverse-engineered from the official Naim Android application.

## Protocol Summary

Naim devices use **three distinct communication protocols** depending on the device generation and the type of operation:

| Protocol | Port | Transport | Devices | Primary Use |
|----------|------|-----------|---------|-------------|
| **REST API (Leo)** | 15081 | HTTP/JSON | Newer (Uniti, Mu-so 2nd gen) | Full device control |
| **UPnP/DLNA** | 8080 | HTTP/SOAP/XML | All devices | Playback, volume, discovery |
| **n-Stream (BridgeCo)** | 15555 | TCP/XML | Legacy devices | Input switching, preamp control |

---

## 1. REST API (Leo Protocol)

**Target Devices:** Uniti Atom, Uniti Star, Uniti Nova, Mu-so 2nd gen, NAC-N 272, ND5 XS 2, NDX 2, etc.

### Overview

The Leo protocol is a modern REST API that provides comprehensive control over newer Naim devices. It uses simple HTTP requests with JSON payloads.

### Key Characteristics

| Property | Value |
|----------|-------|
| Port | **15081** (hardcoded) |
| Transport | HTTP/1.1 (plain, not HTTPS) |
| Data Format | JSON |
| Authentication | None (local network only) |
| Real-time Updates | Server-Sent Events (SSE) |

### Capabilities

- **Full playback control** (play, pause, stop, seek, next/prev)
- **Volume and mute control**
- **Input/source selection and configuration**
- **Power management** (on/off/standby)
- **Multiroom control**
- **Alarm/timer management**
- **Firmware updates**
- **Device configuration**

### Implementation

```python
# Example: Set volume to 50
import requests
requests.put("http://192.168.1.100:15081/levels/room", params={"volume": 50})

# Example: Get now playing
response = requests.get("http://192.168.1.100:15081/nowplaying")
print(response.json())
```

### CLI Tool

```bash
# Use naim_control_rest.py for newer devices
./naim_control_rest.py --host 192.168.1.100 volume-set --level 50
./naim_control_rest.py --host 192.168.1.100 play
./naim_control_rest.py --host 192.168.1.100 input-set --input HDMI1
```

**Detailed Documentation:** See `PROTOCOLS_DETAILED.md`, Part A (Sections 1-12)

---

## 2. UPnP/DLNA Protocol

**Target Devices:** All Naim network streamers (both legacy and newer)

### Overview

UPnP/DLNA is a standard protocol for media device control. Naim devices implement standard UPnP services for playback and volume control, making them compatible with any UPnP control point.

### Key Characteristics

| Property | Value |
|----------|-------|
| Port | **8080** (typical) |
| Transport | HTTP with SOAP XML |
| Discovery | SSDP multicast |
| Authentication | None |

### Services Implemented

| Service | URN | Purpose |
|---------|-----|---------|
| AVTransport | `urn:schemas-upnp-org:service:AVTransport:1` | Playback control |
| RenderingControl | `urn:schemas-upnp-org:service:RenderingControl:1` | Volume, mute |
| ConnectionManager | `urn:schemas-upnp-org:service:ConnectionManager:1` | Protocol info |
| ContentDirectory | `urn:schemas-upnp-org:service:ContentDirectory:1` | Media browsing |

### Capabilities

- **Playback control** (play, pause, stop, seek, next/prev)
- **Volume and mute control**
- **Device discovery** via SSDP
- **Media browsing** (on supported devices)
- **Transport state queries**

### Limitations

- **Cannot switch inputs** on legacy devices (use n-Stream instead)
- No power control
- No device configuration
- Limited real-time updates

### Implementation

```python
# SOAP request for Play action
import urllib.request

soap_body = '''<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
      <InstanceID>0</InstanceID>
      <Speed>1</Speed>
    </u:Play>
  </s:Body>
</s:Envelope>'''

req = urllib.request.Request(
    "http://192.168.1.21:8080/AVTransport/ctrl",
    data=soap_body.encode(),
    headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": '"urn:schemas-upnp-org:service:AVTransport:1#Play"'
    }
)
```

### CLI Tool

```bash
# Use naim_control_upnp.py for UPnP operations
./naim_control_upnp.py --host 192.168.1.21 play
./naim_control_upnp.py --host 192.168.1.21 volume-set --level 50
./naim_control_upnp.py --host 192.168.1.21 transport-info
```

**Detailed Documentation:** See `PROTOCOLS_DETAILED.md`, Part B (Sections 13-21)

---

## 3. n-Stream (BridgeCo) Protocol

**Target Devices:** Legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, NAC-N 272, etc.)

### Overview

The n-Stream protocol is a proprietary TCP-based protocol built on the BridgeCo platform. It provides access to device-specific features that are not available via UPnP, most importantly **input/source switching**.

### Key Characteristics

| Property | Value |
|----------|-------|
| Port | **15555** |
| Transport | Raw TCP |
| Data Format | XML with Base64-encoded commands |
| Authentication | None |

### Protocol Architecture

The protocol has two layers:

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│   NVM Commands: *NVM SETINPUT, *NVM GETINPUT, etc.          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Tunnel Layer                             │
│   XML: <command><name>TunnelToHost</name>...</command>      │
│   Payload: Base64-encoded NVM command                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   BC (BridgeCo) Layer                        │
│   Connection setup, API version negotiation                  │
│   XML: <command><name>RequestAPIVersion</name>...</command> │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     TCP Socket                               │
│   Port 15555, UTF-8 encoded XML                              │
└─────────────────────────────────────────────────────────────┘
```

### Required Initialization Sequence

Before NVM commands work, the connection MUST be initialized:

**Step 1: RequestAPIVersion (BC layer)**
```xml
<command>
  <name>RequestAPIVersion</name>
  <id>1</id>
  <map>
    <item><name>module</name><string>naim</string></item>
    <item><name>version</name><string>1</string></item>
  </map>
</command>
```

**Step 2: Enable unsolicited messages (Tunnel layer)**
```xml
<command>
  <name>TunnelToHost</name>
  <id>2</id>
  <map>
    <item>
      <name>data</name>
      <base64>Kk5WTSBTRVRVT1NPTElDSVRFRCBPTg0=</base64>
      <!-- Base64 of "*NVM SETUNSOLICITED ON\r" -->
    </item>
  </map>
</command>
```

**Step 3: Now NVM commands will work**

### NVM Commands

| Command | Description |
|---------|-------------|
| `*NVM PRODUCT\r` | Get product type |
| `*NVM VERSION\r` | Get firmware version |
| `*NVM SETINPUT <input>\r` | Switch to specified input |
| `*NVM GETINPUT\r` | Get current input |
| `*NVM INPUT+\r` | Cycle to next input |
| `*NVM INPUT-\r` | Cycle to previous input |
| `*NVM GETINPUTBLK\r` | Get all inputs (bulk query) |
| `*NVM SETINPUTENABLED <input> ON\|OFF\r` | Enable/disable an input |
| `*NVM GETINPUTENABLED <input>\r` | Check if input is enabled |
| `*NVM SETINPUTNAME <input> "<name>"\r` | Set input display name/alias |
| `*NVM GETINPUTNAME <input>\r` | Get input display name |
| `*NVM SETUNSOLICITED ON\r` | Enable unsolicited messages |
| `*NVM VOL+\r` / `*NVM VOL-\r` | Volume up/down |
| `*NVM GETPREAMP\r` | Get preamp status |
| `*NVM GETMAC\r` | Get MAC address |

### Valid Input Names

| Category | Inputs |
|----------|--------|
| Streaming | `UPNP`, `IRADIO`, `SPOTIFY`, `TIDAL`, `AIRPLAY`, `BLUETOOTH` |
| Digital | `DIGITAL1` through `DIGITAL10` |
| Analog | `ANALOGUE1` through `ANALOGUE5`, `PHONO` |
| Other | `USB`, `CD`, `FM`, `DAB`, `FRONT`, `MULTIROOM`, `IPOD` |
| HDMI | `HDMI1` through `HDMI5` |

### Capabilities

- **Input/source switching** (primary use case)
- **Input management** (enable/disable, rename/alias)
- **Preamp control** (volume, balance, trim)
- **Device information** (product type, version, MAC)
- **Alarm/timer management**
- **Language settings**
- **IR remote learning**

### CLI Tool

```bash
# Use naim_control_nstream.py for n-Stream operations
./naim_control_nstream.py --host 192.168.1.21 inputs              # List all inputs on device
./naim_control_nstream.py --host 192.168.1.21 set-input --input DIGITAL2
./naim_control_nstream.py --host 192.168.1.21 set-input --input UPNP
./naim_control_nstream.py --host 192.168.1.21 get-input
./naim_control_nstream.py --host 192.168.1.21 input-up
./naim_control_nstream.py --host 192.168.1.21 input-down

# Input management
./naim_control_nstream.py --host 192.168.1.21 input-enable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-disable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-enabled --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-rename --input DIGITAL1 --name "TV Audio"
./naim_control_nstream.py --host 192.168.1.21 input-name --input DIGITAL1

# Device info
./naim_control_nstream.py --host 192.168.1.21 product
./naim_control_nstream.py --host 192.168.1.21 version
./naim_control_nstream.py --host 192.168.1.21 mac
./naim_control_nstream.py --host 192.168.1.21 preamp

# Use -v for verbose debug output
./naim_control_nstream.py --host 192.168.1.21 set-input --input DIGITAL2 -v
```

**Detailed Documentation:** See `PROTOCOLS_DETAILED.md`, Section 21

---

## Protocol Selection Guide

### Which protocol should I use?

```
Is your device a newer model (Uniti series, Mu-so 2nd gen)?
│
├─ YES → Use REST API (port 15081)
│        Tool: naim_control_rest.py
│        Capabilities: Everything
│
└─ NO (Legacy device: SuperUniti, NDS, NDX, UnitiQute, etc.)
   │
   ├─ For playback control (play/pause/stop/volume)?
   │  └─ Use UPnP/DLNA (port 8080)
   │     Tool: naim_control_upnp.py
   │
   └─ For input switching/management?
      └─ Use n-Stream (port 15555)
         Tool: naim_control_nstream.py
```

### Device Detection

To detect which protocols a device supports:

```bash
# Check for REST API (newer devices)
curl -s http://192.168.1.100:15081/system | head -c 100

# Check for UPnP (all devices)
curl -s http://192.168.1.100:8080/description.xml | head -c 100

# Check for n-Stream (legacy devices)
nc -zv 192.168.1.100 15555
```

---

## Protocol Comparison

| Feature | REST API | UPnP/DLNA | n-Stream |
|---------|----------|-----------|----------|
| **Port** | 15081 | 8080 | 15555 |
| **Transport** | HTTP/JSON | HTTP/SOAP/XML | TCP/XML |
| **Playback Control** | ✅ Full | ✅ Full | ❌ No |
| **Volume Control** | ✅ | ✅ | ✅ (via NVM) |
| **Input Switching** | ✅ | ❌ | ✅ |
| **Power Control** | ✅ | ❌ | ❌ |
| **Device Discovery** | mDNS | SSDP | ❌ |
| **Real-time Updates** | ✅ SSE | ❌ | ❌ |
| **Multiroom** | ✅ | ❌ | ❌ |
| **Newer Devices** | ✅ | ✅ | ❌ |
| **Legacy Devices** | ❌ | ✅ | ✅ |

---

## File Structure

| File | Description |
|------|-------------|
| `PROTOCOL_OVERVIEW.md` | This document - high-level overview |
| `PROTOCOLS_DETAILED.md` | Detailed protocol specifications |
| `USAGE.md` | CLI usage guide and examples |
| `naim_control_rest.py` | CLI tool for REST API (newer devices, port 15081) |
| `naim_control_upnp.py` | CLI tool for UPnP/DLNA (all devices, port 8080) |
| `naim_control_nstream.py` | CLI tool for n-Stream/BridgeCo (legacy devices, port 15555) |

---

## References

### Source Analysis

All protocol information was reverse-engineered from:
- Naim Android app (decompiled APK)
- Network traffic analysis
- Device testing on Naim SuperUniti

### Key Source Files (from decompiled app)

| File | Protocol | Purpose |
|------|----------|---------|
| `LeoApiService.java` | REST API | Retrofit interface definitions |
| `UnitiConnectionManagerService.java` | n-Stream | NVM command implementations |
| `Connection.java` | n-Stream | TCP socket management |
| `TunnelManager.java` | n-Stream | Initialization sequence |
| `CommandTunnelSendMessage.java` | n-Stream | XML/Base64 encoding |
| `TunnelCommand.java` | n-Stream | Command structure |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-27 | Separated n-Stream into dedicated `naim_control_nstream.py` script |
| 2026-03-27 | Added input management commands (enable/disable, rename) |
| 2026-03-27 | Added GETINPUTBLK for listing device inputs |
| 2026-03-25 | Added n-Stream initialization sequence (RequestAPIVersion + SETUNSOLICITED) |
| 2026-03-25 | Created protocol overview document |
| 2026-03-24 | Discovered n-Stream protocol for input switching |
| 2026-03-23 | Initial UPnP protocol documentation |
