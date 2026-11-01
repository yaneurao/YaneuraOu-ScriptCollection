import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source'
YANE_SOURCE = ROOT.parents[1] / 'YaneuraOu' / 'source'


@unittest.skipUnless(os.name != 'nt' and shutil.which('make') and shutil.which('g++')
                     and (YANE_SOURCE / 'position.cpp').is_file(),
                     'requires POSIX, make, g++ and sibling YaneuraOu source')
class CppIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='bookminer-cpp-test-')
        cls.root = Path(cls.temp.name)
        build = cls.root / 'build'
        binary = cls.root / 'bookminer'
        subprocess.run(['make', '-j2', f'BUILD_DIR={build}', f'TARGET={binary}'],
                       cwd=SOURCE, check=True, capture_output=True, text=True, timeout=180)
        cls.binary = binary
        objects = [build / 'book_store.o', build / 'config.o', build / 'usi_engine.o',
                   build / 'yaneuraou' / 'yaneuraou_core.lib']
        cls.executables = {}
        for name in ('peta_next', 'peta_settings', 'book_save'):
            exe = cls.root / f'{name}-test'
            subprocess.run(['g++', '-std=c++20', '-O2', '-ffunction-sections', '-fdata-sections',
                            '-I', str(SOURCE), str(ROOT / 'tests' / f'{name}_test.cpp'),
                            *map(str, objects), '-Wl,--gc-sections', '-pthread', '-o', str(exe)],
                           check=True, capture_output=True, text=True, timeout=120)
            cls.executables[name] = exe
        cls.config_exe = cls.root / 'config-test'
        subprocess.run(['g++', '-std=c++20', '-I', str(SOURCE), str(SOURCE / 'config.cpp'),
                        str(ROOT / 'tests' / 'config_test.cpp'), '-o', str(cls.config_exe)],
                       check=True, capture_output=True, text=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_overlapping_roots_and_rejoin(self):
        subprocess.run([str(self.executables['peta_next']), str(self.root / 'roots.txt')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_config_strings_and_threads(self):
        subprocess.run([str(self.config_exe), str(self.root / 'settings.json5')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_peta_shock_uses_configured_engine_and_threads(self):
        subprocess.run([str(self.executables['peta_settings']), str(self.root / 'peta')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_book_save_and_reload(self):
        directory = self.root / 'book-save'
        directory.mkdir()
        subprocess.run([str(self.executables['book_save']), str(directory)],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_p_command_uses_shared_peta_settings(self):
        root = self.root / 'p-command'
        (root / 'book' / 'backup').mkdir(parents=True)
        (root / 'settings').mkdir()
        (root / 'engine directory').mkdir()
        binary = root / 'bookminer'
        shutil.copy2(self.binary, binary)
        search = root / 'search-engine'
        search.write_text('#!/usr/bin/env python3\nimport sys\n'
                          'for line in sys.stdin:\n'
                          ' line = line.strip()\n'
                          ' if line == "usi": print("usiok", flush=True)\n'
                          ' elif line == "isready": print("readyok", flush=True)\n'
                          ' elif line == "quit": break\n')
        search.chmod(0o755)
        material = root / 'engine directory' / 'custom engine'
        material.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                            'commands = sys.stdin.read()\n'
                            'pathlib.Path("peta-commands.txt").write_text(commands)\n'
                            'command = next(line for line in commands.splitlines() if line.startswith("makebook "))\n'
                            '(pathlib.Path("book") / command.split()[3]).write_text("#YANEURAOU-DB2016 1.00\\n")\n')
        material.chmod(0o755)
        (root / 'settings' / 'engine_settings.json5').write_text(
            '[{path: "' + str(search) + '", nodes: 100, multi: 1}]')
        (root / 'settings' / 'book_miner_settings.json5').write_text(
            "{peta_shock_engine_path: 'engine directory/custom engine', peta_shock_threads: 4}")
        (root / 'book' / 'backup' / 'book_miner-20261029000000_1.db').write_text(
            '#YANEURAOU-DB2016 1.00\n'
            'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
            '7g7f none 0 1\n')
        result = subprocess.run([str(binary), '--from_gui'], input='p\n!\n',
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('..peta_shock makebook has done', result.stdout)
        self.assertIn(' threads 4\n', (root / 'peta-commands.txt').read_text())


if __name__ == '__main__':
    unittest.main()
