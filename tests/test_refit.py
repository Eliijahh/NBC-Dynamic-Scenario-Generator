import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import patch

from nbc.core import Campaign, Fleet, put, write_xml
from nbc.refit import RefitService, installed, reconcile
from test_core import sample


class RefitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.c = Campaign(root / "game", root / "data")
        self.c.player = sample()
        self.c.player.duplicate(0)
        self.c.player.duplicate(0)
        self.service = RefitService(self.c)
        catalog = ET.fromstring("<ComponentCatalog><Component><Key>Stock/Radar</Key><Name>Radar</Name><Type>Module</Type></Component></ComponentCatalog>")
        write_xml(self.c.exchange / "catalog.xml", catalog)

    def tearDown(self):
        self.temp.cleanup()

    def ready(self, draft=None, revision=None, identity=None):
        active = self.service.active
        revision = revision or active["revision"]
        folder = Path(active["directory"])
        draft = draft or self.c.player.clone()
        path = folder / f"draft-{revision}.fleet"
        draft.save(path)
        marker = ET.Element("RefitReady")
        put(marker, "Id", identity or active["id"])
        put(marker, "Revision", revision)
        put(marker, "Hash", hashlib.sha256(path.read_bytes()).hexdigest())
        write_xml(folder / f"ready-{revision}.xml", marker)

    def test_buy_sell_and_restart_without_currency(self):
        self.service.trade("Stock/Radar", 2)
        self.service.trade("Stock/Radar", 1, buy=False)
        restarted = Campaign(self.c.game, self.c.data)
        self.assertEqual(restarted.state["inventory"], {"Stock/Radar": 1})
        self.assertNotIn("credits", restarted.state)
        with self.assertRaises(ValueError):
            self.service.trade("Stock/Radar", 2, buy=False)
        with self.assertRaises(ValueError):
            self.service.trade("Stock/Unknown", 1)
        with self.assertRaises(ValueError):
            self.service.trade("Stock/Radar", -1)

    def test_trade_rolls_back_if_save_fails(self):
        before = copy.deepcopy(self.c.state)
        with patch.object(self.c, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.trade("Stock/Radar", 1)
        self.assertEqual(self.c.state, before)

    def test_pending_mission_locks_economy_and_refit(self):
        self.c.state["accepted_mission"] = {"id": "mission-1"}
        with self.assertRaisesRegex(ValueError, "pending mission"):
            self.service.trade("Stock/Radar", 1)
        with self.assertRaisesRegex(ValueError, "pending mission"):
            self.service.start(launch=False)

    def test_shared_transfer_and_inventory_conservation(self):
        baseline = self.c.player.clone()
        draft = baseline.clone()
        a = draft.ships[0].find("SocketMap/HullSocket")
        b = draft.ships[1].find("SocketMap/HullSocket")
        # Remove B's gun, then move A's gun to another socket on B.
        put(a, "ComponentName", "")
        new = copy.deepcopy(b)
        put(new, "Key", "other-socket")
        draft.ships[1].find("SocketMap").append(new)
        self.assertEqual(reconcile(baseline, draft, {}), {})
        put(a, "ComponentName", "Stock/Mk62 Cannon")
        with self.assertRaises(ValueError):
            reconcile(baseline, draft, {})

    def test_purchase_consumed_only_on_confirm_and_survives_restart(self):
        self.service.trade("Stock/Radar", 1)
        self.service.start(launch=False)
        draft = self.c.player.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        self.assertTrue(self.service.collect())
        self.assertEqual(self.c.state["inventory"], {"Stock/Radar": 1})
        self.assertEqual(installed(self.c.player)["Stock/Radar"], 0)
        self.service.confirm()
        restarted = Campaign(self.c.game, self.c.data)
        self.assertEqual(restarted.state["inventory"], {"Stock/Mk62 Cannon": 1})
        self.assertEqual(installed(restarted.player)["Stock/Radar"], 1)
        self.assertIsNone(restarted.state["active_refit"])
        with self.assertRaises(ValueError):
            self.service.confirm()

    def test_selected_hull_refit_merges_only_that_hull(self):
        self.service.trade("Stock/Radar", 1)
        key = self.c.player.ships[1].findtext("Key")
        untouched = ET.tostring(self.c.player.ships[0])
        self.service.start(launch=False, ship_keys=[key])
        baseline = Fleet.load(Path(self.service.active["directory"]) / "baseline.fleet")
        self.assertEqual(len(baseline.ships), 1)
        draft = baseline.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        self.assertTrue(self.service.collect())
        self.service.confirm()

        self.assertEqual(len(self.c.player.ships), 3)
        self.assertEqual(self.c.player.ships[1].findtext("SocketMap/HullSocket/ComponentName"), "Stock/Radar")
        self.assertEqual(ET.tostring(self.c.player.ships[0]), untouched)
        self.assertIn("Stock/Radar", self.c.state["owned_hulls"][key]["ship"])

        restarted = Campaign(self.c.game, self.c.data)
        self.assertEqual(len(restarted.player.ships), 3)
        self.assertEqual(installed(restarted.player)["Stock/Radar"], 1)

    def test_trade_battle_and_roster_edit_locked_during_refit(self):
        self.service.start(launch=False)
        with self.assertRaises(ValueError):
            self.service.trade("Stock/Radar", 1)
        with self.assertRaises(ValueError):
            self.c.prepare()
        with self.assertRaises(ValueError):
            self.service.start(launch=False)
        restarted = RefitService(Campaign(self.c.game, self.c.data))
        self.assertEqual(restarted.active["id"], self.service.active["id"])

    def test_cancel_keeps_purchases_and_original_fleet(self):
        self.service.trade("Stock/Radar", 2)
        self.c.player.validate()
        original = ET.tostring(self.c.player.root)
        self.service.start(launch=False)
        draft = self.c.player.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        self.service.collect()
        self.service.cancel()
        self.assertEqual(ET.tostring(self.c.player.root), original)
        self.assertEqual(self.c.state["inventory"], {"Stock/Radar": 2})
        self.assertFalse(self.service.collect())

    def test_unowned_components_wrong_roster_and_wrong_result_rejected(self):
        self.service.start(launch=False)
        draft = self.c.player.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        with self.assertRaises(ValueError):
            self.service.collect()
        self.ready(identity=str(uuid.uuid4()))
        with self.assertRaises(ValueError):
            self.service.collect()
        draft = self.c.player.clone()
        put(draft.ships[0], "Key", uuid.uuid4())
        self.ready(draft)
        with self.assertRaises(ValueError):
            self.service.collect()
        draft = self.c.player.clone()
        put(draft.ships[0], "HullType", "Stock/Other Hull")
        self.ready(draft)
        with self.assertRaises(ValueError):
            self.service.collect()

    def test_continue_keeps_original_supply_and_rejects_stale_revision(self):
        self.service.trade("Stock/Radar", 1)
        self.service.start(launch=False)
        self.ready()
        self.service.collect()
        self.service.resume(launch=False)
        self.assertEqual(self.service.active["revision"], 2)
        self.assertFalse(self.service.collect())
        draft = self.c.player.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        self.service.collect()
        self.service.confirm()
        self.assertEqual(self.c.state["inventory"], {"Stock/Mk62 Cannon": 1})

    def test_tampered_draft_rejected_on_confirmation(self):
        self.service.start(launch=False)
        self.ready()
        self.service.collect()
        path = Path(self.service.active["directory"]) / "draft-1.fleet"
        with path.open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaises(ValueError):
            self.service.confirm()
        self.assertIsNotNone(self.service.active)

    def test_failed_commit_preserves_fleet_and_inventory(self):
        self.service.start(launch=False)
        self.ready()
        self.service.collect()
        state = copy.deepcopy(self.c.state)
        with patch.object(self.c, "persist", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.service.confirm()
        self.assertEqual(self.c.state, state)

    def test_authoritative_commit_recovers_if_export_is_interrupted(self):
        self.service.trade("Stock/Radar", 1)
        self.service.start(launch=False)
        draft = self.c.player.clone()
        put(draft.ships[0].find("SocketMap/HullSocket"), "ComponentName", "Stock/Radar")
        self.ready(draft)
        self.service.collect()
        with patch.object(Fleet, "save", side_effect=OSError("export interrupted")):
            with self.assertRaises(OSError):
                self.service.confirm()
        restarted = Campaign(self.c.game, self.c.data)
        self.assertIsNone(restarted.state["active_refit"])
        self.assertEqual(installed(restarted.player)["Stock/Radar"], 1)
        self.assertEqual(restarted.state["inventory"], {"Stock/Mk62 Cannon": 1})


if __name__ == "__main__":
    unittest.main()
