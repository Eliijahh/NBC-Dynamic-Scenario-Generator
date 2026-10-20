"""Offline ship fitting from the bridge's native fitting catalogue.

The catalogue is exported by the game bridge.  This module only consumes
declarative hull/component data; it never opens the fleet editor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import random
import uuid
import xml.etree.ElementTree as ET

from .core import Fleet, put, read_xml

XSI = "http://www.w3.org/2001/XMLSchema-instance"
SURFACE, COMPARTMENT, MODULE = 0, 1, 2


class CatalogError(ValueError):
    pass


def _text(node, tag, default=""):
    if node is None:
        return default
    return node.findtext(tag, default)


def _int(node, tag, default=0):
    try:
        return int(_text(node, tag, str(default)))
    except ValueError:
        return default


def _float(node, tag, default=0.0):
    try:
        return float(_text(node, tag, str(default)))
    except ValueError:
        return default


def _bool(node, tag):
    return _int(node, tag, 0) != 0


@dataclass(frozen=True)
class Resource:
    name: str
    amount: int
    per_unit: bool = False
    only_when_operating: bool = False


@dataclass(frozen=True)
class Munition:
    key: str
    name: str
    storage: float
    point_cost: int
    division: int
    sim_method: str
    role: str
    munition_type: str
    ammo_class: str
    subclass: str

    def cost(self, quantity):
        if quantity <= 0:
            return 0
        division = max(1, self.division)
        return self.point_cost * math.ceil(quantity / division)


@dataclass(frozen=True)
class Socket:
    key: str
    type: int
    size: tuple[int, int, int]
    overhang: int
    default: str = ""


@dataclass(frozen=True)
class Weapon:
    wep_type: str
    role: str
    needs_ammo: bool
    supports_selection: bool
    allow_grouping: bool
    grouping_name: str
    ammo: tuple[str, ...]

    @property
    def requires_ammo(self):
        return self.needs_ammo or self.supports_selection or "missile" in self.wep_type.lower()


@dataclass(frozen=True)
class Magazine:
    capacity: int
    ammo: tuple[str, ...]


@dataclass(frozen=True)
class Component:
    key: str
    name: str
    category: str
    type: int
    size: tuple[int, int, int]
    overhang: int
    rotate: int
    can_tile: bool
    consistent_cost: bool
    points: int
    compounding: bool
    compounding_class: str
    first_free: bool
    compounding_multiplier: float
    crew: int
    is_drive: bool
    is_powerplant: bool
    is_command: bool
    command_all: bool
    is_sensor: bool
    is_bulk_magazine: bool
    is_hangar: bool
    is_weapon: bool
    weapon: Weapon | None
    magazine: Magazine | None
    provided: tuple[Resource, ...]
    required: tuple[Resource, ...]

    @property
    def is_launcher(self):
        return bool(self.weapon and "missile" in self.weapon.wep_type.lower())

    @property
    def is_offensive_weapon(self):
        if not self.weapon or self.is_launcher:
            return False
        role = " ".join(self.weapon.role.lower().split())
        category = " ".join(self.category.lower().split())
        # EWar, sensors and point defence can report an "Offensive" role but
        # are not primary guns. Missile launchers are excluded separately.
        if any(kind in category for kind in ("point defense", "missile", "ewar", "sensor", "hangar")):
            return False
        if any(kind in category for kind in ("projectile", "energy", "beam", "spinal")):
            return True
        if "defensive" in role and "offensive" not in role:
            return False
        return self.weapon.wep_type.strip().lower() in ("projectile", "energy", "beam", "fixed", "turret")

    @property
    def is_defensive_weapon(self):
        if not self.weapon or self.is_launcher:
            return False
        role = " ".join(self.weapon.role.lower().split())
        category = " ".join(self.category.lower().split())
        return "defensive" in role or "point defense" in category or "pdt" in category or "ewar" in category

    @property
    def power_output(self):
        return sum(r.amount for r in self.provided if r.name.lower() == "power")

    def tile_multiplier(self, socket):
        if not self.can_tile:
            return 1
        volume = max(1, socket.size[0] * socket.size[1] * socket.size[2])
        part = max(1, self.size[0] * self.size[1] * self.size[2])
        return max(1, volume // part)

    def fits(self, socket):
        if self.type != socket.type:
            return False
        if socket.overhang & self.overhang != self.overhang:
            return False
        x, y, z = self.size
        sx, sy, sz = socket.size
        if self.rotate == 2:  # AnyAxis
            return x * x + y * y + z * z <= sx * sx + sy * sy + sz * sz
        if self.rotate == 1:  # UpOnly
            return y <= sy and ((x <= sx and z <= sz) or (x <= sz and z <= sx))
        return x <= sx and y <= sy and z <= sz

    def sorting_point_cost(self, socket):
        multiplier = self.tile_multiplier(socket)
        return self.points if self.consistent_cost else self.points * multiplier

    def point_cost(self, socket, existing_count=0):
        base = self.sorting_point_cost(socket)
        if not self.compounding:
            return base
        if self.first_free and existing_count == 0:
            return 0
        return int(round(base * max(self.compounding_multiplier * existing_count, 1.0)))


@dataclass(frozen=True)
class Hull:
    key: str
    name: str
    class_name: str
    points: int
    crew: int
    sockets: tuple[Socket, ...]


@dataclass
class FactionCatalog:
    key: str
    name: str
    hulls: tuple[Hull, ...]
    components: dict[str, Component]
    munitions: dict[str, Munition]


@dataclass
class Catalog:
    game_version: str
    factions: dict[str, FactionCatalog]

    @classmethod
    def load(cls, path):
        try:
            root = read_xml(path)
        except (OSError, ET.ParseError) as exc:
            raise CatalogError(f"Could not read the native fitting catalogue: {exc}") from exc
        if root.tag != "FittingCatalog" or _int(root, "Format", 0) != 1:
            raise CatalogError("The fitting catalogue is missing or from an unsupported bridge version.")
        factions = {}
        for faction_node in root.findall("Faction"):
            key = _text(faction_node, "Key")
            if not key:
                continue
            munitions = {}
            for node in faction_node.findall("Munitions/Munition"):
                item = Munition(
                    key=_text(node, "Key"),
                    name=_text(node, "Name", _text(node, "Key")),
                    storage=_float(node, "Storage", 1.0),
                    point_cost=_int(node, "Points", 1),
                    division=_int(node, "Division", 1),
                    sim_method=_text(node, "SimMethod"),
                    role=_text(node, "Role"),
                    munition_type=_text(node, "Type"),
                    ammo_class=_text(node, "Class"),
                    subclass=_text(node, "Subclass"),
                )
                if item.key:
                    munitions[item.key] = item
            components = {}
            for node in faction_node.findall("Components/Component"):
                weapon = None
                weapon_node = node if _bool(node, "Weapon") else None
                if weapon_node is not None:
                    weapon = Weapon(
                        wep_type=_text(node, "WepType"),
                        role=_text(node, "Role"),
                        needs_ammo=_bool(node, "NeedsAmmo"),
                        supports_selection=_bool(node, "AmmoSelection"),
                        allow_grouping=_bool(node, "AllowGrouping"),
                        grouping_name=_text(node, "GroupingName"),
                        ammo=tuple(x.text for x in node.findall("Ammo/Munition") if x.text),
                    )
                magazine = None
                mag_node = node.find("Magazine")
                if mag_node is not None:
                    magazine = Magazine(
                        capacity=_int(mag_node, "Capacity", 0),
                        ammo=tuple(x.text for x in mag_node.findall("Ammo/Munition") if x.text),
                    )
                provided = tuple(
                    Resource(_text(r, "Name"), _int(r, "Amount"), _bool(r, "PerUnit"), _bool(r, "OnlyWhenOperating"))
                    for r in node.findall("ResourcesProvided/Resource")
                    if _text(r, "Name")
                )
                required = tuple(
                    Resource(_text(r, "Name"), _int(r, "Amount"), _bool(r, "PerUnit"), _bool(r, "OnlyWhenOperating"))
                    for r in node.findall("ResourcesRequired/Resource")
                    if _text(r, "Name")
                )
                item = Component(
                    key=_text(node, "Key"),
                    name=_text(node, "Name", _text(node, "Key")),
                    category=_text(node, "Category"),
                    type=_int(node, "Type"),
                    size=(_int(node, "X"), _int(node, "Y"), _int(node, "Z")),
                    overhang=_int(node, "Overhang"),
                    rotate=_int(node, "Rotate"),
                    can_tile=_bool(node, "CanTile"),
                    consistent_cost=_bool(node, "ConsistentCost"),
                    points=_int(node, "Points"),
                    compounding=_bool(node, "Compounding"),
                    compounding_class=_text(node, "CompoundingClass"),
                    first_free=_bool(node, "FirstFree"),
                    compounding_multiplier=_float(node, "CompoundingMultiplier", 1.0),
                    crew=_int(node, "Crew"),
                    is_drive=_bool(node, "Drive"),
                    is_powerplant=_bool(node, "Powerplant"),
                    is_command=_bool(node, "Command"),
                    command_all=_bool(node, "CommandAll"),
                    is_sensor=_bool(node, "Sensor"),
                    is_bulk_magazine=_bool(node, "BulkMagazine"),
                    is_hangar=_bool(node, "Hangar"),
                    is_weapon=weapon is not None,
                    weapon=weapon,
                    magazine=magazine,
                    provided=provided,
                    required=required,
                )
                if item.key:
                    components[item.key] = item
            hulls = []
            for node in faction_node.findall("Hulls/Hull"):
                sockets = tuple(
                    Socket(
                        key=_text(s, "Key"),
                        type=_int(s, "Type"),
                        size=(_int(s, "X"), _int(s, "Y"), _int(s, "Z")),
                        overhang=_int(s, "Overhang"),
                        default=_text(s, "Default"),
                    )
                    for s in node.findall("Sockets/Socket")
                    if _text(s, "Key")
                )
                hull = Hull(
                    key=_text(node, "Key"),
                    name=_text(node, "Name", _text(node, "Key")),
                    class_name=_text(node, "Class"),
                    points=_int(node, "Points"),
                    crew=_int(node, "Crew"),
                    sockets=sockets,
                )
                if hull.key and hull.sockets:
                    hulls.append(hull)
            if hulls and components:
                factions[key] = FactionCatalog(key, _text(faction_node, "Name", key), tuple(hulls), components, munitions)
        if not factions:
            raise CatalogError("The fitting catalogue contains no usable stock factions.")
        return cls(_text(root, "GameVersion"), factions)


@dataclass
class ShipPlan:
    hull: Hull
    installed: dict[str, Component] = field(default_factory=dict)
    ammo: dict[str, list[tuple[Munition, int]]] = field(default_factory=dict)
    cost: int = 0

    def socket(self, key):
        for socket in self.hull.sockets:
            if socket.key == key:
                return socket
        return None


class Fitter:
    """Builds one plausible ship from the currently installed stock parts."""

    def __init__(self, faction, rng):
        self.faction = faction
        self.rng = rng
        self.hull = None
        self.plan = None
        self._options_cache = {}

    def options(self, socket):
        key = (socket.type, socket.size, socket.overhang, socket.default)
        cached = self._options_cache.get(key)
        if cached is not None:
            return cached
        result = []
        seen = set()
        for component in self.faction.components.values():
            if component.is_hangar or component.is_launcher:
                continue
            if not component.fits(socket):
                continue
            result.append(component)
            seen.add(component.key)
        if socket.default and socket.default not in seen:
            default = self.faction.components.get(socket.default)
            if default is not None and not default.is_hangar and not default.is_launcher and default.fits(socket):
                result.append(default)
        self._options_cache[key] = result
        return result

    def _pick(self, predicate, socket_types=None, prefer=None):
        candidates = []
        sockets = [s for s in self.hull.sockets if s.key not in self.plan.installed]
        if socket_types is not None:
            preferred = [s for s in sockets if s.type in socket_types]
            candidates = [(s, c) for s in preferred for c in self.options(s) if predicate(c)]
            if not candidates:
                candidates = [(s, c) for s in sockets for c in self.options(s) if predicate(c)]
        else:
            candidates = [(s, c) for s in sockets for c in self.options(s) if predicate(c)]
        if not candidates:
            return None
        if prefer is not None:
            best = max(prefer(c) for _, c in candidates)
            candidates = [(s, c) for s, c in candidates if prefer(c) == best]
        socket, component = self.rng.choice(candidates)
        self.plan.installed[socket.key] = component
        return socket, component

    def _has_magazine_ammo(self, weapon):
        if not weapon.requires_ammo:
            return True
        if not weapon.ammo:
            return False
        for component in self.plan.installed.values():
            if component.magazine is None:
                continue
            if set(weapon.ammo) & set(component.magazine.ammo) & self.faction.munitions.keys():
                return True
        return False

    def _install_magazine(self):
        pair = self._pick(
            lambda c: c.is_bulk_magazine and c.magazine and c.magazine.capacity > 0,
            socket_types=(COMPARTMENT, MODULE),
            prefer=lambda c: c.magazine.capacity,
        )
        return pair is not None

    def fit(self, hull):
        self.hull = hull
        self.plan = ShipPlan(hull)
        installed = self.plan.installed

        for predicate, types in (
            (lambda c: c.is_drive, (MODULE,)),
            (lambda c: c.is_powerplant, (MODULE,)),
            (lambda c: c.is_command and c.command_all, (COMPARTMENT, MODULE)),
        ):
            if self._pick(predicate, socket_types=types) is None:
                return None

        # A magazine is useful even on energy-only ships, but never reserve the
        # last compartment if it would prevent a command module.
        self._install_magazine()

        surface = [s for s in hull.sockets if s.type == SURFACE and s.key not in installed]
        self.rng.shuffle(surface)
        desired = self.rng.randint(1, min(3, max(1, len(surface))))
        offensive = 0
        for socket in surface:
            if offensive >= desired:
                break
            choices = [c for c in self.options(socket) if c.is_offensive_weapon and self._has_magazine_ammo(c.weapon)]
            if not choices:
                continue
            component = self.rng.choice(choices)
            installed[socket.key] = component
            offensive += 1

        if offensive == 0:
            # Fall back to any compatible non-missile weapon, preferring one
            # that does not need an external magazine.
            for socket in surface:
                choices = [c for c in self.options(socket) if c.is_offensive_weapon]
                choices.sort(key=lambda c: (c.weapon.requires_ammo, self.rng.random()))
                if choices:
                    installed[socket.key] = choices[0]
                    offensive = 1
                    break
        if offensive == 0:
            return None
        self._pick(lambda c: c.is_sensor, socket_types=(SURFACE, MODULE, COMPARTMENT))

        # Fill most remaining sockets, but keep some room for resource pruning.
        optional = [s for s in hull.sockets if s.key not in installed]
        self.rng.shuffle(optional)
        for socket in optional:
            if self.rng.random() > 0.86:
                continue
            choices = list(self.options(socket))
            if not choices:
                continue
            # Do not stack command-all modules.
            if any(c.is_command and c.command_all for c in installed.values()):
                choices = [c for c in choices if not (c.is_command and c.command_all)]
            choices = [c for c in choices if not (c.is_weapon and c.weapon.requires_ammo and not self._has_magazine_ammo(c.weapon))]
            if not choices:
                continue
            # Prefer weapons on surface sockets and support systems elsewhere.
            if socket.type == SURFACE:
                weapons = [c for c in choices if c.is_weapon]
                if weapons and self.rng.random() < 0.7:
                    choices = weapons
            else:
                support = [c for c in choices if not c.is_weapon]
                if support and self.rng.random() < 0.8:
                    choices = support
            installed[socket.key] = self.rng.choice(choices)

        self._load_ammo()
        self._drop_unfed_weapons()
        if not any(c.is_offensive_weapon for c in self.plan.installed.values()):
            return None
        self._load_ammo()
        if not self._prune_resources():
            return None
        self._calculate_cost()
        if self.plan.cost <= 0:
            return None
        return self.plan

    def _magazines(self):
        return [(s, c) for s, c in ((self.plan.socket(k), c) for k, c in self.plan.installed.items()) if s and c.magazine]

    def _load_ammo(self):
        self.plan.ammo.clear()
        needed = set()
        for component in self.plan.installed.values():
            if component.weapon and component.weapon.requires_ammo:
                needed.update(component.weapon.ammo)
        magazines = self._magazines()
        if not magazines:
            return
        # Only load ammunition that a current weapon can actually use.
        if not needed:
            return
        for socket, component in magazines:
            accepted = [self.faction.munitions[k] for k in component.magazine.ammo if k in self.faction.munitions and k in needed]
            if not accepted:
                continue
            capacity = max(0, component.magazine.capacity * component.tile_multiplier(socket))
            if capacity <= 0:
                continue
            share = capacity / len(accepted)
            load = []
            for munition in accepted:
                volume = max(0.001, munition.storage)
                quantity = max(1, min(10000, int(share * 0.92 / volume)))
                load.append((munition, quantity))
            self.plan.ammo[socket.key] = load

    def _drop_unfed_weapons(self):
        for key, component in list(self.plan.installed.items()):
            if component.weapon and component.weapon.requires_ammo and not self._has_magazine_ammo(component.weapon):
                del self.plan.installed[key]
        if not any(c.is_offensive_weapon for c in self.plan.installed.values()):
            self.plan.installed.clear()
            return

    def _usage(self):
        provided, required = {}, {}
        crew = 0
        for key, component in self.plan.installed.items():
            socket = self.plan.socket(key)
            multiplier = component.tile_multiplier(socket)
            crew += component.crew
            for resource in component.provided:
                amount = resource.amount * (multiplier if resource.per_unit else 1)
                provided[resource.name] = provided.get(resource.name, 0) + amount
            for resource in component.required:
                amount = resource.amount * (multiplier if resource.per_unit else 1)
                required[resource.name] = required.get(resource.name, 0) + amount
        return provided, required, crew

    def _resources_ok(self):
        provided, required, crew = self._usage()
        if any(amount > provided.get(name, 0) for name, amount in required.items()):
            return False
        # Crew is a soft limit in the editor, but a large excess is implausible.
        return crew <= max(self.hull.crew, 1) * 1.35

    def _prune_resources(self):
        if self._resources_ok():
            return True
        # Keep the command, drive, reactor, one offensive weapon and enough
        # magazine capacity for it.  Strip everything else in a stable order.
        keep = set()
        for key, component in self.plan.installed.items():
            if component.is_drive or component.is_powerplant or (component.is_command and component.command_all):
                keep.add(key)
        for key, component in self.plan.installed.items():
            if component.is_offensive_weapon:
                keep.add(key)
                break
        for key, component in self.plan.installed.items():
            if component.is_bulk_magazine:
                keep.add(key)
        optional = [k for k in self.plan.installed if k not in keep]
        self.rng.shuffle(optional)
        for key in optional:
            if self._resources_ok():
                self._load_ammo()
                return True
            del self.plan.installed[key]
        self._load_ammo()
        return self._resources_ok()

    def _calculate_cost(self):
        total = self.plan.hull.points
        counts = {}
        order = sorted(
            self.plan.installed.items(),
            key=lambda kv: kv[1].sorting_point_cost(self.plan.socket(kv[0])),
            reverse=True,
        )
        for key, component in order:
            socket = self.plan.socket(key)
            group = component.compounding_class
            index = counts.get(group, 0)
            total += component.point_cost(socket, index)
            counts[group] = index + 1
        for load in self.plan.ammo.values():
            for munition, quantity in load:
                total += munition.cost(quantity)
        self.plan.cost = total


def _deterministic_uuid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _ship_xml(plan, rng, index):
    ship = ET.Element("Ship")
    put(ship, "Key", _deterministic_uuid(rng))
    put(ship, "Name", f"Hostile {index:02d}")
    put(ship, "Cost", plan.cost)
    put(ship, "Callsign", "")
    put(ship, "Number", rng.randint(100, 9999))
    put(ship, "SymbolOption", 0)
    put(ship, "HullType", plan.hull.key)
    ET.SubElement(ship, "ModDependencies")
    sockets = ET.SubElement(ship, "SocketMap")
    for socket in plan.hull.sockets:
        component = plan.installed.get(socket.key)
        if component is None:
            continue
        node = ET.SubElement(sockets, "HullSocket")
        put(node, "Key", socket.key)
        put(node, "ComponentName", component.key)
        load = plan.ammo.get(socket.key)
        if load:
            data = ET.SubElement(node, "ComponentData", {f"{{{XSI}}}type": "BulkMagazineData"})
            load_node = ET.SubElement(data, "Load")
            for munition, quantity in load:
                item = ET.SubElement(load_node, "MagSaveData")
                put(item, "MagazineKey", _deterministic_uuid(rng).replace("-", "")[:22])
                put(item, "MunitionKey", munition.key)
                put(item, "Quantity", quantity)
    ET.SubElement(ship, "WeaponGroups")
    ET.SubElement(ship, "TemplateMissileTypes")
    ET.SubElement(ship, "TemplateSpacecraftTypes")
    return ship


def build_candidate_pool(catalog, faction_key, budget, seed=0, count=24):
    if faction_key not in catalog.factions:
        raise CatalogError("The selected faction is not present in the native fitting catalogue.")
    if not 1 <= budget <= 30000:
        raise ValueError("Choose a target from 1 to 30,000 points.")
    faction = catalog.factions[faction_key]
    rng = random.Random(seed)
    hulls = [h for h in faction.hulls if h.points <= max(200, budget)]
    if not hulls:
        hulls = list(faction.hulls)
    if not hulls:
        raise CatalogError("The selected faction has no usable stock hulls.")

    pool_root = ET.Element("Fleet")
    put(pool_root, "Name", "NBC Generated Candidates")
    put(pool_root, "Version", 3)
    put(pool_root, "FactionKey", faction_key)
    ET.SubElement(pool_root, "ModDependencies")
    ships = ET.SubElement(pool_root, "Ships")

    attempts = 0
    max_attempts = max(60, count * 8)
    while len(ships) < count and attempts < max_attempts:
        attempts += 1
        hull = rng.choice(hulls)
        plan = Fitter(faction, rng).fit(hull)
        if plan is None:
            continue
        ships.append(_ship_xml(plan, rng, len(ships) + 1))
    if len(ships) == 0:
        raise CatalogError("No valid random fitting could be built from the exported catalogue.")
    ET.SubElement(pool_root, "MissileTypes")
    ET.SubElement(pool_root, "CraftTypes")
    return Fleet(pool_root)


def generate_fleet(catalog_path, faction_key, budget, seed=0, count=24):
    catalog = Catalog.load(catalog_path)
    pool = build_candidate_pool(catalog, faction_key, budget, seed, count)
    # Imported lazily so the fitting module stays independent of the service.
    from .generation import select_generated_fleet
    return select_generated_fleet(pool, budget, seed)
