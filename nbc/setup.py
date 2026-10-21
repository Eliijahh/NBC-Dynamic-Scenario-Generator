"""First-run setup, portable configuration and automatic bridge installation.

The packaged application is portable: it keeps its config and campaign data next
to the executable. First run detects the NEBULOUS installation, copies the
bundled bridge into the game's Mods folder, enables it in the game's active-mod
list, and remembers the paths. Later runs just load the config.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

from .core import ROOT, read_xml, write_xml

APP_NAME = "NBC Campaign Command"
BRIDGE_MOD_NAME = "NBC Campaign Bridge"
BRIDGE_FILES = ("NBCBridge.dll", "ModInfo.xml", "0Harmony.dll", "Harmony-LICENSE.txt")
BRIDGE_MIN_VERSION = (0, 5, 0)
MOD_LIST_NAME = ".ActiveModList.xml"


def app_dir():
    """The folder holding the executable (packaged) or the repository (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def bundled_bridge_dir():
    """Where the bridge files that ship with this build live."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(ROOT / "bridge")
    candidates.extend([ROOT / "build" / "NBCBridge", ROOT / "bridge"])
    for candidate in candidates:
        if (candidate / "NBCBridge.dll").is_file():
            return candidate
    return candidates[0]


def config_path():
    return app_dir() / "config.json"


def default_config():
    return {"game_path": "", "data_path": ""}


def load_config():
    config = default_config()
    try:
        loaded = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if isinstance(loaded, dict):
        for key in config:
            if isinstance(loaded.get(key), str):
                config[key] = loaded[key]
    return config


def save_config(config):
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot save the configuration next to the executable: {exc}") from exc
    return path


def valid_game(path):
    path = Path(path)
    return (path / "Nebulous.exe").is_file() and (
        path / "Nebulous_Data" / "Managed" / "Nebulous.dll"
    ).is_file()


def resolve_game(game=None):
    """Configured or auto-detected NEBULOUS path, or None."""
    if game and valid_game(game):
        return Path(game)
    configured = load_config().get("game_path", "")
    if configured and valid_game(configured):
        return Path(configured)
    return detect_game()


def _writable(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def resolve_data(data=None):
    """Campaign data directory: explicit, configured, else portable ``data/``.

    Falls back to a per-user folder when the portable location is not writable
    (for example an executable left in Program Files).
    """
    if data:
        return Path(data)
    configured = load_config().get("data_path", "")
    if configured:
        return Path(configured)
    portable = app_dir() / "data"
    if _writable(portable):
        return portable
    fallback = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def steam_libraries():
    """Steam library folders from the registry and ``libraryfolders.vdf``."""
    if os.name != "nt":
        return []
    import winreg

    libraries = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
    except OSError:
        steam_path = ""
    if steam_path:
        libraries.append(Path(steam_path))
    for root in list(libraries):
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if '"path"' not in line:
                continue
            parts = line.split('"path"', 1)[1].split('"')
            if len(parts) >= 2 and parts[1]:
                libraries.append(Path(parts[1].replace("\\\\", "\\")))
    seen, result = set(), []
    for library in libraries:
        key = os.path.normcase(str(library))
        if key not in seen:
            seen.add(key)
            result.append(library)
    return result


def detect_game():
    for library in steam_libraries():
        candidate = library / "steamapps" / "common" / "Nebulous"
        if valid_game(candidate):
            return candidate
    return None


def _version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


def bridge_version(source=None):
    manifest = Path(source or bundled_bridge_dir()) / "ModInfo.xml"
    if not manifest.is_file():
        return ""
    try:
        return read_xml(manifest).findtext("ModVer", "")
    except (OSError, ET.ParseError):
        return ""


def installed_bridge_dir(game):
    return Path(game) / "Mods" / "NBCBridge"


def installed_bridge_version(game):
    manifest = installed_bridge_dir(game) / "ModInfo.xml"
    if not manifest.is_file():
        return ""
    try:
        return read_xml(manifest).findtext("ModVer", "")
    except (OSError, ET.ParseError):
        return ""


def install_bridge(game, source=None, force=False):
    """Copy the bundled bridge into the game. Idempotent and version-aware."""
    game = Path(game)
    if not valid_game(game):
        raise ValueError("That folder does not look like a NEBULOUS installation.")
    source = Path(source or bundled_bridge_dir())
    if not (source / "NBCBridge.dll").is_file():
        raise ValueError("This build does not contain the bridge files.")
    target = installed_bridge_dir(game)
    installed = installed_bridge_version(game)
    bundled = bridge_version(source)
    if not force and installed and _version_tuple(installed) >= _version_tuple(bundled):
        return {"installed": True, "updated": False, "version": installed}
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"Cannot write to the NEBULOUS Mods folder: {exc}") from exc
    for name in BRIDGE_FILES:
        source_file = source / name
        if source_file.is_file():
            shutil.copyfile(source_file, target / name)
    return {"installed": True, "updated": bool(installed), "version": bundled}


def net_string_hash(value):
    """Replicate .NET Framework / Mono ``string.GetHashCode`` (64-bit path)."""
    h1 = 5381
    h2 = 5381
    chars = [ord(c) for c in str(value)]
    index, count = 0, len(chars)

    def wrap(number):
        number &= 0xFFFFFFFF
        return number - 0x100000000 if number >= 0x80000000 else number

    while index < count:
        char = chars[index]
        if char == 0:
            break
        h1 = wrap(((h1 << 5) + h1) ^ char)
        index += 1
        if index >= count:
            break
        char = chars[index]
        if char == 0:
            break
        h2 = wrap(((h2 << 5) + h2) ^ char)
        index += 1
    return wrap(h1 + h2 * 1566083941) & 0xFFFFFFFF


def mod_id(name=BRIDGE_MOD_NAME):
    """The id NEBULOUS assigns to a local mod with this name."""
    return net_string_hash(name)


def mod_list_path(game):
    return Path(game) / "Mods" / MOD_LIST_NAME


def active_mod_ids(game):
    path = mod_list_path(game)
    if not path.is_file():
        return []
    try:
        root = read_xml(path)
    except (OSError, ET.ParseError):
        return []
    ids = []
    for node in root.findall("Mods/unsignedLong"):
        text = (node.text or "").strip()
        if text.isdigit():
            ids.append(int(text))
    return ids


def enable_mod(game, name=BRIDGE_MOD_NAME):
    """Add the bridge id to the game's active-mod list, preserving other mods."""
    path = mod_list_path(game)
    ids = active_mod_ids(game)
    wanted = mod_id(name)
    if wanted in ids:
        return {"enabled": True, "changed": False, "id": wanted}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot write to the NEBULOUS Mods folder: {exc}") from exc
    root = ET.Element(
        "PersistentActiveMods",
        {
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        },
    )
    mods = ET.SubElement(root, "Mods")
    for value in ids + [wanted]:
        ET.SubElement(mods, "unsignedLong").text = str(value)
    write_xml(path, root)
    return {"enabled": True, "changed": True, "id": wanted}


def catalogue_path(game):
    return Path(game) / "Saves" / "NBCBridge" / "fitting-catalog.xml"


def bridge_status(game=None, source=None):
    """A snapshot of the game/bridge/catalogue state for the setup screen."""
    game = Path(game) if game else None
    bundled = bridge_version(source)
    status = {
        "game": str(game) if game else "",
        "game_ok": bool(game and valid_game(game)),
        "bundled_version": bundled,
        "installed_version": "",
        "installed": False,
        "version_ok": False,
        "enabled": False,
        "catalogue": False,
    }
    if not status["game_ok"]:
        return status
    status["installed_version"] = installed_bridge_version(game)
    status["installed"] = bool(status["installed_version"])
    status["version_ok"] = (
        status["installed"]
        and _version_tuple(status["installed_version"]) >= BRIDGE_MIN_VERSION
    )
    status["enabled"] = mod_id() in active_mod_ids(game)
    status["catalogue"] = catalogue_path(game).is_file()
    return status


def status_text(status):
    lines = []
    lines.append(f"Game folder: {status['game'] or 'not set'}")
    if not status["game_ok"]:
        lines.append("NEBULOUS was not found there. Use Detect or Browse to select it.")
        return "\n".join(lines)
    lines.append(f"Bridge installed: {status['installed_version'] or 'no'}")
    lines.append(f"Bridge enabled: {'yes' if status['enabled'] else 'no'}")
    lines.append(f"Fitting catalogue: {'exported' if status['catalogue'] else 'missing'}")
    if not status["catalogue"]:
        lines.append("Launch NEBULOUS once with the bridge enabled to export the catalogue.")
    return "\n".join(lines)
