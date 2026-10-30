import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.script_writer import _write_bookminer_cpp_scripts


class BookMinerBuildPathsTest(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'fixture executes a POSIX shell script')
    def test_multiple_cpu_artifacts_and_intermediates(self):
        with tempfile.TemporaryDirectory(prefix='builder paths ') as directory:
            root = Path(directory)
            run = root / 'run folder'
            scripts = run / 'scripts'
            scripts.mkdir(parents=True)
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            make = fake_bin / 'make'
            make.write_text('''#!/bin/sh
for arg do
  case "$arg" in
    TARGET=*) target=${arg#TARGET=} ;;
    BUILD_DIR=*) objects=${arg#BUILD_DIR=} ;;
    clean) clean=yes ;;
  esac
done
if [ "${clean:-}" = yes ]; then
  rm -rf "$objects" "$target"
else
  mkdir -p "$objects" "$(dirname "$target")"
  printf object > "$objects/test.o"
  printf executable > "$target"
fi
''', encoding='utf8')
            make.chmod(0o755)
            compiler = fake_bin / 'fake-cxx'
            compiler.write_text('#!/bin/sh\nexit 0\n', encoding='utf8')
            compiler.chmod(0o755)
            plan = [dict(script_group='bookminer', platform='win64',
                         bookminer_cpp_source_dir=str(root / 'source folder'), source_dir=str(root / 'yaneura source'),
                         cpu=cpu, artifact=f'BookMinerCpp-{cpu}.exe', compiler='fake-cxx', jobs=2)
                    for cpu in ('AVX2', 'SSE42')]
            _write_bookminer_cpp_scripts({}, plan, scripts)
            env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ.get('PATH', ''))
            subprocess.run(['bash', str(scripts / 'bookminer')], env=env, check=True, capture_output=True, text=True)
            for cpu in ('AVX2', 'SSE42'):
                self.assertEqual((run / 'build' / f'BookMinerCpp-{cpu}.exe').read_text(), 'executable')
                self.assertEqual((run / 'build-intermediate' / 'bookminer-cpp' / cpu / 'test.o').read_text(), 'object')
            self.assertFalse((run / 'artifacts').exists())
            self.assertFalse((run / 'build' / 'bookminer-cpp').exists())


if __name__ == '__main__':
    unittest.main()
