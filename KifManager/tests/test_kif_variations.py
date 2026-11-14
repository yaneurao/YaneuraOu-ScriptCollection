import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kif_extractor_common as extractor


class KifVariationsTest(unittest.TestCase):
    def test_extracts_mainline_without_parsing_variation_as_continuation(self):
        for separator in ('：', ':'):
            with self.subTest(separator=separator), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source = root / 'game.kif'
                source.write_text(
                    '先手：Alice\n後手：Bob\n手合割：平手\n'
                    '手数----指手---------消費時間--\n'
                    '1 ７六歩(77)\n2 ３四歩(33)\n'
                    f'\n変化{separator}2手\n2 ８四歩(83)\n3 ２六歩(27)\n',
                    encoding='utf-8',
                )
                output = root / 'output.txt'
                stats = extractor.run_extractor(root, output, allow_non_startpos=True)
                self.assertEqual(stats.skipped_parse, 0)
                self.assertEqual(output.read_text(), 'startpos moves 7g7f 3c3d\n')


if __name__ == '__main__':
    unittest.main()
