"""Upload Windows release artifacts described by a YO-Build run's plan."""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit

_ARCHES = {"win64": "x64", "win32": "x86", "winarm": "ARM64"}


def collect_artifacts(run_dir: Path) -> list[tuple[Path, str, str]]:
    run_dir = run_dir.resolve()
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    if not isinstance(plan, list):
        raise ValueError("plan.json must contain a list of build jobs")
    root = (run_dir / "build").resolve()
    artifacts = []
    seen = set()
    for job in plan:
        if not isinstance(job, dict) or job.get("kind") != "release_job":
            continue
        arch = _ARCHES.get(job.get("platform"))
        if not arch:
            continue
        cpu = str(job.get("cpu", ""))
        directory = str(job.get("eval_dir", ""))
        name = str(job.get("artifact", ""))
        if not cpu or not directory or not name or Path(directory).name != directory or Path(name).name != name:
            raise ValueError("Invalid artifact path or CPU target in plan.json")
        path = root / directory / name
        if not path.is_file() and not name.lower().endswith('.exe'):
            path = path.with_name(name + '.exe')
        path = path.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Build artifact not found: {path}")
        key = (path, arch, cpu)
        if key not in seen:
            seen.add(key)
            artifacts.append(key)
    if not artifacts:
        raise ValueError("No Windows release executables found in this run.")
    return artifacts


def upload_artifact(server: str, password: str, path: Path, arch: str, cpu: str) -> dict:
    url = urlsplit(server.strip())
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("ShogiZone URL must be an http(s) server URL.")
    if not password or not password.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in password):
        raise ValueError("Upload password must contain printable ASCII characters without spaces.")
    query = urlencode({'name': path.name, 'arch': arch, 'cpu_target': cpu})
    target = url.path.rstrip('/') + '/api/builder/engine?' + query
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=120)
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        connection.putrequest('POST', target)
        connection.putheader('Authorization', 'Bearer ' + password)
        connection.putheader('Content-Type', 'application/octet-stream')
        connection.putheader('Content-Length', str(size))
        connection.endheaders()
        sent = 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                sent += len(chunk)
                digest.update(chunk)
                connection.send(chunk)
        if sent != size:
            raise OSError('Artifact size changed while uploading.')
        response = connection.getresponse()
        data = response.read(1024 * 1024)
        if response.status != 200:
            try:
                message = str(json.loads(data).get('error', response.reason))
            except (ValueError, AttributeError):
                message = response.reason
            raise RuntimeError(f"Upload failed ({response.status}): {message}")
        result = json.loads(data)
        if result.get('sha256') != digest.hexdigest() or result.get('size') != size:
            raise RuntimeError('ShogiZone returned an unexpected file hash or size.')
        return result
    finally:
        connection.close()
