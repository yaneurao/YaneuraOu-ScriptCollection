import importlib.util
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'convert_ybb_db-gui.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('convert_ybb_db_gui', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConverterGuiTests(unittest.TestCase):
    def test_direction_updates_and_worker_uses_initial_options(self):
        try:
            gui = module.BookConverterGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        gui.withdraw()
        try:
            gui.input_var.set('input.db')
            self.assertEqual(gui.direction_var.get(), '.db -> .ybb')
            gui.input_var.set('input.ybb')
            self.assertEqual(gui.direction_var.get(), '.ybb -> .db')
            gui.input_var.set('input.db')
            gui.output_var.set('output.ybb')
            gui.include_depth_var.set(True)
            gui.keep_temp_var.set(False)
            with patch.object(module, 'run_conversion', return_value=Path('output.ybb')) as convert, \
                 patch.object(module.threading, 'Thread') as thread:
                gui._start_conversion()
                gui.include_depth_var.set(False)
                gui.keep_temp_var.set(True)
                thread.call_args.kwargs['target']()
                self.assertTrue(convert.call_args.kwargs['include_depth'])
                self.assertFalse(convert.call_args.kwargs['keep_temp'])
        finally:
            gui.destroy()


if __name__ == '__main__':
    unittest.main()
