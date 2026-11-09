from __future__ import annotations

from collections import Counter
import shlex
import posixpath
from pathlib import PureWindowsPath
from typing import Any

from .toolchains import (
    WINARM_CLANGARM64_INCLUDE_DIR,
    WINARM_CLANGARM64_LIB_DIR,
    WINARM_CROSS_COMPILER,
    WINARM_EHANDLER_STUB_O,
    WIN32_CROSS_COMPILER,
    WIN32_MINGW32_BIN_DIR,
    WIN32_MINGW32_INCLUDE_DIR,
    WIN32_MINGW32_LIB_DIR,
    WIN32_MINGW32_TARGET_LIB_DIR,
    WIN32_SYSROOT,
    WIN32_TARGET,
)
from .versioning import package_version_from_engine_version


def create_plan(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    kind = recipe.get("kind")
    if kind == "release_all":
        return _create_release_plan(recipe)
    if kind == "single_build":
        return [_create_single_build_job(recipe)]
    if kind == "spsa_build":
        return [_create_spsa_build_job(recipe)]
    if kind == "bookminer_cpp":
        return _create_bookminer_cpp_plan(recipe)
    raise ValueError(f"Unknown recipe kind: {kind!r}")


def validate_plan(recipe: dict[str, Any], plan: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not plan:
        warnings.append("Build Plan is empty.")

    artifact_keys = [
        (job.get("script_group", ""), job.get("artifact") or job.get("output_path"))
        for job in plan
    ]
    duplicates = [key for key, count in Counter(artifact_keys).items() if key[1] and count > 1]
    for group, name in duplicates[:20]:
        warnings.append(f"Duplicate output path in {group}: {name}")
    if len(duplicates) > 20:
        warnings.append(f"{len(duplicates) - 20} more duplicate output paths omitted.")

    if recipe.get("kind") == "release_all":
        editions = recipe.get("editions", [])
        edition_keys = [
            (edition.get("edition"), edition.get("artifact_prefix"))
            for edition in editions
            if edition.get("enabled", True)
        ]
        duplicate_editions = [key for key, count in Counter(edition_keys).items() if count > 1]
        for edition, prefix in duplicate_editions:
            warnings.append(f"Duplicate edition row: {edition} -> {prefix}")

    return warnings


def _create_release_plan(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    version = _required_str(recipe, "version")
    package_version = package_version_from_engine_version(version)
    recipe_compiler = _required_str(recipe, "compiler")
    jobs = int(recipe.get("jobs", 8))
    platforms = _required_list(recipe, "platforms")
    variants = [variant for variant in _required_list(recipe, "variants") if variant.get("enabled", True)]
    if not variants:
        raise ValueError("Missing enabled variant.")
    editions = [e for e in _required_list(recipe, "editions") if e.get("enabled", True)]
    cpus_by_platform = recipe.get("cpus", {})
    source_dirs = recipe.get("source_dirs", {})
    common_cppflags = list(recipe.get("common_cppflags", []))
    engine_name = recipe.get("engine_name", "YaneuraOu")

    plan: list[dict[str, Any]] = []
    for platform in platforms:
        source_key = "win" if "win" in platform else platform
        source_dir = source_dirs.get(source_key, "")
        cpus = cpus_by_platform.get(platform)
        if cpus is None and "win" in platform:
            cpus = cpus_by_platform.get("win", [])
        if not cpus:
            continue
        for variant in variants:
            variant_name = _required_str(variant, "name")
            variant_flags = list(variant.get("extra_cppflags", []))
            script_group = f"my-uraou-{variant_name.lower()}-{platform}"
            for edition in editions:
                edition_name = _required_str(edition, "edition")
                recipe_artifact_prefix = _required_str(edition, "artifact_prefix")
                artifact_prefix = _artifact_prefix_for_platform(platform, edition_name, recipe_artifact_prefix)
                eval_dir = artifact_prefix.removeprefix("YaneuraOu_")
                pgo, pgo_vars, pgo_eval_dir = _pgo_options(recipe, edition_name, platform, source_dir)
                for cpu in cpus:
                    artifact = f"{artifact_prefix}-{package_version}{variant_name}_{cpu}{_artifact_extension(platform)}"
                    compiler = _compiler_for_platform(platform, recipe_compiler)
                    extra_cppflags = (
                        _extra_cppflags_for_platform(platform)
                        + common_cppflags
                        + variant_flags
                        + [
                            f"-DENGINE_VERSION={_engine_version_define(version + variant_name)}"
                        ]
                    )
                    extra_ldflags = _extra_ldflags_for_platform(platform)
                    command = _make_command(
                        jobs=jobs,
                        target="pgo" if pgo else "tournament",
                        compiler=compiler,
                        edition=edition_name,
                        engine_name=engine_name,
                        cpu=cpu,
                        extra_cppflags=extra_cppflags,
                        extra_ldflags=extra_ldflags,
                        make_vars=_make_vars_for_platform(platform) + pgo_vars,
                        material_level=recipe.get("material_level") if "MATERIAL" in edition_name else None,
                    )
                    plan.append(
                        {
                            "kind": "release_job",
                            "script_group": script_group,
                            "platform": platform,
                            "source_dir": source_dir,
                            "variant": variant_name,
                            "target": "pgo" if pgo else "tournament",
                            "pgo": pgo,
                            "pgo_eval_dir": pgo_eval_dir,
                            "edition": edition_name,
                            "artifact_prefix": artifact_prefix,
                            "recipe_artifact_prefix": recipe_artifact_prefix,
                            "eval_dir": eval_dir,
                            "cpu": cpu,
                            "artifact": artifact,
                            "package_version": package_version,
                            "source_executable": _source_executable_for_platform(platform),
                            "compiler": compiler,
                            "command": command,
                        }
                    )
    return plan


def _create_single_build_job(recipe: dict[str, Any]) -> dict[str, Any]:
    edition = _required_str(recipe, "edition")
    version = _required_str(recipe, "version")
    pgo, pgo_vars, pgo_eval_dir = _pgo_options(recipe, edition, source_dir=_required_str(recipe, "source_dir"))
    extra_cppflags = list(recipe.get("common_cppflags", [])) + [
        f"-DENGINE_VERSION={_engine_version_define(version)}"
    ]
    return {
        "kind": "single_build",
        "script_group": recipe.get("name", "single-build"),
        "source_dir": _required_str(recipe, "source_dir"),
        "work_dir": _required_str(recipe, "work_dir"),
        "target": "pgo" if pgo else recipe.get("target", "tournament"),
        "pgo": pgo,
        "pgo_eval_dir": pgo_eval_dir,
        "compiler": recipe.get("compiler", "clang++"),
        "jobs": int(recipe.get("jobs", 8)),
        "edition": edition,
        "cpu": _required_str(recipe, "target_cpu"),
        "engine_name": recipe.get("engine_name", "YaneuraOu"),
        "material_level": recipe.get("material_level") if "MATERIAL" in edition else None,
        "output_path": _required_str(recipe, "output_path"),
        "command": _make_command(
            jobs=int(recipe.get("jobs", 8)),
            target="pgo" if pgo else recipe.get("target", "tournament"),
            compiler=recipe.get("compiler", "clang++"),
            edition=edition,
            engine_name=recipe.get("engine_name", "YaneuraOu"),
            cpu=_required_str(recipe, "target_cpu"),
            extra_cppflags=extra_cppflags,
            extra_ldflags=[],
            make_vars=pgo_vars,
            material_level=recipe.get("material_level") if "MATERIAL" in edition else None,
        ),
    }


def _create_spsa_build_job(recipe: dict[str, Any]) -> dict[str, Any]:
    job = _create_single_build_job(recipe)
    job["kind"] = "spsa_build"
    job["tune_mode"] = _required_str(recipe, "tune_mode")
    job["tune_py"] = _required_str(recipe, "tune_py")
    job["param_lib"] = _required_str(recipe, "param_lib")
    job["tune_file"] = _required_str(recipe, "tune_file")
    job["params_file"] = _required_str(recipe, "params_file")
    return job


def _create_bookminer_cpp_plan(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    platforms = _required_list(recipe, "platforms")
    cpus_by_platform = recipe.get("cpus", {})
    compiler = _required_str(recipe, "compiler")
    jobs = int(recipe.get("jobs", 8))
    source_dir = _required_str(recipe, "source_dir")
    bookminer_cpp_source_dir = _required_str(recipe, "bookminer_cpp_source_dir")
    version = str(recipe.get("version", "")).strip()
    extra_cppflags = list(recipe.get("common_cppflags", []))
    if version:
        extra_cppflags.append(f"-DENGINE_VERSION={_bookminer_engine_version_define(version)}")

    plan: list[dict[str, Any]] = []
    for platform in platforms:
        if platform == "winarm":
            raise ValueError("BookMinerCpp build does not support Windows arm yet.")
        cpus = cpus_by_platform.get(platform)
        if cpus is None and "win" in platform:
            cpus = cpus_by_platform.get("win", [])
        if not cpus:
            continue
        script_group = f"bookminer-cpp-{platform}"
        for cpu in cpus:
            artifact = f"BookMinerCpp-{cpu}{_bookminer_artifact_extension(platform)}"
            plan.append(
                {
                    "kind": "bookminer_cpp",
                    "script_group": script_group,
                    "platform": platform,
                    "source_dir": source_dir,
                    "bookminer_cpp_source_dir": bookminer_cpp_source_dir,
                    "compiler": _compiler_for_platform(platform, compiler),
                    "jobs": jobs,
                    "cpu": str(cpu),
                    "extra_cppflags": extra_cppflags,
                    "artifact": artifact,
                }
            )
    return plan


def _pgo_options(
    recipe: dict[str, Any], edition: str, platform: str = "", source_dir: str = "",
) -> tuple[bool, list[str], str]:
    pgo = recipe.get("pgo", {})
    enabled = bool(pgo.get("enabled", False) or recipe.get("target") == "pgo")
    if not enabled:
        return False, [], ""
    if platform == "winarm":
        raise ValueError("PGO training requires a runnable engine; Windows arm cross builds are not supported.")
    arch = edition.removeprefix("YANEURAOU_ENGINE_")
    eval_dir = str(pgo.get("eval_dir", "eval/[arch]")).strip().replace("[arch]", arch)
    eval_dir = eval_dir.replace("\\", "/")
    if eval_dir and not (posixpath.isabs(eval_dir) or PureWindowsPath(eval_dir).is_absolute()):
        # ビルド用コピーではなく、元のリポジトリのeval/を使う。
        eval_dir = posixpath.normpath(posixpath.join(posixpath.dirname(posixpath.normpath(source_dir.replace("\\", "/"))), eval_dir))
    if any(name in edition for name in ("NNUE", "SFNN", "KPPT", "KPP_KKPT", "DEEP")) and not eval_dir:
        raise ValueError(f"PGO eval folder is required for {edition}. Set it in the PGO tab.")
    bench = str(pgo.get("bench", "1024 1 14 default depth")).strip()
    if not bench or any(c in bench + eval_dir for c in "\r\n"):
        raise ValueError("PGO bench and eval folder must each be a single nonempty line.")
    values = {"PGO_BENCH": bench}
    if eval_dir:
        values["PGO_EVAL_DIR"] = eval_dir.replace("\\", "/")
    if str(pgo.get("profdata", "")).strip():
        values["PGO_PROFDATA"] = str(pgo["profdata"]).replace("\\", "/")
    variables = [shlex.quote(key + "=" + value) for key, value in values.items()]
    variables.append('PYTHON="$PYTHON_CMD"')
    return True, variables, eval_dir


def _make_command(
    *,
    jobs: int,
    target: str,
    compiler: str,
    edition: str,
    engine_name: str,
    cpu: str,
    extra_cppflags: list[str],
    extra_ldflags: list[str],
    make_vars: list[str],
    material_level: Any = None,
) -> str:
    material = f"MATERIAL_LEVEL={material_level} " if material_level is not None else ""
    cppflags = " ".join(flag for flag in extra_cppflags if flag)
    command = (
        f"make -j{jobs} {target} COMPILER={compiler} "
        f"YANEURAOU_EDITION={edition} ENGINE_NAME=\"{engine_name}\" "
        f"TARGET_CPU={cpu} {material}EXTRA_CPPFLAGS=\"{cppflags}\""
    )
    ldflags = " ".join(flag for flag in extra_ldflags if flag)
    if ldflags:
        command += f" EXTRA_LDFLAGS=\"{ldflags}\""
    for make_var in make_vars:
        command += f" {make_var}"
    return command


def _compiler_for_platform(platform: str, recipe_compiler: str) -> str:
    if platform == "winarm":
        return WINARM_CROSS_COMPILER
    if platform == "win32":
        return WIN32_CROSS_COMPILER
    return recipe_compiler


def _extra_cppflags_for_platform(platform: str) -> list[str]:
    if platform == "winarm":
        return ["-idirafter", WINARM_CLANGARM64_INCLUDE_DIR]
    if platform == "win32":
        return [
            f"--target={WIN32_TARGET}",
            f"--sysroot={WIN32_SYSROOT}",
            "-stdlib=libstdc++",
            "-mstackrealign",
            "-Wno-unused-parameter",
            "-fno-threadsafe-statics",
            "-idirafter",
            WIN32_MINGW32_INCLUDE_DIR,
        ]
    return []


def _extra_ldflags_for_platform(platform: str) -> list[str]:
    if platform == "winarm":
        return [f"./{WINARM_EHANDLER_STUB_O}", f"-L{WINARM_CLANGARM64_LIB_DIR}"]
    if platform == "win32":
        return [
            f"-B{WIN32_MINGW32_BIN_DIR}",
            f"--sysroot={WIN32_SYSROOT}",
            "-fuse-ld=bfd",
            "-rtlib=libgcc",
            f"-L{WIN32_MINGW32_LIB_DIR}",
            f"-L{WIN32_MINGW32_TARGET_LIB_DIR}",
        ]
    return []


def _make_vars_for_platform(platform: str) -> list[str]:
    if platform == "win32":
        return ["LTOFLAGS="]
    return []


def _artifact_prefix_for_platform(platform: str, edition: str, artifact_prefix: str) -> str:
    if platform == "winarm" and edition == "YANEURAOU_ENGINE_NNUE":
        return "YaneuraOu_NNUE"
    return artifact_prefix


def _artifact_extension(platform: str) -> str:
    if platform == "winarm":
        return ".exe"
    return ""


def _bookminer_artifact_extension(platform: str) -> str:
    if "win" in platform:
        return ".exe"
    return ""


def _source_executable_for_platform(platform: str) -> str:
    if platform == "winarm":
        return "YaneuraOu-by-gcc.exe"
    return "YaneuraOu-by-gcc"


def _engine_version_define(version: str) -> str:
    return f'\\\\\\"{version}\\\\\\"'


def _bookminer_engine_version_define(version: str) -> str:
    return f'\\"{version}\\"'


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required value: {key}")
    return str(value)


def _required_list(mapping: dict[str, Any], key: str) -> list[Any]:
    value = mapping.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Missing required list: {key}")
    return value
