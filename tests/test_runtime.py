import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from nbc.core import Campaign, put, read_xml, write_xml
from nbc.refit import RefitService
from nbc.runtime import GameRuntime, GameProcess
from test_core import sample
import test_refit


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = Campaign(self.root / 'game', self.root / 'data')
        self.c.player = sample()
        (self.c.game / 'Mods/NBCBridge').mkdir(parents=True)
        (self.c.game / 'Nebulous.exe').touch()
        (self.c.game / 'Mods/NBCBridge/NBCBridge.dll').touch()
        (self.c.game / 'Mods/NBCBridge/ModInfo.xml').write_text('<ModInfo><ModVer>0.5.0</ModVer></ModInfo>')
        self.processes = []
        self.child = Mock()
        self.child.poll.return_value = None
        self.launcher = Mock(return_value=self.child)
        self.clock = Mock(return_value=100)
        self.c.runtime = GameRuntime(self.c.game, self.c.exchange, scanner=lambda _: self.processes,
            launcher=self.launcher, clock=self.clock, wall_clock=lambda: 1000)
        self.r = self.c.runtime
        self.refits = RefitService(self.c)

    def tearDown(self):
        self.temp.cleanup()

    def heartbeat(self, phase='ready', pid=42, started=900, epoch=1000):
        root = ET.Element('GameRuntime')
        for k, v in dict(Protocol='1', Instance='process-one', Pid=pid, StartedEpoch=started,
                         HeartbeatEpoch=epoch, Ready='true', Phase=phase, LaunchToken=self.r.launch_token or '').items():
            put(root, k, v)
        write_xml(self.c.exchange / 'runtime-status.xml', root)

    def connected(self):
        self.processes[:] = [GameProcess(42, 900)]
        self.heartbeat()

    def test_cold_refit_waits_for_live_bridge_before_publishing(self):
        record = self.refits.start()
        self.launcher.assert_called_once()
        self.assertEqual(self.launcher.call_args.kwargs['cwd'], str(self.c.game))
        self.assertFalse((self.c.exchange / 'refit-request.xml').exists())
        self.connected()
        self.c.pump_game()
        doc = read_xml(self.c.exchange / 'refit-request.xml')
        self.assertEqual(doc.findtext('RuntimeInstance'), 'process-one')
        self.assertEqual(doc.findtext('CloseGame'), 'true')
        self.assertFalse(record['launch_pending'])

    def test_existing_game_is_reused(self):
        self.connected()
        self.refits.start()
        self.c.pump_game()
        self.launcher.assert_not_called()

    def test_stale_or_reused_pid_heartbeat_rejected(self):
        self.connected()
        for fields in [dict(epoch=990), dict(started=700), dict(pid=77)]:
            self.heartbeat(**fields)
            self.assertIsNone(self.r.heartbeat(self.processes))

    def test_previous_export_and_shutdown_are_allowed_to_finish(self):
        self.connected()
        self.heartbeat('awaiting-ack')
        self.refits.start()
        self.c.pump_game()
        self.launcher.assert_not_called()
        self.assertFalse((self.c.exchange / 'refit-request.xml').exists())
        self.processes.clear()
        self.c.pump_game()
        self.launcher.assert_called_once()
        self.connected()
        self.c.pump_game()
        self.assertTrue((self.c.exchange / 'refit-request.xml').exists())

    def test_unrelated_editor_not_interrupted(self):
        self.connected()
        self.heartbeat('starting')
        self.refits.start()
        self.c.pump_game()
        self.assertFalse((self.c.exchange / 'refit-request.xml').exists())
        self.refits.cancel()
        self.assertFalse((self.c.exchange / 'runtime-command.xml').exists())

    def test_timeout_keeps_session_recoverable(self):
        self.refits.start()
        self.clock.return_value = 281
        with self.assertRaises(TimeoutError):
            self.c.pump_game()
        self.assertIn('launch_error', self.refits.active)
        self.c.pump_game()  # No repeated automatic launches after a timeout.
        self.assertEqual(self.launcher.call_count, 1)

    def test_early_exit_reports_failure(self):
        self.r.begin('refit')
        self.clock.return_value = 111
        self.child.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            self.r.poll()

    def test_catalog_only_closes_game_it_launched(self):
        self.r.begin('catalog')
        self.connected()
        self.r.poll()
        command = read_xml(self.c.exchange / 'runtime-command.xml')
        self.assertEqual(command.findtext('LaunchToken'), self.r.launch_token)

    def test_cancel_during_cold_start_uses_launch_token(self):
        self.refits.start()
        token = self.r.launch_token
        self.refits.cancel()
        self.assertEqual(read_xml(self.c.exchange / 'runtime-command.xml').findtext('LaunchToken'), token)

    def test_battle_waits_for_bridge_and_scopes_request(self):
        self.c.enemy = sample()
        self.c.launch()
        self.assertFalse((self.c.exchange / 'request.xml').exists())
        self.connected()
        self.c.pump_game()
        self.assertEqual(read_xml(self.c.exchange / 'request.xml').findtext('RuntimeInstance'), 'process-one')

    def test_old_bridge_rejected_before_game_launch(self):
        (self.c.game / 'Mods/NBCBridge/ModInfo.xml').write_text('<ModInfo><ModVer>0.2.0</ModVer></ModInfo>')
        with self.assertRaisesRegex(ValueError, '0.5'):
            self.refits.start()
        self.launcher.assert_not_called()

    def test_resume_can_replace_only_its_own_live_editor(self):
        self.connected()
        self.heartbeat('refit')
        self.r.begin('refit', resume_instance='process-one')
        self.assertTrue(self.r.poll())
        self.r.submitted()
        self.r.begin('refit', resume_instance='different-process')
        self.assertFalse(self.r.poll())

    def test_battle_ack_follows_durable_import(self):
        self.c.enemy = sample()
        record = self.c.prepare()
        result = ET.Element('BattleResult')
        for k, v in dict(Id=record['id'], Outcome='Victory', RuntimeInstance='battle-process').items():
            put(result, k, v)
        write_xml(Path(record['directory']) / 'result.xml', result)
        with patch.object(self.c, 'persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.c.collect()
        self.assertFalse((self.c.exchange / 'operation-ack.xml').exists())
        self.assertIsNotNone(self.c.state['active_battle'])
        self.c.collect()
        self.assertEqual(read_xml(self.c.exchange / 'operation-ack.xml').findtext('Instance'), 'battle-process')
        self.assertIsNone(Campaign(self.c.game, self.c.data).state['active_battle'])


class HandoffTests(unittest.TestCase):
    setUp = test_refit.RefitTests.setUp
    tearDown = test_refit.RefitTests.tearDown
    ready = test_refit.RefitTests.ready
    def prepare_live_marker(self):
        self.service.start(launch=False)
        self.ready()
        record = self.service.active
        record['runtime_instance'] = 'exporting-process'
        path = Path(record['directory']) / 'ready-1.xml'
        doc = read_xml(path)
        put(doc, 'RuntimeInstance', 'exporting-process')
        write_xml(path, doc)

    def test_validated_persisted_refit_is_acknowledged(self):
        self.prepare_live_marker()
        self.service.collect()
        ack = read_xml(self.c.exchange / 'operation-ack.xml')
        self.assertEqual(ack.findtext('Instance'), 'exporting-process')
        self.assertEqual(ack.findtext('Kind'), 'refit')
        self.assertEqual(ack.findtext('Revision'), '1')
        self.assertEqual(Campaign(self.c.game, self.c.data).state['active_refit']['stage'], 'review')

    def test_failed_campaign_save_never_acknowledges_and_can_retry(self):
        self.prepare_live_marker()
        with patch.object(self.c, 'persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.service.collect()
        self.assertFalse((self.c.exchange / 'operation-ack.xml').exists())
        self.assertEqual(self.service.active['stage'], 'editor')
        self.service.collect()
        self.assertTrue((self.c.exchange / 'operation-ack.xml').exists())

    def test_ack_write_retries_after_restart(self):
        self.prepare_live_marker()
        with patch('nbc.core.write_xml', side_effect=OSError('exchange unavailable')):
            with self.assertRaises(OSError):
                self.service.collect()
        restarted = Campaign(self.c.game, self.c.data)
        restarted.flush_ack()
        self.assertTrue((self.c.exchange / 'operation-ack.xml').exists())

    def test_invalid_export_never_acknowledges(self):
        self.prepare_live_marker()
        (Path(self.service.active['directory']) / 'draft-1.fleet').write_text('broken')
        with self.assertRaises(ValueError):
            self.service.collect()
        self.assertFalse((self.c.exchange / 'operation-ack.xml').exists())

    def test_failed_discard_save_does_not_acknowledge(self):
        self.prepare_live_marker()
        with patch.object(self.c, 'persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.service.cancel()
        self.assertFalse((self.c.exchange / 'operation-ack.xml').exists())
        self.assertIsNotNone(self.service.active)
        self.assertNotIn('pending_ack', self.c.state)

