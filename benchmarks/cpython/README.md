# Run 3 — CPython Internals

**Codebase**: CPython sparse checkout — `Python/` (~120 files), `Objects/` (~50 files), `Include/` (headers)  
**Language**: C + Python hybrid  
**Design**: 1 shared research session → 6 isolated implementation sessions  
**Result file**: `run3_20260609_002347_additive.json` (gitignored)  
**Definitive run date**: 2026-06-09 (B7 re-run, post B3/B5/B6/B9 fixes)

---

## Why CPython

CPython's C internals are the opposite of Django or Camel from a training-data perspective. The model knows Python's public API deeply but has almost no coverage of the C implementation layer — `_PyObject_GC_TRACK`, `type_getattro`, `PyDictKeyEntry`, `ob_type->tp_traverse` are opaque without reading source. This makes it genuine unfamiliar territory where re-discovery pressure is high and vectr recall should pay off.

C identifiers are also BM25-heavy: names are not semantically meaningful, so exact keyword match outperforms embedding similarity for navigation. Hybrid search with BM25 weighted higher is the correct strategy here.

---

## Design: 1 research session → 6 isolated impl sessions

This design simulates a realistic week of feature work: an engineer (or AI agent) researches the relevant subsystem once, then implements six separate features on successive days — each starting from a blank context.

```
Session 1 — Research (one shared session; explores all 6 task areas)
  vectr agent  → vectr_remember() for each key finding; CLAUDE.md guides what to store
  vanilla agent → writes a prose RESEARCH SUMMARY; summary is lost when session closes

Sessions 2–7 — Implement feature A–F (each a fresh claude -p, zero prior context)
  vectr agent  → vectr_status() → vectr_recall(query=<task>) → implement from notes
  vanilla agent → no notes; re-discovers from scratch every session
```

Both agents receive identical task prompts. The only vectr surface in impl sessions is `CLAUDE.md` — no `impl_prefix` is prepended. Session isolation is enforced by the harness: each impl session is a separate `claude -p` invocation with no `--resume` flag.

---

## Results

### Research vs implementation cost

| Phase | Vanilla | Vectr | Delta |
|---|---:|---:|---:|
| Research (1 session, paid once) | $1.36 | $2.63 | +94% |
| Implementation (6 sessions, repeating) | $2.50 | $1.97 | **−21%** |
| Total sprint | $3.86 | $4.60 | +19% |

Research overhead (+$1.27) breaks even after ~8 tasks reusing the same notes. At 6 tasks the total is +19%; every task beyond 6 is net saving.

### Implementation sessions — all 6 combined

| Metric | Vanilla | Vectr | Delta |
|---|---:|---:|---:|
| Cost | $2.50 | $1.97 | **−21%** |
| Wall time | 17.6 min | 13.5 min | **−24%** |
| Turns | 123 | 94 | **−24%** |
| Read + Bash calls | 102 | 62 | **−39%** |

### Per-task re-discovery (Read+Bash before first write)

| Task | Vanilla | Vectr | Delta | `vectr_recall` fired |
|---|---:|---:|---:|---|
| `debug_gc_finalizer` | 16 | 6 | −62% | no |
| `feature_dict_pop_last` | 13 | 3 | −77% | yes |
| `cross_session_set_cartesian` | 23 | 9 | −61% | yes |
| `debug_descriptor_priority` | 6 | 6 | 0% | no |
| `cross_session_bytes_find_all` | 13 | 2 | −85% | yes |
| `cross_session_list_rotate` | 21 | 16 | −24% | yes |

### Vectr tool usage — impl sessions only

| Tool | Count |
|---|---:|
| `vectr_status` | 5 |
| `vectr_recall` | 4 |
| `vectr_search` | 1 |
| `vectr_locate` / `vectr_trace` | 0 |

`vectr_recall` is the dominant tool. When research notes contain exact function signatures and code stubs, impl sessions recall rather than re-explore. `vectr_search` fired once for a detail not in notes.

---

## Task descriptions

### Task 1: `debug_gc_finalizer` — Bug investigation

Explore `Modules/gcmodule.c`. Find `handle_legacy_finalizers`, `move_legacy_finalizers`, `tp_finalize`, and how objects with `__del__` methods end up in `gc.garbage`. Impl: write a minimal test that reliably produces `gc.garbage` entries; cite the exact C function that causes deferral; show `weakref.finalize` as the fix.

### Task 2: `feature_dict_pop_last` — Feature development

Explore `Objects/dictobject.c`. Find `dict_popitem`, `PyDictKeysObject`, `dk_nentries`, `PyDictKeyEntry` layout, `ma_used`, `DKIX_EMPTY`/`DKIX_DUMMY` sentinels. Impl: `dict.pop_last()` — removes and returns `(key, value)` for the most recently inserted key. Full C function `dict_pop_last_impl` + `PyMethodDef` entry.

### Task 3: `cross_session_set_cartesian` — Feature development

Explore `Objects/setobject.c`. Find `PySetObject`, `setentry`, `hash_pointer`, `set_add_key`. Impl: `set.cartesian(other)` returning a `frozenset` of `(a, b)` tuples. Full C function + `PyMethodDef` + docstring.

### Task 4: `debug_descriptor_priority` — Bug investigation

Explore `Objects/typeobject.c`. Understand descriptor protocol priority: data descriptors (`__get__` + `__set__`) override instance `__dict__`; non-data descriptors do not. Impl: minimal test demonstrating the priority order; explain the C-level lookup path in `type_getattro`.

### Task 5: `cross_session_bytes_find_all` — Feature development

Explore `Objects/bytesobject.c`. Find `bytes_find`, `_Py_FindUnicodeObject`, the `Py_buffer` protocol for bytes search. Impl: `bytes.find_all(sub)` returning a list of all non-overlapping match positions. Full C implementation.

### Task 6: `cross_session_list_rotate` — Feature development

Explore `Objects/listobject.c`. Find `PyListObject`, `ob_item`, memory layout, and how `list.reverse()` is implemented for reference. Impl: `list.rotate(n)` — rotate in place by n positions (positive = right, negative = left). Full C implementation.

---

## Key finding: the B9 semantic recall fix

Pre-fix (2026-05-30 original run): `vectr_recall` used SQL LIKE matching. Multi-word queries always returned 0 results. The impl agent paid note-storage overhead in research and then re-discovered anyway. Vectr Read+Bash was **equal or higher** than vanilla on every task.

Post-fix (B9, 2026-06-07): `vectr_recall` uses vector search over ChromaDB. `vectr_recall` fired with results in 4 of 6 impl tasks. Vectr Read+Bash is **below vanilla on 5 of 6 tasks**.

The B9 fix is the single most impactful change in the entire benchmark3 sprint.

---

## The honest outlier: `debug_descriptor_priority`

Vanilla re-discovery: 6 calls. Vectr re-discovery: 6 calls. No improvement.

The model has strong training coverage of Python's descriptor protocol — it navigated to the right files on the first Read call without needing research notes. Vectr's advantage is proportional to how unfamiliar the code is. This task sits at the "model already knows this" end of the spectrum. The 0% result is expected and informative: it tells you where not to expect help.
