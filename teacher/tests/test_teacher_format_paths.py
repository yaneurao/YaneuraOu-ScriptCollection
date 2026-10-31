import sys
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CommonLib"))
sys.path.insert(0, str(ROOT / "teacher"))

from TeacherFormatLib import classify_input, classify_output
from convert_teacher import (collect_input_files, resolve_output_format,
                             convert_to_output_folder, convert_to_single_file)


class DottedDirectoryTests(unittest.TestCase):
    def test_failed_conversion_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'bad.hcpe'
            output = root / 'result.psv'
            source.write_bytes(b'bad input')
            output.write_bytes(b'old result')
            def broken_converter(path, file, **kwargs):
                file.write(b'partial')
                raise ValueError('bad input')
            with self.assertRaisesRegex(ValueError, 'bad input'):
                convert_to_single_file(broken_converter, [source], output, batch_size=1, no_progress=True)
            self.assertEqual(output.read_bytes(), b'old result')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['bad.hcpe', 'result.psv'])

    def test_conversion_rejects_output_hardlinked_to_input_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.hcpe'
            output = root / 'output.psv'
            data = bytes(range(38))
            source.write_bytes(data)
            os.link(source, output)
            with self.assertRaisesRegex(ValueError, 'output refers to an input'):
                convert_to_single_file(None, [source], output, batch_size=1, no_progress=True)
            self.assertEqual(source.read_bytes(), data)

    def test_folder_conversion_checks_all_destinations_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'output'
            source.mkdir()
            output.mkdir()
            first, second = source / 'a.hcpe', source / 'b.hcpe'
            first.write_bytes(bytes(38))
            second.write_bytes(bytes([1]) * 38)
            os.link(second, output / 'b.psv')
            with self.assertRaisesRegex(ValueError, 'output refers to an input'):
                convert_to_output_folder(None, [first, second], source, output, 'psv',
                                         recursive=False, batch_size=1, no_progress=True)
            self.assertFalse((output / 'a.psv').exists())
            self.assertEqual(second.read_bytes(), bytes([1]) * 38)

    def test_existing_directories_with_dots_are_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.v1"
            output = Path(directory) / "output.v2"
            source.mkdir()
            output.mkdir()
            (source / "input.hcpe").write_bytes(bytes(38))
            self.assertEqual(classify_input(source, "hcpe"), "folder")
            self.assertEqual(classify_output(output, "psv"), "folder")
            self.assertEqual(collect_input_files(source, False)[0], "folder")
            self.assertEqual(resolve_output_format(output, "psv"), ("folder", "psv"))


if __name__ == "__main__":
    unittest.main()
