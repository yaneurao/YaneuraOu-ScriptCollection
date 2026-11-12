"""Validate the V994 tuning domain and the selected policy defaults."""
from pathlib import Path
import unittest

import test_v993_ranges
import tune


class V994RangeTests(test_v993_ranges.V993RangeTests):
    tune_filename = 'YaneuraOuV994.tune'
    has_lmr_history_switch = False
    has_lmr_slope_parameter = False
    range_overrides = {
        'Search_fail_low_quiet_bonus_2': (98, 216),
        'Search_quiet_stat_score_main': (0, 2252),
        'Search_quiet_stat_score_continuation0': (0, 1126),
        'Search_quiet_stat_score_continuation1': (0, 1093),
        'history_nonPV_bonus_1': (128, 512),
    }

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


    def test_eval_difference_products_fit_int32(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        ranges = tune.collect_parameter_ranges(tune.read_tune_file(str(path)))
        self.assertEqual(ranges['correction_history_initial_1'], (-32, 32))
        lower, _ = ranges['Search_static_evaluation_1a_1Q8']
        _, upper = ranges['Search_static_evaluation_1a_2Q8']
        _, offset = ranges['Search_static_evaluation_1a_3Q8']
        magnitude = max(abs(lower), upper + offset)
        for name in ('Search_static_evaluation_1a_4Q8', 'Search_static_evaluation_2a_1Q8'):
            self.assertLessEqual(magnitude * ranges[name][1], 2**31 - 1)


    def test_history_int32_products_at_all_independent_range_extremes(self):
        """Bound every caller, not only the fitted/default parameter point."""
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        blocks = tune.read_tune_file(str(path))
        ranges = tune.collect_parameter_ranges(blocks)
        for block in blocks:
            context = tune.get_context_block(block)
            _, values, names = tune.parse_content_block(
                context, context.params[0] if context.params else '')
            for value, name in zip(values, names):
                number = float(value)
                ranges.setdefault(name, (min(0, 2 * number), max(0, 2 * number)))
        lo = lambda name: int(ranges[name][0])
        hi = lambda name: int(ranges[name][1])
        magnitude = lambda name: max(abs(lo(name)), abs(hi(name)))
        int_max = 2**31 - 1
        def product(*factors):
            result = 1
            for factor in factors:
                self.assertGreaterEqual(factor, 0)
                result *= factor
                self.assertLessEqual(result, int_max, (factors, result))
            return result

        # At entry depth <= MAX_PLY-1; hindsight and singular search can
        # each increment it once. All following changes only reduce it.
        max_depth = 247
        main_limit, continuation_limit, capture_limit = 7183, 30000, 10692
        quiet_numerator = (hi('Search_quiet_stat_score_main') * main_limit
                           + (hi('Search_quiet_stat_score_continuation0')
                              + hi('Search_quiet_stat_score_continuation1')) * continuation_limit)
        self.assertLessEqual(quiet_numerator, int_max)
        quiet_stat = quiet_numerator // 1024
        # Also covers noncapturing promotions (captureHistory only).
        prior_quiet_stat = max(quiet_stat, capture_limit)
        capture_numerator = product(hi('Search_Decrease_reduction_for_PvNodes_8_1'), 32001)
        stat_score = max(quiet_stat, capture_numerator // 128 + capture_limit)

        # fail-low updates: actual min/max expressions are part of the
        # algorithm, with independently bounded SPSA coefficients.
        f = lambda i: 'Search_fail_low_quiet_bonus_' + str(i)
        self.assertGreaterEqual(lo(f(11)) - hi(f(12)), 0)
        scale = max(0, hi(f(1)) + prior_quiet_stat // lo(f(2))
                    + hi(f(4)) + hi(f(5)) + hi(f(7)) + hi(f(9)))
        product(hi(f(3)), max_depth)
        product(hi(f(11)), max_depth)
        scaled = product(scale, hi(f(13)))
        fail_low_cont = product(scaled, hi(f(14))) // 16384
        product(scaled, hi(f(15)))
        product(scaled, hi(f(16)))

        # Best-move/malus updates, including negative statScore. Each
        # SearchedList holds at most 32 moves, so their sum is <= 64.
        a = lambda i: 'update_all_stats_1c_' + str(i)
        base_bonus = max(hi(a(3)) + hi(a(4)) + stat_score // 32,
                         hi(a(2)) + stat_score // 32)
        searched = 64
        nonpv = product(base_bonus, searched) // lo('history_nonPV_bonus_1')
        best_bonus = base_bonus + nonpv
        self.assertLessEqual(best_bonus, int_max)
        product(hi(a(1)), max_depth)
        product(hi(a(5)), max_depth)
        malus = max(hi(a(7)), hi(a(6)))
        actual_malus = product(malus, 1113) // 1024
        product(actual_malus, 977)
        best_quiet = product(best_bonus, 806) // 1024
        product(best_bonus, 1286)
        product(malus, 1559)
        extra_penalty = product(malus, 616) // 1024

        # All update_quiet_histories callers, including the depth-scaled
        # TT bonus, then all continuation-history callers.
        tt_quiet = product(hi('Search_tt_lookup1_1'), max_depth)
        quiet_bonus = max(best_quiet, actual_malus, tt_quiet)
        product(quiet_bonus, hi('update_quiet_histories_1a_1'))
        product(quiet_bonus, hi('update_quiet_histories_2b_1'))
        product(quiet_bonus, hi('update_quiet_histories_2b_2'))
        quiet_cont = product(quiet_bonus, hi('update_quiet_histories_1a_2')) // 1024
        continuation_bonus = max(fail_low_cont, quiet_cont, extra_penalty,
                                 magnitude('Search_tt_lookup2_1'),
                                 hi('Search_Post_LMR_continuation_history_updates_1'))
        max_weight = max(hi('conthist_bonuses_ply' + str(i)) for i in (1, 2, 3, 4, 6))
        # Check both the fixed OFF multiplier and every ON multiplier.
        multipliers = [64] + [hi('CMHC_multipliers_' + str(i)) for i in range(6)]
        for multiplier in multipliers:
            result = product(continuation_bonus, max_weight, multiplier)
            self.assertLessEqual(result // 65536 + hi('CMHC_bonus_1'), int_max)

    def test_history_casts_are_removed_from_v994_tuning_contexts(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        for block in tune.read_tune_file(str(path)):
            context = tune.get_context_block(block)
            if context.params and context.params[0] in (
                    'Search_fail_low_quiet_bonus', 'CMHC_bonus', 'history_nonPV_bonus'):
                self.assertNotIn('i64(', ''.join(context.content))

    def test_stand_pat_blend_is_bounded_and_half_weight_preserves_old_rounding(self):
        path = Path(__file__).resolve().parents[1] / 'param' / self.tune_filename
        ranges = tune.collect_parameter_ranges(tune.read_tune_file(str(path)))
        self.assertEqual(ranges['QSearch_stand_pat_blend_weight'], (0, 1024))
        self.assertEqual(ranges['QSearch_stand_pat_storage_store'], (0, 1))
        def divide(n, d):
            return (1 if n >= 0 else -1) * (abs(n) // d)
        values = (-32001, -32000, -3, -2, -1, 0, 1, 2, 3, 32000, 32001)
        for best in values:
            for beta in values:
                old = divide(best + beta, 2)
                self.assertEqual(divide(512 * best + 512 * beta, 1024), old)
                for weight in (0, 441, 512, 1024):
                    numerator = weight * best + (1024 - weight) * beta
                    self.assertLessEqual(abs(numerator), 2**31 - 1)



if __name__ == '__main__':
    unittest.main()
