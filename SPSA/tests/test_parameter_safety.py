import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tune
import BloodgateSPSA
from ParamLib import Entry, round_parameter_int


class ParameterSafetyTests(unittest.TestCase):
    def test_rounding_negative_and_positive_integers(self):
        for value, expected in ((-5.5, -6), (-5.2, -5), (-5.0, -5),
                                (-0.5, -1), (0.5, 1), (5.2, 5), (5.5, 6)):
            with self.subTest(value=value):
                self.assertEqual(round_parameter_int(value), expected)

    def test_engine_receives_rounded_negative_value(self):
        match = BloodgateSPSA.ShogiMatch.__new__(BloodgateSPSA.ShogiMatch)
        engine = Mock()
        match.engines = [None, engine]
        param = Entry('test', 'int', -5, -10, 10, 1, 0.1, '', False)
        match.set_engine_options([param], [-5.2])
        engine.send_usi.assert_called_once_with('setoption name test value -5')

    def test_apply_preserves_negative_integer(self):
        param = Entry('test', 'int', -5, -10, 10, 1, 0.1, '', False)
        block = tune.TuneBlock(setblock={'file': 'unused'},
                               context_block=tune.Block(params=['test'], content=['old']))
        with patch.object(tune, 'read_parameters', return_value=[param]), \
             patch.object(tune, 'parse_tune_file', return_value=[]), \
             patch.object(tune, 'read_tune_file', return_value=[block]), \
             patch.object(tune, 'parse_content_block', return_value=(['test'], [], ['test'])), \
             patch.object(tune, 'replace_context') as replace:
            tune.apply_parameters('unused.tune', 'unused.params', 'unused')
        self.assertEqual(replace.call_args.args[2], ['-5'])

    def test_invalid_existing_parameters_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.params'
            path.write_text('broken parameter line\n', encoding='utf-8')
            with patch.object(tune, 'parse_tune_file', return_value=[]), \
                 patch.object(tune, 'read_explicit_params', return_value=[]), \
                 patch.object(tune, 'read_tune_file', return_value=[]):
                with self.assertRaisesRegex(Exception, 'insufficient params'):
                    tune.tune_parameters('unused.tune', str(path), directory)
            self.assertEqual(path.read_text(encoding='utf-8'), 'broken parameter line\n')

    def test_invalid_parameters_fail_before_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.params'
            path.write_text('broken parameter line\n', encoding='utf-8')
            with patch.object(tune, 'read_tune_file', return_value=[]) as read_tune:
                with self.assertRaisesRegex(Exception, 'insufficient params'):
                    tune.apply_parameters('unused.tune', str(path), directory)
                read_tune.assert_not_called()

    def test_global_parameter_copy_requires_refresh(self):
        context = tune.Block(params=['test'], content=['const int k = 5@;'])
        declaration = tune.Block(params=['%%TUNE_DECLARATION%%'], content=['int k = @;'])
        block = tune.TuneBlock(context_block=context, add_blocks=[declaration])
        with self.assertRaisesRegex(ValueError, 'k.*起動時'):
            tune.validate_tune_sync([block])
        block.add_blocks.append(tune.Block(params=['%%TUNE_ISREADY%%'], content=['k = @;']))
        tune.validate_tune_sync([block])
        block.add_blocks = [tune.Block(params=['%%TUNE_DECLARATION%%'], content=['int& k = @;'])]
        tune.validate_tune_sync([block])

    def test_apply_rejects_unapplied_explicit_option(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'x.tune').write_text('#param FV_SCALE int 40 16 64 1 0.002\n')
            (root/'x.params').write_text('FV_SCALE,int,28,16,64,1,0.002\n')
            with self.assertRaisesRegex(ValueError, 'FV_SCALE.*実行時'):
                tune.apply_parameters(str(root/'x.tune'), str(root/'x.params'), d)

    def test_rejects_duplicate_and_nonfinite_parameters(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'x.params'
            path.write_text('X,int,1,0,10,1,0.1\nx,int,2,0,10,1,0.1\n')
            with self.assertRaisesRegex(ValueError, '重複'):
                tune.read_parameters(str(path))
            path.write_text('X,int,nan,0,10,1,0.1\n')
            with self.assertRaisesRegex(ValueError, '有限数'):
                tune.read_parameters(str(path))

    def test_apply_rejects_unused_active_parameter_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'x.tune').write_text('')
            (root/'x.params').write_text('Extra,int,2,0,10,1,0.1\n')
            with self.assertRaisesRegex(ValueError, 'Extra'):
                tune.apply_parameters(str(root/'x.tune'), str(root/'x.params'), d)
            (root/'x.params').write_text('Extra,int,2,0,10,1,0.1 [[NOT USED]]\n')
            tune.apply_parameters(str(root/'x.tune'), str(root/'x.params'), d)

    def test_missing_parameters_can_be_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'new.params'
            with patch.object(tune, 'parse_tune_file', return_value=[]), \
                 patch.object(tune, 'read_explicit_params', return_value=[]), \
                 patch.object(tune, 'read_tune_file', return_value=[]):
                tune.tune_parameters('unused.tune', str(path), directory)
            self.assertEqual(path.read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()
