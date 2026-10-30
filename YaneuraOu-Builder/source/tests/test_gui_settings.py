from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import app


class GuiSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = patch.object(app, 'GUI_SETTINGS_PATH', Path(self.tmp.name) / 'settings.pickle')
        self.settings.start()
        self.addCleanup(self.settings.stop)
        try:
            self.gui = app.BuildGui(Path(__file__).resolve().parents[2])
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        self.gui.withdraw()
        self.addCleanup(self.gui.destroy)

    def test_shogizone_settings_persist_outside_recipe(self):
        gui = self.gui
        gui.shogizone_url_var.set('https://zone.example')
        gui.shogizone_password_var.set('secret')
        self.assertTrue(gui._save_gui_settings())
        settings, _ = app.load_gui_settings()
        self.assertEqual(settings['shogizone_url'], 'https://zone.example')
        self.assertEqual(settings['shogizone_password'], 'secret')
        self.assertNotIn('shogizone_password', settings['presets'][gui.current_preset_name])

    def test_connection_test_reports_success_and_failure(self):
        gui = self.gui
        gui.shogizone_url_var.set('https://zone.example')
        gui.shogizone_password_var.set('secret')
        for failure in (None, RuntimeError('unauthorized')):
            with self.subTest(failure=failure), \
                 patch.object(app, 'check_connection', side_effect=failure) as check, \
                 patch.object(gui, 'after', side_effect=lambda _delay, func, *args: func(*args)), \
                 patch.object(app.messagebox, 'showinfo') as info, \
                 patch.object(app.messagebox, 'showerror') as error, \
                 patch.object(app.threading, 'Thread') as thread:
                thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
                gui._test_shogizone_connection()
                check.assert_called_once_with('https://zone.example', 'secret')
                if failure:
                    error.assert_called_once_with('ShogiZone connection test', 'unauthorized')
                    info.assert_not_called()
                else:
                    info.assert_called_once_with('ShogiZone connection test', 'Connection and upload password verified.')
                    error.assert_not_called()
                self.assertFalse(gui.run_active)
        self.assertFalse(hasattr(gui, 'upload_button'))

    def test_shogizone_upload_fields_are_preset_specific(self):
        gui = self.gui
        self.assertEqual([gui.notebook.tab(tab, 'text') for tab in gui.notebook.tabs()],
                         ['Recipe', 'Logs', 'ShogiZone'])
        gui.shogizone_engine_name_var.set('Engine One')
        gui.shogizone_auto_upload_var.set(True)
        gui.preset_var.set('yo-material')
        gui._select_preset()
        self.assertFalse(gui.shogizone_auto_upload_var.get())
        gui.preset_var.set('release-all')
        gui._select_preset()
        self.assertEqual(gui.shogizone_engine_name_var.get(), 'Engine One')
        self.assertTrue(gui.shogizone_auto_upload_var.get())

    def test_upload_occurs_only_after_successful_scripts(self):
        gui = self.gui
        script = Path(self.tmp.name) / 'scripts' / 'build'
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                process = unittest.mock.Mock(stdout=[], wait=unittest.mock.Mock(return_value=exit_code))
                with patch.object(app.subprocess, 'Popen', return_value=process), \
                     patch.object(gui, 'after', side_effect=lambda _delay, func, *args: func(*args)), \
                     patch.object(app, 'collect_artifact_groups', return_value={'name': []}) as collect, \
                     patch.object(gui, '_upload_shogizone_groups') as upload, \
                     patch.object(gui, '_finish_run'):
                    gui._run_script_subprocesses([script], lambda _script: ['test'], cwd=lambda _s: self.tmp.name,
                                                  env={}, completion_title='Build', upload=('https://zone', 'secret', 'Engine'))
                self.assertEqual(collect.called, exit_code == 0)
                self.assertEqual(upload.called, exit_code == 0)

    def test_clone_preserves_selections_and_run_name(self):
        gui = self.gui
        gui.name_var.set('my-run')
        for index, var in enumerate(gui.cpu_vars.values()):
            var.set(index == 0)
        for index, row in enumerate(gui.edition_rows):
            row['enabled'].set(index == 0)
        cpus = [cpu for cpu, var in gui.cpu_vars.items() if var.get()]
        editions = [row['edition'] for row in gui.edition_rows if row['enabled'].get()]
        with patch.object(app, '_ask_wide_string', return_value='copy'):
            gui._clone_preset()
        self.assertEqual(gui.name_var.get(), 'my-run')
        self.assertEqual([cpu for cpu, var in gui.cpu_vars.items() if var.get()], cpus)
        self.assertEqual([row['edition'] for row in gui.edition_rows if row['enabled'].get()], editions)
        gui.preset_var.set('release-all')
        gui._select_preset()
        self.assertEqual([cpu for cpu, var in gui.cpu_vars.items() if var.get()], cpus)
        self.assertEqual([row['edition'] for row in gui.edition_rows if row['enabled'].get()], editions)

    def test_cpu_selection_survives_platform_changes_and_reload(self):
        gui = self.gui
        for index, var in enumerate(gui.cpu_vars.values()):
            var.set(index == 0)
        selected = [cpu for cpu, var in gui.cpu_vars.items() if var.get()]
        gui.release_platform_var.set('macOS')
        gui._on_release_platform_changed()
        gui._set_all_cpus(False)
        gui.release_platform_var.set('Windows x64')
        gui._on_release_platform_changed()
        self.assertEqual([cpu for cpu, var in gui.cpu_vars.items() if var.get()], selected)
        self.assertTrue(gui._save_current_preset_from_form())
        gui._load_recipe_into_form()
        gui.release_platform_var.set('macOS')
        gui._on_release_platform_changed()
        self.assertFalse(any(var.get() for var in gui.cpu_vars.values()))

    def test_source_folder_survives_platform_changes(self):
        gui = self.gui
        gui.release_source_var.set('/windows-source')
        gui.release_platform_var.set('macOS')
        gui._on_release_platform_changed()
        gui.release_source_var.set('/mac-source')
        gui.release_platform_var.set('Windows x64')
        gui._on_release_platform_changed()
        self.assertEqual(gui.release_source_var.get(), '/windows-source')
        gui._save_current_preset_from_form()
        gui._load_recipe_into_form()
        gui.release_platform_var.set('macOS')
        gui._on_release_platform_changed()
        self.assertEqual(gui.release_source_var.get(), '/mac-source')

    def test_empty_plan_reports_error_and_clears_previous_plan(self):
        gui = self.gui
        gui._set_all_editions(False)
        gui.plan = [{'old': True}]
        with patch.object(app.messagebox, 'showerror') as showerror:
            gui._create_plan_from_form()
        self.assertEqual(gui.plan, [])
        showerror.assert_called_once()

    def test_single_preset_selects_only_its_edition(self):
        gui = self.gui
        gui.preset_var.set('yo-material')
        gui._select_preset()
        self.assertEqual([row['edition'] for row in gui.edition_rows if row['enabled'].get()],
                         ['YANEURAOU_ENGINE_MATERIAL'])
        recipe = gui._release_recipe_from_form()
        self.assertEqual(recipe['material_level'], 9)

    def test_tabs_and_bookminer_visibility(self):
        gui = self.gui
        self.assertEqual([gui.settings_tabs.tab(tab, 'text') for tab in gui.settings_tabs.tabs()],
                         ['Release settings', 'Variants / CPU targets', 'Editions'])
        gui.settings_tabs.select(gui.edition_box)
        gui.preset_var.set('bookminer-cpp')
        gui._select_preset()
        self.assertEqual(gui.settings_tabs.tab(gui.edition_box, 'state'), 'hidden')
        self.assertFalse(gui.variant_box.winfo_manager())
        self.assertEqual(gui.settings_tabs.tab(gui.targets_tab, 'state'), 'normal')
        gui.preset_var.set('release-all')
        gui._select_preset()
        self.assertEqual(gui.settings_tabs.tab(gui.edition_box, 'state'), 'normal')
        self.assertEqual(gui.variant_box.winfo_manager(), 'grid')

    def test_edition_columns_align(self):
        gui = self.gui
        gui.deiconify()
        gui.settings_tabs.select(gui.edition_box)
        gui.update_idletasks()
        labels = [w for w in gui.edition_scroller.inner.winfo_children()
                  if isinstance(w, app.ttk.Label) and int(w.grid_info()['column']) == 2]
        self.assertGreater(len(labels), 1)
        self.assertEqual(len({w.winfo_x() for w in labels}), 1)


if __name__ == '__main__':
    unittest.main()
