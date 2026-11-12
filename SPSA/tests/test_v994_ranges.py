"""Validate the V994 tuning domain and the selected policy defaults."""
from pathlib import Path
import unittest

import test_v993_ranges
import tune


class V994RangeTests(test_v993_ranges.V993RangeTests):
    tune_filename = 'YaneuraOuV994.tune'
    has_lmr_history_switch = False
    has_lmr_slope_parameter = False

    def test_selected_policies_default_to_off_and_remain_tunable(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path))
        ranges = tune.collect_parameter_ranges(blocks)
        defaults = {}
        for block in blocks:
            context = tune.get_context_block(block)
            if not context.params:
                continue
            _, values, names = tune.parse_content_block(context, context.params[0])
            defaults.update(zip(names, map(float, values)))
        for name in ('Search_TT_LMR_exclusive_enable',
                     'Search_CMHC_feedback_enable'):
            self.assertEqual(defaults[name], 0)
            self.assertEqual(ranges[name], (0, 1))


    def test_lmr_divisor_only_exposes_the_base(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path))
        names = []
        for block in blocks:
            context = tune.get_context_block(block)
            if not context.params:
                continue
            _, _, parameters = tune.parse_content_block(context, context.params[0])
            names.extend(parameters)
            if context.params[0] == 'Search_lmr_divisor':
                self.assertEqual(parameters, ['Search_lmr_divisor_base'])
                self.assertIn('+ 7 * (d - 8) * (d - 8)', ''.join(context.content))
        self.assertNotIn('Search_LMR_history_divisor_enable', names)
        self.assertNotIn('Search_lmr_divisor_slope', names)


    def test_correction_weights_and_fallback_have_explicit_ranges(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        ranges = tune.collect_parameter_ranges(tune.read_tune_file(str(path)))
        expected = {'correction_value_1': (0, 27612),
                    'correction_value_2': (0, 19024),
                    'correction_value_3': (0, 23230),
                    'correction_value_4': (0, 15770),
                    'correction_value_6ply': (0, 12614),
                    'correction_value_fallback': (0, 161390)}
        for name, bounds in expected.items():
            self.assertEqual(ranges[name], bounds)


    def test_time_effort_domain_and_direct_main_history_initialization(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path))
        ranges = tune.collect_parameter_ranges(blocks)
        self.assertEqual(ranges['Time_best_move_effort_depth'], (0, 32))
        self.assertEqual(ranges['Time_best_move_effort_span'], (1, 100000))
        self.assertEqual(ranges['main_history_initial_1'], (-256, 256))
        context = next(tune.get_context_block(b) for b in blocks
                       if tune.get_context_block(b).params == ['main_history_initial'])
        self.assertIn('mainHistory.fill(-5@{-256,256});', ''.join(context.content))
        self.assertNotIn('mainHistoryDefault', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
