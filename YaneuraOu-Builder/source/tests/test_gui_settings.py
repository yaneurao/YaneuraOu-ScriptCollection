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

    def test_shared_path_aliases_are_expanded_only_for_build(self):
        gui = self.gui
        root = Path(self.tmp.name) / 'checkout'
        source = Path(self.tmp.name) / 'engine source'
        gui.path_alias_rows[0][1].set(str(root))
        gui._add_path_alias('YaneuraOuSource', str(source))
        gui.run_root_var.set('[YOSC]/runs')
        gui.release_source_var.set('[YaneuraOuSource]')
        gui.spsa_mode_var.set('apply')
        gui.spsa_tune_py_var.set('[YOSC]\\SPSA\\tune.py')
        gui.spsa_param_lib_var.set('[YOSC]/SPSA/ParamLib.py')
        gui.spsa_tune_file_var.set('[YOSC]/SPSA/param/example.tune')
        gui.spsa_params_file_var.set('[YOSC]/SPSA/param/example.params')
        run = gui._write_scripts(show_message=False)
        self.assertIsNotNone(run)
        self.assertEqual(run.parent, root / 'runs')
        import json
        recipe = json.loads((run / 'recipe.json').read_text(encoding='utf8'))
        self.assertEqual(recipe['source_dirs']['win'], str(source))
        self.assertEqual(recipe['spsa']['tune_py'], str(root / 'SPSA' / 'tune.py'))
        scripts = list((run / 'scripts').iterdir())
        self.assertTrue(scripts)
        self.assertIn(str(root / 'SPSA' / 'tune.py'), scripts[0].read_text(encoding='utf8'))
        settings, _ = app.load_gui_settings()
        self.assertEqual(settings['presets'][gui.current_preset_name]['spsa']['tune_py'], '[YOSC]\\SPSA\\tune.py')
        self.assertEqual(settings['presets'][gui.current_preset_name]['run_root'], '[YOSC]/runs')
        self.assertIn({'name': 'YaneuraOuSource', 'path': str(source)}, settings['path_aliases'])

    def test_open_latest_build_folder(self):
        gui = self.gui
        self.assertEqual(str(gui.open_build_button['state']), 'disabled')
        gui.run_root_var.set(str(Path(self.tmp.name) / 'runs'))
        run = gui._write_scripts(show_message=False)
        self.assertIsNotNone(run)
        self.assertTrue((run / 'build').is_dir())
        self.assertEqual(str(gui.open_build_button['state']), 'normal')
        gui._set_run_active(True, 'msys2')
        self.assertEqual(str(gui.open_build_button['state']), 'normal')
        gui._set_run_active(False)
        with patch.object(app.sys, 'platform', 'win32'), \
             patch.object(app.os, 'startfile', create=True) as startfile:
            gui._open_build_folder()
            startfile.assert_called_once_with(str(run / 'build'))
        with patch.object(app.sys, 'platform', 'darwin'), \
             patch.object(app.subprocess, 'Popen') as popen:
            gui._open_build_folder()
            popen.assert_called_once_with(['open', str(run / 'build')])
        (run / 'build').rmdir()
        with patch.object(app.messagebox, 'showerror') as error:
            gui._open_build_folder()
            error.assert_called_once()

    def test_invalid_path_alias_is_reported_before_writing(self):
        gui = self.gui
        gui.release_source_var.set('[Missing]/source')
        with patch.object(app.messagebox, 'showerror') as error:
            self.assertIsNone(gui._write_scripts(show_message=False))
        self.assertIn('[Missing]', error.call_args.args[1])
        self.assertEqual(gui.plan, [])

    def test_path_alias_windows_paths_and_validation(self):
        aliases = {'YOSC': r'C:\Users\user\work\YOSC', 'YaneuraOuSource': r'D:\engine\source'}
        self.assertEqual(app._expand_alias_path(r'[YOSC]\SPSA\tune.py', aliases),
                         r'C:\Users\user\work\YOSC\SPSA\tune.py')
        self.assertEqual(app._expand_alias_path('[YaneuraOuSource]/file', aliases), r'D:\engine\source\file')
        self.assertEqual(app._expand_alias_path('[YOSC]/file', {'YOSC': '/'}), '/file')
        for path, mapping in [('[Unknown]/file', aliases), ('[YOSC]suffix', aliases), ('[YOSC]/file', {'YOSC': 'relative'})]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                app._expand_alias_path(path, mapping)

    def test_preset_list_copy_field_and_persist_across_switches(self):
        gui = self.gui
        self.assertEqual(tuple(gui.preset_list.get(0, tk.END)), gui.preset_values)
        gui.release_source_var.set('source path one')
        gui._copy_preset_field(gui.release_source_var)
        index = gui.preset_values.index('yo-material')
        gui.preset_list.selection_clear(0, tk.END)
        gui.preset_list.selection_set(index)
        gui._on_preset_list_select(None)
        self.assertEqual(gui.current_preset_name, 'yo-material')
        self.assertEqual(gui.preset_list.curselection(), (index,))
        self.assertNotEqual(gui.release_source_var.get(), 'source path one')
        gui._paste_preset_field(gui.preset_list, gui.release_source_var)
        gui.preset_var.set('release-all')
        gui._select_preset()
        gui.preset_var.set('yo-material')
        gui._select_preset()
        self.assertEqual(gui.release_source_var.get(), 'source path one')
        self.assertEqual(gui.copied_fields[str(gui.release_source_var)][1], 'release-all')

    def test_invalid_edit_restores_preset_list_selection(self):
        gui = self.gui
        gui.release_common_flags_var.set('"unterminated')
        gui.preset_list.selection_clear(0, tk.END)
        gui.preset_list.selection_set(gui.preset_values.index('yo-material'))
        with patch.object(app.messagebox, 'showerror') as error:
            gui._on_preset_list_select(None)
        error.assert_called_once()
        self.assertEqual(gui.current_preset_name, 'release-all')
        self.assertEqual(gui.preset_list.curselection(), (gui.preset_values.index('release-all'),))

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
                         ['Recipe', 'Logs', 'ShogiZone', 'Paths'])
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
