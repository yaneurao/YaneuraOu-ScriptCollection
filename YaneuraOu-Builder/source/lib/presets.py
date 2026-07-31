from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


PRESET_NAMES = ("release-all", "yo-material", "spsa-tune", "spsa-apply", "bookminer-cpp")

PRESET_LABELS = {
    "release-all": "Release all",
    "yo-material": "YO-MATERIAL",
    "spsa-tune": "SPSA tune",
    "spsa-apply": "SPSA apply",
    "bookminer-cpp": "BookMinerCpp",
}

MAC_WINBUILD_ROOT = "/winbuild"
MAC_DEFAULT_SOURCE_DIR = f"{MAC_WINBUILD_ROOT}/source"
MAC_DEFAULT_TUNE_PY = f"{MAC_WINBUILD_ROOT}/tune.py"
MAC_DEFAULT_PARAM_LIB = f"{MAC_WINBUILD_ROOT}/ParamLib.py"
MAC_DEFAULT_TUNE_FILE = f"{MAC_WINBUILD_ROOT}/YaneuraOuV950.tune"
MAC_DEFAULT_PARAMS_FILE = f"{MAC_WINBUILD_ROOT}/YaneuraOuV950.params"


RELEASE_EDITIONS = [
    ("YANEURAOU_ENGINE_NNUE", "YaneuraOu_NNUE_halfkp_256x2_32_32"),
    ("YANEURAOU_ENGINE_SFNN1536", "YaneuraOu_SFNN1536"),
    ("YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3", "YaneuraOu_SFNN_halfka2_1024_7_64_k3k3"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_1024X2_8_32", "YaneuraOu_NNUE_halfkp_1024x2_8_32"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_1024X2_8_64", "YaneuraOu_NNUE_halfkp_1024x2_8_64"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_768X2_16_64", "YaneuraOu_NNUE_halfkp_768x2_16_64"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64", "YaneuraOu_NNUE_halfkp_512x2_8_64"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_384X2_8_96", "YaneuraOu_NNUE_halfkp_384x2_8_96"),
    ("YANEURAOU_ENGINE_NNUE_HALFKPE9", "YaneuraOu_NNUE_halfkpe9_256x2_32_32"),
    ("YANEURAOU_ENGINE_NNUE_HALFKP_VM_256X2_32_32", "YaneuraOu_NNUE_halfkpvm_256x2_32_32"),
    ("YANEURAOU_ENGINE_NNUE_KP256", "YaneuraOu_NNUE_kp_256x2_32_32"),
    ("YANEURAOU_ENGINE_KPPT", "YaneuraOu_KPPT"),
    ("YANEURAOU_ENGINE_KPP_KKPT", "YaneuraOu_KPP_KKPT"),
    ("YANEURAOU_ENGINE_MATERIAL", "YO-MATERIAL"),
]

DEFAULT_DISABLED_RELEASE_EDITIONS = {
    "YANEURAOU_ENGINE_MATERIAL",
}


def create_preset(name: str, yobuild_root: Path) -> dict[str, Any]:
    if name == "release-all":
        return _release_all(yobuild_root)
    if name == "yo-material":
        return _yo_material(yobuild_root)
    if name == "spsa-tune":
        return _spsa_tune(yobuild_root)
    if name == "spsa-apply":
        return _spsa_apply(yobuild_root)
    if name == "bookminer-cpp":
        return _bookminer_cpp(yobuild_root)
    raise ValueError(f"Unknown preset: {name}")


def _base_recipe(yobuild_root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_root": str(yobuild_root / "runs"),
    }


def _first_existing_dir(candidates: tuple[Path, ...], fallback: Path) -> str:
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(fallback)


def _first_existing_file(candidates: tuple[Path, ...], fallback: Path) -> str:
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(fallback)


def _yaneuraou_source_dir(yobuild_root: Path) -> str:
    return _first_existing_dir(
        (
            yobuild_root.parent / "YaneuraOu" / "source",
            yobuild_root.parent.parent / "YaneuraOu" / "source",
        ),
        yobuild_root.parent / "YaneuraOu" / "source",
    )


def _yosc_dir(yobuild_root: Path) -> Path:
    candidates = (
        yobuild_root.parent,
        yobuild_root.parent / "YaneuraOu-ScriptCollection",
        yobuild_root.parent.parent / "YaneuraOu-ScriptCollection",
    )
    for candidate in candidates:
        if (candidate / "SPSA").is_dir() or (candidate / "BookMinerCpp").is_dir():
            return candidate
    return yobuild_root.parent / "YaneuraOu-ScriptCollection"


def _spsa_dir(yobuild_root: Path) -> Path:
    yosc = _yosc_dir(yobuild_root)
    return Path(
        _first_existing_dir(
            (
                yosc / "SPSA",
                yobuild_root.parent / "SPSA",
                yobuild_root.parent / "YaneuraOu-ScriptCollection" / "SPSA",
                yobuild_root.parent.parent / "YaneuraOu-ScriptCollection" / "SPSA",
            ),
            yosc / "SPSA",
        )
    )


def _bookminer_cpp_source_dir(yobuild_root: Path) -> str:
    yosc = _yosc_dir(yobuild_root)
    return _first_existing_dir(
        (
            yosc / "BookMinerCpp" / "source",
            yobuild_root.parent / "BookMinerCpp" / "source",
            yobuild_root.parent / "YaneuraOu-ScriptCollection" / "BookMinerCpp" / "source",
            yobuild_root.parent.parent / "YaneuraOu-ScriptCollection" / "BookMinerCpp" / "source",
        ),
        yosc / "BookMinerCpp" / "source",
    )


def _release_all(yobuild_root: Path) -> dict[str, Any]:
    recipe = _base_recipe(yobuild_root)
    source_dir = _yaneuraou_source_dir(yobuild_root)
    recipe.update(
        {
            "kind": "release_all",
            "name": "release-all",
            "version": "V9.40",
            "source_dirs": {
                "win": source_dir,
                "mac": MAC_DEFAULT_SOURCE_DIR,
            },
            "platforms": ["win64", "win32", "mac"],
            "variants": [
                {"name": "DEV", "extra_cppflags": []},
                {"name": "Git", "extra_cppflags": []},
            ],
            "target": "tournament",
            "compiler": "clang++",
            "jobs": 8,
            "engine_name": "YaneuraOu",
            "common_cppflags": ["-DHASH_KEY_BITS=128", "-DTT_CLUSTER_SIZE=4"],
            "cpus": {
                "win64": ["SSE41", "SSE42", "AVX2", "ZEN1", "ZEN2", "AVXVNNI", "AVX512", "AVX512VNNI"],
                "win32": ["SSE41", "SSE42", "AVX2", "ZEN1", "ZEN2", "AVXVNNI", "AVX512", "AVX512VNNI"],
                "winarm": ["ARMV8", "ARMV8_DOTPROD"],
                "mac": ["APPLEM1", "APPLEAVX2", "APPLESSE42"],
            },
            "material_level": 9,
            "editions": [
                {
                    "edition": edition,
                    "artifact_prefix": artifact_prefix,
                    "enabled": edition not in DEFAULT_DISABLED_RELEASE_EDITIONS,
                }
                for edition, artifact_prefix in RELEASE_EDITIONS
            ],
            "package": {
                "enabled": True,
                "format": "7z",
                "exclude": ["obj"],
            },
        }
    )
    return recipe


def _yo_material(yobuild_root: Path) -> dict[str, Any]:
    recipe = _base_recipe(yobuild_root)
    source_dir = _yaneuraou_source_dir(yobuild_root)
    recipe.update(
        {
            "kind": "single_build",
            "name": "yo-material",
            "source_dir": source_dir,
            "work_dir": "~/shogi",
            "target": "tournament",
            "compiler": "clang++",
            "jobs": 8,
            "edition": "YANEURAOU_ENGINE_MATERIAL",
            "engine_name": r"YaneuraOu\(tournament128-cl4\)",
            "target_cpu": "AVX2",
            "version": "V9.40YANE",
            "material_level": 9,
            "common_cppflags": ["-DHASH_KEY_BITS=128", "-DTT_CLUSTER_SIZE=4"],
            "output_path": "../bin/YO-MATERIAL.exe",
        }
    )
    return recipe


def _spsa_base(yobuild_root: Path) -> dict[str, Any]:
    recipe = _yo_material(yobuild_root)
    source_dir = _yaneuraou_source_dir(yobuild_root)
    spsa_dir = _spsa_dir(yobuild_root)
    recipe.update(
        {
            "kind": "spsa_build",
            "source_dir": source_dir,
            "work_dir": "~/shogi/source-tune",
            "tune_py": str(spsa_dir / "tune.py"),
            "param_lib": str(spsa_dir / "ParamLib.py"),
            "material_level": None,
            "target_cpu": "AVX512VNNI",
        }
    )
    return recipe


def _spsa_tune(yobuild_root: Path) -> dict[str, Any]:
    recipe = deepcopy(_spsa_base(yobuild_root))
    spsa_dir = _spsa_dir(yobuild_root)
    recipe.update(
        {
            "name": "spsa-tune",
            "tune_mode": "tune",
            "tune_file": _first_existing_file(
                (
                    spsa_dir / "param" / "YaneuraOuV931.tune",
                    spsa_dir / "param" / "YaneuraOuV930.tune",
                ),
                spsa_dir / "param" / "YaneuraOuV931.tune",
            ),
            "params_file": str(spsa_dir / "param" / "YaneuraOuV931.params"),
            "edition": "YANEURAOU_ENGINE_NNUE_SFNNwoPSQT_HALFKA2_1024_7_64_LS9",
            "version": "V9.31YANE",
            "output_path": "~/shogi/source-tune/YANEURAOU_ENGINE_NNUE_SFNNwoPSQT_HALFKA2_1024_7_64_LS9-tune.exe",
        }
    )
    return recipe


def _spsa_apply(yobuild_root: Path) -> dict[str, Any]:
    recipe = deepcopy(_spsa_base(yobuild_root))
    spsa_dir = _spsa_dir(yobuild_root)
    recipe.update(
        {
            "name": "spsa-apply",
            "tune_mode": "apply",
            "tune_file": str(spsa_dir / "param" / "YaneuraOuV940.tune"),
            "params_file": str(spsa_dir / "param" / "YaneuraOuV940.params"),
            "edition": "YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3",
            "version": "V9.41YANE",
            "output_path": "~/shogi/source-tune/YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3_V941apply.exe",
        }
    )
    return recipe


def _bookminer_cpp(yobuild_root: Path) -> dict[str, Any]:
    recipe = _base_recipe(yobuild_root)
    recipe.update(
        {
            "kind": "bookminer_cpp",
            "name": "bookminer-cpp",
            "source_dir": _yaneuraou_source_dir(yobuild_root),
            "bookminer_cpp_source_dir": _bookminer_cpp_source_dir(yobuild_root),
            "version": "",
            "common_cppflags": [],
            "platforms": ["win64"],
            "compiler": "clang++",
            "jobs": 8,
            "cpus": {
                "win64": ["AVX2"],
                "win32": ["AVX2"],
                "mac": ["APPLEAVX2"],
            },
        }
    )
    return recipe
