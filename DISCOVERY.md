# Naim Device Discovery — Protocol Analysis

Reverse-engineered from the Naim Android application (`com.naimaudio.naim.std`)
decompiled APK. All findings are based on static analysis of DEX bytecode strings,
obfuscated Java source, AndroidManifest.xml, embedded JSON configuration, and
live testing against real hardware.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Device Platforms](#2-device-platforms)
3. [Discovery Architecture](#3-discovery-architecture)
4. [SSDP / UPnP Discovery](#4-ssdp--upnp-discovery)
   - [M-SEARCH Request](#41-m-search-request)
   - [M-SEARCH Response](#42-m-search-response)
   - [UPnP Device Description XML](#43-upnp-device-description-xml)
   - [Naim Device Filtering](#44-naim-device-filtering)
5. [mDNS / DNS-SD Discovery](#5-mdns--dns-sd-discovery)
   - [Service Types](#51-service-types)
   - [Service Type Descriptions](#52-service-type-descriptions)
6. [TCP Port Scan Discovery](#6-tcp-port-scan-discovery)
7. [Device Verification](#7-device-verification)
8. [Discovery Configuration](#8-discovery-configuration)
9. [Discovery Flow](#9-discovery-flow)
10. [Device Classification](#10-device-classification)
11. [SSDP Event Types](#11-ssdp-event-types)
12. [UPnP Service URNs](#12-upnp-service-urns)
13. [Android Permissions](#13-android-permissions)
14. [Libraries Used](#14-libraries-used)
15. [Real-World Examples](#15-real-world-examples)
16. [CLI Tool Usage](#16-cli-tool-usage)
17. [Implementation Notes](#17-implementation-notes)

---

## 1. Overview

Naim devices advertise themselves on the local network so that the Naim app
(and other controllers) can find them without the user manually entering an
IP address. The app runs **four discovery mechanisms in parallel** and merges
the results. Once an IP address is obtained, all subsequent communication is
pure HTTP — discovery protocols are only used for the initial find.

There are two generations of Naim network products:

| Generation | Platform | Control Protocol | Discovery |
|------------|----------|-----------------|-----------|
| Modern (2017+) | "Leo" | HTTP REST API on port **15081** | SSDP + mDNS (`_leo._tcp`) |
| Legacy | S800 / earlier | UPnP/DLNA (AVTransport, RenderingControl) | SSDP + mDNS (`_sueS800Device._tcp`) |

---

## 2. Device Platforms

The app classifies discovered devices into three types (from the
`Discovered` class hierarchy in the decompiled source):

| Type | Description | Examples |
|------|-------------|---------|
| `Discovered.Leo` | Modern streaming platform with REST API on :15081 | Uniti Atom, Uniti Star, Uniti Nova, Uniti Core, Mu-so 2nd Gen |
| `Discovered.Legacy` | Older streamers using UPnP/DLNA control | SuperUniti, NDS, NDX, ND5 XS, NAC-N 272 |
| `Discovered.NetApiAnalyser` | Network analyser devices | Specialised diagnostic hardware |

Leo devices expose a rich REST API (documented in `PROTOCOL.md`).
Legacy devices are controlled via standard UPnP/DLNA SOAP actions
(AVTransport, RenderingControl, ConnectionManager) and a web UI on port 80.

---

## 3. Discovery Architecture

From the decompiled APK, the `DiscoveryManager` class orchestrates four
parallel discoverer implementations:

| Class | Mechanism | Protocol | Fallback Order |
|-------|-----------|----------|----------------|
| `MMUPnPDiscoverer` | SSDP multicast search | UDP 239.255.255.250:1900 | Primary |
| `DnssdDiscoverer` | mDNS/DNS-SD via JmDNS | UDP multicast 5353 | Primary |
| `ScannerDiscoverer` | Android NsdManager | mDNS (platform API) | Secondary |
| `PortScanDiscoveryManager` | TCP port scan for :15081 | TCP connect scan | Fallback |

All four run concurrently. Results are deduplicated by IP address and passed
through device resolvers (`LeoDeviceResolver`, `LegacyDeviceResolver`) before
being presented to the user.

---

## 4. SSDP / UPnP Discovery

SSDP (Simple Service Discovery Protocol) is the **primary** discovery
mechanism. The app uses the `mmupnp` library (v3.0.0, MIT License) to send
M-SEARCH requests and listen for responses.

### 4.1 M-SEARCH Request

The app sends UDP multicast M-SEARCH packets to the standard SSDP address:

```
M-SEARCH * HTTP/1.1
HOST: 239.255.255.250:1900
MAN: "ssdp:discover"
MX: 3
ST: upnp:rootdevice
USER-AGENT: UPnP/1.0 mmupnp/3.0.0

```

| Field | Value | Notes |
|-------|-------|-------|
| Destination | `239.255.255.250:1900` | Standard SSDP multicast group |
| Protocol | UDP | |
| `MAN` | `"ssdp:discover"` | Must be quoted, per UPnP spec |
| `MX` | `3` | Max response delay in seconds |
| `ST` | See search targets below | Sent as separate requests per target |
| `USER-AGENT` | `UPnP/1.0 mmupnp/3.0.0` | mmupnp library identifier |

**Search Targets (ST values):**

The app sends separate M-SEARCH requests for each of these:

```
upnp:rootdevice
urn:schemas-upnp-org:device:MediaRenderer:1
urn:schemas-upnp-org:device:MediaServer:1
urn:schemas-upnp-org:device:MediaServer:2
```

Additional user-agent strings observed in the codebase:

```
Android/4.0 UPnP/1.1 NaimUPnP/1.0
UPnP/1.0 DLNADOC/1.50 NaimUPnP/1.0
```

### 4.2 M-SEARCH Response

Responding devices send a unicast UDP reply back to the sender. The key
header is `LOCATION`, which points to the UPnP device description XML:

```
HTTP/1.1 200 OK
CACHE-CONTROL: max-age=1800
LOCATION: http://192.168.1.21:8080/description.xml
SERVER: Linux/3.x UPnP/1.0 Naim/1.0
ST: upnp:rootdevice
USN: uuid:5F9EC1B3-ED59-79BB-4530-0011F6AE6014::upnp:rootdevice
```

The `LOCATION` URL is fetched via HTTP GET to obtain the full device
description.

### 4.3 UPnP Device Description XML

The device description is a standard UPnP XML document served at the
`LOCATION` URL (typically `http://<ip>:8080/description.xml` for legacy
devices):

```xml
<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>S1</friendlyName>
    <manufacturer>Naim Audio Ltd.</manufacturer>
    <manufacturerURL>http://www.naimaudio.com</manufacturerURL>
    <modelDescription>Naim SuperUniti all-in-one audio player</modelDescription>
    <modelName>SuperUniti</modelName>
    <modelNumber>20-004-0007</modelNumber>
    <modelURL>https://www.naimaudio.com/product/superuniti</modelURL>
    <serialNumber>0011F6AE6014</serialNumber>
    <UDN>uuid:5F9EC1B3-ED59-79BB-4530-0011F6AE6014</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
        <SCPDURL>/RenderingControl/desc.xml</SCPDURL>
        <controlURL>/RenderingControl/ctrl</controlURL>
        <eventSubURL>/RenderingControl/evt</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        ...
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        ...
      </service>
    </serviceList>
    <presentationURL>http://192.168.1.21:80/index.asp</presentationURL>
  </device>
</root>
```

**Key fields extracted by the app:**

| XML Element | Usage |
|-------------|-------|
| `friendlyName` | Display name shown to the user |
| `manufacturer` | **Filtering** — must contain "Naim" to be accepted |
| `modelName` | Device model (e.g. "SuperUniti", "Uniti Atom") |
| `modelDescription` | Human-readable description |
| `serialNumber` | Hardware serial number |
| `UDN` | Unique Device Name (UUID) — used as device identity |
| `deviceType` | UPnP device type URN |
| `presentationURL` | Web interface URL (port 80 on legacy devices) |

### 4.4 Naim Device Filtering

After fetching the device description, the app filters out non-Naim devices.
A log message in the decompiled code confirms this:

```
DISCOVERY - Non Naim Product at <IP>
```

The filtering logic checks the `<manufacturer>` field in the XML. Accepted
values include:

- `Naim Audio Ltd.` (legacy devices)
- `Naim Audio` (modern devices)

Any device whose manufacturer string does **not** contain "naim"
(case-insensitive) is discarded.

---

## 5. mDNS / DNS-SD Discovery

mDNS (Multicast DNS) / DNS-SD (DNS Service Discovery) is the second primary
discovery mechanism, running in parallel with SSDP. The app uses the
**JmDNS** library (Apache License 2.0) for Bonjour/mDNS queries.

### 5.1 Service Types

The app browses these mDNS service types:

```
_leo._tcp.local.
_Naim-Updater._tcp.local.
_sueS800Device._tcp.local.
_sueGrouping._tcp.local.
```

Additional supplemental service types observed (used for feature detection,
not primary discovery):

```
_spotify-connect._tcp.local.
_googlezone._tcp.local.
_raop._tcp.local.
_afpovertcp._tcp.local.
_smb._tcp.local.
_workstation._tcp.local.
```

### 5.2 Service Type Descriptions

| Service Type | Platform | Description |
|-------------|----------|-------------|
| `_leo._tcp` | Modern | Primary service for new-generation Naim streamers (Leo platform). Indicates device supports REST API on port 15081. |
| `_Naim-Updater._tcp` | Both | Firmware update service. Present on devices that support OTA updates via the Naim app. |
| `_sueS800Device._tcp` | Legacy | Legacy streamer platform identifier. "S800" refers to the hardware platform used in older Naim network players (SuperUniti, NDS, NDX, etc.). |
| `_sueGrouping._tcp` | Both | Multiroom grouping service. Indicates the device supports Naim's multiroom functionality. |
| `_spotify-connect._tcp` | Both | Standard Spotify Connect service. Not Naim-specific but indicates Spotify support. |
| `_googlezone._tcp` | Modern | Google Cast / Chromecast built-in support. |
| `_raop._tcp` | Modern | AirPlay audio service (Remote Audio Output Protocol). |

**mDNS Configuration Parameters** (from embedded JSON config):

| Parameter | Description |
|-----------|-------------|
| `nsdTaskServiceType` | Single service type to discover |
| `nsdTaskServiceTypeList` | List of service types to browse |
| `nsdAcceptedServiceNameFilterList` | Whitelist filter for service names |
| `nsdDiscoveryTaskTimeoutMillis` | Browse timeout (default: 5000ms) |
| `nsdTaskTimeoutMillis` | Resolve timeout (default: 20000ms) |
| `nsdTaskRunTwice` | Whether to run discovery twice (default: false) |
| `nsdTaskAllowIPv6` | Allow IPv6 addresses (default: depends on config) |
| `nsdDiscoveryTaskUseNewMethod` | Toggle for updated NSD API |

---

## 6. TCP Port Scan Discovery

As a fallback when SSDP and mDNS fail (e.g. on networks that block
multicast), the app performs a TCP port scan of the local /24 subnet,
looking for **port 15081** (the Leo REST API port).

From the decompiled code:

```
PortScanDiscoveryManager - Found device with port 15081 open on : <IP>
```

**Scan Parameters** (from embedded JSON config):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `localDevicePingBlasterTaskMaxPings` | 50 | Max hosts to probe per batch |
| `localDevicePingBlasterTaskMinPings` | 5 | Min hosts before reporting |
| `localDevicePingBlasterTaskTimeoutSeconds` | 2 | TCP connect timeout per host |
| `localDevicePingBlasterTaskDelayMillis` | 40 | Delay between batches |
| `localDevicePingBlasterTaskTargetsPerBatch` | 5 | Hosts probed simultaneously |
| `localDevicePingBlasterTCPPingEnabled` | true | Enable TCP probing |
| `localDevicePingBlasterTaskMaxWithoutHTTPDeviceDiscoveryMillis` | 15000 | Max scan time |

**Limitations:**

- Only finds Leo-platform devices (legacy devices don't listen on 15081)
- Assumes a /24 subnet — does not detect the actual subnet mask
- Slower than SSDP/mDNS — used as a last resort

---

## 7. Device Verification

After a candidate IP is found via any discovery method, the app attempts to
verify and classify it:

### Leo Platform Verification

```
GET http://<ip>:15081/system
```

A successful JSON response confirms a modern Naim streamer. The response
includes model name, serial number, firmware version, and hardware details
(see `PROTOCOL.md` section 5.7 for the full `LeoSystem` schema).

### Legacy Device Verification

Legacy devices do not have port 15081. They are verified by:

1. The SSDP/mDNS discovery itself (manufacturer = "Naim Audio Ltd.")
2. The UPnP device description XML (parsed during SSDP flow)
3. The `presentationURL` in the XML points to a web UI on port 80

### Verification Sequence

```
Discovery candidate (IP) found
  |
  +-- Try GET http://<ip>:15081/system
  |     |
  |     +-- 200 OK + JSON → Leo platform device (modern)
  |     +-- Connection refused / timeout → not a Leo device
  |
  +-- If found via SSDP with manufacturer = "Naim*" → Legacy device
  |
  +-- If found via mDNS (_leo._tcp or _sueS800Device._tcp) → Naim device
  |
  +-- If found via port scan only and /system fails → discard
```

---

## 8. Discovery Configuration

The app embeds a JSON configuration object that controls discovery behaviour.
These values were extracted from the DEX bytecode:

```json
{
  "identifyEnabled": false,
  "ssdpQueryTaskMaxScanTimeMillis": 15000,
  "ssdpQueryTaskMaxFetchTimeMillis": 5000,
  "nsdDiscoveryTaskTimeoutMillis": 5000,
  "nsdTaskTimeoutMillis": 20000,
  "nsdTaskRunTwice": false,
  "pingBlasterTaskMaxPings": 50,
  "pingBlasterTaskMaxMillis": 30000,
  "pingBlasterTaskMinPings": 5,
  "localDevicePingBlasterTaskMaxPings": 50,
  "localDevicePingBlasterTaskMinPings": 5,
  "localDevicePingBlasterTaskTimeoutSeconds": 2,
  "localDevicePingBlasterTaskDelayMillis": 40,
  "localDevicePingBlasterTaskMaxWithoutHTTPDeviceDiscoveryMillis": 15000,
  "localDevicePingBlasterTaskMaxWithHTTPDeviceDiscoveryMillis": 15000,
  "localDevicePingBlasterTaskTargetsPerBatch": 5,
  "localDevicePingBlasterTCPPingEnabled": true,
  "maxScanTimeMillis": 45000,
  "maxScanTimeText": "2 minutes",
  "wifiInformationTaskTimeoutMillis": 20000,
  "wifiInformationTaskScanDelayMillis": 10000,
  "udpSessionTimeoutMaxTimeoutMillis": 119000,
  "wanSoapTaskInitialDelayMillis": 15000,
  "httpDeviceDiscoveryEnabled": false,
  "statusObjectFetchEnabled": false,
  "speedTestEnabled": false,
  "wanSoapTaskEnabled": false,
  "connectivityCheckEnabled": false,
  "dnsQueryEnabled": true,
  "tcpPortCheckEnabled": false,
  "udpPortCheckEnabled": false,
  "broadcastDiscoveryEnabled": false,
  "networkMapTracerouteEnabled": false,
  "hnapInformationTaskEnabled": false,
  "healthCheckEnabled": true,
  "routerHTTPTimeoutMillis": 2500,
  "arpMaxRuntimeMillis": 8000,
  "dnsURLToResolve": "google.com"
}
```

**Key timing parameters for reimplementation:**

| Phase | Timeout |
|-------|---------|
| SSDP scan | 15 seconds |
| SSDP XML fetch | 5 seconds per device |
| mDNS browse | 5 seconds |
| mDNS resolve | 20 seconds |
| Port scan per host | 2 seconds |
| Overall max scan | 45 seconds |

Additional config field names found (not in the default JSON above):

| Field | Description |
|-------|-------------|
| `ssdpQueryTaskSearchTargetList` | List of SSDP ST values to query |
| `nsdTaskServiceType` | Single mDNS service type |
| `nsdTaskServiceTypeList` | List of mDNS service types |
| `nsdAcceptedServiceNameFilterList` | Whitelist for service name patterns |
| `httpDeviceDiscoveryPath` | HTTP probe path |
| `httpDeviceDiscoveryPrefix` | HTTP probe URL prefix |
| `httpDeviceDiscoveryRegex` | Regex to match HTTP probe responses |
| `httpDeviceDiscoveryTimeoutMillis` | HTTP probe timeout |
| `broadcastDiscoveryDefinitions` | Broadcast packet definitions |

---

## 9. Discovery Flow

Complete discovery sequence as implemented in the Naim app:

```
DiscoveryManager.start()
  |
  +-- [Parallel] MMUPnPDiscoverer
  |     +-- Send M-SEARCH for each ST target
  |     +-- Collect LOCATION URLs from responses
  |     +-- Fetch description.xml for each LOCATION
  |     +-- Filter: manufacturer contains "Naim"
  |     +-- Non-Naim → log "Non Naim Product at <IP>", discard
  |     +-- Naim → extract friendlyName, model, serial, UDN
  |
  +-- [Parallel] DnssdDiscoverer (JmDNS)
  |     +-- Browse _leo._tcp.local.
  |     +-- Browse _Naim-Updater._tcp.local.
  |     +-- Browse _sueS800Device._tcp.local.
  |     +-- Browse _sueGrouping._tcp.local.
  |     +-- Resolve each discovered service → IP + port
  |
  +-- [Parallel] ScannerDiscoverer (Android NsdManager)
  |     +-- Same service types as DnssdDiscoverer
  |     +-- Platform-level mDNS (Android API)
  |
  +-- [Parallel / Fallback] PortScanDiscoveryManager
        +-- Get local IP, derive /24 subnet
        +-- TCP connect scan all 254 hosts on port 15081
        +-- Batch of 5 targets, 40ms delay between batches
        +-- 2-second timeout per connection attempt
  |
  +-- Merge & deduplicate results by IP address
  |
  +-- Resolve device type:
  |     +-- LeoDeviceResolver → GET /system on :15081
  |     +-- LegacyDeviceResolver → use UPnP description
  |
  +-- Cache results (DiscoveryCacheManager)
  |
  +-- Present to user
```

---

## 10. Device Classification

After discovery, devices are classified by their platform:

### Leo Platform (Modern)

- **Indicator:** Responds to `GET http://<ip>:15081/system` with JSON
- **mDNS service:** `_leo._tcp`
- **Control:** HTTP REST API on port 15081 (see `PROTOCOL.md`)
- **Examples:** Uniti Atom, Uniti Star, Uniti Nova, Uniti Core, Mu-so 2nd Gen,
  Mu-so Qb 2nd Gen

### Legacy Platform (S800)

- **Indicator:** No port 15081; has UPnP services on port 8080
- **mDNS service:** `_sueS800Device._tcp`
- **Control:** UPnP/DLNA SOAP actions + web UI on port 80
- **UPnP Services:**
  - `urn:schemas-upnp-org:service:AVTransport:1`
  - `urn:schemas-upnp-org:service:RenderingControl:1`
  - `urn:schemas-upnp-org:service:ConnectionManager:1`
  - `urn:schemas-dm-holdings-com:service:X_HtmlPageHandler:1` (Naim-specific)
- **Examples:** SuperUniti, NDS, NDX, NDX 2 (early firmware), ND5 XS,
  NAC-N 272, UnitiQute, UnitiQute 2, Mu-so 1st Gen

### Port Summary

| Port | Protocol | Platform | Purpose |
|------|----------|----------|---------|
| 15081 | HTTP | Leo | REST API (JSON) |
| 8080 | HTTP | Legacy | UPnP device description + SOAP control |
| 80 | HTTP | Legacy | Web UI (`presentationURL`) |
| 1900 | UDP | Both | SSDP multicast (discovery only) |
| 5353 | UDP | Both | mDNS (discovery only) |

---

## 11. SSDP Event Types

The SSDP protocol uses several message types. All were found in the
decompiled mmupnp library code:

| Event | Direction | Description |
|-------|-----------|-------------|
| `ssdp:discover` | Client → Multicast | M-SEARCH query to find devices |
| `ssdp:alive` | Device → Multicast | Periodic announcement that a device is online |
| `ssdp:byebye` | Device → Multicast | Notification that a device is going offline |
| `ssdp:update` | Device → Multicast | Notification that device description has changed |
| `ssdp:all` | Client → Multicast | Search for all SSDP services (catch-all ST) |

The app also listens for unsolicited `ssdp:alive` NOTIFY messages on the
multicast group, which is how devices that come online after the initial
M-SEARCH are detected.

---

## 12. UPnP Service URNs

Complete list of UPnP device and service URNs found in the decompiled code:

### Device Types

```
urn:schemas-upnp-org:device:Basic:1
urn:schemas-upnp-org:device:MediaServer:1
urn:schemas-upnp-org:device:MediaServer:2
urn:schemas-upnp-org:device:MediaRenderer:1
urn:schemas-upnp-org:device:InternetGatewayDevice:1
urn:schemas-upnp-org:device:InternetGatewayDevice:2
urn:schemas-upnp-org:device:WLANAccessPointDevice:1
urn:schemas-wifialliance-org:device:WFADevice:1
```

### Service Types

```
urn:schemas-upnp-org:service:AVTransport:1
urn:schemas-upnp-org:service:RenderingControl:1
urn:schemas-upnp-org:service:ConnectionManager:1
urn:schemas-upnp-org:service:ContentDirectory:1
urn:schemas-upnp-org:service:ContentDirectory:2
urn:schemas-upnp-org:service:ContentManager:1
urn:schemas-upnp-org:service:ContentManager:2
urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1
```

### Naim-Specific Service

```
urn:schemas-dm-holdings-com:service:X_HtmlPageHandler:1
```

This vendor-specific service is found on legacy Naim devices and provides
the HTML-based page handling used by the web UI on port 80.

### UPnP XML Namespaces

```
urn:schemas-upnp-org:device-1-0
urn:schemas-upnp-org:service-1-0
urn:schemas-upnp-org:control-1-0
urn:schemas-upnp-org:event-1-0
urn:schemas-upnp-org:metadata-1-0/upnp/
urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/
```

---

## 13. Android Permissions

The following Android permissions are required for discovery (from
`AndroidManifest.xml`):

| Permission | Purpose |
|------------|---------|
| `INTERNET` | HTTP communication with discovered devices |
| `ACCESS_NETWORK_STATE` | Check network connectivity before scanning |
| `CHANGE_WIFI_MULTICAST_STATE` | Enable multicast reception for SSDP and mDNS |
| `ACCESS_WIFI_STATE` | Read WiFi connection info (SSID, gateway IP) |
| `CHANGE_WIFI_STATE` | WiFi scan for network discovery |
| `CHANGE_NETWORK_STATE` | Network configuration |
| `ACCESS_COARSE_LOCATION` | Required for WiFi SSID access on Android 8+ |
| `ACCESS_FINE_LOCATION` | Required for WiFi SSID access on Android 10+ |
| `BLUETOOTH` | Bluetooth device discovery |
| `BLUETOOTH_ADMIN` | Bluetooth pairing |
| `BLUETOOTH_SCAN` | BLE scanning (Android 12+) |
| `BLUETOOTH_CONNECT` | BLE connection (Android 12+) |

**Note:** `CHANGE_WIFI_MULTICAST_STATE` is critical. Without it, the Android
OS will filter out multicast UDP packets, preventing both SSDP and mDNS from
working. The app acquires a `WifiManager.MulticastLock` during discovery.

---

## 14. Libraries Used

| Library | License | Purpose |
|---------|---------|---------|
| **mmupnp** v3.0.0 | MIT (OHMAE Ryosuke) | UPnP/SSDP discovery and control point |
| **JmDNS** | Apache 2.0 | mDNS/Bonjour service discovery |
| **RouteThis SDK** | Commercial | Network diagnostics (ping blaster, port scan) |
| **OkHttp3** | Apache 2.0 | HTTP client for REST API |
| **Retrofit2** | Apache 2.0 | REST API interface |
| **Moshi** | Apache 2.0 | JSON serialization |

Android system APIs:

| API | Purpose |
|-----|---------|
| `NsdManager` | Platform mDNS discovery (secondary to JmDNS) |
| `WifiManager` | Multicast lock, WiFi info |
| `ConnectivityManager` | Network state |

---

## 15. Real-World Examples

### SuperUniti (Legacy Device) at 192.168.1.21

**SSDP Response:**
```
LOCATION: http://192.168.1.21:8080/description.xml
```

**UPnP Description (key fields):**
```xml
<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
<friendlyName>S1</friendlyName>
<manufacturer>Naim Audio Ltd.</manufacturer>
<modelDescription>Naim SuperUniti all-in-one audio player</modelDescription>
<modelName>SuperUniti</modelName>
<modelNumber>20-004-0007</modelNumber>
<serialNumber>0011F6AE6014</serialNumber>
<UDN>uuid:5F9EC1B3-ED59-79BB-4530-0011F6AE6014</UDN>
<presentationURL>http://192.168.1.21:80/index.asp</presentationURL>
```

**UPnP Services:**
- `RenderingControl:1` (volume, mute)
- `ConnectionManager:1` (media transport setup)
- `AVTransport:1` (play, pause, stop, seek)
- `X_HtmlPageHandler:1` (Naim web UI control)

**Open Ports:**
- 80 (web UI)
- 8080 (UPnP description + SOAP control)

**Port 15081:** Not available (legacy device)

### Uniti Atom (Leo Device) at 192.168.1.20

**SSDP Response:**
```
LOCATION: http://192.168.1.20:<port>/description.xml
```

**Verification:**
```
GET http://192.168.1.20:15081/system → 200 OK (JSON)
```

**Open Ports:**
- 15081 (REST API)
- 8080 (UPnP description, may vary)

---

## 16. CLI Tool Usage

The `naim_control.py` CLI implements three of the four discovery mechanisms
(excluding Android NsdManager, which is platform-specific):

```bash
# Basic discovery (5-second timeout)
python3 naim_control.py discover

# Extended scan for slower networks
python3 naim_control.py discover --timeout 10
```

**Example output:**

```
Scanning for Naim devices (timeout=5s)...

Found 2 Naim device(s):

  [1] Uniti Atom
      IP:       192.168.1.20
      Model:    Uniti Atom
      Platform: leo (REST API :15081)
      Found via: ssdp

  [2] S1
      IP:       192.168.1.21
      Model:    SuperUniti
      Serial:   0011F6AE6014
      Platform: legacy (UPnP/DLNA)
      Found via: ssdp
```

**Discovery methods in the CLI:**

| Method | Always Available | Notes |
|--------|-----------------|-------|
| SSDP | Yes | Uses only Python standard library (`socket`, `xml.etree`) |
| mDNS | Requires `zeroconf` package | `pip install zeroconf` — gracefully skipped if absent |
| Port scan | Yes | Fallback only — runs if SSDP and mDNS find nothing |

---

## 17. Implementation Notes

### Multicast Considerations

- SSDP multicast requires the network to allow UDP multicast traffic.
  Some managed switches, VLANs, or WiFi isolation settings may block it.
- On macOS/Linux, no special privileges are needed for sending multicast
  UDP. On some systems, receiving multicast may require `SO_REUSEADDR`.
- The secondary multicast address `239.255.255.246` (SSDP Site-Local) was
  also found in the decompiled code but is not used in the primary
  discovery flow.

### mDNS Considerations

- The `zeroconf` Python package is optional. If not installed, only SSDP
  and port scanning are used.
- mDNS operates on UDP port 5353 (multicast group `224.0.0.251`).
- Service names may include the device hostname or friendly name.

### Legacy Device Control

Legacy Naim devices (SuperUniti, NDS, etc.) cannot be controlled via the
REST API documented in `PROTOCOL.md`. They use standard UPnP/DLNA SOAP
actions:

- **Volume:** `RenderingControl:1` → `SetVolume`, `GetVolume`, `SetMute`
- **Transport:** `AVTransport:1` → `Play`, `Pause`, `Stop`, `Seek`, `Next`, `Previous`
- **Input selection:** Via the web UI on port 80 or `X_HtmlPageHandler:1`

### Deduplication

The app deduplicates by IP address. A single device may respond to multiple
SSDP search targets and multiple mDNS service types. Only the first
occurrence is kept, with data enriched from subsequent discoveries.

### Caching

The app maintains a `DiscoveryCacheManager` that persists discovered device
information. Previously-seen devices can be connected to directly without
re-running discovery. The CLI tool does not implement caching.
