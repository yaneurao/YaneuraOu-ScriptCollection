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
                'import pathlib, sys\n'
                'path = pathlib.Path(sys.argv[2]).with_suffix(".params")\n'
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
            self.assertEqual((root / 'build' / 'example.params').read_text(), 'selected')

    def test_single_spsa_script_copies_to_expected_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {'work_dir': 'work', 'tune_file': 'example.tune', 'source_dir': 'source',
                   'tune_py': 'tune.py', 'param_lib': 'ParamLib.py', 'params_file': 'other.params',
                   'output_path': 'out', 'tune_mode': 'apply', 'edition': 'MATERIAL', 'command': 'true'}
            _write_spsa_build_script({'name': 'test'}, job, root)
            script = (root / 'test.sh').read_text()
            self.assertIn('cp "$PARAMS_FILE" "$WORK_DIR/${TUNE_FILE_NAME%.*}.params"', script)

    def test_tune_errors_return_failure(self):
        tune = Path(__file__).resolve().parents[3] / 'SPSA' / 'tune.py'
        result = subprocess.run([sys.executable, str(tune), 'apply', 'missing.tune', 'source'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
