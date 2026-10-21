import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from nbc.core import Campaign, write_xml
from nbc.economy import DEFAULT_PRICE_FILE, EconomyService, PriceBook, PriceError, format_credits
from nbc.fitting import Catalog
from test_core import sample

FIXTURE = Path(__file__).parent / "fixtures" / "fitting-catalog.xml"


class PriceBookTests(unittest.TestCase):
    def test_formula_fallback_override_and_sale_rates(self):
        book = PriceBook.load()
        self.assertEqual(book.component_price("Stock/Test Radar", 10), 11_000)
        self.assertEqual(book.hull_price("Stock/Test Frigate", 100), 500_000)
        self.assertEqual(book.component_price("Stock/New Component", 0), 5_000)
        self.assertEqual(book.hull_price("Stock/New Hull", 0), 100_000)
        self.assertEqual(book.sale_value(11_000), 1_100)
        self.assertEqual(book.sale_value(500_000), 50_000)
        self.assertEqual(format_credits(1_234_567), "1,234,567 credits")

    def test_overrides_are_loaded_from_a_separate_price_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prices.json"
            data = json.loads(DEFAULT_PRICE_FILE.read_text(encoding="utf-8"))
            data["overrides"]["components"]["Stock/Test Radar"] = 1_234
            data["overrides"]["hulls"]["Stock/Test Frigate"] = 2_345
            path.write_text(json.dumps(data), encoding="utf-8")
            book = PriceBook.load(path)
            self.assertEqual(book.component_price("Stock/Test Radar", 10), 1_234)
            self.assertEqual(book.hull_price("Stock/Test Frigate", 100), 2_345)

    def test_every_fixture_catalogue_item_has_a_positive_price(self):
        catalog = Catalog.load(FIXTURE)
        count = PriceBook.load().validate_catalog(catalog)
        self.assertGreater(count[0], 0)
        self.assertGreater(count[1], 0)

    def test_missing_price_file_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(PriceError, "not found"):
                PriceBook.load(Path(temp) / "missing.json")


class EconomyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.c = Campaign(root / "game", root / "data")
        self.c.start_new_campaign("Economy Tests", "Cmdr Vale", "Stock/Alliance", 1_000_000, sample())
        self.c.exchange.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE, self.c.exchange / "fitting-catalog.xml")
        catalog = ET.fromstring(
            "<ComponentCatalog><Component><Key>Stock/Test Radar</Key><Name>Test Radar</Name><Type>Sensors</Type></Component></ComponentCatalog>"
        )
        write_xml(self.c.exchange / "catalog.xml", catalog)
        self.economy = EconomyService(self.c)
        self.catalog = Catalog.load(self.c.exchange / "fitting-catalog.xml")

    def tearDown(self):
        self.temp.cleanup()

    def test_component_points_reload_after_bridge_export_changes(self):
        self.assertEqual(self.economy.component_points("Stock/Test Radar"), 10)
        path = self.c.exchange / "fitting-catalog.xml"
        tree = ET.parse(path)
        for component in tree.findall(".//Component"):
            if component.findtext("Key") == "Stock/Test Radar":
                component.find("Points").text = "25"
        tree.write(path, encoding="utf-8", xml_declaration=True)
        stamp = path.stat().st_mtime_ns
        os.utime(path, ns=(stamp + 1_000_000, stamp + 1_000_000))
        self.assertEqual(self.economy.component_points("Stock/Test Radar"), 25)

    def test_component_purchase_and_sale_change_wallet_and_ledger(self):
        total = self.economy.buy_component("Stock/Test Radar", 2)
        self.assertEqual(total, 22_000)
        self.assertEqual(self.c.credits, 978_000)
        self.assertEqual(self.c.state["inventory"]["Stock/Test Radar"], 2)
        self.assertEqual(self.c.credit_ledger[-1]["kind"], "component-purchase")
        self.assertEqual(self.c.credit_ledger[-1]["amount"], -22_000)
        self.assertEqual(self.c.credit_ledger[-1]["balance"], 978_000)

        refund = self.economy.sell_component("Stock/Test Radar", 1)
        self.assertEqual(refund, 1_100)
        self.assertEqual(self.c.credits, 979_100)
        self.assertEqual(self.c.state["inventory"]["Stock/Test Radar"], 1)
        self.assertEqual(self.c.credit_ledger[-1]["kind"], "component-sale")
        self.assertEqual(self.c.credit_ledger[-1]["amount"], 1_100)
        self.assertEqual(self.c.credit_ledger[-1]["balance"], 979_100)

    def test_insufficient_credits_leave_inventory_and_ledger_unchanged(self):
        self.c.set_credits(100)
        before = copy.deepcopy(self.c.state)
        with self.assertRaisesRegex(ValueError, "Not enough credits"):
            self.economy.buy_component("Stock/Test Radar", 1)
        self.assertEqual(self.c.state, before)
        self.assertEqual(self.c.credits, 100)
        self.assertNotIn("Stock/Test Radar", self.c.state["inventory"])

    def test_hull_purchase_and_sale_use_campaign_wallet(self):
        hull = self.catalog.factions["Stock/Alliance"].hulls[0]
        key, price = self.economy.buy_hull("Stock/Alliance", hull, "Purchased Frigate", 4321)
        self.assertEqual(price, 500_000)
        self.assertEqual(self.c.credits, 500_000)
        self.assertEqual(self.c.state["owned_hulls"][key]["price"], 500_000)
        self.assertEqual(self.c.state["owned_hulls"][key]["source"], "purchase")
        self.assertEqual(self.c.credit_ledger[-1]["kind"], "hull-purchase")
        self.assertEqual(self.c.credit_ledger[-1]["amount"], -500_000)
        self.assertEqual(self.c.credit_ledger[-1]["balance"], 500_000)
        self.assertEqual(self.c.player.ships[-1].findtext("Key"), key)

        _returned, refund = self.economy.sell_hull(key)
        self.assertEqual(refund, 50_000)
        self.assertEqual(self.c.credits, 550_000)
        self.assertNotIn(key, self.c.state["owned_hulls"])
        self.assertEqual(self.c.credit_ledger[-1]["kind"], "hull-sale")
        self.assertEqual(self.c.credit_ledger[-1]["amount"], 50_000)
        self.assertEqual(self.c.credit_ledger[-1]["balance"], 550_000)

    def test_component_purchase_rolls_back_credits_and_inventory_on_save_failure(self):
        before = copy.deepcopy(self.c.state)
        with patch.object(self.c, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.economy.buy_component("Stock/Test Radar", 1)
        self.assertEqual(self.c.state, before)

    def test_hull_purchase_rolls_back_wallet_and_roster_on_save_failure(self):
        hull = self.catalog.factions["Stock/Alliance"].hulls[0]
        before = copy.deepcopy(self.c.state)
        before_ships = len(self.c.player.ships)
        with patch.object(self.c, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.economy.buy_hull("Stock/Alliance", hull, "Broken Purchase", 4322)
        self.assertEqual(self.c.state, before)
        self.assertEqual(len(self.c.player.ships), before_ships)

    def test_hull_purchase_is_rejected_when_too_expensive(self):
        hull = self.catalog.factions["Stock/Alliance"].hulls[0]
        self.c.set_credits(100)
        before = copy.deepcopy(self.c.state)
        with self.assertRaisesRegex(ValueError, "Not enough credits"):
            self.economy.buy_hull("Stock/Alliance", hull, "Unaffordable", 4323)
        self.assertEqual(self.c.state, before)

    def test_credit_adjustment_rejects_negative_balance(self):
        with self.assertRaisesRegex(ValueError, "Not enough credits"):
            self.c.adjust_credits(-1_000_001, "test", "Too much")
        self.assertEqual(self.c.credits, 1_000_000)
        with self.assertRaisesRegex(ValueError, "whole number"):
            self.c.adjust_credits(1.5, "test", "Fractional")


if __name__ == "__main__":
    unittest.main()
