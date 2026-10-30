import importlib.util
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'peta_shock-gui.py'
spec = importlib.util.spec_from_file_location('peta_shock_gui', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PetaShockThreadTests(unittest.TestCase):
    def test_engine_command_contains_requested_threads(self):
        with patch.object(module, 'stream_process') as process:
            for threads in (1, 8):
                module.run_engine_peta_shock(engine_path=Path('/tmp/engine'), book_dir=Path('/tmp'),
                                             source_name='source.db', output_name='peta.db',
                                             threads=threads, log=lambda _: None)
                command = process.call_args.kwargs['input_lines'][-2]
                self.assertEqual(command, 'makebook peta_shock source.db peta.db' +
                                 (' threads 8' if threads == 8 else ''))
        for value in (0, -1, True, 1.5, 0x100000000):
            with self.subTest(value=value), self.assertRaises(module.PetaShockError):
                module.run_engine_peta_shock(engine_path=Path('/tmp/engine'), book_dir=Path('/tmp'),
                                             source_name='source.db', output_name='peta.db',
                                             threads=value, log=lambda _: None)

    def test_threads_forwarded_for_both_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / 'engine'
            converter = root / 'converter.py'
            engine.touch()
            converter.touch()
            for suffix in ('.db', '.ybb'):
                source = root / ('source' + suffix)
                source.write_bytes(b'book')
                def fake_engine(**kwargs):
                    (kwargs['book_dir'] / kwargs['output_name']).write_bytes(b'book')
                with self.subTest(suffix=suffix), \
                     patch.object(module, 'run_engine_peta_shock', side_effect=fake_engine) as run, \
                     patch.object(module, 'run_db_to_ybb_converter'):
                    module.run_peta_shock(input_path=source, output_path=root / 'out.ybb',
                                          engine_path=engine, converter_path=converter,
                                          log=lambda _: None, threads=4)
                    self.assertEqual(run.call_args.kwargs['threads'], 4)

    def test_gui_restores_and_validates_threads(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, 'GUI_SETTINGS_PATH', Path(directory) / 'settings.pickle'):
            try:
                gui = module.PetaShockGui()
            except tk.TclError as exc:
                self.skipTest(f'Tk display unavailable: {exc}')
            gui.withdraw()
            try:
                self.assertEqual(gui.threads_var.get(), '1')
                gui.input_var.set('input.db')
                gui.output_var.set('output.ybb')
                for value in ('0', '-1', '1.5', '4294967296', ''):
                    gui.threads_var.set(value)
                    with self.subTest(value=value), patch.object(module.messagebox, 'showerror') as error:
                        gui._start_conversion()
                        error.assert_called_once()
                        self.assertIsNone(gui.worker)
                gui.threads_var.set('8')
                gui._save_settings()
                self.assertEqual(module.load_gui_settings()['threads'], '8')
                with patch.object(module, 'run_peta_shock') as run, \
                     patch.object(module.threading, 'Thread') as thread:
                    thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
                    gui._start_conversion()
                    self.assertEqual(run.call_args.kwargs['threads'], 8)
            finally:
                gui.destroy()


if __name__ == '__main__':
    unittest.main()
