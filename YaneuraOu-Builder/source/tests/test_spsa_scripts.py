from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.script_writer import _release_source_setup, _write_spsa_build_script


class SpsaScriptTest(unittest.TestCase):
    def test_release_uses_selected_params_instead_of_stale_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').mkdir()
            (root / 'build').mkdir()
            (root / 'build' / 'example.params').write_text('stale')
            (root / 'example.tune').write_text('tune')
            (root / 'selected.params').write_text('selected')
            (root / 'ParamLib.py').write_text('')
            (root / 'tune.py').write_text(
                'SPSA_VALIDATION_VERSION = 1\n'
                'SPSA_PARAMS_PATH_VERSION = 1\n'
                'import pathlib, sys\n'
                'path = pathlib.Path(sys.argv[4])\n'
                'assert path.read_text() == "selected"\n'
            )
            recipe = {'spsa': {'mode': 'apply', 'tune_py': str(root / 'tune.py'),
                               'param_lib': str(root / 'ParamLib.py'),
                               'tune_file': str(root / 'example.tune'),
                               'params_file': str(root / 'selected.params')}}
            script = '\n'.join(['set -eu', f'SOURCE_DIR={str(root / "source")!r}',
                                f'PYTHON_CMD={sys.executable!r}'] + _release_source_setup(recipe))
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(root / 'selected.params'), result.stdout)
            self.assertEqual((root / 'build' / 'selected.params').read_text(), 'selected')
            self.assertEqual((root / 'build' / 'example.params').read_text(), 'stale')
            self.assertEqual((root / 'selected.params').read_text(), 'selected')

    def test_validation_marker_accepts_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'source').mkdir()
            (root/'tune.py').write_bytes(b'SPSA_VALIDATION_VERSION = 1\r\nSPSA_PARAMS_PATH_VERSION = 1\r\n')
            (root/'ParamLib.py').write_text('');(root/'x.tune').write_text('');(root/'x.params').write_text('')
            recipe={'spsa':{'mode':'apply','tune_py':str(root/'tune.py'),'param_lib':str(root/'ParamLib.py'),'tune_file':str(root/'x.tune'),'params_file':str(root/'x.params')}}
            script='\n'.join(['set -eu',f'SOURCE_DIR={str(root / "source")!r}',f'PYTHON_CMD={sys.executable!r}']+_release_source_setup(recipe))
            result=subprocess.run(['bash','-c',script],cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_old_tune_script_fails_before_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'source').mkdir(); (root/'old.py').write_text('print("old")\n')
            recipe={'spsa':{'mode':'apply','tune_py':str(root/'old.py'),'param_lib':'p','tune_file':'t','params_file':'p'}}
            script='\n'.join(['set -eu', f'SOURCE_DIR={str(root / "source")!r}', f'PYTHON_CMD={sys.executable!r}'] + _release_source_setup(recipe))
            result=subprocess.run(['bash','-c',script],cwd=root,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('tune.py',result.stderr)
            self.assertFalse((root/'build').exists())

    def test_single_spsa_script_copies_to_expected_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {'work_dir': 'work', 'tune_file': 'example.tune', 'source_dir': 'source',
                   'tune_py': 'tune.py', 'param_lib': 'ParamLib.py', 'params_file': 'other.params',
                   'output_path': 'out', 'tune_mode': 'apply', 'edition': 'MATERIAL', 'command': 'true'}
            _write_spsa_build_script({'name': 'test'}, job, root)
            script = (root / 'test.sh').read_text()
            self.assertIn('cp "$PARAMS_FILE" "$WORK_DIR/$PARAMS_FILE_NAME"', script)

    def test_old_params_interface_fails_before_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'source').mkdir()
            (root / 'old.py').write_text('SPSA_VALIDATION_VERSION = 1\n')
            recipe = {'spsa': {'mode': 'apply', 'tune_py': str(root / 'old.py'),
                               'param_lib': 'p', 'tune_file': 't', 'params_file': 'p'}}
            script = '\n'.join(['set -eu', f'SOURCE_DIR={str(root / "source")!r}',
                                f'PYTHON_CMD={sys.executable!r}'] + _release_source_setup(recipe))
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('params', result.stderr)
            self.assertFalse((root / 'build').exists())

    def test_actual_tune_and_apply_use_selected_filename(self):
        spsa_dir = Path(__file__).resolve().parents[3] / 'SPSA'
        for mode in ('tune', 'apply'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / 'source').mkdir()
                (root / 'source' / 'x.cpp').write_text('int x = 6;\n')
                (root / 'example.tune').write_text('#set file x.cpp\n#context X\nint x = 6@;\n')
                (root / 'selected file.params').write_text('X_1,int,8,0,20,1,0.002\n')
                (root / 'example.params').write_text('X_1,int,3,0,20,1,0.002\n')
                recipe = {'spsa': {'mode': mode, 'tune_py': str(spsa_dir / 'tune.py'),
                                   'param_lib': str(spsa_dir / 'ParamLib.py'),
                                   'tune_file': str(root / 'example.tune'),
                                   'params_file': str(root / 'selected file.params')}}
                script = '\n'.join(['set -eu', f'SOURCE_DIR={str(root / "source")!r}',
                                    f'PYTHON_CMD={sys.executable!r}'] + _release_source_setup(recipe))
                result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn('params_file : selected file.params', result.stdout)
                self.assertEqual((root / 'selected file.params').read_text(), 'X_1,int,8,0,20,1,0.002\n')
                self.assertFalse((root / 'build' / 'example.params').exists())
                self.assertTrue((root / 'build' / 'selected file.params').exists())
                if mode == 'apply':
                    self.assertEqual((root / 'build/source/x.cpp').read_text().strip(), 'int x = 8;')
                else:
                    self.assertIn('X_1', (root / 'build/source/x.cpp').read_text())
                    self.assertIn('8.0', (root / 'build/selected file.params').read_text())

    def test_tune_cli_defaults_to_same_basename(self):
        tune = Path(__file__).resolve().parents[3] / 'SPSA' / 'tune.py'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'x.cpp').write_text('int x = 6;\n')
            (root / 'example.tune').write_text('#set file x.cpp\n#context X\nint x = 6@;\n')
            (root / 'example.params').write_text('X_1,int,8,0,20,1,0.002\n')
            result = subprocess.run([sys.executable, str(tune), 'apply', str(root / 'example.tune'), str(root)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('params_file : ' + str(root / 'example.params'), result.stdout)
            self.assertEqual((root / 'x.cpp').read_text().strip(), 'int x = 8;')

    def test_tune_errors_return_failure(self):
        tune = Path(__file__).resolve().parents[3] / 'SPSA' / 'tune.py'
        result = subprocess.run([sys.executable, str(tune), 'apply', 'missing.tune', 'source'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
