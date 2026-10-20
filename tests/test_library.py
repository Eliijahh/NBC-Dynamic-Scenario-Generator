import json
from pathlib import Path
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

from nbc.core import Campaign, Fleet, DEFAULT_STARTING_CREDITS
from nbc.library import CampaignLibrary


def sample(name="Test Fleet", faction="Stock/Alliance"):
    root = ET.fromstring(f'''<Fleet><Name>{name}</Name><Version>3</Version><FactionKey>{faction}</FactionKey><Ships>
      <Ship><Key>{uuid.uuid4()}</Key><Name>Resolute</Name><Number>42</Number><Cost>350</Cost>
      <HullType>Stock/Raines Frigate</HullType><SocketMap><HullSocket><Key>socket</Key>
      <ComponentName>Stock/Mk62 Cannon</ComponentName></HullSocket></SocketMap></Ship>
      </Ships><MissileTypes/><CraftTypes/></Fleet>''')
    return Fleet(root)


class CampaignLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.root / "data"
        self.library = CampaignLibrary(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, name, commander="Cmdr Vale", credits=DEFAULT_STARTING_CREDITS):
        campaign_id, path = self.library.new_campaign_path()
        campaign = Campaign(self.game, path)
        campaign.start_new_campaign(
            name,
            commander,
            "Stock/Alliance",
            credits,
            sample(f"{name} Starter"),
            campaign_id=campaign_id,
        )
        return campaign_id, path, campaign

    def test_new_campaigns_are_independent_and_listed_with_metadata(self):
        first_id, first_path, first = self.create("First Watch")
        second_id, second_path, second = self.create("Second Watch", commander="Cmdr Reyes", credits=250_000)

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(Campaign(self.game, first_path).metadata["id"], first_id)
        self.assertEqual(Campaign(self.game, second_path).metadata["id"], second_id)
        self.assertEqual(self.library.id_for_path(first_path), first_id)
        self.assertEqual(self.library.id_for_path(self.data), "root")

        infos = {info.id: info for info in self.library.list()}
        self.assertEqual(set(infos), {first_id, second_id})
        first_info = infos[first_id]
        self.assertTrue(first_info.valid)
        self.assertEqual(first_info.name, "First Watch")
        self.assertEqual(first_info.commander, "Cmdr Vale")
        self.assertEqual(first_info.faction, "Stock/Alliance")
        self.assertEqual(first_info.credits, DEFAULT_STARTING_CREDITS)
        self.assertEqual(first_info.ships, 1)
        self.assertTrue(first_info.last_played)
        self.assertFalse(first_info.legacy)
        self.assertEqual(infos[second_id].credits, 250_000)

        self.library.set_last(first_id)
        self.assertEqual(self.library.last_id(), first_id)
        self.assertEqual(self.library.last_info().id, first_id)

        before = first_info.last_played
        first.mark_played()
        self.assertGreaterEqual(self.library.get(first_id).last_played, before)

    def test_legacy_root_save_is_listed_and_migrated_on_load(self):
        legacy = Campaign(self.game, self.data)
        legacy.player = sample("Old Fleet")
        legacy.save_fleets()
        self.assertFalse(legacy.has_campaign)

        info = self.library.get("root")
        self.assertIsNotNone(info)
        self.assertTrue(info.valid)
        self.assertTrue(info.legacy)
        self.assertEqual(info.name, "Old Fleet")
        self.assertEqual(info.credits, 0)
        self.assertEqual(info.ships, 1)

        self.library.set_last("root")
        self.assertEqual(self.library.last_info().id, "root")
        loaded = Campaign(self.game, info.path)
        self.assertTrue(loaded.has_campaign)
        self.assertTrue(loaded.metadata["legacy"])
        self.assertEqual(loaded.player.name, "Old Fleet")

    def test_corrupt_newer_and_unsupported_saves_are_reported(self):
        broken = self.library.campaigns_dir / "broken"
        broken.mkdir()
        (broken / "campaign.json").write_text("{not valid json", encoding="utf-8")

        newer = self.library.campaigns_dir / "newer"
        newer.mkdir()
        (newer / "campaign.json").write_text(
            json.dumps({"version": 99, "campaign": {"name": "Future"}, "player_xml": ET.tostring(sample().root, encoding="unicode")}),
            encoding="utf-8",
        )

        missing_fleet = self.library.campaigns_dir / "missing-fleet"
        missing_fleet.mkdir()
        (missing_fleet / "campaign.json").write_text(
            json.dumps({"version": 1, "campaign": {"name": "No Fleet", "credits": 10}}),
            encoding="utf-8",
        )

        infos = {info.id: info for info in self.library.list()}
        self.assertIn("Unreadable", infos["broken"].error)
        self.assertIn("newer", infos["newer"].error)
        self.assertIn("no player fleet", infos["missing-fleet"].error)
        for campaign_id in ("broken", "newer", "missing-fleet"):
            self.assertFalse(infos[campaign_id].valid)
            self.assertEqual(infos[campaign_id].id, campaign_id)

    def test_unknown_last_campaign_does_not_select_a_random_save(self):
        self.create("Only Campaign")
        self.library.set_last("missing-campaign")
        self.assertEqual(self.library.last_id(), "missing-campaign")
        self.assertIsNone(self.library.last_info())


if __name__ == "__main__":
    unittest.main()
