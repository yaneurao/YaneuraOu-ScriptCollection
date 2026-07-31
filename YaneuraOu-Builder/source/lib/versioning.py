from __future__ import annotations


def package_version_from_engine_version(version: str) -> str:
    text = version.strip()
    if text.startswith(("V", "v")):
        prefix = text[0]
        rest = text[1:]
    else:
        prefix = ""
        rest = text

    numeric = []
    index = 0
    while index < len(rest) and (rest[index].isdigit() or rest[index] == "."):
        numeric.append(rest[index])
        index += 1

    if not numeric:
        return text

    compact = "".join(char for char in numeric if char != ".")
    if not compact:
        return text
    return prefix + compact + rest[index:]
