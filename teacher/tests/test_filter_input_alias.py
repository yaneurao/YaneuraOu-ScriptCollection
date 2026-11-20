import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filter_drawn_games import filter_drawn_games
from filter_hcpe_by_eval import filter_hcpe_by_eval


class FilterInputAliasTests(unittest.TestCase):
    def test_failed_publish_preserves_previous_output(self):
        for filter_fn in (filter_drawn_games, filter_hcpe_by_eval):
            with self.subTest(filter=filter_fn.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output = root / 'input.hcpe', root / 'output.hcpe'
                source.write_bytes(bytes(38))
                output.write_bytes(b'previous')
                with patch('os.replace', side_effect=OSError('disk full')):
                    with self.assertRaisesRegex(OSError, 'disk full'):
                        if filter_fn is filter_drawn_games:
                            filter_fn(source, output, 10)
                        else:
                            filter_fn(source, output, 25000, 10)
                self.assertEqual(output.read_bytes(), b'previous')
                self.assertFalse(list(root.glob('.output.hcpe.*.tmp')))

    def test_hardlink_and_symlink_preserve_input(self):
        for kind in ("hardlink", "symlink"):
            for filter_fn in (filter_drawn_games, filter_hcpe_by_eval):
                with self.subTest(kind=kind, filter=filter_fn.__name__), tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "source.hcpe"
                    output = Path(directory) / "output.hcpe"
                    data = bytes(range(38))
                    source.write_bytes(data)
                    if kind == "hardlink":
                        os.link(source, output)
                    else:
                        try:
                            output.symlink_to(source)
                        except OSError:
                            continue  # Windows may not grant symlink creation privileges.
                    with self.assertRaises(ValueError):
                        if filter_fn is filter_drawn_games:
                            filter_fn(source, output, 10)
                        else:
                            filter_fn(source, output, 25000, 10)
                    self.assertEqual(source.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
