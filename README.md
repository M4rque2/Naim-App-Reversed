# naim-streamer-control

Unofficial Python tools for controlling Naim Audio streaming devices over your
local network, reverse-engineered from the official Naim Android application.
Includes CLI clients for all three device protocols **and a server-side emulator**
for testing without real hardware.

---

> **WARNING — READ BEFORE USE**
>
> This project is the result of reverse engineering a third-party application.
> It is **completely unofficial** and has **no affiliation with Naim Audio
> Limited** whatsoever.
>
> **THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
> OR IMPLIED. THE AUTHORS ACCEPT NO RESPONSIBILITY FOR ANY DAMAGE, DATA LOSS,
> DEVICE MALFUNCTION, VOIDED WARRANTY, BRICKED HARDWARE, OR ANY OTHER HARM
> THAT MAY RESULT FROM USING THIS SOFTWARE.**
>
> Sending undocumented commands to your device — including but not limited to
> firmware updates, factory resets, and network reconfiguration — **could
> permanently damage it or render it unrecoverable**. You use this software
> entirely at your own risk.

---

## What's Included

### CLI Clients

| File | Protocol | Target Devices | Port |
|------|----------|----------------|------|
| `naim_control_rest.py` | HTTP REST API + SSE | Newer devices (Uniti series, Mu-so 2nd gen) | 15081 |
| `naim_control_upnp.py` | UPnP/DLNA SOAP | All devices (playback, volume, media browsing) | 8080 |
| `naim_control_nstream.py` | n-Stream/BridgeCo | Legacy devices (input switching, preamp, BT) | 15555 |

### Emulator

| File | Description |
|------|-------------|
| `naim_emulator_legacy.py` | Server-side emulator for legacy Naim devices (SuperUniti etc.) |
| `device_profiles/superuniti.json` | SuperUniti device configuration (18 inputs) |
| `device_state/<model>_state.json` | Auto-saved mutable state (created at runtime) |

### Documentation

| File | Description |
|------|-------------|
| `PROTOCOLS_DETAILED.md` | Full technical analysis — all three protocols, JSON schemas, NVM command reference |
| `PROTOCOL_OVERVIEW.md` | High-level protocol summary |
| `USAGE.md` | Complete CLI usage guide and emulator reference |

---

## Protocol Architecture

Naim devices use **three distinct protocols** depending on the device generation:

| Device generation | Protocols available |
|-------------------|---------------------|
| **Legacy** (SuperUniti, NDS, NDX, UnitiQute, NAC-N 272) | n-Stream :15555 + UPnP :8080 |
| **Modern** (Uniti Atom/Star/Nova, Mu-so 2nd gen) | REST :15081 + UPnP :8080 |

**Inferred server-side architecture on legacy devices:**

```
  TCP :15555  n-Stream ──┐
  HTTP :8080  UPnP     ──┤──► NVM Command Queue ──► Device State ──► Hardware (DSP)
                          │       (serialised)         (single owner)
                          └─ SETUNSOLICITED broadcast to all connected n-Stream clients
```

Both protocol adapters share a single command queue and a single state store —
which is why a volume change via UPnP is immediately visible via n-Stream
`GETPREAMP`, and vice versa.

See `PROTOCOLS_DETAILED.md` for the full technical analysis.

---

## Quick Start

### Legacy devices (SuperUniti, NDS, NDX…)

```bash
# Input switching — n-Stream protocol (port 15555)
./naim_control_nstream.py --host 192.168.1.48 inputs
./naim_control_nstream.py --host 192.168.1.48 set-input --input DIGITAL2
./naim_control_nstream.py --host 192.168.1.48 vol-set --level 40

# Playback and volume — UPnP (port 8080)
./naim_control_upnp.py --host 192.168.1.48 play
./naim_control_upnp.py --host 192.168.1.48 volume-set --level 40
```

### Newer devices (Uniti Atom, Nova, Mu-so 2nd gen…)

```bash
# Discover devices on the network
./naim_control_rest.py discover

# Check what is currently playing
./naim_control_rest.py --host 192.168.1.50 nowplaying

# Play / pause / volume
./naim_control_rest.py --host 192.168.1.50 play
./naim_control_rest.py --host 192.168.1.50 volume-set --level 40
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/tidal

# Real-time state stream via SSE (GET /notify)
./naim_control_rest.py --host 192.168.1.50 monitor
./naim_control_rest.py --host 192.168.1.50 monitor --ussi nowplaying
```

---

## Device Emulator

`naim_emulator_legacy.py` emulates a legacy Naim device so you can develop and
test client tools without real hardware.

```bash
# Start the emulator (SuperUniti profile, default ports)
./naim_emulator_legacy.py --model superuniti

# Verbose — shows every parsed command and response
./naim_emulator_legacy.py --model superuniti --verbose

# Debug — raw bytes + full XML (for wire-protocol debugging)
./naim_emulator_legacy.py --model superuniti --debug
```

The emulator listens on the same ports as a real device:

| Service | Port |
|---------|------|
| n-Stream/BridgeCo | TCP 15555 |
| UPnP/DLNA | HTTP 8080 |
| SSDP discovery | UDP 239.255.255.250:1900 |

**State persistence:** volume, current input, per-input names, Bluetooth
settings and all other mutable state are auto-saved to
`device_state/superuniti_state.json` after every change and reloaded on restart.
Use `--no-persist` to start fresh each run, or `--state-file <path>` to
specify a custom save path.

**Status display:** shown at startup, after each client session (in verbose
mode), and on shutdown:

```
┌─ SuperUniti ──────────────────────────────────────┐
│  Input    : DIGITAL2  "TV Audio"                  │
│  Volume   : 40                                    │
│  Playback : STOPPED   Repeat: OFF   Shuffle: OFF  │
│  Power    : On   Auto-standby: 20 min             │
│  Room     : Living Room                           │
│  BT       : INACTIVE   Name: "SuperUniti"         │
└───────────────────────────────────────────────────┘
```

Test against the emulator with the same CLI clients:

```bash
./naim_control_nstream.py --host 127.0.0.1 inputs
./naim_control_nstream.py --host 127.0.0.1 set-input --input DIGITAL2
./naim_control_nstream.py --host 127.0.0.1 vol-set --level 40
./naim_control_upnp.py    --host 127.0.0.1 volume-get
./naim_control_upnp.py    --host 127.0.0.1 transport-info
```

See `USAGE.md` for the full emulator reference including device profile
customisation and adding new models.

---

## Tested Hardware

| Device | IP | Protocols verified |
|--------|----|--------------------|
| Naim SuperUniti | 192.168.1.48 | n-Stream ✅  UPnP ✅ |
| Naim Atom | — (not tested locally) | REST API modelled from naim-atom-home-assistant reference |

---

## Requirements

- Python 3.10 or later (standard library only — no `pip install` needed)
- A Naim device on the local network, or the emulator for offline testing

---

## Disclaimer

This project is not affiliated with, endorsed by, or connected to
**Naim Audio Limited** in any way. All product names, trademarks, and
registered trademarks are the property of their respective owners.

The API was discovered through lawful reverse engineering of an Android
application for the purpose of interoperability. No proprietary code from
the original application is included in this repository.

**THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.**

Use at your own risk.

## License

[MIT](LICENSE)
