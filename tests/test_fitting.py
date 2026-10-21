import dataclasses
from pathlib import Path
import random
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from nbc.core import Campaign, DEFAULT_GAME
from nbc.fitting import Catalog, Fitter, ShipPlan, build_candidate_pool, empty_ship, generate_fleet
from nbc.generation import GenerationService

FIXTURE = Path(__file__).parent / "fixtures" / "fitting-catalog.xml"
GAME_CATALOG = DEFAULT_GAME / "Saves" / "NBCBridge" / "fitting-catalog.xml"
FACTION = "Stock/Alliance"


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.load(FIXTURE)
        self.faction = self.catalog.factions[FACTION]

    def test_catalogue_parses_native_fitting_data(self):
        self.assertEqual(self.catalog.game_version, "test")
        self.assertEqual(len(self.faction.hulls), 1)
        self.assertIn("Stock/Test Cannon", self.faction.components)
        self.assertIn("Stock/120mm AP", self.faction.munitions)
        self.assertEqual(self.faction.hulls[0].crew, 40)

    def test_socket_fit_checks_size_and_type(self):
        hull = self.faction.hulls[0]
        bow = next(s for s in hull.sockets if s.key == "bow")
        hold = next(s for s in hull.sockets if s.key == "hold-a")
        cannon = self.faction.components["Stock/Test Cannon"]
        heavy = self.faction.components["Stock/Test Heavy Cannon"]
        armor = self.faction.components["Stock/Test Armor"]
        self.assertTrue(cannon.fits(bow))
        self.assertFalse(heavy.fits(bow))
        self.assertFalse(armor.fits(bow))
        self.assertTrue(armor.fits(hold))

    def test_rotation_and_overhang_rules(self):
        base = self.faction.components["Stock/Test Cannon"]
        rotated = dataclasses.replace(base, size=(1, 2, 3), rotate=1)
        socket = next(s for s in self.faction.hulls[0].sockets if s.key == "bow")
        socket = dataclasses.replace(socket, size=(3, 2, 1), type=0)
        self.assertTrue(rotated.fits(socket))
        overhang = dataclasses.replace(base, overhang=1)
        self.assertFalse(overhang.fits(socket))

    def test_missiles_hangars_and_oversized_parts_are_not_candidates(self):
        hull = self.faction.hulls[0]
        bow = next(s for s in hull.sockets if s.key == "bow")
        fitter = Fitter(self.faction, __import__("random").Random(1))
        keys = {c.key for c in fitter.options(bow)}
        self.assertIn("Stock/Test Cannon", keys)
        self.assertNotIn("Stock/Test Missile", keys)
        self.assertNotIn("Stock/Test Hangar", keys)
        self.assertNotIn("Stock/Test Heavy Cannon", keys)

    def test_ewar_and_sensor_roles_are_not_primary_weapons(self):
        cannon = self.faction.components["Stock/Test Cannon"]
        ewar = dataclasses.replace(
            cannon,
            category="EWar",
            weapon=dataclasses.replace(cannon.weapon, role="Offensive", wep_type="EWar"),
        )
        sensor = dataclasses.replace(
            cannon,
            category="Sensors",
            weapon=dataclasses.replace(cannon.weapon, role="Offensive", wep_type="EWar"),
        )
        self.assertFalse(ewar.is_offensive_weapon)
        self.assertFalse(sensor.is_offensive_weapon)

    def test_dual_role_direct_fire_gun_is_still_a_primary_weapon(self):
        cannon = self.faction.components["Stock/Test Cannon"]
        dual = dataclasses.replace(
            cannon,
            weapon=dataclasses.replace(cannon.weapon, role="Offensive, Defensive"),
        )
        self.assertTrue(dual.is_offensive_weapon)

    def test_empty_ship_has_identity_hull_and_no_components(self):
        hull = self.faction.hulls[0]
        ship = empty_ship(hull, "New Hull", 77)
        self.assertEqual(ship.findtext("Name"), "New Hull")
        self.assertEqual(ship.findtext("Number"), "77")
        self.assertEqual(ship.findtext("HullType"), hull.key)
        self.assertEqual(ship.findtext("Cost"), str(hull.points))
        self.assertIsNotNone(ship.find("SocketMap"))
        self.assertEqual(ship.findall("SocketMap/HullSocket"), [])

    def test_compounding_cost_sorts_by_base_cost_before_first_free(self):
        hull = self.faction.hulls[0]
        armor = self.faction.components["Stock/Test Armor"]
        free = dataclasses.replace(armor, key="free", points=20, first_free=True)
        paid = dataclasses.replace(armor, key="paid", points=10, first_free=False)
        fitter = Fitter(self.faction, random.Random(1))
        fitter.plan = ShipPlan(hull, {"hold-a": free, "hold-b": paid})
        fitter._calculate_cost()
        # The free first instance sorts first, then the 10-point part pays the
        # 1.5x compounding multiplier for the second slot.
        self.assertEqual(fitter.plan.cost, hull.points + 15)

    def test_tiled_cost_and_capacity_use_socket_volume(self):
        hull = self.faction.hulls[0]
        hold = next(s for s in hull.sockets if s.key == "hold-a")
        armor = self.faction.components["Stock/Test Armor"]
        self.assertEqual(armor.tile_multiplier(hold), 1)
        small = dataclasses.replace(armor, size=(1, 1, 1))
        self.assertEqual(small.tile_multiplier(hold), 8)
        self.assertEqual(small.point_cost(hold), 20)


class OfflineGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load(FIXTURE)

    def test_generated_fleet_is_valid_and_uses_supported_parts(self):
        fleet = generate_fleet(FIXTURE, FACTION, 500, seed=42)
        fleet.validate()
        self.assertTrue(1 <= len(fleet.ships) <= 10)
        self.assertGreater(fleet.points, 0)
        self.assertIsNotNone(fleet.root.find("ModDependencies"))
        self.assertIsNotNone(fleet.root.find("MissileTypes"))
        self.assertIsNotNone(fleet.root.find("CraftTypes"))
        for ship in fleet.ships:
            components = [s.findtext("ComponentName") for s in ship.findall("SocketMap/HullSocket")]
            self.assertNotIn("Stock/Test Missile", components)
            self.assertNotIn("Stock/Test Hangar", components)
            self.assertTrue(any(c in components for c in ("Stock/Test Cannon", "Stock/Test Beam")))
        self.assertTrue(any(ship.findall(".//MagSaveData") for ship in fleet.ships))

    def test_generation_is_deterministic_for_a_seed(self):
        a = generate_fleet(FIXTURE, FACTION, 500, seed=123)
        b = generate_fleet(FIXTURE, FACTION, 500, seed=123)
        self.assertEqual(ET.tostring(a.root), ET.tostring(b.root))

    def test_candidate_pool_has_positive_costs_and_no_missiles(self):
        pool = build_candidate_pool(self.catalog, FACTION, 500, seed=7, count=12)
        self.assertEqual(len(pool.ships), 12)
        for ship in pool.ships:
            self.assertGreater(int(ship.findtext("Cost", "0")), 0)
            components = [s.findtext("ComponentName") for s in ship.findall("SocketMap/HullSocket")]
            self.assertNotIn("Stock/Test Missile", components)
            self.assertNotIn("Stock/Test Hangar", components)


class OfflineGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.root / "data"
        exchange = self.game / "Saves" / "NBCBridge"
        exchange.mkdir(parents=True)
        shutil.copyfile(FIXTURE, exchange / "fitting-catalog.xml")
        self.campaign = Campaign(self.game, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def test_start_fits_enemy_without_launching_game(self):
        service = GenerationService(self.campaign)
        record = service.start(FACTION, 500, seed=9)
        self.assertEqual(record["method"], "offline")
        self.assertNotIn("active_generation", self.campaign.state)
        self.assertEqual(record["faction"], FACTION)
        self.assertEqual(record["actual_points"], self.campaign.enemy.points)
        self.assertTrue((self.data / "enemy.fleet").exists())
        self.assertEqual(self.campaign.state["last_generation"]["seed"], 9)
        self.assertEqual(self.campaign.runtime.purpose, None)

        restarted = Campaign(self.game, self.data)
        self.assertIsNotNone(restarted.enemy)
        self.assertEqual(restarted.enemy.points, record["actual_points"])

    def test_missing_catalogue_has_actionable_error(self):
        (self.campaign.exchange / "fitting-catalog.xml").unlink()
        with self.assertRaisesRegex(ValueError, "catalogue is missing"):
            GenerationService(self.campaign).start(FACTION, 500, seed=9)


@unittest.skipUnless(GAME_CATALOG.exists(), "the installed game has not exported a fitting catalogue")
class InstalledCatalogueTests(unittest.TestCase):
    def test_real_catalogue_generates_plausible_ships(self):
        catalog = Catalog.load(GAME_CATALOG)
        self.assertIn("Stock/Alliance", catalog.factions)
        self.assertIn("Stock/Protectorate", catalog.factions)
        for faction_key, faction in catalog.factions.items():
            fleet = generate_fleet(GAME_CATALOG, faction_key, 700, seed=1, count=8)
            fleet.validate()
            self.assertTrue(1 <= len(fleet.ships) <= 10)
            for ship in fleet.ships:
                installed = [
                    faction.components[h.findtext("ComponentName")]
                    for h in ship.findall("SocketMap/HullSocket")
                ]
                self.assertTrue(any(c.is_offensive_weapon for c in installed))
                self.assertFalse(any(c.is_launcher or c.is_hangar for c in installed))


if __name__ == "__main__":
    unittest.main()
