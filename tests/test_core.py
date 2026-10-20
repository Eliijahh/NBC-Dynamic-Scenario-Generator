import copy
from pathlib import Path
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

from nbc.generation import select_generated_fleet
from nbc.core import Campaign, Fleet, DEFAULT_GAME, DEFAULT_STARTING_CREDITS, discover, put, write_xml


def sample(name="Test Fleet", faction="Stock/Alliance"):
    root = ET.fromstring(f'''<Fleet><Name>{name}</Name><Version>3</Version><FactionKey>{faction}</FactionKey><Ships>
      <Ship><Key>{uuid.uuid4()}</Key><Name>Resolute</Name><Number>42</Number><Cost>350</Cost>
      <HullType>Stock/Raines Frigate</HullType><SocketMap><HullSocket><Key>socket</Key>
      <ComponentName>Stock/Mk62 Cannon</ComponentName><ComponentData><FutureField>preserve</FutureField></ComponentData>
      </HullSocket></SocketMap><UnknownFutureData important="yes">keep</UnknownFutureData></Ship>
      </Ships><MissileTypes/><CraftTypes/></Fleet>''')
    return Fleet(root)


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
        self.campaign.state["active_battle"] = {"id": "pending"}
        with self.assertRaises(ValueError):
            self.campaign.start_new_campaign(
                "Campaign", "Cmdr", "Stock/Alliance", 1000, sample()
            )
        self.assertEqual(self.campaign.state["active_battle"], {"id": "pending"})


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
        for key, value in {"Key": self.campaign.player.ships[0].findtext("Key"), "Side": "Player", "Condition": "0.72", "Status": "NotEliminated"}.items():
            put(ship, key, value)
        write_xml(Path(battle["directory"]) / "result.xml", root)

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
        self.assertEqual(restarted.state["ship_results"][key]["Condition"], "0.72")

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

    def test_cancel_is_explicit_and_does_not_import(self):
        battle = self.campaign.prepare()
        self.campaign.cancel()
        self.complete(battle)
        self.assertIsNone(self.campaign.collect())
        self.assertEqual(self.campaign.state["battles"][0]["status"], "cancelled")


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
