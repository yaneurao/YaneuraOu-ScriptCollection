"""Keep the V993 tuning domain within the search formulas' required bounds."""
import sys
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tune
from ParamLib import Entry, round_parameter_int


class V993RangeTests(unittest.TestCase):
    def test_search_invariants_have_explicit_ranges(self):
        path = Path(__file__).resolve().parents[1] / 'param' / 'YaneuraOuV993.tune'
        blocks = tune.read_tune_file(str(path), tune.parse_tune_file(str(path)))
        ranges = tune.collect_parameter_ranges(blocks)
        required = {
            'Search_capture_futility_gain_enable': (0, 1),
            'QSearch_capture_futility_gain_enable': (0, 1),
            'Search_negative_capture_SEE_enable': (0, 1),
            'aspiration_window_1': (1, 10),
            'aspiration_window_2': (1, 18000),
            'Search_futility_1_5': (1, 361200),
            'Search_Extensions1_3': (1, 110),
            'Search_Extensions2_1': (1, 421180),
            'Search_Decrease_reduction_for_PvNodes_3_2': (1, 51200),
            'Search_fail_low_quiet_bonus_2': (1, 216),
            'MovePicker_good_capture_see_1': (1, 36),
            'Search_nullmove_2_1': (2, 14),
            'Search_lmr_divisor_slope': (0, 14),
            'Time_best_move_effort_span': (1, 100000),
            'Time_best_move_effort_minimumQ10': (0, 1024),
            'Search_lmr_divisor_base': (1, 6000),
            'Search_nullmove_2_margin': (1, 512),
            'history_nonPV_bonus_1': (1, 512),
            'root_score_EMA_ratio_numerator': (1, 6),
            'root_score_EMA_ratio_denominator': (1, 4),
            'main_history_decay_1': (0, 1024),
            'main_history_initial_1': (-256, 256),
            'Search_futility_blend_1': (0, 1024),
        }
        for name in (
            'Search_razoring_policy',
            'Search_child_futility_policy',
            'Search_fail_high_recovery',
            'Search_previous_pv_protection',
            'Search_seek_mate',
            'Search_NMP_dynamic_reduction',
            'Search_NMP_odd_thread',
            'Search_LMR_history_divisor',
            'Search_quiet_futility_formula',
            'Search_TT_bound_penalty',
            'Search_TT_LMR_exclusive',
            'Search_nonPV_history_scaling',
            'Search_root_score_EMA',
            'Search_CMHC_feedback',
            'Time_best_move_effort_policy',
        ):
            required[name + '_enable'] = (0, 1)
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


    def test_binary_generation_compares_both_integer_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'x.cpp'
            source.write_text('if (1) work();\n', encoding='utf-8')
            config = root / 'x.tune'
            config.write_text('#set file x.cpp\n#context Switch\nif (1@enable{0,1}) work();\n', encoding='utf-8')
            params = root / 'x.params'
            with patch.object(tune, 'target_dir', directory, create=True):
                tune.tune_parameters(str(config), str(params), directory)
            entry = tune.read_parameters(str(params))[0]
            self.assertEqual((entry.type, entry.min, entry.max, entry.step), ('int', 0, 1, 1))
            for value in (0, 0.1, 0.49, 0.5, 0.9, 1):
                states = {round_parameter_int(min(entry.max, max(entry.min, value + sign * entry.step)))
                          for sign in (-1, 1)}
                self.assertEqual(states, {0, 1})


if __name__ == '__main__':
    unittest.main()
