"""Inventory and refit transactions. The campaign JSON is the commit boundary."""
from collections import Counter
import copy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import uuid

from .core import Fleet, now, put, read_xml, write_xml


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
        if self.active or self.c.state.get("active_battle"):
            raise ValueError("Finish or cancel the pending battle/refit first.")

    def trade(self, key, quantity, buy=True):
        self.ensure_idle()
        if type(quantity) is not int or not 1 <= quantity <= 999:
            raise ValueError("Choose a quantity from 1 to 999.")
        inventory = dict(self.c.state["inventory"])
        if buy and key not in self.catalog():
            raise ValueError("Component is not in the game-exported catalogue. Start NEBULOUS with the updated bridge first.")
        count = inventory.get(key, 0) + (quantity if buy else -quantity)
        if count < 0:
            raise ValueError("Only spare equipment can be sold; not enough units available.")
        if count:
            inventory[key] = count
        else:
            inventory.pop(key, None)
        self.c.state["inventory"] = inventory
        self.c.persist()

    def start(self, launch=True):
        self.ensure_idle()
        if not self.c.player:
            raise ValueError("Load a campaign fleet first.")
        self.c.save_fleets()
        session_id = str(uuid.uuid4())
        directory = self.c.data / "refits" / session_id
        directory.mkdir(parents=True)
        self.c.player.save(directory / "baseline.fleet")
        self.c.player.save(directory / "input-1.fleet")
        record = {"id": session_id, "directory": str(directory.resolve()), "revision": 1,
                  "stage": "editor", "created": now(), "inventory": dict(self.c.state["inventory"])}
        self.c.state["active_refit"] = record
        self.c.persist()
        try:
            self._request(record)
            if launch:
                self.open_game()
        except Exception:
            self.cancel()
            raise
        return record

    def open_game(self):
        import subprocess
        if not (self.c.game / "Mods/NBCBridge/NBCBridge.dll").exists():
            raise ValueError("Install the updated bridge before opening a refit.")
        running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Nebulous.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if '"nebulous.exe"' not in running.stdout.lower():
            subprocess.Popen([str(self.c.game / "Nebulous.exe")], cwd=self.c.game)

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
        record["stage"] = "review"
        self.c.persist()
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

    def confirm(self):
        if not self.active or self.active["stage"] != "review":
            raise ValueError("Return from the game editor before confirming.")
        draft, inventory, changes = self.review()
        previous = copy.deepcopy(self.c.state)
        record = dict(self.active, stage="confirmed", completed=now(), changes=len(changes))
        self.c.state["inventory"] = inventory
        self.c.state["player_xml"] = ET.tostring(draft.root, encoding="unicode")
        self.c.state["active_refit"] = None
        self.c.state["refits"].append(record)
        try:
            self.c.persist()  # Fleet, inventory and session completion commit in one replacement.
        except Exception:
            self.c.state = previous
            raise
        self.c.player = draft
        draft.save(self.c.data / "player.fleet")  # Derived export; JSON remains authoritative.
        return changes

    def resume(self, launch=True):
        if not self.active:
            raise ValueError("There is no refit to resume.")
        record = self.active
        folder = Path(record["directory"])
        if record["stage"] == "review":
            draft, _, _ = self.review()
        else:
            draft = Fleet.load(folder / f"input-{record['revision']}.fleet")
        record["revision"] += 1
        draft.save(folder / f"input-{record['revision']}.fleet")
        record["stage"] = "editor"
        self.c.persist()
        self._request(record)
        if launch:
            self.open_game()

    def cancel(self):
        if not self.active:
            return
        root = ET.Element("RefitCancellation")
        put(root, "Id", self.active["id"])
        write_xml(self.c.exchange / "refit-cancel.xml", root)
        self.c.state["refits"].append(dict(self.active, stage="cancelled", completed=now()))
        self.c.state["active_refit"] = None
        self.c.persist()

    def status(self):
        try:
            doc = read_xml(self.c.exchange / "refit-status.xml")
            if self.active and doc.findtext("Id") == self.active["id"]:
                return doc.findtext("Message", "Waiting for editor")
        except (OSError, ET.ParseError):
            pass
        return "Waiting for the updated bridge. Return NEBULOUS to its main menu."
