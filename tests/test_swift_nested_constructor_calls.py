"""Same-file Swift constructor calls to nested types bind to the caller's own type (АР24).

The extractor's bare-label map holds one node per label, so `State()` inside
`A.runA()` and inside `B.runB()` used to bind to one `State`. A label that names
a nested type, or 2+ types of the file, now stays a raw call and extract() binds
it through the caller's lexical scope (INFERRED, `context == "constructor"`).
A unique top-level type keeps the extractor's EXTRACTED same-file binding.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _node_id(result: dict, label: str) -> str:
    matches = [n["id"] for n in result["nodes"] if n.get("label") == label]
    assert len(matches) == 1, (label, matches)
    return matches[0]


def _parent_label(result: dict, nid: str) -> str:
    labels = {n["id"]: n.get("label", "") for n in result["nodes"]}
    parents = [
        labels.get(e["source"], "") for e in result["edges"]
        if e.get("relation") == "contains" and e.get("target") == nid
    ]
    assert len(parents) == 1, (nid, parents)
    return parents[0]


def _calls_from(result: dict, caller_nid: str) -> list[dict]:
    return [
        e for e in result["edges"]
        if e.get("relation") == "calls" and e.get("source") == caller_nid
    ]


def test_same_file_nested_constructor_binds_own_type(tmp_path: Path):
    src = _write(tmp_path / "src/Features.swift", (
        "struct A {\n"
        "    struct State {}\n"
        "    func runA() {\n"
        "        State()\n"
        "    }\n"
        "}\n"
        "\n"
        "struct B {\n"
        "    struct State {}\n"
        "    func runB() {\n"
        "        State()\n"
        "    }\n"
        "}\n"
    ))
    result = extract([src], cache_root=tmp_path / "cache")

    state_nids = [n["id"] for n in result["nodes"] if n.get("label") == "State"]
    assert len(set(state_nids)) == 2, state_nids

    for caller_label, owner_label in ((".runA()", "A"), (".runB()", "B")):
        calls = _calls_from(result, _node_id(result, caller_label))
        assert len(calls) == 1, (caller_label, calls)
        edge = calls[0]
        assert edge["target"] in state_nids
        assert _parent_label(result, edge["target"]) == owner_label
        assert edge["confidence"] == "INFERRED"
        assert edge.get("context") == "constructor"

    run_a_target = _calls_from(result, _node_id(result, ".runA()"))[0]["target"]
    run_b_target = _calls_from(result, _node_id(result, ".runB()"))[0]["target"]
    assert run_a_target != run_b_target


def test_same_file_top_level_constructor_stays_extracted(tmp_path: Path):
    src = _write(tmp_path / "src/Foo.swift", (
        "struct Foo {}\n"
        "\n"
        "func make() {\n"
        "    Foo()\n"
        "}\n"
    ))
    result = extract([src], cache_root=tmp_path / "cache")

    calls = _calls_from(result, _node_id(result, "make()"))
    assert len(calls) == 1, calls
    edge = calls[0]
    assert edge["target"] == _node_id(result, "Foo")
    assert edge["confidence"] == "EXTRACTED"
    assert edge.get("context") == "call"
