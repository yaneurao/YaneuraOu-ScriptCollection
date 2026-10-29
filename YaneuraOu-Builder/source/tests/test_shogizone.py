import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.shogizone import collect_artifacts, upload_artifact


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'build' / 'MATERIAL').mkdir(parents=True)
        self.executable = self.root / 'build' / 'MATERIAL' / 'YaneuraOu_V980_AVX2.exe'
        self.executable.write_bytes(b'fake exe')
        self.job = dict(kind='release_job', platform='win64', cpu='AVX2',
                        eval_dir='MATERIAL', artifact=self.executable.name)

    def plan(self, jobs):
        (self.root / 'plan.json').write_text(json.dumps(jobs), encoding='utf-8')

    def test_collects_windows_jobs_and_deduplicates(self):
        self.plan([self.job, self.job, dict(self.job, platform='mac')])
        self.assertEqual(collect_artifacts(self.root), [(self.executable, 'x64', 'AVX2')])

    def test_missing_artifact_stops_before_upload(self):
        self.plan([self.job, dict(self.job, artifact='missing.exe')])
        with self.assertRaisesRegex(ValueError, 'not found'):
            collect_artifacts(self.root)

    def test_artifact_cannot_escape_run(self):
        self.plan([dict(self.job, eval_dir='..', artifact='plan.json')])
        with self.assertRaises(ValueError):
            collect_artifacts(self.root)

    def test_upload_sends_metadata_and_checks_receipt(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.path, self.headers.get('Authorization'), body))
                payload = json.dumps(dict(id='asset', size=len(body), sha256=hashlib.sha256(body).hexdigest())).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = upload_artifact(f'http://127.0.0.1:{server.server_port}', 'password', self.executable, 'x64', 'AVX2')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertEqual(result['id'], 'asset')
        path, auth, data = received[0]
        self.assertEqual(urlsplit(path).path, '/api/builder/engine')
        self.assertEqual(parse_qs(urlsplit(path).query)['cpu_target'], ['AVX2'])
        self.assertEqual(auth, 'Bearer password')
        self.assertEqual(data, b'fake exe')


if __name__ == '__main__':
    unittest.main()
