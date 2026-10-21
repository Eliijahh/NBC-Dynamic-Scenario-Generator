"""Credit pricing and credit-backed purchases/sales.

Native point values remain the game's balance data.  This module maps those
points to campaign credits through a separate, editable price table and keeps
the wallet changes inside the campaign transaction.
"""
from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .core import ROOT, read_xml, ship_state_item
from .fitting import Catalog, empty_ship

DEFAULT_PRICE_FILE = ROOT / "prices.json"
FALLBACK_COMPONENT = 5000
FALLBACK_HULL = 100000


class PriceError(ValueError):
    pass


def format_credits(amount):
    """Format every credit amount the same way, with integer-only values."""
    return f"{int(amount):,} credits"


def _whole_number(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise PriceError(f"{label} must be a whole number of at least {minimum}.")
    return value


def _round_to(value, step):
    if type(step) is not int or step <= 0:
        return int(value)
    return int(((value + step // 2) // step) * step)


class PriceBook:
    """Formula-driven prices with optional per-item overrides."""

    def __init__(self, formula, overrides, sale_percent=10, source=None, repair_discount_percent=30):
        self.formula = formula
        self.overrides = overrides
        self.sale_percent = sale_percent
        self.repair_discount_percent = repair_discount_percent
        self.source = Path(source) if source is not None else None

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path is not None else DEFAULT_PRICE_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PriceError(f"Price table not found: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PriceError(f"Could not read the price table: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise PriceError("Price table must use version 1.")
        sale_percent = _whole_number(data.get("sale_percent", 10), "sale_percent")
        if sale_percent > 100:
            raise PriceError("sale_percent must be between 0 and 100.")
        repair_discount_percent = _whole_number(data.get("repair_discount_percent", 30), "repair_discount_percent")
        if not 0 <= repair_discount_percent <= 99:
            raise PriceError("repair_discount_percent must be between 0 and 99.")

        raw_formula = data.get("formula")
        if not isinstance(raw_formula, dict):
            raise PriceError("Price table formula must be an object.")
        formula = {}
        for kind, fields in (
            ("component", ("base", "points_squared", "minimum", "round_to")),
            ("hull", ("base", "per_point", "minimum", "round_to")),
        ):
            values = raw_formula.get(kind)
            if not isinstance(values, dict):
                raise PriceError(f"Price formula for {kind} is missing.")
            formula[kind] = {
                key: _whole_number(values.get(key, 0), f"{kind}.{key}")
                for key in fields
            }
        fallback = raw_formula.get("fallback", {})
        if not isinstance(fallback, dict):
            raise PriceError("Price formula fallback must be an object.")
        formula["fallback"] = {
            "component": _whole_number(fallback.get("component", FALLBACK_COMPONENT), "fallback.component"),
            "hull": _whole_number(fallback.get("hull", FALLBACK_HULL), "fallback.hull"),
        }

        raw_overrides = data.get("overrides", {})
        if not isinstance(raw_overrides, dict):
            raise PriceError("Price overrides must be an object.")
        overrides = {}
        for kind in ("hulls", "components"):
            values = raw_overrides.get(kind, {})
            if not isinstance(values, dict):
                raise PriceError(f"Price overrides for {kind} must be an object.")
            overrides[kind] = {
                str(key): _whole_number(value, f"{kind}.{key}")
                for key, value in values.items()
            }
        return cls(formula, overrides, sale_percent=sale_percent, repair_discount_percent=repair_discount_percent, source=path)

    def _derived(self, kind, points):
        try:
            points = max(0, int(points))
        except (TypeError, ValueError):
            points = 0
        formula = self.formula[kind]
        if points <= 0:
            return int(formula.get("minimum", 0)) or int(self.formula["fallback"][kind])
        if kind == "component":
            value = formula["base"] + formula["points_squared"] * points * points
        else:
            value = formula["base"] + formula["per_point"] * points
        value = max(value, formula["minimum"])
        return max(1, _round_to(value, formula["round_to"]))

    def component_price(self, key, points=0):
        override = self.overrides["components"].get(str(key))
        if override is not None:
            return override
        if not points:
            return int(self.formula["fallback"]["component"])
        return self._derived("component", points)

    def hull_price(self, key, points=0):
        override = self.overrides["hulls"].get(str(key))
        if override is not None:
            return override
        if not points:
            return int(self.formula["fallback"]["hull"])
        return self._derived("hull", points)

    def sale_value(self, price):
        return max(0, int(price) * self.sale_percent // 100)

    def repair_cost(self, component_value, damage):
        """Price repairing a damage fraction as discounted component value.

        A flat ``repair_discount_percent`` is removed so restoring a component is
        always cheaper than buying a replacement.
        """
        try:
            damage = float(damage)
        except (TypeError, ValueError) as exc:
            raise PriceError("Component damage must be a number.") from exc
        damage = max(0.0, min(1.0, damage))
        if damage <= 0 or component_value <= 0:
            return 0
        return max(1, int(round(component_value * damage * (100 - self.repair_discount_percent) / 100)))

    def validate_catalog(self, catalog):
        """Confirm every exported hull and component resolves to a positive price."""
        hulls = components = 0
        for faction in catalog.factions.values():
            for hull in faction.hulls:
                if self.hull_price(hull.key, hull.points) <= 0:
                    raise PriceError(f"No price for hull {hull.key}.")
                hulls += 1
            for component in faction.components.values():
                if self.component_price(component.key, component.points) <= 0:
                    raise PriceError(f"No price for component {component.key}.")
                components += 1
        return hulls, components


class EconomyService:
    """Price lookup and atomic wallet-backed trade operations for one campaign."""

    def __init__(self, campaign, price_path=None):
        self.campaign = campaign
        self.price_path = Path(price_path) if price_path is not None else DEFAULT_PRICE_FILE
        self._book = None
        self._catalog = None
        self._catalog_stamp = None
        self._component_points = None
        self._hull_index = None

    def price_book(self, reload=False):
        if self._book is None or reload:
            self._book = PriceBook.load(self.price_path)
        return self._book

    def fitting_catalog(self, reload=False):
        path = self.campaign.exchange / "fitting-catalog.xml"
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if not reload and self._catalog is not None and stamp == self._catalog_stamp:
            return self._catalog
        self._catalog = Catalog.load(path) if stamp is not None else None
        self._catalog_stamp = stamp
        self._component_points = None
        self._hull_index = None
        return self._catalog

    def _ensure_index(self):
        catalog = self.fitting_catalog()
        if self._component_points is not None:
            return
        self._component_points = {}
        self._hull_index = {}
        if catalog is None:
            return
        for faction in catalog.factions.values():
            for component in faction.components.values():
                old = self._component_points.get(component.key)
                self._component_points[component.key] = max(component.points, old or 0)
            for hull in faction.hulls:
                self._hull_index[hull.key] = hull

    def component_points(self, key):
        self._ensure_index()
        return self._component_points.get(str(key), 0)

    def hull_for(self, key):
        self._ensure_index()
        return self._hull_index.get(str(key))

    def component_price(self, key, points=None):
        if points is None:
            points = self.component_points(key)
        return self.price_book().component_price(key, points)

    def hull_price(self, key, points=None):
        if points is None:
            hull = self.hull_for(key)
            points = hull.points if hull else 0
        return self.price_book().hull_price(key, points)

    def sale_value(self, price):
        return self.price_book().sale_value(price)

    def component_catalog(self):
        path = self.campaign.exchange / "catalog.xml"
        if not path.exists():
            return {}
        try:
            root = read_xml(path)
        except (OSError, ET.ParseError) as exc:
            raise ValueError(f"Invalid game component catalogue: {exc}") from exc
        if root.tag != "ComponentCatalog":
            raise ValueError("Invalid game component catalogue.")
        result = {}
        for node in root.findall("Component"):
            key = node.findtext("Key")
            if not key:
                continue
            item = {child.tag: child.text or "" for child in node}
            if item.get("Name", "").startswith("$") and "|" in item["Name"]:
                item["Name"] = item["Name"].split("|")[1]
            result[key] = item
        return result

    def validate_catalog(self):
        catalog = self.fitting_catalog()
        if catalog is None:
            raise PriceError("The native fitting catalogue is missing. Start NEBULOUS with the bridge enabled first.")
        return self.price_book().validate_catalog(catalog)

    def buy_component(self, key, quantity):
        self.campaign.ensure_idle()
        if type(quantity) is not int or not 1 <= quantity <= 999:
            raise ValueError("Choose a quantity from 1 to 999.")
        catalog = self.component_catalog()
        if key not in catalog:
            raise ValueError("Component is not in the game-exported catalogue. Start NEBULOUS with the updated bridge first.")
        unit = self.component_price(key)
        total = unit * quantity
        inventory = dict(self.campaign.state["inventory"])
        inventory[key] = inventory.get(key, 0) + quantity
        name = catalog[key].get("Name") or key.replace("Stock/", "")
        with self.campaign.transaction():
            self.campaign.adjust_credits(
                -total,
                "component-purchase",
                f"Bought {quantity} × {name}",
                {"item": key, "quantity": quantity, "unit_price": unit},
            )
            self.campaign.state["inventory"] = inventory
        return total

    def sell_component(self, key, quantity):
        self.campaign.ensure_idle()
        if type(quantity) is not int or not 1 <= quantity <= 999:
            raise ValueError("Choose a quantity from 1 to 999.")
        inventory = dict(self.campaign.state["inventory"])
        count = inventory.get(key, 0) - quantity
        if count < 0:
            raise ValueError("Only spare equipment can be sold; not enough units available.")
        unit = self.component_price(key)
        refund = self.sale_value(unit * quantity)
        if count:
            inventory[key] = count
        else:
            inventory.pop(key, None)
        with self.campaign.transaction():
            self.campaign.adjust_credits(
                refund,
                "component-sale",
                f"Sold {quantity} × {key.replace('Stock/', '')}",
                {"item": key, "quantity": quantity, "unit_price": unit},
            )
            self.campaign.state["inventory"] = inventory
        return refund

    def hull_record(self, key):
        return (self.campaign.state.get("owned_hulls") or {}).get(key)

    def hull_value(self, key):
        record = self.hull_record(key)
        if not record:
            return 0
        return self.hull_price(record.get("hull", ""))

    def buy_hull(self, faction, hull, name, number):
        self.campaign.ensure_idle()
        if not self.campaign.has_campaign:
            raise ValueError("Create or load a campaign first.")
        price = self.hull_price(hull.key, hull.points)
        if self.campaign.credits < price:
            raise ValueError(
                f"Not enough credits for {hull.name}. It costs {format_credits(price)}; "
                f"your balance is {format_credits(self.campaign.credits)}."
            )
        ship = empty_ship(hull, name, number)
        key = self.campaign.add_owned_hull(
            faction,
            ship,
            name=name,
            number=number,
            source="purchase",
            price=price,
            debit=price,
            ledger_description=f"Bought hull {name} ({hull.name})",
            ledger_details={"hull": hull.key, "ship": ship.findtext("Key")},
        )
        return key, price

    def sell_hull(self, key):
        self.campaign.ensure_idle()
        record = self.hull_record(key)
        if not record:
            raise ValueError("That hull is not in the campaign roster.")
        refund = self.sale_value(self.hull_value(key))
        returned = self.campaign.sell_owned_hull(
            key,
            refund=refund,
            ledger_description=f"Sold hull {record.get('name', '')}",
            ledger_details={"hull": record.get("hull", ""), "ship": key},
        )
        return returned, refund

    def _roster_ship(self, key):
        if not self.campaign.player:
            return None
        return next((s for s in self.campaign.player.ships if s.findtext("Key") == key), None)

    def repair_quote(self, key):
        """Return the per-component damage and the credit cost to repair one hull."""
        ship = self._roster_ship(key)
        if ship is None:
            raise ValueError("That hull is not in the campaign roster.")
        state = (self.campaign.state.get("ship_state") or {}).get(key, {})
        records = []
        repairable = []
        destroyed = []
        total = 0
        for entry in state.get("components") or []:
            if not isinstance(entry, dict):
                continue
            component = (entry.get("component") or "").strip()
            try:
                health = float(entry.get("health", 0.0) or 0.0)
            except (TypeError, ValueError):
                health = 0.0
            health = max(0.0, min(1.0, health))
            is_destroyed = bool(entry.get("destroyed")) or health <= 0.0
            value = self.component_price(component) if component else 0
            record = {
                "socket": (entry.get("socket") or "").strip(),
                "component": component,
                "health": 0.0 if is_destroyed else health,
                "damage": 1.0 if is_destroyed else 1.0 - health,
                "destroyed": is_destroyed,
                "value": value,
                "cost": 0,
            }
            if is_destroyed:
                destroyed.append(record)
            else:
                record["cost"] = self.price_book().repair_cost(value, record["damage"])
                total += record["cost"]
                if record["damage"] > 0:
                    repairable.append(record)
            records.append(record)
        status = "damaged" if records else "ready"
        return {
            "key": key,
            "name": ship.findtext("Name", ""),
            "status": status,
            "damaged": bool(records),
            "needs_repair": bool(repairable),
            "cost": total,
            "components": records,
            "repairable": repairable,
            "destroyed_components": destroyed,
        }

    def repair_quotes(self):
        """Return repair quotes for every hull currently in the roster."""
        if not self.campaign.player:
            return []
        return [self.repair_quote(s.findtext("Key")) for s in self.campaign.player.ships]

    def repair_ship(self, key):
        """Repair one hull instantly and debit the campaign wallet atomically."""
        self.campaign.ensure_idle()
        if not self.campaign.has_campaign:
            raise ValueError("Create or load a campaign first.")
        quote = self.repair_quote(key)
        if not quote["damaged"]:
            raise ValueError(f"{quote['name'] or 'That hull'} is already ready.")
        if not quote["needs_repair"]:
            raise ValueError(
                f"{quote['name'] or 'That hull'} has no repairable component damage. "
                "Scrap or refit its destroyed components first."
            )
        cost = quote["cost"]
        if self.campaign.credits < cost:
            raise ValueError(
                f"Not enough credits to repair {quote['name'] or 'that hull'}. "
                f"It costs {format_credits(cost)}; your balance is {format_credits(self.campaign.credits)}."
            )
        with self.campaign.transaction():
            if cost:
                self.campaign.adjust_credits(
                    -cost,
                    "repair",
                    f"Repaired {quote['name'] or key}",
                    {
                        "ship": key,
                        "components": len(quote["repairable"]),
                        "cost": cost,
                    },
                )
            state = self.campaign.state.setdefault("ship_state", {})
            state[key] = ship_state_item(quote["destroyed_components"])
        return quote

    def repair_all(self):
        """Repair component damage on every damaged hull in one atomic change."""
        self.campaign.ensure_idle()
        if not self.campaign.has_campaign:
            raise ValueError("Create or load a campaign first.")
        quotes = [quote for quote in self.repair_quotes() if quote["needs_repair"]]
        if not quotes:
            raise ValueError("No component damage needs repair.")
        total = sum(quote["cost"] for quote in quotes)
        if self.campaign.credits < total:
            raise ValueError(
                f"Not enough credits to repair {len(quotes)} ships. "
                f"The total is {format_credits(total)}; your balance is {format_credits(self.campaign.credits)}."
            )
        with self.campaign.transaction():
            if total:
                self.campaign.adjust_credits(
                    -total,
                    "repair",
                    f"Repaired {len(quotes)} ships",
                    {"ships": [quote["key"] for quote in quotes], "cost": total},
                )
            state = self.campaign.state.setdefault("ship_state", {})
            for quote in quotes:
                state[quote["key"]] = ship_state_item(quote["destroyed_components"])
        return quotes, total

    def ledger(self):
        return list(self.campaign.state.get("credit_ledger") or [])

    def price_summary(self):
        book = self.price_book()
        return {
            "source": str(book.source) if book.source else "",
            "sale_percent": book.sale_percent,
            "repair_discount_percent": book.repair_discount_percent,
            "component_formula": dict(book.formula["component"]),
            "hull_formula": dict(book.formula["hull"]),
            "fallback": dict(book.formula["fallback"]),
        }
