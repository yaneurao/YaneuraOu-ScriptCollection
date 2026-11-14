import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kif_extractor_common as extractor


class KifVariationsTest(unittest.TestCase):
    def test_mainline_nested_variation_and_sibling_variation(self):
        for separator in ('：', ':'):
            with self.subTest(separator=separator), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source = root / 'game.kif'
                source.write_text(
                    '先手：Alice\n後手：Bob\n手合割：平手\n'
                    '手数----指手---------消費時間--\n'
                    '1 ７六歩(77)\n2 ３四歩(33)\n3 ２六歩(27)\n'
                    f'\n変化{separator}2手\n2 ８四歩(83)\n3 ２六歩(27)\n'
                    f'\n変化{separator}3手\n3 ６六歩(67)\n'
                    f'\n変化{separator}2手\n2 ４四歩(43)\n3 ２六歩(27)\n',
                    encoding='utf-8',
                )
                output = root / 'output.txt'
                stats = extractor.run_extractor(root, output, allow_non_startpos=True)
                self.assertEqual(stats.skipped_parse, 0)
                self.assertEqual(output.read_text().splitlines(), [
                    'startpos moves 7g7f 3c3d 2g2f',
                    'startpos moves 7g7f 8c8d 2g2f',
                    'startpos moves 7g7f 8c8d 6g6f',
                    'startpos moves 7g7f 4c4d 2g2f',
                ])

    def test_variations_inherit_only_parent_evaluations_before_branch(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'game.kif'
            source.write_text(
                '先手：Alice\n後手：Bob\n手合割：平手\n'
                '1 ７六歩(77)\n** 100\n2 ３四歩(33)\n** 200\n'
                '変化：2手\n2 ８四歩(83)\n** -300\n', encoding='utf-8')
            games = extractor.parse_kif(source, include_eval_records=True)
            self.assertEqual([[r.value for r in game.eval_records] for game in games],
                             [[100, 200], [100, -300]])

    def test_invalid_variation_does_not_produce_partial_game(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'game.kif'
            source.write_text('手合割：平手\n1 ７六歩(77)\n変化：3手\n3 ２六歩(27)\n', encoding='utf-8')
            with self.assertRaisesRegex(extractor.ParseError, 'missing parent'):
                extractor.parse_kif(source, require_names=False)


if __name__ == '__main__':
    unittest.main()
