"""Mission offers: generation, difficulty scaling, rewards and lifecycle rules.

Offers are generated offline from the bridge catalogue, persisted in the
campaign save, and frozen into a fixed enemy snapshot when accepted. Nothing in
this module launches the game; deployment remains a separate concern.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import threading
import uuid
import xml.etree.ElementTree as ET

from .core import Fleet, now
from .fitting import Catalog, build_candidate_pool
from .generation import select_generated_fleet

# Enemy factions are rolled from these stock factions independently of the
# player's own faction. If the catalogue only contains a subset (as in the
# synthetic test fixture), the available subset is used.
MISSION_FACTIONS = ("Stock/Alliance", "Stock/Protectorate")

DEFAULT_OFFER_TTL_HOURS = 24
MIN_OFFER_COUNT = 5
MAX_OFFER_COUNT = 10
MIN_BUDGET = 500
MAX_BUDGET = 30_000                       # The offline fitter refuses larger targets.
FALLBACK_PLAYER_POINTS = 1_500            # Used when the player has no deployable fleet.

# Difficulty is a multiplier on the player's current fleet points. The reward
# is driven by the enemy fleet that was actually generated, so a stronger offer
# always pays more.
DIFFICULTIES = (
    ("easy", 0.7),
    ("standard", 1.0),
    ("hard", 1.3),
)
REWARD_PER_POINT = 100
MIN_REWARD = 5_000
REROLL_COOLDOWN_SECONDS = 0               # 0 = free, unlimited rerolls.

# Mission rules for the current PoC. Types and maps are stored on every offer so
# the deployment/skirmish milestone can use the same values.
MISSION_TYPES = ("annihilation",)
MISSION_MAPS = ("Abyssal (4p)", "The Cliff (4p)")
DEFAULT_MISSION_TYPE = MISSION_TYPES[0]

MISSION_ACTIONS = (
    "Patrol",
    "Sweep",
    "Interdiction",
    "Escort",
    "Raid",
    "Reconnaissance",
    "Blockade",
    "Pursuit",
)
MISSION_PLACES = (
    "Kepler Reach",
    "Cygnus Drift",
    "Vesta Gate",
    "Orion Shelf",
    "Tessera Belt",
    "Halcyon Rift",
    "Morrow Anchorage",
    "Sable Expanse",
)

# Lifecycle used across the mission flow. 5.1 generates and accepts offers;
# deploying/active/completed/failed are driven by the deployment and battle
# milestones.
MISSION_STATUSES = frozenset(
    {
        "offered",
        "accepted",
        "deploying",
        "active",
        "completed",
        "failed",
        "cancelled",
        "expired",
    }
)


def round_step(value, step):
    """Round a positive number to the nearest step without banker's rounding."""
    return max(step, int((value + step / 2) // step) * step)


def budget_for(player_points, multiplier=1.0):
    base = player_points if player_points and player_points > 0 else FALLBACK_PLAYER_POINTS
    return max(MIN_BUDGET, min(MAX_BUDGET, round_step(base * multiplier, 50)))


def reward_for(points):
    """Credits paid for defeating an enemy fleet of ``points`` native points."""
    return max(MIN_REWARD, round_step(int(points) * REWARD_PER_POINT, 1_000))


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _expiry(created, ttl_hours):
    if not ttl_hours:
        return None
    start = _parse_time(created) or datetime.now(timezone.utc)
    return (start + timedelta(hours=ttl_hours)).isoformat()


def offer_expired(offer, moment=None):
    expires = _parse_time(offer.get("expires"))
    if expires is None:
        return False
    return expires <= (moment or datetime.now(timezone.utc))


def mission_name(rng):
    return f"{rng.choice(MISSION_ACTIONS)} at {rng.choice(MISSION_PLACES)}"


def difficulty_tiers(count, rng):
    """Return ``count`` tiers, giving each difficulty once before repeating."""
    pool = list(DIFFICULTIES)
    rng.shuffle(pool)
    return [pool[index % len(pool)] for index in range(count)]


def default_map(seed):
    """Stable map assignment for offers created before map selection existed."""
    return MISSION_MAPS[int(seed or 0) % len(MISSION_MAPS)]


def normalized_offer(offer):
    """Return an offer with the current type/map fields filled in."""
    result = dict(offer)
    if not result.get("type"):
        result["type"] = DEFAULT_MISSION_TYPE
    if not result.get("map"):
        result["map"] = default_map(result.get("seed", 0))
    return result


def build_offer(enemy, faction, difficulty, budget, seed, name, mission_type, map_name, ttl_hours):
    created = now()
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "type": mission_type,
        "map": map_name,
        "faction": faction,
        "difficulty": difficulty,
        "budget": int(budget),
        "points": int(enemy.points),
        "ships": len(enemy.ships),
        "reward": reward_for(enemy.points),
        "seed": int(seed),
        "status": "offered",
        "created": created,
        "expires": _expiry(created, ttl_hours),
        "enemy_xml": ET.tostring(enemy.root, encoding="unicode"),
        "preview": [
            {
                "name": ship.findtext("Name", ""),
                "hull": ship.findtext("HullType", ""),
                "points": int(ship.findtext("Cost", "0") or 0),
            }
            for ship in enemy.ships
        ],
    }


def plan_offers(
    catalog_path,
    player_points,
    count=None,
    seed=None,
    ttl_hours=DEFAULT_OFFER_TTL_HOURS,
    progress=None,
):
    """Build a fresh list of mission offers without touching campaign state."""
    rng = random.Random(seed)
    if count is None:
        count = rng.randint(MIN_OFFER_COUNT, MAX_OFFER_COUNT)
    if type(count) is not int or not 1 <= count <= 12:
        raise ValueError("Choose between 1 and 12 mission offers.")
    path = Path(catalog_path)
    if not path.exists():
        raise ValueError(
            "The native fitting catalogue is missing. Start NEBULOUS once with bridge 0.5 "
            "installed and enabled, then try again."
        )
    catalog = Catalog.load(path)
    available = [key for key in MISSION_FACTIONS if key in catalog.factions]
    if not available:
        available = sorted(catalog.factions)
    if not available:
        raise ValueError("The fitting catalogue does not contain a usable faction.")
    factions = available

    offers = []
    used_names = set()
    tiers = difficulty_tiers(count, rng)
    for index, (difficulty, multiplier) in enumerate(tiers):
        budget = budget_for(player_points, multiplier)
        faction = rng.choice(factions)
        mission_type = rng.choice(MISSION_TYPES)
        map_name = rng.choice(MISSION_MAPS)
        enemy_seed = rng.randrange(2**31)
        name = mission_name(rng)
        while name in used_names:
            name = mission_name(rng)
        used_names.add(name)
        pool = build_candidate_pool(catalog, faction, budget, enemy_seed)
        enemy = select_generated_fleet(pool, budget, enemy_seed)
        offers.append(
            build_offer(
                enemy,
                faction,
                difficulty,
                budget,
                enemy_seed,
                name,
                mission_type,
                map_name,
                ttl_hours,
            )
        )
        if progress is not None:
            progress((index + 1) / count)
    return offers


class MissionGenerationJob:
    """Run offer generation on a worker thread and expose pollable progress.

    The worker only reads the catalogue and builds plain dictionaries. The
    caller commits the result on the UI thread with
    :meth:`MissionService.store_generation`, so no campaign state is mutated
    from the worker.
    """

    def __init__(
        self,
        catalog_path,
        player_points,
        count=None,
        seed=None,
    ):
        self.catalog_path = Path(catalog_path)
        self.player_points = player_points
        self.count = count
        self.seed = seed
        self.state = "idle"  # idle | running | done | error
        self.progress = 0.0
        self.offers = None
        self.error = None
        self._thread = None

    def start(self):
        if self.state == "running":
            raise ValueError("Mission generation is already running.")
        self.state = "running"
        self.progress = 0.0
        self.offers = None
        self.error = None
        self._thread = threading.Thread(
            target=self._run, name="nbc-mission-generation", daemon=True
        )
        self._thread.start()
        return self

    def _run(self):
        try:
            self.offers = plan_offers(
                self.catalog_path,
                self.player_points,
                self.count,
                self.seed,
                progress=self._set_progress,
            )
            self.progress = 1.0
            self.state = "done"
        except Exception as exc:  # surfaced to the UI and retryable
            self.error = str(exc)
            self.state = "error"

    def _set_progress(self, value):
        self.progress = max(0.0, min(1.0, float(value)))

    def running(self):
        return self.state == "running"

    def done(self):
        return self.state == "done"

    def failed(self):
        return self.state == "error"

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)
        return self.state

    def result(self):
        if self.state == "error":
            raise ValueError(self.error or "Mission generation failed.")
        if self.state != "done":
            raise ValueError("Mission generation has not finished.")
        return list(self.offers or [])


class MissionService:
    """Own mission offers and the accept/cancel lifecycle for one campaign."""

    def __init__(self, campaign):
        self.c = campaign

    @property
    def catalog_path(self):
        return self.c.exchange / "fitting-catalog.xml"

    @property
    def offers(self):
        offers = self.c.state.get("mission_offers")
        return list(offers) if isinstance(offers, list) else []

    @property
    def accepted(self):
        return self.c.state.get("accepted_mission")

    def _meta(self):
        meta = self.c.state.get("mission_meta")
        return dict(meta) if isinstance(meta, dict) else {}

    def catalog_signature(self):
        """A cheap fingerprint so offers survive restarts but expire with the catalogue."""
        path = self.catalog_path
        if not path.exists():
            return ""
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def player_points(self):
        return self.c.player.points if self.c.player else 0

    def sanitize(self):
        """Return the offered pool without malformed, duplicate or expired entries.

        Offers created before mission types and maps existed are normalized in
        memory so they keep working; :meth:`migrate` writes that shape back.
        """
        raw = self.c.state.get("mission_offers")
        if not isinstance(raw, list):
            return []
        cleaned, seen = [], set()
        for raw_offer in raw:
            if not isinstance(raw_offer, dict):
                continue
            offer = normalized_offer(raw_offer)
            mission_id = offer.get("id")
            if not isinstance(mission_id, str) or not mission_id or mission_id in seen:
                continue
            if offer.get("status") != "offered":
                continue
            if offer_expired(offer):
                continue
            if not isinstance(offer.get("enemy_xml"), str) or not offer["enemy_xml"].strip():
                continue
            if type(offer.get("points")) is not int or offer["points"] < 1:
                continue
            if type(offer.get("reward")) is not int or offer["reward"] < 0:
                continue
            if offer.get("type") not in MISSION_TYPES:
                continue
            if offer.get("map") not in MISSION_MAPS:
                continue
            seen.add(mission_id)
            cleaned.append(offer)
        return cleaned

    def migrate(self):
        """Persist type/map fields for offers and an accepted mission from older builds."""
        offers = self.c.state.get("mission_offers")
        accepted = self.c.state.get("accepted_mission")
        migrated_offers = (
            [normalized_offer(offer) if isinstance(offer, dict) else offer for offer in offers]
            if isinstance(offers, list)
            else offers
        )
        migrated_accepted = (
            normalized_offer(accepted) if isinstance(accepted, dict) else accepted
        )
        if migrated_offers == offers and migrated_accepted == accepted:
            return False
        with self.c.transaction():
            if isinstance(offers, list):
                self.c.state["mission_offers"] = migrated_offers
            if isinstance(accepted, dict):
                self.c.state["accepted_mission"] = migrated_accepted
        return True

    def refresh(self, count=None, seed=None, force=False):
        """Ensure the board holds current offers and persist any cleanup."""
        self.migrate()
        cleaned = self.sanitize()
        if self.accepted:
            if cleaned != self.offers:
                with self.c.transaction():
                    self.c.state["mission_offers"] = cleaned
            return cleaned
        stale = force or self._meta().get("catalog") != self.catalog_signature()
        if stale or not cleaned:
            return self.generate(count=count, seed=seed)
        if cleaned != self.offers:
            with self.c.transaction():
                self.c.state["mission_offers"] = cleaned
        return cleaned

    def generate(self, count=None, seed=None, rerolled=False):
        if self.accepted:
            raise ValueError("Cancel the accepted mission before regenerating offers.")
        offers = plan_offers(
            self.catalog_path,
            self.player_points(),
            count,
            seed,
        )
        meta = {
            "catalog": self.catalog_signature(),
            "generated": now(),
            "player_points": self.player_points(),
        }
        if rerolled:
            meta["rerolled"] = now()
        with self.c.transaction():
            self.c.state["mission_offers"] = offers
            self.c.state["mission_meta"] = meta
        return offers

    def reroll(self, count=None, seed=None):
        if self.accepted:
            raise ValueError("Cancel the accepted mission before regenerating offers.")
        self.c.ensure_idle()
        last = _parse_time(self._meta().get("rerolled"))
        if REROLL_COOLDOWN_SECONDS and last is not None:
            remaining = REROLL_COOLDOWN_SECONDS - (
                datetime.now(timezone.utc) - last
            ).total_seconds()
            if remaining > 0:
                raise ValueError(
                    f"Mission offers can be regenerated again in {int(remaining) + 1} seconds."
                )
        return self.generate(count=count, seed=seed, rerolled=True)

    def start_generation(self, count=None, seed=None):
        if self.accepted:
            raise ValueError("Cancel the accepted mission before regenerating offers.")
        job = MissionGenerationJob(
            self.catalog_path,
            self.player_points(),
            count,
            seed,
        )
        return job.start()

    def store_generation(self, job, rerolled=False):
        if self.accepted:
            raise ValueError("Cancel the accepted mission before regenerating offers.")
        offers = job.result()
        meta = {
            "catalog": self.catalog_signature(),
            "generated": now(),
            "player_points": self.player_points(),
        }
        if rerolled:
            meta["rerolled"] = now()
        with self.c.transaction():
            self.c.state["mission_offers"] = offers
            self.c.state["mission_meta"] = meta
        return offers

    def accept(self, mission_id):
        if not self.c.player or not self.c.player.ships:
            raise ValueError("Build a fleet before accepting a mission.")
        if self.accepted:
            raise ValueError("A mission is already accepted.")
        self.c.ensure_idle()
        offers = self.sanitize()
        offer = next((item for item in offers if item.get("id") == mission_id), None)
        if offer is None:
            raise ValueError("That mission offer is no longer available.")
        if offer_expired(offer):
            raise ValueError("That mission offer has expired.")
        try:
            enemy = Fleet(ET.fromstring(offer["enemy_xml"]))
        except (ET.ParseError, ValueError) as exc:
            raise ValueError("That mission offer has an invalid enemy snapshot.") from exc

        accepted = dict(copy.deepcopy(offer), status="accepted", accepted=now())
        before = copy.deepcopy(self.c.state)
        try:
            with self.c.transaction():
                self.c.state["mission_offers"] = [
                    item for item in offers if item.get("id") != mission_id
                ]
                self.c.state["accepted_mission"] = accepted
                self.c.state["enemy_xml"] = accepted["enemy_xml"]
                self.c.state["deployment_xml"] = ""
        except Exception:
            self.c.state = before
            raise

        self.c.enemy = enemy
        self.c.deployment = None
        self.c.save_fleets()
        return accepted

    def start_deployment(self):
        """Move an accepted mission into the deployment screen state."""
        accepted = self.accepted
        if not accepted:
            raise ValueError("Accept a mission before deploying.")
        if self.c.state.get("active_battle"):
            raise ValueError("A battle is already pending. Wait for it to finish.")
        if not self.c.player or not self.c.player.ships:
            raise ValueError("Build a fleet before deploying.")
        accepted = dict(copy.deepcopy(accepted), status="deploying")
        with self.c.transaction():
            self.c.state["accepted_mission"] = accepted
        return accepted

    def confirm_deployment(self, ship_keys, launch=True):
        """Build the selected fleet, persist it, and optionally start NEBULOUS."""
        accepted = self.accepted
        if not accepted:
            raise ValueError("Accept a mission before deploying.")
        if self.c.state.get("active_battle"):
            raise ValueError("A battle is already pending. Wait for it to finish.")
        if not self.c.player or not self.c.player.ships:
            raise ValueError("Build a fleet before deploying.")
        self.c.ensure_no_launch()

        keys = list(dict.fromkeys(str(key) for key in ship_keys if key))
        if not keys:
            raise ValueError("Select at least one ready ship.")
        if not 1 <= len(keys) <= 10:
            raise ValueError("Select between 1 and 10 ships for deployment.")
        ships = {ship.findtext("Key"): ship for ship in self.c.player.ships}
        missing = [key for key in keys if key not in ships]
        if missing:
            raise ValueError("A selected ship is no longer in the roster.")
        unready = [ships[key] for key in keys if not self.c.ship_ready(key)]
        if unready:
            names = ", ".join(ship.findtext("Name", "Unnamed hull") for ship in unready)
            raise ValueError(
                f"Repair damaged ships or scrap destroyed components before deployment: {names}."
            )

        subset = self.c.player.clone()
        container = subset.root.find("Ships")
        for ship in list(container.findall("Ship")):
            if (ship.findtext("Key") or "").strip() not in keys:
                container.remove(ship)
        subset.validate(max_ships=10)
        deployment_xml = ET.tostring(subset.root, encoding="unicode")

        accepted = dict(copy.deepcopy(accepted), status="deploying")
        accepted["deployment"] = {"ships": keys, "created": now()}
        before = copy.deepcopy(self.c.state)
        try:
            with self.c.transaction():
                self.c.state["accepted_mission"] = accepted
                self.c.state["deployment_xml"] = deployment_xml
        except Exception:
            self.c.state = before
            raise
        self.c.deployment = subset

        if not launch:
            return accepted

        self.c.launch()
        accepted = dict(copy.deepcopy(self.accepted), status="active")
        with self.c.transaction():
            self.c.state["accepted_mission"] = accepted
        return accepted

    def abandon_deployment(self):
        """Return from the deployment screen to the accepted mission board."""
        accepted = self.accepted
        if not accepted:
            return None
        if self.c.state.get("active_battle"):
            raise ValueError("A battle is already pending. Wait for it to finish.")
        accepted = dict(copy.deepcopy(accepted), status="accepted")
        accepted.pop("deployment", None)
        with self.c.transaction():
            self.c.state["accepted_mission"] = accepted
            self.c.state["deployment_xml"] = ""
        self.c.deployment = None
        return accepted

    def cancel_accepted(self):
        """Return the accepted mission to the board without a reward or penalty."""
        offer = self.accepted
        if not offer:
            raise ValueError("There is no accepted mission to cancel.")
        if self.c.state.get("active_battle"):
            raise ValueError("Wait for the pending battle to finish before cancelling the mission.")
        returned = normalized_offer(copy.deepcopy(offer))
        returned["status"] = "offered"
        returned.pop("accepted", None)
        returned.pop("deployment", None)
        returned["expires"] = _expiry(now(), DEFAULT_OFFER_TTL_HOURS)

        before = copy.deepcopy(self.c.state)
        try:
            with self.c.transaction():
                self.c.state["accepted_mission"] = None
                self.c.state["mission_offers"] = [returned] + [
                    item for item in self.sanitize() if item.get("id") != returned.get("id")
                ]
                self.c.state["enemy_xml"] = ""
                self.c.state["deployment_xml"] = ""
        except Exception:
            self.c.state = before
            raise

        self.c.enemy = None
        self.c.deployment = None
        try:
            (self.c.data / "enemy.fleet").unlink()
        except OSError:
            pass
        return returned
