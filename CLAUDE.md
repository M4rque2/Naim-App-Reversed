# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

**This is NOT a production project.**

This repository serves as:
- **Knowledge Base**: Documenting how Naim audio device protocols work
- **Reference Implementation**: Demo CLI tools showing protocol usage
- **Development Resource**: A starting point for future projects (Home Assistant integrations, custom apps, etc.)

The goal is understanding and documentation, not creating polished end-user software. The CLI tools are functional demos that prove the protocols work, not production-ready applications.

## Project Overview

Naim-App-Reversed is a reverse-engineering project documenting Naim audio device control protocols, with working CLI tools for device control.

**Status:** Milestone 1 Complete - Basic functionality implemented and tested.

## Protocol Architecture

Naim devices use three distinct control protocols:

| Protocol | Port | Script | Target Devices |
|----------|------|--------|----------------|
| REST API (Leo) | 15081 | `naim_control_rest.py` | Newer (Uniti, Mu-so 2nd gen) |
| UPnP/DLNA | 8080 | `naim_control_upnp.py` | All devices (playback/volume) |
| n-Stream/BridgeCo | 15555 | `naim_control_nstream.py` | Legacy (SuperUniti, NDS, NDX) |

## File Structure

```
├── naim_control_rest.py      # CLI for REST API (port 15081)
├── naim_control_upnp.py      # CLI for UPnP/DLNA (port 8080)
├── naim_control_nstream.py   # CLI for n-Stream (port 15555)
├── PROTOCOL_OVERVIEW.md      # High-level protocol summary
├── PROTOCOLS_DETAILED.md     # Detailed API specifications
├── USAGE.md                  # CLI usage guide
└── naim-atom-home-assistant/ # Reference: Home Assistant integration
```

## Tested Devices

- **Naim SuperUniti** (192.168.1.48): UPnP + n-Stream protocols verified
- **Naim Atom** (not tested locally): REST API based on naim-atom-home-assistant reference

## Key Implementation Notes

### n-Stream Protocol (Legacy Devices)
- Requires initialization: `RequestAPIVersion` then `SETUNSOLICITED ON`
- Commands are Base64-encoded NVM strings wrapped in XML
- Responses come as async `TunnelFromHost` events
- Use `GETINPUTBLK` to query device inputs (not hardcoded list)

### REST API (Newer Devices)
- Seek position is in **milliseconds** (not seconds)
- Mute uses integer `0`/`1` (not string `true`/`false`)
- WebSocket on port 4545 provides real-time status updates
- Default volume endpoint is `/levels/room`

### UPnP/DLNA
- Standard SOAP/XML protocol for playback and volume
- Cannot switch inputs on legacy devices (use n-Stream instead)
- Device discovery via SSDP multicast

## Development Commands

```bash
# Test n-Stream on legacy device
./naim_control_nstream.py --host 192.168.1.48 inputs
./naim_control_nstream.py --host 192.168.1.48 set-input --input DIGITAL2

# Test UPnP on any device
./naim_control_upnp.py --host 192.168.1.48 play
./naim_control_upnp.py --host 192.168.1.48 volume-set --level 50

# Test REST API on newer device
./naim_control_rest.py --host <ip> nowplaying
./naim_control_rest.py --host <ip> monitor --raw
```

## Future Work

- [ ] Test REST API on actual newer device
- [ ] Add event subscription (UPnP GENA)
- [ ] Implement local library browsing for legacy devices
- [ ] Create unified CLI that auto-detects device type
- [ ] Package as installable Python module

## Reference Sources

- Decompiled Naim Android APK (static analysis)
- naim-atom-home-assistant integration (REST API reference)
- Network traffic analysis on SuperUniti
