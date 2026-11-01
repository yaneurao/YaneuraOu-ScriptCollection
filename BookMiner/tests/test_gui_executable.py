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
