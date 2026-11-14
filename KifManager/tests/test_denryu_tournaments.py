import importlib.util
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kif_manager_denryu_test', ROOT / 'kif-manager.py')
manager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manager
spec.loader.exec_module(manager)
import denryu_kif_downloader_core as denryu


class DenryuTournamentsTest(unittest.TestCase):
    def test_seventh_production_available_before_and_after_refresh(self):
        expected = 'https://denryu-sen.jp/denryusen/dr7_prd/dr1_live.php'
        fallback = {option.key: option for option in denryu.fallback_denryu_tournament_options()}
        self.assertEqual(fallback['dr7_prd'].live_url, expected)
        # The official index can lag behind the tournament website.
        with patch.object(denryu, 'fetch_bytes', return_value=b'<html></html>'):
            refreshed = denryu.fetch_denryu_tournament_options()
        self.assertEqual(next(x for x in refreshed if x.key == 'dr7_prd').live_url, expected)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        root.withdraw()
        self.addCleanup(root.destroy)
        kind = next(x for x in manager.DOWNLOADERS if x.key == 'denryu')
        pane = manager.DenryuDownloadPane(root, kind)
        self.addCleanup(pane.destroy)
        option = next(x for x in pane.tournament_options if x.key == 'dr7_prd')
        pane.tournament_choice.set(option.display_name)
        pane._on_tournament_selected()
        self.assertEqual(pane.tournament_url.get(), expected)
        self.assertEqual(pane.build_job().source_url, expected)
        pane._set_tournament_options(refreshed, preferred_key='dr7_prd')
        self.assertEqual(pane.tournament_url.get(), expected)
        self.assertIn('dr7_prd', pane.tournament_choice.get())

    def test_official_option_supplies_archive_url(self):
        html = b'<a href="/denryusen/dr7_prd/results.zip">results</a>'
        with patch.object(denryu, 'fetch_bytes', return_value=html):
            refreshed = denryu.fetch_denryu_tournament_options()
        option = next(x for x in refreshed if x.key == 'dr7_prd')
        self.assertEqual(option.live_url, 'https://denryu-sen.jp/denryusen/dr7_prd/dr1_live.php')
        self.assertEqual(option.archive_url, 'https://denryu-sen.jp/denryusen/dr7_prd/results.zip')


if __name__ == '__main__':
    unittest.main()
