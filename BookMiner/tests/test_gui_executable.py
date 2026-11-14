import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner-gui.py'
spec = importlib.util.spec_from_file_location('bookminer_gui_test_module', MODULE_PATH)
gui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gui
spec.loader.exec_module(gui)


class GuiExecutableTests(unittest.TestCase):
    def test_stale_queue_done_does_not_erase_newer_work(self):
        instance = Mock()
        instance.task_queue_total_highwater = 0
        instance.task_queue_taken_highwater = 0
        instance.task_queue_remaining = None
        instance.busy_action = None
        instance.enqueue_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._handle_task_queue_progress_line(instance, '[TaskQueueStart] 1/3')
        gui.BookMinerGui._handle_task_queue_progress_line(instance, '[TaskQueueDone] 2/2')
        self.assertEqual(instance.task_queue_remaining, 1)
        self.assertEqual(instance.enqueue_status_text, 'enqueue 2/3')
        gui.BookMinerGui._handle_task_queue_progress_line(instance, '[TaskQueueDone] 3/3')
        self.assertEqual(instance.task_queue_remaining, 0)
    def test_settings_wait_for_latest_apply_acknowledgement(self):
        instance = Mock()
        instance.settings_apply_pending = True
        instance.settings_apply_outstanding = 2
        instance.settings_apply_latest_sent = True
        instance.busy_action = None
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[SettingsApplied]')
        self.assertTrue(instance.settings_apply_pending)
        instance.setting_status.set.assert_not_called()
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[SettingsApplied]')
        self.assertFalse(instance.settings_apply_pending)
        instance.setting_status.set.assert_called_once_with('保存して実行中のBookMinerに反映しました。')

    def test_settings_failure_does_not_cancel_other_command(self):
        instance = Mock()
        instance.settings_apply_pending = True
        instance.settings_apply_outstanding = 1
        instance.settings_apply_latest_sent = True
        instance.busy_action = 'manual_peta_next'
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._handle_auto_enqueue_line(
            instance, 'Exception : command failed, command=reload-settings : invalid')
        self.assertFalse(instance.settings_apply_pending)
        self.assertEqual(instance.busy_action, 'manual_peta_next')

    def test_worker_failure_does_not_cancel_running_auto_enqueue(self):
        for line in ('[TaskWorkerError] Exception : engine failed',
                     'Exception : task failed, job=3 : engine failed'):
            instance = Mock()
            instance.settings_apply_pending = False
            instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT
            instance.busy_action = 'auto_enqueue'
            gui.BookMinerGui._handle_auto_enqueue_line(instance, line)
            instance._abort_auto_enqueue.assert_not_called()
            self.assertEqual(instance.busy_action, 'auto_enqueue')

    def test_auto_enqueue_merges_each_duplicate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'think_sfens.txt'
            target = root / 'think_sfens-tmp.txt'
            instance = Mock()
            instance.auto_tmp_seen = {}
            with patch.object(gui, 'THINK_SFENS_PATH', source), patch.object(gui, 'AUTO_THINK_SFENS_PATH', target):
                source.write_text('startpos, book_extend_ply=10, eval_limit=100, game_ply_limit=100\n')
                self.assertTrue(gui.BookMinerGui._append_auto_think_sfens_tmp(instance, 'first'))
                source.write_text('startpos, book_extend_ply=6, eval_limit=400, game_ply_limit=200\n')
                self.assertTrue(gui.BookMinerGui._append_auto_think_sfens_tmp(instance, 'second'))
            self.assertEqual(target.read_text(),
                             'startpos, book_extend_ply=10, eval_limit=400, game_ply_limit=200\n')

    def test_auto_enqueue_stops_when_no_positions_are_extracted(self):
        instance = Mock()
        instance.auto_enqueue_enabled.get.return_value = True
        instance.auto_step2_queue = []
        instance.auto_tmp_seen = {}
        gui.BookMinerGui._start_next_auto_step2(instance)
        instance._abort_auto_enqueue.assert_called_once_with(
            'auto enqueue stopped: no positions were extracted.')
        instance._promote_auto_think_sfens_tmp.assert_not_called()
        instance.send_enqueue.assert_not_called()

    def test_edit_start_file_uses_current_unapplied_path(self):
        instance = Mock()
        instance.setting_start_path.get.return_value = 'book/開始局面.txt'
        gui.BookMinerGui._edit_start_sfens(instance)
        instance._edit_file.assert_called_once_with(gui.BASE_DIR / 'book/開始局面.txt', 'startpos\n')

    def test_vscode_edit_existing_and_optional_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing' / '開始局面.txt'
            instance = Mock()
            with patch.object(gui, 'find_vscode', return_value='/fake/Code'), \
                 patch.object(gui.messagebox, 'askyesno', return_value=False), \
                 patch.object(gui.subprocess, 'Popen') as popen:
                gui.BookMinerGui._edit_file(instance, path, 'startpos\n')
                self.assertFalse(path.exists())
                popen.assert_not_called()
            with patch.object(gui, 'find_vscode', return_value='/fake/Code'), \
                 patch.object(gui.messagebox, 'askyesno', return_value=True), \
                 patch.object(gui.subprocess, 'Popen') as popen:
                gui.BookMinerGui._edit_file(instance, path, 'startpos\n')
                self.assertEqual(path.read_text(encoding='utf8'), 'startpos\n')
                self.assertEqual(popen.call_args.args[0], ['/fake/Code', str(path)])
            with patch.object(gui, 'find_vscode', return_value=None), \
                 patch.object(gui.messagebox, 'showerror') as error, \
                 patch.object(gui.subprocess, 'Popen') as popen:
                gui.BookMinerGui._edit_file(instance, path, '')
                error.assert_called_once()
                popen.assert_not_called()

    def test_apply_settings_saves_and_requests_live_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = Mock()
            instance.use_cpp = False
            instance.settings_apply_outstanding = 0
            instance.setting_minutes.get.return_value = '15'
            instance.setting_shock_every.get.return_value = '2'
            instance.setting_start_path.get.return_value = 'book/開始.txt'
            instance.setting_max_ply.get.return_value = '250'
            instance.setting_threads.get.return_value = '8'
            instance.backup_in_progress = False
            instance.is_running.return_value = True
            instance.send_command.return_value = True
            path = Path(directory) / 'settings.json5'
            with patch.object(gui, 'BOOK_MINER_SETTINGS_PATH', path):
                gui.BookMinerGui.apply_mining_settings(instance)
            self.assertEqual(gui.load_mining_settings(path), {
                'auto_save_interval_seconds': 900,
                'auto_peta_shock_every_n_saves': 2,
                'peta_next_start_sfens_path': 'book/開始.txt',
                'max_book_ply': 250,
                'peta_shock_threads': 8,
            })
            instance.send_command.assert_called_once_with('reload-settings')
            instance.game_ply_limit.set.assert_called_once_with('250')
            self.assertTrue(instance.settings_apply_pending)

    def test_settings_update_preserves_comments_and_other_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json5'
            path.write_text('{\n // Keep this comment\n bookminer_cpp_path: "engine.exe",\n'
                            ' peta_shock_engine_path: "engines/王.exe",\n'
                            ' max_book_ply: 180, // limit\n nested: {max_book_ply: 2},\n}\n', encoding='utf8')
            values = dict(gui.MINING_SETTING_DEFAULTS)
            values['peta_next_start_sfens_path'] = 'book/指定局面.txt'
            gui.save_mining_settings(path, values)
            result = path.read_text(encoding='utf8')
            self.assertIn('// Keep this comment', result)
            self.assertIn('nested: {max_book_ply: 2}', result)
            self.assertIn('engines/王.exe', result)
            self.assertEqual(gui.load_mining_settings(path), values)
            self.assertEqual(gui.json5.loads(result)['bookminer_cpp_path'], 'engine.exe')
            gui.save_mining_settings(path, values)
            self.assertEqual(gui.load_mining_settings(path), values)

    def test_automatic_peta_follows_successful_backups(self):
        instance = Mock()
        instance.busy_action = None
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
        instance.auto_backup_count = 0
        instance.auto_peta_pending = False
        instance.ignore_current_backup = False
        instance.settings_apply_pending = False
        for _ in range(2):
            gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupStart]')
            gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
        self.assertFalse(instance.auto_peta_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupStart]')
        gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : auto save failed')
        self.assertFalse(instance.auto_peta_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupStart]')
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
        self.assertTrue(instance.auto_peta_pending)
        instance.is_running.return_value = True
        instance.command_ready = True
        instance.enqueue_pending = False
        instance.peta_makebook_active = False
        instance.send_command.return_value = True
        gui.BookMinerGui._maybe_start_auto_peta(instance)
        instance.send_command.assert_called_once_with('pl', origin='AUTO')
        self.assertEqual(instance.busy_action, 'auto_peta_shock')

    def test_auto_enqueue_does_not_run_peta_shock(self):
        instance = Mock()
        instance.auto_enqueue_enabled.get.return_value = True
        instance.is_running.return_value = True
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.busy_action = None
        instance.auto_peta_pending = False
        instance.task_queue_remaining = 5
        instance._get_auto_enqueue_threshold.return_value = 10
        instance._selected_auto_step2_methods.return_value = [gui.AUTO_STEP2_PETA_NEXT]
        instance._reset_auto_think_sfens_tmp.return_value = True
        gui.BookMinerGui._maybe_start_auto_enqueue(instance)
        self.assertEqual(instance.auto_enqueue_state, gui.AUTO_ENQUEUE_NEXT)
        instance.send_command.assert_not_called()
        instance._start_next_auto_step2.assert_called_once_with()

    def test_default_relative_absolute_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json5'
            self.assertEqual(gui.configured_bookminer_cpp_path(root, settings), (root / 'BookMinerCpp.exe', False))
            settings.write_text('{ // comment\n max_book_ply: 200, }', encoding='utf8')
            self.assertEqual(gui.configured_bookminer_cpp_path(root, settings), (root / 'BookMinerCpp.exe', False))
            settings.write_text('{bookminer_cpp_path: "build/custom.exe"}', encoding='utf8')
            self.assertEqual(gui.configured_bookminer_cpp_path(root, settings), (root / 'build/custom.exe', True))
            absolute = (root / 'other/custom.exe').as_posix()
            settings.write_text('{bookminer_cpp_path: "' + absolute + '"}', encoding='utf8')
            self.assertEqual(gui.configured_bookminer_cpp_path(root, settings), (Path(absolute), True))
            for contents in ('{bookminer_cpp_path: " "}', '{bookminer_cpp_path: 5}', '[]', '{'):
                settings.write_text(contents, encoding='utf8')
                with self.subTest(contents=contents), self.assertRaises(ValueError):
                    gui.configured_bookminer_cpp_path(root, settings)

    def test_gui_launch_uses_bookminer_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'somewhere' / 'custom.exe'
            executable.parent.mkdir()
            executable.touch()
            instance = Mock()
            instance.is_running.return_value = False
            instance.use_cpp = True
            instance.cpp_executable = executable
            with patch.object(gui.subprocess, 'Popen') as popen, patch.object(gui.threading, 'Thread'):
                gui.BookMinerGui.start_process(instance)
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(executable), '--from_gui', '--work-dir', str(gui.BASE_DIR)])
            self.assertEqual(kwargs['cwd'], gui.BASE_DIR)

    def test_explicit_path_selects_cpp_even_without_default_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json5'
            settings.write_text('{bookminer_cpp_path: "build/custom.exe"}', encoding='utf8')
            with patch.object(gui, 'BASE_DIR', root), patch.object(gui, 'BOOK_MINER_SETTINGS_PATH', settings), \
                 patch.object(gui.tk, 'Tk'), patch.object(gui, 'BookMinerGui') as construct, \
                 patch.object(gui, 'configure_initial_window_size'), patch.object(sys, 'argv', ['gui']):
                self.assertEqual(gui.main(), 0)
            self.assertTrue(construct.call_args.kwargs['use_cpp'])
            self.assertEqual(construct.call_args.kwargs['cpp_executable'], root / 'build/custom.exe')


if __name__ == '__main__':
    unittest.main()
