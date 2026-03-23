# Naim Streamer Control CLI — Usage Guide

Reverse-engineered from the Naim Android application. Controls Naim streaming
devices (Uniti, Mu-so, NAC-N, SuperUniti, NDS, NDX, etc.) over your local network.

There are **two separate scripts** for the two protocol generations:

| Script | Protocol | Devices | Default Port |
|--------|----------|---------|-------------|
| `naim_control_rest.py` | HTTP REST API | Newer devices (Uniti series, Mu-so 2nd gen, etc.) | 15081 |
| `naim_control_upnp.py` | UPnP/DLNA SOAP | Legacy devices (SuperUniti, NDS, NDX, UnitiQute, etc.) | 8080 |

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

**Device not found via discover**
- Ensure the device is powered on
- Some legacy devices take longer to respond to SSDP; increase `--timeout`
