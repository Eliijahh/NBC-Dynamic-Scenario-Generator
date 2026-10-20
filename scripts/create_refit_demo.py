"""Create an isolated three-frigate test save; never replaces an existing save."""
import argparse
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nbc.core import Campaign, DEFAULT_GAME, discover, put


def create(data, game=DEFAULT_GAME):
    if (data / "campaign.json").exists() or (data / "player.fleet").exists():
        raise ValueError("Demo save already exists. Choose a new --data directory.")
    candidates = [(f, i) for f in discover(game)
                  if f.source.parent.name == "Starter Fleets - Alliance"
                  for i, s in enumerate(f.ships) if s.findtext("HullType") == "Stock/Raines Frigate"]
    if not candidates:
        raise ValueError("No current starter Raines design found.")
    donor, index = candidates[0]
    fleet = donor.clone()
    container = fleet.root.find("Ships")
    for s in list(container):
        container.remove(s)
    container.append(copy.deepcopy(donor.ships[index]))
    fleet.duplicate(0)
    fleet.duplicate(0)
    for i, ship in enumerate(fleet.ships):
        put(ship, "Name", f"Refit Test {i + 1}")
        sockets = ship.find("SocketMap")
        kept_turret = False
        for socket in list(sockets):
            part = socket.findtext("ComponentName", "")
            keep_turret = i == 0 and part == "Stock/Mk95 'Sarissa' PDT" and not kept_turret
            if keep_turret:
                kept_turret = True
            if part not in ("Stock/Basic CIC", "Stock/FR4800 Reactor", "Stock/FM230 'Whiplash' Drive") and not keep_turret:
                sockets.remove(socket)
    put(fleet.root, "Name", "Isolated Campaign Refit Demo")
    campaign = Campaign(game, data)
    campaign.player = fleet
    campaign.save_fleets()
    print(f"Created {len(fleet.ships)} test ships in {data}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    args = parser.parse_args()
    create(args.data, args.game)
