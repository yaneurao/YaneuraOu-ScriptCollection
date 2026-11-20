import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import denryu_kif_downloader_core as denryu
import wcsc_kif_downloader_core as wcsc
import floodgate_kif_downloader_core as floodgate
import shogidb2_kif_downloader as shogidb2


class DownloadTemporaryTests(unittest.TestCase):
    def test_atomic_write_and_copy_keep_existing_files_on_failure(self):
        for module in (denryu, wcsc):
            for operation in ('write', 'copy'):
                with self.subTest(module=module.__name__, operation=operation), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    output, existing_tmp, source = root / 'out.csa', root / 'out.csa.tmp', root / 'source.csa'
                    output.write_bytes(b'old')
                    existing_tmp.write_bytes(b'unrelated')
                    source.write_bytes(b'new')
                    action = (lambda: module.write_bytes_atomically(output, b'new')) if operation == 'write' else (
                        lambda: module.copy_file_atomically(source, output))
                    with patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                        with self.assertRaises(PermissionError):
                            action()
                    self.assertEqual(output.read_bytes(), b'old')
                    self.assertEqual(existing_tmp.read_bytes(), b'unrelated')
                    self.assertEqual(set(root.iterdir()), {output, existing_tmp, source})
                    action()
                    self.assertEqual(output.read_bytes(), b'new')
                    self.assertEqual(existing_tmp.read_bytes(), b'unrelated')

    def test_download_staging_does_not_replace_existing_tmp(self):
        for module in (denryu, wcsc):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output, existing_tmp = root / 'out.csa', root / 'out.csa.tmp'
                output.write_bytes(b'old')
                existing_tmp.write_bytes(b'unrelated')
                with patch.object(module, 'urlopen', return_value=io.BytesIO(b'new')):
                    module.download_to_file('https://example.test/game', output, Mock(wait=Mock(return_value=True)), 1)
                self.assertEqual(output.read_bytes(), b'new')
                self.assertEqual(existing_tmp.read_bytes(), b'unrelated')

    def test_floodgate_replace_failure_cleans_only_own_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, existing_tmp = root / 'out.csa', root / 'out.csa.tmp'
            output.write_bytes(b'old')
            existing_tmp.write_bytes(b'unrelated')
            def fake_download(url, path, timeout, stop):
                path.write_bytes(b'new!')
                return 4
            with patch.object(floodgate, 'fetch_content_length', return_value=4), \
                 patch.object(floodgate, 'download_to_file', side_effect=fake_download), \
                 patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    floodgate.download_changed_file('https://example.test/game', output, 1, None)
            self.assertEqual(output.read_bytes(), b'old')
            self.assertEqual(existing_tmp.read_bytes(), b'unrelated')
            self.assertEqual(set(root.iterdir()), {output, existing_tmp})

    def test_shogidb2_staging_is_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.kif'
            first = shogidb2.unique_temporary_path(output)
            second = shogidb2.unique_temporary_path(output)
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists() and second.exists())
