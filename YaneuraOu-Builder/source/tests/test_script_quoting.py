from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.script_writer import _quote


class ScriptQuotingTest(unittest.TestCase):
    def test_path_is_literal_and_home_expands(self):
        for value, expected in [('/tmp/a $USER `echo bad`', '/tmp/a $USER `echo bad`'),
                                ('~/a b', '/test-home/a b'),
                                ('C:\\some dir\\source', '/c/some dir/source')]:
            with self.subTest(value=value):
                result = subprocess.run(['bash', '-c', 'HOME=/test-home; printf %s ' + _quote(value)],
                                        check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout, expected)


if __name__ == '__main__':
    unittest.main()
