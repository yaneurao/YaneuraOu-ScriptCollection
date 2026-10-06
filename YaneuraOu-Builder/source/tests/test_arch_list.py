from pathlib import Path
import pickle
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.presets import create_preset, load_release_editions
from lib.app import _normalize_editions_for_form
from lib.planner import create_plan


class ArchListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'arch-list.txt'

    def test_order_naming_and_bom(self):
        self.path.write_text('\ufeff# comment\n\n SFNN_halfka2_1024_8_64_k3k3 \nMATERIAL\n', encoding='utf-8')
        self.assertEqual(load_release_editions(self.root), [
            ('YANEURAOU_ENGINE_SFNN_halfka2_1024_8_64_k3k3', 'YaneuraOu_SFNN_halfka2_1024_8_64_k3k3'),
            ('YANEURAOU_ENGINE_MATERIAL', 'YaneuraOu_MATERIAL'),
        ])

    def test_saved_selection_and_new_entries(self):
        self.path.write_text('MATERIAL\nNNUE_HALFKP_256X2_32_32\n')
        rows = _normalize_editions_for_form([
            dict(edition='YANEURAOU_ENGINE_MATERIAL', enabled=False, artifact_prefix='YO-MATERIAL'),
            dict(edition='removed', enabled=True),
        ], self.root)
        self.assertEqual([row['enabled'] for row in rows], [False, True])
        self.assertEqual(rows[0]['artifact_prefix'], 'YaneuraOu_MATERIAL')
        self.path.write_text('SFNN1536\n')
        self.assertEqual(load_release_editions(self.root)[0][0], 'YANEURAOU_ENGINE_SFNN1536')

    def test_invalid(self):
        for value in ('', '# only comment\n', 'NNUE', 'MATERIAL\nmaterial',
                      '../MATERIAL', 'SFNN;echo', 'YANEURAOU_ENGINE_MATERIAL', 'two words'):
            with self.subTest(value=value):
                self.path.write_text(value)
                with self.assertRaises(ValueError):
                    load_release_editions(self.root)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            load_release_editions(self.root)

    def test_pickle_then_arch_list_changed(self):
        self.path.write_text('MATERIAL\nSFNN1536\n')
        recipe = create_preset('release-all', self.root)
        recipe['editions'][0]['enabled'] = False
        saved = pickle.dumps(recipe)
        self.path.write_text('SFNN_halfka2_1024_8_64_progress8\nMATERIAL\n')
        rows = _normalize_editions_for_form(pickle.loads(saved)['editions'], self.root)
        self.assertEqual([row['edition'] for row in rows], [
            'YANEURAOU_ENGINE_SFNN_halfka2_1024_8_64_progress8',
            'YANEURAOU_ENGINE_MATERIAL',
        ])
        self.assertEqual([row['enabled'] for row in rows], [True, False])

    def test_shipped_list_and_plan(self):
        root = Path(__file__).resolve().parents[2]
        recipe = create_preset('release-all', root)
        self.assertEqual(
            [(item['edition'], item['artifact_prefix']) for item in recipe['editions']],
            load_release_editions(root),
        )
        self.assertTrue(all(item['enabled'] for item in recipe['editions']))
        self.assertTrue(create_plan(recipe))
        self.assertEqual(create_preset('yo-material', root)['output_path'], '../bin/YaneuraOu_MATERIAL.exe')


if __name__ == '__main__':
    unittest.main()
