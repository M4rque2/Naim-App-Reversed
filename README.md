# naim-streamer-control

An unofficial Python command-line tool for controlling Naim Audio streaming
devices over your local network, reverse-engineered from the official Naim
Android application.

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

## What This Is

Naim Audio devices (Uniti series, Mu-so, NAC-N, ND series, etc.) expose an
undocumented HTTP REST API on port `15081` of your local network. This API is
what the official Naim app uses to control the device.

This project reverse-engineered that API from the Naim Android APK and
reimplements it as a single-file Python CLI tool with no external dependencies,
allowing you to control your Naim streamer from any terminal, script, or
automation system.

## What's Included

| File | Description |
|------|-------------|
| `naim_control.py` | The CLI tool — all commands in one file |
| `PROTOCOL.md` | Full technical protocol analysis (HTTP, JSON models, enums) |
| `USAGE.md` | Complete usage guide with examples for every command |

## Requirements

- Python 3.6 or later (standard library only — no `pip install` needed)
- A Naim streaming device on your local network
- The device's IP address

## Quick Start

```bash
# Check what is currently playing
python3 naim_control.py --host 192.168.1.50 nowplaying

# Play / pause / skip
python3 naim_control.py --host 192.168.1.50 play
python3 naim_control.py --host 192.168.1.50 pause
python3 naim_control.py --host 192.168.1.50 next

# Set volume to 40
python3 naim_control.py --host 192.168.1.50 volume-set --level 40

# Switch input to Tidal
python3 naim_control.py --host 192.168.1.50 input-select --ussi inputs/tidal

# List all available inputs
python3 naim_control.py --host 192.168.1.50 inputs-list
```

## Supported Commands

The tool covers every API endpoint found in the decompiled app:

| Category | Commands |
|----------|----------|
| Playback | `play` `pause` `stop` `resume` `next` `prev` `toggle` `seek` `repeat` `shuffle` `nowplaying` |
| Volume | `volume-set` `mute` `unmute` `balance` `volume-mode` `levels-get` `levels-room` `levels-group` `levels-bluetooth` |
| Inputs | `inputs-list` `input-select` `input-play` `input-resume` `input-rename` `input-disable` `input-trim` `input-sensitivity` `input-unity-gain` |
| Outputs | `outputs-list` `output-enabled` `output-max-volume` `loudness` `loudness-enabled` `room-position` `dsd-mode` |
| Streaming | Qobuz login/logout/quality · Tidal OAuth login/logout · Spotify bitrate/gain/presets |
| Radio | iRadio browse/play · FM/DAB scan/step · User station add/delete |
| Bluetooth | Pair · drop · forget · auto-pair |
| Play Queue | Get · clear · move · set-current |
| Favourites | List · play · delete · preset assign/move |
| Multiroom | Get state · add/remove from group |
| CD | Info · eject · play · insert-action |
| Alarms | List · create · enable/disable · delete |
| Sleep Timer | Start · cancel |
| Network | Get config · WiFi setup · DHCP/static IP · hostname |
| System | Info · reboot · keep-awake · usage · date/time |
| Firmware | Status · check for updates · start update |
| Library | Browse tracks/albums/artists · play · queue |

See [`USAGE.md`](USAGE.md) for the full reference with options and examples.

## Protocol Overview

The API is a plain HTTP REST interface on port `15081`. There is no
authentication — any device on the same network can send commands.

- **Transport:** HTTP/1.1, port `15081`, no HTTPS
- **Format:** JSON (all requests and responses)
- **Actions:** `GET /endpoint?cmd=<action>` — e.g. `?cmd=play`
- **Settings:** `PUT /endpoint?key=<value>` — e.g. `?volume=40`
- **Real-time:** Server-Sent Events (SSE) for push state updates

See [`PROTOCOL.md`](PROTOCOL.md) for the complete technical analysis including
all JSON schemas, enumeration values, USSI addressing, and SSE documentation.

## Shell Alias

To avoid typing `--host` on every command:

```bash
# Add to ~/.bashrc or ~/.zshrc
alias naim='python3 /path/to/naim_control.py --host 192.168.1.50'
```

Then:
```bash
naim nowplaying
naim volume-set --level 30
naim input-select --ussi inputs/spotify
```

## Tested Devices

This tool was developed through static analysis of the Naim Android app only.
It has **not** been systematically tested against real hardware. Devices that
may be compatible include:

- Naim Uniti series (Atom, Star, Nova, Core)
- Naim Mu-so and Mu-so Qb series
- Naim ND series network players
- Naim NAC-N series preamplifiers

Not every command applies to every model. Commands for hardware your device
does not have (e.g. CD commands on a device without a CD drive) will return
HTTP 404 and the tool will exit with an error message.

## Disclaimer

This project is not affiliated with, endorsed by, or connected to
**Naim Audio Limited** in any way. All product names, trademarks, and
registered trademarks are the property of their respective owners.

The API was discovered through lawful reverse engineering of an Android
application for the purpose of interoperability. No proprietary code from
the original application is included in this repository.

**THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR CONTRIBUTORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR
IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.**

Use at your own risk.

## License

[MIT](LICENSE)
