from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.script_writer import _write_release_scripts


class ReleaseArtifactMoveTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('7z'), '7z is required to inspect the release archive')
    def test_each_build_moves_executable_and_archive_has_no_source_binary(self):
        for platform, executable, extension in (
            ('win64', 'YaneuraOu-by-gcc.exe', '.exe'),
            ('win64', 'YaneuraOu-by-gcc', ''),
            ('win32', 'YaneuraOu-by-gcc.exe', '.exe'),
            ('winarm', 'YaneuraOu-by-gcc.exe', '.exe'),
        ):
            with self.subTest(platform=platform, executable=executable), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source = root / 'original source'
                source.mkdir()
                (source / 'x.cpp').write_text('source')
                scripts = root / 'run' / 'scripts'
                scripts.mkdir(parents=True)
                fake_bin = root / 'bin'
                fake_bin.mkdir()
                fake_make = fake_bin / 'make'
                fake_make.write_text('#!/bin/sh\nexit 0\n')
                fake_make.chmod(0o755)
                jobs = []
                for cpu in ('FIRST', 'SECOND'):
                    jobs.append({'script_group': 'release', 'platform': platform, 'variant': 'Git',
                                 'source_dir': str(source), 'eval_dir': 'MATERIAL',
                                 'compiler': 'true', 'edition': 'MATERIAL', 'cpu': cpu,
                                 'source_executable': 'YaneuraOu-by-gcc.exe' if platform == 'winarm' else 'YaneuraOu-by-gcc',
                                 'artifact': 'engine-' + cpu + ('.exe' if platform == 'winarm' else ''),
                                 'command': f"printf {cpu} > {executable}"})
                recipe = {'version': 'V1', 'spsa': {'mode': 'none'},
                          'package': {'enabled': True, 'exclude': ['obj']}}
                with patch("lib.script_writer._winarm_cross_setup", return_value=[]), \
                     patch("lib.script_writer._win32_cross_setup", return_value=["WIN32_CLANG_WRAPPER=true"]):
                    _write_release_scripts(recipe, jobs, scripts)
                env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ['PATH'])
                result = subprocess.run(['bash', str(scripts / 'release')], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                build = root / 'run' / 'build'
                for cpu in ('FIRST', 'SECOND'):
                    self.assertEqual((build / 'MATERIAL' / ('engine-' + cpu + extension)).read_text(), cpu)
                self.assertFalse((build / 'source' / executable).exists())
                archive = next(build.glob('*.7z'))
                listing = subprocess.run(['7z', 'l', '-ba', str(archive)], capture_output=True, text=True, check=True).stdout
                self.assertNotIn('source/' + executable, listing)
                self.assertIn('MATERIAL/engine-FIRST' + extension, listing)
                self.assertIn('MATERIAL/engine-SECOND' + extension, listing)


if __name__ == '__main__':
    unittest.main()
