from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nbc import setup


def fake_game(root, name="Nebulous"):
    game = Path(root) / name
    (game / "Nebulous_Data" / "Managed").mkdir(parents=True, exist_ok=True)
    (game / "Nebulous.exe").write_text("", encoding="utf-8")
    (game / "Nebulous_Data" / "Managed" / "Nebulous.dll").write_text("", encoding="utf-8")
    return game


def fake_bridge(root, version="0.5.0"):
    source = Path(root) / "bridge"
    source.mkdir(parents=True, exist_ok=True)
    (source / "NBCBridge.dll").write_text("dll", encoding="utf-8")
    (source / "0Harmony.dll").write_text("harmony", encoding="utf-8")
    (source / "Harmony-LICENSE.txt").write_text("license", encoding="utf-8")
    (source / "ModInfo.xml").write_text(
        f"<ModInfo><ModName>{setup.BRIDGE_MOD_NAME}</ModName><ModVer>{version}</ModVer></ModInfo>",
        encoding="utf-8",
    )
    return source


class SetupTests(unittest.TestCase):
    def test_net_string_hash_matches_the_game_mod_id(self):
        self.assertEqual(setup.net_string_hash("NBC Campaign Bridge"), 1370915012)
        self.assertEqual(setup.mod_id(), 1370915012)

    def test_valid_game_requires_the_managed_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = fake_game(tmp)
            self.assertTrue(setup.valid_game(game))
            (game / "Nebulous_Data" / "Managed" / "Nebulous.dll").unlink()
            self.assertFalse(setup.valid_game(game))

    def test_detect_game_scans_steam_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "SteamLibrary"
            fake_game(library / "steamapps" / "common")
            with patch("nbc.setup.steam_libraries", return_value=[library]):
                self.assertEqual(setup.detect_game(), library / "steamapps" / "common" / "Nebulous")
            with patch("nbc.setup.steam_libraries", return_value=[]):
                self.assertIsNone(setup.detect_game())

    def test_install_bridge_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = fake_game(tmp)
            source = fake_bridge(tmp)
            first = setup.install_bridge(game, source)
            self.assertTrue(first["installed"])
            self.assertFalse(first["updated"])
            target = setup.installed_bridge_dir(game)
            for name in setup.BRIDGE_FILES:
                self.assertTrue((target / name).is_file(), name)
            second = setup.install_bridge(game, source)
            self.assertFalse(second["updated"])
            upgraded = setup.install_bridge(game, fake_bridge(Path(tmp) / "new", version="0.6.0"))
            self.assertTrue(upgraded["updated"])
            self.assertEqual(setup.installed_bridge_version(game), "0.6.0")

    def test_install_bridge_rejects_a_bad_game_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = fake_bridge(tmp)
            with self.assertRaisesRegex(ValueError, "NEBULOUS installation"):
                setup.install_bridge(Path(tmp) / "not-a-game", source)

    def test_enable_mod_preserves_other_mods(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = fake_game(tmp)
            mods = game / "Mods"
            mods.mkdir()
            (mods / setup.MOD_LIST_NAME).write_text(
                '<?xml version="1.0"?><PersistentActiveMods><Mods>'
                "<unsignedLong>42</unsignedLong></Mods></PersistentActiveMods>",
                encoding="utf-8",
            )
            result = setup.enable_mod(game)
            self.assertTrue(result["changed"])
            self.assertEqual(sorted(setup.active_mod_ids(game)), [42, setup.mod_id()])
            again = setup.enable_mod(game)
            self.assertFalse(again["changed"])
            self.assertEqual(sorted(setup.active_mod_ids(game)), [42, setup.mod_id()])

    def test_enable_mod_creates_the_list_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = fake_game(tmp)
            setup.enable_mod(game)
            self.assertEqual(setup.active_mod_ids(game), [setup.mod_id()])

    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("nbc.setup.app_dir", return_value=Path(tmp)):
                self.assertEqual(setup.load_config(), {"game_path": "", "data_path": ""})
                setup.save_config({"game_path": "R:\\Nebulous", "data_path": ""})
                self.assertEqual(setup.load_config()["game_path"], "R:\\Nebulous")
                self.assertEqual(setup.config_path(), Path(tmp) / "config.json")

    def test_resolve_data_prefers_the_portable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("nbc.setup.app_dir", return_value=Path(tmp)):
                self.assertEqual(setup.resolve_data(), Path(tmp) / "data")

    def test_resolve_data_falls_back_when_not_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("nbc.setup.app_dir", return_value=Path(tmp)):
                with patch("nbc.setup._writable", return_value=False):
                    with patch.dict("os.environ", {"LOCALAPPDATA": str(Path(tmp) / "local")}):
                        result = setup.resolve_data()
            self.assertIn(setup.APP_NAME, str(result))
            self.assertTrue(str(result).endswith("data"))

    def test_bridge_status_reports_install_enable_and_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = fake_game(tmp)
            source = fake_bridge(tmp)
            status = setup.bridge_status(game, source)
            self.assertTrue(status["game_ok"])
            self.assertFalse(status["installed"])
            setup.install_bridge(game, source)
            setup.enable_mod(game)
            setup.catalogue_path(game).parent.mkdir(parents=True, exist_ok=True)
            setup.catalogue_path(game).write_text("<FittingCatalog Format=\"1\" />", encoding="utf-8")
            status = setup.bridge_status(game, source)
            self.assertTrue(status["installed"])
            self.assertTrue(status["version_ok"])
            self.assertTrue(status["enabled"])
            self.assertTrue(status["catalogue"])
            self.assertIn("Bridge enabled: yes", setup.status_text(status))


if __name__ == "__main__":
    unittest.main()
