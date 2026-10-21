"""Non-blocking NEBULOUS startup and process-scoped bridge acknowledgements."""
import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import io
import os
from pathlib import Path
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from .core import read_xml, put, write_xml


@dataclass(frozen=True)
class GameProcess:
    pid: int
    started: float


def game_processes(executable):
    """Match the actual executable path, not another installation with the same name."""
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/tasklist.exe"
    result = subprocess.run([str(tasklist), "/FI", "IMAGENAME eq Nebulous.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, errors="replace", check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    matches = []
    expected = os.path.normcase(str(Path(executable).resolve()))
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2 or row[0].casefold() != "nebulous.exe":
            continue
        pid = int(row[1])
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Process exited after tasklist took its snapshot.
                continue
            raise OSError("Cannot inspect a running NEBULOUS process. Close that game instance and retry.")
        try:
            name = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(name))
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            if os.path.normcase(str(Path(name.value).resolve())) != expected:
                continue
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise ctypes.WinError(ctypes.get_last_error())
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            matches.append(GameProcess(pid, ticks / 10_000_000 - 11_644_473_600))
        finally:
            kernel.CloseHandle(handle)
    return matches


class GameRuntime:
    TIMEOUT = 180

    def __init__(self, game, exchange, scanner=game_processes, launcher=subprocess.Popen,
                 clock=time.monotonic, wall_clock=time.time):
        self.game, self.exchange = Path(game), Path(exchange)
        self.scanner, self.launcher, self.clock, self.wall_clock = scanner, launcher, clock, wall_clock
        self.purpose = None
        self.message = ""
        self.instance = None
        self.resume_instance = None
        self.launch_token = None
        self.child = None
        self.started = 0
        self.spawned = 0

    def begin(self, purpose, resume_instance=None):
        if self.purpose:
            raise ValueError("NEBULOUS is already starting. Wait for it to connect or cancel the pending request.")
        if not (self.game / "Nebulous.exe").is_file():
            raise ValueError(f"NEBULOUS was not found at {self.game}. Check the configured game location.")
        manifest = self.game / "Mods/NBCBridge/ModInfo.xml"
        if not (self.game / "Mods/NBCBridge/NBCBridge.dll").is_file() or not manifest.exists():
            raise ValueError("Install the updated bridge using Install Bridge.ps1 before starting NEBULOUS.")
        version = read_xml(manifest).findtext("ModVer", "0")
        if tuple(int(n) for n in version.split(".")) < (0, 5, 0):
            raise ValueError("Install bridge 0.5.0 or newer using Install Bridge.ps1, then restart NEBULOUS.")
        self.purpose = purpose
        self.resume_instance = resume_instance
        self.started = self.clock()
        self.instance = None
        self.child = None
        self.launch_token = None
        self.message = "Starting NEBULOUS… Waiting for the game bridge."
        try:
            self.poll()
        except Exception:
            self.purpose = None
            raise

    def heartbeat(self, processes):
        try:
            doc = read_xml(self.exchange / "runtime-status.xml")
            if doc.tag != "GameRuntime" or doc.findtext("Protocol") != "1":
                return None
            pid = int(doc.findtext("Pid", "0"))
            started = float(doc.findtext("StartedEpoch", "0"))
            age = self.wall_clock() - float(doc.findtext("HeartbeatEpoch", "0"))
            if not -2 <= age <= 6 or not any(p.pid == pid and abs(p.started - started) < 1 for p in processes):
                return None
            return doc
        except (OSError, ValueError, ET.ParseError):
            return None

    def poll(self):
        if not self.purpose:
            return False
        if self.clock() - self.started > self.TIMEOUT:
            self.purpose = None
            self.message = "NEBULOUS did not connect within three minutes. Check Steam, enable NBC Campaign Bridge, and resume the pending request."
            raise TimeoutError(self.message)
        processes = self.scanner(self.game / "Nebulous.exe")
        if len(processes) > 1:
            raise ValueError("Multiple NEBULOUS processes use this installation. Close the extra instance and resume.")
        heartbeat = self.heartbeat(processes)
        if heartbeat is not None:
            phase = heartbeat.findtext("Phase")
            if phase in ("quitting", "awaiting-ack", "exporting"):
                self.message = "Waiting for the previous NEBULOUS session to close…"
                return False
            resuming = bool(self.resume_instance) and self.purpose == "refit" and phase == "refit" and self.resume_instance == heartbeat.findtext("Instance")
            if (heartbeat.findtext("Ready") == "true" and phase == "ready") or resuming:
                self.instance = heartbeat.findtext("Instance")
                self.message = "NEBULOUS connected. Opening the requested session…"
                if self.purpose == "catalog":
                    if self.launch_token and heartbeat.findtext("LaunchToken") == self.launch_token:
                        self.command_quit(self.launch_token)
                    self.message = "Equipment catalogue refreshed."
                    self.purpose = None
                return True
            self.message = heartbeat.findtext("Message") or "Waiting for the current NEBULOUS session to return to its main menu…"
            return False
        if processes:
            self.message = "NEBULOUS is loading… Waiting for bridge 0.5 to connect."
            return False
        if self.child is not None:
            if self.child.poll() is None or self.clock() - self.spawned < 10:
                return False
            self.purpose = None
            self.message = "NEBULOUS exited before its bridge connected. Check Steam and the game log, then resume."
            raise RuntimeError(self.message)
        self.launch_token = str(uuid.uuid4())
        self.spawned = self.clock()
        self.child = self.launcher([str(self.game / "Nebulous.exe"), "-nbc-launch", self.launch_token], cwd=str(self.game))
        self.message = "Starting NEBULOUS… Waiting for the game bridge."
        return False

    def submitted(self):
        self.purpose = None

    def command_quit(self, token):
        root = ET.Element("RuntimeCommand")
        put(root, "Action", "QuitIfIdle")
        put(root, "LaunchToken", token)
        write_xml(self.exchange / "runtime-command.xml", root)

    def cancel_startup(self):
        if self.launch_token and self.child is not None and self.instance is None:
            self.command_quit(self.launch_token)
        self.purpose = None

