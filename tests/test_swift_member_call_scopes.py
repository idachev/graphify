from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(files: list[Path], tmp_path: Path) -> dict:
    return extract(files, cache_root=tmp_path / "cache", root=tmp_path / "src", parallel=False)


def _nodes_by_file(result: dict, file: Path, label: str) -> list[str]:
    return [
        n["id"] for n in result["nodes"]
        if n.get("label") == label and str(n.get("source_file", "")).endswith(file.name)
    ]


def _call_targets(result: dict, caller_nid: str) -> set[str]:
    return {
        e["target"] for e in result["edges"]
        if e.get("source") == caller_nid and e.get("relation") == "calls"
    }


def _fixture(base: Path) -> tuple[list[Path], Path, Path, Path]:
    outer = _write(base / "Layout/Outer.swift", (
        "struct Outer {\n"
        "    struct Inner {\n"
        "        func go() {}\n"
        "    }\n"
        "}\n"
    ))
    concatenated = _write(base / "Layout/OuterInner.swift", (
        "struct OuterInner {\n"
        "    func go() {}\n"
        "}\n"
    ))
    user = _write(base / "Views/User.swift", (
        "class User {\n"
        "    var x: Outer.Inner\n\n"
        "    func run() {\n"
        "        x.go()\n"
        "    }\n"
        "}\n"
    ))
    return [outer, concatenated, user], outer, concatenated, user


def test_member_call_through_nested_type_annotation(tmp_path: Path):
    # `x: Outer.Inner; x.go()` binds the go() declared in Outer.Inner.
    files, outer, _concatenated, user = _fixture(tmp_path / "src")
    result = _run(files, tmp_path)

    run_nids = _nodes_by_file(result, user, ".run()")
    assert len(run_nids) == 1
    targets = _call_targets(result, run_nids[0])
    go_nids = _nodes_by_file(result, outer, ".go()")
    assert len(go_nids) == 1
    assert go_nids[0] in targets


def test_member_call_dotted_type_does_not_bind_concatenated_label(tmp_path: Path):
    # The old label key turned `Outer.Inner` into `outerinner`, which bound the
    # unrelated top-level `OuterInner.go()`. It must never be a target.
    files, _outer, concatenated, user = _fixture(tmp_path / "src")
    result = _run(files, tmp_path)

    run_nids = _nodes_by_file(result, user, ".run()")
    assert len(run_nids) == 1
    wrong_go = set(_nodes_by_file(result, concatenated, ".go()"))
    wrong_type = set(_nodes_by_file(result, concatenated, "OuterInner"))
    assert wrong_go
    targets = _call_targets(result, run_nids[0])
    assert not (targets & (wrong_go | wrong_type))
    for e in result["edges"]:
        if e.get("source") == run_nids[0]:
            assert e["target"] not in wrong_go | wrong_type
