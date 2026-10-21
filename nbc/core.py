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
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_GAME = Path(r"R:\SteamLibrary\steamapps\common\Nebulous")
DEFAULT_STARTING_CREDITS = 100_000_000
STATE_VERSION = 2
CAMPAIGN_STATUSES = frozenset({"uninitialized", "active", "completed", "abandoned"})
# A frozen build extracts its data to a temporary bundle directory; source runs use the repo.
ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
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


def battle_condition(value, label="ship condition"):
    """Read a native report fraction and keep it inside the valid range."""
    text = (value or "").strip()
    if not text:
        return 1.0
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"Battle report contains an invalid {label}.") from exc
    return max(0.0, min(1.0, number))


def component_entry(socket, component, health=1.0, destroyed=False):
    """Build one per-component damage record for a hull's saved state."""
    try:
        health = float(health)
    except (TypeError, ValueError):
        health = 0.0
    health = max(0.0, min(1.0, health))
    destroyed = bool(destroyed) or health <= 0.0
    return {
        "socket": str(socket or ""),
        "component": str(component or ""),
        "health": 0.0 if destroyed else health,
        "destroyed": destroyed,
    }


def component_damage(entry):
    """The damaged fraction of one component (a destroyed component is fully damaged)."""
    try:
        health = float(entry.get("health", 1.0) or 0.0)
    except (TypeError, ValueError):
        health = 0.0
    return max(0.0, min(1.0, 1.0 - health))


def component_is_damaged(entry):
    return bool(entry.get("destroyed")) or component_damage(entry) > 0.0


def ship_components(item):
    """The damaged or destroyed component records kept for one hull."""
    components = (item or {}).get("components") or []
    return [entry for entry in components if isinstance(entry, dict)] if isinstance(components, list) else []


def ship_status(item):
    """A ship is only ever ready or damaged; damage itself lives on components."""
    return "damaged" if ship_components(item) else "ready"


def ship_damage_fraction(ship, item):
    """Average damage across a hull's installed components (destroyed counts fully).

    Ten components with one at 90% damage therefore read as 9% damaged.
    """
    sockets = {
        (socket.findtext("Key") or "").strip()
        for socket in ship.findall("SocketMap/HullSocket")
    }
    sockets.discard("")
    if not sockets:
        return 0.0
    total = sum(
        component_damage(entry)
        for entry in ship_components(item)
        if (entry.get("socket") or "").strip() in sockets
    )
    return max(0.0, min(1.0, total / len(sockets)))


def ship_state_item(components):
    """Assemble a saved ship state from its damaged or destroyed components."""
    kept = []
    for entry in components:
        if not isinstance(entry, dict):
            continue
        normalized = component_entry(
            entry.get("socket"),
            entry.get("component"),
            entry.get("health", 1.0),
            entry.get("destroyed"),
        )
        if component_is_damaged(normalized):
            kept.append(normalized)
    kept.sort(key=lambda entry: entry.get("socket", ""))
    item = {"status": "damaged" if kept else "ready"}
    if kept:
        item["components"] = kept
    return item


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

    def validate(self, max_ships=10, allow_empty=False):
        count = len(self.ships)
        if count == 0 and not allow_empty:
            raise ValueError("A fleet must contain at least one ship.")
        if max_ships is not None and count > max_ships:
            raise ValueError(f"A fleet can contain at most {max_ships} ships.")
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

    def save(self, path, max_ships=10, allow_empty=False):
        self.validate(max_ships=max_ships, allow_empty=allow_empty)
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
        migrated = normalized = False
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
        self.deployment = Fleet(ET.fromstring(self.state["deployment_xml"])) if self.state.get("deployment_xml") else None
        self.ensure_legacy_campaign(persist=False)
        seeded_owned = False
        if self.state.get("owned_hulls"):
            self.player = self._player_from_owned_hulls(self.player)
        elif self.player is not None:
            seeded_owned = self.sync_owned_hulls(self.player)
        self.sync_ship_state()

        if had_state_file and before is not None and self.state != before:
            # Existing v2 saves that only needed their roster migrated into owned
            # hulls get their own backup before the new shape is written.
            if seeded_owned and not migrated and not normalized:
                self.backup_campaign("owned-hulls")
            self.persist()
        elif not had_state_file and self.player is not None:
            self.persist()
        if self.player is not None:
            self.player.save(self.data / "player.fleet", max_ships=None, allow_empty=True)

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
            "mission_meta": {},
            "accepted_mission": None,
            "deployment_xml": "",
            "active_battle": None,
            "inventory": {},
            "credit_ledger": [],
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

        for key in ("inventory", "owned_hulls", "ship_state", "ship_results", "mission_meta"):
            if not isinstance(state[key], dict):
                raise ValueError(f"Campaign {key} must be an object.")
        for key, record in state["owned_hulls"].items():
            if not isinstance(record, dict) or not isinstance(record.get("ship", ""), str):
                raise ValueError("Each owned hull must contain its ship XML.")
            uuid.UUID(str(key))
        for key, item in list(state["ship_state"].items()):
            if not isinstance(item, dict):
                raise ValueError("Each ship state must be an object.")
            state["ship_state"][key] = self._migrate_ship_state_item(item)
        for key in ("battles", "mission_offers", "refits", "credit_ledger"):
            if not isinstance(state[key], list):
                raise ValueError(f"Campaign {key} must be a list.")
        if not isinstance(state.get("deployment_xml", ""), str):
            raise ValueError("Campaign deployment fleet must be text.")
        for entry in state["credit_ledger"]:
            if not isinstance(entry, dict):
                raise ValueError("Campaign credit ledger entries must be objects.")
            if type(entry.get("amount")) is not int or type(entry.get("balance")) is not int:
                raise ValueError("Campaign credit ledger amounts must be whole numbers.")
            if entry["balance"] < 0:
                raise ValueError("Campaign credit ledger balance cannot be negative.")
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

    @staticmethod
    def _migrate_ship_state_item(item):
        """Convert a saved ship state to the per-component damage shape.

        Older saves stored a whole-ship ``condition`` fraction plus a list of
        ``destroyed_components``.  Destroyed records carry over; a partial
        ``condition`` cannot be attributed to individual components and is
        dropped, leaving the ship ready.
        """
        if "components" in item:
            return ship_state_item(ship_components(item))
        components = [
            component_entry(record.get("socket"), record.get("component"), 0.0, True)
            for record in (item.get("destroyed_components") or [])
            if isinstance(record, dict) and record.get("socket") and record.get("component")
        ]
        return ship_state_item(components)

    def _new_roster_root(self):
        root = ET.Element("Fleet")
        put(root, "Name", f"{self.metadata.get('name', 'Campaign')} Roster")
        put(root, "Version", 3)
        put(root, "FactionKey", self.metadata.get("faction", ""))
        ET.SubElement(root, "ModDependencies")
        ET.SubElement(root, "Ships")
        ET.SubElement(root, "MissileTypes")
        ET.SubElement(root, "CraftTypes")
        return root

    def _player_from_owned_hulls(self, envelope=None):
        """Rebuild the roster from the authoritative owned-hull records."""
        root = copy.deepcopy(envelope.root) if envelope is not None else self._new_roster_root()
        container = root.find("Ships")
        if container is None:
            container = ET.SubElement(root, "Ships")
        for child in list(container):
            container.remove(child)
        for key, record in self.state["owned_hulls"].items():
            try:
                ship = ET.fromstring(record.get("ship", ""))
            except ET.ParseError as exc:
                raise ValueError(f"Owned hull {key} has invalid ship XML.") from exc
            put(ship, "Key", key)
            put(ship, "Name", record.get("name", ship.findtext("Name", "")))
            put(ship, "Number", record.get("number", ship.findtext("Number", "0")))
            put(ship, "HullType", record.get("hull", ship.findtext("HullType", "")))
            container.append(ship)
        player = Fleet(root)
        player.validate(max_ships=None, allow_empty=True)
        return player

    def sync_owned_hulls(self, fleet=None, sources=None, prices=None):
        """Mirror the current roster into the owned-hull records with stable IDs."""
        fleet = self.player if fleet is None else fleet
        previous = self.state.get("owned_hulls") or {}
        sources = sources or {}
        prices = prices or {}
        current = {}
        for ship in fleet.ships if fleet else ():
            key = ship.findtext("Key")
            uuid.UUID(key or "")
            try:
                number = int(ship.findtext("Number", "0") or 0)
            except ValueError:
                number = 0
            record = dict(previous.get(key, {}))
            record.update(
                {
                    "faction": fleet.faction,
                    "hull": ship.findtext("HullType", ""),
                    "name": ship.findtext("Name", ""),
                    "number": number,
                    "ship": ET.tostring(ship, encoding="unicode"),
                    "acquired": record.get("acquired") or now(),
                    "source": sources.get(key, record.get("source", "roster")),
                    "price": prices.get(key, record.get("price", 0)),
                }
            )
            current[key] = record
        if current != self.state.get("owned_hulls"):
            self.state["owned_hulls"] = current
            return True
        return False

    def _commit_player(self, player, inventory=None, sources=None, prices=None,
                       credit_delta=0, ledger_kind="adjustment", ledger_description="",
                       ledger_details=None, state_updates=None):
        """Replace the roster and persist state as one rollback-safe change."""
        before = copy.deepcopy(self.state)
        previous = self.player
        self.player = player
        if inventory is not None:
            self.state["inventory"] = inventory
        if state_updates:
            for key, value in state_updates.items():
                self.state[key] = value
        try:
            if credit_delta:
                self.adjust_credits(
                    credit_delta,
                    ledger_kind,
                    ledger_description or ledger_kind.replace("-", " ").title(),
                    ledger_details,
                )
            self.save_fleets(sources=sources, prices=prices)
        except Exception:
            self.state = before
            self.player = previous
            raise

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
    def credit_ledger(self):
        return list(self.state.get("credit_ledger") or [])

    @property
    def accepted_mission(self):
        return self.state.get("accepted_mission")

    def pending_action(self):
        """Return the operation that blocks the campaign, if any.

        An accepted mission is deliberately not included: accepting a mission
        must not stop refits, repairs, purchases or roster edits.
        """
        if self.state.get("active_battle"):
            return "battle"
        if self.state.get("active_refit"):
            return "refit"
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

    def _append_credit_entry(self, amount, kind, description, details=None):
        self.state.setdefault("credit_ledger", []).append(
            {
                "id": str(uuid.uuid4()),
                "at": now(),
                "amount": int(amount),
                "balance": self.credits,
                "kind": str(kind),
                "description": str(description),
                "details": copy.deepcopy(details or {}),
            }
        )

    def adjust_credits(self, amount, kind, description, details=None):
        """Apply one integer wallet change and append it to the audit trail."""
        if type(amount) is not int:
            raise ValueError("Credit changes must be whole numbers.")
        if not self.has_campaign:
            raise ValueError("No campaign is loaded.")
        balance = self.credits + amount
        if balance < 0:
            raise ValueError(
                f"Not enough credits. This costs {abs(amount):,} credits; "
                f"your balance is {self.credits:,}."
            )
        self.state["campaign"]["credits"] = balance
        self._append_credit_entry(amount, kind, description, details)
        return balance

    def set_credits(self, credits):
        if type(credits) is not int or credits < 0:
            raise ValueError("Credits must be a non-negative whole number.")
        if not self.has_campaign:
            raise ValueError("No campaign is loaded.")
        previous = self.credits
        delta = credits - previous
        with self.transaction():
            if delta:
                self.adjust_credits(
                    delta,
                    "adjustment",
                    "Credits set directly",
                    {"previous": previous, "new": credits},
                )

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
        """Keep per-component damage aligned with the current player fleet.

        A record survives only while its socket still holds the same component,
        so refitting clears damage automatically.  A hull with no damaged or
        destroyed components is ready.
        """
        previous = self.state.get("ship_state") or {}
        current = {}
        fleet = self.player if fleet is None else fleet
        if fleet:
            for ship in fleet.ships:
                key = ship.findtext("Key")
                if not key:
                    continue
                sockets = {
                    (socket.findtext("Key") or "").strip(): (socket.findtext("ComponentName") or "").strip()
                    for socket in ship.findall("SocketMap/HullSocket")
                }
                kept = [
                    component_entry(
                        entry.get("socket"),
                        entry.get("component"),
                        entry.get("health", 1.0),
                        entry.get("destroyed"),
                    )
                    for entry in ship_components(previous.get(key))
                    if sockets.get((entry.get("socket") or "").strip())
                    == (entry.get("component") or "").strip()
                ]
                current[key] = ship_state_item(kept)
        if current != self.state.get("ship_state"):
            self.state["ship_state"] = current
            return True
        return False

    def ship_ready(self, key):
        """A ship can deploy only when none of its components are damaged or destroyed."""
        item = (self.state.get("ship_state") or {}).get(key) or {}
        return ship_status(item) == "ready"

    def unready_ships(self):
        if not self.player:
            return []
        return [ship for ship in self.player.ships if not self.ship_ready(ship.findtext("Key"))]

    def can_deploy(self):
        """The game accepts at most ten ready ships in one exported fleet."""
        return bool(
            self.player
            and 1 <= len(self.player.ships) <= 10
            and not self.unready_ships()
        )

    def add_owned_hull(self, faction, ship, name=None, number=None, source="purchase",
                       price=0, debit=0, ledger_description=None, ledger_details=None):
        """Add an empty, uniquely identified hull to the campaign roster."""
        self.ensure_idle()
        if not self.has_campaign:
            raise ValueError("Create or load a campaign first.")
        if faction != self.metadata.get("faction"):
            raise ValueError("Hulls must match the campaign faction.")
        if type(price) is not int or price < 0:
            raise ValueError("Hull price must be a non-negative whole number.")
        if type(debit) is not int or debit < 0:
            raise ValueError("Hull charge must be a non-negative whole number.")
        ship = copy.deepcopy(ship)
        key = ship.findtext("Key") or str(uuid.uuid4())
        try:
            uuid.UUID(key)
        except ValueError as exc:
            raise ValueError("The new hull needs a valid unique identity.") from exc
        if self.player and any(s.findtext("Key") == key for s in self.player.ships):
            raise ValueError("A hull with that identity is already owned.")
        if not ship.findtext("HullType"):
            raise ValueError("Choose a hull type.")
        if ship.find("SocketMap") is None:
            ET.SubElement(ship, "SocketMap")
        if ship.findall("SocketMap/HullSocket"):
            raise ValueError("Purchased hulls start empty with no components.")
        if ship.find("ModDependencies") is None:
            ET.SubElement(ship, "ModDependencies")

        name = str(name if name is not None else ship.findtext("Name", "")).strip()
        if not name:
            raise ValueError("Enter a name for the new hull.")
        existing = self.player.ships if self.player else ()
        if any(s.findtext("Name", "").strip().casefold() == name.casefold() for s in existing):
            raise ValueError("Hull names must be unique in the campaign.")
        if number is None:
            number = ship.findtext("Number", "0")
        try:
            number = int(number)
        except (TypeError, ValueError) as exc:
            raise ValueError("Hull number must be a whole number.") from exc
        if not 0 <= number <= 2_147_483_647:
            raise ValueError("Hull number must be a non-negative integer.")
        if any(int(s.findtext("Number", "0") or 0) == number for s in existing):
            raise ValueError("Hull numbers must be unique in the campaign.")

        put(ship, "Key", key)
        put(ship, "Name", name)
        put(ship, "Number", number)
        player = self.player.clone() if self.player else Fleet(self._new_roster_root())
        player.root.find("Ships").append(ship)
        player.validate(max_ships=None)
        self._commit_player(
            player,
            sources={key: source},
            prices={key: price},
            credit_delta=-debit,
            ledger_kind="hull-purchase",
            ledger_description=ledger_description or f"Bought hull {name}",
            ledger_details=ledger_details or {"hull": ship.findtext("HullType", ""), "ship": key},
        )
        return key

    def rename_owned_hull(self, key, name, number):
        """Rename one owned hull without changing its identity or loadout."""
        self.ensure_idle()
        if not self.player:
            raise ValueError("No campaign roster is loaded.")
        player = self.player.clone()
        ship = next((s for s in player.ships if s.findtext("Key") == key), None)
        if ship is None:
            raise ValueError("That hull is not in the campaign roster.")
        name = str(name).strip()
        if not name:
            raise ValueError("Enter a name for the hull.")
        if any(s is not ship and s.findtext("Name", "").strip().casefold() == name.casefold() for s in player.ships):
            raise ValueError("Hull names must be unique in the campaign.")
        try:
            number = int(number)
        except (TypeError, ValueError) as exc:
            raise ValueError("Hull number must be a whole number.") from exc
        if not 0 <= number <= 2_147_483_647:
            raise ValueError("Hull number must be a non-negative integer.")
        if any(s is not ship and int(s.findtext("Number", "0") or 0) == number for s in player.ships):
            raise ValueError("Hull numbers must be unique in the campaign.")
        put(ship, "Name", name)
        put(ship, "Number", number)
        player.validate(max_ships=None, allow_empty=True)
        self._commit_player(player)

    def sell_owned_hull(self, key, refund=0, ledger_description=None, ledger_details=None):
        """Remove a hull, returning its installed components to spare inventory."""
        self.ensure_idle()
        if not self.player:
            raise ValueError("No campaign roster is loaded.")
        if type(refund) is not int or refund < 0:
            raise ValueError("Hull refund must be a non-negative whole number.")
        player = self.player.clone()
        ship = next((s for s in player.ships if s.findtext("Key") == key), None)
        if ship is None:
            raise ValueError("That hull is not in the campaign roster.")
        name = ship.findtext("Name", "")
        hull = ship.findtext("HullType", "")
        returned = {}
        for socket in ship.findall("SocketMap/HullSocket"):
            component = socket.findtext("ComponentName", "").strip()
            if component:
                returned[component] = returned.get(component, 0) + 1
        player.root.find("Ships").remove(ship)
        player.validate(max_ships=None, allow_empty=True)
        inventory = dict(self.state.get("inventory") or {})
        for component, count in returned.items():
            inventory[component] = inventory.get(component, 0) + count
        self._commit_player(
            player,
            inventory=inventory,
            credit_delta=refund,
            ledger_kind="hull-sale",
            ledger_description=ledger_description or f"Sold hull {name}",
            ledger_details=ledger_details or {"hull": hull, "ship": key},
        )
        return returned

    def scrap_installed_component(self, ship_key, socket_key):
        """Permanently discard an installed or already-destroyed component."""
        self.ensure_idle()
        if not self.player:
            raise ValueError("No campaign roster is loaded.")
        player = self.player.clone()
        ship = next((s for s in player.ships if s.findtext("Key") == ship_key), None)
        if ship is None:
            raise ValueError("That hull is not in the campaign roster.")
        state = copy.deepcopy(self.state.get("ship_state") or {})
        item = dict(state.get(ship_key) or {})
        records = ship_components(item)
        record = next((entry for entry in records if entry.get("socket") == socket_key), None)
        socket = next(
            (s for s in ship.findall("SocketMap/HullSocket") if s.findtext("Key") == socket_key),
            None,
        )
        if socket is not None:
            component = (socket.findtext("ComponentName") or "").strip()
            if not component:
                raise ValueError("That equipment socket is already empty.")
            ship.find("SocketMap").remove(socket)
        elif record is not None:
            component = (record.get("component") or "").strip()
        else:
            raise ValueError("That equipment socket is no longer installed.")
        remaining = [entry for entry in records if entry.get("socket") != socket_key]
        state[ship_key] = ship_state_item(remaining)
        player.validate(max_ships=None, allow_empty=True)
        self._commit_player(player, state_updates={"ship_state": state})
        return component

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
        previous = (copy.deepcopy(self.state), self.player, self.enemy, self.deployment)
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
        self.deployment = None
        if credits:
            self._append_credit_entry(
                credits,
                "campaign-start",
                "Starting credits",
                {"campaign": name},
            )
        try:
            enemy_path = self.data / "enemy.fleet"
            if enemy_path.exists():
                enemy_path.unlink()
            self.save_fleets()
        except Exception:
            self.state, self.player, self.enemy, self.deployment = previous
            raise
        return self.metadata

    def save_fleets(self, sources=None, prices=None):
        if self.player:
            self.player.validate(max_ships=None, allow_empty=True)
        if self.enemy:
            self.enemy.validate()
        if self.player:
            self.sync_owned_hulls(sources=sources, prices=prices)
            self.state["player_xml"] = ET.tostring(self.player.root, encoding="unicode")
        else:
            self.state["owned_hulls"] = {}
            self.state["player_xml"] = ""
        if self.enemy:
            self.state["enemy_xml"] = ET.tostring(self.enemy.root, encoding="unicode")
        self.sync_ship_state()
        if self.player or self.enemy or self.has_campaign:
            self.persist()
        if self.player:
            self.player.save(self.data / "player.fleet", max_ships=None, allow_empty=True)
        if self.enemy:
            self.enemy.save(self.data / "enemy.fleet")

    def prepare(self, publish=True):
        self.ensure_no_launch()
        fleet = self.deployment or self.player
        if not fleet or not self.enemy:
            raise ValueError("Load a player fleet and generate an enemy first.")
        if not 1 <= len(fleet.ships) <= 10:
            raise ValueError("A battle export requires 1–10 ships. Select a mission deployment of ten or fewer hulls first.")
        unready = [
            ship for ship in fleet.ships
            if not self.ship_ready(ship.findtext("Key"))
        ]
        if unready:
            names = ", ".join(ship.findtext("Name", "Unnamed hull") for ship in unready)
            raise ValueError(
                f"Repair damaged ships or scrap destroyed components before deployment: {names}."
            )
        self.save_fleets()
        battle_id = str(uuid.uuid4())
        archive = self.data / "battles" / battle_id
        archive.mkdir(parents=True)
        exports = self.game / "Saves" / "Fleets" / "NBC-POC"
        p, e = exports / "NBC Player.fleet", exports / "NBC Enemy.fleet"
        player = fleet.clone()
        put(player.root, "Name", "NBC Player")
        player.save(p)
        self.enemy.save(e)
        player.save(archive / "player.fleet")
        self.enemy.save(archive / "enemy.fleet")
        mission = self.state.get("accepted_mission") or {}
        request = ET.Element("BattleRequest", version="1")
        for key, value in {"Id": battle_id, "CreatedUtc": now(), "PlayerFleet": str(p), "EnemyFleet": str(e), "ResultDirectory": str(archive.resolve()), "PlayerHash": hashlib.sha256(p.read_bytes()).hexdigest(), "EnemyHash": hashlib.sha256(e.read_bytes()).hexdigest(), "Map": mission.get("map", ""), "MissionType": mission.get("type", "")}.items():
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

    @staticmethod
    def _report_parts(report, names):
        """Map local-player part damage by ship name and archived socket key."""
        if report is None:
            return {}
        names = {name for name in names if name}
        parts = {}
        for player in report.findall(".//AARPlayerReportOfShipBattleReportCraftBattleReport"):
            if (player.findtext("IsLocalPlayer") or "").strip().casefold() != "true":
                continue
            for ship in player.findall("Ships/ShipBattleReport"):
                name = (ship.findtext("ShipName") or "").strip()
                if name not in names:
                    continue
                status = parts.setdefault(name, {})
                for part in ship.findall("PartStatus/PartDamage"):
                    key = (part.findtext("Key") or "").strip()
                    if not key:
                        continue
                    health = battle_condition(part.findtext("HealthPercent"), "component condition")
                    destroyed = (
                        (part.findtext("IsDestroyed") or "").strip().casefold() in {"true", "1"}
                        or health <= 0
                    )
                    status[key] = {"health": health, "destroyed": destroyed}
        return parts

    def _powerplant_keys(self):
        """Return catalogue component keys that count as reactors."""
        path = self.exchange / "fitting-catalog.xml"
        if not path.exists():
            return set()
        from .fitting import Catalog
        catalog = Catalog.load(path)
        return {
            component.key
            for faction in catalog.factions.values()
            for component in faction.components.values()
            if component.is_powerplant
        }

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
        report_path = Path(active["directory"]) / "report.xml"
        report = None
        if report_path.exists():
            report = read_xml(report_path)
            if report.tag != "FullAfterActionReport":
                raise ValueError("Battle report file is not a full after-action report.")
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

        if report is None and any(battle_condition(item.get("Condition")) < 1.0 for item in updates.values()):
            raise ValueError("The battle report is missing, so component damage cannot be imported.")
        report_parts = self._report_parts(report, {item.get("Name", "") for item in updates.values()})
        powerplants = self._powerplant_keys()
        ship_state = copy.deepcopy(self.state.get("ship_state") or {})
        player = self.player.clone() if self.player else None
        lost = set()
        lost_ships = []
        for key, item in updates.items():
            archived = next((s for s in roster.ships if s.findtext("Key") == key), None)
            sockets = {}
            if archived is not None:
                for node in archived.findall("SocketMap/HullSocket"):
                    socket_key = (node.findtext("Key") or "").strip()
                    if socket_key:
                        sockets[socket_key] = (node.findtext("ComponentName") or "").strip()
            parts = report_parts.get((item.get("Name") or "").strip(), {})
            battle_condition(item.get("Condition"))
            components = []
            for socket_key, component in sockets.items():
                part = parts.get(socket_key)
                if part is None or not (part["destroyed"] or part["health"] < 1.0):
                    continue
                components.append(
                    component_entry(socket_key, component, part["health"], part["destroyed"])
                )
            reactors = {socket_key for socket_key, component in sockets.items() if component in powerplants}
            reactor_lost = bool(reactors) and all(
                parts.get(socket_key, {}).get("destroyed") for socket_key in reactors
            )
            eliminated = (item.get("Status") or "").strip().casefold() in {"destroyed", "true", "1", "eliminated"}
            if eliminated or reactor_lost:
                lost.add(key)
                lost_ships.append(
                    {
                        "key": key,
                        "name": item.get("Name")
                        or (archived.findtext("Name", "") if archived is not None else ""),
                        "hull": (archived.findtext("HullType", "") if archived is not None else ""),
                        "reason": "destroyed" if eliminated else "reactor",
                        "components": [
                            {"socket": socket_key, "component": component}
                            for socket_key, component in sorted(sockets.items())
                            if component
                        ],
                    }
                )
                ship_state.pop(key, None)
                continue
            ship_state[key] = ship_state_item(components)
        record["lost_ships"] = lost_ships
        if player is not None:
            container = player.root.find("Ships")
            for ship in list(container.findall("Ship")):
                if (ship.findtext("Key") or "").strip() in lost:
                    container.remove(ship)
            player.validate(max_ships=None, allow_empty=True)

        before = (copy.deepcopy(self.state), self.player, self.enemy, self.deployment)
        self.player = player
        mission = None
        try:
            self.state["ship_results"].update(updates)
            self.state["battles"].append(record)
            self.state["active_battle"] = None
            self.state["ship_state"] = ship_state
            mission = self.state.get("accepted_mission")
            if mission:
                record["mission"] = self._resolve_mission(mission, record.get("outcome", ""))
                self.state["accepted_mission"] = None
                self.state["deployment_xml"] = ""
                self.state["enemy_xml"] = ""
                self.enemy = None
                self.deployment = None
            self.queue_ack(result, "battle")
            self.save_fleets()
        except Exception:
            self.state, self.player, self.enemy, self.deployment = before
            raise
        if mission:
            try:
                (self.data / "enemy.fleet").unlink()
            except OSError:
                pass
        self.flush_ack()
        return record

    def _resolve_mission(self, mission, outcome):
        """Mark a completed mission and pay its reward exactly once."""
        won = (outcome or "").strip().casefold() == "victory"
        reward = int(mission.get("reward", 0)) if won else 0
        if reward and self.state.get("campaign") is not None:
            self.state["campaign"]["credits"] = self.credits + reward
            self._append_credit_entry(
                reward,
                "mission-reward",
                f"Mission reward: {mission.get('name', 'Mission')}",
                {"mission": mission.get("id"), "outcome": outcome},
            )
        return {
            "id": mission.get("id"),
            "name": mission.get("name"),
            "type": mission.get("type"),
            "map": mission.get("map"),
            "difficulty": mission.get("difficulty"),
            "status": "completed" if won else "failed",
            "outcome": outcome,
            "reward": int(mission.get("reward", 0)),
            "payout": reward,
        }

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
