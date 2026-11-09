from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.app import _resolve_recipe_paths
from lib.planner import create_plan
from lib.presets import create_preset
from lib.script_writer import _release_source_setup, _write_single_build_script


class PgoTest(unittest.TestCase):
    def recipe(self):
        root = Path(__file__).resolve().parents[2]
        recipe = create_preset('release-all', root)
        recipe['platforms'] = ['win64']
        recipe['cpus'] = {'win64': ['AVX2']}
        recipe['variants'] = [{'name': 'Git', 'extra_cppflags': []}]
        recipe['editions'] = [
            {'edition': 'YANEURAOU_ENGINE_SFNN1536', 'artifact_prefix': 'YaneuraOu_A'},
            {'edition': 'YANEURAOU_ENGINE_NNUE', 'artifact_prefix': 'YaneuraOu_B'},
        ]
        return recipe

    def test_default_target_remains_tournament(self):
        for job in create_plan(self.recipe()):
            self.assertEqual(job['target'], 'tournament')
            self.assertNotIn('PGO_EVAL_DIR', job['command'])

    def test_multiple_architectures_expand_one_template(self):
        recipe = self.recipe()
        recipe['pgo'] = {'enabled': True, 'eval_dir': "C:/models/model's $USER/[arch]", 'bench': '1024 1 8 default depth'}
        # 古いedition別指定は使用しない。
        recipe['editions'][0]['pgo_eval_dir'] = '/obsolete/override'
        jobs = create_plan(recipe)
        self.assertEqual([j['target'] for j in jobs], ['pgo', 'pgo'])
        self.assertIn("PGO_EVAL_DIR=C:/models/model's $USER/SFNN1536", shlex.split(jobs[0]['command']))
        self.assertIn("PGO_EVAL_DIR=C:/models/model's $USER/NNUE", shlex.split(jobs[1]['command']))
        self.assertIn('PGO_BENCH=1024 1 8 default depth', shlex.split(jobs[0]['command']))

    def test_default_template_is_relative_to_original_repository(self):
        recipe = self.recipe()
        recipe['source_dirs'] = {'win': 'C:\\original\\YaneuraOu\\source\\'}
        recipe['pgo'] = {'enabled': True}
        jobs = create_plan(recipe)
        self.assertEqual(jobs[0]['pgo_eval_dir'], 'C:/original/YaneuraOu/eval/SFNN1536')
        self.assertEqual(jobs[1]['pgo_eval_dir'], 'C:/original/YaneuraOu/eval/NNUE')

    def test_template_and_path_alias_expand_without_changing_saved_recipe(self):
        recipe = self.recipe()
        recipe['pgo'] = {'enabled': True, 'eval_dir': '[YOSC]/eval/[arch]'}
        resolved = _resolve_recipe_paths(recipe, {'YOSC': '/tmp/yosc'})
        self.assertEqual(create_plan(resolved)[0]['pgo_eval_dir'], '/tmp/yosc/eval/SFNN1536')
        self.assertEqual(recipe['pgo']['eval_dir'], '[YOSC]/eval/[arch]')
        recipe['pgo']['eval_dir'] = '[arch]/eval'
        recipe['source_dirs'] = {'win': '/original/source'}
        self.assertEqual(create_plan(_resolve_recipe_paths(recipe, {}))[0]['pgo_eval_dir'], '/original/SFNN1536/eval')

    def test_missing_model_and_unrunnable_cross_build_fail_before_script_generation(self):
        recipe = self.recipe()
        recipe['pgo'] = {'enabled': True, 'eval_dir': ''}
        with self.assertRaisesRegex(ValueError, 'PGO eval folder'):
            create_plan(recipe)
        recipe['pgo']['eval_dir'] = '/models'
        recipe['platforms'] = ['winarm']
        recipe['cpus'] = {'winarm': ['ARMV8']}
        with self.assertRaisesRegex(ValueError, 'cross builds'):
            create_plan(recipe)

    def test_material_does_not_require_a_model(self):
        recipe = self.recipe()
        recipe['pgo'] = {'enabled': True}
        recipe['editions'] = [{'edition': 'YANEURAOU_ENGINE_MATERIAL', 'artifact_prefix': 'YaneuraOu_MATERIAL'}]
        self.assertEqual(create_plan(recipe)[0]['target'], 'pgo')

    def test_disabled_pgo_does_not_resolve_unused_model_macros(self):
        recipe = self.recipe()
        recipe['pgo'] = {'enabled': False, 'eval_dir': '[NOT_REGISTERED]/model'}
        recipe['editions'][0]['pgo_eval_dir'] = '[NOT_REGISTERED]/other'
        self.assertEqual(create_plan(_resolve_recipe_paths(recipe, {}))[0]['target'], 'tournament')

    def test_source_copy_includes_the_pgo_helper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'engine/source').mkdir(parents=True)
            helper = root / 'engine/tools/build/pgo.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('# pgo helper\n')
            recipe = {'pgo': {'enabled': True}}
            script = '\n'.join(['set -eu', f'SOURCE_DIR={shlex.quote(str(root / "engine/source"))}']
                               + _release_source_setup(recipe))
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'build/tools/build/pgo.py').read_text(), helper.read_text())

    def test_single_script_also_copies_helper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = dict(source_dir='/engine/source', work_dir='/build', output_path='/out',
                       edition='YANEURAOU_ENGINE_MATERIAL', command='true', pgo=True)
            _write_single_build_script({'name': 'pgo'}, job, root)
            text = (root / 'pgo.sh').read_text()
            self.assertIn('"$SOURCE_DIR/../tools/build/pgo.py"', text)
            self.assertIn('"$WORK_DIR"/tools/build/pgo.py', text)


if __name__ == '__main__':
    unittest.main()
