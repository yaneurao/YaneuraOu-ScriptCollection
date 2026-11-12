"""Keep the V993 tuning domain within the search formulas' required bounds."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tune
from ParamLib import Entry


class V993RangeTests(unittest.TestCase):
    def test_search_invariants_have_explicit_ranges(self):
        path = Path(__file__).resolve().parents[1] / 'param' / 'YaneuraOuV993.tune'
        blocks = tune.read_tune_file(str(path), tune.parse_tune_file(str(path)))
        ranges = tune.collect_parameter_ranges(blocks)
        required = {
            'Search_lmr_divisor_base': (1, 6000),
            'Search_nullmove_2_margin': (1, 512),
            'history_nonPV_bonus_1': (1, 512),
            'root_score_EMA_ratio_numerator': (1, 6),
            'root_score_EMA_ratio_denominator': (1, 4),
            'main_history_decay_1': (0, 1024),
            'Search_futility_blend_1': (0, 1024),
        }
        for name, bounds in required.items():
            with self.subTest(parameter=name):
                self.assertEqual(ranges.get(name), bounds)
                for value in bounds:
                    tune.validate_parameter_range(
                        Entry(name, 'int', value, *bounds, 1, 0.002, '', False), bounds)
                for value in (bounds[0] - 1, bounds[1] + 1):
                    with self.assertRaises(ValueError):
                        tune.validate_parameter_range(
                            Entry(name, 'int', value, *bounds, 1, 0.002, '', False), bounds)


if __name__ == '__main__':
    unittest.main()
