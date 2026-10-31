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
