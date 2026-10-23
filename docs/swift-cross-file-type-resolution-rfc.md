# RFC: Swift cross-file type resolution

**Date:** 2026-10-23
**Issues:** #4574, #4575 (related open PRs: #4600, #4599, #4639); a new issue is needed for the parts not covered there
**Branch:** v8 (baseline `c588b72d`, released as 0.9.94)
**Status:** proposal for maintainer review. No code yet.

---

## Summary

In a Swift corpus, many type references never reach the declaration of the type they name.
The usual trigger is ordinary Swift: a type declared next to its own `extension` and also
extended in another file. Each referencing file then keeps its own sourceless stub
(`<file>_swift_<name>`), so `graphify affected` and `graphify explain` undercount the users of
structs and enums.

This spec traces the failure to five separate mechanisms and proposes five small fixes. Each
fix can ship as its own PR, ordered by the number of edges it recovers on a real app.

The reproductions below use the installed `graphifyy` 0.9.94. Its `graphify/` package matches
v8 `c588b72d` file for file (`diff -rq` differs only in `extractors/MIGRATION.md`), so the
results hold for the current HEAD.

---

## Problem

### Observed on a real app

The real app is a SwiftUI/TCA macOS app with 567 Swift files, built with
`graphify extract . --code-only --no-label`.

- `graphify affected "PaneLayout"` prints `No unique node match for PaneLayout`. Two sourced
  `PaneLayout` nodes exist: the declaration in `Models/PaneLayout.swift` and an unmerged
  `extension PaneLayout` in `BusinessLogic/LayoutsMigrator.swift`.
- `graphify affected <declaration id>` lists only PaneLayout's own `.encode()` callers. None of
  the 17 references from other files reach it. Each referencing file has its own stub, for
  example `..._reducer_layoutfeature_swift_panelayout`, with an empty `source_file`, and
  `LayoutFeature.State` (L114 `var layout: PaneLayout`) references that stub.
- Every `import Foundation` / `import SwiftUI` prints
  `WARNING: node 'foundation' is minted by two different files`.

Across the corpus, 1,362 `references` edges from Swift files point at a sourceless stub whose
label matches a type the corpus declares. They fall into four groups:

| Bucket | Labels | Stub edges | Cause | Phase |
|---|---|---|---|---|
| One declaration + same-file extension + extension elsewhere | 8 (`Worktree`, `PaneLayout`, `ShellClient`, …) | 601 | §RC1 | 1 |
| Extension only, the type is declared outside the corpus (`View`, `DependencyValues`, …) | 6 | 408 | #4574 | 1 (fail-closed) |
| Several declarations of one name (nested `State`, `Action`, `CodingKeys`, `Delegate`, …) | 40 | 353 | §RC3 | 3 |
| Declaration lost to a tree-sitter-swift parse error | (`RepositoriesFeature`, 119 of the 408 above) | — | out of scope | — |

`RepositoriesFeature.swift` (7,222 lines) parses with a top-level `ERROR` node from L100 to
L900, and that node swallows `@Reducer struct RepositoriesFeature`. This is a grammar
limitation, a separate problem that this spec does not address (see #3542 for that class of
bug).

### Minimal reproduction (3 files + 1 consumer)

```swift
// App/Models/PaneLayout.swift
import Foundation

struct PaneLayout: Equatable, Codable {
  var panes: [Pane]
  struct Pane: Equatable, Codable { var id: Int }
  func encodeAll() -> Data { Data() }
}

extension PaneLayout {            // same-file extension: shares the declaration's id
  var isEmpty: Bool { panes.isEmpty }
}
```

```swift
// App/Logic/LayoutsMigrator.swift
import Foundation

extension PaneLayout {            // cross-file extension
  static func migrated() -> PaneLayout { PaneLayout(panes: []) }
}

enum LayoutsMigrator {
  static func run(_ input: PaneLayout) -> PaneLayout { input }
}
```

```swift
// App/Reducer/LayoutFeature.swift
import Foundation

struct LayoutFeature {
  struct State: Equatable {
    var layout: PaneLayout
    var focused: PaneLayout.Pane?
  }
  func reset(_ state: inout State) {
    state.layout = PaneLayout(panes: [])
  }
}
```

```swift
// App/Logic/Persistence.swift
import Foundation

final class LayoutPersistence {
  var cached: PaneLayout?
  func save(_ layout: PaneLayout) {}
  func load() -> PaneLayout { PaneLayout(panes: []) }
}
```

**Actual** (`graphify extract . --code-only --no-label`, 0.9.94):

```
PaneLayout nodes:
  app_models_panelayout_panelayout        App/Models/PaneLayout.swift     <- declaration
  app_logic_layoutsmigrator_panelayout    App/Logic/LayoutsMigrator.swift <- unmerged extension
  app_logic_persistence_swift_panelayout  ''                              <- stub
  app_reducer_layoutfeature_swift_panelayout ''                           <- stub
edges into PaneLayout:
  .run()             references parameter_type -> app_logic_layoutsmigrator_panelayout
  .run()             references return_type    -> app_logic_layoutsmigrator_panelayout
  LayoutPersistence  references field          -> app_logic_persistence_swift_panelayout
  .save()            references parameter_type -> app_logic_persistence_swift_panelayout
  .load()            references return_type    -> app_logic_persistence_swift_panelayout
  State              references field          -> app_reducer_layoutfeature_swift_panelayout
  (no edge from .reset() or .load() for the PaneLayout(...) constructor calls)
  (no edge from State to Pane; `focused: PaneLayout.Pane?` is recorded as a PaneLayout reference)
stderr:
  WARNING: node 'foundation' is minted by two different files ... (x2)
```

**Expected:**

```
PaneLayout nodes: app_models_panelayout_panelayout only
  (migrated() attached to it as a method; LayoutsMigrator.swift contains it)
edges into PaneLayout: all six references above point at app_models_panelayout_panelayout
  .reset() calls -> PaneLayout   (constructor; INFERRED, see Phase 4)
  State references field -> app_models_panelayout_pane   (the nested Pane)
stderr: no 'foundation' collision warning
```

Two controls narrow the cause down:

- *Remove the same-file `extension PaneLayout`* (keep the cross-file one): every reference
  resolves to the declaration. So the generic rewire works for Swift, and the merge fails only
  because of the shared id.
- *Remove `LayoutsMigrator.swift`* (keep the same-file extension): every reference resolves.

---

## Root cause

The Swift extractor (`graphify/extractors/engine.py`, `_extract_generic` with `_SWIFT_CONFIG`,
`graphify/extract.py:1712`) parses each file with tree-sitter-swift on its own. Swift has no
per-type imports inside a module. Every cross-file type name therefore becomes a sourceless
stub, and the corpus-level passes are expected to repair it later. There is no Swift-specific
type-reference pass like the ones for Java, Go, PHP or C#
(`extract.py:10188-10233`, `extractors/csharp.py:439`). Swift relies only on two generic
passes that match by label: `_merge_swift_extensions` (`extract.py:4147`) and
`_rewire_unique_stub_nodes` (`extract.py:3925`, called at `extract.py:10260`).

### RC1: a declaration that has a same-file extension is never canonical

- Node ids are `_make_id(stem, name)` (`engine.py:6168`; Swift leaves `namespace_stack` empty),
  so `struct PaneLayout` and `extension PaneLayout` in **one** file mint the **same** id.
- When the declaration node has an `extension` child, its id is appended to `swift_extensions`
  (`engine.py:6287-6290`). That includes this shared id.
- `_merge_swift_extensions` skips every id in `extension_nids` when it collects canonical
  candidates (`extract.py:4180-4181`). The real declaration is therefore not a candidate.
  The extension in `LayoutsMigrator.swift` finds zero candidates and is left alone
  (`extract.py:4201-4203`).
- `_rewire_unique_stub_nodes` then sees two sourced, type-like `PaneLayout` nodes
  (`extract.py:3936-3941`), treats the label as ambiguous and keeps every stub
  (`extract.py:3993-4009`). The fail-closed guard is correct here, but its input is wrong.
- `_disambiguate_colliding_node_ids` (`extractors/resolution.py:1060`, called at
  `extract.py:10185`) salts the bare `panelayout` stubs apart by referencing file. That
  produces the `<file>_swift_panelayout` ids seen above. The ids are a symptom, not a cause.

The #4574 author describes the same mechanism (the "Money" case) in a comment on that issue.

### RC2: an extension of an outside type acts as a declaration (#4574)

An extension of a type declared nowhere in the corpus (`extension View`,
`private extension String`) survives the merge with a `source_file` and the label of the
extended type. If one such node exists, it becomes the unique "real" definition for the rewire
(`extract.py:3936-3941`). If several files extend the same type, the label becomes ambiguous
(408 edges on the app above). Either way the result is wrong.

### RC3: Swift type names are reduced to their first segment

- `_swift_collect_type_refs` keeps only the first `type_identifier` of a `user_type`
  (`engine.py:1478-1484`). tree-sitter-swift parses `PaneLayout.Pane` as `user_type` with
  children `type_identifier(PaneLayout) type_identifier(Pane)`, so the reference becomes
  `PaneLayout`. `_swift_user_type_name` behaves the same way for inheritance clauses
  (`engine.py:1424-1432`).
- The same head-only name goes into `swift_type_table` (`engine.py:7332-7333`, `7371`), so
  `focused` is typed as `PaneLayout` and a later `focused.id` would be resolved against the
  wrong type.
- Nested types get no qualified identity. Their id omits the enclosing type (#4575), and their
  label is the bare name, so `real_by_label["State"]` holds 23 candidates on the app.
  `extension LayoutFeature.State` is labelled `LayoutFeature.State`. That dotted label is never
  type-like (`resolution.py:1209-1212`) and never matches the bare-labelled declaration in
  `_merge_swift_extensions`, so extensions of nested types never merge.

### RC4: cross-file constructor calls are dropped

`PaneLayout(panes: [])` is recorded as a raw call with `via_stub: True` whenever the same file
also names `PaneLayout` in a type position (`engine.py:9614-9618`, #3888). The shared call pass
accepts such a call only with import evidence or a C# namespace hit (`extract.py:10798`). A
Swift file never imports a type from its own module, so the call is always dropped. Even
without `via_stub`, the `(caller, target)` pair dedup at `extract.py:10800` suppresses the call
when a `references` edge for the same pair already exists, as in `.load()`. That dedup is
intended and is not changed here.

### RC5: duplicate import-node warnings

`_import_swift` (`extract.py:1683`) returns `(id, label)` pairs. The extractor turns each pair
into a `type=module` anchor whose `source_file` is the **importing** file
(`engine.py:6103-6115`). `_disambiguate_colliding_node_ids` exempts `type=module`
(`resolution.py:1076`), but `deduplicate_entities` → `_report_id_collision`
(`dedup.py:1041-1042`, `dedup.py:612-650`) does not. The id `foundation` carries no path
prefix, so `_defines_id` (`dedup.py:319`) is false for every copy, and the warning fires once
per extra importing file. No data is lost, since these are the same module. The warning is
noise, but it trains users to ignore a real data-loss warning.

### How other languages avoid this

- **Go** has package-wide visibility like Swift and folds per-file copies of one type by
  package directory (`_merge_go_package_types`, `resolution.py:3361`). It resolves
  `pkg.Type` through import paths (`_resolve_go_type_references`, `resolution.py:3612`).
- **Java, C# and PHP** resolve type references with package, namespace or `using` evidence
  before the generic rewire runs (`extract.py:10204-10218`, `extract.py:10274-10285`).
- **Python** resolves imports and repoints type references in
  `_resolve_cross_file_imports` (`resolution.py:2913`).
- **Rust** marks the true declaration (`_rust_declaration_count`) so the rewire can tell a
  declaration from an `impl` block (`extract.py:3979-3992`). This is the closest precedent
  for Phase 1.

Swift has none of these. It needs the Rust-style "which node is the declaration" marker. For
nested types it needs the Go/Java-style "resolve with scope evidence before the bare-label
rewire" pass.

---

## Relationship to open PRs

| | RC1 (repro above) | RC1 control: same-file extension only | RC2 `extension String` | Nested `Outer.Inner` from another file |
|---|---|---|---|---|
| v8 `c588b72d` | stubs (fail) | resolves | absorbs refs (fail) | refs `Outer` (fail) |
| v8 + #4600 | stubs (fail) | **stubs (regression)** | fixed | — |
| v8 + #4599 | — | — | — | refs `Outer` (fail); in-file bare `Inner` still resolves |

These were checked by applying each PR's diff to a scratch worktree of `c588b72d`.

**#4600 regresses the common case.** It sets `metadata.swift_extension = True` on
`class_nid` whenever a declaration has an `extension` child. In the same-file case that id is
the real declaration, so the rewire skips the only definition. The 5 references in the
control fixture move from `app_models_panelayout_panelayout` back onto stubs. In the
`struct X` + `extension X` in `X.swift` layout this affects every such type, even when no
other file extends it. Phase 1 below is the variant that the #4574 author proposed in the
issue thread: mark only **extension-only** ids. We suggest coordinating with #4600 instead of
opening a competing PR.

**#4599** gives nested types a parent-qualified id. Phase 3 builds on it and does not replace
it. #4599 alone does not resolve `Outer.Inner` written in another file (RC3).

**#4639** (file-scope `typealias` nodes) is complementary. Phase 2 resolves references to those
alias nodes the same way as references to any other declaration.

---

## Proposed design

Five phases. Each one has a regression test that fails on `c588b72d`, and each can ship on its
own. All of them are deterministic, fail closed on ambiguity, and never bind across language
families (the existing `_lang_family` guards stay).

### Phase 1: mark real declarations (RC1, RC2)

Extractor (`engine.py`, class walk around `6257-6290`):

- Keep a per-file set `swift_declared_nids`. An id goes into it when the file has a
  `class_declaration` whose keyword (`_swift_declaration_keyword`) is
  `class/struct/enum/actor`, or has a `protocol_declaration`.
- Emit `swift_extensions` records as `{"nid", "label", "declared": nid in swift_declared_nids}`.
- Stamp sourced Swift class nodes that are **extension-only** with
  `metadata.swift_extension_only = True`. Stamp after the walk, because Swift allows the
  extension to come before the declaration.

Corpus (`_merge_swift_extensions`, `extract.py:4147`):

- Build `label_to_canonical` from declared ids, including ids that also appear in
  `swift_extensions`. Never remap a declared id.
- Remap only extension-only ids. A survivor (no candidate, or several) keeps the
  `swift_extension_only` marker.

Rewire (`_rewire_unique_stub_nodes`, `extract.py:3936`):

- A node with `swift_extension_only` is not added to `real_by_label`. Its references stay on
  stubs, as for `Int` or `Bool`. This is fail-closed and matches #4574's expected behaviour.

Incremental builds: add `swift_extension_only` to the context-node marker lists in
`cli.py:4342` and `watch.py:2036`, so `graphify update` keeps it for unchanged files. Add it to
`resolution_context_nodes` in the same way as `_rust_declaration_count`.

Expected effect on the app: the 601-edge bucket resolves. The extension-only nodes for `View`,
`DependencyValues` and similar stop attracting references.

### Phase 2: Swift module symbol table and type-reference pass (RC3, qualified names)

A new pass, `_resolve_swift_type_references(per_file, paths, all_nodes, all_edges, ...)`,
modelled on `_resolve_go_type_references`. It runs in the same slot as the Java, Go and PHP
passes: after `_merge_swift_extensions` and `_disambiguate_colliding_node_ids`, and **before**
`_rewire_unique_stub_nodes` (`extract.py:10186-10260`).

Extractor inputs. These are new per-file records, so `_AST_CACHE_SCHEMA` (`cache.py:40`) gets
a bump:

- `swift_type_decls`: `[{nid, qualified: "LayoutFeature.State", label: "State", kind}]`,
  built from the nesting stack during the walk. Extensions of nested types
  (`extension LayoutFeature.State`) record the path that they extend.
- Reference edges keep the full written path. `_swift_collect_type_refs` returns every
  `type_identifier` segment of a `user_type`. The stub for `PaneLayout.Pane` is labelled
  `PaneLayout.Pane`, not `PaneLayout`, and the edge records the lexical scope it was written
  in, for example `_swift_scope: "LayoutFeature.State"`. This also fixes the
  `swift_type_table` value. Generic arguments keep `context=generic_arg` as today.

Symbol table:

```
qualified name ("PaneLayout", "PaneLayout.Pane", "LayoutFeature.State") -> [declared nid, ...]
```

The table holds declared Swift nodes only (Phase 1 marker), and in the first version it covers
the whole corpus (see open question Q1 on modules).

Resolution of a reference `A.B.C` written in scope `S1.S2`, applied the same way to
annotations, parameter and return types, inheritance/conformance clauses, generic arguments
and extension targets:

1. Lexical lookup, innermost first: try `S1.S2.A.B.C`, then `S1.A.B.C`, then `A.B.C`. This
   mirrors Swift name lookup, where a bare `State` inside `LayoutFeature` or inside
   `extension LayoutFeature` means `LayoutFeature.State`.
2. The first level that has exactly one candidate wins. The edge keeps `EXTRACTED`, since the
   binding follows from the text and the language rules.
3. A level that has two or more candidates stops the search without binding. The edge stays on
   its stub (fail-closed) and gets `_swift_ambiguous: true` for diagnostics.
4. If no level matches, the name is external (SwiftUI, Foundation, a package outside the
   corpus). The stub stays. It is never folded onto a same-named extension (Phase 1).

Extensions of nested types resolve with the same table, so `extension LayoutFeature.State`
merges into the nested declaration. This replaces the bare-label match in
`_merge_swift_extensions` for dotted labels.

Edges that the pass repoints leave their stubs unreferenced. Those stubs are dropped in the
same way as in `_resolve_go_type_references` (`resolution.py:3705-3712`). Stubs that are still
referenced stay as they are today.

### Phase 3: nested type identity (builds on #4599)

Use #4599's `_make_id(parent_class_nid, name)` for nested Swift types. Phase 2's table makes
the bare in-body reference (`func f(_ s: State)`) resolve through lexical lookup, not through
the `ensure_named_node` same-file shortcut. This also covers the same-file member/type id
collision that the #4575 author mentions. If #4599 merges first, this phase only adds tests.

### Phase 4: constructor calls with module-scope evidence (RC4)

In the shared call pass (`extract.py:10798`), a Swift `via_stub` raw call whose callee resolves
through the Phase 2 table to **exactly one** declared Swift type in the caller's module counts
as having evidence. It emits `calls` with `context="constructor"` and `confidence=INFERRED`
(0.85). The call is inferred and not extracted, because `Foo(...)` could also be a free
function or a static `callAsFunction`. Ambiguous or external callees stay unresolved. This is
the Swift counterpart of the implicit-scope exemption that C, C++ and Ruby already have
(`extract.py:10785-10797`), restricted to a unique declared type.

### Phase 5: no collision warning for module anchors (RC5)

In `deduplicate_entities`, skip `_report_id_collision` for a node group whose members are all
`type == "module"` (or `"namespace"`), mirroring `resolution.py:1076`. As an alternative,
`_import_swift` anchors could be emitted sourceless with an `origin_file`. That would change
`build.py` validation and the `--no-cluster` output, so we prefer the one-line skip.

---

## Impact

### Node ids and graph shape

- Phase 1 changes no ids. It removes duplicate sourced nodes (extension nodes folded into
  their declaration) and sourceless stubs that become unreferenced.
- Phase 2 changes stub labels from `PaneLayout` to `PaneLayout.Pane` for qualified references
  that still do not resolve. Their ids follow from the label through `_make_id`.
- Phase 3 changes nested-type ids (as #4599 does). Saved queries and `--seed` ids that name a
  nested Swift type stop matching. This belongs in the CHANGELOG.
- `graphify affected` / `explain` for a declared Swift type report more dependants. This is
  the purpose of the change, and `GRAPH_REPORT.md` god-node rankings will move on Swift
  corpora.

### Cache and incremental builds

- Bump `_AST_CACHE_SCHEMA` once for Phase 1+2 (new per-file records and labels).
- The new pass must read `resolution_context_nodes` / `resolution_context_edges`, the way the
  Go pass does, so `graphify update` on a single file resolves as much as a full build. This
  belongs in the regression test list ("Cross-file edges must be preserved during incremental
  updates").
- The shrink guard is unaffected in practice: the node count drops only by duplicates and
  unreferenced stubs. A large Swift corpus may still trip it on the first rebuild after the
  upgrade. The CHANGELOG entry should mention `--force` / `GRAPHIFY_FORCE=1`.

### Backwards compatibility

- No CLI or flag changes.
- Non-Swift corpora: every new code path is gated on `.swift` / `tree_sitter_swift` or on the
  new markers. Phase 5 only removes a warning.
- Swift↔ObjC folding (#1556) stays: Phase 1 keeps the `native` family gate in
  `_merge_swift_extensions`. The new symbol table contains Swift declarations only, and ObjC
  classes still reach Swift extensions through the existing merge.

### Performance

- Phase 1: O(nodes) set membership, no extra parse.
- Phase 2: one dict build over declared Swift nodes, plus at most `depth(scope)` lookups per
  Swift reference edge, which in practice means at most 3-4. It needs no reparse, because
  scope and full path are recorded at extraction time. On the app above, with 39,656 edges and
  13,962 nodes, this costs less than the existing rewire pass.
- No change to the per-file extraction cost beyond keeping a scope stack.

---

## Test plan

The tests follow `tests/test_swift_cross_file_calls.py`: `_write(...)` the files into
`tmp_path`, call `extract(files, cache_root=tmp_path / "cache", root=tmp_path / "src",
parallel=False)`, and assert on `_edge_labels`. Each test below fails on `c588b72d`.

Phase 1 (`tests/test_swift_cross_file_calls.py`):

- `test_declaration_with_same_file_extension_absorbs_cross_file_extension`: the 3+1 file repro.
  It asserts one `PaneLayout` node, `.migrated()` as its method, and every `references` edge
  onto it.
- `test_same_file_extension_alone_still_resolves_references`: the RC1 control. It passes on v8,
  and it guards against the #4600 regression.
- `test_extension_of_outside_type_does_not_absorb_references`: the #4574 repro.
- `test_extension_only_type_extended_in_two_files_stays_unresolved`: `extension View` in two
  files, `var body: some View` elsewhere. Asserts no edge onto either extension node.
- `test_extension_only_marker_survives_incremental_update`: `_rebuild_code` with the extension
  file only in the resolution context.

Phase 2 (new `tests/test_swift_type_resolution.py`):

- `test_qualified_nested_reference_resolves_to_nested_type`: `var focused: PaneLayout.Pane?`
  from another file reaches `Pane`.
- `test_bare_nested_name_resolves_lexically_in_extension`:
  `extension LayoutFeature { func f(_ s: State) }` in another file reaches
  `LayoutFeature.State` and not another reducer's `State`.
- `test_ambiguous_bare_name_stays_on_stub`: two top-level `Config` in different files. No
  edge, and the stub is kept.
- `test_extension_of_nested_type_merges`: `extension LayoutFeature.State { ... }` in another
  file. Its methods attach to the nested declaration.
- `test_conformance_and_generic_args_resolve`:
  `struct A: SomeProtocol` and `var x: [String: PaneLayout]` across files.
- `test_type_table_records_full_path`: `swift_type_table["focused"] == "PaneLayout.Pane"`.
- `test_no_cross_language_binding`: a Swift `Store` reference with a TS `class Store` in the
  corpus. This complements `test_extension_does_not_merge_into_same_named_foreign_type`.

Phase 4: `test_cross_file_constructor_call_emits_inferred_call`, plus a negative test
for an ambiguous callee.

Phase 5 (`tests/test_swift_import_resolution.py`):
`test_shared_module_import_prints_no_collision_warning`, which uses `capsys` on two files that
both `import Foundation`.

Full gate, per CONTRIBUTING: `uv run pytest tests -q`, `uv run ruff check .`, `uv run pyright`.
Each phase's regression test must be shown to fail without the fix.

---

## Out of scope

- tree-sitter-swift parse errors that swallow declarations (the `@Reducer` struct above, #3542).
- Member-call resolution through nested receivers (`state.layout.encodeAll()`, depth > 1).
- Cross-module resolution with `@testable import` evidence (see Q1).
- Protocol-extension default implementations and conditional conformances
  (`extension Array: P where Element == X`). These are left as they are today.

---

## Open questions for the maintainer

1. **Module boundary.** Should the symbol table be scoped per Swift module (a SwiftPM target
   from `Package.swift`, `Sources/<Target>/` and `Tests/<Target>/`, or an Xcode target), or
   cover the whole corpus with fail-closed ambiguity? The corpus-wide table is simpler and
   matches what the rewire does today. Per-module scoping would avoid false ambiguity between
   an app and its test target, which declare many same-named helpers.
2. **#4600 vs Phase 1.** Would you rather have #4600 amended to mark only extension-only ids,
   or a separate PR that replaces it?
3. **Confidence for lexical resolution.** Is `EXTRACTED` right for a binding derived from Swift
   lookup rules, or should cross-file Swift type bindings be `INFERRED` like Phase 4?
4. **Stub labels.** Should a qualified unresolved reference (`Foo.Bar`) keep the full dotted
   label, as Go's external stubs do, or keep the head for continuity with existing graphs?
5. **Ordering with #4599/#4639.** Should these land first, so Phase 3 shrinks to tests only?
