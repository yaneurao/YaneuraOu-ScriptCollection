import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'The test mock uses a POSIX executable script')
class EngineProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.tmp.name) / 'test-engine'
        subprocess.run(['g++', '-std=c++20', '-pthread', '-I', str(ROOT / 'source'),
                        str(ROOT / 'source' / 'usi_engine.cpp'), str(ROOT / 'tests' / 'usi_engine_test.cpp'),
                        '-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_engine(self, searches):
        engine = Path(self.tmp.name) / 'mock-engine'
        engine.write_text('#!/usr/bin/env python3\n'
                          'import sys\n'
                          f'searches = iter({searches!r})\n'
                          'configured = False\n'
                          'for line in sys.stdin:\n'
                          ' line = line.strip()\n'
                          ' if line == "usi": print("usiok", flush=True)\n'
                          ' elif line == "isready": print("readyok", flush=True)\n'
                          ' elif line.startswith("setoption name MultiPV value "): configured = True\n'
                          ' elif line.startswith("go "):\n'
                          '  assert configured\n'
                          '  for reply in next(searches): print(reply, flush=True)\n'
                          ' elif line == "quit": break\n')
        engine.chmod(0o755)
        return subprocess.run([str(self.exe), str(engine)], capture_output=True, text=True, timeout=10)

    def test_bounds_are_used_and_info_string_is_ignored(self):
        result = self.run_engine([["info depth 5 multipv 1 score cp 100 pv 7g7f",
                                   "info depth 5 multipv 2 score cp 20 pv 2g2f",
                                   "info depth 6 multipv 1 score cp 999 lowerbound pv 7g7f",
                                   "info string score cp 777 pv 3g3f", "bestmove 7g7f"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['7g7f 999', '2g2f 20'])

    def test_missing_exact_score_is_not_silently_accepted(self):
        result = self.run_engine([["info depth 1 nodes 10", "bestmove 7g7f"]])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without a score', result.stderr)

    def test_bound_only_score_is_accepted(self):
        result = self.run_engine([["info score cp -47 upperbound pv 7g7f", "bestmove 7g7f"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['7g7f -47'])

    def test_negative_zero_mate_is_loss(self):
        result = self.run_engine([["info score mate -0 pv 7g7f", "bestmove 7g7f"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['7g7f -32000'])

    def test_resignation_can_follow_a_scored_pv(self):
        result = self.run_engine([["info depth 12 multipv 1 score cp -26 upperbound pv 1a1b 2g2f",
                                   "bestmove resign"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['1a1b -26'])

    def test_ranking_updates_replace_earlier_candidates(self):
        result = self.run_engine([["info multipv 1 score cp 100 pv 7g7f",
                                   "info multipv 2 score cp 20 pv 2g2f",
                                   "info multipv 1 score cp 120 pv 3g3f",
                                   "info multipv 2 score cp 25 pv 7g7f",
                                   "bestmove 3g3f"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['3g3f 120', '7g7f 25'])

    def test_bestmove_must_match_scored_pv(self):
        result = self.run_engine([["info score cp 100 pv 7g7f", "bestmove 2g2f"]])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match', result.stderr)

    def test_incomplete_research_falls_back_to_previous_result(self):
        result = self.run_engine([["info multipv 1 score cp 100 pv 7g7f",
                                   "info multipv 2 score cp 95 pv 2g2f", "bestmove 7g7f"],
                                  ["info multipv 1 score cp 10 pv 3g3f", "bestmove 3g3f"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['7g7f 100', '2g2f 95'])
        self.assertIn('incomplete MultiPV', result.stderr)


if __name__ == '__main__':
    unittest.main()
