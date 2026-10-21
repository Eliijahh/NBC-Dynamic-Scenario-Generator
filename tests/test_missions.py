import copy
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

from nbc.core import Campaign, Fleet, write_xml
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


def player_fleet():
    return fleet_of(ship("Alpha", 1000), ship("Bravo", 1000), ship("Charlie", 1000))


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


if __name__ == "__main__":
    unittest.main()
