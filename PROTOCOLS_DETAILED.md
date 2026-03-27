# Naim Streamer Control Protocols — Detailed Specifications

> **For a high-level overview of all protocols, see [PROTOCOL_OVERVIEW.md](PROTOCOL_OVERVIEW.md)**

Reverse-engineered from the Naim Android application (decompiled APK).
All findings are based on static analysis of Retrofit interface declarations,
Moshi JSON model classes, and enum definitions found in the decompiled source.

Naim devices use **three distinct control protocols** depending on the device generation:

| Protocol | Script | Devices | Port |
|----------|--------|---------|------|
| **REST API (Leo)** | `naim_control_rest.py` | Newer devices (Uniti series, Mu-so 2nd gen, etc.) | 15081 |
| **UPnP/DLNA** | `naim_control_upnp.py` | All devices (playback, volume) | 8080 |
| **n-Stream (BridgeCo)** | `naim_control_nstream.py` | Legacy devices (input switching/management) | 15555 |

---

## Table of Contents

### Part A — REST API (newer devices)

1. [Transport Layer](#1-transport-layer)
2. [Request & Response Conventions](#2-request--response-conventions)
3. [Device Discovery](#3-device-discovery)
4. [Endpoint Map](#4-endpoint-map)
5. [JSON Data Models](#5-json-data-models)
   - [NowPlaying](#51-nowplaying)
   - [AudioLevels](#52-audiolevels)
   - [Power](#53-power)
   - [Inputs / Input](#54-inputs--input)
   - [Outputs / Output](#55-outputs--output)
   - [Multiroom](#56-multiroom)
   - [LeoSystem](#57-leosystem)
   - [Update](#58-update)
   - [Alarms](#59-alarms)
   - [Favourites](#510-favourites)
6. [Enumeration Reference](#6-enumeration-reference)
7. [Command Pattern](#7-command-pattern)
8. [USSI Path System](#8-ussi-path-system)
9. [Real-time Updates (SSE)](#9-real-time-updates-sse)
10. [Error Handling](#10-error-handling)
11. [HTTP Client Behaviour](#11-http-client-behaviour)
12. [Known Limitations & Notes](#12-known-limitations--notes)

### Part B — UPnP/DLNA (all devices)

13. [UPnP/DLNA Protocol Overview](#13-upnpdlna-protocol-overview)
14. [UPnP Services & Control URLs](#14-upnp-services--control-urls)
15. [SOAP Action Reference](#15-soap-action-reference)
16. [Input Discovery via ContentDirectory](#16-input-discovery-via-contentdirectory)
17. [ConnectionManager Service](#17-connectionmanager-service)
18. [Input Settings Limitations on Legacy Devices](#18-input-settings-limitations-on-legacy-devices)
19. [Service Discovery via SCPD](#19-service-discovery-via-scpd)
20. [X_HtmlPageHandler Service (DM Holdings)](#20-x_htmlpagehandler-service-dm-holdings)

### Part C — n-Stream/BridgeCo Protocol (legacy devices)

21. [SuperUniti Device Profile](#21-superuniti-device-profile)
22. [n-Stream Protocol Architecture](#22-n-stream-protocol-architecture)
23. [NVM Command Reference](#23-nvm-command-reference)

---

# Part A — REST API (newer devices)

## 1. Transport Layer

| Property | Value |
|----------|-------|
| Protocol | HTTP/1.1 (plain, not HTTPS) |
| Port | **15081** (hardcoded in firmware) |
| Base URL | `http://<device-ip>:15081/` |
| Authentication | None — unauthenticated local network access |
| Content-Type | `application/json` |
| Encoding | UTF-8 |

The port `15081` is confirmed hardcoded in `LeoSimpleNetworking.java` and
`LeoWidgetNetworking.java` where it is embedded directly in URL strings:

```
http://<ip>:15081/api
http://<ip>:15081/power
http://<ip>:15081/system
```

The main application uses Retrofit2 with a dynamic base URL constructed as:

```
"http://" + device.ipAddress + ":" + device.port + "/"
```

where `device.port` is always populated with `15081` from the UPnP discovery payload.

---

## 2. Request & Response Conventions

### HTTP Methods

| Method | Usage |
|--------|-------|
| `GET` | Read state **or** trigger an action (via `?cmd=` parameter) |
| `PUT` | Mutate a setting (parameters passed as query string) |
| `POST` | Create a new resource (body is a JSON array or form parameters) |
| `DELETE` | Remove a resource |

### Query Parameters vs. Body

- **GET commands** pass all parameters as query string: `?cmd=seek&position=90`
- **PUT settings** pass all parameters as query string: `?volume=40&mute=false`
- **POST creates** pass creation parameters as query string; when a body is
  required (e.g. track list, image binary), it is passed as the request body
- **DELETE** uses only the path — no parameters

### Response Format

All responses are JSON objects. There is no envelope wrapper — the top-level
object *is* the data model. Empty responses (action commands) return HTTP `200`
with an empty body or `{}`.

```http
GET /nowplaying HTTP/1.1
Host: 192.168.1.50:15081

HTTP/1.1 200 OK
Content-Type: application/json

{"title":"Clair de Lune","artistName":"Debussy", ...}
```

### Error Responses

HTTP status codes are used conventionally:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request / invalid parameter value |
| 404 | Endpoint or USSI does not exist on this device |
| 500 | Internal device error |

Error bodies typically contain a JSON object with an error description.

---

## 3. Device Discovery

Naim devices advertise themselves on the local network via **UPnP (SSDP)**.
The app uses the `mmupnp` library to listen for `ssdp:alive` multicast messages
on `239.255.255.250:1900`.

The UPnP device description XML provides:
- Device IP address
- Device UDN (Unique Device Name)
- Model name, friendly name, serial number
- The control port (`15081`)

Once the IP is known, all subsequent communication is pure HTTP to port 15081 —
UPnP is only used for initial discovery.

**mDNS** (`_naim._tcp.local`) may also be used as a secondary discovery
mechanism but the primary path seen in the codebase is UPnP.

---

## 4. Endpoint Map

Complete list of all API paths found in the decompiled Retrofit interfaces.

### System & Device

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api` | API feature versions |
| GET | `/system` | Full system information |
| GET | `/system/usage` | CPU/memory usage |
| GET | `/system/datetime` | Device clock |
| GET | `/system?cmd=reboot` | Reboot device |
| GET | `/system?cmd=kick` | Keep-alive / prevent standby |
| GET | `/system?cmd=reset&what=<target>` | Factory reset (requires `Authorization` header) |
| PUT | `/system?firstTimeSetupComplete=<bool>` | Mark setup complete |

### Power

| Method | Path | Description |
|--------|------|-------------|
| GET | `/power` | Power state |
| PUT | `/power?system=<state>` | Set power state |
| PUT | `/power?serverMode=<bool>` | Enable/disable server mode |
| PUT | `/power?standbyTimeout=<minutes>` | Standby timeout |

### Playback

| Method | Path | Description |
|--------|------|-------------|
| GET | `/nowplaying` | Current playback state |
| GET | `/nowplaying?cmd=play` | Play |
| GET | `/nowplaying?cmd=pause` | Pause |
| GET | `/nowplaying?cmd=stop` | Stop |
| GET | `/nowplaying?cmd=resume` | Resume |
| GET | `/nowplaying?cmd=next` | Next track |
| GET | `/nowplaying?cmd=prev` | Previous track |
| GET | `/nowplaying?cmd=playpause` | Toggle play/pause |
| GET | `/nowplaying?cmd=seek&position=<ms>` | Seek (milliseconds) |
| PUT | `/nowplaying?repeat=<0-2>` | Repeat mode |
| PUT | `/nowplaying?shuffle=<bool>` | Shuffle |

### Volume & Levels

| Method | Path | Description |
|--------|------|-------------|
| GET | `/levels` | Main volume |
| GET | `/levels/room` | Room volume |
| GET | `/levels/group` | Group (multiroom) volume |
| GET | `/levels/bluetooth` | Bluetooth volume |
| PUT | `/<levels-ussi>?volume=<0-100>` | Set volume |
| PUT | `/<levels-ussi>?mute=<bool>` | Mute/unmute |
| PUT | `/<levels-ussi>?balance=<n>` | Balance |
| PUT | `/<levels-ussi>?mode=<1-3>` | Volume mode |

### Inputs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs` | All inputs |
| GET | `/<input-ussi>` | Input details |
| GET | `/<input-ussi>?cmd=select` | Activate input |
| GET | `/<input-ussi>?cmd=play` | Play from input |
| GET | `/<input-ussi>?cmd=resume` | Resume from input |
| PUT | `/<input-ussi>?alias=<name>` | Rename input |
| PUT | `/<input-ussi>?disabled=<bool>` | Hide/show input |
| PUT | `/<input-ussi>?trim=<dB>` | Input level trim |
| PUT | `/<input-ussi>?sensitivity=<n>` | Input sensitivity |
| PUT | `/<input-ussi>?unityGain=<bool>` | Unity gain |
| PUT | `/<input-ussi>?delay=<ms>` | Input delay |
| PUT | `/<input-ussi>?autoDim=<bool>` | Auto dim display |
| PUT | `/<input-ussi>?autoSwitching=<n>` | Auto-switch priority |
| PUT | `/<input-ussi>?eArcLatencyAuto=<bool>` | eARC auto latency |

### Outputs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/outputs` | All outputs |
| GET | `/<output-ussi>` | Output details |
| PUT | `/<output-ussi>?enabled=<bool>` | Enable/disable output |
| PUT | `/<output-ussi>?maxVolume=<n>` | Volume cap |
| PUT | `/<output-ussi>?connectionEnables=<n>` | Connector type bitmask |
| PUT | `/outputs?loudness=<n>` | Loudness level |
| PUT | `/outputs?loudnessEnabled=<bool>` | Loudness on/off |
| PUT | `/outputs?position=<0-2>` | Room placement |
| PUT | `/outputs/digital?dsdMode=<n>` | DSD mode |
| PUT | `/outputs/aux?mode=<n>` | Aux output mode |
| PUT | `/outputs/aux?crossoverSubwoofer=<Hz>` | Subwoofer crossover |
| PUT | `/outputs/aux?trimSubwoofer=<dB>` | Subwoofer trim |
| PUT | `/outputs/aux?trimRCAL=<dB>` | RCA left trim |
| PUT | `/outputs/aux?trimRCAR=<dB>` | RCA right trim |
| PUT | `/outputs/poweramp?master=<n>` | DIVA side selection |

### Bluetooth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs/bluetooth?cmd=pair` | Start pairing |
| GET | `/inputs/bluetooth?cmd=pair&action=stop` | Stop pairing |
| GET | `/inputs/bluetooth?cmd=pair&action=clear` | Clear history |
| GET | `/inputs/bluetooth?cmd=drop` | Disconnect device |
| GET | `/inputs/bluetooth?cmd=forget` | Forget all pairings |
| PUT | `/inputs/bluetooth?open=<bool>` | Automatic pairing |

### Streaming Services

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs/qobuz?cmd=login&username=<u>&password=<p>` | Qobuz login |
| GET | `/inputs/qobuz?cmd=logout` | Qobuz logout |
| PUT | `/inputs/qobuz?quality=<n>` | Qobuz quality |
| GET | `/inputs/tidal?cmd=oauthLogin&accessToken=<t>&refreshToken=<t>&oauthIdent=<i>` | Tidal login |
| GET | `/inputs/tidal?cmd=oauthLogout` | Tidal logout |
| PUT | `/inputs/spotify?spotifyBitrate=<bitrate>` | Spotify bitrate |
| PUT | `/inputs/spotify?gainNormalisation=<bool>` | Spotify gain norm |
| GET | `/inputs/spotify/presets` | Spotify presets |
| GET | `/inputs/spotify/presets?cmd=save&presetID=<n>` | Save preset |
| GET | `/<preset-ussi>?cmd=delete` | Delete preset |

### Radio (FM / DAB / iRadio)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs/radio` | Browse iRadio |
| GET | `/<ussi>?cmd=scanStations` | Scan for stations |
| GET | `/<ussi>?cmd=scanUp` | Scan up |
| GET | `/<ussi>?cmd=scanDown` | Scan down |
| GET | `/<ussi>?cmd=scanStop` | Stop scanning |
| GET | `/<ussi>?cmd=stepUp` | Step channel up |
| GET | `/<ussi>?cmd=stepDown` | Step channel down |
| GET | `/<ussi>?cmd=play&stationKey=<url>` | Play station |
| POST | `/inputs/radio/user?name=<n>&stationKey=<url>&...` | Add user station |
| PUT | `/<ussi>?name=<n>&stationKey=<url>&...` | Update user station |
| DELETE | `/<ussi>` | Delete user station |

### Play Queue

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs/playqueue` | Get queue |
| GET | `/<track-ussi>` | Track details |
| POST | `/inputs/playqueue?where=<ussi>&current=<n>&clear=<bool>&play=<bool>` (body: JSON track list) | Add tracks |
| POST | `/inputs/playqueue?clear=true` | Clear queue |
| GET | `/inputs/playqueue?cmd=move&what=<ussi>&where=<ussi>` | Move track |
| PUT | `/inputs/playqueue?current=<ussi>` | Set current track |

### Favourites & Presets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/favourites` | All favourites |
| GET | `/<fav-ussi>` | Favourite details |
| GET | `/<fav-ussi>?cmd=play` | Play favourite |
| POST | `/favourites` (body: JSON array) | Add favourites |
| DELETE | `/<fav-ussi>` | Remove favourite |
| GET | `/<fav-ussi>?cmd=assign&presetID=<n>` | Assign to preset |
| GET | `/<fav-ussi>?cmd=deassign` | Remove from preset |
| GET | `/favourites?cmd=move_preset&what=<n>&where=<n>` | Move preset |

### Multiroom

| Method | Path | Description |
|--------|------|-------------|
| GET | `/multiroom` | Multiroom state |
| PUT | `/<ussi>?cmd=addToGroup` | Join group |
| PUT | `/<ussi>?cmd=leaveGroup` | Leave group |

### CD Player

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inputs/cd` | CD state |
| GET | `/inputs/cd?cmd=eject` | Eject disc |
| PUT | `/inputs/cd?current=/inputs/cd/0` | Play first track |
| PUT | `/inputs/cd?action=<n>` | Insert action |

### Alarms & Sleep

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alarms` | List alarms |
| GET | `/<alarm-ussi>` | Alarm details |
| POST | `/alarms?name=<n>&source=<ussi>&hours=<h>&minutes=<m>&recurrenceDays=<bitmask>&enabled=<bool>` | Create alarm |
| PUT | `/<alarm-ussi>?enabled=<bool>` | Enable/disable |
| DELETE | `/<alarm-ussi>` | Delete alarm |
| GET | `/alarms?cmd=sleep&sleepPeriod=<minutes>` | Start sleep timer |
| GET | `/alarms?cmd=cancelSleep` | Cancel sleep timer |

### Network Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/network` | Network state |
| GET | `/<iface-ussi>` | Interface details |
| PUT | `/network?hostname=<name>` | Set hostname |
| PUT | `/network?sambaSMB1=<bool>` | Toggle SMB1 |
| GET | `/network/wireless?cmd=scan` | Scan WiFi |
| PUT | `/network/wireless?wifiSsid=<ssid>&wifiKey=<key>` | Connect WiFi |
| PUT | `/<iface>?dhcp=1` | Enable DHCP |
| PUT | `/<iface>/?dhcp=0&ipAddress=<ip>&netmask=<nm>&gateway=<gw>&dns1=<d1>&dns2=<d2>` | Static IP |

### Firmware Updates

| Method | Path | Description |
|--------|------|-------------|
| GET | `/update` | Update status |
| GET | `/update?cmd=get_versions` | Check for updates |
| GET | `/update?cmd=start_update` | Start update |
| PUT | `/update?source=<url>` | Set update source (requires `Authorization` header) |

### Local Library Browse

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tracks` | All tracks |
| GET | `/albums` | All albums |
| GET | `/albums/descriptor?flteqDescriptorIdent=<id>` | Albums by descriptor |
| GET | `/artists` | All artists |
| GET | `/artists/albumartists` | Album artists |
| GET | `/artists/classicalcomposers` | Classical composers |
| GET | `/descriptors` | Tags/genres/descriptors |
| GET | `/<ussi>` | Item details |
| GET | `/<ussi>?cmd=play` | Play item |
| GET | `/<ussi>?cmd=playNext` | Play next |
| GET | `/<ussi>?cmd=playLast` | Add to end of queue |
| GET | `/<ussi>?cmd=refresh` | Re-scan metadata |
| POST | `/<ussi>/meta` (body: JSON) | Edit metadata |
| POST | `/<ussi>/meta/user` (body: JSON) | Edit user metadata |
| GET | `/<ussi>/meta?cmd=clearEdits` | Clear user edits |
| POST | `/<ussi>` (body: binary image) | Upload artwork |

---

## 5. JSON Data Models

All field names use **camelCase**. Fields present in the model but not returned
by the device are typically `null` rather than absent.

### 5.1 NowPlaying

`GET /nowplaying`

```json
{
  "title":              "string | null",
  "albumName":          "string | null",
  "artistName":         "string | null",
  "description":        "string | null",
  "contentTag":         "string | null",

  "artwork":            "URL string | null",
  "artworkArtist":      "URL string | null",
  "artworkSource":      "URL string | null",
  "artworkTrack":       "URL string | null",
  "imageSize":          "string | null",

  "station":            "string | null",
  "stationId":          "string | null",
  "stationKey":         "URL string | null",
  "genre":              "string | null",
  "country":            "string | null",
  "live":               "boolean | null",

  "sourceApplication":  "string | null",
  "sourceDetail":       "string | null",
  "sourceMultiroom":    "string | null",

  "codec":              "string | null",
  "mimeType":           "string | null",
  "bitDepth":           "integer | null",
  "bitRate":            "integer (bps) | null",
  "sampleRate":         "integer (Hz) | null",
  "channels":           "integer | null",
  "channelLayoutInputString": "string | null",
  "frequency":          "integer | null",

  "duration":           "integer (ms) | null",
  "seekPosition":       "integer (ms) | null",
  "transportState":     "TransportState enum string",
  "transportPosition":  "integer (ms) | null",

  "repeat":             "0 | 1 | 2",
  "shuffle":            "boolean",
  "presetID":           "integer | null",

  "restrictPause":      "0 | 1 | 2",
  "restrictResume":     "0 | 1 | 2",
  "restrictStop":       "0 | 1 | 2",
  "restrictSeek":       "0 | 1 | 2",
  "restrictSkipNext":   "0 | 1 | 2",
  "restrictSkipPrev":   "0 | 1 | 2",
  "restrictPeekNext":   "0 | 1 | 2",
  "restrictPeekPrev":   "0 | 1 | 2",

  "airplayMode":        "AirplayMode enum string | null",
  "dolbyMode":          "DolbyMode enum string | null",
  "dolbySurround":      "boolean | null",

  "error":              "NowPlayingError enum string",
  "favouriteUssi":      "string | null",

  "socialFB":           "URL | null",
  "socialIG":           "URL | null",
  "socialX":            "URL | null",
  "socialUrl":          "URL | null",
  "streamDomain":       "string | null",
  "streamMessage":      "string | null",

  "tivoAlbum":          "string | null",
  "tivoArtist":         "string | null",
  "tivoTrack":          "string | null",
  "xperiState":         "string | null"
}
```

### 5.2 AudioLevels

`GET /levels` · `GET /levels/room` · `GET /levels/group` · `GET /levels/bluetooth`

```json
{
  "volume":          "integer 0–100",
  "mute":            "boolean",
  "mode":            "VolumeMode integer (1=Variable, 2=Fixed, 3=Hybrid)",
  "balance":         "integer | null   (negative=left, 0=centre, positive=right)",
  "headphoneDetect": "boolean | null"
}
```

### 5.3 Power

`GET /power`

```json
{
  "system":          "PowerState string  (On | Lona | Eup | Off)",
  "powerMode":       "PowerMode integer  (1=Low, 2=Audiophile)",
  "serverMode":      "boolean",
  "standbyTimeout":  "integer (minutes, 0 = never)"
}
```

### 5.4 Inputs / Input

`GET /inputs`

```json
{
  "items": [
    { "ussi": "inputs/spotify",  "name": "Spotify",  "type": "...", "enabled": true },
    { "ussi": "inputs/tidal",    "name": "Tidal",     "type": "...", "enabled": true },
    ...
  ]
}
```

`GET /<input-ussi>`

```json
{
  "ussi":            "inputs/spotify",
  "name":            "Spotify",
  "alias":           "string | null",
  "type":            "string",
  "disabled":        "boolean",
  "trim":            "integer (dB) | null",
  "sensitivity":     "integer | null",
  "delay":           "integer (ms) | null",
  "unityGain":       "boolean | null",
  "autoDim":         "boolean | null",
  "autoSwitching":   "integer | null",
  "eArcLatencyAuto": "boolean | null",

  "radioState":      "RadioState enum string | null",
  "cdState":         "CDState enum string | null",

  "spotifyBitrate":       "string | null",
  "gainNormalisation":    "boolean | null",

  "quality":         "integer | null",

  "open":            "boolean | null",

  "artworkChoice":   "integer | null",
  "bitrate":         "string | null"
}
```

### 5.5 Outputs / Output

`GET /outputs`

```json
{
  "loudness":        "integer | null",
  "loudnessEnabled": "boolean | null",
  "position":        "integer (0=freestanding, 1=wall, 2=corner) | null",
  "items": [
    { "ussi": "outputs/analogue", "name": "Analogue", ... },
    ...
  ]
}
```

`GET /<output-ussi>` — output type-specific fields

**Analogue output (`outputs/analogue`):**
```json
{
  "ussi":             "outputs/analogue",
  "enabled":          "boolean",
  "maxVolume":        "integer | null",
  "connectionEnables": "integer (bitmask, see OutputConnections enum)"
}
```

**Digital output (`outputs/digital`):**
```json
{
  "ussi":    "outputs/digital",
  "enabled": "boolean",
  "dsdMode": "DsdMode integer (0=Native, 1=PCM)"
}
```

**Headphone output (`outputs/headphone`):**
```json
{
  "ussi":    "outputs/headphone",
  "enabled": "boolean",
  "maxVolume": "integer | null"
}
```

**Aux output (`outputs/aux`):**
```json
{
  "ussi":              "outputs/aux",
  "enabled":           "boolean",
  "mode":              "AuxMode integer (0=Off, 1=Subwoofer, 2=ExtStereo, 3=LRChannelOutput)",
  "crossoverSubwoofer": "integer (Hz) | null",
  "trimSubwoofer":     "float (dB) | null",
  "trimRCAL":          "float (dB) | null",
  "trimRCAR":          "float (dB) | null"
}
```

**Pre-amp output (`outputs/preamp`):**
```json
{
  "ussi":             "outputs/preamp",
  "enabled":          "boolean",
  "connectionEnables": "integer (bitmask)"
}
```

**Power amp output (`outputs/poweramp`):**
```json
{
  "ussi":    "outputs/poweramp",
  "enabled": "boolean",
  "master":  "integer | null"
}
```

### 5.6 Multiroom

`GET /multiroom`

```json
{
  "state":                "MultiroomState string (NotConnected | Master | Client)",
  "statusCode":           "MultiroomStatus integer (0=Pending, 1=Success, 2=Error)",
  "udn":                  "string — this device UDN",
  "masterUdn":            "string | null — master device UDN when in Client state",
  "supportedClientCount": "integer — max simultaneous clients",
  "children": [
    {
      "udn":  "string",
      "name": "string",
      "type": "string (Unknown | Streamer | Uniti)"
    }
  ]
}
```

### 5.7 LeoSystem

`GET /system`

```json
{
  "apiregular":     "integer — API version for regular commands",
  "apistreaming":   "integer — API version for streaming commands",
  "appVer":         "string — application version",
  "build":          "string — build identifier",

  "hardwareSerial":   "string",
  "hardwareRevision": "string",
  "hardwareType":     "string",
  "hostname":         "string",
  "ipAddress":        "string",
  "kernel":           "string",
  "machine":          "string (e.g. armv7l)",
  "model":            "string",
  "system":           "string",
  "udid":             "string — unique device ID",
  "variant":          "integer",

  "firstTimeSetupComplete": "boolean",
  "chromecastKeyInstalled": "boolean | null",
  "demoMode":               "boolean | null",
  "coreDumps":              "boolean | null",

  "ubootVer":       "string",
  "rootfsVer":      "string",
  "hostAppVer":     "string | null",
  "hostBSL1Ver":    "string | null",
  "hostBSL2Ver":    "string | null",
  "hostDSPAppVer":  "string | null",
  "hostDSPBSLVer":  "string | null",
  "hostProxAppVer": "string | null",
  "hostProxBSLVer": "string | null",
  "hostUWBAppVer":  "string | null",
  "hostZigAppVer":  "string | null",

  "hostCpuTemp":    "integer (°C) | null",
  "hostSysTemp":    "integer (°C) | null",

  "nsdkVer":        "string | null",
  "nsdkSourceV1":   "string | null",
  "nsdkSourceV2":   "string | null",
  "nsdkSourceV3":   "string | null",

  "spotifyVersion": "string | null",
  "displayType":    "string | null",
  "dabFmVer":       "string | null",

  "soak":           "Soak integer",
  "resetState":     "integer"
}
```

### 5.8 Update

`GET /update`

```json
{
  "state":           "UpdateState string (Idle|GetVersion|Ready|Updating|Aborting|HostUpdate)",
  "statusCode":      "UpdateStatus integer (0=Pending, 1=Success, 2=Failed, 3=Cancelled)",
  "percent":         "integer 0–100 — overall progress",

  "updateAvailable": "boolean",
  "updateInstalled": "boolean",
  "updateAutomatic": "boolean",
  "downgradeEnable": "boolean",
  "updateSource":    "string | null",
  "updateInterval":  "integer (hours) | null",
  "updateType":      "string | null",
  "updateDate":      "string | null",
  "updateDesc":      "string | null",

  "appCur":    "string — current app version",
  "appTgt":    "string | null — target version",
  "appNew":    "string | null — newly available version",

  "rootfsCur": "string",
  "rootfsTgt": "string | null",
  "rootfsNew": "string | null",

  "ubootCur":  "string",
  "ubootTgt":  "string | null",
  "ubootNew":  "string | null"
}
```

### 5.9 Alarms

`GET /alarms`

```json
{
  "items": [
    {
      "ussi":           "alarms/1",
      "name":           "string",
      "source":         "USSI of source (e.g. inputs/radio)",
      "hours":          "integer 0–23",
      "minutes":        "integer 0–59",
      "recurrenceDays": "integer bitmask (1=Mon, 2=Tue, 4=Wed, 8=Thu, 16=Fri, 32=Sat, 64=Sun)",
      "enabled":        "boolean"
    }
  ]
}
```

Sleep timer:
```
GET /alarms?cmd=sleep&sleepPeriod=<minutes>
GET /alarms?cmd=cancelSleep
```

### 5.10 Favourites

`GET /favourites`

```json
{
  "items": [
    {
      "ussi":      "favourites/1",
      "name":      "string",
      "type":      "string",
      "presetID":  "integer | null",
      "available": "boolean"
    }
  ]
}
```

---

## 6. Enumeration Reference

All enum values observed in the decompiled Moshi adapters and model classes.

### TransportState

Reported in `NowPlaying.transportState` as a string.

| Value | Meaning |
|-------|---------|
| `"Unknown"` | State cannot be determined |
| `"Stopped"` | Playback stopped |
| `"Playing"` | Active playback |
| `"Paused"` | Paused |
| `"Connecting"` | Connecting to stream source |
| `"Stalled"` | Buffering / stream stalled |
| `"Interrupted"` | Externally interrupted |
| `"Errored"` | Playback error |
| `"Transitioning"` | Changing track/source |

### NowPlayingRepeat

Reported and set as an **integer** in `NowPlaying.repeat`.

| Value | Meaning |
|-------|---------|
| `0` | No repeat |
| `1` | Repeat current track |
| `2` | Repeat all tracks |

### NowPlayingRestrictions

Fields: `restrictPause`, `restrictResume`, `restrictStop`, `restrictSeek`,
`restrictSkipNext`, `restrictSkipPrev`, `restrictPeekNext`, `restrictPeekPrev`.

| Value | Meaning |
|-------|---------|
| `0` | Action allowed |
| `1` | Action not allowed (control should be greyed out) |
| `2` | Unknown |

### NowPlayingError

Reported in `NowPlaying.error`.

| Value | Meaning |
|-------|---------|
| `"NoError"` | Playback OK |
| `"UnsupportedFileFormat"` | File format not supported |
| `"ResourceNotAvailable"` | Stream/file unavailable |
| `"NetworkNotReachable"` | Network connectivity error |
| `"NetworkTooSlow"` | Insufficient bandwidth |
| `"UnspecifiedError"` | Generic error |

### AirplayMode

Reported in `NowPlaying.airplayMode`.

| Value | Meaning |
|-------|---------|
| `"Off"` | AirPlay not active |
| `"Stereo"` | Standard stereo AirPlay |
| `"Surround"` | Surround sound AirPlay |
| `"Spatial"` | Spatial audio |
| `"DolbyAudio"` | Dolby Audio via AirPlay |
| `"DolbyAtmos"` | Dolby Atmos via AirPlay |

### DolbyMode

Reported in `NowPlaying.dolbyMode`.

| Value | Meaning |
|-------|---------|
| `"Off"` | No Dolby processing |
| `"DolbyAudio"` | Dolby Audio active |
| `"DolbyAtmos"` | Dolby Atmos active |

### PowerState

Used in `PUT /power?system=<value>`.

| Value | Meaning |
|-------|---------|
| `"on"` | Full power on |
| `"lona"` | LONA (network standby — keeps LAN active) |
| `"eup"` | EUP eco standby |
| `"off"` | Fully off |

### PowerMode

Reported in `Power.powerMode` as an integer.

| Value | Meaning |
|-------|---------|
| `1` | Low power mode |
| `2` | Audiophile mode (high quality, more power) |

### VolumeMode

Used in `PUT /<levels-ussi>?mode=<value>`.

| Value | Meaning |
|-------|---------|
| `1` | Variable — software volume control |
| `2` | Fixed — hardware fixed output level |
| `3` | Hybrid — software pre-attenuate + hardware |

### MultiroomState

Reported in `Multiroom.state`.

| Value | Meaning |
|-------|---------|
| `"NotConnected"` | Standalone, not in a group |
| `"Master"` | This device is the group master (sending audio) |
| `"Client"` | This device is a group client (receiving audio) |

### MultiroomStatus

Reported in `Multiroom.statusCode` as an integer.

| Value | Meaning |
|-------|---------|
| `0` | Pending |
| `1` | Success |
| `2` | Error |

### UpdateState

Reported in `Update.state`.

| Value | Meaning |
|-------|---------|
| `"Idle"` | No update activity |
| `"GetVersion"` | Checking for updates |
| `"Ready"` | Update downloaded, ready to install |
| `"Updating"` | Installation in progress |
| `"Aborting"` | Update cancelled |
| `"HostUpdate"` | Updating a connected host device (e.g. power amp) |

### UpdateStatus

Reported in `Update.statusCode` as an integer.

| Value | Meaning |
|-------|---------|
| `0` | Pending |
| `1` | Success |
| `2` | Failed |
| `3` | Cancelled |

### RadioState

Reported in `Input.radioState`.

| Value | Meaning |
|-------|---------|
| `"Idle"` | Radio not active |
| `"Playing"` | Radio playing |
| `"Scanning"` | Scanning for stations |
| `"Initialising"` | Radio hardware starting up |

### CDState

Reported in `Input.cdState`.

| Value | Meaning |
|-------|---------|
| `"Error"` | CD error |
| `"Unknown"` | State unknown |
| `"Empty"` | No disc |
| `"Loading"` | Disc loading |
| `"Loaded"` | Disc ready |

### AuxMode

Used in `PUT /outputs/aux?mode=<value>`.

| Value | Meaning |
|-------|---------|
| `0` | Off |
| `1` | Subwoofer output |
| `2` | Extended stereo |
| `3` | Left/Right channel output |

### DsdMode

Used in `PUT /outputs/digital?dsdMode=<value>`.

| Value | Meaning |
|-------|---------|
| `0` | Native DSD |
| `1` | Convert DSD to PCM |

### OutputConnections (bitmask)

Used in `connectionEnables` field. Combine with bitwise OR.

| Bit | Connector |
|-----|-----------|
| `0x01` | BANANA |
| `0x02` | RCA |
| `0x04` | DIN |
| `0x08` | DIN + RCA |
| `0x10` | BNC |
| `0x20` | TOSLINK optical |
| `0x40` | JACK (3.5mm) |
| `0x80` | XLR balanced |
| `0x100` | XLR + RCA |

### Soak (internal diagnostic)

Reported in `LeoSystem.soak`.

| Value | Meaning |
|-------|---------|
| `0` | None |
| `1` | Preamble |
| `2` | Headphone soak |
| `3` | Input relays |
| `4` | Output relays |
| `5` | Power supply |

### UsageMode (device role)

| Value | Meaning |
|-------|---------|
| `"STREAMER"` | Standard streaming device |
| `"DANTE"` | Dante audio network device |
| `"ANALOGUE_REPEATER"` | Analogue signal repeater role |
| `"UNKNOWN"` | Unrecognised role |

---

## 7. Command Pattern

### Action Commands (GET `?cmd=`)

Many state-changing operations are encoded as GET requests with a `cmd` query
parameter. This is unconventional REST but consistent throughout the API:

```
GET /nowplaying?cmd=play
GET /inputs/bluetooth?cmd=pair
GET /system?cmd=reboot
```

Additional parameters for a command follow as extra query arguments:

```
GET /nowplaying?cmd=seek&position=90000
GET /alarms?cmd=sleep&sleepPeriod=30
GET /inputs/spotify/presets?cmd=save&presetID=1
```

### Setting Values (PUT `?key=value`)

Settings are mutated with PUT and the new value as a query parameter:

```
PUT /nowplaying?repeat=2
PUT /levels?volume=40
PUT /power?standbyTimeout=20
```

Multiple settings can be combined in a single PUT (device must support it):

```
PUT /network/wireless?wifiSsid=MyNet&wifiKey=pass123
```

---

## 8. USSI Path System

**USSI** (Universal Stream Source Identifier) is the path-based addressing
system used throughout the API. Every addressable object — input, output,
track, favourite, alarm — has a USSI that is its URL path relative to the
device root.

### Structural Conventions

```
inputs/<type>           — top-level input
inputs/<type>/<id>      — child of an input (e.g. radio station, track)
inputs/playqueue/<n>    — item in the play queue
outputs/<type>          — output endpoint
levels                  — main volume
levels/room             — room volume
levels/group            — group volume
favourites/<n>          — favourite slot
alarms/<n>              — alarm
multiroom               — multiroom endpoint
tracks/<id>             — local library track
albums/<id>             — local library album
artists/<id>            — local library artist
```

### Path Encoding

USSI values used in `@Path` annotations are marked `encoded = true`, meaning
they are transmitted as-is without additional URL encoding. When constructing
URLs manually, paths that are already path-encoded (e.g. containing `%2F`)
should not be double-encoded.

### USSI as a Resource Reference

When one endpoint needs to reference another (e.g. the current track in the
play queue, or the source for an alarm), the USSI of the target resource is
passed as a parameter value:

```
PUT /inputs/playqueue?current=inputs%2Fplayqueue%2F3
GET /favourites/<ussi>?cmd=assign&presetID=2
POST /alarms?name=Morning&source=inputs%2Fradio
```

---

## 9. Real-time Updates (SSE)

The device supports **Server-Sent Events (SSE)** for push notifications,
avoiding the need to poll. The app uses SSE for:

- `nowplaying` — transport state and track changes
- `levels` — volume changes
- `inputs` — source switching
- `power` — standby state

The SSE stream is opened as a long-lived HTTP connection:

```
GET /nowplaying
Accept: text/event-stream
```

Events are sent as standard SSE format:

```
data: {"title":"New Track","transportState":"Playing",...}

data: {"transportState":"Paused",...}
```

The app implements `SSEUpdatable` interfaces on model objects, and the `copyWithSSE`
methods apply partial updates from the SSE delta to the last-known full model
state. This means SSE events may contain only the changed fields, not the full
object.

---

## 10. Error Handling

The app handles the following network exceptions from OkHttp:

| Exception | Meaning |
|-----------|---------|
| `IOException` | Generic I/O failure |
| `EOFException` | Connection closed unexpectedly |
| `SocketException` | Socket-level error |
| `SocketTimeoutException` | Request timed out |
| `ConnectException` | Could not connect (device offline / wrong IP) |

HTTP-level errors return a non-2xx status code with a JSON error body.
The app maps these to a `Failure` sealed class with subtypes:
`NetworkFailure`, `ServerFailure`, `NoInternetFailure`, `NotFoundFailure`.

---

## 11. HTTP Client Behaviour

The app uses **OkHttp3** as the underlying HTTP engine, configured via a
Koin dependency injection module (`NetworkModule`):

- **Timeout:** Default OkHttp3 values (connect 10s, read 10s, write 10s)
- **Connection pooling:** Enabled (OkHttp default)
- **Redirects:** Followed automatically
- **Interceptors:** None visible beyond standard Retrofit/OkHttp
- **Converter:** Moshi with custom adapters for every enum type
- **Coroutines:** All calls are `suspend` functions using `retrofit2-kotlin-coroutines-adapter`

Retrofit base URL is constructed dynamically per device:

```kotlin
Retrofit.Builder()
    .baseUrl("http://${device.ipAddress}:${device.port}/")
    .addConverterFactory(MoshiConverterFactory.create(moshi))
    .client(okHttpClient)
    .build()
```

A separate Retrofit instance is created per device, so multi-device setups
(multiroom) are handled by maintaining a pool of `LeoInstance` objects.

---

## 12. Known Limitations & Notes

- **No HTTPS:** All traffic is plain HTTP. Do not expose port 15081 beyond your
  local network.

- **No authentication:** Any device on the same network segment can send
  commands. Some destructive commands (factory reset, update source override)
  require an `Authorization: <token>` header, but the token provisioning
  mechanism is not visible in the app — it is likely set during manufacturing
  or first-time setup.

- **seek position unit:** The `position` parameter for the seek command is
  in **milliseconds** (consistent with `duration` and `seekPosition` in the
  NowPlaying response). Some older device firmware may interpret it as seconds —
  test with a short value first.

- **Model availability:** Not every field in a model is populated by every
  device. A Naim Mu-so will not return `cdState`; a Naim Uniti Star (with
  CD drive) will. Fields not applicable to the device are `null`.

- **Input USSI discovery:** The authoritative list of available inputs is
  `GET /inputs`. The USSI values are not always exactly as listed in this
  document — they may include a sub-path or numeric suffix on some firmware
  versions. Always use the USSI returned by `/inputs` rather than hardcoded
  paths.

- **API versioning:** The `apiregular` and `apistreaming` fields in
  `/system` indicate the API revision. The app currently targets API
  version `1`. Unknown fields should be ignored to allow forward compatibility.

- **Rate limiting:** No rate limiting is enforced. However, sending
  high-frequency polling requests (faster than ~1 Hz) is unnecessary and
  may impact device performance. Use SSE for real-time state updates.

- **Partial PUT responses:** PUT requests that succeed typically return
  HTTP `200` with an empty body. The updated state must be read back with
  a subsequent GET if confirmation is needed.

---

# Part B — UPnP/DLNA (legacy devices)

## 13. UPnP/DLNA Protocol Overview

Legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, etc.) do not expose
a REST API on port 15081. Instead, they implement standard **UPnP/DLNA**
services controlled via **SOAP over HTTP**.

| Property | Value |
|----------|-------|
| Protocol | HTTP/1.1 with SOAP XML |
| Port | **8080** (typical, discovered via SSDP) |
| Content-Type | `text/xml; charset="utf-8"` |
| Authentication | None |
| Discovery | SSDP multicast on 239.255.255.250:1900 |

The device description is available at `http://<device-ip>:<port>/description.xml`
and lists all supported UPnP services with their control URLs.

---

## 14. UPnP Services & Control URLs

Legacy Naim devices expose two primary UPnP services:

| Service | Service Type URN | Default Control URL |
|---------|------------------|---------------------|
| **AVTransport** | `urn:schemas-upnp-org:service:AVTransport:1` | `/AVTransport/ctrl` |
| **RenderingControl** | `urn:schemas-upnp-org:service:RenderingControl:1` | `/RenderingControl/ctrl` |

Control URLs should be discovered from `description.xml` rather than hardcoded,
as they may vary between device models. The tool falls back to the defaults above
if discovery fails.

### SOAP Request Format

All control commands are sent as HTTP POST with a SOAP XML envelope:

```http
POST /AVTransport/ctrl HTTP/1.1
Host: 192.168.1.21:8080
Content-Type: text/xml; charset="utf-8"
SOAPAction: "urn:schemas-upnp-org:service:AVTransport:1#Play"

<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
<InstanceID>0</InstanceID>
<Speed>1</Speed>
</u:Play>
</s:Body>
</s:Envelope>
```

### SOAP Response Format

Responses are SOAP XML envelopes containing the action response element:

```xml
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:PlayResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
</u:PlayResponse>
</s:Body>
</s:Envelope>
```

### SOAP Fault Format

Errors return a SOAP fault with a UPnP error code and description:

```xml
<s:Envelope ...>
<s:Body>
<s:Fault>
<detail>
<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">
<errorCode>501</errorCode>
<errorDescription>Action Failed</errorDescription>
</UPnPError>
</detail>
</s:Fault>
</s:Body>
</s:Envelope>
```

---

## 15. SOAP Action Reference

### AVTransport Actions

All AVTransport actions use `InstanceID=0`.

| Action | Arguments | Description |
|--------|-----------|-------------|
| `Play` | `Speed=1` | Start/resume playback |
| `Pause` | — | Pause playback |
| `Stop` | — | Stop playback |
| `Next` | — | Skip to next track |
| `Previous` | — | Skip to previous track |
| `Seek` | `Unit=REL_TIME`, `Target=HH:MM:SS` | Seek to position |
| `GetTransportInfo` | — | Returns: `CurrentTransportState`, `CurrentTransportStatus`, `CurrentSpeed` |
| `GetPositionInfo` | — | Returns: `Track`, `TrackDuration`, `TrackMetaData`, `TrackURI`, `RelTime`, `AbsTime`, `RelCount`, `AbsCount` |
| `GetMediaInfo` | — | Returns: `NrTracks`, `MediaDuration`, `CurrentURI`, `CurrentURIMetaData`, `NextURI`, `NextURIMetaData`, `PlayMedium`, `RecordMedium`, `WriteStatus` |

### RenderingControl Actions

All RenderingControl actions use `InstanceID=0` and `Channel=Master`.

| Action | Arguments | Description |
|--------|-----------|-------------|
| `GetVolume` | — | Returns: `CurrentVolume` |
| `SetVolume` | `DesiredVolume=<0-100>` | Set volume level |
| `GetMute` | — | Returns: `CurrentMute` (0 or 1) |
| `SetMute` | `DesiredMute=<0\|1>` | Set mute state |
| `GetLoudness` | — | Returns: `CurrentLoudness` (0 or 1) — may not be supported |

---

## 16. Input Discovery via ContentDirectory

Legacy Naim devices expose available inputs through the **ContentDirectory** service,
which is part of the standard UPnP AV architecture. Inputs are organized as a
hierarchical container structure that can be browsed.

### ContentDirectory Service

| Property | Value |
|----------|-------|
| Service Type | `urn:schemas-upnp-org:service:ContentDirectory:1` |
| Default Control URL | `/ContentDirectory/ctrl` |

### Browse Action

The `Browse` action is used to navigate the content hierarchy:

| Argument | Value | Description |
|----------|-------|-------------|
| `ObjectID` | `"0"` | Root container; `"0/0"` for inputs on some devices |
| `BrowseFlag` | `"BrowseDirectChildren"` | List children; `"BrowseMetadata"` for item details |
| `Filter` | `"*"` | Return all metadata fields |
| `StartingIndex` | `"0"` | Pagination start |
| `RequestedCount` | `"100"` | Max items to return |
| `SortCriteria` | `""` | Sort order (usually empty) |

### DIDL-Lite Response Format

Browse results are returned as DIDL-Lite XML in the `Result` field:

```xml
<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
  <container id="0/0" parentID="0" restricted="1">
    <dc:title>Inputs</dc:title>
    <upnp:class>object.container</upnp:class>
  </container>
  <item id="0/1" parentID="0" restricted="1">
    <dc:title>Digital 1</dc:title>
    <upnp:class>object.item.audioItem</upnp:class>
    <res protocolInfo="http-get:*:audio/x-naim-digital:*">x-naim-input:digital1</res>
  </item>
</DIDL-Lite>
```

### Common Naim Input ObjectIDs

The exact ObjectID structure varies by device model. Common patterns:

| ObjectID | Description |
|----------|-------------|
| `0` | Root container |
| `0/0` | Inputs container (device-specific) |
| `0/1` | Media servers / UPnP sources |
| `0/2` | Internet radio |
| `0/n` | Various input types |

### Input Selection via SetAVTransportURI

Once an input's resource URI is obtained from ContentDirectory browsing, it can
be selected using the AVTransport `SetAVTransportURI` action:

```
Action: SetAVTransportURI
Arguments:
  InstanceID: 0
  CurrentURI: <URI from res element>
  CurrentURIMetaData: <DIDL-Lite XML>
```

Naim-specific input URIs may use schemes like:
- `x-naim-input:digital1`
- `x-naim-input:analog1`
- `x-rincon:*` (for certain streaming inputs)

After setting the URI, call `Play` to start playback from the selected input.

---

## 17. ConnectionManager Service

The ConnectionManager service provides information about supported media types
and transport protocols.

| Property | Value |
|----------|-------|
| Service Type | `urn:schemas-upnp-org:service:ConnectionManager:1` |
| Default Control URL | `/ConnectionManager/ctrl` |

### GetProtocolInfo Action

Returns supported protocols for sending (Source) and receiving (Sink):

| Output | Description |
|--------|-------------|
| `Source` | Comma-separated list of protocols the device can send |
| `Sink` | Comma-separated list of protocols the device can receive |

Protocol format: `<protocol>:<network>:<contentType>:<additionalInfo>`

Example protocols for Naim devices:
- `http-get:*:audio/flac:*`
- `http-get:*:audio/mpeg:*`
- `http-get:*:audio/x-wav:*`
- `http-get:*:audio/x-aiff:*`

---

## 18. Input Settings Limitations on Legacy Devices

Unlike newer Naim devices with the REST API, legacy UPnP devices have limited
support for input-specific settings:

### Available on Legacy (UPnP) Devices
- Input discovery (via ContentDirectory browsing)
- Input selection (via SetAVTransportURI)
- Master volume control
- Mute control
- Loudness (on some models)

### NOT Available on Legacy (UPnP) Devices
The following settings require the REST API (newer devices only):
- Input trim (per-input volume adjustment)
- Input alias/rename
- Input enable/disable
- Input sensitivity
- Unity gain mode

If you need these features on a legacy device, check if your device has a REST API
on port 15081 — some devices may support both protocols.

---

## 19. Service Discovery via SCPD

Each UPnP service publishes an SCPD (Service Control Protocol Description) XML
document that lists all available actions and state variables. This can be used
to discover device-specific capabilities.

The SCPD URL is found in `description.xml` under each service's `<SCPDURL>` element.

Example SCPD content:

```xml
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <actionList>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument>
          <name>ObjectID</name>
          <direction>in</direction>
        </argument>
        <!-- ... more arguments ... -->
      </argumentList>
    </action>
  </actionList>
</scpd>
```

Use the `services -v` command to view all available actions on a device.

---

## 20. X_HtmlPageHandler Service (DM Holdings)

Some legacy Naim devices include a proprietary service from DM Holdings
for web interface control.

| Property | Value |
|----------|-------|
| Service Type | `urn:schemas-dm-holdings-com:service:X_HtmlPageHandler:1` |
| Default Control URL | `/HtmlPageHandler/ctrl` |

### Actions

| Action | Arguments | Description |
|--------|-----------|-------------|
| `actHtmlDeviceName` | `argHtmlDeviceName` (in) | Set device friendly name |
| `actHtmlRefresh` | `argHtmlRefresh` (in) | Refresh/reload |
| `actHtmlTcpip` | Multiple network args (in) | Configure network settings |
| `actHtmlIrControl` | `argIrControl` (in) | Send IR command |

### IR Control (EXPERIMENTAL)

The `actHtmlIrControl` action is intended for sending infrared remote control
commands to the device. However, the exact format of the `argIrControl` parameter
is **not yet documented**.

Testing on SuperUniti firmware 2.0.11.14171 returns UPnP error 401 (Invalid Action)
for various attempted formats:
- Decimal codes (e.g., "51", "53")
- Hex codes (e.g., "0x33")
- String names (e.g., "input_digital2")

**Research needed:** The command format may be:
- Raw NEC/RC5/RC6 protocol codes
- Proprietary Naim encoding
- A specific string format

If you discover the correct format, please document it!

---

## 21. SuperUniti Device Profile

The Naim SuperUniti (and similar legacy devices) has been tested with the following
characteristics:

### Device Information
- **Model:** SuperUniti
- **Device Type:** `urn:schemas-upnp-org:device:MediaRenderer:1`
- **Manufacturer:** Naim Audio Ltd.
- **Firmware:** 2.0.11.14171 (tested)
- **UPnP Port:** 8080
- **Web Interface:** Port 80 (`/index.asp`)

### Available Services
1. `RenderingControl:1` - Volume, mute, loudness
2. `ConnectionManager:1` - Protocol info
3. `AVTransport:1` - Playback control (15 actions)
4. `X_HtmlPageHandler:1` - Web/IR control (limited)

### NOT Available
- ContentDirectory service (no input browsing via UPnP)
- REST API on port 15081
- Input trim/alias/enable via UPnP

### Supported Audio Formats (via ConnectionManager)
The device reports support for:
- PCM: L16, L24 at 44.1/48/88.2/96 kHz
- Compressed: MP3, WMA, OGG, M4A, AAC
- Lossless: FLAC, WAV, AIFF
- High-res: DSD, DSF, DFF

### Input Control via n-Stream Protocol

**DISCOVERY:** Input switching on legacy Naim devices uses the **n-Stream/BridgeCo protocol**
on **TCP port 15555**, NOT UPnP or IR commands.

#### Protocol Details

| Property | Value |
|----------|-------|
| Port | TCP 15555 |
| Protocol | XML-based (BridgeCo) with two layers |
| Command Format | Base64-encoded NVM commands wrapped in XML |

#### Protocol Architecture

The n-Stream protocol has **two layers**:

1. **BC (BridgeCo) Layer** - Handles connection setup and API version negotiation
2. **Tunnel Layer** - Carries NVM commands wrapped in Base64

#### Required Initialization Sequence

Before NVM commands (like input switching) will work, the connection MUST be initialized:

**Step 1: Send RequestAPIVersion (BC layer)**
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

**Step 2: Enable unsolicited messages (via Tunnel)**
```xml
<command>
  <name>TunnelToHost</name>
  <id>2</id>
  <map>
    <item>
      <name>data</name>
      <base64>[Base64 of "*NVM SETUNSOLICITED ON\r"]</base64>
    </item>
  </map>
</command>
```

**Step 3: Now NVM commands will work**

#### Tunnel Command Structure

Commands like `*NVM SETINPUT DIGITAL2\r` are Base64-encoded and wrapped in XML:

```xml
<command>
  <name>TunnelToHost</name>
  <id>3</id>
  <map>
    <item>
      <name>data</name>
      <base64>[Base64 encoded "*NVM SETINPUT DIGITAL2\r"]</base64>
    </item>
  </map>
</command>
```

#### Input Control Commands

| Command | Description |
|---------|-------------|
| `*NVM SETINPUT <input>\r` | Switch to specified input |
| `*NVM GETINPUT\r` | Get current input |
| `*NVM INPUT+\r` | Cycle to next input |
| `*NVM INPUT-\r` | Cycle to previous input |
| `*NVM GETINPUTBLK\r` | Get all inputs (bulk query, returns multiple responses) |
| `*NVM SETINPUTENABLED <input> ON\|OFF\r` | Enable/disable input |
| `*NVM GETINPUTENABLED <input>\r` | Check if input is enabled |
| `*NVM SETINPUTNAME <input> "<name>"\r` | Set input display name/alias |
| `*NVM GETINPUTNAME <input>\r` | Get input display name |

#### Valid Input Names

| Category | Inputs |
|----------|--------|
| Streaming | `UPNP`, `IRADIO`, `SPOTIFY`, `TIDAL`, `AIRPLAY`, `BLUETOOTH` |
| Digital | `DIGITAL1` through `DIGITAL10` |
| Analog | `ANALOGUE1` through `ANALOGUE5`, `PHONO` |
| Other | `USB`, `CD`, `FM`, `DAB`, `FRONT`, `MULTIROOM`, `IPOD` |
| HDMI | `HDMI1` through `HDMI5` |

#### Usage with naim_control_nstream.py

```bash
# List all inputs available on the device
./naim_control_nstream.py --host 192.168.1.21 inputs

# Switch to Digital Input 2
./naim_control_nstream.py --host 192.168.1.21 set-input --input DIGITAL2

# Switch to UPnP streaming
./naim_control_nstream.py --host 192.168.1.21 set-input --input UPNP

# Get current input
./naim_control_nstream.py --host 192.168.1.21 get-input

# Cycle through inputs
./naim_control_nstream.py --host 192.168.1.21 input-up
./naim_control_nstream.py --host 192.168.1.21 input-down

# Enable/disable inputs
./naim_control_nstream.py --host 192.168.1.21 input-enable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-disable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-enabled --input DIGITAL1

# Rename inputs (set custom display name)
./naim_control_nstream.py --host 192.168.1.21 input-rename --input DIGITAL1 --name "TV Audio"
./naim_control_nstream.py --host 192.168.1.21 input-name --input DIGITAL1

# Device info
./naim_control_nstream.py --host 192.168.1.21 product
./naim_control_nstream.py --host 192.168.1.21 version
./naim_control_nstream.py --host 192.168.1.21 mac
./naim_control_nstream.py --host 192.168.1.21 preamp
```

#### Note on X_HtmlPageHandler IR Control

The X_HtmlPageHandler IR control action (`actHtmlIrControl`) via UPnP consistently returns
error 401. This is likely because input switching was always intended to use the n-Stream
protocol on port 15555, not UPnP. The IR control action may be reserved for other purposes
or disabled.

---

## 22. n-Stream Protocol Architecture

The n-Stream protocol is a proprietary TCP-based protocol built on the BridgeCo platform.
It provides access to device-specific features not available via UPnP.

### Protocol Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Application Layer                               │
│   NVM Commands: *NVM SETINPUT, *NVM GETINPUT, *NVM VOL+, etc.       │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Tunnel Layer                                  │
│   XML Command: <command><name>TunnelToHost</name>...</command>      │
│   Payload: Base64-encoded NVM command in <base64> element           │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    BC (BridgeCo) Layer                               │
│   Connection setup: RequestAPIVersion, Disconnect                    │
│   API negotiation: module="naim", version="1"                        │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        TCP Socket                                    │
│   Port: 15555, Encoding: UTF-8, No framing (XML documents)          │
└─────────────────────────────────────────────────────────────────────┘
```

### BC (BridgeCo) Layer Commands

These commands operate at the connection level, before tunnel commands work.

#### RequestAPIVersion

Sent immediately after connecting to negotiate API version:

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

#### Disconnect

Sent when closing the connection cleanly:

```xml
<command id="0" name="Disconnect"/>
```

### Tunnel Layer Commands

NVM commands are wrapped in TunnelToHost messages with Base64 encoding:

```xml
<command>
  <name>TunnelToHost</name>
  <id>2</id>
  <map>
    <item>
      <name>data</name>
      <base64>[Base64-encoded NVM command]</base64>
    </item>
  </map>
</command>
```

### Responses

The device responds with `TunnelFromHost` messages containing Base64-encoded responses:

```xml
<reply>
  <name>TunnelFromHost</name>
  <id>2</id>
  <map>
    <item>
      <name>data</name>
      <base64>[Base64-encoded response]</base64>
    </item>
  </map>
</reply>
```

### Connection Initialization Sequence

This sequence MUST be followed for commands to work:

```
Client                                  Device
   │                                      │
   │──── TCP Connect (port 15555) ───────>│
   │                                      │
   │──── RequestAPIVersion ──────────────>│
   │<─── Reply (API version accepted) ────│
   │                                      │
   │──── TunnelToHost: *NVM SETUNSOLICITED ON\r ─>│
   │<─── TunnelFromHost: response ────────│
   │                                      │
   │     (Connection is now ready)        │
   │                                      │
   │──── TunnelToHost: *NVM SETINPUT X\r ─>│
   │<─── TunnelFromHost: response ────────│
   │                                      │
   │──── Disconnect ─────────────────────>│
   │                                      │
```

### Source Files (from decompiled Naim app)

| Class | Purpose |
|-------|---------|
| `Connection.java` | TCP socket management, port 15555 |
| `BCManager.java` | BC-layer connection lifecycle |
| `BCQueue.java` | Command queue for BC layer |
| `TunnelManager.java` | Startup sequence orchestration |
| `TunnelQueue.java` | Command queue for tunnel layer |
| `TunnelCommand.java` | Tunnel command construction |
| `TunnelConversation.java` | Request/response pairing |
| `CommandTunnelSendMessage.java` | XML/Base64 encoding |
| `UnitiConnectionManagerService.java` | NVM command implementations |

---

## 23. NVM Command Reference

NVM (Naim Virtual Machine) commands control device functions. Commands are ASCII strings
terminated with `\r` (carriage return).

### Command Format

```
*NVM <COMMAND> [<ARGS>...]\r
```

### Response Format

Responses are prefixed with `#NVM` and have space-separated fields:

```
#NVM <COMMAND> <VALUE1> <VALUE2> ...
```

For commands that return OK:
```
#NVM <COMMAND> OK
```

Error responses:
```
#NVM ERROR <COMMAND> <ERROR_CODE>
```

### Input Control Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM SETINPUT <name>\r` | Switch to input | `#NVM SETINPUT OK` |
| `*NVM GETINPUT\r` | Get current input | `#NVM GETINPUT <name>` |
| `*NVM INPUT+\r` | Next input | `#NVM INPUT+ <name>` |
| `*NVM INPUT-\r` | Previous input | `#NVM INPUT- <name>` |
| `*NVM GETINPUTBLK\r` | Get all inputs (bulk) | Multiple `#NVM GETINPUTBLK` responses (see below) |
| `*NVM SETINPUTENABLED <name> ON\|OFF\r` | Enable/disable input | `#NVM SETINPUTENABLED OK` |
| `*NVM GETINPUTENABLED <name>\r` | Check if enabled | `#NVM GETINPUTENABLED <name> ON\|OFF` |
| `*NVM SETINPUTNAME <name> "<display>"\r` | Set input display name | `#NVM SETINPUTNAME OK` |
| `*NVM GETINPUTNAME <name>\r` | Get input display name | `#NVM GETINPUTNAME <name> "<display>"` |
| `*NVM GETINPUTTRIM <name>\r` | Get input trim level | `#NVM GETINPUTTRIM <name> <level>` |
| `*NVM SETINPUTTRIM <name> <level>\r` | Set input trim level | `#NVM SETINPUTTRIM OK` |

### GETINPUTBLK Response Format

The `GETINPUTBLK` command returns one response line per input:

```
#NVM GETINPUTBLK <index> <total> <active> <input_id> "<display_name>"
```

| Field | Description | Example |
|-------|-------------|---------|
| `<index>` | Input number (1-based) | `5` |
| `<total>` | Total number of inputs | `18` |
| `<active>` | Whether input is enabled (0=disabled, 1=enabled) | `1` |
| `<input_id>` | Input identifier | `UPNP` |
| `<display_name>` | Display name (quoted) | `"UPnP"` |

Example response (multiple lines):
```
#NVM GETINPUTBLK 1 18 0 FM "FM"
#NVM GETINPUTBLK 2 18 0 DAB "DAB"
#NVM GETINPUTBLK 3 18 0 IRADIO "iRadio"
#NVM GETINPUTBLK 4 18 0 MULTIROOM "Multiroom"
#NVM GETINPUTBLK 5 18 1 UPNP "UPnP"
...
```

### Volume/Preamp Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM VOL+\r` | Volume up | `#NVM VOL+ <level>` |
| `*NVM VOL-\r` | Volume down | `#NVM VOL- <level>` |
| `*NVM SETRVOL <level>\r` | Set volume (0-100) | `#NVM SETRVOL OK` |
| `*NVM SETMUTE ON\|OFF\r` | Set mute state | `#NVM SETMUTE OK` |
| `*NVM GETPREAMP\r` | Get preamp status | `#NVM PREAMP <vol> <mute> <balance> <input> ... "<inputname>" ...` |
| `*NVM GETAMPMAXVOL\r` | Get max amplifier volume limit | `#NVM GETAMPMAXVOL <level>` |
| `*NVM SETAMPMAXVOL <level>\r` | Set max amplifier volume (0-100) | `#NVM SETAMPMAXVOL OK` |
| `*NVM GETHEADMAXVOL\r` | Get max headphone volume limit | `#NVM GETHEADMAXVOL <level>` |
| `*NVM GETBAL\r` | Get balance | `#NVM GETBAL <level>` |
| `*NVM SETBAL <level>\r` | Set balance | `#NVM SETBAL OK` |

### GETPREAMP Response Format

The `GETPREAMP` command returns detailed preamp status:

```
#NVM PREAMP <volume> <mute> <balance> <input_id> <arg5> <arg6> <arg7> <arg8> "<input_name>" <arg10>
```

| Field | Description | Example |
|-------|-------------|---------|
| `<volume>` | Current volume level (0-100) | `76` |
| `<mute>` | Mute status (0=unmuted, 1=muted) | `0` |
| `<balance>` | Balance level | `0` |
| `<input_id>` | Current input identifier | `UPNP` |
| `<input_name>` | Current input display name (quoted) | `"UPnP"` |

### Device Info Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM PRODUCT\r` | Get product type | `#NVM PRODUCT <type>` |
| `*NVM VERSION\r` | Get firmware version | `#NVM VERSION <ver> <build> <type> <release>` |
| `*NVM GETSEDMPTYPE\r` | Get hardware version | `#NVM GETSEDMPTYPE <type>` |
| `*NVM GETSEDMPCAPS\r` | Get DMP capabilities | `#NVM GETSEDMPCAPS <caps>` |
| `*NVM GETMAC\r` | Get MAC address | `#NVM GETMAC <b1> <b2> <b3> <b4> <b5> <b6>` (6 hex bytes) |
| `*NVM GETLANG\r` | Get language setting | `#NVM GETLANG <code>` |
| `*NVM GETROOMNAME\r` | Get room/device name | `#NVM GETROOMNAME "<name>"` |
| `*NVM SETROOMNAME "<name>"\r` | Set room/device name | `#NVM SETROOMNAME OK` |
| `*NVM GETSERIALNUM\r` | Get serial number (newer models) | `#NVM GETSERIALNUM <serial>` |
| `*NVM GETBSLVER\r` | Get BSL version | `#NVM GETBSLVER <version>` |

### Playback Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM PLAY\r` | Start playback | `#NVM PLAY OK` |
| `*NVM STOP\r` | Stop playback | `#NVM STOP OK` |
| `*NVM PAUSE ON\|OFF\r` | Set pause state | `#NVM PAUSE OK` |
| `*NVM PAUSE TOGGLE\r` | Toggle pause | `#NVM PAUSE OK` |
| `*NVM NEXTTRACK\r` | Skip to next track | `#NVM NEXTTRACK OK` |
| `*NVM PREVTRACK\r` | Skip to previous track | `#NVM PREVTRACK OK` |
| `*NVM FF ON\r` | Fast forward on | `#NVM FF OK` |
| `*NVM FF OFF\r` | Fast forward off | `#NVM FF OK` |
| `*NVM FR ON\r` | Fast rewind on | `#NVM FR OK` |
| `*NVM FR OFF\r` | Fast rewind off | `#NVM FR OK` |
| `*NVM REPEAT ON\|OFF\r` | Set repeat mode | `#NVM REPEAT OK` |
| `*NVM RANDOM ON\|OFF\r` | Set shuffle/random mode | `#NVM RANDOM OK` |

### Standby/Power Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM SETSTANDBY ON\r` | Put device into standby | `#NVM SETSTANDBY OK` |
| `*NVM SETSTANDBY OFF\r` | Wake device from standby | `#NVM SETSTANDBY OK` |
| `*NVM GETSTANDBYSTATUS\r` | Get standby status | `#NVM GETSTANDBYSTATUS ON\|OFF` |

### Bluetooth Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM BTSTATUS\r` | Get Bluetooth status | `#NVM BTSTATUS <status fields>` |
| `*NVM BTPAIR\r` | Start pairing mode | `#NVM BTPAIR OK` |
| `*NVM BTPAIR EXIT\r` | Exit pairing mode | `#NVM BTPAIR OK` |
| `*NVM BTDROPLINK\r` | Disconnect current device | `#NVM BTDROPLINK OK` |
| `*NVM BTDROPLINK FORGET\r` | Forget current device | `#NVM BTDROPLINK OK` |
| `*NVM BTRECONNECT\r` | Reconnect to last device | `#NVM BTRECONNECT OK` |
| `*NVM BTRESET\r` | Reset Bluetooth | `#NVM BTRESET OK` |
| `*NVM GETBTNAME\r` | Get BT device name | `#NVM GETBTNAME "<name>"` |
| `*NVM SETBTNAME "<name>"\r` | Set BT device name | `#NVM SETBTNAME OK` |
| `*NVM GETBTSECURITY\r` | Get BT security mode | `#NVM GETBTSECURITY OPEN\|CLOSED` |
| `*NVM SETBTSECURITY OPEN\|CLOSED\r` | Set BT security mode | `#NVM SETBTSECURITY OK` |
| `*NVM GETBTAUTORECONNECT\r` | Get auto-reconnect setting | `#NVM GETBTAUTORECONNECT ON\|OFF` |
| `*NVM SETBTAUTORECONNECT ON\|OFF\r` | Set auto-reconnect | `#NVM SETBTAUTORECONNECT OK` |
| `*NVM GETBTAUTOPLAY\r` | Get auto-play setting | `#NVM GETBTAUTOPLAY ON\|OFF` |
| `*NVM SETBTAUTOPLAY ON\|OFF\r` | Set auto-play | `#NVM SETBTAUTOPLAY OK` |

### Preset Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM GETTOTALPRESETS\r` | Get total preset count | `#NVM GETTOTALPRESETS <count>` |
| `*NVM GETPRESET <n>\r` | Get preset details | `#NVM GETPRESET <n> <uri> "<name>"` |
| `*NVM SETPRESET <n> <uri>\r` | Set preset content | `#NVM SETPRESET OK` |
| `*NVM GETPRESETBLK <start> <count>\r` | Bulk query presets | Multiple responses |
| `*NVM RENAMEPRESET <n> "<name>"\r` | Rename preset | `#NVM RENAMEPRESET OK` |
| `*NVM CLEARPRESET <n>\r` | Clear preset | `#NVM CLEARPRESET OK` |
| `*NVM GOTOPRESET <n>\r` | Play preset | `#NVM GOTOPRESET OK` |
| `*NVM PRESET+\r` | Next preset | `#NVM PRESET+ <n>` |
| `*NVM PRESET-\r` | Previous preset | `#NVM PRESET- <n>` |

### System Commands

| Command | Description | Response |
|---------|-------------|----------|
| `*NVM SETUNSOLICITED ON\|OFF\r` | Enable/disable unsolicited messages | `#NVM SETUNSOLICITED OK` |
| `*NVM SYNCDISP ON\|OFF\r` | Sync display state | `#NVM SYNCDISP OK` |
| `*NVM DEBUG ON\|OFF\r` | Enable debug output | `#NVM DEBUG OK` |
| `*NVM GETAUTOSTANDBYPERIOD\r` | Get auto-standby timeout | `#NVM GETAUTOSTANDBYPERIOD <mins>` |
| `*NVM SETAUTOSTANDBYPERIOD <mins>\r` | Set auto-standby timeout | `#NVM SETAUTOSTANDBYPERIOD OK` |

### Alarm Commands

| Command | Description |
|---------|-------------|
| `*NVM GETDATETIME\r` | Get device date/time |
| `*NVM GETALARMWEEKDAY\r` | Get weekday alarm |
| `*NVM SETALARMWEEKDAY ON <time> <input> <vol>\r` | Set weekday alarm |
| `*NVM GETALARMWEEKEND\r` | Get weekend alarm |
| `*NVM SETALARMWEEKEND ON <time> <input> <vol>\r` | Set weekend alarm |
| `*NVM GETALARMACTIVE\r` | Check if alarm is active |
| `*NVM CANCELACTIVEALARM CONT\r` | Cancel active alarm |

### Radio Commands

| Command | Description |
|---------|-------------|
| `*NVM GETIRADIOHIDDENROWS\r` | Get hidden iRadio rows |
| `*NVM GETBLASTCAPS\r` | Get IR blaster capabilities |

### Initial Startup Command

Used during app startup to get multiple values at once:

| Command | Description |
|---------|-------------|
| `*NVM GETINITIALINFO\r` | Get startup info (alarm, hidden rows, language, product, trackers, input, IR caps, multiroom) |
| `*NVM GETCHGTRACKERS\r` | Get change trackers |

### Product Type Codes

| Code | Device |
|------|--------|
| `SUPER_UNITI` | SuperUniti |
| `UNITI_LITE` | UnitiLite |
| `QUTE` | UnitiQute |
| `NDS` | NDS |
| `NDX` | NDX |
| `ND5XS` | ND5 XS |
| `NAC172` | NAC-N 172 XS |
| `NAC272` | NAC-N 272 |

### Error Codes

| Code | Meaning |
|------|---------|
| `4` | Unknown command (not supported on this device/firmware) |
| `401` | Invalid argument |
| `402` | Command not supported |
| `403` | Input not available |
| `404` | Resource not found |
| `500` | Internal error |

### Command Availability by Device

Not all commands are available on all devices. Availability depends on device model and firmware version.

**Commands tested on SuperUniti (firmware 2.0.11.14171):**

| Command | Status |
|---------|--------|
| `GETAMPMAXVOL` / `SETAMPMAXVOL` | Working |
| `GETHEADMAXVOL` | Working |
| `SETRVOL` / `SETMUTE` | Working |
| `GETBAL` / `SETBAL` | Working |
| `GETROOMNAME` / `SETROOMNAME` | Working |
| `BTSTATUS` / `BTPAIR` / `BTDROPLINK` | Working |
| `GETBTNAME` / `SETBTNAME` | Working |
| `GETILLUM` / `SETILLUM` | Error 4 (Not available) |
| `GETAUTOSTANDBYPERIOD` / `SETAUTOSTANDBYPERIOD` | Error 4 (Not available) |
| `GETSERIALNUM` | Error 4 (Not available) |

---

## Appendix: Protocol Evolution

### Legacy Era (2008-2015)

- SuperUniti, NDS, NDX, UnitiQute
- Primary protocol: n-Stream/BridgeCo on port 15555
- UPnP for playback only
- No REST API

### Transition Era (2015-2018)

- NAC-N 272, ND5 XS 2
- Added REST API (Leo) on port 15081
- n-Stream still used for some functions

### Modern Era (2018+)

- Uniti Atom, Star, Nova; Mu-so 2nd gen
- REST API (Leo) is primary protocol
- UPnP maintained for compatibility
- n-Stream deprecated (not present on newer devices)
