# Naim Streamer Control Protocols — Analysis

Reverse-engineered from the Naim Android application (decompiled APK).
All findings are based on static analysis of Retrofit interface declarations,
Moshi JSON model classes, and enum definitions found in the decompiled source.

Naim devices use **two distinct control protocols** depending on the device generation:

| Protocol | Script | Devices | Port |
|----------|--------|---------|------|
| **REST API** | `naim_control_rest.py` | Newer devices (Uniti series, Mu-so 2nd gen, etc.) | 15081 |
| **UPnP/DLNA** | `naim_control_upnp.py` | Legacy devices (SuperUniti, NDS, NDX, UnitiQute, etc.) | 8080 |

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

### Part B — UPnP/DLNA (legacy devices)

13. [UPnP/DLNA Protocol Overview](#13-upnpdlna-protocol-overview)
14. [UPnP Services & Control URLs](#14-upnp-services--control-urls)
15. [SOAP Action Reference](#15-soap-action-reference)

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
