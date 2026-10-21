"""Inventory and refit transactions. The campaign JSON is the commit boundary."""
from collections import Counter
import copy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import uuid

from .core import Fleet, now, put, read_xml, write_xml
from .economy import EconomyService


def installed(fleet):
    return Counter(s.findtext("ComponentName") for ship in fleet.ships
                   for s in ship.findall("SocketMap/HullSocket") if s.findtext("ComponentName"))


def reconcile(baseline, draft, spares):
    baseline.validate()
    draft.validate()
    original = {s.findtext("Key"): s for s in baseline.ships}
    proposed = {s.findtext("Key"): s for s in draft.ships}
    if baseline.faction != draft.faction or original.keys() != proposed.keys():
        raise ValueError("Refits must preserve the fleet faction and every ship identity.")
    for key, ship in proposed.items():
        old = original[key]
        if ship.findtext("HullType") != old.findtext("HullType"):
            raise ValueError("Changing hulls is outside this refit session.")
        sockets = [s.findtext("Key") for s in ship.findall("SocketMap/HullSocket")]
        # Native saves omit empty sockets; new keys require a game-exported socket map.
        # Actual compatibility is checked by the game's fitter, not copied into Python.
        if len(sockets) != len(set(sockets)) or any(not s for s in sockets):
            raise ValueError("Refit contains duplicate or invalid socket identities.")
    supply = installed(baseline)
    supply.update(spares)
    used = installed(draft)
    missing = {k: n - supply[k] for k, n in used.items() if n > supply[k]}
    if missing:
        raise ValueError("Inventory exceeded: " + ", ".join(f"{k} (+{n})" for k, n in missing.items()))
    return {k: supply[k] - used[k] for k in sorted(supply) if supply[k] - used[k] > 0}


class RefitService:
    def __init__(self, campaign):
        self.c = campaign

    @property
    def active(self):
        return self.c.state.get("active_refit")

    def catalog(self):
        path = self.c.exchange / "catalog.xml"
        if not path.exists():
            return {}
        root = read_xml(path)
        if root.tag != "ComponentCatalog":
            raise ValueError("Invalid game component catalogue.")
        result = {}
        for node in root.findall("Component"):
            if not node.findtext("Key"):
                continue
            item = {x.tag: x.text or "" for x in node}
            # Accept older bridge exports containing a localization token's fallback text.
            if item.get("Name", "").startswith("$") and "|" in item["Name"]:
                item["Name"] = item["Name"].split("|")[1]
            result[item["Key"]] = item
        return result

    def ensure_idle(self):
        self.c.ensure_idle()

    def trade(self, key, quantity, buy=True):
        self.ensure_idle()
        economy = EconomyService(self.c)
        if buy:
            return economy.buy_component(key, quantity)
        return economy.sell_component(key, quantity)

    def start(self, launch=True, ship_keys=None):
        self.ensure_idle()
        if not self.c.player or not self.c.player.ships:
            raise ValueError("Add or load a campaign hull first.")
        self.c.save_fleets()
        baseline = self.c.player.clone()
        keys = None
        if ship_keys is not None:
            keys = list(dict.fromkeys(str(key) for key in ship_keys))
            if not keys:
                raise ValueError("Select at least one hull to refit.")
            container = baseline.root.find("Ships")
            for ship in list(baseline.ships):
                if ship.findtext("Key") not in keys:
                    container.remove(ship)
            if len(baseline.ships) != len(keys):
                raise ValueError("The selected hull is no longer in the campaign roster.")
        baseline.validate()
        session_id = str(uuid.uuid4())
        directory = self.c.data / "refits" / session_id
        directory.mkdir(parents=True)
        baseline.save(directory / "baseline.fleet")
        baseline.save(directory / "input-1.fleet")
        record = {"id": session_id, "directory": str(directory.resolve()), "revision": 1,
                  "stage": "editor", "created": now(), "inventory": dict(self.c.state["inventory"]),
                  "ship_keys": keys}
        self.c.state["active_refit"] = record
        self.c.persist()
        try:
            if launch:
                record["launch_pending"] = True
                self.c.persist()
                self.c.runtime.begin("refit")
            else:
                self._request(record)
        except Exception:
            self.cancel()
            raise
        return record

    def open_game(self):
        self.ensure_idle()
        self.c.runtime.begin("catalog")

    def _request(self, record):
        folder = Path(record["directory"])
        baseline = Fleet.load(folder / "baseline.fleet")
        supply = installed(baseline)
        supply.update(record["inventory"])
        root = ET.Element("RefitRequest", version="1")
        source = folder / f"input-{record['revision']}.fleet"
        for k, v in {"Id": record["id"], "Revision": record["revision"], "Fleet": str(source),
                     "Hash": hashlib.sha256(source.read_bytes()).hexdigest(), "Directory": str(folder)}.items():
            put(root, k, v)
        if record.get("runtime_instance"):
            put(root, "RuntimeInstance", record["runtime_instance"])
            put(root, "CloseGame", "true")
        items = ET.SubElement(root, "Supply")
        for key, count in sorted(supply.items()):
            ET.SubElement(items, "Item", key=key, quantity=str(count))
        write_xml(self.c.exchange / "refit-request.xml", root)

    def collect(self):
        record = self.active
        if not record or record["stage"] == "review":
            return False
        marker = Path(record["directory"]) / f"ready-{record['revision']}.xml"
        if not marker.exists():
            return False
        doc = read_xml(marker)
        if doc.tag != "RefitReady" or doc.findtext("Id") != record["id"] or doc.findtext("Revision") != str(record["revision"]):
            raise ValueError("Refit result identity mismatch.")
        if doc.findtext("Cancelled") == "true":
            self.cancel()
            return True
        self.review()
        with self.c.transaction():
            record["stage"] = "review"
            self.c.queue_ack(doc, "refit")
        self.c.flush_ack()
        return True

    def review(self):
        if not self.active:
            raise ValueError("There is no active refit.")
        record = self.active
        folder = Path(record["directory"])
        marker = read_xml(folder / f"ready-{record['revision']}.xml")
        if marker.tag != "RefitReady" or marker.findtext("Id") != record["id"] or marker.findtext("Revision") != str(record["revision"]):
            raise ValueError("Refit result identity mismatch.")
        path = folder / f"draft-{record['revision']}.fleet"
        if hashlib.sha256(path.read_bytes()).hexdigest() != marker.findtext("Hash"):
            raise ValueError("Refit draft changed after export; return to the editor.")
        baseline, draft = Fleet.load(folder / "baseline.fleet"), Fleet.load(path)
        inventory = reconcile(baseline, draft, record["inventory"])
        changes = []
        labels = {(n.get("ship"), n.get("key")): n.get("name", "Equipment socket") for n in marker.findall("Sockets/Socket")}
        before = {s.findtext("Key"): s for s in baseline.ships}
        for ship in draft.ships:
            old = before[ship.findtext("Key")]
            a = {s.findtext("Key"): s.findtext("ComponentName", "") for s in old.findall("SocketMap/HullSocket")}
            b = {s.findtext("Key"): s.findtext("ComponentName", "") for s in ship.findall("SocketMap/HullSocket")}
            for socket in sorted(a.keys() | b.keys()):
                if a.get(socket, "") != b.get(socket, ""):
                    changes.append((ship.findtext("Name"), labels.get((ship.findtext("Key"), socket), "Equipment socket"), a.get(socket) or "Empty", b.get(socket) or "Empty"))
            if old.findtext("Name") != ship.findtext("Name") or old.findtext("Number") != ship.findtext("Number"):
                changes.append((ship.findtext("Name"), "Identity", old.findtext("Name"), ship.findtext("Name")))
        return draft, inventory, changes

    def _merge_draft(self, draft):
        """Apply a selected-hull refit draft back onto the full campaign roster."""
        if not self.c.player:
            raise ValueError("The campaign roster is no longer available.")
        current = self.c.player.clone()
        container = current.root.find("Ships")
        proposed = {ship.findtext("Key"): ship for ship in draft.ships}
        replaced = set()
        for index, old in enumerate(list(current.ships)):
            key = old.findtext("Key")
            if key in proposed:
                container.remove(old)
                container.insert(index, copy.deepcopy(proposed[key]))
                replaced.add(key)
        if replaced != set(proposed):
            raise ValueError("Refit result contains a hull outside the campaign roster.")
        current.validate(max_ships=None, allow_empty=True)
        return current

    def confirm(self):
        if not self.active or self.active["stage"] != "review":
            raise ValueError("Return from the game editor before confirming.")
        draft, inventory, changes = self.review()
        record = dict(self.active, stage="confirmed", completed=now(), changes=len(changes))
        merged = self._merge_draft(draft)
        with self.c.transaction():  # Fleet, inventory and session completion commit in one replacement.
            self.c.state["inventory"] = inventory
            self.c.state["player_xml"] = ET.tostring(merged.root, encoding="unicode")
            self.c.state["active_refit"] = None
            self.c.state["refits"].append(record)
            self.c.sync_ship_state(merged)
            self.c.sync_owned_hulls(merged)
        self.c.player = merged
        merged.save(self.c.data / "player.fleet", max_ships=None, allow_empty=True)  # Derived export; JSON remains authoritative.
        return changes

    def resume(self, launch=True):
        if not self.active:
            raise ValueError("There is no refit to resume.")
        record = self.active
        if launch and self.c.runtime.purpose:
            raise ValueError("NEBULOUS is already starting. Wait for the editor or cancel the refit.")
        folder = Path(record["directory"])
        if record["stage"] == "review":
            draft, _, _ = self.review()
        else:
            draft = Fleet.load(folder / f"input-{record['revision']}.fleet")
        resume_instance = record.get("runtime_instance") if record["stage"] == "editor" else None
        record["revision"] += 1
        draft.save(folder / f"input-{record['revision']}.fleet")
        record["stage"] = "editor"
        self.c.persist()
        record.pop("launch_error", None)
        if launch:
            record["launch_pending"] = True
            self.c.persist()
            self.c.runtime.begin("refit", resume_instance=resume_instance)
        else:
            self._request(record)

    def cancel(self):
        if not self.active:
            return
        self.c.runtime.cancel_startup()
        record = self.active
        ack = None
        if record.get("runtime_instance"):
            ack = ET.Element("RefitReady")
            for k, v in {"RuntimeInstance": record["runtime_instance"], "Id": record["id"], "Revision": record["revision"]}.items():
                put(ack, k, v)
        root = ET.Element("RefitCancellation")
        put(root, "Id", self.active["id"])
        write_xml(self.c.exchange / "refit-cancel.xml", root)
        with self.c.transaction():
            if ack is not None:
                self.c.queue_ack(ack, "refit")
            self.c.state["refits"].append(dict(self.active, stage="cancelled", completed=now()))
            self.c.state["active_refit"] = None
        self.c.flush_ack()

    def status(self):
        if self.active and self.active.get("launch_pending"):
            return self.active.get("launch_error") or self.c.runtime.message
        try:
            doc = read_xml(self.c.exchange / "refit-status.xml")
            if self.active and doc.findtext("Id") == self.active["id"]:
                return doc.findtext("Message", "Waiting for editor")
        except (OSError, ET.ParseError):
            pass
        return "Waiting for the updated bridge. Return NEBULOUS to its main menu."
