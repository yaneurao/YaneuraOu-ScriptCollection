import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CommonLib"))
sys.path.insert(0, str(ROOT / "teacher"))

from TeacherFormatLib import classify_input, classify_output
from convert_teacher import collect_input_files, resolve_output_format


class DottedDirectoryTests(unittest.TestCase):
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
