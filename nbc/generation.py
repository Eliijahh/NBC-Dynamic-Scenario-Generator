"""Generate an opposing fleet from the bridge-exported native fitting catalogue."""
from __future__ import annotations

import random
import uuid
import xml.etree.ElementTree as ET

from .core import now, put


def select_generated_fleet(pool, budget, seed=0):
    if not 1 <= budget <= 30000 or not pool.ships or len(pool.ships) > 64:
        raise ValueError("Invalid generated candidate pool or target budget.")
    # Each candidate can appear once. Keep the smallest ship count for each cost.
    order = list(range(len(pool.ships)))
    random.Random(seed).shuffle(order)
    costs = [int(s.findtext("Cost", "0")) for s in pool.ships]
    if any(c <= 0 for c in costs):
        raise ValueError("Generated ships must have positive game-calculated costs.")
    states = {0: ()}
    for index in order:
        for total, chosen in list(states.items()):
            new = total + costs[index]
            if len(chosen) < 10 and new <= budget * 2 + max(costs):
                proposed = chosen + (index,)
                if new not in states or len(proposed) < len(states[new]):
                    states[new] = proposed
    total = min((n for n in states if n), key=lambda n: (abs(n-budget), n > budget))
    chosen = set(states[total])
    result = pool.clone()
    container = result.root.find("Ships")
    for i, ship in enumerate(list(container)):
        if i not in chosen:
            container.remove(ship)
    for i, ship in enumerate(result.ships, 1):
        put(ship, "Name", f"Hostile {i:02d}")
    put(result.root, "Name", "NBC Randomly Fitted Opposing Force")
    result._clear_formations()
    result.validate()
    return result


class GenerationService:
    """Fits and stores an enemy locally. The game is never opened by this service."""

    def __init__(self, campaign):
        self.c = campaign

    @property
    def catalog_path(self):
        return self.c.exchange / "fitting-catalog.xml"

    def start(self, faction, budget, seed=None):
        from .fitting import generate_fleet
        from .refit import RefitService

        RefitService(self.c).ensure_idle()
        if not 1 <= budget <= 30000 or faction not in ("Stock/Alliance", "Stock/Protectorate"):
            raise ValueError("Choose a stock faction and a target from 1 to 30,000 points.")
        if not self.catalog_path.exists():
            raise ValueError(
                "The native fitting catalogue is missing. Start NEBULOUS once with bridge 0.5.0 "
                "installed and enabled, then try again."
            )
        seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        enemy = generate_fleet(self.catalog_path, faction, budget, seed)
        record = dict(
            id=str(uuid.uuid4()),
            faction=faction,
            budget=budget,
            seed=seed,
            method="offline",
            ships=len(enemy.ships),
            actual_points=enemy.points,
            created=now(),
            completed=now(),
        )
        enemy.save(self.c.data / "enemy.fleet")
        with self.c.transaction():
            self.c.state["enemy_xml"] = ET.tostring(enemy.root, encoding="unicode")
            self.c.state["last_generation"] = record
        self.c.enemy = enemy
        return record
