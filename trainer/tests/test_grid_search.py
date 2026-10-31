import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grid_search import float_tag, trial_from_directory


class GridDirectoryTests(unittest.TestCase):
    def test_close_values_have_distinct_round_trippable_tags(self):
        values = [0.001, 0.001000000000001, 0.12345678901231, 0.12345678901234]
        tags = [float_tag(value) for value in values]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual([float(tag) for tag in tags], values)
        self.assertEqual(tags[0], '0.001')
        for value, tag in zip(values, tags):
            trial = trial_from_directory(Path(f'network_lr{tag}_val0.5'))
            self.assertIsNotNone(trial)
            self.assertEqual(trial.lr, value)

    def test_malformed_optional_numeric_tags_are_ignored(self):
        self.assertIsNone(trial_from_directory(Path('network_lr0.1_val0.5_warmupbad')))
        self.assertIsNone(trial_from_directory(Path('network_lr0.1_val0.5_clipbad')))


if __name__ == '__main__':
    unittest.main()
