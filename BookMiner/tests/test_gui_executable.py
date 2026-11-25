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
    def test_skipped_auto_backup_does_not_count_as_a_save(self):
        instance = Mock()
        instance.settings_apply_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.busy_action = None
        instance.backup_in_progress = True
        instance.auto_backup_count = 1
        instance.auto_peta_pending = False
        instance.ignore_current_backup = True
        instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupSkipped] reason=timer-reset')
        self.assertFalse(instance.backup_in_progress)
        self.assertFalse(instance.ignore_current_backup)
        self.assertEqual(instance.auto_backup_count, 1)
        self.assertFalse(instance.auto_peta_pending)

    def test_closed_stdin_reports_send_failure(self):
        for origin in ('GUI', 'AUTO'):
            with self.subTest(origin=origin), patch.object(gui.messagebox, 'showerror') as showerror:
                instance = Mock()
                instance.is_running.return_value = True
                instance.command_ready = True
                instance.process.stdin.write.side_effect = ValueError('I/O operation on closed file')
                self.assertFalse(gui.BookMinerGui.send_command(instance, 'cancel-peta-unsolved', origin))
                if origin == 'GUI':
                    showerror.assert_called_once()
                else:
                    showerror.assert_not_called()
                    self.assertIn('send failed', instance._append_log.call_args.args[1])

    def test_manual_backup_button_cancels_once_while_peta_runs(self):
        instance = Mock()
        instance.busy_action = 'manual_peta_shock'
        instance.manual_backup_pending = True
        instance.manual_backup_cancel_pending = False
        instance.send_command.return_value = True
        gui.BookMinerGui.on_backup_button(instance)
        gui.BookMinerGui.on_backup_button(instance)
        instance.send_command.assert_called_once_with('cancel-manual-backup')
        self.assertTrue(instance.manual_backup_cancel_pending)
        self.assertEqual(instance.busy_action, 'manual_peta_shock')

    def test_manual_and_peta_cancellation_are_independent(self):
        for first in ('manual', 'peta'):
            with self.subTest(first=first):
                instance = Mock()
                instance.busy_action = 'manual_peta_shock'
                instance.manual_backup_pending = True
                instance.manual_backup_cancel_pending = True
                instance.peta_shock_cancel_pending = True
                instance.peta_makebook_active = True
                instance.auto_peta_pending = False
                instance.settings_apply_pending = False
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                if first == 'manual':
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, '[ManualBackupCancelled]')
                    self.assertFalse(instance.manual_backup_pending)
                    self.assertEqual(instance.busy_action, 'manual_peta_shock')
                    self.assertTrue(instance.peta_shock_cancel_pending)
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaShockCancelled]')
                else:
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaShockCancelled]')
                    self.assertTrue(instance.manual_backup_pending)
                    self.assertTrue(instance.manual_backup_cancel_pending)
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, '[ManualBackupCancelled]')
                self.assertIsNone(instance.busy_action)
                self.assertFalse(instance.manual_backup_pending)
                self.assertFalse(instance.manual_backup_cancel_pending)

    def test_manual_backup_legacy_progress_is_not_completion(self):
        instance = Mock()
        instance.busy_action = 'manual_backup'
        instance.manual_backup_pending = True
        instance.manual_backup_cancel_pending = True
        instance.settings_apply_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '..w command write has done. path=x')
        self.assertTrue(instance.manual_backup_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[ManualBackupDone] path=x')
        self.assertFalse(instance.manual_backup_pending)
        self.assertFalse(instance.manual_backup_cancel_pending)

    def test_unrelated_failure_does_not_clear_manual_backup(self):
        instance = Mock()
        instance.busy_action = 'manual_backup'
        instance.manual_backup_pending = True
        instance.manual_backup_cancel_pending = True
        instance.settings_apply_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : command failed, command=sd : invalid')
        self.assertEqual(instance.busy_action, 'manual_backup')
        self.assertTrue(instance.manual_backup_pending)
        self.assertTrue(instance.manual_backup_cancel_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[ManualBackupCancelled]')
        self.assertFalse(instance.manual_backup_pending)

    def test_unsolved_sends_depth_list_and_rejects_invalid_input(self):
        instance = Mock()
        instance.peta_unsolved_min_depths.get.return_value = '10,8,6,4'
        instance._get_step2_int_token.side_effect = ['9999', '200', '6', '400']
        instance._build_default_settings_command.return_value = 'sd 30 9999 200 6 400'
        instance._begin_manual_action.return_value = True
        instance.send_command.return_value = True
        self.assertTrue(gui.BookMinerGui.send_peta_unsolved(instance))
        self.assertEqual(instance.send_command.call_args_list[-1].args[0], 'pu 10,8,6,4 9999 200 6 400')
        for value in ('', '10,,4', '-1,4', '1,', '1,abc', '2147483648'):
            with self.subTest(value=value), patch.object(gui.messagebox, 'showerror'):
                instance.peta_unsolved_min_depths.get.return_value = value
                self.assertFalse(gui.BookMinerGui.send_peta_unsolved(instance))

    def test_failed_enqueue_during_peta_keeps_peta_running_and_queue_count(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest', 'auto_peta_shock', 'manual_peta_read'):
            for command in ('e', 'eb'):
                with self.subTest(action=action, command=command):
                    instance = Mock()
                    instance.settings_apply_pending = False
                    instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                    instance.busy_action = action
                    instance.enqueue_pending = True
                    instance.task_queue_remaining = 123
                    gui.BookMinerGui._handle_auto_enqueue_line(
                        instance, f'Exception : command failed, command={command} : no search engines are available')
                    self.assertEqual(instance.busy_action, action)
                    self.assertFalse(instance.enqueue_pending)
                    self.assertEqual(instance.task_queue_remaining, 123)
                    instance._update_buttons.assert_called_once()

    def test_other_command_error_does_not_clear_running_peta(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest', 'auto_peta_shock', 'manual_peta_read'):
            with self.subTest(action=action):
                instance = Mock()
                instance.settings_apply_pending = False
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                instance.busy_action = action
                instance.enqueue_pending = False
                gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : command failed, command=job : bad id')
                self.assertEqual(instance.busy_action, action)
                instance._update_buttons.assert_not_called()

    def test_failed_enqueue_keeps_other_jobs_and_stops_auto_enqueue(self):
        instance = Mock()
        instance.settings_apply_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.busy_action = 'manual_enqueue'
        instance.enqueue_pending = False
        instance.task_queue_remaining = 321
        gui.BookMinerGui._handle_auto_enqueue_line(
            instance, 'Exception : command failed, command=e : missing file')
        self.assertIsNone(instance.busy_action)
        self.assertEqual(instance.task_queue_remaining, 321)
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_ENQUEUE
        instance.busy_action = 'auto_enqueue'
        gui.BookMinerGui._handle_auto_enqueue_line(
            instance, 'Exception : command failed, command=eb : missing file')
        instance._abort_auto_enqueue.assert_called_once()

    def test_unrelated_worker_error_does_not_reset_peta_next_cancel(self):
        instance = Mock()
        instance.settings_apply_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.busy_action = 'manual_peta_next'
        instance.peta_next_cancel_pending = True
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[TaskWorkerError] Exception : worker disconnected')
        self.assertTrue(instance.peta_next_cancel_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : command failed, command=job : bad id')
        self.assertEqual(instance.busy_action, 'manual_peta_next')
        self.assertTrue(instance.peta_next_cancel_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaNextCancelIgnored] no peta next is running')
        self.assertFalse(instance.peta_next_cancel_pending)
        instance.busy_action = 'auto_enqueue'
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT
        gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : command failed, command=job : bad id')
        instance._abort_auto_enqueue.assert_not_called()
    def test_job_control_updates_only_existing_job(self):
        instance = Mock()
        instance.task_job_items = {7: Mock()}
        instance.task_job_states = {}
        gui.BookMinerGui._handle_task_job_list_line(instance, '[JobControl] job=7 state=pause removed=0')
        self.assertEqual(instance.task_job_states[7], 'pause')
        gui.BookMinerGui._handle_task_job_list_line(instance, '[JobControl] job=7 state=resume removed=0')
        self.assertEqual(instance.task_job_states[7], 'resume')
        gui.BookMinerGui._handle_task_job_list_line(instance, '[JobControl] job=8 state=delete removed=3')
        self.assertNotIn(8, instance.task_job_states)

    def test_auto_peta_schedule_shows_estimate_and_states(self):
        start = gui.datetime(2026, 11, 15, 9, 0, 0)
        schedule = gui.auto_peta_schedule_text
        self.assertIn('2026/11/15 15:00:00頃（自動保存あと3回）', schedule(start, 10800, 0, 3))
        self.assertIn('2026/11/15 12:00:00頃（自動保存あと2回）', schedule(start, 10800, 1, 3))
        self.assertIn('開始待ち', schedule(start, 10800, 0, 3, pending=True))
        self.assertIn('実行中', schedule(start, 10800, 0, 3, running=True))
        self.assertIn('自動保存中', schedule(None, 10800, 1, 3, saving=True))
        self.assertIn('無効', schedule(start, 10800, 0, 0))
        self.assertEqual(schedule(None, None, 0, 3), '次回自動peta shock -')

    def test_auto_peta_schedule_uses_backup_events(self):
        instance = Mock()
        instance.mining_settings = {'auto_peta_shock_every_n_saves': 2}
        instance.auto_backup_count = 0
        instance.auto_peta_pending = False
        instance.ignore_current_backup = False
        instance.settings_apply_pending = False
        instance.busy_action = None
        gui.BookMinerGui._handle_startup_line(instance, '[BackupNext] next=2026/11/15_09:00:00 interval=10800')
        self.assertEqual(instance.next_backup_at, gui.datetime(2026, 11, 15, 9))
        self.assertEqual(instance.next_backup_interval, 10800)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupStart]')
        self.assertIsNone(instance.next_backup_at)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, 'Exception : auto save failed')
        self.assertEqual(instance.auto_backup_count, 0)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupStart]')
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
        self.assertEqual(instance.auto_backup_count, 1)
        self.assertFalse(instance.auto_peta_pending)

    def test_idle_engine_loss_updates_display(self):
        instance = Mock()
        gui.BookMinerGui._handle_startup_line(instance, '[EngineAlive] 0/1 dead=0')
        self.assertEqual(instance.engine_status_text, 'エンジン起動数 0/1')
        instance._update_mining_status.assert_called_once()

    def test_shock_completion_after_cancel_request_resets_button(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest', 'auto_peta_shock'):
            with self.subTest(action=action):
                instance = Mock()
                instance.busy_action = action
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                instance.peta_shock_cancel_pending = True
                instance.settings_apply_pending = False
                instance.manual_shock_auto_saves = 0
                instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaCommandDone]')
                self.assertFalse(instance.peta_shock_cancel_pending)
                self.assertIsNone(instance.busy_action)

    def test_rejected_shock_cancel_resets_pending_state(self):
        instance = Mock()
        instance.peta_shock_cancel_pending = True
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaShockCancelIgnored] no peta shock is running')
        self.assertFalse(instance.peta_shock_cancel_pending)
        instance._update_buttons.assert_called_once()

    def test_failed_manual_backup_does_not_clear_running_peta_shock(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest', 'auto_peta_shock'):
            with self.subTest(action=action):
                instance = Mock()
                instance.settings_apply_pending = False
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                instance.busy_action = action
                instance.manual_backup_pending = True
                gui.BookMinerGui._handle_auto_enqueue_line(
                    instance, 'Exception : command failed, command=w : disk full')
                self.assertFalse(instance.manual_backup_pending)
                self.assertEqual(instance.busy_action, action)
                instance._update_buttons.assert_called_once()

    def test_peta_shock_button_changes_to_cancel(self):
        for action, button in (('manual_peta_shock', 'peta_button'),
                               ('manual_peta_shock_latest', 'peta_latest_button'),
                               ('auto_peta_shock', 'peta_latest_button')):
            with self.subTest(action=action):
                instance = Mock()
                instance.is_running.return_value = True
                instance.command_ready = True
                instance.busy_action = action
                instance.enqueue_pending = False
                instance.manual_backup_pending = False
                instance.peta_makebook_active = True
                instance.peta_shock_cancel_pending = False
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                gui.BookMinerGui._update_buttons(instance)
                getattr(instance, button).configure.assert_any_call(text='cancel')
                getattr(instance, button).configure.assert_any_call(state='normal')

    def test_peta_shock_cancel_acknowledgement_clears_busy(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest', 'auto_peta_shock'):
            with self.subTest(action=action):
                instance = Mock()
                instance.busy_action = action
                instance.peta_makebook_active = True
                instance.peta_shock_cancel_pending = True
                instance.auto_peta_pending = True
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaShockCancelled]')
                self.assertIsNone(instance.busy_action)
                self.assertFalse(instance.peta_makebook_active)
                self.assertFalse(instance.peta_shock_cancel_pending)
                self.assertTrue(instance.auto_peta_pending)
                instance._update_buttons.assert_called_once()

    def test_auto_shock_cancel_keeps_new_reservation_made_during_shock(self):
        instance = Mock()
        instance.busy_action = 'auto_peta_shock'
        instance.auto_backup_count = 2
        instance.auto_peta_pending = False
        instance.ignore_current_backup = False
        instance.settings_apply_pending = False
        instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
        self.assertTrue(instance.auto_peta_pending)
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaShockCancelled]')
        self.assertTrue(instance.auto_peta_pending)
        self.assertIsNone(instance.busy_action)

    def test_manual_peta_success_resets_automatic_schedule(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest'):
            with self.subTest(action=action):
                instance = Mock()
                instance.busy_action = action
                instance.auto_backup_count = 2
                instance.auto_peta_pending = True
                instance.manual_shock_auto_saves = 0
                instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
                instance.settings_apply_pending = False
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaCommandDone]')
                self.assertEqual(instance.auto_backup_count, 0)
                self.assertFalse(instance.auto_peta_pending)
                self.assertIsNone(instance.busy_action)
                instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
                instance.ignore_current_backup = False
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
                self.assertEqual(instance.auto_backup_count, 1)
                self.assertFalse(instance.auto_peta_pending)

    def test_manual_peta_keeps_automatic_saves_completed_during_shock(self):
        for action in ('manual_peta_shock', 'manual_peta_shock_latest'):
            for saves in (1, 3, 4):
                with self.subTest(action=action, saves=saves):
                    instance = Mock()
                    instance.busy_action = action
                    instance.auto_backup_count = 2
                    instance.auto_peta_pending = True
                    instance.manual_shock_auto_saves = 0
                    instance.ignore_current_backup = False
                    instance.settings_apply_pending = False
                    instance.mining_settings = {'auto_peta_shock_every_n_saves': 3}
                    for _ in range(saves):
                        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[BackupDone]')
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaCommandDone]')
                    self.assertEqual(instance.auto_backup_count, saves % 3)
                    self.assertEqual(instance.auto_peta_pending, saves >= 3)

    def test_manual_peta_failure_and_cancel_preserve_automatic_schedule(self):
        for event in ('[PetaShockCancelled]', 'Exception : peta shock failed: error'):
            with self.subTest(event=event):
                instance = Mock()
                instance.busy_action = 'manual_peta_shock'
                instance.auto_backup_count = 2
                instance.auto_peta_pending = True
                instance.settings_apply_pending = False
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
                gui.BookMinerGui._handle_auto_enqueue_line(instance, event)
                self.assertEqual(instance.auto_backup_count, 2)
                self.assertTrue(instance.auto_peta_pending)

    def test_shock_cancel_sends_once_in_manual_and_auto_modes(self):
        for action, handler in (('manual_peta_shock', gui.BookMinerGui.on_peta_shock_button),
                                ('manual_peta_shock_latest', gui.BookMinerGui.on_peta_shock_latest_button),
                                ('auto_peta_shock', gui.BookMinerGui.on_peta_shock_latest_button)):
            with self.subTest(action=action):
                instance = Mock()
                instance.busy_action = action
                handler(instance)
                instance._cancel_peta_shock.assert_called_once()
        instance = Mock()
        instance.peta_shock_cancel_pending = False
        instance.send_command.return_value = True
        gui.BookMinerGui._cancel_peta_shock(instance)
        gui.BookMinerGui._cancel_peta_shock(instance)
        instance.send_command.assert_called_once_with('cancel-peta-shock')

    def test_peta_next_button_changes_to_cancel_while_running(self):
        instance = Mock()
        instance.is_running.return_value = True
        instance.command_ready = True
        instance.busy_action = 'manual_peta_next'
        instance.enqueue_pending = False
        instance.manual_backup_pending = False
        instance.peta_makebook_active = False
        instance.peta_next_cancel_pending = False
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        gui.BookMinerGui._update_buttons(instance)
        instance.next_button.configure.assert_any_call(text='cancel')
        instance.next_button.configure.assert_any_call(state='normal')
        instance.next_button.reset_mock()
        instance.peta_next_cancel_pending = True
        gui.BookMinerGui._update_buttons(instance)
        instance.next_button.configure.assert_any_call(state='disabled')

    def test_unsolved_button_changes_to_cancel_while_running(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                instance = Mock()
                instance.is_running.return_value = True
                instance.command_ready = True
                instance.busy_action = 'auto_enqueue' if auto else 'manual_peta_unsolved'
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT if auto else gui.AUTO_ENQUEUE_IDLE
                instance.auto_current_step2 = gui.AUTO_STEP2_PETA_UNSOLVED
                instance.enqueue_pending = False
                instance.manual_backup_pending = False
                instance.peta_makebook_active = False
                instance.peta_unsolved_cancel_pending = False
                gui.BookMinerGui._update_buttons(instance)
                instance.unsolved_button.configure.assert_any_call(text='cancel')
                instance.unsolved_button.configure.assert_any_call(state='normal')
                instance.unsolved_button.reset_mock()
                instance.peta_unsolved_cancel_pending = True
                gui.BookMinerGui._update_buttons(instance)
                instance.unsolved_button.configure.assert_any_call(state='disabled')

    def test_unsolved_button_cancels_manual_and_automatic_once(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                instance = Mock()
                instance.busy_action = 'auto_enqueue' if auto else 'manual_peta_unsolved'
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT if auto else gui.AUTO_ENQUEUE_IDLE
                instance.auto_current_step2 = gui.AUTO_STEP2_PETA_UNSOLVED
                instance.peta_unsolved_cancel_pending = False
                instance.send_command.return_value = True
                gui.BookMinerGui.on_peta_unsolved_button(instance)
                gui.BookMinerGui.on_peta_unsolved_button(instance)
                instance.send_command.assert_called_once_with('cancel-peta-unsolved')
                self.assertTrue(instance.peta_unsolved_cancel_pending)
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaUnsolvedCancelled]')
                self.assertFalse(instance.peta_unsolved_cancel_pending)
                if auto:
                    instance._abort_auto_enqueue.assert_called_once()
                else:
                    self.assertIsNone(instance.busy_action)

    def test_unsolved_progress_is_in_peta_log(self):
        instance = Mock()
        self.assertEqual(gui.BookMinerGui._classify_log_line(instance, '[PetaUnsolvedProgress] lines=20'), 'peta')

    def test_unsolved_cancel_does_not_clear_manual_backup(self):
        instance = Mock()
        instance.busy_action = 'manual_peta_unsolved'
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_IDLE
        instance.manual_backup_pending = True
        instance.manual_backup_cancel_pending = True
        instance.peta_unsolved_cancel_pending = True
        gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaUnsolvedCancelled]')
        self.assertTrue(instance.manual_backup_pending)
        self.assertTrue(instance.manual_backup_cancel_pending)
        self.assertIsNone(instance.busy_action)

    def test_cancel_peta_next_button_sends_cancel_once(self):
        instance = Mock()
        instance.busy_action = 'manual_peta_next'
        instance.peta_next_cancel_pending = False
        instance.send_command.return_value = True
        gui.BookMinerGui.on_peta_next_button(instance)
        gui.BookMinerGui.on_peta_next_button(instance)
        instance.send_command.assert_called_once_with('cancel-peta-next')
        self.assertTrue(instance.peta_next_cancel_pending)

    def test_cancelled_peta_next_restores_manual_and_stops_automatic(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                instance = Mock()
                instance.busy_action = 'auto_enqueue' if auto else 'manual_peta_next'
                instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT if auto else gui.AUTO_ENQUEUE_IDLE
                instance.peta_next_cancel_pending = True
                gui.BookMinerGui._handle_auto_enqueue_line(instance, '[PetaNextCancelled]')
                self.assertFalse(instance.peta_next_cancel_pending)
                if auto:
                    instance._abort_auto_enqueue.assert_called_once()
                else:
                    self.assertIsNone(instance.busy_action)
                    instance._update_buttons.assert_called_once()

    def test_rejected_peta_command_does_not_leave_gui_busy(self):
        for action in ('manual_peta_next', 'manual_peta_unsolved', 'manual_peta_shock',
                       'auto_peta_shock', 'auto_enqueue'):
            for error in ('Error : peta command is running. wait for peta command completion.',
                          'Error : peta book is loading. wait for peta read completion.'):
                with self.subTest(action=action, error=error):
                    instance = Mock()
                    instance.settings_apply_pending = False
                    instance.busy_action = action
                    instance.auto_enqueue_state = gui.AUTO_ENQUEUE_NEXT if action == 'auto_enqueue' else gui.AUTO_ENQUEUE_IDLE
                    instance.peta_shock_cancel_pending = True
                    instance.peta_next_cancel_pending = True
                    instance.peta_unsolved_cancel_pending = True
                    gui.BookMinerGui._handle_auto_enqueue_line(instance, error)
                    if action == 'auto_enqueue':
                        instance._abort_auto_enqueue.assert_called_once()
                    else:
                        self.assertIsNone(instance.busy_action)
                        self.assertFalse(instance.peta_shock_cancel_pending)
                        self.assertFalse(instance.peta_next_cancel_pending)
                        self.assertFalse(instance.peta_unsolved_cancel_pending)
                        instance._update_buttons.assert_called_once()

    def test_mining_status_is_one_line(self):
        instance = Mock()
        instance.latest_mining_positions = None
        instance.book_positions_text = '定跡局面数 12,345,678'
        instance.peta_positions_text = 'peta定跡局面数 12,000,000'
        instance.engine_status_text = 'エンジン起動数 4/4'
        instance.enqueue_status_text = 'enqueue 1/100'
        gui.BookMinerGui._update_mining_status(instance)
        text = instance.mining_status.set.call_args.args[0]
        self.assertNotIn('\n', text)
        for item in (instance.book_positions_text, instance.peta_positions_text,
                     instance.engine_status_text, instance.enqueue_status_text, '現在の採掘速度'):
            self.assertIn(item, text)

    def test_auto_enqueue_stops_when_backend_cannot_enqueue(self):
        instance = Mock()
        instance.settings_apply_pending = False
        instance.busy_action = 'auto_enqueue'
        instance.auto_enqueue_state = gui.AUTO_ENQUEUE_ENQUEUE
        gui.BookMinerGui._handle_auto_enqueue_line(
            instance, '(1) put position commands Error : no search engines are available')
        instance._abort_auto_enqueue.assert_called_once_with(
            'auto enqueue stopped: BookMiner could not enqueue the positions.')

    def test_automatic_peta_read_messages_go_to_peta_log(self):
        instance = Mock()
        instance.peta_makebook_active = False
        instance.reading_peta_book = False
        instance.busy_action = 'auto_peta_shock'
        self.assertEqual(gui.BookMinerGui._classify_log_line(instance, '[BookReadDone] 100/100'), 'peta')
        self.assertEqual(gui.BookMinerGui._classify_log_line(instance, '[PetaBookPositions] positions=100'), 'peta')
        instance.busy_action = None
        self.assertEqual(gui.BookMinerGui._classify_log_line(instance, '[BookReadDone] 100/100'), 'other')

    def test_out_of_order_mining_progress_preserves_cumulative_counts(self):
        instance = Mock()
        instance.latest_mining_positions = 100
        instance.latest_mining_searched_positions = 50
        instance.latest_mining_nodes = 5000
        instance.mining_samples = [object()]
        instance._parse_optional_int_field.side_effect = lambda fields, key: int(fields[key]) if key in fields else None
        gui.BookMinerGui._handle_mining_progress_line(
            instance, '[MiningProgress] positions=99 searched_positions=49 nodes=4900')
        self.assertEqual(instance.book_positions_text, '定跡局面数 100')
        self.assertEqual(instance.latest_mining_searched_positions, 50)
        self.assertEqual(instance.latest_mining_nodes, 5000)
        self.assertEqual(len(instance.mining_samples), 1)

    def test_stale_job_events_do_not_clear_or_resurrect_tasks(self):
        instance = Mock()
        instance.task_job_items = {}
        instance.completed_task_jobs = set()
        instance.task_queue_total_highwater = 3
        instance.task_queue_taken_highwater = 1
        instance.task_queue_remaining = 2
        instance._parse_task_job_remaining.side_effect = lambda value, taken, total: int(value) if value else total - taken
        instance._parse_task_job_deferred.side_effect = lambda value: int(value) if value else None
        instance._parse_task_job_eval_limit.side_effect = lambda value: int(value) if value else None
        gui.BookMinerGui._handle_task_job_list_line(
            instance, '[TaskQueueStart] 1/3 job=2 job_progress=0/2 job_remaining=2 remaining=2')
        self.assertIn(2, instance.task_job_items)
        gui.BookMinerGui._handle_task_job_list_line(
            instance, '[TaskQueueProgress] 2/3 job=2 job_progress=1/2 job_remaining=1 deferred=3')
        gui.BookMinerGui._handle_task_job_list_line(
            instance, '[TaskQueueProgress] 1/3 job=2 job_progress=0/2 job_remaining=2 deferred=1')
        self.assertEqual(instance.task_job_items[2].taken, 1)
        self.assertEqual(instance.task_job_items[2].remaining, 1)
        self.assertEqual(instance.task_job_items[2].deferred, 3)
        gui.BookMinerGui._handle_task_job_list_line(instance, '[TaskQueueDone] 2/2 job=1 remaining=0')
        self.assertIn(2, instance.task_job_items)
        self.assertEqual(instance.task_queue_remaining, 2)
        gui.BookMinerGui._handle_task_job_list_line(instance, '[TaskQueueJobDone] 3/3 job=2 job_remaining=0')
        self.assertNotIn(2, instance.task_job_items)
        gui.BookMinerGui._handle_task_job_list_line(
            instance, '[TaskQueueProgress] 1/3 job=2 job_progress=1/2 job_remaining=1 remaining=2')
        self.assertNotIn(2, instance.task_job_items)

    def test_peta_read_progress_does_not_overwrite_normal_book_count(self):
        instance = Mock()
        instance.book_positions_text = '定跡局面数 12,345,678'
        instance.peta_positions_count = 100
        instance.peta_positions_text = 'peta定跡局面数 100'
        instance.reading_peta_book = False
        gui.BookMinerGui._handle_book_progress_line(instance, 'read peta shocked book , path = book/backup/peta_book.db')
        gui.BookMinerGui._handle_book_progress_line(instance, '[BookReadProgress] 500/1000')
        self.assertEqual(instance.book_positions_text, '定跡局面数 12,345,678')
        self.assertIn('読込中 500/1,000', instance.peta_positions_text)
        gui.BookMinerGui._handle_book_progress_line(instance, '[BookReadDone] 1000/1000')
        gui.BookMinerGui._handle_book_progress_line(instance, '[PetaBookPositions] positions=990')
        self.assertEqual(instance.peta_positions_text, 'peta定跡局面数 990')
        self.assertEqual(instance.book_positions_text, '定跡局面数 12,345,678')
        self.assertFalse(instance.reading_peta_book)

    def test_peta_read_failure_preserves_cpp_count(self):
        instance = Mock()
        instance.peta_positions_count = 123
        instance.peta_positions_text = 'peta定跡局面数 123'
        instance.reading_peta_book = False
        gui.BookMinerGui._handle_book_progress_line(instance, 'read peta shocked book , path = broken.ybb')
        gui.BookMinerGui._handle_book_progress_line(instance, '[BookReadProgress] 10/100')
        gui.BookMinerGui._handle_book_progress_line(instance, 'Exception : peta read failed: bad file')
        self.assertEqual(instance.peta_positions_text, 'peta定跡局面数 123')
        self.assertFalse(instance.reading_peta_book)

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

    def test_auto_enqueue_preserves_default_and_explicit_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            instance = Mock()
            instance.auto_tmp_seen = {}
            with patch.object(gui, 'THINK_SFENS_PATH', source), patch.object(gui, 'AUTO_THINK_SFENS_PATH', target):
                source.write_text('startpos\n')
                self.assertTrue(gui.BookMinerGui._append_auto_think_sfens_tmp(instance, 'first'))
                source.write_text('startpos, eval_limit=100\n')
                self.assertTrue(gui.BookMinerGui._append_auto_think_sfens_tmp(instance, 'second'))
            self.assertEqual(target.read_text().splitlines(), ['startpos', 'startpos, eval_limit=100'])

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
            instance.setting_node_ratio.get.return_value = '0.5'
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
                'consecutive_node_ratio': 0.5,
            })
            instance.send_command.assert_called_once_with('reload-settings')
            instance.game_ply_limit.set.assert_called_once_with('250')
            self.assertTrue(instance.settings_apply_pending)

    def test_applying_unrelated_settings_preserves_pending_auto_peta(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = Mock()
            instance.use_cpp = True
            instance.mining_settings = dict(gui.MINING_SETTING_DEFAULTS)
            instance.auto_backup_count = 2
            instance.auto_peta_pending = True
            instance.ignore_current_backup = False
            instance.backup_in_progress = True
            instance.next_backup_at = object()
            old_next = instance.next_backup_at
            instance.next_backup_interval = 10800
            instance.setting_minutes.get.return_value = '180'
            instance.setting_shock_every.get.return_value = str(instance.mining_settings['auto_peta_shock_every_n_saves'])
            instance.setting_start_path.get.return_value = 'book/peta_start_sfens.txt'
            instance.setting_max_ply.get.return_value = '200'
            instance.setting_threads.get.return_value = '4'
            instance.setting_node_ratio.get.return_value = '0.5'
            instance.is_running.return_value = True
            instance.send_command.return_value = True
            instance.settings_apply_outstanding = 0
            with patch.object(gui, 'BOOK_MINER_SETTINGS_PATH', Path(directory) / 'settings.json5'):
                gui.BookMinerGui.apply_mining_settings(instance)
            self.assertEqual(instance.auto_backup_count, 2)
            self.assertTrue(instance.auto_peta_pending)
            self.assertFalse(instance.ignore_current_backup)
            self.assertIs(instance.next_backup_at, old_next)
            self.assertEqual(instance.next_backup_interval, 10800)

    def test_changing_save_interval_keeps_peta_save_count(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = Mock()
            instance.use_cpp = False
            instance.mining_settings = dict(gui.MINING_SETTING_DEFAULTS)
            instance.auto_backup_count = 2
            instance.auto_peta_pending = True
            instance.ignore_current_backup = False
            instance.backup_in_progress = True
            instance.next_backup_at = object()
            instance.next_backup_interval = 10800
            instance.setting_minutes.get.return_value = '60'
            instance.setting_shock_every.get.return_value = str(instance.mining_settings['auto_peta_shock_every_n_saves'])
            instance.setting_start_path.get.return_value = 'book/peta_start_sfens.txt'
            instance.setting_max_ply.get.return_value = '200'
            instance.setting_threads.get.return_value = '4'
            instance.setting_node_ratio.get.return_value = '0.8'
            instance.is_running.return_value = False
            with patch.object(gui, 'BOOK_MINER_SETTINGS_PATH', Path(directory) / 'settings.json5'):
                gui.BookMinerGui.apply_mining_settings(instance)
            self.assertEqual(instance.auto_backup_count, 2)
            self.assertTrue(instance.auto_peta_pending)
            self.assertFalse(instance.ignore_current_backup)
            self.assertIsNone(instance.next_backup_at)
            self.assertIsNone(instance.next_backup_interval)

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
