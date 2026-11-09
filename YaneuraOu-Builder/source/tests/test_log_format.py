import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.log_format import log_level


class LogFormatTests(unittest.TestCase):
    def test_significant_lines(self):
        for line, expected in [('[SPSA] 開始: apply', 'stage'), ('[SPSA] 成功: apply', 'success'),
                               ('[BUILD] 成功: engine.exe', 'success'),
                               ('[PGO] START: SFNN / AVX512VNNI / all', 'stage'),
                               ('[PGO] DONE: engine.exe', 'success'),
                               ('[PGO] ERROR: missing runtime', 'error'),
                               ('[PGO] make -B tournament', None), ('[run] exit code 0: a.sh', 'success'),
                               ('[run] exit code 2: a.sh', 'error'), ('file.cpp:4:2: error: bad', 'error'),
                               ('file.cpp:4:2: warning: note', 'warning'),
                               ('[ERROR] ビルドまたは前処理に失敗しました', 'error'),
                               ('completed without error', None)]:
            with self.subTest(line=line):
                self.assertEqual(log_level(line), expected)
