import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filter_drawn_games import filter_drawn_games
from filter_hcpe_by_eval import filter_hcpe_by_eval


class FilterInputAliasTests(unittest.TestCase):
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
