#!/usr/bin/env python3
"""
Naim Streamer Control CLI — n-Stream/BridgeCo Protocol
Reverse-engineered from the Naim App Android application.
Protocol: n-Stream/BridgeCo TCP protocol on port 15555

For legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, NAC-N 272, etc.)
that use the proprietary BridgeCo platform for input switching and device control.

This protocol is required for:
- Input/source switching (not available via UPnP)
- Preamp control (volume via NVM commands)
- Device information queries
"""

import argparse
import base64
import socket
import sys

NSTREAM_PORT = 15555  # n-Stream/BridgeCo protocol port

# Valid input names for n-Stream protocol
NSTREAM_INPUTS = {
    # Streaming inputs
    "upnp": "UPNP",
    "iradio": "IRADIO",
    "spotify": "SPOTIFY",
    "tidal": "TIDAL",
    "airplay": "AIRPLAY",
    "bluetooth": "BLUETOOTH",
    # Digital inputs
    "digital1": "DIGITAL1",
    "digital2": "DIGITAL2",
    "digital3": "DIGITAL3",
    "digital4": "DIGITAL4",
    "digital5": "DIGITAL5",
    "digital6": "DIGITAL6",
    "digital7": "DIGITAL7",
    "digital8": "DIGITAL8",
    "digital9": "DIGITAL9",
    "digital10": "DIGITAL10",
    # Analog inputs
    "analog1": "ANALOGUE1",
    "analog2": "ANALOGUE2",
    "analogue1": "ANALOGUE1",
    "analogue2": "ANALOGUE2",
    "analogue3": "ANALOGUE3",
    "analogue4": "ANALOGUE4",
    "analogue5": "ANALOGUE5",
    "phono": "PHONO",
    # Other inputs
    "usb": "USB",
    "cd": "CD",
    "fm": "FM",
    "dab": "DAB",
    "front": "FRONT",
    "multiroom": "MULTIROOM",
    "ipod": "IPOD",
    # HDMI inputs (if available)
    "hdmi1": "HDMI1",
    "hdmi2": "HDMI2",
    "hdmi3": "HDMI3",
    "hdmi4": "HDMI4",
    "hdmi5": "HDMI5",
}


class NStreamError(Exception):
    """Raised when an n-Stream protocol operation fails."""
    pass


class NStreamConnection:
    """Manages TCP connection to Naim device for n-Stream/BridgeCo protocol.

    The protocol has two layers:
    1. BC (BridgeCo) layer - handles connection setup and API version negotiation
    2. Tunnel layer - carries NVM commands wrapped in Base64

    Initialization sequence:
    1. Connect to port 15555
    2. Send RequestAPIVersion (BC layer) with module="naim", version="1"
    3. Send *NVM SETUNSOLICITED ON\r (tunnel) to enable command responses
    4. Now regular NVM commands will work
    """

    def __init__(self, host, port=NSTREAM_PORT, timeout=5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.message_id = 0
        self.initialized = False

    def connect(self):
        """Establish TCP connection to the device."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect((self.host, self.port))
        except socket.error as e:
            raise NStreamError(f"Failed to connect to {self.host}:{self.port}: {e}")

    def close(self):
        """Close the connection."""
        if self.sock:
            try:
                # Send disconnect command
                disconnect_xml = '<command id="0" name="Disconnect"/>'
                self.sock.sendall(disconnect_xml.encode('utf-8'))
            except:
                pass
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
        self.initialized = False

    def _get_message_id(self):
        """Get next message ID."""
        self.message_id += 1
        return self.message_id

    def _build_bc_command(self, name, params=None):
        """Build a BC-layer XML command (not tunnel wrapped).

        Args:
            name: Command name (e.g., "RequestAPIVersion")
            params: Dict of parameter name -> (value, value_type) where value_type is "string", "int", etc.

        Returns:
            XML string
        """
        msg_id = self._get_message_id()
        items = ""
        if params:
            for param_name, (value, value_type) in params.items():
                items += f"<item><name>{param_name}</name><{value_type}>{value}</{value_type}></item>"

        xml = f"<command><name>{name}</name><id>{msg_id}</id>"
        if items:
            xml += f"<map>{items}</map>"
        xml += "</command>"
        return xml

    def _build_tunnel_command(self, nvm_command):
        """Build XML command for TunnelToHost with Base64-encoded NVM command."""
        msg_id = self._get_message_id()
        # Encode the NVM command in Base64
        cmd_bytes = nvm_command.encode('utf-8')
        cmd_b64 = base64.b64encode(cmd_bytes).decode('ascii')

        # Build XML
        xml = f'<command><name>TunnelToHost</name><id>{msg_id}</id><map><item><name>data</name><base64>{cmd_b64}</base64></item></map></command>'
        return xml

    def _read_response(self, timeout=None):
        """Read response from socket until we have a complete XML response."""
        old_timeout = self.sock.gettimeout()
        if timeout:
            self.sock.settimeout(timeout)
        try:
            response = b""
            while True:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                response += chunk
                # Check if we have a complete response (ends with </command> or </reply>)
                if b"</command>" in response or b"</reply>" in response:
                    break
            return response.decode('utf-8', errors='replace')
        finally:
            self.sock.settimeout(old_timeout)

    def _read_all_responses(self, timeout=None, max_wait=5):
        """Read all available responses from socket.

        Used for commands like GETINPUTBLK that return multiple async events.
        Returns a list of response strings.
        """
        import time
        old_timeout = self.sock.gettimeout()
        self.sock.settimeout(0.5)  # Short timeout for quick reads
        responses = []
        buffer = b""
        start_time = time.time()

        try:
            while time.time() - start_time < max_wait:
                try:
                    chunk = self.sock.recv(4096)
                    if chunk:
                        buffer += chunk
                except socket.timeout:
                    # No more data available right now
                    if buffer:
                        # We have data, wait a bit more for complete messages
                        pass
                    else:
                        # No data at all, give up after initial timeout
                        if time.time() - start_time > (timeout or 2):
                            break
                    continue
                except socket.error:
                    break

            # Parse all complete XML messages from buffer
            decoded = buffer.decode('utf-8', errors='replace')
            responses = self._extract_xml_messages(decoded)
            return responses
        finally:
            self.sock.settimeout(old_timeout)

    def _extract_xml_messages(self, data):
        """Extract all complete XML messages from a data buffer."""
        import re
        messages = []

        # Find all <event ...>...</event> blocks
        event_pattern = re.compile(r'<event[^>]*>.*?</event>', re.DOTALL)
        for match in event_pattern.finditer(data):
            messages.append(match.group())

        # Find all <reply ...>...</reply> blocks
        reply_pattern = re.compile(r'<reply[^>]*>.*?</reply>', re.DOTALL)
        for match in reply_pattern.finditer(data):
            messages.append(match.group())

        return messages

    def _send_raw(self, xml_cmd):
        """Send raw XML command to the device."""
        try:
            self.sock.sendall(xml_cmd.encode('utf-8'))
        except socket.error as e:
            raise NStreamError(f"Failed to send command: {e}")

    def initialize(self):
        """Initialize the connection with BC-layer handshake.

        This must be called before sending NVM commands.
        Sends RequestAPIVersion and enables unsolicited messages.
        """
        if not self.sock:
            self.connect()

        # Step 1: Send RequestAPIVersion (BC layer)
        # This is required to set up the API version with the device
        api_cmd = self._build_bc_command("RequestAPIVersion", {
            "module": ("naim", "string"),
            "version": ("1", "string"),
        })
        self._send_raw(api_cmd)

        # Read response (may timeout if device doesn't respond)
        response = self._read_response(timeout=2)
        # We don't strictly need to parse the response, just need to send it

        # Step 2: Enable unsolicited messages via tunnel
        # This is critical - without this, the device won't respond to input commands
        unsolicited_cmd = self._build_tunnel_command("*NVM SETUNSOLICITED ON\r")
        self._send_raw(unsolicited_cmd)
        self._read_response(timeout=2)

        self.initialized = True

    def send_command(self, nvm_command, expect_response=True):
        """Send an NVM command and optionally wait for response.

        Args:
            nvm_command: Command like "*NVM SETINPUT DIGITAL2\r"
            expect_response: Whether to wait for and return response

        Returns:
            Response string if expect_response, else None
        """
        if not self.sock:
            self.connect()

        # Ensure connection is initialized
        if not self.initialized:
            self.initialize()

        xml_cmd = self._build_tunnel_command(nvm_command)
        self._send_raw(xml_cmd)

        if not expect_response:
            return None

        # Read response
        try:
            return self._read_response(timeout=self.timeout)
        except socket.timeout:
            return None
        except socket.error as e:
            raise NStreamError(f"Failed to read response: {e}")


def _parse_tunnel_data(xml_message):
    """Parse a TunnelFromHost event and extract the base64-decoded NVM response.

    The device sends responses as:
    <event name="TunnelFromHost">
        <map>
            <item name="data">
                <base64>...</base64>
            </item>
        </map>
    </event>

    Returns the decoded data string, or None if not a TunnelFromHost event.
    """
    import re
    if 'TunnelFromHost' not in xml_message:
        return None

    # Extract base64 data
    b64_match = re.search(r'<base64>([^<]+)</base64>', xml_message)
    if not b64_match:
        return None

    try:
        decoded = base64.b64decode(b64_match.group(1)).decode('utf-8', errors='replace')
        return decoded.strip()
    except Exception:
        return None


def _parse_nvm_response(data):
    """Parse an NVM response line into command name and values.

    NVM responses look like:
    #NVM COMMAND value1 value2 value3 "quoted value"
    or
    #NVM COMMAND OK
    """
    if not data or not data.startswith('#NVM '):
        return None, []

    # Remove the #NVM prefix
    rest = data[5:].strip()

    # Split into command and values, handling quoted strings
    import shlex
    try:
        parts = shlex.split(rest)
    except ValueError:
        # Fallback to simple split if shlex fails
        parts = rest.split()

    if not parts:
        return None, []

    command = parts[0]
    values = parts[1:] if len(parts) > 1 else []

    return command, values


def nstream_send_and_receive(host, nvm_command, port=NSTREAM_PORT, verbose=False, timeout=2, max_wait=3):
    """Send an NVM command and collect all async responses.

    Returns a list of decoded NVM response lines (without #NVM prefix parsing).
    """
    conn = NStreamConnection(host, port)

    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        # Ensure command ends with \r
        if not nvm_command.endswith('\r'):
            nvm_command += '\r'

        if verbose:
            print(f"  Sending: {repr(nvm_command)}")
        xml_cmd = conn._build_tunnel_command(nvm_command)
        conn._send_raw(xml_cmd)

        # Read all async responses
        if verbose:
            print("  Waiting for responses...")
        responses = conn._read_all_responses(timeout=timeout, max_wait=max_wait)

        if verbose:
            print(f"  Received {len(responses)} XML messages")

        # Collect all decoded response lines
        all_lines = []
        for resp in responses:
            data = _parse_tunnel_data(resp)
            if not data:
                continue

            # Split by lines (responses use \r\n)
            lines = data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            for line in lines:
                line = line.strip()
                if line and line.startswith('#NVM '):
                    all_lines.append(line)

        return all_lines

    finally:
        conn.close()


def nstream_get_inputs_list(host, port=NSTREAM_PORT, verbose=False):
    """Query the device for available inputs using GETINPUTBLK command.

    Returns a list of dicts with:
    - id: Input ID (e.g., "UPNP", "DIGITAL1")
    - name: Display name
    - active: Whether the input is currently active/enabled
    - index: Input index number
    """
    conn = NStreamConnection(host, port)
    inputs = []

    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        if verbose:
            print("  Sending: *NVM GETINPUTBLK\\r")
        xml_cmd = conn._build_tunnel_command("*NVM GETINPUTBLK\r")
        conn._send_raw(xml_cmd)

        # Read all async responses
        if verbose:
            print("  Waiting for input list responses...")
        responses = conn._read_all_responses(timeout=2, max_wait=3)

        if verbose:
            print(f"  Received {len(responses)} XML messages")

        # Each TunnelFromHost event may contain multiple lines of NVM responses
        for resp in responses:
            data = _parse_tunnel_data(resp)
            if not data:
                continue

            if verbose:
                print(f"  Decoded data: {data[:100]}...")

            # Split by lines (responses use \r\n)
            lines = data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            for line in lines:
                line = line.strip()
                if not line.startswith('#NVM GETINPUTBLK'):
                    continue

                # Parse: #NVM GETINPUTBLK <index> <total> <active> <ID> "<Name>"
                # Example: #NVM GETINPUTBLK 1 18 0 FM "FM"
                cmd, values = _parse_nvm_response(line)
                if cmd == 'GETINPUTBLK' and len(values) >= 5:
                    try:
                        inputs.append({
                            'index': int(values[0]),
                            'total': int(values[1]),
                            'active': values[2] == '1',
                            'id': values[3],
                            'name': values[4],
                        })
                    except (ValueError, IndexError):
                        if verbose:
                            print(f"  Warning: Could not parse input info: {values}")

        # Sort by index
        inputs.sort(key=lambda x: x.get('index', 0))
        return inputs

    finally:
        conn.close()


def nstream_set_input(host, input_name, port=NSTREAM_PORT, verbose=False):
    """Switch input using n-Stream protocol.

    Args:
        host: Device IP address
        input_name: Input name (e.g., "DIGITAL2", "UPNP")
        port: n-Stream port (default 15555)
        verbose: Print debug information

    Returns:
        Response string if successful, raises NStreamError otherwise
    """
    conn = NStreamConnection(host, port)
    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection (RequestAPIVersion + SETUNSOLICITED)...")
        conn.initialize()

        if verbose:
            print(f"  Sending: *NVM SETINPUT {input_name}\\r")
        cmd = f"*NVM SETINPUT {input_name}\r"
        response = conn.send_command(cmd, expect_response=True)

        if verbose and response:
            print(f"  Response: {response[:200]}...")

        return response
    finally:
        if verbose:
            print("  Closing connection...")
        conn.close()


def nstream_get_input(host, port=NSTREAM_PORT, verbose=False):
    """Get current input using n-Stream protocol.

    Args:
        host: Device IP address
        port: n-Stream port (default 15555)
        verbose: Print debug information

    Returns:
        Response string containing input name
    """
    conn = NStreamConnection(host, port)
    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        if verbose:
            print("  Sending: *NVM GETINPUT\\r")
        cmd = "*NVM GETINPUT\r"
        response = conn.send_command(cmd)
        return response
    finally:
        conn.close()


def nstream_input_up(host, port=NSTREAM_PORT, verbose=False):
    """Cycle to next input using n-Stream protocol."""
    conn = NStreamConnection(host, port)
    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        if verbose:
            print("  Sending: *NVM INPUT+\\r")
        response = conn.send_command("*NVM INPUT+\r")
        return response
    finally:
        conn.close()


def nstream_input_down(host, port=NSTREAM_PORT, verbose=False):
    """Cycle to previous input using n-Stream protocol."""
    conn = NStreamConnection(host, port)
    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        if verbose:
            print("  Sending: *NVM INPUT-\\r")
        response = conn.send_command("*NVM INPUT-\r")
        return response
    finally:
        conn.close()


def nstream_send_raw(host, nvm_command, port=NSTREAM_PORT, verbose=False):
    """Send a raw NVM command using n-Stream protocol.

    Args:
        host: Device IP address
        nvm_command: Raw NVM command (e.g., "*NVM PRODUCT\r")
        port: n-Stream port (default 15555)
        verbose: Print debug information

    Returns:
        Response string
    """
    conn = NStreamConnection(host, port)
    try:
        if verbose:
            print(f"  Connecting to {host}:{port}...")
        conn.connect()

        if verbose:
            print("  Initializing connection...")
        conn.initialize()

        if verbose:
            print(f"  Sending: {repr(nvm_command)}")
        response = conn.send_command(nvm_command)
        return response
    finally:
        conn.close()


# ─────────────────────────────────────────────
# CLI COMMAND HANDLERS
# ─────────────────────────────────────────────

def cmd_set_input(args):
    """Switch input using n-Stream protocol (port 15555)."""
    input_name = args.input.upper()

    # Check if it's an alias
    input_lower = args.input.lower()
    if input_lower in NSTREAM_INPUTS:
        input_name = NSTREAM_INPUTS[input_lower]

    verbose = getattr(args, 'verbose', False)

    print(f"Switching to input: {input_name}")
    print(f"Using n-Stream/BridgeCo protocol on port {NSTREAM_PORT}...")
    print()

    try:
        response = nstream_set_input(args.host, input_name, NSTREAM_PORT, verbose=verbose)
        print()
        print(f"Command sent successfully!")
        print(f"Input should now be: {input_name}")

        if response and verbose:
            print(f"\nRaw response:\n{response}")

    except NStreamError as e:
        print(f"\nFailed to switch input: {e}")
        print("\nTroubleshooting:")
        print("  - Ensure the device is powered on and not in standby")
        print("  - Check that port 15555 is not blocked by firewall")
        print("  - Try 'inputs' to see valid input names")
        print("  - The input may not be available on your device")
        print("  - Try using --verbose flag to see detailed debug output")
        sys.exit(1)


def cmd_get_input(args):
    """Get current input using n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Getting current input via n-Stream protocol (port {NSTREAM_PORT})...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM GETINPUT", NSTREAM_PORT, verbose=verbose)
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'GETINPUT' and values:
                print(f"Current Input: {values[0]}")
            elif cmd != 'SETUNSOLICITED':
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get input: {e}")
        sys.exit(1)


def cmd_input_up(args):
    """Cycle to next input using n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Cycling to next input via n-Stream protocol (port {NSTREAM_PORT})...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM INPUT+", NSTREAM_PORT, verbose=verbose)
        print("Command sent successfully!")
        print("Input should have cycled to next.")
        if verbose:
            for line in responses:
                print(f"  {line}")
    except NStreamError as e:
        print(f"Failed to cycle input: {e}")
        sys.exit(1)


def cmd_input_down(args):
    """Cycle to previous input using n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Cycling to previous input via n-Stream protocol (port {NSTREAM_PORT})...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM INPUT-", NSTREAM_PORT, verbose=verbose)
        print("Command sent successfully!")
        print("Input should have cycled to previous.")
        if verbose:
            for line in responses:
                print(f"  {line}")
    except NStreamError as e:
        print(f"Failed to cycle input: {e}")
        sys.exit(1)


def cmd_input_enable(args):
    """Enable an input via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    input_id = args.input.upper()

    print(f"Enabling input '{input_id}' via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(
            args.host, f"*NVM SETINPUTENABLED {input_id} ON",
            NSTREAM_PORT, verbose=verbose
        )
        success = False
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'SETINPUTENABLED':
                if values and values[0] == 'OK':
                    print(f"Input '{input_id}' enabled successfully")
                    success = True
                elif values:
                    print(f"Response: {' '.join(values)}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
        elif not success:
            print(f"Command sent for input '{input_id}'")
    except NStreamError as e:
        print(f"Failed to enable input: {e}")
        sys.exit(1)


def cmd_input_disable(args):
    """Disable an input via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    input_id = args.input.upper()

    print(f"Disabling input '{input_id}' via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(
            args.host, f"*NVM SETINPUTENABLED {input_id} OFF",
            NSTREAM_PORT, verbose=verbose
        )
        success = False
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'SETINPUTENABLED':
                if values and values[0] == 'OK':
                    print(f"Input '{input_id}' disabled successfully")
                    success = True
                elif values:
                    print(f"Response: {' '.join(values)}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
        elif not success:
            print(f"Command sent for input '{input_id}'")
    except NStreamError as e:
        print(f"Failed to disable input: {e}")
        sys.exit(1)


def cmd_input_get_enabled(args):
    """Check if an input is enabled via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    input_id = args.input.upper()

    print(f"Checking if input '{input_id}' is enabled...")
    print()

    try:
        responses = nstream_send_and_receive(
            args.host, f"*NVM GETINPUTENABLED {input_id}",
            NSTREAM_PORT, verbose=verbose
        )
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'GETINPUTENABLED' and len(values) >= 2:
                enabled = "Yes" if values[1] == 'ON' else "No"
                print(f"Input '{values[0]}' enabled: {enabled}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to check input status: {e}")
        sys.exit(1)


def cmd_input_rename(args):
    """Rename/alias an input via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    input_id = args.input.upper()
    new_name = args.name

    print(f"Renaming input '{input_id}' to '{new_name}'...")
    print()

    try:
        # Escape quotes in the name
        escaped_name = new_name.replace('"', '\\"')
        responses = nstream_send_and_receive(
            args.host, f'*NVM SETINPUTNAME {input_id} "{escaped_name}"',
            NSTREAM_PORT, verbose=verbose
        )
        success = False
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'SETINPUTNAME':
                if values and values[0] == 'OK':
                    print(f"Input '{input_id}' renamed to '{new_name}' successfully")
                    success = True
                elif values:
                    print(f"Response: {' '.join(values)}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
        elif not success:
            print(f"Command sent for input '{input_id}'")
    except NStreamError as e:
        print(f"Failed to rename input: {e}")
        sys.exit(1)


def cmd_input_get_name(args):
    """Get the name/alias of an input via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    input_id = args.input.upper()

    print(f"Getting name of input '{input_id}'...")
    print()

    try:
        responses = nstream_send_and_receive(
            args.host, f"*NVM GETINPUTNAME {input_id}",
            NSTREAM_PORT, verbose=verbose
        )
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'GETINPUTNAME' and len(values) >= 2:
                print(f"Input '{values[0]}' name: {values[1]}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get input name: {e}")
        sys.exit(1)


def cmd_list_inputs(args):
    """List available inputs on the device by querying via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)

    # If no host specified, show general info about input names
    if not args.host:
        print("=" * 70)
        print("n-Stream Protocol - Common Input Names")
        print("=" * 70)
        print()
        print("To query available inputs from a specific device, use:")
        print("  ./naim_control_nstream.py --host <IP> inputs")
        print()
        print("Common input IDs used by Naim devices:")
        print()

        categories = {
            "Streaming": ["UPNP", "IRADIO", "SPOTIFY", "TIDAL", "AIRPLAY", "BLUETOOTH"],
            "Digital": ["DIGITAL1", "DIGITAL2", "DIGITAL3", "DIGITAL4", "DIGITAL5"],
            "Analog": ["ANALOGUE1", "ANALOGUE2", "PHONO"],
            "Other": ["USB", "CD", "FM", "DAB", "FRONT", "MULTIROOM", "IPOD"],
        }

        for category, inputs in categories.items():
            print(f"  {category}: {', '.join(inputs)}")
        print()
        return

    print(f"Querying available inputs from {args.host}...")
    print()

    try:
        inputs = nstream_get_inputs_list(args.host, NSTREAM_PORT, verbose=verbose)

        if not inputs:
            print("No inputs returned from device.")
            print("The device may not support GETINPUTBLK command.")
            return

        print(f"Found {len(inputs)} input(s) on this device:\n")
        print(f"{'#':<4} {'ID':<15} {'Name':<20} {'Active'}")
        print("-" * 50)

        for inp in inputs:
            active_str = "Yes" if inp.get('active') else "No"
            idx = inp.get('index', '?')
            input_id = inp.get('id', '?')
            name = inp.get('name', '?')
            print(f"{idx:<4} {input_id:<15} {name:<20} {active_str}")

        print()
        print("Usage:")
        print(f"  ./naim_control_nstream.py --host {args.host} set-input --input <ID>")
        print()
        print("Example:")
        if inputs:
            example_id = inputs[0].get('id', 'UPNP')
            print(f"  ./naim_control_nstream.py --host {args.host} set-input --input {example_id}")

    except NStreamError as e:
        print(f"Failed to query inputs: {e}")
        sys.exit(1)


def cmd_raw(args):
    """Send a raw NVM command."""
    verbose = getattr(args, 'verbose', False)

    # Ensure command ends with \r
    nvm_command = args.command
    if not nvm_command.endswith('\r'):
        nvm_command += '\r'

    print(f"Sending raw NVM command: {repr(nvm_command)}")
    print()

    try:
        responses = nstream_send_and_receive(args.host, nvm_command, NSTREAM_PORT, verbose=verbose)
        if responses:
            print("Response(s):")
            for line in responses:
                print(f"  {line}")
        else:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to send command: {e}")
        sys.exit(1)


def cmd_product(args):
    """Get product type via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Getting product info via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM PRODUCT", NSTREAM_PORT, verbose=verbose)
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'PRODUCT' and values:
                print(f"Product: {values[0]}")
            elif cmd != 'SETUNSOLICITED':
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get product info: {e}")
        sys.exit(1)


def cmd_version(args):
    """Get firmware version via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Getting firmware version via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM VERSION", NSTREAM_PORT, verbose=verbose)
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'VERSION' and values:
                print(f"Firmware Version: {' '.join(values)}")
            elif cmd != 'SETUNSOLICITED':
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get version: {e}")
        sys.exit(1)


def cmd_mac(args):
    """Get MAC address via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Getting MAC address via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM GETMAC", NSTREAM_PORT, verbose=verbose)
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'GETMAC' and values:
                # MAC is returned as 6 separate hex bytes
                mac_addr = ':'.join(values[:6]) if len(values) >= 6 else ' '.join(values)
                print(f"MAC Address: {mac_addr}")
            elif cmd not in ('SETUNSOLICITED', 'ALARMSTATE'):
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get MAC address: {e}")
        sys.exit(1)


def cmd_preamp(args):
    """Get preamp status via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Getting preamp status via n-Stream protocol...")
    print()

    try:
        responses = nstream_send_and_receive(args.host, "*NVM GETPREAMP", NSTREAM_PORT, verbose=verbose)
        for line in responses:
            cmd, values = _parse_nvm_response(line)
            if cmd == 'PREAMP' and values:
                # Parse preamp info: volume, mute, balance, input, ...
                # Format: PREAMP <vol> <mute> <balance> <input> <arg5> <arg6> <arg7> <arg8> <displayName> <arg10>
                print("Preamp Status:")
                if len(values) >= 1:
                    print(f"  Volume: {values[0]}")
                if len(values) >= 2:
                    mute_status = "Yes" if values[1] == '1' else "No"
                    print(f"  Muted: {mute_status}")
                if len(values) >= 3:
                    print(f"  Balance: {values[2]}")
                if len(values) >= 4:
                    print(f"  Input ID: {values[3]}")
                if len(values) >= 9:
                    print(f"  Input Name: {values[8]}")
                if verbose:
                    print(f"  Raw values: {values}")
            elif cmd != 'SETUNSOLICITED':
                print(f"  {line}")
        if not responses:
            print("No response received (timeout)")
    except NStreamError as e:
        print(f"Failed to get preamp status: {e}")
        sys.exit(1)


def cmd_volume_up(args):
    """Increase volume via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Increasing volume via n-Stream protocol...")

    try:
        responses = nstream_send_and_receive(args.host, "*NVM VOL+", NSTREAM_PORT, verbose=verbose)
        print("Volume up command sent!")
        if verbose:
            for line in responses:
                print(f"  {line}")
    except NStreamError as e:
        print(f"Failed to increase volume: {e}")
        sys.exit(1)


def cmd_volume_down(args):
    """Decrease volume via n-Stream protocol."""
    verbose = getattr(args, 'verbose', False)
    print(f"Decreasing volume via n-Stream protocol...")

    try:
        responses = nstream_send_and_receive(args.host, "*NVM VOL-", NSTREAM_PORT, verbose=verbose)
        print("Volume down command sent!")
        if verbose:
            for line in responses:
                print(f"  {line}")
    except NStreamError as e:
        print(f"Failed to decrease volume: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────
# CLI SETUP
# ─────────────────────────────────────────────

def add_host_args(p):
    p.add_argument("--host", default=None, help="Naim device IP address")
    p.add_argument("--port", type=int, default=NSTREAM_PORT,
                   help=f"n-Stream port (default: {NSTREAM_PORT})")


def main():
    parser = argparse.ArgumentParser(
        description="Naim Streamer Control CLI — n-Stream/BridgeCo Protocol (port 15555)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This tool controls legacy Naim devices (SuperUniti, NDS, NDX, UnitiQute, etc.)
via the proprietary n-Stream/BridgeCo protocol on TCP port 15555.

Primary use case: Input/source switching (not available via UPnP)

Examples:
  %(prog)s inputs                                    # List valid input names
  %(prog)s --host 192.168.1.21 set-input --input DIGITAL2
  %(prog)s --host 192.168.1.21 set-input --input UPNP
  %(prog)s --host 192.168.1.21 set-input --input digital2 -v
  %(prog)s --host 192.168.1.21 get-input
  %(prog)s --host 192.168.1.21 input-up
  %(prog)s --host 192.168.1.21 input-down

Device Information:
  %(prog)s --host 192.168.1.21 product
  %(prog)s --host 192.168.1.21 version
  %(prog)s --host 192.168.1.21 mac
  %(prog)s --host 192.168.1.21 preamp

Volume Control (via NVM):
  %(prog)s --host 192.168.1.21 vol-up
  %(prog)s --host 192.168.1.21 vol-down

Raw Commands:
  %(prog)s --host 192.168.1.21 raw --command "*NVM PRODUCT"
  %(prog)s --host 192.168.1.21 raw --command "*NVM VERSION"

For playback control (play/pause/stop), use naim_control_upnp.py instead.
For newer devices (Uniti series), use naim_control_rest.py instead.
        """,
    )
    add_host_args(parser)

    sub = parser.add_subparsers(title="commands", dest="command", required=True)

    # ── INPUT COMMANDS ──
    p = sub.add_parser("inputs", help="List available inputs (query device if --host provided)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_list_inputs)

    p = sub.add_parser("set-input", help="Switch to specified input")
    p.add_argument("--input", "-i", required=True,
                   help="Input name (e.g., UPNP, DIGITAL1, DIGITAL2, ANALOGUE1, BLUETOOTH)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_set_input)

    p = sub.add_parser("get-input", help="Get current input")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_get_input)

    p = sub.add_parser("input-up", help="Cycle to next input")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_up)

    p = sub.add_parser("input-down", help="Cycle to previous input")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_down)

    p = sub.add_parser("input-enable", help="Enable an input (make it active/selectable)")
    p.add_argument("--input", "-i", required=True,
                   help="Input ID to enable (e.g., DIGITAL1, ANALOGUE1)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_enable)

    p = sub.add_parser("input-disable", help="Disable an input (hide it from selection)")
    p.add_argument("--input", "-i", required=True,
                   help="Input ID to disable (e.g., DIGITAL1, ANALOGUE1)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_disable)

    p = sub.add_parser("input-enabled", help="Check if an input is enabled")
    p.add_argument("--input", "-i", required=True,
                   help="Input ID to check (e.g., DIGITAL1, ANALOGUE1)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_get_enabled)

    p = sub.add_parser("input-rename", help="Set a custom name/alias for an input")
    p.add_argument("--input", "-i", required=True,
                   help="Input ID to rename (e.g., DIGITAL1, ANALOGUE1)")
    p.add_argument("--name", "-n", required=True,
                   help="New display name for the input")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_rename)

    p = sub.add_parser("input-name", help="Get the current name/alias of an input")
    p.add_argument("--input", "-i", required=True,
                   help="Input ID to query (e.g., DIGITAL1, ANALOGUE1)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_input_get_name)

    # ── DEVICE INFO COMMANDS ──
    p = sub.add_parser("product", help="Get product type")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_product)

    p = sub.add_parser("version", help="Get firmware version")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("mac", help="Get MAC address")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_mac)

    p = sub.add_parser("preamp", help="Get preamp status")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_preamp)

    # ── VOLUME COMMANDS ──
    p = sub.add_parser("vol-up", help="Increase volume")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_volume_up)

    p = sub.add_parser("vol-down", help="Decrease volume")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_volume_down)

    # ── RAW COMMAND ──
    p = sub.add_parser("raw", help="Send a raw NVM command")
    p.add_argument("--command", "-c", required=True,
                   help="Raw NVM command (e.g., '*NVM PRODUCT')")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show detailed debug output")
    p.set_defaults(func=cmd_raw)

    args = parser.parse_args()

    # --host is required for all commands except these
    commands_without_host = {"inputs"}
    if args.command not in commands_without_host and not args.host:
        parser.error("--host is required for this command")

    try:
        args.func(args)
    except NStreamError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
