"""Request freshly fitted ships from the game, then select a fleet by verified cost."""
import copy
import hashlib
from pathlib import Path
import random
import uuid
import xml.etree.ElementTree as ET

from .core import Fleet, now, put, read_xml, write_xml


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
    def __init__(self, campaign):
        self.c = campaign

    @property
    def active(self):
        return self.c.state.get("active_generation")

    def start(self, templates, faction, budget, seed=None):
        from .refit import RefitService
        RefitService(self.c).ensure_idle()
        if not 1 <= budget <= 30000 or faction not in ("Stock/Alliance", "Stock/Protectorate"):
            raise ValueError("Choose a stock faction and a target from 1 to 30,000 points.")
        manifest = read_xml(self.c.game / "Mods/NBCBridge/ModInfo.xml")
        if tuple(int(n) for n in manifest.findtext("ModVer", "0").split('.')) < (0, 4, 0):
            raise ValueError("Install bridge 0.4 or newer to generate random ship fittings.")
        identity = str(uuid.uuid4())
        folder = self.c.data / 'generation' / identity
        folder.mkdir(parents=True)
        # Reuse only missile/craft pattern definitions, never ships or socket loadouts.
        library = ET.Element('Fleet')
        for k, v in dict(Name='NBC Generation Library', Version=3, FactionKey=faction).items():
            put(library, k, v)
        ET.SubElement(library, 'Ships')
        for tag in ('MissileTypes', 'CraftTypes'):
            target = ET.SubElement(library, tag)
            keys = set()
            for template in templates:
                if template.faction != faction or template.root.findall('ModDependencies/unsignedLong'):
                    continue
                for pattern in template.root.findall(f'{tag}/*'):
                    key = pattern.findtext('SaveKey')
                    if key and key not in keys:
                        keys.add(key)
                        target.append(copy.deepcopy(pattern))
        write_xml(folder / 'library.fleet', library)
        record = dict(id=identity, directory=str(folder.resolve()), faction=faction, budget=budget,
                      seed=seed if seed is not None else random.SystemRandom().randrange(2**31),
                      created=now(), launch_pending=True)
        self.c.state['active_generation'] = record
        self.c.persist()
        try:
            self.c.runtime.begin('generation')
        except Exception:
            self.c.state['active_generation'] = None
            self.c.persist()
            raise
        return record

    def request(self, record):
        root = ET.Element('GenerationRequest')
        for k, v in dict(Id=record['id'], Directory=record['directory'], Faction=record['faction'],
                         Budget=record['budget'], Seed=record['seed'], RuntimeInstance=self.c.runtime.instance,
                         CloseGame='true').items():
            put(root, k, v)
        write_xml(self.c.exchange / 'generation-request.xml', root)

    def collect(self):
        record = self.active
        if not record:
            return False
        folder = Path(record['directory'])
        path = folder / 'ready.xml'
        if not path.exists():
            return False
        marker = read_xml(path)
        if marker.tag != 'GenerationReady' or marker.findtext('Id') != record['id']:
            raise ValueError('Generated fleet identity mismatch.')
        fleet_path = folder / 'candidates.fleet'
        if hashlib.sha256(fleet_path.read_bytes()).hexdigest() != marker.findtext('Hash'):
            raise ValueError('Generated fleet changed after validation.')
        pool = Fleet.load(fleet_path)
        if pool.faction != record['faction']:
            raise ValueError('Generated fleet faction mismatch.')
        enemy = select_generated_fleet(pool, record['budget'], record['seed'])
        previous = copy.deepcopy(self.c.state)
        self.c.state['enemy_xml'] = ET.tostring(enemy.root, encoding='unicode')
        self.c.state['active_generation'] = None
        self.c.state['last_generation'] = dict(record, actual_points=enemy.points, completed=now())
        self.c.queue_ack(marker, 'generation')
        try:
            self.c.persist()
        except Exception:
            self.c.state = previous
            raise
        self.c.enemy = enemy
        enemy.save(self.c.data / 'enemy.fleet')
        self.c.flush_ack()
        return True

    def cancel(self):
        if not self.active:
            return
        record = self.active
        self.c.runtime.cancel_startup()
        root = ET.Element('GenerationCancellation')
        put(root, 'Id', record['id'])
        write_xml(self.c.exchange / 'generation-cancel.xml', root)
        if record.get('runtime_instance'):
            marker = ET.Element('GenerationReady')
            put(marker, 'Id', record['id'])
            put(marker, 'RuntimeInstance', record['runtime_instance'])
            self.c.queue_ack(marker, 'generation')
        self.c.state['active_generation'] = None
        self.c.persist()
        self.c.flush_ack()

    def status(self):
        if self.active and self.active.get('launch_pending'):
            return self.active.get('launch_error') or self.c.runtime.message
        try:
            root = read_xml(self.c.exchange / 'generation-status.xml')
            if self.active and root.findtext('Id') == self.active['id']:
                return root.findtext('Message', 'Generating random fittings…')
        except (OSError, ET.ParseError):
            pass
        return 'Waiting for NEBULOUS to build random ship fittings…'
