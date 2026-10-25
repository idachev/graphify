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

## ТД3 A nested type id can equal a top-level `Outer_State` id

**Evidence.** Phase 3 gives a nested type `_make_id(outer_nid, "State")`, which is the same id
as a top-level type named `Outer_State` in the same file (`_swift_member_nid`,
`graphify/extractors/engine.py:6267-6269`; `make_id` joins the parts with `_`,
`graphify/ids.py:86`). The same limit exists for Python nested classes (review ЗД12 r1 F4).

**Why not here.** The input is artificial and the fix is a change of the id scheme shared with
Python, outside the task's scope.

## ТД4 Raw `.swift` suffix checks ignore the `.graphifyrc` language override

**Evidence.** These checks test the raw file name instead of `effective_suffix`, so a file
remapped to Swift with `.graphifyrc` (for example `language.swifttpl=swift`) is not treated as
Swift there:

- `graphify/extract.py:10864`, the Swift constructor-call branch in `extract()` (cited as
  `:10795` in review ПР8; moved on this branch).
- `graphify/extract.py:4103`, `_swift_dotted_stub_edge` in `_rewire_unique_stub_nodes` (added
  on this branch, same pattern).
- `graphify/extractors/resolution.py:3587`, `_swift_type_index`.
- `graphify/extractors/resolution.py:3826`, `swift_source_ids` in
  `_resolve_swift_type_references`.

The member-call pass was fixed in `93ffffb6`; review ПР8.

**Why not here.** Pre-existing pattern outside the member-call fix that found it; each site
needs its own regression test with a language override.

## ТД5 Extensionless scripts still reach the Python member-call pass

**Evidence.** `_is_python_file` in `_resolve_python_member_calls`
(`graphify/extract.py:5186-5191`) lets a file with no suffix pass, so an extensionless bash or
node script can still be a caller or target of the Python member-call pass, as before the gate.

**Why not here.** Accepted risk of review ЗД26 r1 F2: the earlier shebang check read files
relative to the working directory and could drop a real Python edge on a rebuild run outside the
scan root; a safe check needs the scan root or the detected language passed into the pass.

## ТД6 Swift member calls bind a C/C++ namesake in a `.h` header

**Evidence.** Pre-existing. The Swift member-call target gate in `_resolve_swift_member_calls`
(`graphify/extract.py`, task ЗД27) admits targets from `.swift`, `.m`, `.mm` and `.h` files.
A `.h` file can be C or C++ as well as Objective-C, so a C++ namesake passes the gate. Repro:
a corpus with `Use.swift` containing `func useRun() { Inner.go(); Widget.render() }`, a C++
`Inner.h` containing `class Inner { public: static void go() {} };` and an Objective-C
`Widget.h` containing `@interface Widget - (void)render; @end`; run `extract()` over the three
files. The Swift caller gets a `calls` edge to `.go()` in `Inner.h` (wrong) beside the correct
edge to `-render` in `Widget.h`. Owner panel decision КН3 = B (TODO).

**Why not here.** No node data tells an Objective-C `.h` from a C/C++ `.h`: nodes from both
headers carry the same keys, and the extractor choice made in `_get_extractor` is not recorded
in the result. The fix is a new mechanism in a second language's extractor: stamp each type
node `extract_objc` emits (`graphify/extractors/objc.py`), keep the stamp through
`_merge_decl_def_classes` (`graphify/extractors/resolution.py:3209`), bump `_AST_CACHE_SCHEMA`,
and let the gate admit `.h` only with the stamp. It carries a recall risk for Swift and
Objective-C interop (#1556), so it needs its own tests: Swift to a C++ `class Inner` in `.h`
binds nothing, Swift to an ObjC `@interface Widget` in `.h` still binds `-render`, and a Swift
extension folded onto an ObjC class still binds.

## ТД7 Real same-file receiver conflicts bind nothing under the flat receiver table

**Evidence.** On the supacode app, five receivers are written with different types in one file
and so get `scopes[name] = None` (АР25): `content.hibernate` (twice), `defaults.seed`,
`nsView.update` and `state.syncGlobalDefaults`. Their member calls bind nothing, a missing edge.
The table is flat per file: `_swift_set_type_scope` in `graphify/extractors/engine.py:6245-6253`.

**Why not here.** The fix scopes receiver types by the caller function instead of by file,
which changes the АР25 contract and the `swift_type_table` record format settled in АР3/АР12.

## ТД8 The in-file Swift receiver bind matches by label only

**Evidence.** Pre-existing. With a single write for a receiver (`var x: State`), the Swift
in-file call walk (`walk_calls`, `graphify/extractors/engine.py:9174`) binds `x.go()` through
the file's bare-label map (`label_to_nid.get(callee_name)`, `engine.py:9967`), so it can hit a
same-named `go` of another type in the same file. The АР25 guard (`engine.py:9992-9999`) covers
only conflicting writes.

**Why not here.** Pre-existing behaviour outside the documented phases; task ЗД28 (under review)
starts on it for agreed same-type writes, which keep only a method of that type.
