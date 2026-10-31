import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "shuffle_split_teacher_external.py"


class ShuffleSplitSafetyTests(unittest.TestCase):
    def test_separate_directories_preserve_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            source.mkdir()
            original = bytes(range(38)) + bytes(reversed(range(38)))
            (source / "input.hcpe").write_bytes(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), str(output), "--bucket-count", "2"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = (output / "shuffled-00001.hcpe").read_bytes()
            self.assertCountEqual([data[i:i + 38] for i in range(0, len(data), 38)],
                                  [original[:38], original[38:]])

    def test_same_directory_with_force_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "shuffled-00001.hcpe"
            original = bytes(range(38))
            source.write_bytes(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), directory, directory, "--force"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), original)

    def test_recursive_output_inside_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            output = source / "output"
            output.mkdir()
            original = bytes(range(38))
            (output / "shuffled-00001.hcpe").write_bytes(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), directory, str(output), "--recursive", "--force"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((output / "shuffled-00001.hcpe").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
