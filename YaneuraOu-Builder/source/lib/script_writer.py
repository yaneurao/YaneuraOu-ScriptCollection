from __future__ import annotations

import json
import os
import stat
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .toolchains import (
    WINARM_CLANGARM64_INCLUDE_DIR,
    WINARM_CROSS_BIN_DIR,
    WINARM_CROSS_CC,
    WINARM_EHANDLER_STUB_C,
    WINARM_EHANDLER_STUB_O,
)
from .versioning import package_version_from_engine_version


def write_build_run(recipe: dict[str, Any], plan: list[dict[str, Any]], yobuild_root: Path) -> Path:
    run_root = resolve_run_root(recipe.get("run_root", str(yobuild_root / "runs")), yobuild_root)
    run_name = (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{recipe.get('name', 'build')}{_run_platform_suffix(recipe, plan)}"
    )
    run_dir = run_root / _sanitize_filename(run_name)
    scripts_dir = run_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=False)

    (run_dir / "recipe.json").write_text(
        json.dumps(recipe, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(_create_manifest(recipe, plan, yobuild_root), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if recipe.get("kind") == "release_all":
        _write_release_scripts(recipe, plan, scripts_dir)
    elif recipe.get("kind") == "single_build":
        _write_single_build_script(recipe, plan[0], scripts_dir)
    elif recipe.get("kind") == "spsa_build":
        _write_spsa_build_script(recipe, plan[0], scripts_dir)
    elif recipe.get("kind") == "bookminer_cpp":
        _write_bookminer_cpp_scripts(recipe, plan, scripts_dir)
    else:
        raise ValueError(f"Unknown recipe kind: {recipe.get('kind')!r}")

    _write_run_all_script(run_dir, scripts_dir)
    return run_dir


def _write_release_scripts(recipe: dict[str, Any], plan: list[dict[str, Any]], scripts_dir: Path) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in plan:
        groups[job["script_group"]].append(job)

    package = recipe.get("package", {})
    package_version = package_version_from_engine_version(str(recipe.get("version", "")))

    for script_name, jobs in groups.items():
        first = jobs[0]
        platform = first["platform"]
        variant = first["variant"]
        source_dir = first.get("source_dir", "")
        lines = _script_header(f"release {platform} {variant}")
        lines += [
            f"PLATFORM={_quote(platform)}",
            f"VARIANT={_quote(variant)}",
            f"SOURCE_DIR={_quote(source_dir)}",
            "",
        ]
        lines += _release_source_setup(recipe)
        if platform == "mac":
            lines += [
                "if [ -f Makefile ]; then",
                "  sed -i '' \"s/^PYTHON =.*/PYTHON = $PYTHON_CMD/\" Makefile",
                "elif [ -f makefile ]; then",
                "  sed -i '' \"s/^PYTHON =.*/PYTHON = $PYTHON_CMD/\" makefile",
                "fi",
            ]
        elif platform == "winarm":
            lines += _winarm_cross_setup()
        elif platform == "win32":
            lines += _win32_cross_setup()

        previous_eval_dir = None
        for job in jobs:
            eval_dir = job["eval_dir"]
            if eval_dir != previous_eval_dir:
                lines.append(f"mkdir -p {_quote('../' + eval_dir)}")
                previous_eval_dir = eval_dir
            if platform == "win32":
                lines.append('require_command "$WIN32_CLANG_WRAPPER"')
            else:
                lines.append(f"require_command {_quote_literal(str(job.get('compiler', 'clang++')))}")
            lines.append(f"make clean YANEURAOU_EDITION={job['edition']}")
            lines.append(job["command"])
            source_executable = job.get("source_executable", "YaneuraOu-by-gcc")
            lines.append(f"cp {source_executable} {_quote('../' + eval_dir + '/' + job['artifact'])}")
            lines.append("")

        lines += [
            "cd ..",
            'cp "$SCRIPT_PATH" .',
        ]
        if package.get("enabled", True):
            excludes = " ".join(f"-xr!{item}" for item in package.get("exclude", ["obj"]))
            package_name = f"yaneuraou-{package_version}-{variant.lower()}-{platform}-all.7z"
            lines.append("require_command 7z")
            lines.append(f"7z a {package_name} {excludes} *")

        _write_script(scripts_dir / script_name, lines)


def _release_source_setup(recipe: dict[str, Any]) -> list[str]:
    spsa = recipe.get("spsa", {})
    spsa_mode = str(spsa.get("mode", "none"))
    if spsa_mode == "none":
        return [
            "mkdir -p build",
            "rm -rf build/source",
            'cp -r "$SOURCE_DIR" build/source',
            "cd build/source",
        ]

    tune_py = str(spsa.get("tune_py", ""))
    param_lib = str(spsa.get("param_lib", ""))
    tune_file = str(spsa.get("tune_file", ""))
    params_file = str(spsa.get("params_file", ""))
    tune_name = _basename(tune_file)
    return [
        f"TUNE_PY={_quote(tune_py)}",
        f"PARAM_LIB={_quote(param_lib)}",
        f"TUNE_FILE={_quote(tune_file)}",
        f"PARAMS_FILE={_quote(params_file)}",
        f"TUNE_FILE_NAME={_quote(tune_name)}",
        "",
        "mkdir -p build",
        "rm -rf build/source",
        'cp -r "$SOURCE_DIR" build/source',
        'cp "$TUNE_PY" build/',
        'cp "$PARAM_LIB" build/',
        'cp "$TUNE_FILE" build/',
        'cp "$PARAMS_FILE" build/',
        "cd build",
        f'"$PYTHON_CMD" tune.py {spsa_mode} "$TUNE_FILE_NAME" source',
        "cd source",
    ]


def _winarm_cross_setup() -> list[str]:
    return [
        f'export PATH="{WINARM_CROSS_BIN_DIR}:$PATH"',
        f"cat > {WINARM_EHANDLER_STUB_C} <<'EOF'",
        "#include <windows.h>",
        "",
        "LPTOP_LEVEL_EXCEPTION_FILTER __mingw_oldexcpt_handler = 0;",
        "",
        "LONG CALLBACK _gnu_exception_handler(EXCEPTION_POINTERS *exception_data) {",
        "    (void)exception_data;",
        "    return EXCEPTION_CONTINUE_SEARCH;",
        "}",
        "EOF",
        f"{WINARM_CROSS_CC} -idirafter {WINARM_CLANGARM64_INCLUDE_DIR} "
        f"-c {WINARM_EHANDLER_STUB_C} -o {WINARM_EHANDLER_STUB_O}",
        "",
    ]


def _win32_cross_setup() -> list[str]:
    return [
        'mkdir -p "$RUN_DIR/tools"',
        'WIN32_CLANG_WRAPPER="$RUN_DIR/tools/yobuild-win32-clang"',
        'cat > "$WIN32_CLANG_WRAPPER" <<\'EOF\'',
        "#!/usr/bin/env bash",
        'exec /mingw64/bin/clang++ "$@"',
        "EOF",
        'chmod +x "$WIN32_CLANG_WRAPPER"',
        "require_command /mingw32/bin/g++",
        'WIN32_GCC_VERSION="$(/mingw32/bin/g++ -dumpfullversion -dumpversion | head -n 1)"',
        'WIN32_CXX_INCLUDE_DIR="/mingw32/include/c++/$WIN32_GCC_VERSION"',
        'if [ ! -d "$WIN32_CXX_INCLUDE_DIR" ]; then',
        '  WIN32_CXX_INCLUDE_DIR="$(find /mingw32/include/c++ -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"',
        "fi",
        'if [ -z "$WIN32_CXX_INCLUDE_DIR" ] || [ ! -d "$WIN32_CXX_INCLUDE_DIR" ]; then',
        '  echo "mingw32 C++ include directory not found." >&2',
        "  exit 1",
        "fi",
        'WIN32_CXX_TARGET_INCLUDE_DIR="$WIN32_CXX_INCLUDE_DIR/i686-w64-mingw32"',
        'WIN32_GCC_LIB_DIR="/mingw32/lib/gcc/i686-w64-mingw32/$WIN32_GCC_VERSION"',
        'if [ ! -d "$WIN32_GCC_LIB_DIR" ]; then',
        '  WIN32_GCC_LIB_DIR="$(find /mingw32/lib/gcc/i686-w64-mingw32 -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"',
        "fi",
        'export CPLUS_INCLUDE_PATH="$WIN32_CXX_INCLUDE_DIR:$WIN32_CXX_TARGET_INCLUDE_DIR:/mingw32/include${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"',
        'export LIBRARY_PATH="$WIN32_GCC_LIB_DIR:/mingw32/lib:/mingw32/i686-w64-mingw32/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"',
        'echo "[win32] C++ include: $WIN32_CXX_INCLUDE_DIR"',
        'echo "[win32] GCC lib: $WIN32_GCC_LIB_DIR"',
        "",
    ]


def _write_single_build_script(recipe: dict[str, Any], job: dict[str, Any], scripts_dir: Path) -> None:
    script_name = f"{recipe.get('name', 'single-build')}.sh"
    source_dir = job["source_dir"]
    work_dir = job["work_dir"]
    lines = _script_header(str(recipe.get("name", "single build")))
    lines += [
        f"SOURCE_DIR={_quote(source_dir)}",
        f"WORK_DIR={_quote(work_dir)}",
        f"OUTPUT_PATH={_quote(job['output_path'])}",
        "",
        'mkdir -p "$WORK_DIR"',
        'rm -rf "$WORK_DIR/source"',
        'cp -r "$SOURCE_DIR" "$WORK_DIR/source"',
        'cd "$WORK_DIR/source"',
        f"make clean YANEURAOU_EDITION={job['edition']}",
        job["command"],
        'mkdir -p "$(dirname "$OUTPUT_PATH")"',
        'cp YaneuraOu-by-gcc "$OUTPUT_PATH"',
    ]
    _write_script(scripts_dir / script_name, lines)


def _write_spsa_build_script(recipe: dict[str, Any], job: dict[str, Any], scripts_dir: Path) -> None:
    script_name = f"{recipe.get('name', 'spsa-build')}.sh"
    work_dir = job["work_dir"]
    tune_name = _basename(job["tune_file"])
    lines = _script_header(str(recipe.get("name", "spsa build")))
    lines += [
        f"SOURCE_DIR={_quote(job['source_dir'])}",
        f"WORK_DIR={_quote(work_dir)}",
        f"TUNE_PY={_quote(job['tune_py'])}",
        f"PARAM_LIB={_quote(job['param_lib'])}",
        f"TUNE_FILE={_quote(job['tune_file'])}",
        f"PARAMS_FILE={_quote(job['params_file'])}",
        f"TUNE_FILE_NAME={_quote(tune_name)}",
        f"OUTPUT_PATH={_quote(job['output_path'])}",
        "",
        'mkdir -p "$WORK_DIR"',
        'rm -rf "$WORK_DIR/source"',
        'cp -r "$SOURCE_DIR" "$WORK_DIR/source"',
        'cp "$TUNE_PY" "$WORK_DIR"',
        'cp "$PARAM_LIB" "$WORK_DIR"',
        'cp "$TUNE_FILE" "$WORK_DIR"',
        'cp "$PARAMS_FILE" "$WORK_DIR"',
        'cd "$WORK_DIR"',
        f'"$PYTHON_CMD" tune.py {job["tune_mode"]} "$TUNE_FILE_NAME" "$WORK_DIR/source"',
        "cd source",
        f"make clean YANEURAOU_EDITION={job['edition']}",
        job["command"],
        'mkdir -p "$(dirname "$OUTPUT_PATH")"',
        'cp YaneuraOu-by-gcc "$OUTPUT_PATH"',
    ]
    _write_script(scripts_dir / script_name, lines)


def _write_bookminer_cpp_scripts(recipe: dict[str, Any], plan: list[dict[str, Any]], scripts_dir: Path) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in plan:
        groups[job["script_group"]].append(job)

    for script_name, jobs in groups.items():
        first = jobs[0]
        lines = _script_header(f"BookMinerCpp {first['platform']}")
        lines += [
            f"BOOKMINER_CPP_SOURCE_DIR={_quote(first['bookminer_cpp_source_dir'])}",
            f"YANEURAOU_ROOT={_quote(first['source_dir'])}",
            "",
            "mkdir -p artifacts build/bookminer-cpp",
            "",
        ]
        if first["platform"] == "win32":
            lines += _win32_cross_setup()
        for job in jobs:
            cpu = str(job["cpu"])
            artifact = str(job["artifact"])
            extra_cppflags = " ".join(str(flag) for flag in job.get("extra_cppflags", []) if flag)
            build_dir = f"$RUN_DIR/build/bookminer-cpp/{cpu}"
            target_path = f"$RUN_DIR/artifacts/{artifact}"
            lines += [
                f"echo \"[BookMinerCpp] TARGET_CPU={cpu}\"",
                'require_command "$WIN32_CLANG_WRAPPER"' if first["platform"] == "win32" else f"require_command {_quote_literal(str(job.get('compiler', 'clang++')))}",
                (
                    f"make -C \"$BOOKMINER_CPP_SOURCE_DIR\" clean "
                    f"TARGET={_quote(target_path)} "
                    f"BUILD_DIR={_quote(build_dir)}"
                ),
                (
                    f"make -C \"$BOOKMINER_CPP_SOURCE_DIR\" -j{int(job.get('jobs', 8))} "
                    f"CXX={_quote(str(job.get('compiler', 'clang++')))} "
                    f"TARGET_CPU={_quote(cpu)} "
                    f"TARGET={_quote(target_path)} "
                    f"BUILD_DIR={_quote(build_dir)} "
                    f"YANEURAOU_ROOT=\"$YANEURAOU_ROOT\" "
                    f"EXTRA_CPPFLAGS={_quote_literal(extra_cppflags)}"
                ),
                "",
            ]
        _write_script(scripts_dir / script_name, lines)


def _script_header(title: str) -> list[str]:
    return [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"',
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then',
        '  RUN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"',
        "else",
        '  RUN_DIR="$SCRIPT_DIR"',
        "fi",
        'cd "$RUN_DIR"',
        "",
        f"# Generated by YO-Build MVP: {title}",
        f"# Generated at: {datetime.now().isoformat(timespec='seconds')}",
        '# This script is self-contained: run it from any current directory.',
        'require_command() {',
        '  if ! command -v "$1" >/dev/null 2>&1; then',
        '    echo "required command not found: $1" >&2',
        '    echo "MSYSTEM=${MSYSTEM:-}" >&2',
        '    echo "PATH=$PATH" >&2',
        '    exit 127',
        '  fi',
        '}',
        'require_command make',
        'if command -v python3 >/dev/null 2>&1; then',
        '  PYTHON_CMD=python3',
        'elif command -v python >/dev/null 2>&1; then',
        '  PYTHON_CMD=python',
        "else",
        '  echo "python3 or python command not found." >&2',
        "  exit 127",
        "fi",
        "",
    ]


def _write_script(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_run_all_script(run_dir: Path, scripts_dir: Path) -> None:
    scripts = sorted(path for path in scripts_dir.iterdir() if path.is_file())
    lines = _script_header("run all generated scripts")
    lines.append("cd \"$RUN_DIR\"")
    for script in scripts:
        lines.append(f"bash {_quote('scripts/' + script.name)}")
    _write_script(run_dir / "run-all", lines)


def _quote(value: str) -> str:
    value = _to_shell_path(value)
    if value.startswith("~/") or value == "~":
        return value
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _to_shell_path(value: str) -> str:
    path = value.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        drive = path[0].lower()
        rest = path[2:].lstrip("/")
        return f"/{drive}/{rest}" if rest else f"/{drive}"
    return path


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def resolve_run_root(path: Any, yobuild_root: Path) -> Path:
    expanded = os.path.expanduser(str(path))
    expanded = _repair_embedded_posix_absolute_path(expanded, yobuild_root)
    if _looks_like_posix_absolute_without_slash(expanded):
        expanded = "/" + expanded
    candidate = Path(expanded)
    if candidate.is_absolute():
        return candidate
    return yobuild_root / candidate


def _repair_embedded_posix_absolute_path(path: str, yobuild_root: Path) -> str:
    root = str(yobuild_root.resolve()).replace("\\", "/").rstrip("/")
    text = path.replace("\\", "/")
    prefix = root + "/"
    if not text.startswith(prefix):
        return path
    rest = text[len(prefix):]
    if _looks_like_posix_absolute_without_slash(rest):
        return "/" + rest
    embedded = _embedded_posix_absolute(rest)
    if embedded:
        return embedded
    return path


def _looks_like_posix_absolute_without_slash(path: str) -> bool:
    return path.startswith(("Users/", "Volumes/", "home/", "opt/", "private/", "tmp/", "var/"))


def _embedded_posix_absolute(path: str) -> str:
    for marker in ("/Users/", "/Volumes/", "/home/", "/opt/", "/private/", "/tmp/", "/var/"):
        index = path.find(marker)
        if index >= 0:
            return path[index:]
    return ""


def _sanitize_filename(name: str) -> str:
    allowed = []
    for char in name:
        if char.isalnum() or char in "._-":
            allowed.append(char)
        else:
            allowed.append("-")
    return "".join(allowed)


def _run_platform_suffix(recipe: dict[str, Any], plan: list[dict[str, Any]]) -> str:
    platforms = [str(platform) for platform in recipe.get("platforms", []) if platform]
    if not platforms:
        platforms = [str(job.get("platform")) for job in plan if job.get("platform")]
    normalized = [_run_platform_name(platform) for platform in platforms]
    unique_platforms = list(dict.fromkeys(platform for platform in normalized if platform))
    if not unique_platforms:
        return ""
    return "-" + "-".join(unique_platforms)


def _run_platform_name(platform: str) -> str:
    if platform == "macos":
        return "mac"
    return platform


def _create_manifest(recipe: dict[str, Any], plan: list[dict[str, Any]], yobuild_root: Path) -> dict[str, Any]:
    return {
        "recipe_name": recipe.get("name"),
        "recipe_kind": recipe.get("kind"),
        "job_count": len(plan),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_value(["git", "rev-parse", "HEAD"], yobuild_root),
        "git_branch": _git_value(["git", "branch", "--show-current"], yobuild_root),
        "git_dirty": bool(_git_value(["git", "status", "--short"], yobuild_root)),
    }


def _git_value(cmd: list[str], yobuild_root: Path) -> str:
    try:
        result = subprocess.run(
            cmd,
            cwd=yobuild_root.parent,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()
