import copy
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

from nbc.core import Campaign, Fleet, put, read_xml, write_xml
from nbc.missions import (
    FALLBACK_PLAYER_POINTS,
    MAX_BUDGET,
    MAX_OFFER_COUNT,
    MIN_BUDGET,
    MIN_OFFER_COUNT,
    MIN_REWARD,
    MISSION_MAPS,
    MISSION_TYPES,
    MissionService,
    budget_for,
    reward_for,
)
from test_core import fleet_of


FIXTURE = Path(__file__).parent / "fixtures" / "fitting-catalog.xml"
FACTION = "Stock/Alliance"


def ship(name, cost):
    return ET.fromstring(
        f"<Ship><Key>{uuid.uuid4()}</Key><Name>{name}</Name><Cost>{cost}</Cost>"
        f"<Number>1</Number><HullType>Stock/Test Frigate</HullType><SocketMap /></Ship>"
    )


def fitted_ship(name, cost, *parts):
    root = ship(name, cost)
    socket_map = root.find("SocketMap")
    for key, component in parts:
        socket = ET.SubElement(socket_map, "HullSocket")
        ET.SubElement(socket, "Key").text = key
        ET.SubElement(socket, "ComponentName").text = component
    return root


def player_fleet():
    return fleet_of(
        fitted_ship("Alpha", 1000, ("socket", "Stock/Test Cannon")),
        fitted_ship("Bravo", 1000, ("socket", "Stock/Test Cannon")),
        fitted_ship("Charlie", 1000, ("socket", "Stock/Test Cannon")),
    )


def write_result(battle, ships, outcome="Victory"):
    root = ET.Element("BattleResult")
    put(root, "Id", battle["id"])
    put(root, "Outcome", outcome)
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


def write_report(battle, ships):
    root = ET.Element("FullAfterActionReport")
    teams = ET.SubElement(root, "Teams")
    team = ET.SubElement(teams, "TeamReportOfShipBattleReportCraftBattleReport")
    players = ET.SubElement(team, "Players")
    player = ET.SubElement(players, "AARPlayerReportOfShipBattleReportCraftBattleReport")
    put(player, "IsLocalPlayer", "true")
    container = ET.SubElement(player, "Ships")
    for name, parts in ships.items():
        node = ET.SubElement(container, "ShipBattleReport")
        put(node, "ShipName", name)
        part_status = ET.SubElement(node, "PartStatus")
        for key, health, destroyed in parts:
            part = ET.SubElement(part_status, "PartDamage")
            put(part, "Key", key)
            put(part, "HealthPercent", health)
            put(part, "IsDestroyed", "true" if destroyed else "false")
    write_xml(Path(battle["directory"]) / "report.xml", root)


class FormulaTests(unittest.TestCase):
    def test_budget_scales_with_player_points_and_clamps(self):
        self.assertEqual(budget_for(3000, 0.7), 2100)
        self.assertEqual(budget_for(3000, 1.0), 3000)
        self.assertEqual(budget_for(3000, 1.3), 3900)
        self.assertEqual(budget_for(0, 1.0), FALLBACK_PLAYER_POINTS)
        self.assertEqual(budget_for(100_000, 1.3), MAX_BUDGET)
        self.assertEqual(budget_for(10, 0.7), MIN_BUDGET)

    def test_reward_uses_actual_enemy_points(self):
        self.assertEqual(reward_for(0), MIN_REWARD)
        self.assertEqual(reward_for(500), 50_000)
        self.assertEqual(reward_for(1234), 123_000)


class MissionGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.root / "data"
        exchange = self.game / "Saves" / "NBCBridge"
        exchange.mkdir(parents=True)
        shutil.copyfile(FIXTURE, exchange / "fitting-catalog.xml")
        self.campaign = Campaign(self.game, self.data)
        self.campaign.start_new_campaign(
            "Mission Test", "Tester", FACTION, 1_000_000, player_fleet()
        )
        self.service = MissionService(self.campaign)

    def tearDown(self):
        self.temp.cleanup()

    def test_refresh_generates_a_difficulty_spread_and_persists(self):
        offers = self.service.refresh(count=3, seed=7)
        self.assertEqual(len(offers), 3)
        self.assertEqual(
            {offer["difficulty"] for offer in offers},
            {"easy", "standard", "hard"},
        )
        self.assertEqual(sorted(offer["budget"] for offer in offers), [2100, 3000, 3900])
        for offer in offers:
            self.assertEqual(offer["status"], "offered")
            self.assertEqual(offer["type"], "annihilation")
            self.assertIn(offer["map"], MISSION_MAPS)
            self.assertGreaterEqual(offer["ships"], 1)
            self.assertLessEqual(offer["ships"], 10)
            self.assertEqual(offer["reward"], reward_for(offer["points"]))
            self.assertEqual(len(offer["preview"]), offer["ships"])
            self.assertTrue(offer["expires"])
            enemy = Fleet(ET.fromstring(offer["enemy_xml"]))
            self.assertEqual(enemy.points, offer["points"])
            self.assertEqual(enemy.faction, offer["faction"])
        self.assertEqual(self.campaign.state["mission_offers"], offers)
        self.assertEqual(self.campaign.state["mission_meta"]["player_points"], 3000)

    def test_generation_is_deterministic_for_a_seed(self):
        first = self.service.generate(seed=21)
        second = self.service.generate(seed=21)
        fields = (
            "name",
            "type",
            "map",
            "faction",
            "difficulty",
            "budget",
            "points",
            "ships",
            "reward",
            "seed",
        )
        self.assertEqual(
            [{key: offer[key] for key in fields} for offer in first],
            [{key: offer[key] for key in fields} for offer in second],
        )

    def test_offers_use_annihilation_and_the_allowed_maps(self):
        maps = set()
        for seed in range(10):
            for offer in self.service.generate(count=6, seed=seed):
                self.assertEqual(offer["type"], "annihilation")
                self.assertIn(offer["map"], MISSION_MAPS)
                maps.add(offer["map"])
        self.assertEqual(maps, set(MISSION_MAPS))
        self.assertEqual(MISSION_TYPES, ("annihilation",))

    def test_enemy_factions_are_rolled_independently_of_the_player(self):
        path = self.campaign.exchange / "fitting-catalog.xml"
        root = ET.parse(path).getroot()
        clone = copy.deepcopy(root.find("Faction"))
        clone.find("Key").text = "Stock/Protectorate"
        root.append(clone)
        write_xml(path, root)

        factions = set()
        for seed in range(10):
            for offer in self.service.generate(count=6, seed=seed):
                factions.add(offer["faction"])
        self.assertEqual(factions, {"Stock/Alliance", "Stock/Protectorate"})

    def test_type_and_map_are_migrated_for_older_offers(self):
        seed_offer = self.service.generate(count=1, seed=1)[0]
        legacy = dict(seed_offer)
        legacy.pop("type", None)
        legacy.pop("map", None)
        self.campaign.state["mission_offers"] = [dict(legacy, id="offer-1")]
        self.campaign.state["accepted_mission"] = dict(
            legacy, id="accepted-1", status="accepted"
        )
        self.campaign.persist()

        self.assertTrue(self.service.migrate())
        for item in (
            self.campaign.state["mission_offers"][0],
            self.campaign.state["accepted_mission"],
        ):
            self.assertEqual(item["type"], "annihilation")
            self.assertIn(item["map"], MISSION_MAPS)
        self.assertFalse(self.service.migrate())

    def test_default_batch_size_is_between_five_and_ten(self):
        counts = set()
        for seed in range(20):
            offers = self.service.generate(seed=seed)
            self.assertGreaterEqual(len(offers), MIN_OFFER_COUNT)
            self.assertLessEqual(len(offers), MAX_OFFER_COUNT)
            counts.add(len(offers))
        self.assertGreater(len(counts), 1)

    def test_offers_survive_restart(self):
        first = self.service.refresh(seed=11)
        restarted = Campaign(self.game, self.data)
        second = MissionService(restarted).offers
        self.assertEqual([offer["id"] for offer in first], [offer["id"] for offer in second])
        self.assertEqual(
            [offer["enemy_xml"] for offer in first],
            [offer["enemy_xml"] for offer in second],
        )

    def test_expired_and_duplicate_offers_are_pruned(self):
        offers = self.service.generate(seed=3)
        good, expired, duplicate = offers[0], dict(offers[1]), offers[2]
        expired["expires"] = "2000-01-01T00:00:00+00:00"
        self.campaign.state["mission_offers"] = [good, expired, duplicate, duplicate]
        self.campaign.persist()

        refreshed = self.service.refresh()
        self.assertEqual([offer["id"] for offer in refreshed], [good["id"], duplicate["id"]])
        self.assertEqual(self.campaign.state["mission_offers"], refreshed)

    def test_changed_catalogue_invalidates_offers(self):
        first = self.service.generate(seed=10)
        catalog = self.campaign.exchange / "fitting-catalog.xml"
        catalog.write_bytes(catalog.read_bytes() + b"\n")
        second = self.service.refresh()
        self.assertNotEqual([offer["id"] for offer in first], [offer["id"] for offer in second])

    def test_empty_board_is_regenerated(self):
        self.campaign.state["mission_offers"] = []
        self.campaign.persist()
        offers = self.service.refresh(count=2, seed=13)
        self.assertEqual(len(offers), 2)

    def test_missing_catalogue_is_actionable(self):
        (self.campaign.exchange / "fitting-catalog.xml").unlink()
        with self.assertRaisesRegex(ValueError, "catalogue is missing"):
            self.service.refresh()

    def test_accept_freezes_enemy_and_restart_resumes(self):
        offers = self.service.generate(seed=5)
        target = offers[0]
        accepted = self.service.accept(target["id"])

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["type"], "annihilation")
        self.assertIn(accepted["map"], MISSION_MAPS)
        self.assertIsNone(self.campaign.pending_action())
        self.campaign.ensure_idle()  # accepting a mission must not lock the campaign
        self.assertNotIn(
            target["id"], [offer["id"] for offer in self.campaign.state["mission_offers"]]
        )
        self.assertIsNotNone(self.campaign.enemy)
        self.assertEqual(self.campaign.enemy.points, target["points"])
        self.assertEqual(self.campaign.state["enemy_xml"], accepted["enemy_xml"])

        restarted = Campaign(self.game, self.data)
        resumed = MissionService(restarted)
        self.assertEqual(resumed.accepted["id"], target["id"])
        self.assertEqual(resumed.accepted["type"], "annihilation")
        self.assertIn(resumed.accepted["map"], MISSION_MAPS)
        self.assertIsNotNone(restarted.enemy)
        self.assertEqual(restarted.enemy.points, target["points"])

    def test_cancel_returns_mission_without_reward(self):
        offers = self.service.generate(seed=6)
        target = offers[0]
        before = self.campaign.credits
        self.service.accept(target["id"])
        returned = self.service.cancel_accepted()

        self.assertEqual(returned["status"], "offered")
        self.assertIsNone(self.campaign.state["accepted_mission"])
        self.assertIsNone(self.campaign.enemy)
        self.assertEqual(self.campaign.credits, before)
        self.assertIn(
            target["id"], [offer["id"] for offer in self.campaign.state["mission_offers"]]
        )

        restarted = Campaign(self.game, self.data)
        self.assertIsNone(restarted.accepted_mission)
        self.assertIsNone(restarted.enemy)

    def test_reroll_replaces_the_board(self):
        first = self.service.generate(seed=1)
        second = self.service.reroll(seed=2)
        self.assertGreaterEqual(len(second), MIN_OFFER_COUNT)
        self.assertLessEqual(len(second), MAX_OFFER_COUNT)
        self.assertNotEqual([offer["id"] for offer in first], [offer["id"] for offer in second])
        self.assertEqual(self.campaign.state["mission_offers"], second)
        self.assertIn("rerolled", self.campaign.state["mission_meta"])

    def test_reroll_blocks_while_accepted(self):
        offers = self.service.generate(seed=8)
        self.service.accept(offers[0]["id"])
        with self.assertRaisesRegex(ValueError, "Cancel the accepted mission"):
            self.service.reroll()
        with self.assertRaisesRegex(ValueError, "Cancel the accepted mission"):
            self.service.generate()

    def test_accept_requires_a_fleet(self):
        offers = self.service.generate(seed=14)
        self.campaign.player = None
        with self.assertRaisesRegex(ValueError, "Build a fleet"):
            self.service.accept(offers[0]["id"])

    def test_offer_names_are_unique(self):
        offers = self.service.generate(count=12, seed=12)
        names = [offer["name"] for offer in offers]
        self.assertEqual(len(names), len(set(names)))

    def test_background_generation_reports_progress_and_commits(self):
        job = self.service.start_generation(seed=4)
        self.assertEqual(job.join(30), "done")
        self.assertEqual(job.progress, 1.0)
        self.assertFalse(job.failed())

        stored = self.service.store_generation(job)
        self.assertGreaterEqual(len(stored), MIN_OFFER_COUNT)
        self.assertLessEqual(len(stored), MAX_OFFER_COUNT)
        self.assertEqual(self.campaign.state["mission_offers"], stored)

    def test_background_generation_failure_can_be_retried(self):
        catalog = self.campaign.exchange / "fitting-catalog.xml"
        catalog.unlink()
        job = self.service.start_generation()
        self.assertEqual(job.join(30), "error")
        self.assertIn("catalogue is missing", job.error)
        with self.assertRaisesRegex(ValueError, "catalogue is missing"):
            job.result()

        shutil.copyfile(FIXTURE, catalog)
        retry = self.service.start_generation(seed=9)
        self.assertEqual(retry.join(30), "done")
        self.assertGreaterEqual(len(retry.result()), MIN_OFFER_COUNT)
        self.assertLessEqual(len(retry.result()), MAX_OFFER_COUNT)
        self.assertEqual(self.service.store_generation(retry), retry.result())

    def test_start_deployment_requires_an_accepted_mission(self):
        with self.assertRaisesRegex(ValueError, "Accept a mission"):
            self.service.start_deployment()

    def test_confirm_deployment_builds_the_selected_subset(self):
        offers = self.service.generate(seed=20)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        accepted = self.service.confirm_deployment(keys[:2], launch=False)
        self.assertEqual(accepted["status"], "deploying")
        self.assertEqual(accepted["deployment"]["ships"], keys[:2])
        self.assertIsNotNone(self.campaign.deployment)
        self.assertEqual(
            [s.findtext("Key") for s in self.campaign.deployment.ships], keys[:2]
        )
        self.assertTrue(self.campaign.state["deployment_xml"])

    def test_confirm_deployment_validates_the_selection(self):
        offers = self.service.generate(seed=21)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        with self.assertRaisesRegex(ValueError, "at least one ready ship"):
            self.service.confirm_deployment([], launch=False)
        with self.assertRaisesRegex(ValueError, "no longer in the roster"):
            self.service.confirm_deployment(["missing-key"], launch=False)
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            self.service.confirm_deployment([f"key-{i}" for i in range(11)], launch=False)
        self.campaign.state["ship_state"][keys[0]] = {
            "status": "damaged",
            "components": [
                {
                    "socket": "socket",
                    "component": "Stock/Test Cannon",
                    "health": 0.5,
                    "destroyed": False,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "Repair damaged ships"):
            self.service.confirm_deployment([keys[0]], launch=False)

    def test_prepare_exports_only_the_selected_ships(self):
        offers = self.service.generate(seed=22)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.confirm_deployment(keys[:2], launch=False)
        battle = self.campaign.prepare(publish=False)
        archived = Fleet.load(Path(battle["directory"]) / "player.fleet")
        self.assertEqual([s.findtext("Key") for s in archived.ships], keys[:2])
        exported = Fleet.load(
            self.campaign.game / "Saves" / "Fleets" / "NBC-POC" / "NBC Player.fleet"
        )
        self.assertEqual([s.findtext("Key") for s in exported.ships], keys[:2])

    def test_confirm_deployment_launch_marks_the_mission_active(self):
        offers = self.service.generate(seed=23)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.campaign.launch = lambda: self.campaign.prepare(publish=False)
        accepted = self.service.confirm_deployment(keys[:2])
        self.assertEqual(accepted["status"], "active")
        self.assertIsNotNone(self.campaign.state["active_battle"])

    def test_collect_scopes_the_report_to_selected_ships(self):
        offers = self.service.generate(seed=24)
        self.service.accept(offers[0]["id"])
        ships = self.campaign.player.ships
        keys = [s.findtext("Key") for s in ships]
        names = {s.findtext("Key"): s.findtext("Name") for s in ships}
        selected, unselected = keys[0], keys[2]
        self.service.confirm_deployment([keys[0], keys[1]], launch=False)
        battle = self.campaign.prepare(publish=False)
        write_result(
            battle,
            [
                (keys[0], names[keys[0]], "NotEliminated", "0.5"),
                (keys[1], names[keys[1]], "destroyed", "0"),
            ],
        )
        write_report(battle, {names[keys[0]]: [("socket", "0.5", False)]})

        record = self.campaign.collect()
        self.assertIsNotNone(record)
        self.assertEqual(len(record["lost_ships"]), 1)
        self.assertNotEqual(
            self.campaign.state["ship_state"][selected], {"status": "ready"}
        )
        remaining = [s.findtext("Key") for s in self.campaign.player.ships]
        self.assertNotIn(keys[1], remaining)
        self.assertIn(unselected, remaining)
        self.assertEqual(
            self.campaign.state["ship_state"][unselected], {"status": "ready"}
        )

    def test_deployment_survives_restart_and_is_cleared_on_abandon(self):
        offers = self.service.generate(seed=25)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.start_deployment()
        self.assertEqual(self.service.accepted["status"], "deploying")
        self.service.confirm_deployment(keys[:2], launch=False)

        restarted = Campaign(self.game, self.data)
        self.assertEqual(restarted.accepted_mission["status"], "deploying")
        self.assertIsNotNone(restarted.deployment)
        self.assertEqual(
            [s.findtext("Key") for s in restarted.deployment.ships], keys[:2]
        )

        restarted_service = MissionService(restarted)
        restarted_service.abandon_deployment()
        self.assertEqual(restarted.accepted_mission["status"], "accepted")
        self.assertEqual(restarted.state["deployment_xml"], "")
        self.assertIsNone(restarted.deployment)

    def test_cancel_accepted_clears_the_deployment(self):
        offers = self.service.generate(seed=26)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.confirm_deployment(keys[:1], launch=False)
        self.service.cancel_accepted()
        self.assertIsNone(self.service.accepted)
        self.assertEqual(self.campaign.state["deployment_xml"], "")
        self.assertIsNone(self.campaign.deployment)

    def test_prepare_passes_the_mission_map_and_type(self):
        offers = self.service.generate(seed=28)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.confirm_deployment(keys[:1], launch=False)
        battle = self.campaign.prepare(publish=False)
        request = read_xml(Path(battle["directory"]) / "request.xml")
        accepted = self.campaign.accepted_mission
        self.assertEqual(request.findtext("Map"), accepted["map"])
        self.assertEqual(request.findtext("MissionType"), accepted["type"])

    def test_collect_resolves_the_mission_and_pays_once(self):
        offers = self.service.generate(seed=27)
        self.service.accept(offers[0]["id"])
        accepted = self.campaign.accepted_mission
        reward = accepted["reward"]
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.confirm_deployment(keys[:1], launch=False)
        battle = self.campaign.prepare(publish=False)
        before = self.campaign.credits
        write_result(
            battle,
            [(keys[0], self.campaign.player.ships[0].findtext("Name"), "NotEliminated", "1")],
        )

        record = self.campaign.collect()
        self.assertIsNone(self.campaign.accepted_mission)
        self.assertIsNone(self.campaign.enemy)
        self.assertIsNone(self.campaign.deployment)
        self.assertEqual(self.campaign.state["deployment_xml"], "")
        self.assertEqual(self.campaign.state["enemy_xml"], "")
        self.assertEqual(record["mission"]["status"], "completed")
        self.assertEqual(record["mission"]["payout"], reward)
        self.assertEqual(record["mission"]["map"], accepted["map"])
        self.assertEqual(self.campaign.credits, before + reward)
        self.assertEqual(self.campaign.credit_ledger[-1]["kind"], "mission-reward")
        self.assertEqual(self.campaign.credit_ledger[-1]["amount"], reward)

        restarted = Campaign(self.game, self.data)
        self.assertIsNone(restarted.accepted_mission)
        self.assertIsNone(restarted.enemy)
        self.assertEqual(restarted.state["battles"][-1]["mission"]["status"], "completed")

    def test_collect_marks_a_defeat_failed_without_payout(self):
        offers = self.service.generate(seed=29)
        self.service.accept(offers[0]["id"])
        keys = [s.findtext("Key") for s in self.campaign.player.ships]
        self.service.confirm_deployment(keys[:1], launch=False)
        battle = self.campaign.prepare(publish=False)
        before = self.campaign.credits
        write_result(
            battle,
            [(keys[0], self.campaign.player.ships[0].findtext("Name"), "destroyed", "1")],
            outcome="Defeat",
        )

        record = self.campaign.collect()
        self.assertEqual(record["mission"]["status"], "failed")
        self.assertEqual(record["mission"]["payout"], 0)
        self.assertEqual(self.campaign.credits, before)
        self.assertIsNone(self.campaign.accepted_mission)


if __name__ == "__main__":
    unittest.main()
