"""Upload Windows release artifacts described by a YO-Build run's plan."""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit

_ARCHES = {"win64": "x64", "win32": "x86", "winarm": "ARM64"}


def _planned_artifacts(run_dir: Path) -> list[tuple[dict, Path, str, str]]:
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
            artifacts.append((job, path, arch, cpu))
    if not artifacts:
        raise ValueError("No Windows release executables found in this run.")
    return artifacts


def collect_artifacts(run_dir: Path) -> list[tuple[Path, str, str]]:
    return [(path, arch, cpu) for _, path, arch, cpu in _planned_artifacts(run_dir)]


def engine_group_name(engine_name: str, edition: str, variant: str) -> str:
    engine_name = engine_name.strip()
    edition = edition.removeprefix("YANEURAOU_ENGINE_")
    if not engine_name or not edition or not variant:
        raise ValueError("ShogiZone engine name, edition and variant are required.")
    name = f"{engine_name}-{edition}-{variant}"
    if len(name) > 80 or name != name.strip() or name.endswith('.') or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in name):
        raise ValueError(f"Invalid ShogiZone engine name: {name}")
    return name


def collect_artifact_groups(run_dir: Path, engine_name: str) -> dict[str, list[tuple[Path, str, str]]]:
    groups: dict[str, list[tuple[Path, str, str]]] = {}
    identities: dict[str, tuple[str, str]] = {}
    for job, path, arch, cpu in _planned_artifacts(run_dir):
        edition, variant = str(job.get("edition", "")), str(job.get("variant", ""))
        name = engine_group_name(engine_name, edition, variant)
        identity = (edition, variant)
        if name in identities and identities[name] != identity:
            raise ValueError(f"Multiple editions or variants produce the same ShogiZone name: {name}")
        identities[name] = identity
        group = groups.setdefault(name, [])
        if any(existing_arch == arch and existing_cpu == cpu for _, existing_arch, existing_cpu in group):
            raise ValueError(f"Duplicate CPU target in ShogiZone engine: {name} ({arch}, {cpu})")
        group.append((path, arch, cpu))
    return groups


def validate_upload_settings(server: str, password: str) -> None:
    url = urlsplit(server.strip())
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("ShogiZone URL must be an http(s) server URL.")
    try:
        url.port
    except ValueError as exc:
        raise ValueError("Invalid ShogiZone server port.") from exc
    if not password or not password.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in password):
        raise ValueError("Upload password must contain printable ASCII characters without spaces.")


def upload_artifact(server: str, password: str, path: Path, arch: str, cpu: str) -> dict:
    validate_upload_settings(server, password)
    url = urlsplit(server.strip())
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


def _post_json(server: str, password: str, endpoint: str, payload: dict) -> dict:
    validate_upload_settings(server, password)
    url = urlsplit(server.strip())
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=120)
    try:
        connection.request('POST', url.path.rstrip('/') + endpoint, body=data,
                           headers={'Authorization': 'Bearer ' + password, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read(1024 * 1024)
        if response.status != 200:
            try:
                message = str(json.loads(data).get('error', response.reason))
            except (ValueError, AttributeError):
                message = response.reason
            raise RuntimeError(f"Engine registration failed ({response.status}): {message}")
        return json.loads(data)
    finally:
        connection.close()


def preflight_engines(server: str, password: str, names: list[str]) -> None:
    _post_json(server, password, '/api/builder/engine-preflight', {'names': names})


def register_engine(server: str, password: str, name: str, asset_ids: list[str]) -> dict:
    return _post_json(server, password, '/api/builder/engine-preset', {'name': name, 'assets': asset_ids})
