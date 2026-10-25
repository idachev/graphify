"""Swift extensions of a sugared or standard generic type (`[T]`, `[K: V]`, `T?`, `Optional<T>`).

See docs/swift-cross-file-type-resolution-rfc.md. `extension [ScriptDefinition]` extends
`Array`, an external type, not `ScriptDefinition`. Read as text, `[ScriptDefinition]`
slugs to the struct's own id, so in the struct's file the extension folded onto the
struct and stamped a second `_swift_qualified` path (`[ScriptDefinition]`) on it; the
lexical lookup then saw the struct as ambiguous and every reference to it stayed a stub.

The style follows tests/test_swift_type_resolution.py: write the files into ``tmp_path``,
call ``extract(...)`` and name each target node by label and ``source_file``.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


# ── helpers ───────────────────────────────────────────────────────────────────

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _extract(files: list[Path], tmp_path: Path) -> dict:
    return extract(files, cache_root=tmp_path / "cache", root=tmp_path / "src", parallel=False)


def _sf(node: dict) -> str:
    return str(node.get("source_file") or "").replace("\\", "/")


def _by_id(result: dict) -> dict[str, dict]:
    return {n["id"]: n for n in result["nodes"]}


def _node(result: dict, label: str, suffix: str) -> str:
    """The id of the one sourced node ``label`` from ``suffix``."""
    found = [
        n["id"] for n in result["nodes"]
        if n.get("label") == label and _sf(n).endswith(suffix)
    ]
    assert len(found) == 1, f"expected one {label!r} in {suffix}, got {found}"
    return found[0]


def _targets(result: dict, source: str, relation: str = "references") -> set[str]:
    return {
        e["target"] for e in result["edges"]
        if e.get("source") == source and e.get("relation") == relation
    }


def _contains_sources(result: dict, nid: str) -> list[str]:
    return [
        e["source"] for e in result["edges"]
        if e.get("relation") == "contains" and e.get("target") == nid
    ]


def _assert_struct_untouched(result: dict, struct_suffix: str) -> str:
    """The struct keeps only its own path, one `contains` edge and no extension method."""
    struct = _node(result, "ScriptDefinition", struct_suffix)
    assert _by_id(result)[struct].get("_swift_qualified") == ["ScriptDefinition"]
    assert len(_contains_sources(result, struct)) == 1
    assert not _targets(result, struct, "method")
    return struct


def _assert_reference_binds(result: dict, struct: str) -> None:
    user = _node(result, "User", "C.swift")
    assert struct in _targets(result, user)


def _assert_external_extension(result: dict, label: str, suffix: str) -> None:
    ext = _node(result, label, suffix)
    node = _by_id(result)[ext]
    assert node.get("_swift_extension_only") is True
    assert node.get("_swift_qualified") == [label]
    assert _targets(result, ext, "method"), "the extension keeps its own methods"


_STRUCT = "struct ScriptDefinition {\n    let name: String\n}\n"
_USER = "struct User {\n    let def: ScriptDefinition\n}\n"


# ── array sugar ───────────────────────────────────────────────────────────────

def test_array_sugar_extension_does_not_make_element_type_ambiguous(tmp_path):
    src = tmp_path / "src"
    files = [
        _write(src / "A.swift", _STRUCT),
        _write(src / "B.swift", "extension [ScriptDefinition] {\n    func x() {}\n}\n"),
        _write(src / "C.swift", _USER),
    ]
    result = _extract(files, tmp_path)

    struct = _assert_struct_untouched(result, "A.swift")
    _assert_reference_binds(result, struct)
    _assert_external_extension(result, "Array", "B.swift")


def test_array_sugar_extension_in_the_element_types_file(tmp_path):
    # The case that regressed: the extension's text slugged to the struct's own id.
    src = tmp_path / "src"
    files = [
        _write(src / "A.swift", _STRUCT + "extension [ScriptDefinition] {\n    func x() {}\n}\n"),
        _write(src / "C.swift", _USER),
    ]
    result = _extract(files, tmp_path)

    struct = _assert_struct_untouched(result, "A.swift")
    _assert_reference_binds(result, struct)
    _assert_external_extension(result, "Array", "A.swift")


def test_dictionary_sugar_extension_in_the_value_types_file(tmp_path):
    src = tmp_path / "src"
    files = [
        _write(src / "A.swift",
               _STRUCT + "extension [String: ScriptDefinition] {\n    func x() {}\n}\n"),
        _write(src / "C.swift", _USER),
    ]
    result = _extract(files, tmp_path)

    struct = _assert_struct_untouched(result, "A.swift")
    _assert_reference_binds(result, struct)
    _assert_external_extension(result, "Dictionary", "A.swift")


# ── optional ──────────────────────────────────────────────────────────────────

def test_optional_sugar_extension_does_not_make_wrapped_type_ambiguous(tmp_path):
    src = tmp_path / "src"
    files = [
        _write(src / "A.swift", _STRUCT + "extension ScriptDefinition? {\n    func x() {}\n}\n"),
        _write(src / "C.swift", _USER),
    ]
    result = _extract(files, tmp_path)

    struct = _assert_struct_untouched(result, "A.swift")
    _assert_reference_binds(result, struct)
    _assert_external_extension(result, "Optional", "A.swift")


def test_optional_generic_extension_does_not_make_wrapped_type_ambiguous(tmp_path):
    # `Optional<T>` is a user_type whose path is already `Optional`: a guard, not a repro.
    src = tmp_path / "src"
    files = [
        _write(src / "A.swift", _STRUCT),
        _write(src / "B.swift", "extension Optional<ScriptDefinition> {\n    func x() {}\n}\n"),
        _write(src / "C.swift", _USER),
    ]
    result = _extract(files, tmp_path)

    struct = _assert_struct_untouched(result, "A.swift")
    _assert_reference_binds(result, struct)
    ext = next(
        n for n in result["nodes"]
        if _sf(n).endswith("B.swift") and n.get("_swift_extension_only")
    )
    assert ext.get("_swift_qualified") == ["Optional"]
