"""Swift receiver scopes when one name is written several times in a file (ЗД28).

Decision АР25 keeps the per-file `swift_type_table` flat: a name whose writes name
different types gets `scopes[name] = None` and binds nothing. Writes that all mean
the same type are not a conflict, even from different enclosing types (`let
dragModel: PaneTabDragModel` in two views) or spelled differently (`Coordinator`
inside `Outline`, `Outline.Coordinator` elsewhere); `x.m()` must still bind to that
type's method. Reproductions come from supacode (PaneTabStripView.swift,
AppShortcuts.swift, FileExplorerOutlineView.swift, LayoutFeatureTests.swift).
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract, extract_swift


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _extract(path: Path, tmp_path: Path) -> dict:
    return extract([path], cache_root=tmp_path / "cache", root=tmp_path / "src", parallel=False)


def _owners_called(result: dict, caller: str, callee: str) -> set[str]:
    """Labels of the types whose method ``callee`` the method ``caller`` calls."""
    by_id = {n["id"]: n for n in result["nodes"]}
    owner_of = {
        e["target"]: by_id.get(e["source"], {}).get("label", "")
        for e in result["edges"] if e.get("relation") == "method"
    }
    return {
        owner_of.get(e["target"], "?")
        for e in result["edges"]
        if e.get("relation") == "calls"
        and by_id.get(e["source"], {}).get("label") == caller
        and by_id.get(e["target"], {}).get("label") == callee
    }


def test_same_type_twice_in_one_scope_binds(tmp_path: Path):
    # (1) `m` is written twice with the same type in the same enclosing type. No
    # same-named decoy method here: for a receiver with one distinct write the
    # in-file bind is still the bare-label map (unchanged by ЗД28).
    path = _write(tmp_path / "src/Model.swift", (
        "final class Model {\n"
        "    func reset() {}\n"
        "}\n"
        "struct Panel {\n"
        "    func first() { let m = Model(); m.reset() }\n"
        "    func second(_ m: Model) { m.reset() }\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    assert table["scopes"]["m"] == "Panel"

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".first()", ".reset()") == {"Model"}
    assert _owners_called(result, ".second()", ".reset()") == {"Model"}


def test_same_type_in_two_enclosing_types_binds(tmp_path: Path):
    # (2a) PaneTabStripView.swift: `let dragModel: PaneTabDragModel` in several
    # views; `dragModel.reset()` must bind PaneTabDragModel.reset.
    path = _write(tmp_path / "src/PaneTabStripView.swift", (
        "final class PaneTabDragModel {\n"
        "    func startDrag() {}\n"
        "    func reset() {}\n"
        "}\n"
        "struct PaneTabStrip {\n"
        "    let dragModel: PaneTabDragModel\n"
        "    func performDrop() { dragModel.reset() }\n"
        "}\n"
        "struct PaneTabView {\n"
        "    let dragModel: PaneTabDragModel\n"
        "    func endDragIfSource() { dragModel.reset() }\n"
        "}\n"
        "final class Other {\n"
        "    func reset() {}\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    # The writes agree on the type; the common enclosing path is the file scope.
    assert "dragModel" not in table["scopes"]
    assert table["table"]["dragModel"] == "PaneTabDragModel"

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".performDrop()", ".reset()") == {"PaneTabDragModel"}
    assert _owners_called(result, ".endDragIfSource()", ".reset()") == {"PaneTabDragModel"}


def test_same_type_in_an_extension_and_its_own_type_binds(tmp_path: Path):
    # (2a) AppShortcuts.swift: `shortcut: AppShortcut` inside AppShortcut and inside
    # `extension View`.
    path = _write(tmp_path / "src/AppShortcuts.swift", (
        "struct AppShortcut {\n"
        "    func effective(from overrides: Int) -> Int { overrides }\n"
        "    func isSame(_ shortcut: AppShortcut) -> Bool {\n"
        "        shortcut.effective(from: 1) == 1\n"
        "    }\n"
        "}\n"
        "protocol View {}\n"
        "extension View {\n"
        "    func help(_ shortcut: AppShortcut) -> Int { shortcut.effective(from: 2) }\n"
        "}\n"
    ))
    result = _extract(path, tmp_path)
    assert _owners_called(result, ".isSame()", ".effective()") == {"AppShortcut"}
    assert _owners_called(result, ".help()", ".effective()") == {"AppShortcut"}


def test_nested_type_spelled_two_ways_binds_its_own_method(tmp_path: Path):
    # (2b) FileExplorerOutlineView.swift: `coordinator: Coordinator` inside Outline
    # and `coordinator: Outline.Coordinator?` in another class mean one type. The
    # decoy `Cell.beginRename` is declared last, so the bare-label map holds it; the
    # call must not bind to it.
    path = _write(tmp_path / "src/FileExplorerOutlineView.swift", (
        "struct Outline {\n"
        "    final class Coordinator {\n"
        "        func removeKeyMonitor() {}\n"
        "        func beginRename(item: Int) {}\n"
        "    }\n"
        "    func dismantle(coordinator: Coordinator) { coordinator.removeKeyMonitor() }\n"
        "}\n"
        "final class OutlineNSView {\n"
        "    var coordinator: Outline.Coordinator?\n"
        "    func keyDown() { coordinator?.beginRename(item: 1) }\n"
        "    func moved() { coordinator?.removeKeyMonitor() }\n"
        "}\n"
        "final class Cell {\n"
        "    func beginRename(commit: Bool) {}\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    assert table["scopes"]["coordinator"] is not None

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".dismantle()", ".removeKeyMonitor()") == {"Coordinator"}
    assert _owners_called(result, ".moved()", ".removeKeyMonitor()") == {"Coordinator"}
    assert _owners_called(result, ".keyDown()", ".beginRename()") <= {"Coordinator"}


def test_nested_type_from_sibling_nested_types_keeps_common_scope(tmp_path: Path):
    # (2c) LayoutFeatureTests.swift: `recorder: ContentRecorder` in the test class
    # and in two of its nested types; ContentRecorder is nested in the test class.
    path = _write(tmp_path / "src/LayoutFeatureTests.swift", (
        "final class LayoutFeatureTests {\n"
        "    final class ContentRecorder {\n"
        "        func make() {}\n"
        "    }\n"
        "    struct Harness { let recorder: ContentRecorder }\n"
        "    struct StoreBundle { let recorder: ContentRecorder }\n"
        "    func makeStore(_ recorder: ContentRecorder) { recorder.make() }\n"
        "}\n"
        "final class Elsewhere {\n"
        "    func make() {}\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    assert table["scopes"]["recorder"] == "LayoutFeatureTests"

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".makeStore()", ".make()") == {"ContentRecorder"}


def test_two_types_for_one_name_bind_nothing(tmp_path: Path):
    # (3) АР25: `host` is a WorktreeContentHost in one method and a RemoteHost in
    # another (WorktreeTerminalManager.swift); `host.tabID(...)` must not bind the
    # manager's own same-named function or either host type through the flat table.
    path = _write(tmp_path / "src/WorktreeTerminalManager.swift", (
        "final class WorktreeContentHost {\n"
        "    func tabID(containing id: Int) -> Int { id }\n"
        "}\n"
        "struct RemoteHost {\n"
        "    func tabID(containing id: Int) -> Int { id }\n"
        "}\n"
        "final class WorktreeTerminalManager {\n"
        "    func tabID(forWorktreeID id: Int) -> Int { id }\n"
        "    func split(host: WorktreeContentHost) -> Int { host.tabID(containing: 1) }\n"
        "    func kill(host: RemoteHost) -> Int { 0 }\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    assert table["scopes"]["host"] is None

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".split()", ".tabID()") == set()


def test_same_type_name_meaning_two_nested_types_binds_nothing(tmp_path: Path):
    # (3) АР25: the same written type `State` means A.State in A and B.State in B.
    path = _write(tmp_path / "src/Features.swift", (
        "struct A {\n"
        "    struct State { func go() {} }\n"
        "    func runA(_ x: State) { x.go() }\n"
        "}\n"
        "struct B {\n"
        "    struct State { func go() {} }\n"
        "    func runB(_ x: State) { x.go() }\n"
        "}\n"
    ))
    table = extract_swift(path)["swift_type_table"]
    assert table["scopes"]["x"] is None

    result = _extract(path, tmp_path)
    assert _owners_called(result, ".runA()", ".go()") == set()
    assert _owners_called(result, ".runB()", ".go()") == set()
