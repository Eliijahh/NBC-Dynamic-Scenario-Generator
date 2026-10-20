"""Native fleet XML editing and a durable file-based game bridge. No dependencies."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_GAME = Path(r"R:\SteamLibrary\steamapps\common\Nebulous")
DEFAULT_STARTING_CREDITS = 1_000_000
STATE_VERSION = 2
CAMPAIGN_STATUSES = frozenset({"uninitialized", "active", "completed", "abandoned"})
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
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


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



class Campaign:
    """A campaign save with explicit schema defaults and atomic commit boundaries."""

    def __init__(self, game=DEFAULT_GAME, data=None):
        self.game = Path(game)
        self.data = Path(data or ROOT / "data")
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data / "campaign.json"
        self.state = self._default_state()
        had_state_file = self.state_path.exists()
        before = None
        if had_state_file:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Campaign save root is not an object.")
            self.state = loaded
            before = copy.deepcopy(loaded)
            version = self._state_version(loaded)
            if version > STATE_VERSION:
                raise ValueError("Campaign save was created by a newer, unsupported version.")
            migrated = version < STATE_VERSION
            if migrated:
                self.backup_campaign(f"schema-v{version}-to-v{STATE_VERSION}")
            normalized = self._normalize_state()
            if normalized and not migrated:
                self.backup_campaign(f"normalize-v{STATE_VERSION}")
        else:
            self.state = self._default_state()

        from .runtime import GameRuntime
        self.runtime = GameRuntime(self.game, self.exchange)
        self.player = Fleet(ET.fromstring(self.state["player_xml"])) if self.state.get("player_xml") else Fleet.load(self.data / "player.fleet") if (self.data / "player.fleet").exists() else None
        self.enemy = Fleet(ET.fromstring(self.state["enemy_xml"])) if self.state.get("enemy_xml") else Fleet.load(self.data / "enemy.fleet") if (self.data / "enemy.fleet").exists() else None
        self.ensure_legacy_campaign(persist=False)
        self.sync_ship_state()

        if had_state_file and before is not None and self.state != before:
            self.persist()
        elif not had_state_file and self.player is not None:
            self.persist()

    @staticmethod
    def _default_state():
        return {
            "version": STATE_VERSION,
            "campaign": None,
            "campaign_status": "uninitialized",
            "battles": [],
            "ship_results": {},
            "ship_state": {},
            "owned_hulls": {},
            "mission_offers": [],
            "accepted_mission": None,
            "active_battle": None,
            "inventory": {},
            "active_refit": None,
            "refits": [],
        }

    @staticmethod
    def _state_version(state):
        version = state.get("version", 1)
        if type(version) is not int or version < 1:
            raise ValueError("Campaign save version is invalid.")
        return version

    def _normalize_state(self):
        """Add v2 fields, migrate older saves, and reject structurally invalid data."""
        before = copy.deepcopy(self.state)
        state = self.state
        for key, value in self._default_state().items():
            state.setdefault(key, copy.deepcopy(value))
        state["version"] = STATE_VERSION

        for key in ("inventory", "owned_hulls", "ship_state", "ship_results"):
            if not isinstance(state[key], dict):
                raise ValueError(f"Campaign {key} must be an object.")
        for key in ("battles", "mission_offers", "refits"):
            if not isinstance(state[key], list):
                raise ValueError(f"Campaign {key} must be a list.")
        for key in ("active_battle", "active_refit", "accepted_mission"):
            if state[key] is not None and not isinstance(state[key], dict):
                raise ValueError(f"Campaign {key} must be an object or null.")

        meta = state["campaign"]
        if meta is not None:
            if not isinstance(meta, dict):
                raise ValueError("Campaign metadata must be an object or null.")
            credits = meta.setdefault("credits", 0)
            if type(credits) is not int or credits < 0:
                raise ValueError("Campaign credits are invalid.")

        status = state.get("campaign_status")
        if meta is None:
            state["campaign_status"] = "uninitialized"
        elif status not in CAMPAIGN_STATUSES or status == "uninitialized":
            state["campaign_status"] = "active"

        # Generation is offline now. Drop a pending editor-generation request
        # left behind by an older campaign save.
        state.pop("active_generation", None)
        return state != before

    @property
    def exchange(self):
        return self.game / "Saves" / "NBCBridge"

    @property
    def metadata(self):
        return self.state.get("campaign") or {}

    @property
    def has_campaign(self):
        return bool(self.state.get("campaign"))

    @property
    def credits(self):
        if not self.has_campaign:
            return 0
        return int(self.metadata.get("credits", 0))

    @property
    def campaign_status(self):
        return self.state.get("campaign_status", "uninitialized")

    @property
    def accepted_mission(self):
        return self.state.get("accepted_mission")

    def pending_action(self):
        if self.state.get("active_battle"):
            return "battle"
        if self.state.get("active_refit"):
            return "refit"
        if self.state.get("accepted_mission"):
            return "mission"
        return None

    def ensure_idle(self):
        action = self.pending_action()
        if action:
            raise ValueError(f"Finish or cancel the pending {action} first.")

    def ensure_no_launch(self):
        if self.state.get("active_refit"):
            raise ValueError("Finish or cancel the refit before launching a battle.")
        if self.state.get("active_battle"):
            raise ValueError("A battle is already pending. Finish it or cancel it before launching another.")

    @contextmanager
    def transaction(self):
        """Persist a state-only change atomically and restore it if the write fails."""
        previous = copy.deepcopy(self.state)
        try:
            yield self
            self.persist()
        except Exception:
            self.state = previous
            raise

    def persist(self):
        atomic_bytes(self.state_path, json.dumps(self.state, indent=2).encode())

    def mark_played(self):
        """Record that this campaign was opened by the player."""
        if not self.has_campaign:
            return
        with self.transaction():
            self.state["campaign"]["last_played"] = now()

    def set_credits(self, credits):
        if type(credits) is not int or credits < 0:
            raise ValueError("Credits must be a non-negative whole number.")
        if not self.has_campaign:
            raise ValueError("No campaign is loaded.")
        with self.transaction():
            self.state["campaign"]["credits"] = credits

    def set_campaign_status(self, status):
        if status not in CAMPAIGN_STATUSES:
            raise ValueError("Unknown campaign status.")
        if not self.has_campaign and status != "uninitialized":
            raise ValueError("No campaign is loaded.")
        if self.has_campaign and status == "uninitialized":
            raise ValueError("A loaded campaign cannot be uninitialized.")
        with self.transaction():
            self.state["campaign_status"] = status

    def ensure_legacy_campaign(self, persist=True):
        """Give an existing pre-new-campaign save usable campaign metadata."""
        if self.has_campaign:
            return False
        has_existing_state = bool(
            self.player
            or self.state.get("battles")
            or self.state.get("ship_results")
            or self.state.get("inventory")
            or self.state.get("active_battle")
            or self.state.get("active_refit")
            or self.state.get("accepted_mission")
            or self.state.get("mission_offers")
        )
        if not has_existing_state:
            return False
        fleet = self.player
        self.state["campaign"] = {
            "id": str(uuid.uuid4()),
            "name": fleet.name if fleet else "Legacy Campaign",
            "commander": "",
            "faction": fleet.faction if fleet else "",
            "credits": 0,
            "created": now(),
            "started": now(),
            "last_played": now(),
            "starting_fleet": fleet.name if fleet else "",
            "starting_fleet_source": str(fleet.source) if fleet and fleet.source else "",
            "starting_ships": len(fleet.ships) if fleet else 0,
            "starting_points": fleet.points if fleet else 0,
            "legacy": True,
        }
        self.state["campaign_status"] = "active"
        if persist:
            self.persist()
        return True

    def sync_ship_state(self, fleet=None):
        """Keep the per-ship roster state aligned with the current player fleet."""
        previous = self.state.get("ship_state") or {}
        current = {}
        fleet = self.player if fleet is None else fleet
        if fleet:
            for ship in fleet.ships:
                key = ship.findtext("Key")
                if not key:
                    continue
                item = copy.deepcopy(previous.get(key, {}))
                item.setdefault("condition", 1.0)
                item.setdefault("status", "ready")
                current[key] = item
        if current != self.state.get("ship_state"):
            self.state["ship_state"] = current
            return True
        return False

    def backup_campaign(self, reason="new-campaign"):
        """Copy the current campaign's top-level files before an explicit overwrite."""
        files = [self.state_path, self.data / "player.fleet", self.data / "enemy.fleet"]
        existing = [path for path in files if path.exists()]
        if not existing:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_reason = "".join(char if char.isalnum() or char in "-_" else "-" for char in str(reason)).strip("-") or "backup"
        base = f"{stamp}-{safe_reason}"
        directory = self.data / "campaign-backups" / base
        suffix = 2
        while directory.exists():
            directory = self.data / "campaign-backups" / f"{base}-{suffix}"
            suffix += 1
        directory.mkdir(parents=True, exist_ok=False)
        for path in existing:
            shutil.copy2(path, directory / path.name)
        return directory

    def start_new_campaign(self, name, commander, faction, credits, starter, campaign_id=None):
        """Initialize this save directory with a fresh campaign and cloned starter fleet."""
        self.ensure_idle()
        name = str(name).strip()
        commander = str(commander).strip()
        if not name:
            raise ValueError("Enter a campaign name.")
        if len(name) > 80:
            raise ValueError("Campaign name must be 80 characters or fewer.")
        if not commander:
            raise ValueError("Enter a commander name.")
        if len(commander) > 80:
            raise ValueError("Commander name must be 80 characters or fewer.")
        if faction not in ("Stock/Alliance", "Stock/Protectorate"):
            raise ValueError("Choose a stock faction.")
        if type(credits) is not int or not 0 <= credits <= 1_000_000_000:
            raise ValueError("Starting credits must be a whole number from 0 to 1,000,000,000.")
        if starter is None:
            raise ValueError("Choose a starting fleet.")
        if starter.faction != faction:
            raise ValueError("The starting fleet must match the selected faction.")
        starter.validate()
        starter = starter.clone()
        starter.validate()

        self.backup_campaign()
        previous = (copy.deepcopy(self.state), self.player, self.enemy)
        self.state = self._default_state()
        self.state["campaign"] = {
            "id": str(campaign_id or uuid.uuid4()),
            "name": name,
            "commander": commander,
            "faction": faction,
            "credits": credits,
            "created": now(),
            "started": now(),
            "last_played": now(),
            "starting_fleet": starter.name,
            "starting_fleet_source": str(starter.source) if starter.source else "",
            "starting_ships": len(starter.ships),
            "starting_points": starter.points,
            "legacy": False,
        }
        self.state["campaign_status"] = "active"
        self.player = starter
        self.enemy = None
        try:
            enemy_path = self.data / "enemy.fleet"
            if enemy_path.exists():
                enemy_path.unlink()
            self.save_fleets()
        except Exception:
            self.state, self.player, self.enemy = previous
            raise
        return self.metadata

    def save_fleets(self):
        if self.player:
            self.player.validate()
        if self.enemy:
            self.enemy.validate()
        if self.player:
            self.state["player_xml"] = ET.tostring(self.player.root, encoding="unicode")
        if self.enemy:
            self.state["enemy_xml"] = ET.tostring(self.enemy.root, encoding="unicode")
        self.sync_ship_state()
        if self.player or self.enemy or self.has_campaign:
            self.persist()
        if self.player:
            self.player.save(self.data / "player.fleet")
        if self.enemy:
            self.enemy.save(self.data / "enemy.fleet")

    def prepare(self, publish=True):
        self.ensure_no_launch()
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
            write_xml(archive / "request.xml", request)
            if publish:
                write_xml(self.exchange / "request.xml", request)
        except Exception:
            self.state["active_battle"] = None
            self.persist()
            raise
        return record

    def launch(self):
        record = self.prepare(publish=False)
        record["launch_pending"] = True
        self.persist()
        try:
            self.runtime.begin("battle")
        except Exception:
            self.cancel()
            raise
        return record

    def queue_ack(self, result, kind):
        if result.findtext("RuntimeInstance"):
            self.state["pending_ack"] = {"Instance": result.findtext("RuntimeInstance"),
                "Kind": kind, "Id": result.findtext("Id"), "Revision": result.findtext("Revision", "0")}

    def flush_ack(self):
        ack = self.state.get("pending_ack")
        if not ack:
            return
        root = ET.Element("OperationAck")
        for key, value in ack.items():
            put(root, key, value)
        write_xml(self.exchange / "operation-ack.xml", root)
        self.state.pop("pending_ack", None)
        try:
            self.persist()
        except Exception:
            self.state["pending_ack"] = ack
            raise

    def pump_game(self):
        self.flush_ack()
        refit = self.state.get("active_refit")
        battle = self.state.get("active_battle")
        active = refit or battle
        try:
            if active and active.get("launch_pending") and not self.runtime.purpose and not active.get("launch_error"):
                self.runtime.begin("refit" if refit else "battle")
            ready = self.runtime.poll()
        except Exception as exc:
            if active:
                active["launch_error"] = str(exc)
                self.persist()
            raise
        if not ready or not active or not active.get("launch_pending"):
            return
        active["runtime_instance"] = self.runtime.instance
        if refit:
            from .refit import RefitService
            RefitService(self)._request(refit)
        else:
            root = read_xml(Path(active["directory"]) / "request.xml")
            put(root, "RuntimeInstance", self.runtime.instance)
            put(root, "CloseGame", "true")
            write_xml(self.exchange / "request.xml", root)
        active["launch_pending"] = False
        self.persist()
        self.runtime.submitted()

    def bridge_status(self):
        active = self.state.get("active_battle")
        if active and active.get("launch_pending"):
            return active.get("launch_error") or self.runtime.message
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
        with self.transaction():
            self.state["ship_results"].update(updates)
            self.state["battles"].append(record)
            self.state["active_battle"] = None
            self.queue_ack(result, "battle")
        self.flush_ack()
        return record

    def cancel(self):
        active = self.state.get("active_battle")
        if not active:
            return
        self.runtime.cancel_startup()
        # Explicit cancellation is observed by the bridge; no active game is killed.
        root = ET.Element("Cancellation")
        put(root, "Id", active["id"])
        write_xml(self.exchange / "cancel.xml", root)
        with self.transaction():
            self.state["battles"].append(dict(active, status="cancelled"))
            self.state["active_battle"] = None
