import sys
import unittest
import tempfile
import csv
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grid_search import float_tag, trial_from_directory, summarize_trial, write_summary


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

    def test_summary_uses_each_logged_lr_and_warmup_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp) / 'network_lr0.0007_val1_warmup1e-05_warmupfiles4'
            trial_dir.mkdir()
            summary_line = ('epoch = {}, steps = 8, train loss avr = 1, 2, 3, 4, '
                            'test loss = 1, 2, 3, 4, test accuracy = 0.1, 0.2, '
                            'test entropy = 0.3, 0.4\n')
            (trial_dir / 'train-0001.log').write_text(
                'lr=0.0007\nlr warmup start=1e-05 end=0.0001825 updates=8\n'
                'lr_scheduler lr=1e-05\n' + summary_line.format(1), encoding='utf-8')
            (trial_dir / 'train-0002.log').write_text(
                'lr=0.0007\nlr_scheduler lr=0.00042\n' + summary_line.format(2), encoding='utf-8')
            (trial_dir / 'train-0003.log').write_text(summary_line.format(3), encoding='utf-8')
            (trial_dir / 'train-0004.log').write_text(
                'lr=0.0007\nlr warmup start=1e-05 end=0.0001825 updates=1\n'
                'lr_scheduler lr=0.0001825\n' + summary_line.format(4), encoding='utf-8')
            trial = trial_from_directory(trial_dir)
            rows = summarize_trial(SimpleNamespace(train_dir=None), trial, 1)
            self.assertEqual([(r['lr_start'], r['lr_end']) for r in rows],
                             [('1e-05', '0.0001825'), ('0.00042', '0.00042'), ('', ''),
                              ('0.0001825', '0.0001825')])
            csv_path = Path(tmp) / 'grid_summary.csv'
            write_summary(csv_path, rows)
            with csv_path.open(encoding='utf-8', newline='') as stream:
                saved = list(csv.DictReader(stream))
            self.assertFalse({'lr', 'lr_min', 'lr_warmup'} & saved[0].keys())
            self.assertEqual(saved[0]['lr_start'], '0.0000100000')
            self.assertEqual(saved[0]['lr_end'], '0.0001825000')

    def test_summary_keeps_tiny_and_close_learning_rates_distinct(self):
        rows = [{'lr': '0.001', 'lr_start': '1e-12', 'lr_end': '0.001'},
                {'lr': '0.001000000000001', 'lr_start': '', 'lr_end': '0.001000000000001'},
                {'lr': '0.001', 'lr_start': '', 'lr_end': 'nan'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'summary.csv'
            write_summary(path, rows)
            with path.open(encoding='utf-8', newline='') as stream:
                saved = list(csv.DictReader(stream))
        self.assertNotEqual(saved[0]['lr_end'], saved[1]['lr_end'])
        self.assertNotEqual(float(saved[0]['lr_start']), 0.0)
        self.assertEqual(len(saved[0]['lr_end'].split('.')[1]), len(saved[0]['lr_start'].split('.')[1]))
        self.assertEqual(saved[1]['lr_start'], '')
        self.assertEqual(saved[2]['lr_end'], 'nan')


if __name__ == '__main__':
    unittest.main()
