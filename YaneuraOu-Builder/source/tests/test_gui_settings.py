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

    def test_upload_run_uses_saved_credentials_and_reports_success(self):
        gui = self.gui
        gui.shogizone_url_var.set('https://zone.example')
        gui.shogizone_password_var.set('secret')
        artifact = Path(self.tmp.name) / 'engine.exe'
        with patch.object(app.filedialog, 'askdirectory', return_value=self.tmp.name), \
             patch.object(app, 'collect_artifacts', return_value=[(artifact, 'x64', 'AVX2')]), \
             patch.object(app, 'upload_artifact', return_value={'id': 'asset'}) as upload, \
             patch.object(gui, 'after', side_effect=lambda _delay, func, *args: func(*args)), \
             patch.object(app.messagebox, 'showinfo') as info, \
             patch.object(app.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            gui._upload_shogizone_run()
        upload.assert_called_once_with('https://zone.example', 'secret', artifact, 'x64', 'AVX2')
        info.assert_called_once_with('ShogiZone upload', 'Upload completed.')
        self.assertFalse(gui.run_active)

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
                         ['Release settings', 'Variants / CPU targets', 'Editions', 'ShogiZone'])
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
