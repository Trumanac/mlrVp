"""Grid controller interface for mlrVp.

Handles communication with:
- Monome grids via serialosc (OSC protocol)
- Novation Launchpad series via MIDI
- Generic MIDI grid controllers
- Built-in virtual grid (always available)
"""

import asyncio
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Optional, Callable
from dataclasses import dataclass, field

from mlrvp.config import (
    MAX_ROWS,
    MAX_COLUMNS,
    LED_OFF,
    LED_DIM,
    LED_MED,
    LED_BRIGHT,
    MONOME_BACKENDS,
    MONOME_BACKEND_AUTO,
    MONOME_BACKEND_SERIALOSC,
    MONOME_BACKEND_40H_DIRECT,
    MONOME_BACKEND_LEGACY_OSC,
)
from mlrvp.grid.midi_grid import MidiGridAdapter, scan_midi_grid_devices
from mlrvp.grid.monome40h import Monome40hApp, probe_40h
from mlrvp.grid.monomeserial import MonomeSerialApp

from PySide6.QtCore import QObject, Signal, Qt

log = logging.getLogger("mlrvp")

# Default serialosc daemon port (UDP) — the same on macOS, Windows and Linux.
SERIALOSC_HOST = "127.0.0.1"
SERIALOSC_PORT = 12002


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
#  Minimal raw-OSC encoder/decoder for the serialosc probe.
#  We deliberately avoid pulling in pymonome/aiosc here so the probe still
#  works even if pymonome is broken/missing, and so we get a synchronous
#  answer without touching the asyncio thread.
# ---------------------------------------------------------------------------

def _osc_pad(b: bytes) -> bytes:
    """Pad to 4-byte boundary (OSC null-terminator + alignment)."""
    return b + b"\0" + b"\0" * (3 - (len(b) % 4))


def _osc_string(s: str) -> bytes:
    return _osc_pad(s.encode("utf-8"))


def _osc_message(addr: str, *args) -> bytes:
    """Encode a tiny subset of OSC: only str + int32 args (enough for serialosc)."""
    type_tag = ","
    arg_data = b""
    for a in args:
        if isinstance(a, str):
            type_tag += "s"
            arg_data += _osc_string(a)
        elif isinstance(a, int):
            type_tag += "i"
            arg_data += struct.pack(">i", a)
        else:
            raise TypeError(f"Unsupported OSC arg type: {type(a).__name__}")
    return _osc_string(addr) + _osc_string(type_tag) + arg_data


def _osc_parse(data: bytes):
    """Parse a single OSC message. Returns (address, [args]) or None."""
    try:
        # Address
        end = data.index(b"\0")
        addr = data[:end].decode("utf-8")
        i = (end // 4 + 1) * 4
        # Type tag
        end = data.index(b"\0", i)
        types = data[i:end].decode("utf-8")
        i = ((end // 4) + 1) * 4
        if not types.startswith(","):
            return addr, []
        args = []
        for t in types[1:]:
            if t == "i":
                args.append(struct.unpack(">i", data[i:i + 4])[0])
                i += 4
            elif t == "s":
                end = data.index(b"\0", i)
                args.append(data[i:end].decode("utf-8"))
                i = ((end // 4) + 1) * 4
            elif t == "f":
                args.append(struct.unpack(">f", data[i:i + 4])[0])
                i += 4
            else:
                # Unknown type — bail out, return what we have.
                break
        return addr, args
    except Exception:
        return None


@dataclass
class SerialoscProbeResult:
    """Outcome of a synchronous serialosc probe."""
    daemon_running: bool = False
    devices: list[dict] = field(default_factory=list)  # [{id,type,port}, ...]
    error: str = ""

    @property
    def summary(self) -> str:
        if not self.daemon_running:
            return (
                f"serialosc daemon not responding on "
                f"{SERIALOSC_HOST}:{SERIALOSC_PORT}"
                + (f" ({self.error})" if self.error else "")
            )
        if not self.devices:
            return (
                "serialosc is running but reports no devices "
                "(check USB cable / FTDI driver / monome power LED)"
            )
        names = ", ".join(f"{d['id']} ({d['type']})" for d in self.devices)
        return f"serialosc found {len(self.devices)} device(s): {names}"


def _windows_service_hint() -> str:
    """Return an actionable hint if the serialosc Windows service is stopped."""
    if sys.platform != "win32":
        return ""
    try:
        r = subprocess.run(
            ["sc", "query", "serialosc"],
            capture_output=True, text=True, timeout=2.0,
        )
        if "STOPPED" in r.stdout:
            return (
                "On Windows the 'serialosc' service is installed but stopped — "
                "open Task Manager > Services, right-click serialosc > Start "
                "(or run 'net start serialosc' as Administrator)"
            )
        if "does not exist" in r.stderr or "1060" in r.stdout:
            return (
                "serialosc not installed — download from "
                "https://github.com/monome/serialosc/releases"
            )
    except Exception:
        pass
    return ""


def _windows_stuck_device_pids() -> list[int]:
    """Return PIDs of serialosc-device.exe processes that have no UDP socket.

    When serialosc-device.exe opens the COM port but the monome serial
    handshake never completes (common on Windows 11 with serialosc 1.4.7),
    the process holds the port open indefinitely without ever creating an
    OSC UDP socket.  This leaves serialoscd unable to list any devices.
    """
    if sys.platform != "win32":
        return []
    try:
        # Get running serialosc-device PIDs via tasklist (no admin needed)
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq serialosc-device.exe",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=3.0,
        )
        import csv, io as _io
        pids: set[int] = set()
        for row in csv.reader(_io.StringIO(r.stdout)):
            if len(row) >= 2:
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    pass
        if not pids:
            return []

        # Check netstat for UDP sockets owned by those PIDs
        r2 = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"],
            capture_output=True, text=True, timeout=3.0,
        )
        for line in r2.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                owner = int(parts[-1])
            except (ValueError, IndexError):
                continue
            pids.discard(owner)  # this PID has a socket — not stuck

        return list(pids)
    except Exception:
        return []


def _windows_find_monome_com_port() -> str:
    """Return the COM port (e.g. 'COM7') used by the monome FTDI device.

    Queries PnP devices for FTDI USB Serial converters (VID_0403) and
    returns the first COM port assignment found.  Falls back to scanning
    serialosc-device command-line arguments via wmic.
    """
    if sys.platform != "win32":
        return ""
    # Primary: pyserial's cross-platform port enumeration — works without
    # any external process and detects FTDI devices by VID/PID.
    ports = _find_monome_ftdi_ports()
    if ports:
        return ports[0][0]
    # Secondary: wmic — works when serialosc-device is running
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name='serialosc-device.exe'", "get", "CommandLine", "/VALUE"],
            capture_output=True, text=True, timeout=4.0,
        )
        import re as _re
        for m in _re.finditer(r'(COM\d+)', r.stdout, _re.IGNORECASE):
            return m.group(1).upper()
    except Exception:
        pass
    # Fallback: PowerShell PnP query for FTDI COM ports
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice | Where-Object {$_.InstanceId -match 'FTDI' -and "
             "$_.FriendlyName -match 'COM'} | "
             "Select-Object -ExpandProperty FriendlyName"],
            capture_output=True, text=True, timeout=5.0,
        )
        import re as _re
        for m in _re.finditer(r'COM(\d+)', r.stdout):
            return f"COM{m.group(1)}"
    except Exception:
        pass
    return ""


def _find_monome_ftdi_ports() -> list[tuple[str, str]]:
    """Find FTDI serial ports that look like a monome (VID_0403/PID_6001).

    Returns a list of (com_port, hardware_id) tuples. Cross-platform via
    pyserial — works on Windows, macOS and Linux without external tools.
    Returns an empty list if pyserial is missing or no FTDI device found.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    found: list[tuple[str, str]] = []
    try:
        for p in list_ports.comports():
            # FTDI vendor 0x0403, FT232R product 0x6001 — matches monome 40h
            vid_pid = (
                f"{getattr(p, 'vid', 0) or 0:04x}:"
                f"{getattr(p, 'pid', 0) or 0:04x}"
            )
            if vid_pid == "0403:6001":
                found.append((p.device, p.hwid or ""))
    except Exception as e:
        log.debug("FTDI port scan failed: %s", e)
    return found


def _windows_stop_serialosc_service(com_port: str = "") -> None:
    """Request UAC-elevated stop of serialosc + kill any child processes.

    Used when entering 40h direct mode: we need exclusive access to the
    COM port, so serialosc must not be holding it.  Fire-and-forget.
    """
    if sys.platform != "win32":
        return
    ps_script = (
        "Stop-Service serialosc -Force -ErrorAction SilentlyContinue;"
        "Get-Process -Name 'serialosc*' -EA SilentlyContinue "
        "| Stop-Process -Force -EA SilentlyContinue;"
        "Write-Host 'serialosc stopped for 40h direct mode'"
    )
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command",
             f"Start-Process powershell -Verb RunAs "
             f"-ArgumentList '-WindowStyle','Hidden','-Command','{ps_script}'"],
            creationflags=0x00000008,  # DETACHED_PROCESS
        )
        log.info(
            "Requested elevated stop of serialosc (40h direct mode%s)",
            f" on {com_port}" if com_port else "",
        )
    except Exception as e:
        log.warning("Could not request serialosc stop: %s", e)


def _windows_kill_stuck_devices() -> int:
    """Kill stuck serialosc-device.exe processes (returns count actually killed).

    Uses taskkill — works without admin only when the process is owned by
    the current user.  If the service runs as LocalSystem the kill will be
    refused.  Returns 0 if all kills were denied.
    """
    killed = 0
    for pid in _windows_stuck_device_pids():
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=3.0,
            )
            # taskkill prints "SUCCESS: ..." on kill, or "ERROR: Access is denied"
            if "SUCCESS" in r.stdout or r.returncode == 0 and "denied" not in r.stdout.lower():
                killed += 1
        except Exception:
            pass
    return killed


def probe_serialosc(timeout: float = 0.6) -> SerialoscProbeResult:
    """Synchronously query serialosc for connected devices.

    Sends `/serialosc/list` directly via UDP and listens for replies for
    a short window. Bypasses pymonome entirely so it still works if that
    library is missing or its asyncio plumbing is broken on this host.

    daemon_running detection: we attempt to bind the serialosc port first.
    If that bind fails (EADDRINUSE), the daemon is listening regardless of
    whether any /serialosc/device replies arrive. serialosc sends *no* reply
    when zero devices are connected, so relying on a received packet would
    incorrectly report the daemon as down when the monome isn't plugged in.
    """
    result = SerialoscProbeResult()

    # ── Step 1: check whether anything is bound to SERIALOSC_PORT ────────
    # We must test 0.0.0.0 (not 127.0.0.1) because Windows allows a
    # specific-address bind to coexist with an existing wildcard bind
    # (serialoscd uses 0.0.0.0).  Testing with 0.0.0.0 fails reliably.
    _probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _probe_sock.bind(("0.0.0.0", SERIALOSC_PORT))
        # Bind succeeded → port is free → daemon is NOT running.
        _probe_sock.close()
        return result   # daemon_running stays False
    except OSError:
        # EADDRINUSE (or similar) — daemon IS holding the port.
        result.daemon_running = True
    finally:
        try:
            _probe_sock.close()
        except Exception:
            pass

    # ── Step 2: query for connected devices ───────────────────────────────
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        local_host, local_port = sock.getsockname()
        # Tell serialosc where to send replies.
        msg = _osc_message("/serialosc/list", local_host, int(local_port))
        sock.sendto(msg, (SERIALOSC_HOST, SERIALOSC_PORT))

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            except OSError as e:
                # ICMP "port unreachable" can surface here on Windows
                # when nothing is bound to 12002 — surface it explicitly.
                result.error = f"OSError: {e}"
                # Anything we got before this is still valid
                break
            parsed = _osc_parse(data)
            if not parsed:
                continue
            addr, args = parsed
            if addr == "/serialosc/device" and len(args) >= 3:
                dev_id, dev_type, dev_port = args[0], args[1], args[2]
                # serialosc pads type with trailing spaces — strip them.
                if isinstance(dev_type, str):
                    dev_type = dev_type.strip()
                result.devices.append(
                    {"id": dev_id, "type": dev_type, "port": dev_port}
                )
    except OSError as e:
        result.error = f"socket bind failed: {e}"
    finally:
        sock.close()
    return result


class _GridSignalBridge(QObject):
    """Thread-safe bridge for forwarding grid events to the Qt main thread.

    QTimer.singleShot called from non-Qt threads (rtmidi, asyncio) can
    silently fail in PySide6.  Signals with QueuedConnection are the
    correct cross-thread mechanism.
    """
    button_event = Signal(int, int, int)    # x, y, state
    control_event = Signal(int, int)        # btn_idx, state
    tilt_event = Signal(int, float, float, float)  # sensor, x, y, z


@dataclass
class GridInfo:
    """Information about a connected grid."""
    id: str = ""
    type: str = ""
    port: int = 0
    host: str = "127.0.0.1"
    prefix: str = "/mlrvp"
    rows: int = 8
    columns: int = 16
    rotation: int = 0


class GridController:
    """Interface for monome grid hardware via serialosc.

    Also manages an internal LED state buffer that can be used by
    the virtual grid in the GUI.
    """

    def __init__(self):
        self.info = GridInfo()
        self._connected = False
        self._led_state = [[LED_OFF] * MAX_COLUMNS for _ in range(MAX_ROWS)]
        self._control_led_state = [LED_OFF] * MAX_COLUMNS
        self._control_led_sent: dict[int, tuple] = {}  # track sent state to avoid MIDI spam
        # Row-level cache: row_index → (tuple(values), color) to skip unchanged I/O
        self._row_sent: dict[int, tuple] = {}
        self._button_callback: Optional[Callable] = None
        self._control_button_callback: Optional[Callable] = None
        self._led_update_callback: Optional[Callable] = None
        self._monome = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._asyncio_stop: Optional[asyncio.Event] = None  # set inside asyncio thread
        self._monome_row_offset = 0  # 1 when monome connected (top row = control)
        self._pending_monome_connect = False
        self._state_lock = threading.Lock()  # guards _connected / _monome_row_offset

        # Thread-safe bridge for forwarding grid events to Qt main thread
        self._bridge = _GridSignalBridge()
        self._bridge.button_event.connect(self._dispatch_button, Qt.ConnectionType.QueuedConnection)
        self._bridge.control_event.connect(self._dispatch_control, Qt.ConnectionType.QueuedConnection)
        self._bridge.tilt_event.connect(self._dispatch_tilt, Qt.ConnectionType.QueuedConnection)

        # MIDI grid controller (Launchpad, etc.)
        self._midi_grid: Optional[MidiGridAdapter] = None
        self._monome_backend = MONOME_BACKEND_AUTO

        # Tilt / accelerometer support (monome only)
        self._tilt_callback: Optional[Callable] = None
        self._tilt_enabled = False

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._connected

    @property
    def has_control_row(self) -> bool:
        """True if the grid uses a physical control row (monome top row)."""
        with self._state_lock:
            return self._monome_row_offset > 0

    @property
    def led_state(self) -> list[list[int]]:
        return self._led_state

    def get_monome_backend(self) -> str:
        return self._monome_backend

    def set_monome_backend(self, mode: str):
        normalized = (mode or MONOME_BACKEND_AUTO).strip().lower()
        if normalized not in MONOME_BACKENDS:
            normalized = MONOME_BACKEND_AUTO
        self._monome_backend = normalized

    def _effective_backend_mode(self) -> str:
        if _env_truthy("MLRVP_MONOMESERIAL"):
            return MONOME_BACKEND_LEGACY_OSC
        if _env_truthy("MLRVP_FORCE_40H"):
            return MONOME_BACKEND_40H_DIRECT
        return self._monome_backend

    def set_button_callback(self, callback: Callable):
        """Set callback for button presses: callback(x, y, state)."""
        self._button_callback = callback

    def set_control_button_callback(self, callback: Callable):
        """Set callback for control row presses: callback(x, state)."""
        self._control_button_callback = callback

    def set_led_update_callback(self, callback: Callable):
        """Set callback when LED state changes: callback(led_state)."""
        self._led_update_callback = callback

    def set_tilt_callback(self, callback: Callable):
        """Set callback for accelerometer/tilt data: callback(sensor, x, y, z).

        x, y, z are normalized 0.0-1.0 values from the monome accelerometer.
        """
        self._tilt_callback = callback

    def enable_tilt(self, enabled: bool = True):
        """Enable or disable tilt/accelerometer sensor on the monome."""
        self._tilt_enabled = enabled
        if self._monome is not None and hasattr(self._monome, 'grid'):
            try:
                self._monome.grid.tilt_set(0, 1 if enabled else 0)
            except Exception as e:
                log.debug("Tilt enable failed: %s", e)

    def force_hardware_led_sync(self):
        """Force OFF to hardware for any dark cells.

        Clears firmware ghost LEDs that survived the startup clear.  Safe to
        call from the Qt thread after grid connect has settled (e.g., via a
        one-shot QTimer).
        """
        grid = getattr(self._monome, 'grid', None) if self._monome else None
        if grid is not None and hasattr(grid, 'force_off_unlit_cells'):
            try:
                grid.force_off_unlit_cells()
            except Exception as e:
                log.debug("force_hardware_led_sync failed: %s", e)

    def connect(self):
        """Start the serialosc discovery thread.

        Monome grids are detected asynchronously by serialosc — this only
        boots the asyncio event loop in a background thread. Actual grid
        readiness arrives via the `on_grid_ready` callback.
        """
        if self._thread is not None and self._thread.is_alive():
            return  # already running

        # If a MIDI grid is already active there is no need to run the
        # monome discovery thread — serialosc probes and COM port scans
        # waste resources for Launchpad / virtual-grid users.  The thread
        # will be restarted automatically when the MIDI grid disconnects.
        if self._midi_grid is not None and self._midi_grid.connected:
            log.debug("Skipping monome discovery — MIDI grid is active")
            return

        # Check pymonome availability — but don't bail if it's missing AND
        # the configured backend bypasses serialosc entirely.
        backend_mode = self._effective_backend_mode()
        use_serialosc = backend_mode in (
            MONOME_BACKEND_AUTO,
            MONOME_BACKEND_SERIALOSC,
        )
        force_40h = backend_mode == MONOME_BACKEND_40H_DIRECT
        use_monomeserial = backend_mode == MONOME_BACKEND_LEGACY_OSC
        try:
            try:
                import monome  # noqa: F401  pymonome >= 0.9
            except ImportError:
                import pymonome as monome  # noqa: F401  legacy fallback
        except ImportError:
            if use_serialosc:
                log.info("pymonome not available — monome grids won't be detected")
                return
            if use_monomeserial:
                log.info(
                    "pymonome not available — running in legacy OSC mode"
                )
            else:
                log.info(
                    "pymonome not available — running in 40h direct mode"
                )
        try:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_async, daemon=True, name="mlrvp-monome"
            )
            self._thread.start()
        except Exception as e:
            log.warning("Grid connection error: %s", e)

    @staticmethod
    def probe_monome(timeout: float = 0.6) -> SerialoscProbeResult:
        """Synchronously check whether the serialosc daemon is reachable
        and how many monome devices it currently reports.

        Safe to call from any thread, including the Qt main thread.
        """
        return probe_serialosc(timeout=timeout)

    @staticmethod
    def restart_serialosc_service(com_port: str = ""):
        """Restart the serialosc Windows service with a PnP device cycle.

        On Windows, serialosc-device.exe sometimes sticks in the serial
        handshake because the FTDI chip retains stale internal state.
        A PnP disable/enable cycle (Disable-PnpDevice / Enable-PnpDevice)
        operates at the driver level: it forces Windows to release the COM
        port (terminating any process that holds it), then re-enumerates
        the USB device — equivalent to physically unplugging and replugging.

        Opens an elevated UAC prompt that:
          1. Stops the serialosc service
          2. Disables the PnP COM-port device (releases FTDI chip fully)
          3. Re-enables the PnP device (fresh USB enumeration)
          4. Restarts the service

        On other platforms this is a no-op.  The call is fire-and-forget;
        the retry loop in _run_async() picks up the daemon within 10 s.
        """
        if sys.platform != "win32":
            return

# Build the DTR reset block.
        # The monome AVR MCU has its RESET pin wired through a 100 nF cap to the
        # FTDI DTR line.  A HIGH→LOW falling edge on DTR pulses RESET and causes
        # the firmware to re-run its init sequence, re-sending the device ID that
        # serialosc-device is waiting for.
        #
        # CRITICAL direction: open the port with DtrEnable=$true (line HIGH) first,
        # then pull LOW — that is the falling edge that triggers the reset.  Opening
        # with DTR already LOW (the .NET default) produces only a rising edge later
        # which has no effect on the AVR RESET cap circuit.
        if com_port:
            dtr_block = (
                f"try {{"
                f" $sp = New-Object System.IO.Ports.SerialPort '{com_port}',115200;"
                f" $sp.DtrEnable = $true;"
                f" $sp.Open();"
                f" Write-Host 'DTR HIGH — pulsing LOW to reset AVR...';"
                f" $sp.DtrEnable = $false; Start-Sleep -Milliseconds 300;"
                f" $sp.DtrEnable = $true;  Start-Sleep -Milliseconds 600;"
                f" $sp.Close();"
                f" Write-Host 'DTR reset on {com_port} done';"
                f"}} catch {{ Write-Host \"DTR reset skipped: $_\" }}"
            )
        else:
            dtr_block = "Write-Host 'No COM port — skipping DTR reset'"

        ps_script = (
            "Stop-Service serialosc -Force -ErrorAction SilentlyContinue;"
            "Get-Process -Name 'serialosc*' -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue;"
            "Start-Sleep -Seconds 2;"
            + dtr_block + ";"
            "Start-Sleep -Milliseconds 500;"
            "Start-Service serialosc -ErrorAction SilentlyContinue;"
            "Write-Host 'serialosc restarted'"
        )

        try:
            subprocess.Popen(
                [
                    "powershell", "-WindowStyle", "Hidden",
                    "-Command",
                    f"Start-Process powershell -Verb RunAs "
                    f"-ArgumentList '-WindowStyle','Hidden','-Command','{ps_script}'",
                ],
                creationflags=0x00000008,  # DETACHED_PROCESS
            )
            log.info(
                "Elevated serialosc restart requested%s",
                f" (DTR reset on {com_port})" if com_port else "",
            )
        except Exception as e:
            log.warning("Could not request serialosc service restart: %s", e)

    def rescan_monome(self):
        """Ask serialosc to re-enumerate connected devices.

        Useful when:
          - the grid was plugged in after the app started
          - the user just (re)started the serialosc daemon
          - we want to force a fresh /sys/info handshake
        Restarts the asyncio discovery thread if it has died.
        """
        backend_mode = self._effective_backend_mode()
        if backend_mode == MONOME_BACKEND_40H_DIRECT:
            if not (self.connected and isinstance(self._monome, Monome40hApp)):
                self.restart_monome()
            return
        if backend_mode == MONOME_BACKEND_LEGACY_OSC:
            if isinstance(self._monome, MonomeSerialApp):
                try:
                    self._monome.request_report()
                except Exception as e:
                    log.warning("Monomeserial rescan failed: %s", e)
                return
            self.restart_monome()
            return
        if isinstance(self._monome, MonomeSerialApp):
            try:
                self._monome.request_report()
            except Exception as e:
                log.warning("Monomeserial rescan failed: %s", e)
            return
        # If the discovery thread is dead, just (re)start it.
        if self._thread is None or not self._thread.is_alive():
            self.connect()
            return
        # Otherwise, schedule a fresh /serialosc/list on the asyncio loop.
        loop = self._loop
        if loop is None or loop.is_closed():
            self.connect()
            return

        async def _resend_list():
            try:
                import monome
            except ImportError:
                try:
                    import pymonome as monome  # type: ignore
                except ImportError:
                    return
            try:
                serialosc = monome.SerialOsc()
                serialosc.device_added_event.add_handler(
                    self._on_rescan_device_found
                )
                await serialosc.connect()
                # Give serialosc a moment to reply, then close the temp socket.
                await asyncio.sleep(0.5)
                if serialosc.transport is not None:
                    serialosc.transport.close()
            except Exception as e:
                log.warning("Monome rescan failed: %s", e)

        try:
            asyncio.run_coroutine_threadsafe(_resend_list(), loop)
        except RuntimeError as e:
            log.warning("Could not schedule monome rescan: %s", e)

    def restart_monome(self):
        """Restart monome discovery without affecting any MIDI grid."""
        self._disconnect_monome()
        self.connect()

    def _on_rescan_device_found(self, id_, type_, port):
        """Handler used by rescan_monome for a one-shot /serialosc/list."""
        log.info("monome rescan: %s (%s) on port %s", id_, type_, port)
        # If we don't already have a live connection, kick the main
        # discovery flow into action by restarting it.
        if not self.connected and self._monome is not None:
            try:
                # Connect the persistent app's grid to the discovered device.
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._monome.grid.connect("127.0.0.1", port), loop
                    )
            except Exception as e:
                log.warning("Failed to attach monome after rescan: %s", e)

    def connect_midi_grid(self, in_port: int, out_port: int = -1) -> bool:
        """Connect a MIDI grid controller (Launchpad, etc.)."""
        self.disconnect_midi_grid()
        # Stop the monome discovery thread — the serialosc probe loop and
        # COM port scan are unnecessary while a MIDI grid is in use.
        self._disconnect_monome()
        self._midi_grid = MidiGridAdapter()
        self._midi_grid.set_button_callback(self._on_midi_grid_button)
        self._midi_grid.set_control_callback(self._on_midi_grid_control_button)
        if self._midi_grid.connect(in_port, out_port):
            with self._state_lock:
                self._connected = True
                self._control_led_sent.clear()  # force LED refresh on reconnect
                self.info.type = f"midi:{self._midi_grid.model.value}"
                self.info.rows = self._midi_grid.rows
                self.info.columns = self._midi_grid.columns
            log.info("MIDI grid connected: %s (%dx%d)",
                     self._midi_grid.model.value,
                     self._midi_grid.columns, self._midi_grid.rows)
            return True
        self._midi_grid = None
        return False

    def disconnect_midi_grid(self):
        """Disconnect the MIDI grid controller."""
        if self._midi_grid is not None:
            self._midi_grid.disconnect()
            self._midi_grid = None
            if self._monome is None:
                with self._state_lock:
                    self._connected = False
        # Restart monome discovery now that MIDI is no longer blocking it —
        # allows hot-plugging a monome after using a Launchpad.
        if self._monome is None:
            self.connect()

    def _dispatch_button(self, x: int, y: int, state: int):
        """Slot executed on the Qt main thread via queued connection."""
        cb = self._button_callback
        if cb:
            cb(x, y, state)

    def _dispatch_control(self, btn_idx: int, state: int):
        """Slot executed on the Qt main thread via queued connection."""
        cb = self._control_button_callback
        if cb:
            cb(btn_idx, state)

    def _dispatch_tilt(self, sensor: int, x: float, y: float, z: float):
        """Slot executed on the Qt main thread via queued connection."""
        cb = self._tilt_callback
        if cb:
            cb(sensor, x, y, z)

    def _on_midi_grid_button(self, x: int, y: int, state: int):
        """Forward MIDI grid button events to the main callback (on Qt thread)."""
        self._bridge.button_event.emit(x, y, state)

    def _on_midi_grid_control_button(self, btn_idx: int, state: int):
        """Forward MIDI grid control button events (on Qt thread)."""
        self._bridge.control_event.emit(btn_idx, state)

    @staticmethod
    def scan_midi_grids() -> list[dict]:
        """Scan for MIDI grid controllers (Launchpads, etc.)."""
        return scan_midi_grid_devices()

    # ── 40h direct-mode (legacy monome) ──────────────────────────────────
    def _activate_monomeserial(
        self,
        send_host: str,
        send_port: int,
        recv_host: str,
        recv_port: int,
        prefix: str,
        timeout: float = 1.5,
    ) -> bool:
        """Open a legacy monome through monomeserial OSC."""
        controller = self

        class _MlrMonomeSerialApp(MonomeSerialApp):
            def on_grid_ready(self):
                with controller._state_lock:
                    controller._connected = True
                    controller.info.rows = self.grid.height
                    controller.info.columns = self.grid.width
                    controller.info.id = self.grid.id or "monomeserial"
                    controller.info.rotation = 0
                    controller.info.type = "monome-monomeserial"
                    controller._monome_row_offset = 1
                    controller._pending_monome_connect = True
                    controller._row_sent.clear()
                    controller._control_led_sent.clear()
                log.info(
                    "Monome grid ready (monomeserial): id=%s %dx%d via %s:%d -> %s:%d",
                    self.grid.id,
                    self.grid.width,
                    self.grid.height,
                    send_host,
                    send_port,
                    recv_host,
                    recv_port,
                )
                controller._refresh_leds()
                if controller._tilt_enabled:
                    log.info(
                        "Monomeserial backend does not support tilt — "
                        "use 40h-direct or serialosc for tilt control"
                    )

            def on_grid_key(self, x, y, s):
                if controller._monome_row_offset > 0 and y == 0:
                    controller._bridge.control_event.emit(x, s)
                else:
                    adj_y = y - controller._monome_row_offset
                    controller._bridge.button_event.emit(x, adj_y, s)

            def on_grid_disconnect(self, *_):
                with controller._state_lock:
                    controller._connected = False
                    controller._monome_row_offset = 0
                    controller._row_sent.clear()
                    controller._control_led_sent.clear()
                log.info("Monome grid disconnected (monomeserial)")

        app = _MlrMonomeSerialApp()
        self._monome = app
        try:
            app.open_osc(
                send_host=send_host,
                send_port=send_port,
                recv_host=recv_host,
                recv_port=recv_port,
                prefix=prefix,
                timeout=timeout,
            )
            return True
        except Exception as e:
            self._monome = None
            log.warning(
                "Monomeserial mode failed on %s:%d -> %s:%d: %s",
                send_host,
                send_port,
                recv_host,
                recv_port,
                e,
            )
            return False

    def _activate_40h_direct(self, com_port: str) -> bool:
        """Open the legacy 40h-protocol monome on `com_port` directly.

        Used as a fallback when serialosc cannot complete its handshake —
        typically because the user has an old (pre-2010) monome that
        speaks the 40h protocol, which serialosc 1.4.x on Windows does
        not support.

        Returns True if the device was opened successfully.  The caller
        is responsible for stopping serialosc first (via
        `_windows_stop_serialosc_service`) so the COM port is free.
        """
        if not com_port:
            return False
        controller = self

        class _Mlr40hApp(Monome40hApp):
            def on_grid_ready(self):
                with controller._state_lock:
                    controller._connected = True
                    controller.info.rows = self.grid.height
                    controller.info.columns = self.grid.width
                    controller.info.id = self.grid.id or ""
                    controller.info.rotation = 0
                    controller.info.type = "monome-40h"
                    controller._monome_row_offset = 1
                    controller._pending_monome_connect = True
                    controller._row_sent.clear()
                    controller._control_led_sent.clear()
                log.info(
                    "Monome 40h grid ready (direct serial): id=%s %dx%d on %s",
                    self.grid.id, self.grid.width, self.grid.height, com_port,
                )
                controller._refresh_leds()
                # Enable ADC tilt streaming immediately at connect.
                # Also re-enabled ~2s later via enable_tilt() in
                # _check_monome_connect, but sending early improves reliability.
                if controller._tilt_enabled:
                    try:
                        self.grid.tilt_set(0, 1)
                    except Exception:
                        pass

            def on_grid_key(self, x, y, s):
                if controller._monome_row_offset > 0 and y == 0:
                    controller._bridge.control_event.emit(x, s)
                else:
                    adj_y = y - controller._monome_row_offset
                    controller._bridge.button_event.emit(x, adj_y, s)

            def on_grid_disconnect(self, *_):
                with controller._state_lock:
                    controller._connected = False
                    controller._monome_row_offset = 0
                    controller._row_sent.clear()
                    controller._control_led_sent.clear()
                log.info("Monome 40h grid disconnected")

            def on_tilt(self, n, x, y, z):
                """Forward 40h ADC tilt data through the signal bridge."""
                if controller._tilt_callback:
                    nx = max(0.0, min(1.0, x / 255.0))
                    ny = max(0.0, min(1.0, y / 255.0))
                    try:
                        controller._bridge.tilt_event.emit(n, nx, ny, 0.5)
                    except RuntimeError:
                        pass  # bridge deleted during disconnect/reconnect

        try:
            app = _Mlr40hApp()
            # Derive a short device id from the COM port (best-effort).
            ports = _find_monome_ftdi_ports()
            dev_id = ""
            for port, hwid in ports:
                if port.upper() == com_port.upper():
                    # Extract serial number suffix from hwid if present
                    import re as _re
                    m = _re.search(r"SER=([A-Za-z0-9-]+)", hwid)
                    if m:
                        dev_id = m.group(1)
                    break
            app.open_direct(com_port, device_id=dev_id)
            self._monome = app
            return True
        except Exception as e:
            log.warning("40h direct mode failed on %s: %s", com_port, e)
            return False

    def _run_async(self):
        """Run the asyncio event loop for serialosc communication."""
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        main_task = None

        # ── Optional fast-path: force 40h direct mode (skip serialosc) ───
        # Set MLRVP_FORCE_40H=1 to bypass serialosc entirely — useful for
        # users with legacy 40h-protocol monomes who don't want to wait
        # for the stuck-recovery dance.
        backend_mode = self._effective_backend_mode()
        use_serialosc = backend_mode in (
            MONOME_BACKEND_AUTO,
            MONOME_BACKEND_SERIALOSC,
        )
        force_40h = backend_mode == MONOME_BACKEND_40H_DIRECT
        use_monomeserial = backend_mode == MONOME_BACKEND_LEGACY_OSC
        allow_direct_fallback = backend_mode == MONOME_BACKEND_AUTO

        try:
            if use_monomeserial:
                send_host = os.environ.get("MLRVP_MONOMESERIAL_HOST", "127.0.0.1") or "127.0.0.1"
                send_port = _env_int("MLRVP_MONOMESERIAL_SEND_PORT", 8080)
                recv_host = os.environ.get("MLRVP_MONOMESERIAL_RECV_HOST", "127.0.0.1") or "127.0.0.1"
                recv_port = _env_int("MLRVP_MONOMESERIAL_RECV_PORT", 8000)
                prefix = os.environ.get("MLRVP_MONOMESERIAL_PREFIX", "")
                timeout = _env_float("MLRVP_MONOMESERIAL_TIMEOUT", 1.5)
                stop_event = asyncio.Event()
                self._asyncio_stop = stop_event

                async def _monomeserial_main():
                    while not stop_event.is_set():
                        ok = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: self._activate_monomeserial(
                                send_host=send_host,
                                send_port=send_port,
                                recv_host=recv_host,
                                recv_port=recv_port,
                                prefix=prefix,
                                timeout=timeout,
                            ),
                        )
                        if ok:
                            await stop_event.wait()
                            return
                        log.warning(
                            "monomeserial not responding on %s:%d -> %s:%d — retrying in 5s. "
                            "Check monomeserial is already running, the monome is plugged in before launch, "
                            "and its host/listen ports still match mlrVp.",
                            send_host,
                            send_port,
                            recv_host,
                            recv_port,
                        )
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass

                main_task = self._loop.create_task(_monomeserial_main())
                self._loop.run_forever()
                return

            if force_40h:
                ports = _find_monome_ftdi_ports()
                if ports:
                    com_port = ports[0][0]
                    log.info(
                        "Monome backend set to 40h direct — using %s "
                        "(skipping serialosc)", com_port,
                    )
                    # Try direct open first. The standalone COM7 diagnostic that
                    # produced actual RX bytes did not stop serialosc or bounce
                    # the Windows service first, so avoid perturbing the FTDI
                    # device unless we actually need exclusive-access recovery.
                    if self._activate_40h_direct(com_port):
                        # Hold the asyncio loop open until disconnect.
                        stop_event = asyncio.Event()
                        self._asyncio_stop = stop_event
                        main_task = self._loop.create_task(stop_event.wait())
                        self._loop.run_forever()
                        return  # finally: block below handles cleanup

                    if sys.platform == "win32":
                        log.info(
                            "40h direct open failed on %s — stopping serialosc and retrying",
                            com_port,
                        )
                        _windows_stop_serialosc_service(com_port)
                        time.sleep(3.0)  # let service stop and release COM port
                    if self._activate_40h_direct(com_port):
                        # Hold the asyncio loop open until disconnect.
                        stop_event = asyncio.Event()
                        self._asyncio_stop = stop_event
                        main_task = self._loop.create_task(stop_event.wait())
                        self._loop.run_forever()
                        return  # finally: block below handles cleanup
                    if not use_serialosc:
                        log.warning(
                            "40h direct mode selected, but direct open failed on %s",
                            com_port,
                        )
                        return
                else:
                    if not use_serialosc:
                        log.warning(
                            "40h direct mode selected, but no FTDI device was found"
                        )
                        return
                    log.warning(
                        "40h direct mode requested, but no FTDI device was found "
                        "— falling back to serialosc"
                    )

            if not use_serialosc:
                return

            # ── Auto-detect fast-path: FTDI device + no serialosc daemon ───────
            # If the user is on 'auto' and there's a legacy FTDI device (the
            # classic monome chip VID_0403/PID_6001) but serialosc is not
            # running, skip the whole serialosc dance and go straight to 40h.
            # This means zero wait time for pre-2010 monome owners who don't
            # have serialosc installed.
            #
            # SAFETY: VID:PID 0403:6001 is the generic FTDI FT232R chip used by
            # many non-monome devices (Arduino clones, USB-serial cables,
            # industrial sensors).  If multiple FTDI devices are present we
            # cannot tell which is the monome, and opening the wrong one and
            # sending 40h protocol bytes (LED clear) could disturb whatever it
            # is.  In that case, skip the fast-path and let serialosc handle
            # discovery (it queries each device with a real handshake).
            if allow_direct_fallback:
                ftdi_ports = _find_monome_ftdi_ports()
                if ftdi_ports:
                    quick_probe = probe_serialosc(timeout=0.4)
                    if not quick_probe.daemon_running:
                        if len(ftdi_ports) > 1:
                            log.warning(
                                "auto: %d FTDI devices found (%s) — cannot "
                                "safely auto-select 40h direct mode. Set "
                                "Monome Backend = '40h direct' explicitly to "
                                "force the first port, or unplug other FTDI "
                                "devices.",
                                len(ftdi_ports),
                                ", ".join(p[0] for p in ftdi_ports),
                            )
                        else:
                            com_port = ftdi_ports[0][0]
                            log.info(
                                "auto: serialosc not running, FTDI device on %s — "
                                "trying 40h legacy direct mode",
                                com_port,
                            )
                            if self._activate_40h_direct(com_port):
                                stop_event = asyncio.Event()
                                self._asyncio_stop = stop_event
                                main_task = self._loop.create_task(stop_event.wait())
                                self._loop.run_forever()
                                return
                            log.warning(
                                "auto: 40h direct open failed on %s — "
                                "falling back to serialosc", com_port
                            )

            try:
                import monome
            except ImportError:
                try:
                    import pymonome as monome
                except ImportError:
                    log.warning(
                        "pymonome not installed — cannot start serialosc "
                        "discovery (40h direct mode is the only option)"
                    )
                    return  # finally: cleans up loop

            controller = self

            class MlrGridApp(monome.GridApp):
                def on_grid_ready(self):
                    with controller._state_lock:
                        controller._connected = True
                        controller.info.rows = self.grid.height
                        controller.info.columns = self.grid.width
                        controller.info.id = self.grid.id or ""
                        controller.info.rotation = self.grid.rotation or 0
                        controller.info.type = "monome"
                        controller._monome_row_offset = 1  # top row = controls
                        controller._pending_monome_connect = True
                        # Force LED refresh — clear dedupe caches so the next
                        # set_row / set_control_led actually transmits.
                        controller._row_sent.clear()
                        controller._control_led_sent.clear()
                    log.info(
                        "Monome grid ready: id=%s %dx%d rot=%s",
                        self.grid.id, self.grid.width, self.grid.height,
                        self.grid.rotation,
                    )
                    controller._refresh_leds()
                    # Enable tilt sensor if requested
                    if controller._tilt_enabled:
                        try:
                            self.grid.tilt_set(0, 1)
                        except Exception:
                            pass

                def on_grid_key(self, x, y, s):
                    if controller._monome_row_offset > 0 and y == 0:
                        # Top row → control buttons
                        controller._bridge.control_event.emit(x, s)
                    else:
                        # Data rows: offset y for channel mapping
                        adj_y = y - controller._monome_row_offset
                        controller._bridge.button_event.emit(x, adj_y, s)

                def on_grid_disconnect(self, *_):
                    with controller._state_lock:
                        controller._connected = False
                        controller._monome_row_offset = 0
                        controller._row_sent.clear()
                        controller._control_led_sent.clear()
                    log.info("Monome grid disconnected")

                def on_tilt(self, n, x, y, z):
                    """Accelerometer data from the monome.

                    Devices that connect via serialosc use the mext serial
                    protocol which has a 10-bit ADC (0–1023, centre ≈ 512).
                    """
                    if controller._tilt_callback:
                        # Normalize raw tilt values (mext 10-bit ADC: 0-1023) to 0.0-1.0
                        nx = max(0.0, min(1.0, x / 1023.0))
                        ny = max(0.0, min(1.0, y / 1023.0))
                        try:
                            controller._bridge.tilt_event.emit(n, nx, ny, 0.5)
                        except RuntimeError:
                            pass  # bridge deleted during disconnect/reconnect

            app = MlrGridApp()
            self._monome = app

            stop_event = asyncio.Event()
            self._asyncio_stop = stop_event  # exposed for disconnect()

            async def _serialosc_main():
                def _on_device_added(id_, type_, port):
                    log.info("serialosc: device added id=%s type=%s port=%s",
                             id_, type_, port)
                    # Only connect grids — ignore arcs / other devices.
                    t = (type_ or "").lower()
                    if "arc" in t:
                        return
                    asyncio.create_task(app.grid.connect("127.0.0.1", port))

                while not stop_event.is_set():
                    # ── Phase 1: wait for daemon to be running ────────────
                    probe = await asyncio.get_running_loop().run_in_executor(
                        None, probe_serialosc, 0.8
                    )
                    if not probe.daemon_running:
                        hint = _windows_service_hint()
                        log.warning(
                            "serialosc not running on %s:%d — retrying in 10s%s",
                            SERIALOSC_HOST, SERIALOSC_PORT,
                            f". {hint}" if hint else "",
                        )
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=10.0)
                        except asyncio.TimeoutError:
                            pass
                        continue

                    # ── Phase 2: daemon is up — connect ───────────────────
                    serialosc = monome.SerialOsc()
                    serialosc.device_added_event.add_handler(_on_device_added)
                    try:
                        await serialosc.connect()
                    except OSError as e:
                        log.warning(
                            "serialosc connect error: %s — retrying in 5s", e
                        )
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                        continue

                    log.info(
                        "serialosc discovery active on %s:%d — %s",
                        SERIALOSC_HOST, SERIALOSC_PORT, probe.summary,
                    )

                    # ── Phase 3: keep-alive — re-probe every 15 s ────────
                    _stuck_warned = False
                    _stuck_kill_attempts = 0
                    while not stop_event.is_set():
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=15.0)
                            break  # stop was signalled — exit outer loop too
                        except asyncio.TimeoutError:
                            pass
                        re_probe = await asyncio.get_running_loop().run_in_executor(
                            None, probe_serialosc, 0.8
                        )
                        if not re_probe.daemon_running:
                            log.warning(
                                "serialosc daemon stopped — will reconnect"
                            )
                            try:
                                if getattr(serialosc, "transport", None):
                                    serialosc.transport.close()
                            except Exception:
                                pass
                            _stuck_warned = False
                            _stuck_kill_attempts = 0
                            break  # restart outer loop to reconnect

                        if re_probe.devices:
                            _stuck_warned = False
                            _stuck_kill_attempts = 0
                            continue

                        # Daemon running but still no devices — check for stuck process
                        stuck = await asyncio.get_running_loop().run_in_executor(
                            None, _windows_stuck_device_pids
                        )
                        if stuck:
                            if not _stuck_warned:
                                log.warning(
                                    "serialosc-device is running but has no UDP socket "
                                    "(stuck in serial handshake). PIDs: %s", stuck,
                                )
                                _stuck_warned = True
                            # Try user-level kill first (works if serialosc was started
                            # manually rather than as a Windows service).
                            if _stuck_kill_attempts == 0:
                                killed = await asyncio.get_running_loop().run_in_executor(
                                    None, _windows_kill_stuck_devices
                                )
                                _stuck_kill_attempts += 1
                                if killed:
                                    log.info(
                                        "Killed %d stuck serialosc-device process(es) "
                                        "— waiting for detector to re-spawn", killed,
                                    )
                                    _stuck_warned = False
                                else:
                                    # Kill was denied (LocalSystem service process).
                                    # For legacy 40h hardware that serialosc 1.4.x
                                    # cannot handle, go straight to 40h direct mode
                                    # on the FIRST stuck event (in auto mode) rather
                                    # than waiting for a second UAC-elevated retry.
                                    # This reduces the wait from ~45 s to ~15 s.
                                    if allow_direct_fallback:
                                        _stuck_kill_attempts += 1  # skip UAC path
                                    else:
                                        com_port_r = await asyncio.get_running_loop().run_in_executor(
                                            None, _windows_find_monome_com_port
                                        )
                                        log.warning(
                                            "Cannot kill LocalSystem serialosc-device — "
                                            "requesting one elevated service restart via UAC "
                                            "to clear the stuck process%s.",
                                            f" (monome on {com_port_r})" if com_port_r else "",
                                        )
                                        await asyncio.get_running_loop().run_in_executor(
                                            None,
                                            lambda: GridController.restart_serialosc_service(com_port_r),
                                        )
                            elif _stuck_kill_attempts >= 1:
                                # serialosc-device is stuck and user-kill either
                                # succeeded but got re-stuck, or was denied and we
                                # skipped UAC.  Most likely the user has a legacy
                                # 40h-protocol monome that serialosc 1.4.x cannot
                                # speak to.  Try direct 40h serial mode now.
                                _stuck_kill_attempts += 1  # advance so we only try once
                                if not allow_direct_fallback:
                                    log.error(
                                        "MONOME NOT DETECTED — serialosc recovery exhausted. "
                                        "If this is an older monome, switch Monome Backend "
                                        "to 40h direct and rescan. Otherwise unplug the USB "
                                        "cable, wait 3 seconds, then plug it back in."
                                    )
                                    continue
                                com_port = await asyncio.get_running_loop().run_in_executor(
                                    None, _windows_find_monome_com_port
                                )
                                if com_port:
                                    log.warning(
                                        "serialosc handshake repeatedly fails on %s — "
                                        "attempting legacy 40h direct-serial fallback",
                                        com_port,
                                    )
                                    # Stop serialosc to free the COM port, then take
                                    # exclusive control via direct serial I/O.
                                    if sys.platform == "win32":
                                        await asyncio.get_running_loop().run_in_executor(
                                            None,
                                            lambda: _windows_stop_serialosc_service(com_port),
                                        )
                                        await asyncio.sleep(3.0)
                                    ok = await asyncio.get_running_loop().run_in_executor(
                                        None,
                                        lambda: self._activate_40h_direct(com_port),
                                    )
                                    if ok:
                                        log.info(
                                            "40h direct mode active — exiting "
                                            "serialosc loop"
                                        )
                                        # Close the serialosc transport and exit
                                        # the discovery loop; 40h adapter has its
                                        # own reader thread.
                                        try:
                                            if getattr(serialosc, "transport", None):
                                                serialosc.transport.close()
                                        except Exception:
                                            pass
                                        # Wait here forever (until disconnect)
                                        await stop_event.wait()
                                        return
                                log.error(
                                    "MONOME NOT DETECTED — automated recovery exhausted. "
                                    "If you have an older (40h-protocol) monome, switch "
                                    "Monome Backend to 40h direct and rescan. Otherwise "
                                    "unplug the USB cable, "
                                    "wait 3 seconds, then plug it back in."
                                )

            main_task = self._loop.create_task(_serialosc_main())
            self._loop.run_forever()
        except Exception as e:
            log.exception("Serialosc thread error: %s", e)
        finally:
            # Cancel the main task explicitly — avoids the Python 3.12+
            # deprecation of asyncio.all_tasks(loop=...) and ensures we
            # never see "Task was destroyed but it is pending!" on shutdown.
            if main_task is not None and not main_task.done():
                main_task.cancel()
                try:
                    self._loop.run_until_complete(
                        asyncio.gather(main_task, return_exceptions=True)
                    )
                except Exception:
                    pass
            self._asyncio_stop = None
            try:
                self._loop.close()
            except Exception:
                pass

    def _disconnect_monome(self):
        with self._state_lock:
            self._monome_row_offset = 0
            self._pending_monome_connect = False
            if self._midi_grid is None:
                self._connected = False
        # Close 40h direct adapter (if active) — its own reader thread
        # needs explicit shutdown since it doesn't live on the asyncio loop.
        if isinstance(self._monome, (Monome40hApp, MonomeSerialApp)):
            try:
                self._monome.close()
            except Exception:
                log.debug("Legacy monome close error", exc_info=True)
        self._monome = None
        if self._loop is not None and not self._loop.is_closed():
            # Signal the asyncio task to stop cleanly before killing the loop.
            stop = self._asyncio_stop
            if stop is not None:
                try:
                    self._loop.call_soon_threadsafe(stop.set)
                except RuntimeError:
                    pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass  # loop already closed
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                log.warning("Grid asyncio thread did not exit within timeout")
            self._thread = None
        self._loop = None

    def disconnect(self):
        """Disconnect from all grid hardware."""
        with self._state_lock:
            self._connected = False
        self.disconnect_midi_grid()
        self._disconnect_monome()

    def set_led(self, x: int, y: int, brightness: int, color: int = 0):
        """Set a single LED (logical channel row coordinates)."""
        if 0 <= x < MAX_COLUMNS and 0 <= y < MAX_ROWS:
            self._led_state[y][x] = brightness
            with self._state_lock:
                connected = self._connected
                offset = self._monome_row_offset
            if connected and self._monome is not None:
                try:
                    physical_y = y + offset
                    self._monome.grid.led_level_set(x, physical_y, brightness)
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_led(x, y, brightness, color)

    def set_control_led(self, x: int, brightness: int, color: int = 0):
        """Set an LED on the control row (monome top row / LP CC buttons)."""
        if 0 <= x < MAX_COLUMNS:
            key = (x, brightness, color)
            with self._state_lock:
                if key == self._control_led_sent.get(x):
                    return  # unchanged — skip redundant MIDI send
                self._control_led_sent[x] = key
                self._control_led_state[x] = brightness
                connected = self._connected
                offset = self._monome_row_offset
            if connected and self._monome is not None and offset > 0:
                try:
                    self._monome.grid.led_level_set(x, 0, brightness)
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_control_led(x, brightness, color)

    def set_row(self, y: int, values: list[int], color: int = 0):
        """Set an entire row of LEDs (logical channel row coordinates)."""
        if 0 <= y < MAX_ROWS:
            # Fast check: skip all I/O if values + color haven't changed
            cache_key = (tuple(values), color)
            if self._row_sent.get(y) == cache_key:
                return
            self._row_sent[y] = cache_key
            for x, val in enumerate(values):
                if x < MAX_COLUMNS:
                    self._led_state[y][x] = val
            with self._state_lock:
                connected = self._connected
                offset = self._monome_row_offset
                cols = self.info.columns
            if connected and self._monome is not None:
                try:
                    physical_y = y + offset
                    row_data = list(values[:cols])
                    if len(row_data) < cols:
                        row_data.extend([0] * (cols - len(row_data)))
                    self._monome.grid.led_level_row(0, physical_y, row_data)
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_row(y, values, color)

    def set_all(self, brightness: int):
        """Set all LEDs to the same brightness."""
        self._row_sent.clear()
        for y in range(MAX_ROWS):
            for x in range(MAX_COLUMNS):
                self._led_state[y][x] = brightness
        self._refresh_leds()

    def clear(self):
        """Turn off all LEDs."""
        self.set_all(LED_OFF)

    def _refresh_leds(self):
        """Push all LED state to the hardware grid."""
        with self._state_lock:
            connected = self._connected
            offset = self._monome_row_offset
            cols = self.info.columns
            rows = self.info.rows
            # Snapshot LED state under lock to avoid races with Qt thread
            ctrl_leds = self._control_led_state[:cols].copy()
            data_rows = rows - offset
            led_snapshot = [
                self._led_state[y][:cols].copy()
                for y in range(min(data_rows, MAX_ROWS))
            ]
        if connected and self._monome is not None:
            # Control row at physical y=0 for monome
            if offset > 0:
                try:
                    self._monome.grid.led_level_row(0, 0, ctrl_leds)
                except Exception:
                    pass
            # Data rows offset by monome_row_offset
            for y, row_leds in enumerate(led_snapshot):
                try:
                    physical_y = y + offset
                    self._monome.grid.led_level_row(0, physical_y, row_leds)
                except Exception:
                    pass

    def update_playback_leds(self, row: int, play_column: int, total_columns: int,
                             sub_loop_start: int = -1, sub_loop_end: int = -1,
                             color: int = 0):
        """Update LEDs for a row showing playback position.

        Shows a bright LED at playback position, dim LEDs for sub-loop range.
        """
        values = [LED_OFF] * total_columns
        # Show sub-loop range as dim
        if sub_loop_start >= 0 and sub_loop_end >= 0:
            lo, hi = min(sub_loop_start, sub_loop_end), max(sub_loop_start, sub_loop_end)
            for c in range(lo, min(hi + 1, total_columns)):
                values[c] = LED_DIM
        if 0 <= play_column < total_columns:
            values[play_column] = LED_BRIGHT
        self.set_row(row, values, color)

    def clear_row_leds(self, row: int, total_columns: int):
        """Turn off all LEDs in a row."""
        values = [LED_OFF] * total_columns
        self.set_row(row, values, 0)

    def handle_virtual_press(self, x: int, y: int, state: int):
        """Handle a button press from the virtual grid UI."""
        if self._button_callback:
            self._button_callback(x, y, state)
