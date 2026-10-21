import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

from nbc.generation import select_generated_fleet
from nbc.core import Campaign, Fleet, DEFAULT_GAME, DEFAULT_STARTING_CREDITS, STATE_VERSION, atomic_bytes, discover, put, ship_damage_fraction, write_xml


def sample(name="Test Fleet", faction="Stock/Alliance"):
    root = ET.fromstring(f'''<Fleet><Name>{name}</Name><Version>3</Version><FactionKey>{faction}</FactionKey><Ships>
      <Ship><Key>{uuid.uuid4()}</Key><Name>Resolute</Name><Number>42</Number><Cost>350</Cost>
      <HullType>Stock/Raines Frigate</HullType><SocketMap><HullSocket><Key>socket</Key>
      <ComponentName>Stock/Mk62 Cannon</ComponentName><ComponentData><FutureField>preserve</FutureField></ComponentData>
      </HullSocket></SocketMap><UnknownFutureData important="yes">keep</UnknownFutureData></Ship>
      </Ships><MissileTypes/><CraftTypes/></Fleet>''')
    return Fleet(root)


def empty_hull(name, number, hull="Stock/Raines Frigate"):
    return ET.fromstring(
        f"<Ship><Key>{uuid.uuid4()}</Key><Name>{name}</Name><Cost>100</Cost>"
        f"<Number>{number}</Number><HullType>{hull}</HullType><SocketMap /></Ship>"
    )


def fitted_ship(name, number, *parts, hull="Stock/Raines Frigate"):
    ship = empty_hull(name, number, hull)
    socket_map = ship.find("SocketMap")
    for key, component in parts:
        socket = ET.SubElement(socket_map, "HullSocket")
        ET.SubElement(socket, "Key").text = key
        ET.SubElement(socket, "ComponentName").text = component
    return ship


def fleet_of(*ships, name="Battle Fleet", faction="Stock/Alliance"):
    fleet = sample(name, faction)
    container = fleet.root.find("Ships")
    for child in list(container):
        container.remove(child)
    for ship in ships:
        container.append(ship)
    return fleet


def ship_by_key(fleet, key):
    return next(ship for ship in fleet.ships if ship.findtext("Key") == key)


class FleetTests(unittest.TestCase):
    def test_round_trip_preserves_unknown_xml(self):
        with tempfile.TemporaryDirectory() as d:
            f = sample()
            f.save(Path(d) / "test.fleet")
            result = Fleet.load(Path(d) / "test.fleet")
            self.assertEqual(result.ships[0].findtext("UnknownFutureData"), "keep")
            self.assertEqual(result.ships[0].findtext(".//FutureField"), "preserve")

    def test_duplicate_and_remove_preserve_unique_identity(self):
        f = sample()
        f.duplicate(0)
        self.assertNotEqual(f.ships[0].findtext("Key"), f.ships[1].findtext("Key"))
        f.validate()
        self.assertEqual(f.points, 700)
        f.remove(0)
        with self.assertRaises(ValueError):
            f.remove(0)

    def test_same_hull_loadout_keeps_identity_and_metadata(self):
        f, donor = sample(), sample("Other")
        original_key = f.ships[0].findtext("Key")
        put(donor.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/New Weapon")
        f.apply_loadout(0, donor, 0)
        self.assertEqual(f.ships[0].findtext("Key"), original_key)
        self.assertEqual(f.ships[0].findtext("SocketMap/HullSocket/ComponentName"), "Stock/New Weapon")
        self.assertEqual(f.ships[0].findtext("UnknownFutureData"), "keep")

    def test_incompatible_loadout_rejected_without_mutation(self):
        f, donor = sample(), sample(faction="Stock/Protectorate")
        before = ET.tostring(f.root)
        with self.assertRaises(ValueError):
            f.apply_loadout(0, donor, 0)
        self.assertEqual(ET.tostring(f.root), before)

    def test_component_edit_preserves_other_sockets_and_identity(self):
        f, donor = sample(), sample("Other")
        extra = ET.fromstring("<HullSocket><Key>reactor</Key><ComponentName>Stock/Reactor</ComponentName></HullSocket>")
        f.ships[0].find("SocketMap").append(extra)
        original_key = f.ships[0].findtext("Key")
        put(donor.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/New Weapon")
        f.apply_component(0, "socket", donor, 0)
        self.assertEqual(f.ships[0].findtext("Key"), original_key)
        self.assertEqual(f.ships[0].findtext("SocketMap/HullSocket[Key='socket']/ComponentName"), "Stock/New Weapon")
        self.assertEqual(f.ships[0].findtext("SocketMap/HullSocket[Key='reactor']/ComponentName"), "Stock/Reactor")

    def test_names_must_be_unique_for_report_mapping(self):
        f = sample()
        f.duplicate(0)
        put(f.ships[1], "Name", "Resolute")
        with self.assertRaises(ValueError):
            f.validate()

    def test_ship_damage_fraction_weights_components_by_damage(self):
        ship = fitted_ship("Resolute", 42, *[(f"slot-{index}", "Stock/Test Cannon") for index in range(10)])
        item = {
            "status": "damaged",
            "components": [
                {"socket": "slot-9", "component": "Stock/Test Cannon", "health": 0.1, "destroyed": False}
            ],
        }
        self.assertAlmostEqual(ship_damage_fraction(ship, item), 0.09)

        item["components"].append(
            {"socket": "slot-8", "component": "Stock/Test Cannon", "health": 0.5, "destroyed": False}
        )
        self.assertAlmostEqual(ship_damage_fraction(ship, item), 0.14)

        item["components"] = [
            {"socket": "slot-9", "component": "Stock/Test Cannon", "health": 0.0, "destroyed": True}
        ]
        self.assertAlmostEqual(ship_damage_fraction(ship, item), 0.10)

    def test_ship_damage_fraction_ignores_missing_sockets(self):
        ship = fitted_ship(
            "Resolute", 42, ("weapon", "Stock/Test Cannon"), ("reactor", "Stock/Test Reactor")
        )
        item = {
            "status": "damaged",
            "components": [
                {"socket": "weapon", "component": "Stock/Test Cannon", "health": 0.5, "destroyed": False},
                {"socket": "gone", "component": "Stock/Test Cannon", "health": 0.0, "destroyed": True},
            ],
        }
        self.assertAlmostEqual(ship_damage_fraction(ship, item), 0.25)

    def test_enemy_seed_and_faction(self):
        pool = sample()
        a = select_generated_fleet(pool, 350, seed=123)
        b = select_generated_fleet(pool, 350, seed=123)
        self.assertEqual(ET.tostring(a.root), ET.tostring(b.root))
        self.assertEqual(a.points, 350)
        a.validate()
        with self.assertRaises(ValueError):
            select_generated_fleet(sample(), 0)


class CampaignSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.campaign = Campaign(self.root / "game", self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def test_new_campaign_creates_metadata_and_starter_save(self):
        starter = sample("Starter Fleet")
        meta = self.campaign.start_new_campaign(
            "First Watch", "Cmdr Vale", "Stock/Alliance", DEFAULT_STARTING_CREDITS, starter
        )
        self.assertEqual(meta["name"], "First Watch")
        self.assertEqual(meta["commander"], "Cmdr Vale")
        self.assertEqual(meta["faction"], "Stock/Alliance")
        self.assertEqual(meta["credits"], DEFAULT_STARTING_CREDITS)
        self.assertEqual(meta["starting_fleet"], "Starter Fleet")
        self.assertEqual(meta["starting_ships"], 1)
        self.assertEqual(meta["starting_points"], 350)
        self.assertFalse(meta["legacy"])

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertTrue(restarted.has_campaign)
        self.assertEqual(restarted.metadata["name"], "First Watch")
        self.assertEqual(restarted.player.name, "Starter Fleet")
        self.assertEqual(restarted.player.points, 350)
        self.assertIsNone(restarted.enemy)
        self.assertEqual(restarted.state["battles"], [])
        self.assertEqual(restarted.state["ship_results"], {})
        self.assertEqual(restarted.state["inventory"], {})
        self.assertEqual(restarted.state["version"], STATE_VERSION)
        self.assertEqual(restarted.campaign_status, "active")
        self.assertEqual(restarted.credits, DEFAULT_STARTING_CREDITS)
        ship_key = restarted.player.ships[0].findtext("Key")
        owned = restarted.state["owned_hulls"][ship_key]
        self.assertEqual(owned["hull"], "Stock/Raines Frigate")
        self.assertEqual(owned["name"], "Resolute")
        self.assertEqual(owned["number"], 42)
        self.assertIn("<Name>Resolute</Name>", owned["ship"])
        self.assertEqual(restarted.state["mission_offers"], [])
        self.assertIsNone(restarted.state["accepted_mission"])
        self.assertEqual(restarted.state["ship_state"][ship_key], {"status": "ready"})
        self.assertIsNone(restarted.pending_action())

    def test_owned_hulls_are_unlimited_and_survive_restart(self):
        self.campaign.start_new_campaign(
            "Hull Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Hull Starter")
        )
        starter_key = self.campaign.player.ships[0].findtext("Key")
        for index in range(11):
            self.campaign.add_owned_hull(
                "Stock/Alliance", empty_hull(f"Auxiliary {index + 1}", 100 + index), name=f"Auxiliary {index + 1}", number=100 + index
            )

        self.assertEqual(len(self.campaign.player.ships), 12)
        self.assertEqual(len(self.campaign.state["owned_hulls"]), 12)
        self.assertFalse(self.campaign.can_deploy())

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(len(restarted.player.ships), 12)
        self.assertEqual(len(restarted.state["owned_hulls"]), 12)
        self.assertEqual(restarted.player.ships[0].findtext("Key"), starter_key)

    def test_owned_hull_identity_faction_and_empty_loadout_are_guarded(self):
        self.campaign.start_new_campaign(
            "Guard Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Guard Starter")
        )
        self.campaign.add_owned_hull("Stock/Alliance", empty_hull("Auxiliary", 100), name="Auxiliary", number=100)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.campaign.add_owned_hull("Stock/Alliance", empty_hull("Auxiliary", 101), name="Auxiliary", number=101)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.campaign.add_owned_hull("Stock/Alliance", empty_hull("Auxiliary 2", 100), name="Auxiliary 2", number=100)
        with self.assertRaisesRegex(ValueError, "faction"):
            self.campaign.add_owned_hull("Stock/Protectorate", empty_hull("Foreign", 102), name="Foreign", number=102)
        loaded = empty_hull("Loaded", 103)
        socket = ET.SubElement(loaded.find("SocketMap"), "HullSocket")
        ET.SubElement(socket, "Key").text = "socket"
        ET.SubElement(socket, "ComponentName").text = "Stock/Mk62 Cannon"
        with self.assertRaisesRegex(ValueError, "empty"):
            self.campaign.add_owned_hull("Stock/Alliance", loaded, name="Loaded", number=103)

    def test_sell_hull_returns_components_and_allows_an_empty_roster(self):
        self.campaign.start_new_campaign(
            "Sale Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Sale Starter")
        )
        first_key = self.campaign.player.ships[0].findtext("Key")
        self.campaign.add_owned_hull("Stock/Alliance", empty_hull("Auxiliary", 100), name="Auxiliary", number=100)
        self.campaign.add_owned_hull("Stock/Alliance", empty_hull("Tender", 101), name="Tender", number=101)

        returned = self.campaign.sell_owned_hull(first_key)
        self.assertEqual(returned, {"Stock/Mk62 Cannon": 1})
        self.assertEqual(self.campaign.state["inventory"]["Stock/Mk62 Cannon"], 1)
        self.assertNotIn(first_key, self.campaign.state["owned_hulls"])
        self.assertNotIn(first_key, self.campaign.state["ship_state"])

        self.campaign.sell_owned_hull(self.campaign.player.ships[0].findtext("Key"))
        self.campaign.sell_owned_hull(self.campaign.player.ships[0].findtext("Key"))
        self.assertEqual(self.campaign.player.ships, [])
        self.assertEqual(self.campaign.state["owned_hulls"], {})
        self.assertEqual(self.campaign.state["ship_state"], {})
        self.assertFalse(self.campaign.can_deploy())

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(restarted.player.ships, [])
        self.assertEqual(restarted.state["owned_hulls"], {})
        self.assertEqual(restarted.state["inventory"]["Stock/Mk62 Cannon"], 1)

    def test_scrap_installed_component_removes_it_without_inventory_return(self):
        self.campaign.start_new_campaign(
            "Scrap Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Scrap Starter")
        )
        ship = self.campaign.player.ships[0]
        key = ship.findtext("Key")
        socket = ship.find("SocketMap/HullSocket")
        socket_key = socket.findtext("Key")
        component = socket.findtext("ComponentName")
        credits = self.campaign.credits

        scrapped = self.campaign.scrap_installed_component(key, socket_key)
        self.assertEqual(scrapped, component)
        self.assertIsNone(ship_by_key(self.campaign.player, key).find("SocketMap/HullSocket"))
        self.assertEqual(self.campaign.credits, credits)
        self.assertEqual(self.campaign.state["inventory"], {})
        owned = ET.fromstring(self.campaign.state["owned_hulls"][key]["ship"])
        self.assertIsNone(owned.find("SocketMap/HullSocket"))
        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertIsNone(ship_by_key(restarted.player, key).find("SocketMap/HullSocket"))

    def test_damaged_ships_block_deployment_until_repaired(self):
        self.campaign.start_new_campaign(
            "Repair Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Repair Starter")
        )
        key = self.campaign.player.ships[0].findtext("Key")
        self.assertTrue(self.campaign.can_deploy())
        self.campaign.state["ship_state"][key] = {
            "status": "damaged",
            "components": [
                {"socket": "socket", "component": "Stock/Mk62 Cannon", "health": 0.5, "destroyed": False}
            ],
        }
        self.assertFalse(self.campaign.can_deploy())
        self.assertEqual([ship.findtext("Key") for ship in self.campaign.unready_ships()], [key])
        self.campaign.state["ship_state"][key] = {"status": "ready"}
        self.assertTrue(self.campaign.can_deploy())

    def test_refit_reconciliation_clears_damage_for_a_replaced_component(self):
        self.campaign.start_new_campaign(
            "Refit Damage", "Cmdr Vale", "Stock/Alliance", 1000, sample("Refit Starter")
        )
        key = self.campaign.player.ships[0].findtext("Key")
        self.campaign.state["ship_state"][key] = {
            "status": "damaged",
            "components": [
                {"socket": "socket", "component": "Stock/Mk62 Cannon", "health": 0.5, "destroyed": False}
            ],
        }
        self.campaign.sync_ship_state()
        self.assertFalse(self.campaign.ship_ready(key))

        put(self.campaign.player.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Test Cannon")
        self.campaign.sync_ship_state()
        self.assertTrue(self.campaign.ship_ready(key))
        self.assertEqual(self.campaign.state["ship_state"][key], {"status": "ready"})

    def test_existing_v2_roster_is_backfilled_into_owned_hulls(self):
        data = self.root / "owned-hull-migration"
        data.mkdir()
        fleet = sample("Migrated Roster")
        (data / "campaign.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "campaign": {"name": "Migrated", "faction": "Stock/Alliance", "credits": 7},
                    "campaign_status": "active",
                    "player_xml": ET.tostring(fleet.root, encoding="unicode"),
                    "battles": [],
                    "ship_results": {},
                    "ship_state": {},
                    "owned_hulls": {},
                    "mission_offers": [],
                    "accepted_mission": None,
                    "active_battle": None,
                    "inventory": {},
                    "credit_ledger": [],
                    "active_refit": None,
                    "refits": [],
                }
            ),
            encoding="utf-8",
        )

        campaign = Campaign(self.campaign.game, data)

        key = fleet.ships[0].findtext("Key")
        self.assertEqual(campaign.state["owned_hulls"][key]["name"], "Resolute")
        self.assertEqual(campaign.player.ships[0].findtext("Key"), key)
        backups = list((data / "campaign-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("owned-hulls", backups[0].name)

        Campaign(self.campaign.game, data)
        self.assertEqual(len(list((data / "campaign-backups").iterdir())), 1)

    def test_schema_migration_from_v1_backs_up_and_preserves_data(self):
        data = self.root / "migrated"
        data.mkdir()
        fleet = sample("Legacy Fleet")
        ship_key = fleet.ships[0].findtext("Key")
        state = {
            "version": 1,
            "campaign": {"name": "Legacy Watch", "credits": 1234},
            "player_xml": ET.tostring(fleet.root, encoding="unicode"),
            "battles": [{"id": "old-battle"}],
            "ship_results": {ship_key: {"Condition": "0.72"}},
            "active_battle": None,
            "inventory": {"Stock/Mk62 Cannon": 2},
            "active_refit": None,
            "refits": [],
            "future_data": {"preserve": True},
        }
        (data / "campaign.json").write_text(json.dumps(state), encoding="utf-8")

        campaign = Campaign(self.campaign.game, data)

        self.assertEqual(campaign.state["version"], STATE_VERSION)
        self.assertEqual(campaign.metadata["name"], "Legacy Watch")
        self.assertEqual(campaign.credits, 1234)
        self.assertEqual(campaign.campaign_status, "active")
        self.assertEqual(campaign.state["inventory"], {"Stock/Mk62 Cannon": 2})
        self.assertEqual(campaign.state["ship_results"][ship_key]["Condition"], "0.72")
        self.assertEqual(campaign.state["ship_state"][ship_key], {"status": "ready"})
        self.assertEqual(campaign.state["mission_offers"], [])
        self.assertIsNone(campaign.state["accepted_mission"])
        self.assertEqual(campaign.state["future_data"], {"preserve": True})

        backups = list((data / "campaign-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("schema-v1-to-v2", backups[0].name)
        backup = json.loads((backups[0] / "campaign.json").read_text(encoding="utf-8"))
        self.assertEqual(backup["version"], 1)
        self.assertEqual(backup["campaign"]["credits"], 1234)

    def test_v2_normalization_backs_up_previous_save(self):
        data = self.root / "normalized"
        data.mkdir()
        fleet = sample("Normalize Fleet")
        (data / "campaign.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "campaign": {"name": "Normalize Watch", "credits": 42},
                    "player_xml": ET.tostring(fleet.root, encoding="unicode"),
                }
            ),
            encoding="utf-8",
        )

        campaign = Campaign(self.campaign.game, data)

        self.assertEqual(campaign.state["version"], STATE_VERSION)
        self.assertEqual(campaign.credits, 42)
        self.assertEqual(campaign.credit_ledger, [])
        self.assertEqual(campaign.state["mission_offers"], [])
        backups = list((data / "campaign-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("normalize-v2", backups[0].name)

        Campaign(self.campaign.game, data)
        self.assertEqual(len(list((data / "campaign-backups").iterdir())), 1)

    def test_legacy_destroyed_components_migrate_to_component_damage(self):
        data = self.root / "component-damage-migration"
        data.mkdir()
        fleet = sample("Migrated Damage")
        key = fleet.ships[0].findtext("Key")
        (data / "campaign.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "campaign": {"name": "Damage Watch", "faction": "Stock/Alliance", "credits": 42},
                    "campaign_status": "active",
                    "player_xml": ET.tostring(fleet.root, encoding="unicode"),
                    "ship_state": {
                        key: {
                            "condition": 0.5,
                            "status": "damaged",
                            "destroyed_components": [
                                {"socket": "socket", "component": "Stock/Mk62 Cannon"}
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        campaign = Campaign(self.campaign.game, data)

        self.assertEqual(
            campaign.state["ship_state"][key],
            {
                "status": "damaged",
                "components": [
                    {"socket": "socket", "component": "Stock/Mk62 Cannon", "health": 0.0, "destroyed": True}
                ],
            },
        )
        self.assertFalse(campaign.ship_ready(key))

    def test_unattributable_legacy_condition_is_dropped(self):
        data = self.root / "partial-condition-migration"
        data.mkdir()
        fleet = sample("Partial Damage")
        key = fleet.ships[0].findtext("Key")
        (data / "campaign.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "campaign": {"name": "Partial Watch", "faction": "Stock/Alliance", "credits": 42},
                    "campaign_status": "active",
                    "player_xml": ET.tostring(fleet.root, encoding="unicode"),
                    "ship_state": {key: {"condition": 0.4, "status": "damaged"}},
                }
            ),
            encoding="utf-8",
        )

        campaign = Campaign(self.campaign.game, data)

        self.assertEqual(campaign.state["ship_state"][key], {"status": "ready"})
        self.assertTrue(campaign.ship_ready(key))

    def test_newer_or_invalid_schema_versions_are_rejected(self):
        for version, message in ((99, "newer"), ("1", "invalid")):
            with self.subTest(version=version):
                data = self.root / f"version-{version}"
                data.mkdir()
                (data / "campaign.json").write_text(json.dumps({"version": version}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    Campaign(self.campaign.game, data)

    def test_pending_mission_state_persists_and_locks_idle_actions(self):
        self.campaign.start_new_campaign(
            "Mission Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Mission Starter")
        )
        self.campaign.state["accepted_mission"] = {"id": "mission-1", "name": "Patrol"}
        self.campaign.persist()

        self.assertEqual(self.campaign.pending_action(), "mission")
        with self.assertRaisesRegex(ValueError, "pending mission"):
            self.campaign.ensure_idle()

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(restarted.pending_action(), "mission")
        self.assertEqual(restarted.accepted_mission["id"], "mission-1")
        self.assertEqual(restarted.campaign_status, "active")

    def test_credit_and_campaign_status_transactions_persist(self):
        self.campaign.start_new_campaign(
            "Ledger Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Ledger Starter")
        )
        self.campaign.set_credits(5000)
        self.campaign.set_campaign_status("completed")

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(restarted.credits, 5000)
        self.assertEqual(restarted.campaign_status, "completed")
        with self.assertRaises(ValueError):
            restarted.set_credits(-1)
        with self.assertRaises(ValueError):
            restarted.set_campaign_status("unknown")

    def test_transaction_restores_state_when_persist_fails(self):
        self.campaign.start_new_campaign(
            "Rollback Watch", "Cmdr Vale", "Stock/Alliance", 1000, sample("Rollback Starter")
        )
        before = copy.deepcopy(self.campaign.state)
        with patch.object(self.campaign, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                with self.campaign.transaction():
                    self.campaign.state["inventory"] = {"Stock/Radar": 1}
        self.assertEqual(self.campaign.state, before)

    def test_new_campaign_backs_up_previous_save_and_clears_enemy(self):
        self.campaign.player = sample("Old Fleet")
        self.campaign.enemy = sample("Old Enemy", "Stock/Protectorate")
        self.campaign.save_fleets()
        self.assertTrue((self.campaign.data / "enemy.fleet").exists())

        self.campaign.start_new_campaign(
            "Replacement", "Cmdr New", "Stock/Alliance", 250_000, sample("New Starter")
        )

        backups = list((self.campaign.data / "campaign-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "campaign.json").exists())
        self.assertTrue((backups[0] / "player.fleet").exists())
        self.assertTrue((backups[0] / "enemy.fleet").exists())
        self.assertFalse((self.campaign.data / "enemy.fleet").exists())
        self.assertIsNone(self.campaign.enemy)

    def test_legacy_save_gets_campaign_metadata(self):
        self.campaign.player = sample("Old Fleet")
        self.campaign.save_fleets()
        self.assertFalse(self.campaign.has_campaign)

        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertTrue(restarted.has_campaign)
        self.assertTrue(restarted.metadata["legacy"])
        self.assertEqual(restarted.metadata["name"], "Old Fleet")
        self.assertEqual(restarted.metadata["credits"], 0)
        self.assertEqual(restarted.metadata["starting_points"], 350)

    def test_new_campaign_validates_setup(self):
        starter = sample()
        cases = [
            ("", "Cmdr", "Stock/Alliance", 1000, starter),
            ("Campaign", "", "Stock/Alliance", 1000, starter),
            ("Campaign", "Cmdr", "Unknown", 1000, starter),
            ("Campaign", "Cmdr", "Stock/Alliance", -1, starter),
            ("Campaign", "Cmdr", "Stock/Alliance", True, starter),
            ("Campaign", "Cmdr", "Stock/Protectorate", 1000, starter),
            ("Campaign", "Cmdr", "Stock/Alliance", 1000, None),
        ]
        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.campaign.start_new_campaign(*args)

    def test_new_campaign_is_blocked_by_pending_activity(self):
        for key in ("active_battle", "active_refit", "accepted_mission"):
            with self.subTest(key=key):
                self.campaign.state[key] = {"id": "pending"}
                with self.assertRaises(ValueError):
                    self.campaign.start_new_campaign(
                        "Campaign", "Cmdr", "Stock/Alliance", 1000, sample()
                    )
                self.assertEqual(self.campaign.state[key], {"id": "pending"})
                self.campaign.state[key] = None


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.campaign = Campaign(self.root / "game", self.root / "data")
        self.campaign.player = sample()
        self.campaign.enemy = sample("Enemy", "Stock/Protectorate")

    def tearDown(self):
        self.temp.cleanup()

    def complete(self, battle, identity=None):
        root = ET.Element("BattleResult")
        put(root, "Id", identity or battle["id"])
        put(root, "Outcome", "Victory")
        put(root, "Duration", 600)
        ships = ET.SubElement(root, "Ships")
        ship = ET.SubElement(ships, "Ship")
        for key, value in {"Key": self.campaign.player.ships[0].findtext("Key"), "Side": "Player", "Condition": "1", "Status": "NotEliminated"}.items():
            put(ship, key, value)
        write_xml(Path(battle["directory"]) / "result.xml", root)

    def write_result(self, battle, ships):
        root = ET.Element("BattleResult")
        put(root, "Id", battle["id"])
        put(root, "Outcome", "Victory")
        put(root, "Duration", 600)
        container = ET.SubElement(root, "Ships")
        for key, name, status, condition in ships:
            node = ET.SubElement(container, "Ship")
            for tag, value in {
                "Key": key,
                "Side": "Player",
                "Name": name,
                "Status": status,
                "Condition": condition,
            }.items():
                put(node, tag, value)
        write_xml(Path(battle["directory"]) / "result.xml", root)

    def write_report(self, battle, ships):
        root = ET.Element("FullAfterActionReport")
        teams = ET.SubElement(root, "Teams")
        team = ET.SubElement(teams, "TeamReportOfShipBattleReportCraftBattleReport")
        players = ET.SubElement(team, "Players")
        player = ET.SubElement(players, "AARPlayerReportOfShipBattleReportCraftBattleReport")
        put(player, "IsLocalPlayer", "true")
        container = ET.SubElement(player, "Ships")
        for name, parts in ships.items():
            ship = ET.SubElement(container, "ShipBattleReport")
            put(ship, "ShipName", name)
            part_status = ET.SubElement(ship, "PartStatus")
            for key, health, destroyed in parts:
                part = ET.SubElement(part_status, "PartDamage")
                put(part, "Key", key)
                put(part, "HealthPercent", health)
                put(part, "IsDestroyed", "true" if destroyed else "false")
        write_xml(Path(battle["directory"]) / "report.xml", root)

    def install_catalog(self):
        self.campaign.exchange.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            Path(__file__).parent / "fixtures" / "fitting-catalog.xml",
            self.campaign.exchange / "fitting-catalog.xml",
        )

    def test_prepare_exports_native_fleets_and_locks_pending_battle(self):
        battle = self.campaign.prepare()
        self.assertTrue((self.campaign.exchange / "request.xml").exists())
        self.assertTrue((Path(battle["directory"]) / "player.fleet").exists())
        with self.assertRaises(ValueError):
            self.campaign.prepare()

    def test_results_import_once_and_survive_restart(self):
        battle = self.campaign.prepare()
        self.complete(battle)
        result = self.campaign.collect()
        self.assertEqual(result["outcome"], "Victory")
        self.assertIsNone(self.campaign.collect())
        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(len(restarted.state["battles"]), 1)
        key = self.campaign.player.ships[0].findtext("Key")
        self.assertEqual(restarted.state["ship_results"][key]["Condition"], "1")
        self.assertEqual(restarted.state["ship_state"][key], {"status": "ready"})

    def test_prepare_rejects_damaged_ship_until_repaired(self):
        key = self.campaign.player.ships[0].findtext("Key")
        self.campaign.state["ship_state"][key] = {
            "status": "damaged",
            "components": [
                {"socket": "socket", "component": "Stock/Mk62 Cannon", "health": 0.5, "destroyed": False}
            ],
        }
        with self.assertRaisesRegex(ValueError, "Repair damaged ships"):
            self.campaign.prepare()
        self.assertIsNone(self.campaign.state["active_battle"])
        self.campaign.state["ship_state"][key] = {"status": "ready"}
        battle = self.campaign.prepare()
        self.assertEqual(self.campaign.state["active_battle"]["id"], battle["id"])

    def test_wrong_battle_result_rejected(self):
        battle = self.campaign.prepare()
        self.complete(battle, str(uuid.uuid4()))
        with self.assertRaises(ValueError):
            self.campaign.collect()
        self.assertEqual(self.campaign.state["battles"], [])

    def test_unknown_ship_result_rejected_without_changing_state(self):
        battle = self.campaign.prepare()
        self.complete(battle)
        path = Path(battle["directory"]) / "result.xml"
        result = ET.parse(path).getroot()
        put(result.find("Ships/Ship"), "Key", uuid.uuid4())
        write_xml(path, result)
        with self.assertRaises(ValueError):
            self.campaign.collect()
        self.assertEqual(self.campaign.state["ship_results"], {})
        self.assertEqual(self.campaign.state["battles"], [])

    def test_component_damage_and_unselected_ships_persist(self):
        resolute = fitted_ship(
            "Resolute", 42, ("weapon", "Stock/Test Cannon"), ("reactor", "Stock/Test Reactor")
        )
        sentinel = fitted_ship("Sentinel", 43, ("reactor", "Stock/Test Reactor"))
        self.campaign.player = fleet_of(resolute, sentinel)
        battle = self.campaign.prepare()
        self.install_catalog()
        resolute_key, sentinel_key = resolute.findtext("Key"), sentinel.findtext("Key")
        sentinel_xml = ET.tostring(sentinel)
        self.write_result(battle, [(resolute_key, "Resolute", "False", "0.72")])
        self.write_report(
            battle,
            {
                "Resolute": [
                    ("weapon", 0.4, False),
                    ("reactor", 1.0, False),
                    ("hull-structure", 0.6, False),
                ],
                "Sentinel": [("reactor", 0.0, True)],
            },
        )

        self.campaign.collect()

        states = self.campaign.state["ship_state"]
        self.assertEqual(
            states[resolute_key],
            {
                "status": "damaged",
                "components": [
                    {"socket": "weapon", "component": "Stock/Test Cannon", "health": 0.4, "destroyed": False}
                ],
            },
        )
        self.assertEqual(states[sentinel_key], {"status": "ready"})
        self.assertEqual(ET.tostring(ship_by_key(self.campaign.player, sentinel_key)), sentinel_xml)
        self.assertIsNotNone(
            ship_by_key(self.campaign.player, resolute_key).find("SocketMap/HullSocket[Key='weapon']")
        )
        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual(restarted.state["ship_state"][resolute_key], states[resolute_key])

    def test_retired_survivors_and_eliminated_ships_are_separated(self):
        survivor = fitted_ship(
            "Survivor", 1, ("weapon", "Stock/Test Cannon"), ("reactor", "Stock/Test Reactor")
        )
        reactor_lost = fitted_ship("Reactor Lost", 2, ("reactor", "Stock/Test Reactor"))
        destroyed = fitted_ship("Destroyed", 3, ("reactor", "Stock/Test Reactor"))
        self.campaign.player = fleet_of(survivor, reactor_lost, destroyed)
        battle = self.campaign.prepare()
        self.install_catalog()
        survivor_key = survivor.findtext("Key")
        reactor_key = reactor_lost.findtext("Key")
        destroyed_key = destroyed.findtext("Key")
        enemy_xml = ET.tostring(self.campaign.enemy.root)
        self.write_result(
            battle,
            [
                (survivor_key, "Survivor", "Retired", "1"),
                (reactor_key, "Reactor Lost", "Retired", "1"),
                (destroyed_key, "Destroyed", "True", "1"),
            ],
        )
        self.write_report(
            battle,
            {
                "Survivor": [("weapon", 0.5, False), ("reactor", 1.0, False)],
                "Reactor Lost": [("reactor", 0.0, True)],
                "Destroyed": [("reactor", 1.0, False)],
            },
        )

        self.campaign.collect()

        keys = {ship.findtext("Key") for ship in self.campaign.player.ships}
        self.assertEqual(keys, {survivor_key})
        self.assertEqual(set(self.campaign.state["owned_hulls"]), {survivor_key})
        self.assertEqual(set(self.campaign.state["ship_state"]), {survivor_key})
        self.assertEqual(
            self.campaign.state["ship_state"][survivor_key],
            {
                "status": "damaged",
                "components": [
                    {"socket": "weapon", "component": "Stock/Test Cannon", "health": 0.5, "destroyed": False}
                ],
            },
        )
        self.assertEqual(ET.tostring(self.campaign.enemy.root), enemy_xml)
        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertEqual({ship.findtext("Key") for ship in restarted.player.ships}, {survivor_key})

    def test_zero_hp_component_is_flagged_and_removed_only_when_scrapped(self):
        ship = fitted_ship(
            "Resolute", 42, ("weapon", "Stock/Test Cannon"), ("reactor", "Stock/Test Reactor")
        )
        self.campaign.player = fleet_of(ship)
        battle = self.campaign.prepare()
        self.install_catalog()
        key = ship.findtext("Key")
        self.write_result(battle, [(key, "Resolute", "False", "1")])
        self.write_report(
            battle,
            {
                "Resolute": [
                    ("weapon", 0.0, False),
                    ("reactor", 1.0, False),
                    ("hull-structure", 0.0, True),
                ]
            },
        )

        self.campaign.collect()

        current = ship_by_key(self.campaign.player, key)
        self.assertIsNotNone(current.find("SocketMap/HullSocket[Key='weapon']"))
        self.assertIsNotNone(current.find("SocketMap/HullSocket[Key='reactor']"))
        self.assertEqual(self.campaign.state["inventory"], {})
        self.assertEqual(
            self.campaign.state["ship_state"][key],
            {
                "status": "damaged",
                "components": [
                    {"socket": "weapon", "component": "Stock/Test Cannon", "health": 0.0, "destroyed": True}
                ],
            },
        )
        self.assertFalse(self.campaign.ship_ready(key))
        self.assertFalse(self.campaign.can_deploy())
        owned = ET.fromstring(self.campaign.state["owned_hulls"][key]["ship"])
        self.assertIsNotNone(owned.find("SocketMap/HullSocket[Key='weapon']"))

        scrapped = self.campaign.scrap_installed_component(key, "weapon")
        self.assertEqual(scrapped, "Stock/Test Cannon")
        current = ship_by_key(self.campaign.player, key)
        self.assertIsNone(current.find("SocketMap/HullSocket[Key='weapon']"))
        self.assertIsNotNone(current.find("SocketMap/HullSocket[Key='reactor']"))
        self.assertEqual(self.campaign.state["ship_state"][key], {"status": "ready"})
        self.assertEqual(self.campaign.state["inventory"], {})
        self.assertTrue(self.campaign.can_deploy())
        owned = ET.fromstring(self.campaign.state["owned_hulls"][key]["ship"])
        self.assertIsNone(owned.find("SocketMap/HullSocket[Key='weapon']"))
        restarted = Campaign(self.campaign.game, self.campaign.data)
        self.assertNotIn("Stock/Test Cannon", restarted.state["inventory"])
        self.assertEqual(restarted.state["ship_state"][key], {"status": "ready"})
        self.assertIsNone(ship_by_key(restarted.player, key).find("SocketMap/HullSocket[Key='weapon']"))

    def test_undamaged_ship_is_ready(self):
        ship = fitted_ship(
            "Resolute", 42, ("weapon", "Stock/Test Cannon"), ("reactor", "Stock/Test Reactor")
        )
        self.campaign.player = fleet_of(ship)
        battle = self.campaign.prepare()
        self.install_catalog()
        key = ship.findtext("Key")
        self.write_result(battle, [(key, "Resolute", "Retired", "1")])
        self.write_report(
            battle,
            {"Resolute": [("weapon", 1.0, False), ("reactor", 1.0, False)]},
        )
        self.campaign.collect()
        self.assertEqual(
            self.campaign.state["ship_state"][key],
            {"status": "ready"},
        )

    def test_missing_report_cannot_import_component_damage(self):
        battle = self.campaign.prepare()
        key = self.campaign.player.ships[0].findtext("Key")
        self.write_result(battle, [(key, "Resolute", "Retired", "0.4")])
        with self.assertRaisesRegex(ValueError, "battle report is missing"):
            self.campaign.collect()
        self.assertEqual(self.campaign.state["battles"], [])
        self.assertEqual(self.campaign.state["ship_results"], {})
        self.assertIsNotNone(self.campaign.state["active_battle"])

    def test_invalid_condition_rejected_without_import(self):
        battle = self.campaign.prepare()
        key = self.campaign.player.ships[0].findtext("Key")
        self.write_result(battle, [(key, "Resolute", "False", "not-a-number")])
        with self.assertRaisesRegex(ValueError, "invalid ship condition"):
            self.campaign.collect()
        self.assertEqual(self.campaign.state["battles"], [])
        self.assertEqual(self.campaign.state["ship_results"], {})
        self.assertIsNotNone(self.campaign.state["active_battle"])

    def test_import_rollback_restores_roster_and_state(self):
        self.campaign.player = fleet_of(
            fitted_ship("Resolute", 42, ("reactor", "Stock/Test Reactor"))
        )
        battle = self.campaign.prepare()
        key = self.campaign.player.ships[0].findtext("Key")
        self.write_result(battle, [(key, "Resolute", "True", "1")])
        before_state = copy.deepcopy(self.campaign.state)
        before_player = ET.tostring(self.campaign.player.root)
        with patch.object(Campaign, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.campaign.collect()
        self.assertEqual(self.campaign.state, before_state)
        self.assertEqual(ET.tostring(self.campaign.player.root), before_player)
        self.assertEqual(self.campaign.state["active_battle"], battle)

    def test_cancel_is_explicit_and_does_not_import(self):
        battle = self.campaign.prepare()
        self.campaign.cancel()
        self.complete(battle)
        self.assertIsNone(self.campaign.collect())
        self.assertEqual(self.campaign.state["battles"][0]["status"], "cancelled")


class AtomicWriteTests(unittest.TestCase):
    def test_failed_replace_keeps_original_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_bytes(b"old")
            with patch("nbc.core.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_bytes(path, b"new")
            self.assertEqual(path.read_bytes(), b"old")
            self.assertFalse(list(path.parent.glob(path.name + ".*.tmp")))


@unittest.skipUnless((DEFAULT_GAME / "Nebulous.exe").exists(), "Local game is not installed")
class InstalledFleetTests(unittest.TestCase):
    def test_all_local_fleets_roundtrip_and_both_enemy_factions(self):
        templates = discover(DEFAULT_GAME)
        self.assertGreater(len(templates), 10)
        with tempfile.TemporaryDirectory() as d:
            for i, fleet in enumerate(templates):
                path = Path(d) / f"{i}.fleet"
                fleet.save(path)
                reloaded = Fleet.load(path)
                self.assertEqual(len(fleet.ships), len(reloaded.ships))
                self.assertEqual(fleet.faction, reloaded.faction)
                self.assertEqual(fleet.points, reloaded.points)
        for faction in ("Stock/Alliance", "Stock/Protectorate"):
            enemy = select_generated_fleet(next(f for f in templates if f.faction == faction), 700, 42)
            enemy.validate()
            self.assertEqual(enemy.faction, faction)


if __name__ == "__main__":
    unittest.main()
