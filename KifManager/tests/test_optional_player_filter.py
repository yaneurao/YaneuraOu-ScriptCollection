import importlib.util
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kif_manager_test', ROOT / 'kif-manager.py')
manager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manager
spec.loader.exec_module(manager)


class OptionalPlayerFilterTest(unittest.TestCase):
    def test_unsolved_and_other_extract_unnamed_and_named_games(self):
        try:
            root_window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        root_window.withdraw()
        self.addCleanup(root_window.destroy)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            records = root / 'records'
            records.mkdir()
            (records / 'unnamed.csa').write_text('V2.2\nPI\n+\n+7776FU\n-3334FU\n%TORYO\n')
            (records / 'named.csa').write_text('V2.2\nN+Alice\nN-Bob\nPI\n+\n+2726FU\n-3334FU\n%TORYO\n')
            (records / 'unnamed.kif').write_text('手合割：平手\n手数----指手---------消費時間--\n   1 ７六歩(77)\n   2 ８四歩(83)\n', encoding='utf-8')
            player_list = root / 'players.txt'
            player_list.write_text('Alice\n')
            for kind in (item for item in manager.EXTRACTORS if item.key in ('unsolved', 'other')):
                with self.subTest(tab=kind.key):
                    pane = manager.ExtractorPane(root_window, kind)
                    self.addCleanup(pane.destroy)
                    pane.input_dir.set(str(records))
                    pane.output_path.set(str(root / (kind.key + '.txt')))
                    self.assertEqual(pane.both_player_list.get(), '')
                    self.assertEqual(pane.either_player_list.get(), '')
                    job = pane.build_job(False, False)
                    self.assertIsNone(job.both_player_list)
                    self.assertIsNone(job.either_player_list)
                    stats = manager.run_extractor(job.input_dir, job.output_path,
                                                  job.both_player_list, job.either_player_list,
                                                  source_kind=manager.extractor_source_kind(kind.key),
                                                  allow_non_startpos=True, exclude_handicap=job.exclude_handicap)
                    self.assertEqual(stats.selected, 3)
                    self.assertEqual(stats.skipped_parse, 0)
                    self.assertEqual(set(job.output_path.read_text().splitlines()),
                                     {'startpos moves 7g7f 3c3d', 'startpos moves 2g2f 3c3d',
                                      'startpos moves 7g7f 8c8d'})
                    pane.either_player_list.set(str(player_list))
                    filtered = pane.build_job(False, False)
                    stats = manager.run_extractor(filtered.input_dir, filtered.output_path,
                                                  filtered.both_player_list, filtered.either_player_list,
                                                  allow_non_startpos=True, exclude_handicap=filtered.exclude_handicap)
                    self.assertEqual(stats.selected, 1)
                    self.assertEqual(filtered.output_path.read_text().splitlines(), ['startpos moves 2g2f 3c3d'])


if __name__ == '__main__':
    unittest.main()
