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
    tune_filename = 'YaneuraOuV993.tune'
    has_lmr_history_switch = True
    has_lmr_slope_parameter = True
    range_overrides = {}

    def test_search_invariants_have_explicit_ranges(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path), tune.parse_tune_file(str(path)))
        ranges = tune.collect_parameter_ranges(blocks)
        required = {
            'Search_capture_futility_gain_enable': (0, 1),
            'QSearch_capture_futility_gain_enable': (0, 1),
            'Search_negative_capture_SEE_enable': (0, 1),
            'aspiration_window_1': (3, 10),
            'aspiration_window_2': (1, 18000),
            'aspiration_window_growth_1': (43, 94),
            'Search_futility_1_5': (1, 361200),
            'Search_Extensions1_3': (1, 110),
            'Search_Extensions2_1': (1, 421180),
            'Search_Decrease_reduction_for_PvNodes_3_2': (64, 51200),
            'Search_fail_low_quiet_bonus_2': (1, 216),
            'MovePicker_good_capture_see_1': (1, 36),
            'Search_nullmove_2_1': (2, 14),
            'Search_lmr_divisor_slope': (0, 14),
            'Search_TT_child_probe_depth': (1, 32),
            'Search_probcut_depth_improving': (2, 6),
            'Search_probcut_depth_other': (2, 6),
            'Search_quiet_stat_score_main': (0, 4504),
            'Search_quiet_stat_score_continuation0': (0, 2252),
            'Search_quiet_stat_score_continuation1': (0, 2186),
            'Search_All_node_reduction_scale_1': (0, 552),
            'Search_Continuation_history_based_pruning3_1': (-50, 0),
            'Time_best_move_effort_span': (1, 100000),
            'Time_best_move_effort_minimumQ10': (0, 1024),
            'Search_lmr_divisor_base': (16, 6000),
            'Search_nullmove_2_margin': (1, 512),
            'history_nonPV_bonus_1': (1, 512),
            'root_score_EMA_ratio_numerator': (1, 6),
            'root_score_EMA_ratio_denominator': (1, 4),
            'main_history_decay_1': (0, 1024),
            'main_history_initial_1': (-256, 256),
            'Search_futility_blend_1': (0, 1024),
        }
        if not self.has_lmr_slope_parameter:
            del required['Search_lmr_divisor_slope']
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
            if name == 'Search_LMR_history_divisor' and not self.has_lmr_history_switch:
                continue
            required[name + '_enable'] = (0, 1)
        required.update(self.range_overrides)
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


    def test_aspiration_width_range_guarantees_progress(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        ranges = tune.collect_parameter_ranges(tune.read_tune_file(str(path)))
        minimum_width = ranges['aspiration_window_1'][0]
        minimum_growth = ranges['aspiration_window_growth_1'][0]
        # A zero increment would retry the same failed window indefinitely.
        self.assertGreaterEqual(int(minimum_width * minimum_growth) // 128, 1)

    def test_lmr_int_arithmetic_at_domain_extremes(self):
        import math
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path))
        ranges = tune.collect_parameter_ranges(blocks)
        for block in blocks:
            context = tune.get_context_block(block)
            _, numbers, names = tune.parse_content_block(context, context.params[0] if context.params else '')
            for number, name in zip(numbers, names):
                value = float(number)
                ranges.setdefault(name, (min(0, 2 * value), max(0, 2 * value)))
        # Standard search: MAX_PLY=246, MAX_MOVES=600; history saturation
        # limits: main=7183, continuation=30000, pawn=8192, capture=10692.
        hi = lambda name: ranges[name][1]
        lo = lambda name: ranges[name][0]
        int_max = (1 << 31) - 1
        quiet_numerator = (hi('Search_quiet_stat_score_main') * 7183
                           + (hi('Search_quiet_stat_score_continuation0')
                              + hi('Search_quiet_stat_score_continuation1')) * 30000)
        self.assertLess(quiet_numerator, int_max)
        capture_score = hi('Search_Decrease_reduction_for_PvNodes_8_1') * 32001 // 128 + 10692
        stat_score = max(capture_score, quiet_numerator // 1024)
        stat_product = stat_score * hi('Search_Decrease_reduction_for_PvNodes_9_1')
        self.assertLess(stat_product, int_max)
        cntcv_max = 1024 * (2 * hi('correction_value_4') + hi('correction_value_6ply'))
        fallback_max = (hi('correction_value_fallback') if 'correction_value_fallback' in ranges
                        else 8 * hi('correction_value_4'))
        correction_max = (1024 * (hi('correction_value_1') + hi('correction_value_2')
                                   + 2 * hi('correction_value_3'))
                          + max(cntcv_max, fallback_max))
        self.assertLess(correction_max, int_max)
        table_max = math.ceil(hi('YaneuraOuWorker_clear3_1') / 128 * math.log(600))
        scale = table_max ** 2
        reduction = (scale + scale * hi('YaneuraOuWorker_reduction_2') // 512
                     + hi('YaneuraOuWorker_reduction_3'))
        self.assertLess(scale * hi('YaneuraOuWorker_reduction_2'), int_max)
        # Sum absolute bounds, even for branches which cannot occur together.
        r_bound = (reduction + hi('Search_PV_reduction_1')
                   + hi('Search_decrease_reduction_for_tt_pv_1')
                   + sum(hi('Search_Decrease_reduction_for_PvNodes_1_' + str(i)) for i in range(1, 6))
                   + hi('Search_Decrease_reduction_for_PvNodes_2_1')
                   + 600 * hi('Search_Decrease_reduction_for_PvNodes_3_1')
                   + correction_max // lo('Search_Decrease_reduction_for_PvNodes_3_2')
                   + sum(hi('Search_Decrease_reduction_for_PvNodes_4_' + str(i)) for i in (1, 2))
                   + hi('Search_Decrease_reduction_for_PvNodes_5_1')
                   + sum(hi('Search_Decrease_reduction_for_PvNodes_6a_' + str(i)) for i in (1, 2, 3))
                   + hi('Search_Decrease_reduction_for_PvNodes_7_1')
                   + stat_product // 4096
                   + hi('Search_LMR_eval_margin_scale')
                     * max(abs(lo('Search_LMR_eval_margin_min')), hi('Search_LMR_eval_margin_max')))
        self.assertLess(r_bound * hi('Search_All_node_reduction_scale_1'), int_max)
        history_max = 2 * 30000 + 8192 + hi('Search_Continuation_history_based_pruning1_2') * 7183 // 32
        lmr_depth_max = 244 + hi('Search_PV_reduction_1') // 1024 + history_max // lo('Search_lmr_divisor_base')
        see_magnitude = abs(lo('Search_Continuation_history_based_pruning3_1')) * lmr_depth_max ** 2
        self.assertLess(see_magnitude + 32001, int_max)

    def test_probcut_stored_depth_is_representable(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        ranges = tune.collect_parameter_ranges(tune.read_tune_file(str(path)))
        # ProbCut starts at depth=3 and stores probCutDepth + 1.
        # TT depth8 encodes depth - DEPTH_NONE, with DEPTH_NONE=-3;
        # zero is reserved for an empty entry.
        for name in ('Search_probcut_depth_improving', 'Search_probcut_depth_other'):
            self.assertGreater(3 - ranges[name][1] + 1, -3)

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
