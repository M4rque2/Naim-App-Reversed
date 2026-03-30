# Naim Streamer Control — Usage Guide

Reverse-engineered from the Naim Android application. Controls Naim streaming
devices (Uniti, Mu-so, NAC-N, SuperUniti, NDS, NDX, etc.) over your local
network. Also includes a **server-side emulator** for testing without hardware.

There are **three CLI scripts** (one per protocol) plus the emulator:

| Script | Protocol | Devices | Default Port |
|--------|----------|---------|-------------|
| `naim_control_rest.py` | HTTP REST API + SSE | Newer devices (Uniti series, Mu-so 2nd gen) | 15081 |
| `naim_control_upnp.py` | UPnP/DLNA SOAP | All devices (playback, volume) | 8080 |
| `naim_control_nstream.py` | n-Stream/BridgeCo | Legacy devices (input switching, preamp) | 15555 |
| `naim_emulator_legacy.py` | All legacy protocols | Emulates SuperUniti etc. for testing | 15555 / 8080 |

## Requirements

- Python 3.6 or later (no third-party packages required)
- Naim device on the same local network
- Device IP address (find it in your router's DHCP table or the Naim app)

## Quick Start

### Newer devices (REST API)

```bash
# Make the script executable (once)
chmod +x naim_control_rest.py

# Discover devices on the network
./naim_control_rest.py discover

# Check what is currently playing
./naim_control_rest.py --host 192.168.1.50 nowplaying

# Play / pause / skip
./naim_control_rest.py --host 192.168.1.50 play
./naim_control_rest.py --host 192.168.1.50 pause
./naim_control_rest.py --host 192.168.1.50 next

# Set volume to 40
./naim_control_rest.py --host 192.168.1.50 volume-set --level 40
```

### Legacy devices (UPnP/DLNA)

```bash
# Make the script executable (once)
chmod +x naim_control_upnp.py

# Discover legacy devices on the network
./naim_control_upnp.py discover

# Get device info
./naim_control_upnp.py --host 192.168.1.21 info

# Play / pause / stop
./naim_control_upnp.py --host 192.168.1.21 play
./naim_control_upnp.py --host 192.168.1.21 pause

# Volume control
./naim_control_upnp.py --host 192.168.1.21 volume-get
./naim_control_upnp.py --host 192.168.1.21 volume-set --level 50
```

---

# REST API Commands (`naim_control_rest.py`)

## Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | *(required)* | IP address of the Naim device |
| `--port` | `15081` | HTTP API port (do not change unless you know why) |

All output is printed as formatted JSON to stdout. Errors go to stderr.

---

## Command Reference

### API Info

| Command | Description |
|---------|-------------|
| `api-info` | Query supported API feature versions on the device |

```bash
./naim_control_rest.py --host 192.168.1.50 api-info
```

---

### Real-Time Monitor (SSE)

The device pushes state-change events in real time via **Server-Sent Events**
on `GET /notify` — the same port (15081) as the REST API.  Each event carries
the `ussi` of the resource that changed and only the fields that changed:

```
[14:32:01] nowplaying  (id=47)
  transportState  Playing
  seekPosition    90000
```

| Command | Options | Description |
|---------|---------|-------------|
| `monitor` | `[--ussi <filter>]` `[--raw]` | Stream real-time events from `/notify` |

| Option | Description |
|--------|-------------|
| `--ussi <str>` | Only show events whose `ussi` contains this substring (e.g. `nowplaying`, `levels`) |
| `--raw` | Print full JSON event instead of formatted output |

```bash
# Stream all events
./naim_control_rest.py --host 192.168.1.50 monitor

# Only nowplaying changes (transport state, track metadata)
./naim_control_rest.py --host 192.168.1.50 monitor --ussi nowplaying

# Only volume changes
./naim_control_rest.py --host 192.168.1.50 monitor --ussi levels

# Full JSON, all events
./naim_control_rest.py --host 192.168.1.50 monitor --raw
```

---

### System

| Command | Options | Description |
|---------|---------|-------------|
| `system-info` | — | Device model, versions, serial number |
| `system-usage` | — | CPU/memory usage statistics |
| `system-datetime` | — | Current device date and time |
| `system-reboot` | — | Reboot the device |
| `system-keepawake` | — | Send keep-alive ping to prevent standby |
| `system-firstsetup` | `--complete true\|false` | Mark first-time setup complete/incomplete |

```bash
./naim_control_rest.py --host 192.168.1.50 system-info
./naim_control_rest.py --host 192.168.1.50 system-reboot
./naim_control_rest.py --host 192.168.1.50 system-keepawake
```

---

### Power

| Command | Options | Description |
|---------|---------|-------------|
| `power-get` | — | Get current power state |
| `power-set` | `--state on\|off\|lona\|loff` | Set power state (`lona`/`loff` = network standby on/off) |
| `power-server` | `--enable true\|false` | Enable/disable server mode |
| `power-timeout` | `--minutes <n>` | Set standby timeout in minutes (0 = disabled) |

```bash
./naim_control_rest.py --host 192.168.1.50 power-get
./naim_control_rest.py --host 192.168.1.50 power-set --state on
./naim_control_rest.py --host 192.168.1.50 power-set --state off
./naim_control_rest.py --host 192.168.1.50 power-timeout --minutes 20
```

---

### Playback Control

| Command | Options | Description |
|---------|---------|-------------|
| `nowplaying` | — | Get current track / playback state |
| `play` | — | Start playback |
| `pause` | — | Pause playback |
| `stop` | — | Stop playback |
| `resume` | — | Resume playback |
| `next` | — | Skip to next track |
| `prev` | — | Skip to previous track |
| `toggle` | — | Toggle between play and pause |
| `seek` | `--position <seconds>` | Seek to position within current track |
| `repeat` | `--mode 0\|1\|2` | Set repeat mode: `0`=off, `1`=repeat all, `2`=repeat one |
| `shuffle` | `--enable true\|false` | Enable or disable shuffle |

```bash
./naim_control_rest.py --host 192.168.1.50 nowplaying
./naim_control_rest.py --host 192.168.1.50 toggle
./naim_control_rest.py --host 192.168.1.50 seek --position 90
./naim_control_rest.py --host 192.168.1.50 repeat --mode 1
./naim_control_rest.py --host 192.168.1.50 shuffle --enable true
```

---

### Volume & Levels

| Command | Options | Description |
|---------|---------|-------------|
| `levels-get` | — | Get main volume and mute state |
| `levels-room` | — | Get room/zone volume |
| `levels-group` | — | Get multiroom group volume |
| `levels-bluetooth` | — | Get Bluetooth volume |
| `volume-set` | `--level <0-100>` `[--ussi <path>]` | Set volume. Default target: `levels` |
| `mute` | `[--ussi <path>]` | Mute audio |
| `unmute` | `[--ussi <path>]` | Unmute audio |
| `balance` | `--value <n>` `[--ussi <path>]` | Set left/right balance |
| `volume-mode` | `--mode 0\|1\|2` `[--ussi <path>]` | Volume mode: `0`=Variable, `1`=Hybrid, `2`=Fixed |

The `--ussi` argument lets you target a specific zone: `levels`, `levels/room`,
`levels/group`, or `levels/bluetooth`.

```bash
./naim_control_rest.py --host 192.168.1.50 levels-get
./naim_control_rest.py --host 192.168.1.50 volume-set --level 35
./naim_control_rest.py --host 192.168.1.50 volume-set --level 20 --ussi levels/room
./naim_control_rest.py --host 192.168.1.50 mute
./naim_control_rest.py --host 192.168.1.50 unmute
./naim_control_rest.py --host 192.168.1.50 balance --value 0
./naim_control_rest.py --host 192.168.1.50 volume-mode --mode 0
```

---

### Inputs

A USSI (Universal Stream Source Identifier) is a path string that identifies an
input, e.g. `inputs/spotify`, `inputs/tidal`, `inputs/upnp`.

**Known input USSSIs:**

| USSI | Source |
|------|--------|
| `inputs/airplay` | AirPlay |
| `inputs/analogue` / `inputs/ana` | Analogue input |
| `inputs/bluetooth` | Bluetooth |
| `inputs/cd` | CD player |
| `inputs/dab` | DAB radio |
| `inputs/digital` / `inputs/dig` | Digital input |
| `inputs/fm` | FM radio |
| `inputs/gcast` | Google Chromecast |
| `inputs/hdmi` | HDMI |
| `inputs/multiroom` | Multiroom |
| `inputs/phono` | Phono |
| `inputs/playqueue` | Play queue |
| `inputs/qobuz` | Qobuz |
| `inputs/radio` | iRadio (internet radio) |
| `inputs/spotify` | Spotify |
| `inputs/tidal` | Tidal |
| `inputs/upnp` | UPnP / DLNA |
| `inputs/usb` | USB storage |

| Command | Options | Description |
|---------|---------|-------------|
| `inputs-list` | — | List all available inputs and their state |
| `input-details` | `--ussi <path>` | Get full details of an input |
| `input-select` | `--ussi <path>` | Switch to this input |
| `input-play` | `--ussi <path>` | Start playing from this input |
| `input-resume` | `--ussi <path>` | Resume playback from this input |
| `input-rename` | `--ussi <path>` `--name <name>` | Set custom display name |
| `input-disable` | `--ussi <path>` `--disabled true\|false` | Show/hide input from list |
| `input-trim` | `--ussi <path>` `--value <dB>` | Input level trim (dB) |
| `input-sensitivity` | `--ussi <path>` `--value <n>` | Input sensitivity |
| `input-unity-gain` | `--ussi <path>` `--enable true\|false` | Unity gain mode |

```bash
./naim_control_rest.py --host 192.168.1.50 inputs-list
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/tidal
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/spotify
./naim_control_rest.py --host 192.168.1.50 input-rename --ussi inputs/analogue --name "Turntable"
./naim_control_rest.py --host 192.168.1.50 input-disable --ussi inputs/hdmi --disabled true
./naim_control_rest.py --host 192.168.1.50 input-trim --ussi inputs/analogue --value -3
```

---

### Outputs

**Known output USSSIs:**

| USSI | Output |
|------|--------|
| `outputs` | All outputs (global settings) |
| `outputs/analogue` | Analogue output |
| `outputs/digital` | Digital output |
| `outputs/headphone` | Headphone output |
| `outputs/preamp` | Pre-amp output |
| `outputs/poweramp` | Power amplifier output |
| `outputs/aux` | Aux / subwoofer output |

| Command | Options | Description |
|---------|---------|-------------|
| `outputs-list` | — | List all outputs |
| `output-details` | `--ussi <path>` | Get output configuration |
| `output-enabled` | `--ussi <path>` `--enable true\|false` | Enable/disable output |
| `output-max-volume` | `--ussi <path>` `--value <n>` | Set maximum volume cap |
| `loudness` | `--value <n>` | Set loudness compensation level |
| `loudness-enabled` | `--enable true\|false` | Toggle loudness compensation |
| `room-position` | `--position 0\|1\|2` | Speaker placement: `0`=freestanding, `1`=wall, `2`=corner |
| `dsd-mode` | `--mode <n>` | DSD output format for digital output |

```bash
./naim_control_rest.py --host 192.168.1.50 outputs-list
./naim_control_rest.py --host 192.168.1.50 output-details --ussi outputs/analogue
./naim_control_rest.py --host 192.168.1.50 loudness-enabled --enable true
./naim_control_rest.py --host 192.168.1.50 room-position --position 1
./naim_control_rest.py --host 192.168.1.50 output-max-volume --ussi outputs/analogue --value 80
```

---

### Bluetooth

| Command | Options | Description |
|---------|---------|-------------|
| `bt-pair` | — | Start Bluetooth pairing mode |
| `bt-stop-pair` | — | Stop pairing mode |
| `bt-clear-history` | — | Clear pairing history (keep connected) |
| `bt-drop` | — | Disconnect current Bluetooth device |
| `bt-forget` | — | Disconnect and forget all paired devices |
| `bt-auto-pair` | `--enable true\|false` | Enable automatic pairing (open mode) |

```bash
./naim_control_rest.py --host 192.168.1.50 bt-pair
./naim_control_rest.py --host 192.168.1.50 bt-stop-pair
./naim_control_rest.py --host 192.168.1.50 bt-drop
./naim_control_rest.py --host 192.168.1.50 bt-forget
```

---

### Streaming Services

#### Qobuz

| Command | Options | Description |
|---------|---------|-------------|
| `qobuz-login` | `--username` `--password` | Authenticate with Qobuz |
| `qobuz-quality` | `--quality <n>` | Set streaming quality |
| `qobuz-logout` | — | Log out of Qobuz |

Quality values: `5`=320 kbps MP3, `6`=FLAC 16-bit/44.1 kHz, `7`=FLAC 24-bit,
`27`=FLAC 24-bit Hi-Res

```bash
./naim_control_rest.py --host 192.168.1.50 qobuz-login --username user@example.com --password secret
./naim_control_rest.py --host 192.168.1.50 qobuz-quality --quality 6
./naim_control_rest.py --host 192.168.1.50 qobuz-logout
```

#### Tidal

| Command | Options | Description |
|---------|---------|-------------|
| `tidal-login` | `--access-token` `--refresh-token` `[--oauth-ident]` | Login via OAuth tokens |
| `tidal-logout` | — | Log out of Tidal |

```bash
./naim_control_rest.py --host 192.168.1.50 tidal-login \
    --access-token <token> --refresh-token <token>
./naim_control_rest.py --host 192.168.1.50 tidal-logout
```

#### Spotify

| Command | Options | Description |
|---------|---------|-------------|
| `spotify-bitrate` | `--bitrate normal\|high\|very_high` | Set streaming bitrate |
| `spotify-gain-norm` | `--enable true\|false` | Spotify loudness normalisation |
| `spotify-presets` | — | List saved Spotify presets |
| `spotify-preset-save` | `--preset-id <1-6>` | Save current track as preset |

```bash
./naim_control_rest.py --host 192.168.1.50 spotify-bitrate --bitrate very_high
./naim_control_rest.py --host 192.168.1.50 spotify-gain-norm --enable false
./naim_control_rest.py --host 192.168.1.50 spotify-presets
./naim_control_rest.py --host 192.168.1.50 spotify-preset-save --preset-id 1
```

---

### Internet Radio (iRadio / FM / DAB)

| Command | Options | Description |
|---------|---------|-------------|
| `iradio-browse` | — | List iRadio stations/categories |
| `iradio-scan` | `[--ussi <path>]` | Auto-scan for stations |
| `iradio-scan-up` | `[--ussi <path>]` | Scan upward for next station (FM/DAB) |
| `iradio-scan-down` | `[--ussi <path>]` | Scan downward for next station |
| `iradio-scan-stop` | `[--ussi <path>]` | Stop scanning |
| `iradio-step-up` | `[--ussi <path>]` | Step up one preset/channel |
| `iradio-step-down` | `[--ussi <path>]` | Step down one preset/channel |
| `iradio-play` | `--ussi <path>` `[--station-key <url>]` | Play a specific station |
| `iradio-add-station` | `--name` `--station-key <url>` `[--genre]` `[--location]` `[--bitrate]` `[--artwork <url>]` | Add custom internet radio station |
| `iradio-delete-station` | `--ussi <path>` | Delete a user-added station |

The `--ussi` for FM/DAB defaults to `inputs/fm` or `inputs/dab` when omitted.
For iRadio, it defaults to `inputs/radio`.

```bash
./naim_control_rest.py --host 192.168.1.50 iradio-browse
./naim_control_rest.py --host 192.168.1.50 iradio-play --ussi inputs/radio/123
./naim_control_rest.py --host 192.168.1.50 iradio-scan-up --ussi inputs/fm
./naim_control_rest.py --host 192.168.1.50 iradio-add-station \
    --name "My Station" \
    --station-key "http://stream.example.com/live" \
    --genre "Jazz" \
    --bitrate 128
```

---

### Play Queue

| Command | Options | Description |
|---------|---------|-------------|
| `playqueue-get` | — | Get current play queue |
| `playqueue-clear` | — | Clear the entire play queue |
| `playqueue-move` | `--what <ussi>` `[--where <ussi>]` | Move a track within the queue |
| `playqueue-set-current` | `--ussi <track-ussi>` | Jump to a specific track in queue |
| `playqueue-track` | `--track-ussi <ussi>` | Get details of a queued track |

```bash
./naim_control_rest.py --host 192.168.1.50 playqueue-get
./naim_control_rest.py --host 192.168.1.50 playqueue-clear
./naim_control_rest.py --host 192.168.1.50 playqueue-set-current --ussi inputs/playqueue/5
```

---

### Favourites & Presets

| Command | Options | Description |
|---------|---------|-------------|
| `favourites-list` | `[--presets-only]` `[--available-only]` | List favourites |
| `favourite-details` | `--ussi <path>` | Get details of a favourite |
| `favourite-play` | `--ussi <path>` | Play a favourite |
| `favourite-delete` | `--ussi <path>` | Remove a favourite |
| `preset-assign` | `--ussi <path>` `--preset-id <1-40>` | Assign favourite to preset slot |
| `preset-deassign` | `--ussi <path>` | Remove preset assignment |
| `preset-move` | `--from-pos <n>` `--to-pos <n>` | Reorder presets |

```bash
./naim_control_rest.py --host 192.168.1.50 favourites-list
./naim_control_rest.py --host 192.168.1.50 favourites-list --presets-only
./naim_control_rest.py --host 192.168.1.50 favourite-play --ussi favourites/1
./naim_control_rest.py --host 192.168.1.50 preset-assign --ussi favourites/3 --preset-id 1
./naim_control_rest.py --host 192.168.1.50 preset-move --from-pos 3 --to-pos 1
```

---

### Multiroom

| Command | Options | Description |
|---------|---------|-------------|
| `multiroom-get` | — | Get multiroom group status |
| `multiroom-add` | `--ussi <path>` | Add a room/device to the group |
| `multiroom-remove` | `--ussi <path>` | Remove a device from the group |

```bash
./naim_control_rest.py --host 192.168.1.50 multiroom-get
./naim_control_rest.py --host 192.168.1.50 multiroom-add --ussi inputs/multiroom/bedroom
./naim_control_rest.py --host 192.168.1.50 multiroom-remove --ussi inputs/multiroom/bedroom
```

---

### CD Player

| Command | Options | Description |
|---------|---------|-------------|
| `cd-info` | — | Get CD player status and disc info |
| `cd-eject` | — | Eject the disc |
| `cd-play` | — | Play from first track |
| `cd-insert-action` | `--action 0\|1\|2` | Action on disc insert: `0`=nothing, `1`=play, `2`=rip |

```bash
./naim_control_rest.py --host 192.168.1.50 cd-info
./naim_control_rest.py --host 192.168.1.50 cd-play
./naim_control_rest.py --host 192.168.1.50 cd-eject
./naim_control_rest.py --host 192.168.1.50 cd-insert-action --action 1
```

---

### Alarms & Sleep Timer

| Command | Options | Description |
|---------|---------|-------------|
| `alarm-list` | — | List all configured alarms |
| `alarm-details` | `--ussi <path>` | Get details of an alarm |
| `alarm-set` | `--name` `--source <ussi>` `[--hours]` `[--minutes]` `[--days <bitmask>]` `[--enabled true\|false]` | Create or update an alarm |
| `alarm-enable` | `--ussi <path>` `--enable true\|false` | Enable or disable an alarm |
| `alarm-delete` | `--ussi <path>` | Delete an alarm |
| `sleep-start` | `--minutes <n>` | Start sleep timer (auto-off after N minutes) |
| `sleep-stop` | — | Cancel active sleep timer |

The `--days` bitmask for recurrence: `1`=Mon, `2`=Tue, `4`=Wed, `8`=Thu,
`16`=Fri, `32`=Sat, `64`=Sun. Combine with addition (e.g. `31` = weekdays).

```bash
./naim_control_rest.py --host 192.168.1.50 alarm-list
./naim_control_rest.py --host 192.168.1.50 alarm-set \
    --name "Morning" \
    --source inputs/radio \
    --hours 7 \
    --minutes 30 \
    --days 31 \
    --enabled true
./naim_control_rest.py --host 192.168.1.50 alarm-enable --ussi alarms/1 --enable false
./naim_control_rest.py --host 192.168.1.50 alarm-delete --ussi alarms/1
./naim_control_rest.py --host 192.168.1.50 sleep-start --minutes 45
./naim_control_rest.py --host 192.168.1.50 sleep-stop
```

---

### Network Configuration

| Command | Options | Description |
|---------|---------|-------------|
| `network-get` | — | Get full network configuration |
| `network-hostname` | `--hostname <name>` | Set device hostname |
| `network-scan-wifi` | — | Scan for available WiFi networks |
| `network-setup-wifi` | `--ssid <name>` `--key <password>` | Connect to a WiFi network |
| `network-dhcp` | `--iface <path>` | Enable DHCP on interface |
| `network-static` | `--iface <path>` `--ip` `--netmask` `--gateway` `--dns1` `[--dns2]` | Set static IP |
| `network-samba` | `--enable true\|false` | Enable/disable Samba SMB1 (legacy file sharing) |

Interface paths: `network/ethernet`, `network/wireless`

```bash
./naim_control_rest.py --host 192.168.1.50 network-get
./naim_control_rest.py --host 192.168.1.50 network-scan-wifi
./naim_control_rest.py --host 192.168.1.50 network-setup-wifi --ssid "MyNetwork" --key "password123"
./naim_control_rest.py --host 192.168.1.50 network-dhcp --iface network/ethernet
./naim_control_rest.py --host 192.168.1.50 network-static \
    --iface network/ethernet \
    --ip 192.168.1.50 \
    --netmask 255.255.255.0 \
    --gateway 192.168.1.1 \
    --dns1 8.8.8.8 \
    --dns2 8.8.4.4
./naim_control_rest.py --host 192.168.1.50 network-hostname --hostname naim-living-room
```

---

### Firmware Updates

| Command | Options | Description |
|---------|---------|-------------|
| `update-get` | — | Get current and available firmware versions |
| `update-check` | — | Trigger a check for new firmware |
| `update-start` | — | Start firmware update (use with caution) |

```bash
./naim_control_rest.py --host 192.168.1.50 update-get
./naim_control_rest.py --host 192.168.1.50 update-check
./naim_control_rest.py --host 192.168.1.50 update-start
```

---

### Browse Local Library

These commands browse media stored on USB drives or network shares attached to
the device.

| Command | Options | Description |
|---------|---------|-------------|
| `browse-tracks` | `[--offset <n>]` `[--limit <n>]` | List tracks in local library |
| `browse-albums` | `[--offset <n>]` `[--limit <n>]` | List albums |
| `browse-artists` | `[--offset <n>]` `[--limit <n>]` | List artists |
| `browse-play` | `--ussi <path>` | Play item immediately |
| `browse-play-next` | `--ussi <path>` | Add item to play next |
| `browse-play-last` | `--ussi <path>` | Add item to end of queue |
| `browse-refresh` | `--ussi <path>` | Refresh metadata for item |

```bash
./naim_control_rest.py --host 192.168.1.50 browse-albums --offset 0 --limit 20
./naim_control_rest.py --host 192.168.1.50 browse-artists
./naim_control_rest.py --host 192.168.1.50 browse-play --ussi albums/42
./naim_control_rest.py --host 192.168.1.50 browse-play-next --ussi tracks/101
```

---

## Common Workflows

### Switch source and play

```bash
# Switch to Spotify and start playing
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/spotify
./naim_control_rest.py --host 192.168.1.50 play

# Switch to Tidal
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/tidal

# Switch to UPnP/DLNA
./naim_control_rest.py --host 192.168.1.50 input-select --ussi inputs/upnp
```

### Volume control

```bash
# Set volume
./naim_control_rest.py --host 192.168.1.50 volume-set --level 45

# Mute / unmute
./naim_control_rest.py --host 192.168.1.50 mute
./naim_control_rest.py --host 192.168.1.50 unmute

# Fix maximum volume so guests cannot go too loud
./naim_control_rest.py --host 192.168.1.50 output-max-volume --ussi outputs/analogue --value 70
```

### Morning alarm

```bash
# Create a weekday alarm at 07:30 playing iRadio
./naim_control_rest.py --host 192.168.1.50 alarm-set \
    --name "Weekday Wake" \
    --source inputs/radio \
    --hours 7 --minutes 30 \
    --days 31 \
    --enabled true
```

### Quick shell alias

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias naim='python3 /path/to/naim_control_rest.py --host 192.168.1.50'
```

Then use:

```bash
naim nowplaying
naim play
naim volume-set --level 30
naim input-select --ussi inputs/tidal
```

---

## Protocol Notes

- **Port:** `15081` (hardcoded in device firmware)
- **Protocol:** Plain HTTP (not HTTPS)
- **Authentication:** None — local network access only
- **Response format:** JSON
- **USSI paths** are case-sensitive
- Most `GET` commands with `?cmd=...` trigger an action; bare `GET` commands
  return state
- `PUT` commands change a setting via query parameters
- `POST` commands create new resources
- `DELETE` commands remove resources

---

## Troubleshooting

**Connection error / timeout**
- Verify the device IP address with `ping <ip>` or your router's DHCP table
- Ensure the device is powered on and not in deep standby
- The device and your computer must be on the same local network segment
- Some devices need `system-keepawake` sent first if in network standby

**HTTP 404 Not Found**
- The USSI path may not exist on your specific device model
- Run `inputs-list` or `outputs-list` to discover valid paths

**HTTP 400 Bad Request**
- Check parameter names and values match what the device expects
- Some parameters are device-model-specific

**Device not responding after reboot**
- Allow 30-60 seconds for the device to restart fully

---

# UPnP/DLNA Commands (`naim_control_upnp.py`)

For legacy Naim devices that do not have a REST API on port 15081
(SuperUniti, NDS, NDX, UnitiQute, etc.).

## Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | *(required)* | IP address of the Naim device |
| `--port` | `8080` | UPnP port (do not change unless you know why) |

Output is displayed as key-value pairs. Errors go to stderr.

---

## Command Reference

### Discovery

| Command | Options | Description |
|---------|---------|-------------|
| `discover` | `[--timeout <seconds>]` | Discover Naim UPnP devices via SSDP/mDNS |

```bash
./naim_control_upnp.py discover
./naim_control_upnp.py discover --timeout 10
```

---

### Device Info

| Command | Description |
|---------|-------------|
| `info` | Fetch and display UPnP description.xml (model, manufacturer, services) |

```bash
./naim_control_upnp.py --host 192.168.1.21 info
```

---

### Playback Control (AVTransport)

| Command | Options | Description |
|---------|---------|-------------|
| `play` | — | Start/resume playback |
| `pause` | — | Pause playback |
| `stop` | — | Stop playback |
| `next` | — | Skip to next track |
| `prev` | — | Skip to previous track |
| `seek` | `--target HH:MM:SS` | Seek to position |
| `transport-info` | — | Get current transport state |
| `position-info` | — | Get track position, duration, metadata |
| `media-info` | — | Get media info (URI, number of tracks) |

```bash
./naim_control_upnp.py --host 192.168.1.21 play
./naim_control_upnp.py --host 192.168.1.21 pause
./naim_control_upnp.py --host 192.168.1.21 next
./naim_control_upnp.py --host 192.168.1.21 seek --target 00:02:30
./naim_control_upnp.py --host 192.168.1.21 transport-info
./naim_control_upnp.py --host 192.168.1.21 position-info
./naim_control_upnp.py --host 192.168.1.21 media-info
```

---

### Volume Control (RenderingControl)

| Command | Options | Description |
|---------|---------|-------------|
| `volume-get` | — | Get current volume level |
| `volume-set` | `--level <0-100>` | Set volume level |
| `mute` | — | Mute audio |
| `unmute` | — | Unmute audio |
| `mute-get` | — | Get current mute state |

```bash
./naim_control_upnp.py --host 192.168.1.21 volume-get
./naim_control_upnp.py --host 192.168.1.21 volume-set --level 50
./naim_control_upnp.py --host 192.168.1.21 mute
./naim_control_upnp.py --host 192.168.1.21 unmute
./naim_control_upnp.py --host 192.168.1.21 mute-get
```

---

### Input/Source Discovery and Selection

Legacy Naim devices expose available inputs via the UPnP ContentDirectory service.
Use these commands to discover and select inputs.

| Command | Options | Description |
|---------|---------|-------------|
| `services` | `[-v\|--verbose]` | List all UPnP services and their available actions |
| `inputs-list` | `[-r\|--recursive]` | List available inputs via ContentDirectory |
| `input-browse` | `--object-id <id>` | Browse a specific container by ObjectID |
| `current-input` | — | Show the current input/source being played |
| `input-select` | `[--uri <url>] [--object-id <id>] [--play]` | Select an input |
| `protocol-info` | — | Show supported media protocols (ConnectionManager) |
| `input-settings` | — | Show input-related settings (if supported) |

```bash
# List all UPnP services available on the device
./naim_control_upnp.py --host 192.168.1.21 services

# List services with detailed action information
./naim_control_upnp.py --host 192.168.1.21 services -v

# List available inputs (root level)
./naim_control_upnp.py --host 192.168.1.21 inputs-list

# List inputs with children (recursive)
./naim_control_upnp.py --host 192.168.1.21 inputs-list -r

# Browse a specific container (e.g., inputs container)
./naim_control_upnp.py --host 192.168.1.21 input-browse --object-id "0/0"

# Show current input/source
./naim_control_upnp.py --host 192.168.1.21 current-input

# Select an input by URI and start playback
./naim_control_upnp.py --host 192.168.1.21 input-select --uri "x-naim-input:digital1" --play

# Select an input by ObjectID
./naim_control_upnp.py --host 192.168.1.21 input-select --object-id "0/0/1" --play

# Show supported media protocols
./naim_control_upnp.py --host 192.168.1.21 protocol-info
```

**Note:** Input-specific settings like trim, alias, and enable/disable are only
available on newer Naim devices via the REST API. Legacy UPnP devices support
input discovery and selection, but not per-input configuration.

---

### Input Switching via n-Stream Protocol (WORKING!)

Input switching on legacy Naim devices uses the **n-Stream/BridgeCo protocol** on
**TCP port 15555**. This is the same protocol used by the official Naim app.

| Command | Options | Description |
|---------|---------|-------------|
| `nstream-set-input` | `--input <name>` | Switch to specified input |
| `nstream-get-input` | — | Get current input (raw response) |
| `nstream-input-up` | — | Cycle to next input |
| `nstream-input-down` | — | Cycle to previous input |
| `nstream-inputs` | — | List valid input names |

```bash
# Switch to Digital Input 2
./naim_control_upnp.py --host 192.168.1.21 nstream-set-input --input DIGITAL2

# Switch to UPnP streaming
./naim_control_upnp.py --host 192.168.1.21 nstream-set-input --input UPNP

# Cycle through inputs
./naim_control_upnp.py --host 192.168.1.21 nstream-input-up
./naim_control_upnp.py --host 192.168.1.21 nstream-input-down

# List all valid input names
./naim_control_upnp.py nstream-inputs
```

**Valid input names:** `UPNP`, `DIGITAL1`-`DIGITAL10`, `ANALOGUE1`-`ANALOGUE5`,
`CD`, `FM`, `DAB`, `IRADIO`, `USB`, `BLUETOOTH`, `AIRPLAY`, `SPOTIFY`, `TIDAL`

### IR Control (DOES NOT WORK - Use n-Stream Instead)

The `X_HtmlPageHandler` UPnP service has an IR control action, but **use n-Stream
commands instead** - they actually work.

| Command | Options | Description |
|---------|---------|-------------|
| `ir-send` | `--code <code>` | Send raw IR code (fails with error 401) |
| `ir-list` | — | List known IR codes (for reference only) |
| `input-switch` | `--input <name>` `--force` | Legacy IR attempt (fails) |
| `inputs-ir` | — | List inputs (reference only) |

**Note:** The IR commands are kept for historical reference but do not work.
Use the `nstream-*` commands for input control.

---

---

### UPnP Media Server Browsing

The UPnP script can also discover and browse UPnP Media Servers on your network
(such as NAS devices, Plex, MiniDLNA, etc.) and play content from them on your
Naim device.

| Command | Options | Description |
|---------|---------|-------------|
| `media-servers` | `[--timeout <seconds>]` | Discover UPnP Media Servers on the network |
| `server-browse` | `--server <ip>` `[--object-id <id>]` `[--start <n>]` `[--count <n>]` | Browse a Media Server's content |
| `server-search` | `--server <ip>` `--query <text>` | Search a Media Server for content |
| `server-play` | `--server <ip>` `--object-id <id>` | Play a track from a Media Server on a Naim renderer |

```bash
# Discover Media Servers on your network
./naim_control_upnp.py media-servers

# Browse root of a Media Server
./naim_control_upnp.py server-browse --server 192.168.1.100

# Browse a specific folder (use ObjectID from previous browse)
./naim_control_upnp.py server-browse --server 192.168.1.100 --object-id "64"

# Browse with pagination
./naim_control_upnp.py server-browse --server 192.168.1.100 --object-id "64" --start 30 --count 30

# Search for content
./naim_control_upnp.py server-search --server 192.168.1.100 --query "beethoven"

# Play a track from Media Server on your Naim device
./naim_control_upnp.py --host 192.168.1.21 server-play --server 192.168.1.100 --object-id "64$123"
```

**Workflow example - Play music from NAS:**
```bash
# 1. Find Media Servers
./naim_control_upnp.py media-servers
#    Found: 192.168.1.100 (Synology Media Server)

# 2. Browse root to find Music folder
./naim_control_upnp.py server-browse --server 192.168.1.100
#    [D] [1] Music
#    [D] [2] Photos
#    [D] [3] Videos

# 3. Browse Music folder
./naim_control_upnp.py server-browse --server 192.168.1.100 --object-id "1"
#    [D] [1$1] Jazz
#    [D] [1$2] Classical
#    [F] [1$100] Track.flac

# 4. Play a track on your Naim device
./naim_control_upnp.py --host 192.168.1.21 server-play --server 192.168.1.100 --object-id "1$100"
```

---

### Quick shell alias for legacy devices

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias naim-legacy='python3 /path/to/naim_control_upnp.py --host 192.168.1.21'
```

Then use:

```bash
naim-legacy play
naim-legacy volume-set --level 40
naim-legacy transport-info
```

---

## UPnP Troubleshooting

**Connection error / timeout**
- Verify the device IP address
- Legacy devices use port 8080 by default, but this may vary
- Run `discover` to find devices and their ports on your network

**SOAP fault errors**
- The device may not support the requested action
- Check `info` output to see which services are available
- Use `services -v` to see all available actions

**Device not found via discover**
- Ensure the device is powered on
- Some legacy devices take longer to respond to SSDP; increase `--timeout`

**ContentDirectory errors / No inputs found**
- Not all legacy devices expose inputs via ContentDirectory
- Use `services` to check if ContentDirectory service is available
- Try browsing different ObjectIDs: `input-browse --object-id 0/0`

---

## Input Features: REST API vs UPnP Comparison

Legacy Naim devices (UPnP) have more limited input control compared to newer
devices (REST API). Here's what's available on each platform:

| Feature | REST API (newer) | UPnP (legacy) |
|---------|------------------|---------------|
| List available inputs | ✅ `inputs-list` | ✅ `inputs-list` (via ContentDirectory) |
| Select/switch input | ✅ `input-select` | ✅ `input-select` (via SetAVTransportURI) |
| Show current input | ✅ `nowplaying` | ✅ `current-input` |
| Rename input (alias) | ✅ `input-rename` | ❌ Not supported |
| Enable/disable input | ✅ `input-disable` | ❌ Not supported |
| Input volume trim | ✅ `input-trim` | ❌ Not supported |
| Input sensitivity | ✅ `input-sensitivity` | ❌ Not supported |
| Unity gain mode | ✅ `input-unity-gain` | ❌ Not supported |

If you need advanced input settings on a legacy device, check if your device
also supports the REST API on port 15081 — some transitional models support both.

---

# n-Stream Commands (`naim_control_nstream.py`)

For legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, NAC-N 272, etc.) that
require the n-Stream/BridgeCo protocol for input switching and device settings.

**When to use this script:** Use `naim_control_nstream.py` when you need to:
- Switch inputs on legacy devices
- Control volume via the preamp
- Manage input settings (enable/disable, rename)
- Control Bluetooth settings
- Get device information (product, version, MAC)

## Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | *(required)* | IP address of the Naim device |
| `--port` | `15555` | n-Stream port (do not change) |
| `--timeout` | `5` | Response timeout in seconds |

---

## Command Reference

### Input Control

| Command | Options | Description |
|---------|---------|-------------|
| `inputs` | — | List all available inputs on the device |
| `set-input` | `--input <name>` | Switch to specified input |
| `get-input` | — | Get current input |
| `input-up` | — | Cycle to next input |
| `input-down` | — | Cycle to previous input |

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
```

**Valid input names:**
| Category | Inputs |
|----------|--------|
| Streaming | `UPNP`, `IRADIO`, `SPOTIFY`, `TIDAL`, `AIRPLAY`, `BLUETOOTH` |
| Digital | `DIGITAL1` through `DIGITAL10` |
| Analog | `ANALOGUE1` through `ANALOGUE5`, `PHONO` |
| Other | `USB`, `CD`, `FM`, `DAB`, `FRONT`, `MULTIROOM`, `IPOD` |
| HDMI | `HDMI1` through `HDMI5` |

---

### Input Management

| Command | Options | Description |
|---------|---------|-------------|
| `input-enable` | `--input <name>` | Enable an input |
| `input-disable` | `--input <name>` | Disable an input |
| `input-enabled` | `--input <name>` | Check if input is enabled |
| `input-rename` | `--input <name>` `--name "<display>"` | Set input display name |
| `input-name` | `--input <name>` | Get input display name |

```bash
# Enable/disable inputs
./naim_control_nstream.py --host 192.168.1.21 input-enable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-disable --input DIGITAL1
./naim_control_nstream.py --host 192.168.1.21 input-enabled --input DIGITAL1

# Rename inputs (set custom display name)
./naim_control_nstream.py --host 192.168.1.21 input-rename --input DIGITAL1 --name "TV Audio"
./naim_control_nstream.py --host 192.168.1.21 input-name --input DIGITAL1
```

---

### Volume and Preamp Control

| Command | Options | Description |
|---------|---------|-------------|
| `vol-up` | — | Volume up |
| `vol-down` | — | Volume down |
| `vol-set` | `--level <0-100>` | Set volume level |
| `mute` | — | Mute audio |
| `unmute` | — | Unmute audio |
| `preamp` | — | Get full preamp status (volume, mute, balance, input) |
| `max-vol-get` | — | Get maximum amplifier volume limit |
| `max-vol-set` | `--level <0-100>` | Set maximum amplifier volume limit |
| `headphone-max-vol` | — | Get maximum headphone volume limit |
| `balance-get` | — | Get balance setting |
| `balance-set` | `--level <n>` | Set balance (negative=left, 0=center, positive=right) |

```bash
# Volume control
./naim_control_nstream.py --host 192.168.1.21 vol-up
./naim_control_nstream.py --host 192.168.1.21 vol-down
./naim_control_nstream.py --host 192.168.1.21 vol-set --level 50
./naim_control_nstream.py --host 192.168.1.21 mute
./naim_control_nstream.py --host 192.168.1.21 unmute

# Get preamp status (shows volume, mute, balance, current input)
./naim_control_nstream.py --host 192.168.1.21 preamp

# Volume limits
./naim_control_nstream.py --host 192.168.1.21 max-vol-get
./naim_control_nstream.py --host 192.168.1.21 max-vol-set --level 80
./naim_control_nstream.py --host 192.168.1.21 headphone-max-vol

# Balance
./naim_control_nstream.py --host 192.168.1.21 balance-get
./naim_control_nstream.py --host 192.168.1.21 balance-set --level 0
```

---

### Playback Control

| Command | Description |
|---------|-------------|
| `play` | Start playback |
| `pause` | Toggle pause |
| `stop` | Stop playback |
| `next` | Skip to next track |
| `prev` | Skip to previous track |

```bash
./naim_control_nstream.py --host 192.168.1.21 play
./naim_control_nstream.py --host 192.168.1.21 pause
./naim_control_nstream.py --host 192.168.1.21 stop
./naim_control_nstream.py --host 192.168.1.21 next
./naim_control_nstream.py --host 192.168.1.21 prev
```

---

### Bluetooth Control

| Command | Options | Description |
|---------|---------|-------------|
| `bt-status` | — | Get Bluetooth status |
| `bt-pair` | — | Enter pairing mode |
| `bt-pair-exit` | — | Exit pairing mode |
| `bt-disconnect` | — | Disconnect current Bluetooth device |
| `bt-forget` | — | Disconnect and forget current device |
| `bt-name-get` | — | Get Bluetooth device name |
| `bt-name-set` | `--name "<name>"` | Set Bluetooth device name |

```bash
./naim_control_nstream.py --host 192.168.1.21 bt-status
./naim_control_nstream.py --host 192.168.1.21 bt-pair
./naim_control_nstream.py --host 192.168.1.21 bt-pair-exit
./naim_control_nstream.py --host 192.168.1.21 bt-disconnect
./naim_control_nstream.py --host 192.168.1.21 bt-forget
./naim_control_nstream.py --host 192.168.1.21 bt-name-get
./naim_control_nstream.py --host 192.168.1.21 bt-name-set --name "Living Room Naim"
```

---

### Display Wake-Up

When you use the Naim app, the device display lights up to confirm your action.
This is achieved via the SYNCDISP command. Use these commands to wake the display:

| Command | Description |
|---------|-------------|
| `sync-display-on` | Enable display sync - wakes the display (like app interaction) |
| `sync-display-off` | Disable display sync |

```bash
# Wake up the device display
./naim_control_nstream.py --host 192.168.1.21 sync-display-on

# Disable display sync
./naim_control_nstream.py --host 192.168.1.21 sync-display-off
```

**Note:** For newer devices using REST API, the equivalent command is:
```bash
./naim_control_rest.py --host 192.168.1.50 system-keepawake
```

---

### Device Settings

| Command | Options | Description |
|---------|---------|-------------|
| `room-name-get` | — | Get room/device name |
| `room-name-set` | `--name "<name>"` | Set room/device name |
| `standby` | — | Put device into standby |
| `wakeup` | — | Wake device from standby |

```bash
./naim_control_nstream.py --host 192.168.1.21 room-name-get
./naim_control_nstream.py --host 192.168.1.21 room-name-set --name "Living Room"
./naim_control_nstream.py --host 192.168.1.21 standby
./naim_control_nstream.py --host 192.168.1.21 wakeup
```

---

### Device Information

| Command | Description |
|---------|-------------|
| `product` | Get product type (e.g., SUPER_UNITI) |
| `version` | Get firmware version |
| `mac` | Get MAC address |

```bash
./naim_control_nstream.py --host 192.168.1.21 product
./naim_control_nstream.py --host 192.168.1.21 version
./naim_control_nstream.py --host 192.168.1.21 mac
```

---

### Quick shell alias for n-Stream

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias naim-input='python3 /path/to/naim_control_nstream.py --host 192.168.1.21'
```

Then use:

```bash
naim-input inputs
naim-input set-input --input DIGITAL2
naim-input preamp
naim-input max-vol-set --level 80
```

---

## n-Stream Troubleshooting

**Connection refused / timeout**
- Port 15555 must be accessible on the device
- Verify with: `nc -zv 192.168.1.21 15555`
- Device must be powered on (not in deep standby)

**"Unknown command" errors**
- Some commands are not available on all device models
- Check `product` output to verify your device type
- Older firmware may not support newer NVM commands

**Input not switching**
- Verify input name is valid with `inputs` command
- Input names are case-sensitive (use uppercase: `DIGITAL2`, not `digital2`)
- Some inputs may be disabled; use `input-enable` first

---

## Which Script Should I Use?

```
Is your device a newer model (Uniti Atom/Star/Nova, Mu-so 2nd gen)?
│
├─ YES → naim_control_rest.py (port 15081)
│        Full control: playback, volume, inputs, alarms, firmware, library
│        Real-time events: monitor --ussi nowplaying
│
└─ NO (Legacy: SuperUniti, NDS, NDX, UnitiQute, etc.)
   │
   ├─ Playback control (play/pause/stop/next/prev)?
   │  └─ naim_control_upnp.py (port 8080)
   │
   ├─ Volume control?
   │  └─ naim_control_upnp.py  OR  naim_control_nstream.py
   │
   ├─ Input switching?
   │  └─ naim_control_nstream.py (port 15555)  ← only option
   │
   ├─ Browse UPnP media servers / NAS?
   │  └─ naim_control_upnp.py (media-servers, server-browse, server-play)
   │
   └─ Device settings (max volume, room name, Bluetooth)?
      └─ naim_control_nstream.py (port 15555)

No hardware? → naim_emulator_legacy.py — emulates a legacy device locally
```

---

# Legacy Device Emulator (`naim_emulator_legacy.py`)

`naim_emulator_legacy.py` is a software replica of a legacy Naim device
(SuperUniti by default).  It implements the same three server-side services
as the real hardware so the CLI clients work against it unmodified.

**Why it exists:** legacy devices like the SuperUniti use a proprietary
BridgeCo RTOS that is hard to inspect.  The emulator lets you test all three
protocol clients, verify response parsing, and develop automation scripts
without needing the physical device powered on.

## Architecture note

The emulator mirrors the inferred server-side architecture of real legacy
devices:

- **One `DeviceState` object** is the single owner of all device state —
  just as the real device uses one NVM command interpreter with one state store.
- **Two protocol adapters** (`NStreamServer`, `UPnPServer`) share that state,
  so a volume change via UPnP is immediately reflected in n-Stream `GETPREAMP`.
- **SSDP** broadcasts `ssdp:alive` so the Naim app discovers the emulator
  automatically on the same LAN.

## Starting the emulator

```bash
# Default — SuperUniti profile, all services on standard ports
./naim_emulator_legacy.py --model superuniti

# Custom port (useful if real device is also on the same machine)
./naim_emulator_legacy.py --model superuniti --nstream-port 15556 --upnp-port 8081

# Load a custom profile from a JSON file
./naim_emulator_legacy.py --profile device_profiles/superuniti.json

# Disable UPnP / SSDP (n-Stream only)
./naim_emulator_legacy.py --model superuniti --no-upnp --no-ssdp
```

## Logging modes

| Flag | What is logged |
|------|----------------|
| *(none)* | CONNECT / CLOSE events, status panel at start/end |
| `--verbose` | All parsed NVM commands and responses; status panel after each session |
| `--debug` | Everything in verbose + raw hex bytes sent and received (very noisy) |

Example verbose output:

```
[14:12:01.042] [nStream] #a270 CONNECT  192.168.1.10
[14:12:01.043] [nStream] #a270 >> RequestAPIVersion OK (id=1)
[14:12:01.044] [nStream] #a270 >> GetBridgeCoAppVersions (app=1000) (id=2)
[14:12:01.044] [nStream] #a270 NVM<< '*NVM PRODUCT'
[14:12:01.044] [nStream] #a270 NVM>> '#NVM PRODUCT SUPER_UNITI'
[14:12:01.044] [nStream] #a270 NVM<< '*NVM VERSION'
[14:12:01.044] [nStream] #a270 NVM>> '#NVM VERSION 3.21.000 14171'
...
[14:12:04.051] [nStream] #a270 CLOSE    192.168.1.10
┌─ #a270 post-session ──────────────────────────────┐
│  Input    : UPNP  "UPnP"                          │
│  Volume   : 30                                    │
│  Playback : STOPPED   Repeat: OFF   Shuffle: OFF  │
│  Power    : On   Auto-standby: 20 min             │
│  Room     : Living Room                           │
│  BT       : INACTIVE   Name: "SuperUniti"         │
└───────────────────────────────────────────────────┘
```

## State persistence

The emulator saves all mutable device state after every change so it resumes
where it left off on restart.

| Option | Description |
|--------|-------------|
| *(default)* | Save to `device_state/<model>_state.json` |
| `--state-file <path>` | Custom path for the state file |
| `--no-persist` | Disable saving — start from profile defaults each run |

**What is persisted** (mutable, user-changeable state):

- Current input, volume, mute, balance
- Per-input names (after `input-rename`), enabled/disabled, trim
- Max amplifier volume, max headphone volume
- Standby state, auto-standby timeout
- Room name, Bluetooth name and security settings
- Playback transport state, repeat, shuffle

**What is NOT persisted** (fixed hardware identity, always from profile):

- Product code, firmware version, MAC address, serial number
- Input order and IDs
- Unsupported NVM command list

## Device profiles

Profiles are JSON files in `device_profiles/`.  The built-in `superuniti`
profile is also embedded in the script so `--model superuniti` works without
any extra files.

### Profile schema

```json
{
  "model":            "SuperUniti",
  "product_code":     "SUPER_UNITI",
  "firmware_version": "3.21.000 14171",
  "mac":              ["00", "1A", "D0", "AB", "CD", "EF"],
  "room_name":        "Living Room",
  "friendly_name":    "Naim SuperUniti",
  "serial":           "EMU000001",
  "initial_volume":   30,
  "max_amp_volume":   100,
  "max_head_volume":  75,
  "initial_input":    "UPNP",
  "auto_standby_period": 20,
  "unsupported_nvm": ["GETILLUM", "SETILLUM", "GETAUTOSTANDBYPERIOD",
                      "SETAUTOSTANDBYPERIOD", "GETSERIALNUM"],
  "inputs": [
    {"id": "FM",       "name": "FM",        "enabled": false},
    {"id": "UPNP",     "name": "UPnP",      "enabled": true},
    {"id": "DIGITAL1", "name": "Digital 1", "enabled": true},
    ...
  ]
}
```

Key fields:

| Field | Description |
|-------|-------------|
| `firmware_version` | Must be ≥ `"3.0.000"` for the Naim app to accept it |
| `unsupported_nvm` | NVM commands that return `ERROR: CMD 4` on this model |
| `inputs[].enabled` | Whether the input appears in the active input list |
| `initial_input` | Input selected at startup (overridden by state file after first run) |

### Adding a new model

1. Copy `device_profiles/superuniti.json` to e.g. `device_profiles/ndx.json`
2. Update `model`, `product_code`, `firmware_version`, and the `inputs` list
3. Add device-specific `unsupported_nvm` entries (check `PROTOCOLS_DETAILED.md`)
4. Run: `./naim_emulator_legacy.py --profile device_profiles/ndx.json`

## NVM commands supported

The emulator handles the full set of NVM commands sent by the Naim app during
startup and normal operation:

| Category | Commands |
|----------|----------|
| **Startup** | `PRODUCT`, `VERSION`, `GETSEDMPTYPE`, `GETSEDMPCAPS`, `GETINITIALINFO`, `SETUNSOLICITED`, `GETCHGTRACKERS` |
| **Inputs** | `GETINPUTBLK`, `GETINPUT`, `SETINPUT`, `INPUT+`, `INPUT-`, `SETINPUTENABLED`, `GETINPUTENABLED`, `SETINPUTNAME`, `GETINPUTNAME`, `GETINPUTTRIM`, `SETINPUTTRIM` |
| **Volume / preamp** | `VOL+`, `VOL-`, `SETRVOL`, `SETMUTE`, `GETPREAMP`, `GETAMPMAXVOL`, `SETAMPMAXVOL`, `GETHEADMAXVOL`, `GETBAL`, `SETBAL` |
| **Device info** | `GETMAC`, `GETROOMNAME`, `SETROOMNAME`, `GETLANG`, `GETBSLVER`, `GETSEDMPTYPE`, `GETSEDMPCAPS` |
| **Power** | `SETSTANDBY`, `GETSTANDBYSTATUS`, `GETILLUM`\*, `SETILLUM`\*, `GETAUTOSTANDBYPERIOD`\* |
| **Playback** | `PLAY`, `STOP`, `PAUSE`, `NEXTTRACK`, `PREVTRACK`, `FF`, `FR`, `REPEAT`, `RANDOM` |
| **Bluetooth** | `BTSTATUS`, `BTPAIR`, `BTDROPLINK`, `BTRECONNECT`, `GETBTNAME`, `SETBTNAME`, `GETBTSECURITY`, `SETBTSECURITY`, `GETBTAUTORECONNECT`, `SETBTAUTORECONNECT`, `GETBTAUTOPLAY`, `SETBTAUTOPLAY` |
| **Alarms/radio** | `GETIRADIOHIDDENROWS`, `GETBLASTCAPS`, `GETDATETIME`, `ALARMSTATE` |
| **BC layer** | `RequestAPIVersion`, `GetBridgeCoAppVersions`, `SetHeartbeatTimeout`, `Ping` |

\* Returns `ERROR: CMD 4` for SuperUniti (listed in `unsupported_nvm`); supported
on models that include these in their profile.

## Testing workflow

```bash
# Terminal 1 — start emulator
./naim_emulator_legacy.py --model superuniti --verbose

# Terminal 2 — run CLI clients against the emulator
./naim_control_nstream.py --host 127.0.0.1 inputs
./naim_control_nstream.py --host 127.0.0.1 set-input --input DIGITAL1
./naim_control_nstream.py --host 127.0.0.1 input-rename --input DIGITAL1 --name "TV Audio"
./naim_control_nstream.py --host 127.0.0.1 vol-set --level 55
./naim_control_nstream.py --host 127.0.0.1 preamp
./naim_control_upnp.py    --host 127.0.0.1 volume-get
./naim_control_upnp.py    --host 127.0.0.1 transport-info

# Stop and restart emulator — state is reloaded from device_state/
./naim_emulator_legacy.py --model superuniti --verbose
# Status panel shows: Input=DIGITAL1 "TV Audio", Volume=55
```
