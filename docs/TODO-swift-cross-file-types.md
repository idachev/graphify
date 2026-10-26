# TODO: Swift cross-file type resolution follow-ups

Open points left by the Swift cross-file type resolution work
(`docs/swift-cross-file-type-resolution-rfc.md`). Line numbers are checked against branch
`captain/20261023-swift-xfile-types` at `422eee02`.

## ТД1 Data loss on `graphify update` from a stem-id collision

**Evidence.** Pre-existing, all languages (seen with Swift and Python). A changed file and an
unchanged file whose paths slug to the same stem (`Cfg_Helpers.swift` and `Cfg/Helpers.swift`
both give `cfg_helpers`) mint the same node id for a same-named declaration. When only the new
file is re-extracted, the fresh node and the unchanged node collide in the merge, so the
incremental graph loses a node that a full build of the same files keeps (review ЗД2 r2 F1;
the repro below was not re-run for this note). The merge appends the fresh nodes to the kept
existing ones (`graphify/build.py:2262`) and `dedupe_nodes` keeps the last node per id
(`graphify/build.py:892`).

**Repro steps.**

1. In an empty directory, write `Cfg_Helpers.swift` with `struct Config { func original() {} }`
   and `Other.swift` with `struct Config {}`.
2. Run `graphify.extract.extract([Cfg_Helpers.swift, Other.swift], root=dir)` and save the result
   as `graph.json`.
3. Add `Cfg/Helpers.swift` with `extension Config { func extra() {} }`.
4. Extract only the new file, passing as `resolution_context_nodes` the full build's nodes that
   have a `source_file` (`id`, `label`, `source_file`, `file_type`, `type` plus the markers
   `_callable`, `_callable_class`, `_swift_extension_only`, `_swift_qualified`,
   `_rust_declaration_count`) and as `resolution_context_edges` its `contains` edges, as
   `graphify update` does.
5. Merge with `graphify.build.merge_raw_extraction(new, graph.json, root=dir)` and run
   `dedupe_nodes` on the merged nodes.
6. Run a full `extract` over all three files and compare the `Config`, `Other`, `original` and
   `extra` nodes (id, source file) of step 5 with the full build.
7. Python variant: `Cfg_Helpers.py` with `class Config` / `def original(self)`, `Other.py` with
   `class Other`, and the new `Cfg/Helpers.py` with `class Config` / `def extra(self)`.

**Why not here.** Owner decision ДЗ1: the bug is in the incremental merge and the id scheme
shared by every language, not in the Swift type pass, so it goes to a separate issue.

## ТД2 Unchanged consumers are not re-resolved after a declaration changes

**Evidence.** On `graphify update` the resolution context forwarded to `extract()` carries only
structural edges (`contains`, `method`, `inherits`, `imports`, `imports_from`;
`graphify/cli.py:4382-4384`), no `references` or `calls`. References in unchanged files keep
their old binding when a declaration is added, moved, deleted or becomes ambiguous elsewhere,
until the next full `graphify extract`. Same for every language pass (review ЗД1 r1 F5).

**Why not here.** It needs a dependency-driven re-extraction of unchanged consumers in the
incremental pipeline for all languages, outside the scope of a Swift resolver change.

## ТД3 A Python nested class id can equal a top-level `Outer_State` id

**Swift part fixed (ЗД32).** A nested Swift type whose id a declaration of another scope holds
(top-level `Outer_State` or `outer_state()`, or `Outer.A_B` for `Outer.A.B`) is now salted,
and the shallower declaration keeps the plain id (`_swift_nested_type_yields`,
`graphify/extractors/engine.py`; tests in `tests/test_swift_nested_id_collision.py`;
`_AST_CACHE_SCHEMA` 24).

**Evidence (Python, open).** A nested class gets `_make_id(parent_class_nid, "State")`
(`graphify/extractors/engine.py`, the `tree_sitter_python and parent_class_nid` branch), the
same id as a top-level `class Outer_State` or `def outer_state()` in the same module: the
second node is dropped and its methods hang under the nested class (review ЗД12 r1 F4; repro
P1/P2 in `tmp/captain/20261023-145049/reports/reval-ТД2-ТД3.md`). It exists on upstream v8
too. A fix must also give the decorator edge the salted owner id: a change only where the
class id is minted leaves the decorator edge on the unsalted id, which then belongs to the
top-level declaration (Codex F8); test it with a decorated nested class.

**Why not here.** Pre-existing upstream bug; it goes upstream as its own change.

**Swift, open (ЗД32 r1 F3).** A qualified extension of a nested type declared in another file
takes `_make_id(stem, "Outer.State")`, the id of a file-level `Outer_State` in the extension's
file: `Extra.swift` = `extension Outer.State { func extra() {} }` + `struct Outer_State { func top() {} }`
drops `Outer_State`, hangs `.top()` under the extension node, and `Outer.State` in a third file stays
an unresolved stub. 0c7165fc gives the same nodes and edges for these files (repro
`tmp/captain/20261023-145049/reviews/ЗД32-r1-triage.md`, F3); it is an extension-only id
collision, not a nested type id, so ЗД32 leaves it.

**Swift, open (ЗД32 r2, non-blocking).** Each needs an artificial collision (0 cases in 6107 real
files): r2 F1, inheritance and deeper written references such as `Outer.State.Item` still use the
unsalted id (`graphify/extractors/engine.py:6428`, `:6789`); r2 F2, descendants of sibling-salted
types get a needless salt that differs from 0c7165fc (`engine.py:1526`); r2 F5, a nested type that
collides with an enum case or a computed property can lose that node (`engine.py:1532`).

## ТД8 The in-file Swift receiver bind matches by label only

**Evidence.** Pre-existing. With a single write for a receiver (`var x: State`), the Swift
in-file call walk (`walk_calls`, `graphify/extractors/engine.py:9174`) binds `x.go()` through
the file's bare-label map (`label_to_nid.get(callee_name)`, `engine.py:9967`), so it can hit a
same-named `go` of another type in the same file. The АР25 guard (`engine.py:9992-9999`) covers
only conflicting writes.

A typed receiver whose type identity is unproven (a `Self.State` or generic-parameter
annotation, or a path through a typealias, associatedtype or local type) binds nothing since
ЗД33: its write sets the name's scope to None and the АР25 guard stops the bare-label bind.
Still open: a single proven write (`x: First` binds `x.go()` to a `Second.go` declared later in
the file), the last-segment type compare (Codex ТД8 F2) and the case-folded method keys of the
resolver (F3). Untyped receivers and chains are unchanged.

Also open (ЗД33 r1 F5, general case): a proven factory local that shadows a typed parameter
keeps the parameter's type, because the local collector stashes a factory only for a name not
yet in the table. With `struct State { func go() {} }; struct Lib { func go() {} };
struct F { static func make() -> Lib { Lib() } }` in `Lib.swift` and
`struct A { func a(_ x: State) { let x = F.make(); x.go() } }` in `M.swift`, `A.a` binds
`State.go` instead of `Lib.go` (`tmp/captain/20261023-145049/reports/zd33-r1-repro/f5general.py`,
log `tmp/claude-logs/zd33-r1-f5general-20261026-074116.log`). Only the unproven-factory form
(`<Box: Q>`, `let x = Box.make()`) binds nothing since ЗД33 r1.

**Why not here.** Pre-existing behaviour outside the documented phases. The full fix must prove
the receiver's owner for every write or bind nothing, with tests for swapped declaration order.

## ТД9 Inherited methods are not looked up by the Swift member-call resolver

**Evidence.** Pre-existing. `_resolve_swift_member_calls` looks up the called method only on the
receiver's own type, `method_index.get((type_nid, _key(callee)))` in
`graphify/extract.py:4941`. A method declared only on a superclass is not found, so the call gets a type-only `references` edge to the receiver type
(`graphify/extract.py:4948-4949`) instead of a `calls` edge to the inherited method.

**Why not here.** It needs an inheritance and conformance walk in the resolver, a new mechanism
outside the five phases.

## ТД10 Same-file member calls bind nothing when a receiver name is written twice

**Evidence.** The per-file receiver table sets `scopes[name] = None` for any two writes of a
name that differ in type or enclosing scope (АР25, fail-closed), even when both name the same
type: `_swift_set_type_scope` in `graphify/extractors/engine.py:6245-6253`. On the supacode
app 21 real same-file member calls bind nothing for this reason, for example
`dragModel.reset()` in `PaneTabStripView.swift` (to `PaneTabDragModel.reset`),
`shortcut.effective(from:)` in `AppShortcuts.swift` (to `AppShortcut.effective`) and
`content.hibernate()` in `ContentRuntimeTests.swift`. A receiver written with the same type in
two enclosing types, or spelled `Outer.T` and `T`, is enough.

A fix exists on the local branch `captain/20261023-swift-xfile-types-zd28` (head `f98dde7a`,
not pushed). Its closed fail-closed rule (panel КН4) treats two writes as one type only when
that is proven: the type is a struct, enum, protocol, actor or a class without an inheritance
clause, every prefix of the path is declared in the file, and no base-class lookup is needed.
It restores 8 of the 21 calls. 5 of the 21 are real conflicts, written with different types
(`content.hibernate` twice, `defaults.seed`, `nsView.update`, `state.syncGlobalDefaults`); they
need receiver types scoped by the caller function, a change of the АР25 contract and of the
`swift_type_table` record format settled in АР3/АР12.

The single-write wrong edges found while revalidating the fourth review of `f98dde7a` are
fixed on this line by ЗД33 (`tests/test_swift_receiver_fail_closed.py`): a receiver write whose
written type goes through a typealias, associatedtype, generic parameter or parameter pack, a
local type or a `Self`-qualified path, or whose annotation the table cannot record, sets
`scopes[name] = None` at once, so the in-file bind and the corpus resolver bind nothing
(X_F1, X_F1b, X_F2, X_H2, and the older `Self.State`, `<Box: Q> Box.State`, `A<State>` with
`State?`). The rule is file-wide, so it also blocks correct calls through a colliding name.
The recovery of the 21 calls stays deferred: the caller-scoped receiver type with the closed
rule (Codex revalidation, design B+A) is not closed yet.

**Why not here.** Each review round of ЗД28 found new holes in the same proof check (4, 2, 1,
then 2 plus one found while checking them), and a wrong edge is worse than a missing one. Panel
decision КН7 = B (3 of 3): keep the branch unmerged and ship fail-closed. The closed rule above,
or the caller-scoped receiver table, is a separate piece of work with its own review loop.
