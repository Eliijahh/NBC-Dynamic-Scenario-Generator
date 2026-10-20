"""Discover and manage independent campaign save directories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid
import xml.etree.ElementTree as ET

from .core import atomic_bytes, read_xml


SUPPORTED_STATE_VERSION = 1


@dataclass(frozen=True)
class CampaignInfo:
    id: str
    path: Path
    name: str
    commander: str
    faction: str
    credits: int
    ships: int
    last_played: str
    error: str = ""
    legacy: bool = False

    @property
    def valid(self):
        return not self.error


class CampaignLibrary:
    """A root save directory with optional campaign subdirectories."""

    def __init__(self, root):
        self.root = Path(root)
        self.campaigns_dir = self.root / "campaigns"
        self.registry_path = self.root / "campaigns.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)

    def path_for_id(self, campaign_id):
        return self.root if campaign_id == "root" else self.campaigns_dir / campaign_id

    def id_for_path(self, path):
        path = Path(path).resolve()
        if path == self.root.resolve():
            return "root"
        if path.parent == self.campaigns_dir.resolve():
            return path.name
        return None

    def _campaign_paths(self):
        result = []
        if (self.root / "campaign.json").exists() or (self.root / "player.fleet").exists():
            result.append(("root", self.root))
        if self.campaigns_dir.exists():
            for child in sorted(self.campaigns_dir.iterdir()):
                if child.is_dir() and ((child / "campaign.json").exists() or (child / "player.fleet").exists()):
                    result.append((child.name, child))
        return result

    @staticmethod
    def _fleet_details(state, path):
        fleet_root = None
        xml_text = state.get("player_xml")
        if xml_text:
            fleet_root = ET.fromstring(xml_text)
        elif (path / "player.fleet").exists():
            fleet_root = read_xml(path / "player.fleet")
        if fleet_root is None:
            raise ValueError("Campaign has no player fleet.")
        if fleet_root.tag != "Fleet" or fleet_root.find("Ships") is None:
            raise ValueError("Campaign fleet is not a valid NEBULOUS fleet.")
        ships = fleet_root.findall("Ships/Ship")
        return fleet_root.findtext("Name", ""), fleet_root.findtext("FactionKey", ""), len(ships)

    @staticmethod
    def _file_time(path):
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            return ""

    def _read_info(self, campaign_id, path):
        state_path = path / "campaign.json"
        state = {}
        error = ""
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("Campaign save root is not an object.")
            except Exception as exc:
                state = {}
                error = f"Unreadable campaign save: {exc}"
        elif not (path / "player.fleet").exists():
            error = "Campaign save is missing campaign.json and player.fleet."

        version = state.get("version", SUPPORTED_STATE_VERSION)
        if not error and (not isinstance(version, int) or version > SUPPORTED_STATE_VERSION):
            error = "Campaign save was created by a newer, unsupported version."

        meta = state.get("campaign") or {}
        if not isinstance(meta, dict):
            meta = {}
        fleet_name = fleet_faction = ""
        ships = 0
        if not error:
            try:
                fleet_name, fleet_faction, ships = self._fleet_details(state, path)
            except Exception as exc:
                error = f"Campaign fleet is unreadable: {exc}"

        name = meta.get("name") or fleet_name
        if not name:
            name = "Legacy Campaign" if campaign_id == "root" else f"Unreadable Campaign {campaign_id[:8]}"
        commander = meta.get("commander") or ""
        faction = meta.get("faction") or fleet_faction or ""
        try:
            credits = int(meta.get("credits", 0))
        except (TypeError, ValueError):
            credits = 0
            if not error:
                error = "Campaign credits are invalid."
        last_played = meta.get("last_played") or meta.get("started") or meta.get("created") or self._file_time(state_path if state_path.exists() else path)
        legacy = campaign_id == "root" or bool(meta.get("legacy")) or not meta
        return CampaignInfo(
            id=campaign_id,
            path=path,
            name=str(name),
            commander=str(commander),
            faction=str(faction),
            credits=credits,
            ships=ships,
            last_played=str(last_played or ""),
            error=error,
            legacy=legacy,
        )

    def list(self):
        campaigns = [self._read_info(campaign_id, path) for campaign_id, path in self._campaign_paths()]
        return sorted(campaigns, key=lambda c: (c.last_played, c.name.casefold()), reverse=True)

    def get(self, campaign_id):
        return next((campaign for campaign in self.list() if campaign.id == campaign_id), None)

    def last_id(self):
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            value = data.get("last") if isinstance(data, dict) else None
            return value if isinstance(value, str) else None
        except (OSError, ValueError, TypeError):
            return None

    def last_info(self):
        campaign_id = self.last_id()
        return self.get(campaign_id) if campaign_id else None

    def set_last(self, campaign_id):
        atomic_bytes(self.registry_path, json.dumps({"last": campaign_id}, indent=2).encode())

    def new_campaign_path(self):
        campaign_id = str(uuid.uuid4())
        path = self.campaigns_dir / campaign_id
        path.mkdir(parents=True, exist_ok=False)
        return campaign_id, path

    def remove_campaign_path(self, path):
        shutil.rmtree(path, ignore_errors=True)

    def mark_played(self, path):
        campaign_id = self.id_for_path(path)
        if campaign_id:
            self.set_last(campaign_id)
