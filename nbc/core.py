"""Native fleet XML editing and a durable file-based game bridge. No dependencies."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_GAME = Path(r"R:\SteamLibrary\steamapps\common\Nebulous")
ROOT = Path(__file__).resolve().parent.parent
ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")


def now():
    return datetime.now(timezone.utc).isoformat()


def read_xml(path):
    data = Path(path).read_bytes()
    if len(data) > 30_000_000 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported XML document.")
    return ET.fromstring(data)


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def write_xml(path, root):
    root = copy.deepcopy(root)
    ET.indent(root, space="  ")
    atomic_bytes(path, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def put(root, tag, value):
    node = root.find(tag)
    if node is None:
        node = ET.SubElement(root, tag)
    node.text = str(value)


class Fleet:
    def __init__(self, root, source=None):
        if root.tag != "Fleet" or root.find("Ships") is None:
            raise ValueError("This is not a NEBULOUS .fleet file.")
        self.root, self.source = root, source

    @classmethod
    def load(cls, path):
        return cls(read_xml(path), Path(path))

    @property
    def name(self):
        return self.root.findtext("Name", "Unnamed fleet")

    @property
    def faction(self):
        return self.root.findtext("FactionKey", "")

    @property
    def ships(self):
        return self.root.findall("Ships/Ship")

    @property
    def points(self):
        return sum(int(s.findtext("Cost", "0")) for s in self.ships)

    def clone(self):
        return Fleet(copy.deepcopy(self.root), self.source)

    def validate(self):
        if not 1 <= len(self.ships) <= 10:
            raise ValueError("A fleet must contain 1–10 ships.")
        keys, names = set(), set()
        for ship in self.ships:
            key = ship.findtext("Key")
            uuid.UUID(key or "")
            name = ship.findtext("Name", "").strip()
            if not name or name in names or key in keys:
                raise ValueError("Each ship needs a unique identity and a unique, non-empty name.")
            if ship.find("SocketMap") is None or not ship.findtext("HullType"):
                raise ValueError("Ship is missing its hull or equipment.")
            names.add(name)
            keys.add(key)
        put(self.root, "TotalPoints", self.points)

    def save(self, path):
        self.validate()
        write_xml(path, self.root)

    def _clear_formations(self):
        for ship in self.ships:
            for node in ship.findall("InitialFormation"):
                ship.remove(node)

    def duplicate(self, index):
        if len(self.ships) >= 10:
            raise ValueError("The game's standard limit is 10 ships.")
        ship = copy.deepcopy(self.ships[index])
        put(ship, "Key", uuid.uuid4())
        base = ship.findtext("Name", "Ship")
        names = {s.findtext("Name") for s in self.ships}
        suffix = 2
        while f"{base} {suffix}" in names:
            suffix += 1
        put(ship, "Name", f"{base} {suffix}")
        put(ship, "Number", random.randint(100, 9999))
        self.root.find("Ships").append(ship)
        self._clear_formations()

    def remove(self, index):
        if len(self.ships) <= 1:
            raise ValueError("Keep at least one ship in your fleet.")
        self.root.find("Ships").remove(self.ships[index])
        self._clear_formations()

    def apply_loadout(self, index, donor, donor_index):
        old = self.ships[index]
        new = copy.deepcopy(donor.ships[donor_index])
        if donor.faction != self.faction or new.findtext("HullType") != old.findtext("HullType"):
            raise ValueError("Choose a loadout for the same hull and faction.")
        # Keep complete missile/craft definitions and unknown XML, never reconstruct designs.
        merged = copy.deepcopy(self.root)
        for tag in ("MissileTypes", "CraftTypes", "ModDependencies"):
            source = donor.root.find(tag)
            if source is None:
                continue
            target = merged.find(tag)
            if target is None:
                merged.append(copy.deepcopy(source))
                continue
            for item in source:
                identity = item.findtext("Key") or item.findtext("Designation") or item.text
                match = next((x for x in target if (x.findtext("Key") or x.findtext("Designation") or x.text) == identity), None)
                if match is not None and ET.tostring(match) != ET.tostring(item):
                    raise ValueError(f"Conflicting {tag} definition. Use a loadout from this fleet.")
                if match is None:
                    target.append(copy.deepcopy(item))
        for tag in ("Key", "Name", "Number", "Callsign"):
            put(new, tag, old.findtext(tag, ""))
        self.root = merged
        container = self.root.find("Ships")
        container.remove(self.ships[index])
        container.insert(index, new)
        self._clear_formations()

    def apply_component(self, index, socket_key, donor, donor_index):
        replacement = donor.ships[donor_index].find(f"SocketMap/HullSocket[Key='{socket_key}']")
        current = self.ships[index].find(f"SocketMap/HullSocket[Key='{socket_key}']")
        if replacement is None or current is None:
            raise ValueError("This component does not fit the selected socket.")
        # Reuse the template merge checks, keeping only this socket's new loadout.
        merged = self.clone()
        merged.apply_loadout(index, donor, donor_index)
        original = copy.deepcopy(self.ships[index])
        sockets = original.find("SocketMap")
        old_socket = original.find(f"SocketMap/HullSocket[Key='{socket_key}']")
        position = list(sockets).index(old_socket)
        sockets.remove(old_socket)
        sockets.insert(position, copy.deepcopy(replacement))
        container = merged.root.find("Ships")
        container.remove(merged.ships[index])
        container.insert(index, original)
        self.root = merged.root
        self._clear_formations()


def discover(game):
    result = []
    for path in sorted((Path(game) / "Saves" / "Fleets").rglob("*.fleet")):
        if "NBC-POC" in path.parts or "_SavedStates" in path.parts:
            continue
        try:
            fleet = Fleet.load(path)
            fleet.validate()
            result.append(fleet)
        except (OSError, ET.ParseError, ValueError, TypeError):
            continue
    return result


def generate_enemy(templates, faction, budget, seed=None):
    rng = random.Random(seed)
    candidates = [f for f in templates if f.faction == faction and f.ships and not f.root.findall("ModDependencies/unsignedLong")]
    if not candidates:
        raise ValueError("No stock enemy fleet templates found for that faction.")
    # Random subsets of complete proven designs preserve missile/craft references.
    options = []
    for source in candidates:
        for _ in range(12):
            subset = rng.sample(range(len(source.ships)), rng.randint(1, len(source.ships)))
            cost = sum(int(source.ships[i].findtext("Cost", "0")) for i in subset)
            options.append((abs(cost - budget), rng.random(), source, subset))
    _, _, source, subset = min(options, key=lambda x: x[:2])
    enemy = source.clone()
    container = enemy.root.find("Ships")
    for i, ship in enumerate(list(container)):
        if i not in subset:
            container.remove(ship)
        else:
            put(ship, "Key", uuid.UUID(int=rng.getrandbits(128), version=4))
    # Re-number after subset removal; unique names are required for report mapping.
    for i, ship in enumerate(enemy.ships, 1):
        put(ship, "Name", f"Hostile {i:02d}")
    put(enemy.root, "Name", "NBC Opposing Force")
    enemy._clear_formations()
    enemy.validate()
    return enemy, source.name


class Campaign:
    def __init__(self, game=DEFAULT_GAME, data=None):
        self.game = Path(game)
        self.data = Path(data or ROOT / "data")
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data / "campaign.json"
        self.state = {"version": 1, "battles": [], "ship_results": {}, "active_battle": None}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state.setdefault("inventory", {})
        self.state.setdefault("active_refit", None)
        self.state.setdefault("refits", [])
        self.player = Fleet(ET.fromstring(self.state["player_xml"])) if self.state.get("player_xml") else Fleet.load(self.data / "player.fleet") if (self.data / "player.fleet").exists() else None
        self.enemy = Fleet.load(self.data / "enemy.fleet") if (self.data / "enemy.fleet").exists() else None

    @property
    def exchange(self):
        return self.game / "Saves" / "NBCBridge"

    def persist(self):
        atomic_bytes(self.state_path, json.dumps(self.state, indent=2).encode())

    def save_fleets(self):
        if self.player:
            self.player.validate()
            self.state["player_xml"] = ET.tostring(self.player.root, encoding="unicode")
            self.persist()
            self.player.save(self.data / "player.fleet")
        if self.enemy:
            self.enemy.save(self.data / "enemy.fleet")

    def prepare(self):
        if self.state.get("active_refit"):
            raise ValueError("Finish or cancel the refit before launching a battle.")
        if self.state.get("active_battle"):
            raise ValueError("A battle is already pending. Finish it or cancel it before launching another.")
        if not self.player or not self.enemy:
            raise ValueError("Load a player fleet and generate an enemy first.")
        self.save_fleets()
        battle_id = str(uuid.uuid4())
        archive = self.data / "battles" / battle_id
        archive.mkdir(parents=True)
        exports = self.game / "Saves" / "Fleets" / "NBC-POC"
        p, e = exports / "NBC Player.fleet", exports / "NBC Enemy.fleet"
        player = self.player.clone()
        put(player.root, "Name", "NBC Player")
        player.save(p)
        self.enemy.save(e)
        player.save(archive / "player.fleet")
        self.enemy.save(archive / "enemy.fleet")
        request = ET.Element("BattleRequest", version="1")
        for key, value in {"Id": battle_id, "CreatedUtc": now(), "PlayerFleet": str(p), "EnemyFleet": str(e), "ResultDirectory": str(archive.resolve()), "PlayerHash": hashlib.sha256(p.read_bytes()).hexdigest(), "EnemyHash": hashlib.sha256(e.read_bytes()).hexdigest()}.items():
            put(request, key, value)
        record = {"id": battle_id, "status": "requested", "created": now(), "directory": str(archive.resolve())}
        self.state["active_battle"] = record
        self.persist()
        try:
            write_xml(self.exchange / "request.xml", request)
        except Exception:
            self.state["active_battle"] = None
            self.persist()
            raise
        return record

    def launch(self):
        if not (self.game / "Mods" / "NBCBridge" / "NBCBridge.dll").is_file():
            raise ValueError("Install the bridge first using Install Bridge.ps1, then enable NBC Campaign Bridge in the game's Mods menu.")
        record = self.prepare()
        try:
            # Reuse an existing game process; the loaded bridge polls for requests.
            running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Nebulous.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if '"nebulous.exe"' not in running.stdout.lower():
                subprocess.Popen([str(self.game / "Nebulous.exe")], cwd=self.game)
        except OSError:
            self.cancel()
            raise
        return record

    def bridge_status(self):
        try:
            status = read_xml(self.exchange / "status.xml")
            active = self.state.get("active_battle")
            if active and status.findtext("Id") != active["id"]:
                return "Waiting for the game bridge. Enable NBC Campaign Bridge in Mods if needed."
            return status.findtext("Message", "Bridge connected")
        except (OSError, ET.ParseError):
            return "Bridge not connected. Enable NBC Campaign Bridge in the game's Mods menu."

    def collect(self):
        active = self.state.get("active_battle")
        if not active:
            return None
        path = Path(active["directory"]) / "result.xml"
        if not path.exists():
            return None
        result = read_xml(path)
        if result.tag != "BattleResult" or result.findtext("Id") != active["id"]:
            raise ValueError("Battle result identity mismatch.")
        if any(b["id"] == active["id"] for b in self.state["battles"]):
            raise ValueError("This battle was already imported.")
        record = dict(active, status="completed", outcome=result.findtext("Outcome"), duration=result.findtext("Duration"), completed=now(), ships=[])
        roster = Fleet.load(Path(active["directory"]) / "player.fleet")
        valid_keys = {s.findtext("Key") for s in roster.ships}
        updates = {}
        for ship in result.findall("Ships/Ship"):
            item = {c.tag: (c.text or "").strip() for c in ship}
            record["ships"].append(item)
            if item.get("Side") == "Player":
                if item.get("Key") not in valid_keys or item["Key"] in updates:
                    raise ValueError("Battle report contains an unknown or duplicate player ship identity.")
                updates[item["Key"]] = item
        self.state["ship_results"].update(updates)
        self.state["battles"].append(record)
        self.state["active_battle"] = None
        self.persist()
        return record

    def cancel(self):
        active = self.state.get("active_battle")
        if not active:
            return
        # Explicit cancellation is observed by the bridge; no active game is killed.
        root = ET.Element("Cancellation")
        put(root, "Id", active["id"])
        write_xml(self.exchange / "cancel.xml", root)
        self.state["battles"].append(dict(active, status="cancelled"))
        self.state["active_battle"] = None
        self.persist()
