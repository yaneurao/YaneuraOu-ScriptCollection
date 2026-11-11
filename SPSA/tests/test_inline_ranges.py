import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tune


class InlineRangeTests(unittest.TestCase):
    def test_old_and_new_names_and_ranges(self):
        ranges = {}
        block = tune.Block(content=['x = 6@{2,12} + 7@IIR_1{ -4, 20 } + 8@;'])
        lines, numbers, names = tune.parse_content_block(block, 'Search', ranges)
        self.assertEqual(lines, ['x = Search_1 + Search_IIR_1 + Search_2;'])
        self.assertEqual(numbers, ['6', '7', '8'])
        self.assertEqual(names, ['Search_1', 'Search_IIR_1', 'Search_2'])
        self.assertEqual(ranges, {'Search_1': (2, 12), 'Search_IIR_1': (-4, 20)})
        pattern = tune.make_context_pattern(block.content)
        self.assertIsNotNone(re.fullmatch(pattern, 'x = 9 + 10 + 11;'))

    def test_invalid_ranges_and_conflicts(self):
        for annotation in ('{12,2}', '{2,2}', '{nan,12}', '{2,inf}', '{2}', '{2,3,4}', '{2,12'):
            with self.subTest(annotation=annotation), self.assertRaises(ValueError):
                tune.parse_content_block(tune.Block(content=['6@'+annotation]), 'X', {})
        with self.assertRaisesRegex(ValueError, '一致'):
            tune.parse_content_block(tune.Block(content=['6@a{2,12} + 6@a{3,12}']), 'X', {})

    def run_pipeline(self, root, command, value=None, bounds='{2,12}'):
        source = root/'x.cpp'
        source.write_text('int x = 6;\n')
        config = root/'x.tune'
        config.write_text('#set file x.cpp\n#context X\nint x = 6@'+bounds+';\n')
        params = root/'x.params'
        if value is not None:
            params.write_text(f'X_1,int,{value},0,20,1,0.002\n')
        with patch.object(tune, 'target_dir', str(root), create=True):
            command(str(config), str(params), str(root))
        return source, params

    def test_generate_and_update_override_ranges(self):
        for existing in (None, 5.2):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as d:
                source, params = self.run_pipeline(Path(d), tune.tune_parameters, existing)
                entry = tune.read_parameters(str(params))[0]
                self.assertEqual((entry.min, entry.max), (2, 12))
                self.assertEqual(entry.v, 6 if existing is None else existing)
                self.assertEqual(source.read_text().strip(), 'int x = X_1;')

    def test_tune_out_of_range_preserves_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(Exception, 'X_1.*範囲'):
                self.run_pipeline(root, tune.tune_parameters, 1)
            self.assertEqual((root/'x.cpp').read_text(), 'int x = 6;\n')
            self.assertEqual((root/'x.params').read_text(), 'X_1,int,1,0,20,1,0.002\n')

    def test_apply_checks_all_blocks_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'x.cpp'
            source.write_text('int x = 6;\nint y = 7;\n')
            config = root/'x.tune'
            config.write_text('#set file x.cpp\n#context X\nint x = 6@{2,12};\n'
                              '#context Y\nint y = 7@{2,12};\n')
            params = root/'x.params'
            params.write_text('X_1,int,8,0,20,1,0.002\nY_1,int,1,0,20,1,0.002 [[NOT USED]]\n')
            original = source.read_text()
            with patch.object(tune, 'target_dir', str(root), create=True):
                with self.assertRaisesRegex(ValueError, 'Y_1.*範囲'):
                    tune.apply_parameters(str(config), str(params), str(root))
            self.assertEqual(source.read_text(), original)

    def test_apply_bounds_and_rounding(self):
        for value, success in ((2, True), (12, True), (5.5, True), (1, False), (13, False)):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                if success:
                    source, _ = self.run_pipeline(root, tune.apply_parameters, value)
                    self.assertEqual(source.read_text().strip(), f'int x = {tune.round_parameter_int(value)};')
                else:
                    with self.assertRaisesRegex(ValueError, 'X_1.*範囲'):
                        self.run_pipeline(root, tune.apply_parameters, value)
                    self.assertEqual((root/'x.cpp').read_text(), 'int x = 6;\n')
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, '適用値 2'):
            self.run_pipeline(Path(d), tune.apply_parameters, 2.2, '{2.1,12}')


if __name__ == '__main__':
    unittest.main()
