"""POC task definitions — two-phase: Research (Phase 1) → Implementation (Phase 2)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TwoPhaseTask:
    id: str
    title: str
    phase1_description: str   # Research: explore, record findings, end session
    phase2_description: str   # Implementation: fresh session, recall & build


# ---------------------------------------------------------------------------
# Django tasks (Run 1 — familiar codebase baseline)
# ---------------------------------------------------------------------------
DJANGO_TASKS: list[TwoPhaseTask] = [
    TwoPhaseTask(
        id="custom_field",
        title="Implement a custom ORM field (MoneyField)",
        phase1_description="""\
You are researching Django's ORM field system so that a colleague can implement a
custom field in the next session.

Explore:
1. The Field base class — find it in the source, understand its __init__ signature,
   the key attributes (attname, column, db_type), and how it registers itself on a model
2. The lifecycle methods: contribute_to_class, get_prep_value, from_db_value,
   to_python — what each does and when it is called
3. Migration support — deconstruct() contract, what __init__ parameters must be
   preserved, how South/django migrations detect changes
4. Validation — how validators plug in, when clean() / validate() are called
5. Any gotchas: how null/blank/default interact, how db_column overrides work

Goal: leave a complete set of notes so the implementation session can proceed
without re-reading any of these files.
""",
        phase2_description="""\
You are implementing a MoneyField for Django's ORM.

Requirements:
- Stores values as integers in the database (cents, to avoid floating-point errors)
- Exposes values in Python as decimal.Decimal with configurable precision
  (default: 2 decimal places)
- Accepts a currency kwarg (default "USD") that is stored in the field definition
  and round-trips through migrations correctly via deconstruct()
- Validates that the value is non-negative
- Works with Django's standard __gt, __lt, __gte, __lte lookups
- Includes a brief test sketch (no framework needed, just plain assertions) showing
  that a value set as Decimal("12.50") is stored as integer 1250 and retrieved as
  Decimal("12.50")

Write the complete MoneyField class and the test sketch.
""",
    ),
    TwoPhaseTask(
        id="rate_limit_middleware",
        title="Implement per-path request rate limiting middleware",
        phase1_description="""\
You are researching Django's middleware system so that a colleague can implement
rate-limiting middleware in the next session.

Explore:
1. How middleware is loaded — find the code in Django's request handling that reads
   MIDDLEWARE from settings and wraps the handler stack
2. The __call__ protocol — what arguments a modern middleware receives, what it must
   return, and how to call get_response
3. Async middleware — how Django detects and handles async-capable middleware,
   the async_capable / sync_capable class attributes, MiddlewareMixin
4. What request attributes are available when middleware runs — request.path,
   request.META, request.method, timing
5. How to return a 429 response from middleware with a Retry-After header
6. Where to find examples of existing Django middleware to use as reference

Goal: leave enough notes that the implementation session can write the middleware
without re-reading middleware internals.
""",
        phase2_description="""\
You are implementing RateLimitMiddleware for Django.

Requirements:
- Per-IP, per-path rate limiting
- Configurable rate via settings: RATE_LIMIT = {"default": "60/minute",
  "/api/": "200/minute"} — paths are prefix-matched
- In-memory backend by default (dict + timestamps); optionally use Django's cache
  backend if django.core.cache is importable
- Returns HTTP 429 with a Retry-After header (seconds until window resets) when
  the limit is exceeded
- Works in both sync and async Django (middleware must be compatible with both)
- No external dependencies beyond Django itself

Write the complete middleware class. Include brief inline comments only where
the logic is non-obvious (window sliding, async detection).
""",
    ),
    TwoPhaseTask(
        id="async_signals",
        title="Add async receiver support to Django's signal dispatch",
        phase1_description="""\
You are researching Django's signal/dispatch system so that a colleague can add
async receiver support in the next session.

Explore:
1. The Signal class — find it, understand how receivers are stored (self.receivers),
   what the (id, sender) tuple key means, and how weak references are used
2. Signal.send() — the dispatch loop, how it calls each receiver, how it handles
   exceptions vs send_robust()
3. Signal.connect() and the @receiver decorator — how sender filtering works,
   how dispatch_uid prevents duplicate registration
4. Thread safety — find self.lock usage and understand what it protects
5. The weak reference mechanism — why it exists, when it causes receivers to
   silently disappear, and the dispatch_uid workaround
6. Async context — Django has sync_to_async / async_to_sync utilities; find them
   and understand how they work

Goal: leave enough notes so the implementation session can write Signal.asend()
and the async-aware dispatch loop without re-reading these internals.
""",
        phase2_description="""\
You are adding async receiver support to Django's Signal class.

Requirements:
- Add Signal.asend(sender, **kwargs) — an async method that dispatches to all
  connected receivers; awaits receivers that are coroutine functions; calls sync
  receivers via asyncio.get_event_loop().run_in_executor (or Django's async_to_sync)
- Add Signal.asend_robust(sender, **kwargs) — same, but catches exceptions per
  receiver (mirrors send_robust behaviour)
- Backward compatible: existing sync receivers still work via both send() and asend()
- @receiver decorator should work unchanged for both sync and async receivers
- Preserve weak-reference semantics for async receivers the same way sync ones work

Write the complete implementation as a diff-style description: show the exact
methods to add/modify on the Signal class, with the full method bodies. Do not
modify unrelated methods.
""",
    ),
]

# ---------------------------------------------------------------------------
# Apache Camel tasks (Run 2 — unfamiliar enterprise Java codebase)
#
# Design principle: every task requires navigating Camel's INTERNAL architecture.
# The model knows the public DSL (from/to/filter) but NOT how RouteBuilder,
# DefaultCamelContext, Processor, Exchange, and the component model actually work
# internally. A developer (or model) cannot implement these correctly without
# reading the source.
# ---------------------------------------------------------------------------
CAMEL_TASKS: list[TwoPhaseTask] = [
    TwoPhaseTask(
        id="custom_component",
        title="Implement a custom Camel component (in-memory queue)",
        phase1_description="""\
The Apache Camel source code is at the path provided. Explore Camel's internal
component/endpoint/producer/consumer model so a colleague can implement a custom
component in the next session.

Explore:
1. The Component contract — find the Component interface and DefaultComponent.
   How does CamelContext resolve a URI like "myqueue:channel1" to a Component?
   How does createEndpoint() work and what does it receive?
2. The Endpoint contract — find DefaultEndpoint. What must createProducer() and
   createConsumer() return? What lifecycle methods exist (doStart, doStop)?
3. The Producer contract — find DefaultProducer or AsyncProducer. What does
   process(Exchange) do? How does the producer send a message?
4. The Consumer contract — find DefaultConsumer. How does a consumer schedule or
   poll? How does it interact with the Processor passed to it?
5. The Exchange model — find DefaultExchange and DefaultMessage. How are body and
   headers accessed? What is the In/Out message pattern?
6. Component registration — how does Camel discover components? Look for
   META-INF/services or the component registry mechanism.
7. Any lifecycle gotchas: what must be started/stopped, what CamelContext services
   are available to a component via getCamelContext()?

Goal: leave complete notes so the implementation session can write a working
MemoryQueueComponent without reading any of these files again.
""",
        phase2_description="""\
Implement a MemoryQueueComponent for Apache Camel.

Requirements:
- URI scheme: `memqueue:channelName` (e.g. `from("memqueue:orders")`)
- Shared in-memory queues: all producers/consumers on the same channelName share
  one `LinkedBlockingQueue<Exchange>` (capacity configurable, default 1000)
- Producer: enqueues a copy of the exchange body+headers; blocks if full (offer
  with configurable timeout, default 5s)
- Consumer: polls the queue on a configurable schedule (default 100ms); for each
  dequeued exchange, calls the downstream Processor
- Proper lifecycle: start/stop must be correctly wired so queues are cleaned up
  when the route stops
- Component registration: show the META-INF/services entry needed for Camel to
  auto-discover the component by URI scheme

Write the complete Java implementation: MemoryQueueComponent, MemoryQueueEndpoint,
MemoryQueueProducer, MemoryQueueConsumer. Include the META-INF/services file path.
""",
    ),
    TwoPhaseTask(
        id="route_policy",
        title="Implement a circuit-breaker RoutePolicy",
        phase1_description="""\
The Apache Camel source code is at the path provided. Explore Camel's RoutePolicy
system so a colleague can implement a circuit-breaker policy in the next session.

Explore:
1. The RoutePolicy interface — find it. What methods does it define?
   What lifecycle events does Camel call it with (onInit, onStart, onStop,
   onExchangeBegin, onExchangeDone)?
2. RoutePolicySupport — find the base class. What does it provide over the raw
   interface? What is the Lock pattern used for thread safety?
3. Existing policy implementations — find ThrottlingInflightRoutePolicy or
   SuspendableRoutePolicy. How do they call route.suspend() / route.resume()?
   What is the Route object and where is suspend/resume defined?
4. How a policy is attached to a route — find how routePolicy() works in the
   RouteDefinition or how policies are registered on a Route at startup.
5. Exchange outcome — in onExchangeDone(), how does a policy detect whether the
   exchange failed? Look at exchange.isFailed(), exception handling.
6. Thread safety — what state needs locking? How do existing policies handle
   concurrent exchange completion events?

Goal: leave enough notes so the implementation session can write a correct
CircuitBreakerRoutePolicy without re-reading any of these internals.
""",
        phase2_description="""\
Implement a CircuitBreakerRoutePolicy for Apache Camel.

Requirements:
- Three states: CLOSED (normal), OPEN (suspended), HALF_OPEN (trial)
- CLOSED → OPEN: after N consecutive failures (configurable, default 5)
- OPEN → HALF_OPEN: after a timeout (configurable, default 30s)
- HALF_OPEN → CLOSED: if the next exchange succeeds
- HALF_OPEN → OPEN: if the next exchange fails (reset the timeout)
- In OPEN state: suspend the route via the Camel Route API
- In CLOSED/HALF_OPEN: resume the route if it was suspended
- Thread-safe: multiple exchanges may complete concurrently
- Configurable: failureThreshold (int), openTimeout (Duration), policy name

Write the complete Java implementation. Use only the Camel RoutePolicy/
RoutePolicySupport APIs — no external circuit-breaker libraries.
""",
    ),
    TwoPhaseTask(
        id="type_converter",
        title="Implement a custom Camel TypeConverter with fallback chain",
        phase1_description="""\
The Apache Camel source code is at the path provided. Explore Camel's TypeConverter
system so a colleague can implement a custom converter in the next session.

Explore:
1. The TypeConverter interface — find it. What does convertTo(Class, Exchange, Object)
   do? What is the difference between convertTo and mandatoryConvertTo?
2. TypeConverterRegistry — find the registry. How are converters registered?
   How does Camel look up a converter for a (fromType, toType) pair?
3. Auto-discovery — how does Camel find converters at startup? Look for
   @Converter annotation and the TypeConverterLoader / AnnotationTypeConverterLoader
   mechanism. What META-INF file does Camel scan?
4. @Converter annotation — find examples of existing converters using @Converter
   on static methods. What is the fallback=true attribute for?
5. Converter chaining — how does Camel convert A → C when it only knows A → B
   and B → C? Find the logic that chains converters.
6. Exchange parameter — when is the Exchange parameter passed to convertTo vs
   when is it null? What does a converter do with it?
7. FallbackConverter — find the FallbackTypeConverter interface. When is it
   invoked vs a direct converter?

Goal: leave enough notes so the implementation session can write a working
converter with correct registration, without re-reading these files.
""",
        phase2_description="""\
Implement a custom TypeConverter for Apache Camel that converts between a domain
type and its JSON/CSV representations.

Requirements:
- Domain type: `OrderEvent` (a simple POJO with fields: orderId:String,
  amount:double, currency:String, timestamp:long)
- Converters to implement (as @Converter-annotated static methods in a class):
  - OrderEvent → String (JSON): `{"orderId":"...","amount":1.23,"currency":"USD","timestamp":123}`
  - String → OrderEvent (parse JSON)
  - OrderEvent → byte[] (CSV row: orderId,amount,currency,timestamp)
  - byte[] → OrderEvent (parse CSV row)
- Fallback converter: if the target type is any Map, convert OrderEvent to a
  LinkedHashMap<String,Object> of its fields
- Correct registration: show the @Converter class annotation, the static methods,
  and the META-INF/services/org/apache/camel/TypeConverter file content needed
  for Camel to discover and load the converters automatically
- Handle null input correctly (return null, do not throw)

Write the complete Java implementation: the OrderEvent POJO, the
OrderEventConverters class with all @Converter methods, and the registration file.
""",
    ),
]

# ---------------------------------------------------------------------------
# CPython tasks (Run 3 — benchmark3)
#
# Design: one shared research session explores all 5 areas; 5 isolated
# implementation sessions each pick up from vectr's stored notes.
#
# Codebase: CPython sparse checkout — Python/, Objects/, Include/
# Why: C identifiers are not semantically meaningful; BM25 exact-keyword
# match dominates over embedding similarity. The model knows Python's public
# API but NOT CPython C internals at implementation depth.
# ---------------------------------------------------------------------------
CPYTHON_TASKS: list[TwoPhaseTask] = [
    TwoPhaseTask(
        id="debug_gc_finalizer",
        title="Debug why __del__ objects end up in gc.garbage",
        phase1_description="""\
Explore CPython's cyclic garbage collector in Modules/gcmodule.c.

Find:
1. handle_legacy_finalizers() and move_legacy_finalizers() — what do they do
   and when are they called during a collection cycle?
2. tp_finalize vs tp_del — what is the difference and how does CPython decide
   which path an object takes?
3. The exact condition that causes an object with __del__ to be appended to
   gc.garbage rather than freed — find the if-condition in C source.
4. gc_list_merge() — how unreachable objects are moved between lists during
   the finalization sweep.
5. Any change in behavior between Python 3.4 (PEP 442) and earlier versions
   regarding __del__ and gc.garbage.

Goal: leave precise notes (file paths, line number ranges, function names,
C condition expressions) so the implementation session can write a targeted
test and explanation without re-reading any of these files.
""",
        phase2_description="""\
You are writing a diagnostic test and fix for the CPython GC finalizer issue.

Requirements:
1. Write a minimal self-contained Python script (~30 lines) that reliably
   produces entries in gc.garbage — demonstrating the exact scenario where
   __del__ prevents collection of a reference cycle.
2. Include a comment in the script that cites the exact C function name and
   the condition (from gcmodule.c) that defers the object to gc.garbage
   rather than freeing it.
3. Show a __del__-safe rewrite of the same code using weakref.finalize that
   avoids gc.garbage entirely.
4. Explain in 3-4 sentences why weakref.finalize avoids the problem.
""",
    ),

    TwoPhaseTask(
        id="feature_dict_pop_last",
        title="Implement dict.pop_last() in CPython C",
        phase1_description="""\
Explore Objects/dictobject.c in CPython to understand the dict internals
needed to implement dict.pop_last().

Find:
1. dict_popitem() — the existing implementation. How does it choose which
   entry to remove? How does it handle compact vs. split dicts?
2. PyDictKeysObject and PyDictKeyEntry — the struct layouts. What fields
   track insertion order? Where is dk_nentries vs. ma_used?
3. DKIX_EMPTY and DKIX_DUMMY sentinel values — what do they mean and how
   are they used when scanning entries?
4. How dict_popitem() returns the (key, value) tuple — what PyArg_ParseTuple
   or return-value pattern it uses.
5. How to walk entries in reverse insertion order to find the last live entry.
6. The PyMethodDef registration pattern for adding a new dict method — find
   an existing example in dictobject.c.

Goal: leave notes precise enough that the implementation session can write
dict_pop_last_impl without opening dictobject.c again.
""",
        phase2_description="""\
Implement dict.pop_last() for CPython.

Requirements:
- dict.pop_last() removes and returns (key, value) for the most recently
  inserted key (insertion-order last entry).
- Raises KeyError with the message "pop_last from an empty dict" if the
  dict is empty.
- Works correctly on both compact dicts (Python 3.6+ common case) and
  split dicts.
- Write the complete C implementation:
  - dict_pop_last_impl() function body
  - The Argument Clinic /*[clinic input]*/ block or manual PyArg_ParseTuple
    (match the style of adjacent methods in dictobject.c)
  - The PyMethodDef entry to register the method on the dict type

Show the code as a unified diff against dictobject.c (or as clearly
labeled code blocks showing exactly where each piece is inserted).
""",
    ),

    TwoPhaseTask(
        id="cross_session_set_cartesian",
        title="Implement set.cartesian_product(other) — cross-session continuation",
        phase1_description="""\
Explore Objects/setobject.c in CPython. Your goal is to start implementing
set.cartesian_product(other) — a method that returns a frozenset of (a, b)
tuples for all pairs where a ∈ self and b ∈ other.

In this session:
1. Find setentry struct — what fields does it have? How is DKIX_DUMMY/DKIX_EMPTY
   handled for set entries?
2. Find how to iterate all live entries in a set — look at set_richcompare or
   set___contains___impl for the iteration pattern.
3. Find make_new_set() and set_add_key() — the functions you'll need to build
   the result frozenset.
4. Find how existing set operations (e.g. set_intersection) create a new set
   result and add items — use this as the template.
5. Find the PyMethodDef registration for set methods.
6. Write a working function stub: the C function signature, the outer loop
   over self's entries, and the inner loop placeholder. Save the stub text
   and all required function signatures via vectr_remember so the next
   session can continue from exactly this point.
7. Thread safety: check whether existing set methods (e.g. set_add,
   set_discard, set_intersection) use BEGIN_CRITICAL_SECTION / END_CRITICAL_SECTION
   or _lock_held variants. Save the exact macro invocation pattern so the impl
   session does not need to rediscover it.
8. Method tables: check whether frozenset_methods[] is defined separately from
   set_methods[]. Determine whether cartesian_product should appear in one or
   both, and save the answer with the frozenset_methods[] line number.
9. Save the verbatim C body of set_isdisjoint (a METH_O set method) as a
   copy-paste template — include the function signature, any argument validation,
   and its PyMethodDef entry. The impl session will use this as a structural model.

Do NOT implement the full function in this session — stop after writing the
stub and saving it. The next session will complete it.
""",
        phase2_description="""\
You are continuing an implementation started in a previous session.
Call vectr_recall() first to retrieve the stub and function signatures
saved by the research session.

Complete set.cartesian_product(other) for CPython:
- Returns a frozenset of (a, b) tuples for all pairs a ∈ self, b ∈ other.
- Handles empty sets (return empty frozenset).
- Uses make_new_set() + set_add_key() (or equivalent) to build the result.
- Properly handles reference counting (Py_INCREF/Py_DECREF) for the tuple
  and its elements.
- Include the complete PyMethodDef entry and Argument Clinic block (or
  manual PyArg_ParseTuple) matching the style of adjacent methods.

Show the complete implementation as a unified diff or clearly labeled
insertion blocks for setobject.c.
""",
    ),

    TwoPhaseTask(
        id="debug_descriptor_priority",
        title="Debug CPython data vs. non-data descriptor priority",
        phase1_description="""\
Explore Objects/typeobject.c in CPython to understand the attribute lookup
order that gives data descriptors priority over instance __dict__.

Find:
1. type_getattro() — the main attribute lookup function for type objects.
   Trace through the full lookup sequence.
2. _PyType_Lookup() — how it searches the MRO for a descriptor; what it
   returns and how the caller uses the result.
3. The exact comparison in typeobject.c that distinguishes a data descriptor
   (has both __get__ and __set__) from a non-data descriptor (only __get__).
   Find the C macro or tp_descr_set check.
4. Why property is a data descriptor — find property's type definition and
   confirm it defines tp_descr_set.
5. Why a plain Python function is a non-data descriptor — find where
   functions define tp_descr_get but not tp_descr_set.
6. The exact line(s) in type_getattro where instance __dict__ lookup is
   attempted, and how its result is ranked against the descriptor result.

Goal: leave precise notes (file, function, line ranges) so the next session
can write a targeted test without re-reading typeobject.c.
""",
        phase2_description="""\
You are writing diagnostic tests and an explanation for CPython's descriptor
priority rules.

Requirements:
1. Write Test A (~15 lines): a data descriptor that overrides an instance
   __dict__ entry. Show that setting instance.x = value does NOT shadow the
   descriptor. Include an assertion that proves it.
2. Write Test B (~15 lines): a non-data descriptor (only __get__) that IS
   shadowed by an instance __dict__ entry. Show that setting instance.x = value
   DOES shadow the descriptor. Include an assertion.
3. In a comment block after the tests, cite:
   - The exact function name in typeobject.c that implements the priority check
   - The C expression or macro that determines data vs. non-data
   - The line range (approximate) where instance __dict__ lookup occurs
4. One-paragraph explanation of why this design exists (data descriptors
   represent "managed attributes" that must not be bypassed).
""",
    ),

    TwoPhaseTask(
        id="cross_session_bytes_find_all",
        title="Implement bytes.find_all(sub) — cross-session continuation",
        phase1_description="""\
Explore Objects/bytesobject.c and Objects/stringlib/fastsearch.h in CPython.
Your goal is to start implementing bytes.find_all(sub) — a method that returns
a list of all non-overlapping match positions of sub in self.

In this session:
1. Find how bytes.find() is implemented — locate bytes_find_impl or
   _Py_FindSourceFile, follow it to the actual search call.
2. Find the FASTSEARCH macro in fastsearch.h — what arguments does it take,
   what does it return for FAST_SEARCH vs FAST_COUNT mode?
3. Find how bytes arguments are parsed via PyArg_ParseTuple — what format
   string and Py_buffer pattern is used in existing bytes methods.
4. Find how bytes.findall() would need to loop — FASTSEARCH returns one
   match at a time; understand how to advance the search position after
   each match to find all non-overlapping occurrences.
5. Find how to build a Python list of integers — PyList_New, PyList_Append,
   PyLong_FromSsize_t.
6. Write a working function stub: the C function signature, Py_buffer
   acquisition, and the loop skeleton with all needed variables declared.
   Save the stub and all required macros/function signatures via
   vectr_remember so the next session can complete it.
7. Find parse_args_finds_byte — save its file:line location and exact
   function signature. The impl session will call it directly; it must not
   need to search for it.
8. Clinic vs METH_VARARGS: explicitly check whether bytes_find uses
   Argument Clinic (a /*[clinic input]*/ block) or plain METH_VARARGS.
   Save this answer clearly — the impl session must not have to check
   clinic/bytesobject.c.h to resolve this.

Do NOT implement the full function — stop after saving the stub.
""",
        phase2_description="""\
You are continuing an implementation started in a previous session.
Call vectr_recall() first to retrieve the stub and FASTSEARCH details
saved by the research session.

Complete bytes.find_all(sub[, start[, end]]) for CPython:
- Returns a Python list of ints: all non-overlapping positions where sub
  occurs in self[start:end].
- Returns an empty list if sub is not found.
- Matches bytes.find() semantics for start/end (negative indices, clamping).
- Uses FASTSEARCH (or the same underlying call as bytes.find) for the search.
- Properly releases Py_buffer on all exit paths (success and error).
- Include the complete PyMethodDef entry and Argument Clinic block matching
  adjacent methods in bytesobject.c.

Show the complete implementation as a unified diff or clearly labeled
insertion blocks for bytesobject.c.
""",
    ),

    TwoPhaseTask(
        id="cross_session_list_rotate",
        title="Implement list.rotate(n) — cross-session continuation",
        phase1_description="""\
Explore Objects/listobject.c in CPython. Your goal is to start implementing
list.rotate(n) — a method that rotates the list in-place by n positions
(positive n = left rotation: element at index 0 moves to the end; negative
n = right rotation; matches collections.deque.rotate semantics).

In this session:
1. Find the PyListObject struct definition — locate ob_item (the backing
   array pointer), ob_size (Py_ssize_t), and allocated. Understand how ob_item
   is indexed and what it stores.
2. Find list_reverse_impl (the list.reverse() implementation) — read the full
   C body. Save it verbatim via vectr_remember: it is the template for direct
   ob_item pointer manipulation that rotate will use.
3. Find list_insert_impl — it takes a Py_ssize_t argument via Argument Clinic.
   Save the exact /*[clinic input]*/ block for list.insert as the template for
   the rotate Argument Clinic block. The impl session must not need to invent
   the clinic syntax.
4. Find the list method table (listmethods[] or similar) — save its name and
   the line number of the last entry before the sentinel, so the impl session
   knows exactly where to insert the new PyMethodDef.
5. Check whether list_reverse_impl uses any concurrent-modification guard
   (list version tag, Py_BEGIN_ALLOW_THREADS, or size recheck after the loop).
   Save the pattern if it exists — rotate must match it.
6. Write the two-piece stub and save it via vectr_remember:
   a. A static helper:
        static void _list_reverse_slice(PyObject **arr,
                                        Py_ssize_t lo, Py_ssize_t hi);
      with the swap loop body (identical to list_reverse_impl but scoped to [lo, hi)).
   b. The outer function skeleton:
        /*[clinic input]*/
        list.rotate
            n: Py_ssize_t
        /
        [docstring]
        /*[clinic start generated code]*/
        static PyObject *
        list_rotate_impl(PyListObject *self, Py_ssize_t n)
      with: the len == 0 / len == 1 early-return, the n %= len normalisation,
      the n < 0 wrap, the three _list_reverse_slice calls (with correct lo/hi
      expressions), and a Py_RETURN_NONE.
   Save both pieces together as one vectr_remember call tagged ["stub", "list", "rotate"].

Do NOT add the PyMethodDef or clinic-generated boilerplate in this session —
stop after saving the stub. The next session will complete it.
""",
        phase2_description="""\
You are continuing an implementation started in a previous session.
Call vectr_recall() first to retrieve the stub, PyListObject layout, clinic
block template, and method table location saved by the research session.

Complete list.rotate(n) for CPython:
- Rotates the list in-place by n steps. Positive n = left rotation (element
  at ob_item[0] ends up at ob_item[len-n]); negative n = right rotation.
  Matches collections.deque.rotate(n) semantics exactly.
- Handles: empty list (no-op), single-element list (no-op), |n| >= len
  (normalise via modulo), negative n (convert to equivalent positive shift).
- Implements the 3-reverse algorithm using the _list_reverse_slice helper
  from the stub: reverse(0, k), reverse(k, len), reverse(0, len).
- Uses the saved Argument Clinic /*[clinic input]*/ block as the template.
- Adds the PyMethodDef entry in the saved method table at the saved line.

Show the complete implementation as a unified diff or clearly labeled
insertion blocks for listobject.c.
""",
    ),
]

# ---------------------------------------------------------------------------
# Run 4 — CPython GC: 4 sequential tasks, no research phase
#
# Design: each task is a fresh LLM session, but workspace files accumulate
# (no git-restore between tasks — mirrors real dev workflow). Vectr notes
# also accumulate: task2+ vectr sessions recall what task1 wrote, giving
# cross-session memory advantage. Vanilla has nothing.
#
# Both agents get the IDENTICAL prompt. Only difference: CLAUDE.md in the
# vectr workspace, written by `vectr start`.
# ---------------------------------------------------------------------------

@dataclass
class SinglePhaseTask:
    """One task, one LLM session. No research/impl split."""
    id: str
    title: str
    description: str   # complete task prompt; runner prepends codebase path


GC_TASKS: list[SinglePhaseTask] = [
    SinglePhaseTask(
        id="gc_task1",
        title="Add gc.total_collected() + investigate gc.garbage/__del__",
        description="""\
Complete both tasks below by working directly in the CPython source files.

--- TASK A: Implement gc.total_collected() ---

Add gc.total_collected() to the gc module. It takes no arguments and returns
an integer: the total number of objects collected across all three GC
generations (gen0 + gen1 + gen2) combined, cumulative since process start.
This is the sum of the per-generation 'collected' statistic.

Find where the gc module's C implementation lives, locate the per-generation
statistics structure, and wire up the new function. Use the docstring:
  "Return total objects collected across all GC generations."

--- TASK B: Investigate gc.garbage and __del__ ---

A developer filed this report:
  "My class has __del__ defined. When an instance is part of a reference
   cycle, it ends up in gc.garbage instead of being collected. I thought
   Python 3 fixed the __del__ + cycle problem. What is actually going on?"

Investigate the CPython GC source. Write your findings to
gc_del_investigation.txt in the current working directory. Cover:
1. What gc.garbage is and when objects are placed there vs finalized directly
2. What PEP 442 (Python 3.4) changed — and what it did NOT change
3. The specific C function(s) and condition(s) that route an object to
   gc.garbage vs calling its finalizer
4. What the developer must do to avoid gc.garbage contamination
""",
    ),

    SinglePhaseTask(
        id="gc_task2",
        title="Add gc.reset_stats() and gc.peek_collection()",
        description="""\
A previous session added gc.total_collected() to the gc module.
Continue by implementing two more functions in the same source file.

--- TASK A: Implement gc.reset_stats() ---

Add gc.reset_stats() to the gc module. It takes no arguments, returns None,
and resets the per-generation collection statistics to zero — specifically
the 'collections', 'collected', and 'uncollectable' counters for all three
generations.

After gc.reset_stats(), gc.get_stats() should return:
  [{'collections': 0, 'collected': 0, 'uncollectable': 0}] × 3

--- TASK B: Implement gc.peek_collection() ---

Add gc.peek_collection() to the gc module. It takes no arguments and returns
an integer (0, 1, or 2) indicating which generation would be selected for
collection if gc.collect() were triggered right now, based on current object
counts vs generation thresholds.

Follow the same generation-selection logic CPython uses internally.
""",
    ),

    SinglePhaseTask(
        id="gc_task3",
        title="Investigate: freeze behavior, startup objects, generation promotion",
        description="""\
Three developers filed separate bug reports about GC behavior. Investigate
each one using the CPython source and document your findings.

Write all findings to gc_investigations.txt in the current working directory,
with a clearly labeled section header for each report.

--- REPORT A: Frozen objects ---
"I called gc.freeze() to permanently protect all current objects. Then I
called gc.collect(). I expected frozen objects to be completely invisible to
the collector, but collection still seems to process them somehow. Is
gc.freeze() working as I think?"

--- REPORT B: Objects at startup ---
"Right after Python starts — before my code runs a single line —
gc.get_count()[0] already shows a positive number. Where are all these
tracked objects coming from before user code runs?"

--- REPORT C: Automatic generation promotion ---
"I only ever call gc.collect(0) explicitly. But gc.get_stats()[1] shows
collections > 0 — gen1 was collected even though I never asked for it.
When does gen1 collection get triggered automatically?"
""",
    ),

    SinglePhaseTask(
        id="gc_task4",
        title="Add gc.referents_count() + gc.is_collecting() + investigate disable/garbage",
        description="""\
Complete two features and two debug investigations in the CPython source.

--- TASK A: Implement gc.referents_count(*objs) ---

Add gc.referents_count(*objs) to the gc module. It takes one or more objects
and returns an integer: the total count of objects directly referred to by
all arguments combined. This is a memory-efficient alternative to
len(gc.get_referents(*objs)) — it counts without allocating an intermediate
list.

--- TASK B: Implement gc.is_collecting() ---

Add gc.is_collecting() to the gc module. It takes no arguments and returns
True if the GC is currently executing a collection cycle, False otherwise.
Find the flag in the GC state structure that tracks this.

--- TASK C: Investigate gc.disable() ---

A developer reports:
  "After gc.disable(), I expected gc.get_count()[0] to stop incrementing.
   But it keeps going up as I allocate objects. Does gc.disable() not
   actually stop object tracking?"

Investigate and append a section "gc.disable() behavior" to
gc_investigations.txt. Explain exactly what gc.disable() does and does not do.

--- TASK D: Investigate non-__del__ objects in gc.garbage ---

A developer reports:
  "gc.garbage contains a plain list object. Lists don't have __del__. How
   did it end up in gc.garbage? I thought only objects with __del__ go there."

Investigate and append a section "gc.garbage non-__del__ objects" to
gc_investigations.txt. Explain the exact mechanism.
""",
    ),
]

# ---------------------------------------------------------------------------
# uv tasks (Run 5 — unfamiliar Rust codebase, real-world workflow)
#
# Design: single-phase, no research/impl split. Simulates a developer joining
# the uv project — they need vectr_search to navigate unfamiliar Rust code.
# Tasks are sequential; notes from task 1 persist to task 2/3.
# Each task has 3 sub-items covering different areas of the codebase to force
# enough exploration that context bloat and eviction become measurable.
# ---------------------------------------------------------------------------
UV_TASKS: list[SinglePhaseTask] = [
    SinglePhaseTask(
        id="uv_task1",
        title="Extras validation + resolver entry point + platform markers",
        description="""\
Explore the uv codebase (written in Rust) and write a single investigation file
documenting your findings. Work through the three areas below in order — each
builds on the previous. Do not split this into parallel sub-investigations.

1. Extras validation
   - Find where extras specifications are parsed from the CLI input
     (e.g., `uv add requests[nonexistent]`).
   - Trace how extras are stored in the package/dependency types during resolution.
   - Does uv validate extra names against the package's declared extras from PyPI
     metadata? If not, identify the earliest point where such validation could be added.
   - How is PyPI JSON metadata deserialized — what field carries extras and what does
     its structure look like in the Rust types?

2. Resolver entry point (build on what you found about extras types above)
   - Find the CLI command handler for `uv add`.
   - Identify the top-level resolver struct(s). What trait/struct drives the PubGrub
     resolution loop?
   - Where is the decision made: "fetch from PyPI" vs "use workspace member / local path"?
   - What type represents a package being resolved — how does it carry name, version
     constraints, and extras together?

3. Platform markers (build on the resolver types above)
   - Find the marker parsing code — what crate/module handles PEP 508 environment
     markers (e.g., `requests; python_version > "3.8"`)?
   - When are markers evaluated: at parse time, resolution time, or install time?
   - Find where marker evaluation gates package inclusion in the lock file.
   - Find a concrete code path where a marker causes a dependency to be skipped entirely.

Write all findings to `uv_overview.txt`.
""",
    ),

    SinglePhaseTask(
        id="uv_task2",
        title="Workspace lock cycle: discovery → resolution → serialization",
        description="""\
Continue exploring the uv codebase. Build on any notes from previous sessions,
particularly findings about the resolver entry point and extras types from task1.

Trace the complete lifecycle of a workspace dependency from disk to lock file.
The four areas below are interdependent — work through them in order, as each
feeds the next. Do not split this into parallel sub-investigations.

1. Workspace discovery and metadata ingestion
   - How does uv scan the filesystem for workspace members (pyproject.toml files)?
   - What Rust type carries a member's name, version, and extras from disk into
     the resolver?

2. Intra-workspace resolution
   - When member A declares B (another workspace member) as a dependency, is B
     treated as an editable local install, a direct path requirement, or something
     else internally?
   - If two workspace members both constrain the same third-party package with
     different version ranges, find where those ranges are intersected or merged.
     What type represents the merged constraint, and where is an incompatibility
     (non-overlapping ranges) detected and stored?

3. Lock file serialization
   - After resolution completes, trace how a single resolved package entry is
     written to uv.lock. Follow one package from the internal resolved type to
     the fields that appear in the file (name, version, source, hashes).
   - How are sdist vs. wheel sources represented differently in the lock file?

4. Lock file freshness check
   - What does `uv lock --check` compare?
   - What code path fires the "lock file out of date" error, and what field or
     hash triggers it?

Write all findings to `uv_workspace_lock.txt`.
""",
    ),

    SinglePhaseTask(
        id="uv_task3",
        title="Script mode (PEP 723) + dependency groups (PEP 735) + resolution error formatting",
        description="""\
Continue exploring the uv codebase. Build on any notes from previous sessions.

--- TASK A: Script mode (PEP 723) ---

When `uv run script.py` is used and the script contains inline metadata
(PEP 723: dependencies embedded in a `# /// script` comment block), find how
uv parses and uses those dependencies.

1. Find the PEP 723 metadata parser — what regex or parser extracts the TOML block?
2. Trace how the parsed inline dependencies flow into the resolution pipeline.
3. What happens if the script's inline deps conflict with an active virtual environment?
4. Find the code path that runs the script after resolution — how is the environment
   activated for the subprocess?

Write your findings to `uv_script_mode.txt`.

--- TASK B: Dependency groups (PEP 735) ---

Find how `[dependency-groups]` in pyproject.toml (PEP 735) is parsed and handled.

1. Find the parser/deserializer for `[dependency-groups]`.
2. How are dependency groups represented internally — are they the same type as extras?
3. How does `uv sync --group dev` work: where does the `--group` filter take effect
   in the resolution or install pipeline?
4. Can a dependency group reference another group (group inclusion)? Find whether uv
   supports this and where.

Write your findings to `uv_dep_groups.txt`.

--- TASK C: Resolution error formatting ---

When resolution fails (unsatisfiable constraints), find how the error is formatted.

1. Find where PubGrub incompatibilities are converted to user-readable strings.
2. What information does the error include — just the conflict, or also the dependency
   chain that led to it?
3. Find where the "because X requires Y and Z requires not-Y" explanation tree is built.
4. Find any special-casing for common error patterns (e.g., Python version incompatibility).

Write your findings to `uv_resolution_errors.txt`.
""",
    ),
]


# ---------------------------------------------------------------------------
# TigerBeetle tasks (Run 6 — unfamiliar Zig codebase, real-world workflow)
#
# Design: same as uv tasks — single-phase, no research/impl split.
# TigerBeetle is a financial database written in Zig. Claude does not know
# its internal structure at implementation depth.
# ---------------------------------------------------------------------------
TIGERBEETLE_TASKS: list[SinglePhaseTask] = [
    SinglePhaseTask(
        id="tb_task1",
        title="Account validation + pending transfer lifecycle + linked chains",
        description="""\
Explore the TigerBeetle state machine (written in Zig). Work through the three
areas below in order — each builds on the previous. Do not split this into
parallel sub-investigations.

1. Account creation validation
   - Find where `create_accounts` is handled in the state machine.
   - Map all validation rules — for each `CreateAccountResult` error code, find
     the exact Zig condition that produces it.
   - Which flag combinations are explicitly invalid? Find the specific checks.
   - Find the account balance invariant — how does TigerBeetle ensure
     `debits_posted` and `credits_posted` stay consistent and non-negative?
   - What is the maximum number of accounts that can be created in a single batch?

2. Pending transfer lifecycle (build on the account types above)
   - Find the `create_transfers` handling — how is a pending transfer stored
     differently from a regular transfer?
   - Find `post_pending_transfer` — what exactly changes in the debit/credit
     balances? Atomic or in steps?
   - Find `void_pending_transfer` — how does it differ from posting?
   - What happens if you post or void an already-completed transfer, or one
     whose timeout has expired?

3. Linked transfer chains (build on the transfer types above)
   - Find where the `flags.linked` logic is implemented.
   - Find the rollback mechanism — optimistic (apply then undo) or preventive?
   - What is the maximum chain length and where is it enforced?
   - Can pending transfers be linked? Find any restrictions.

Write all findings to `tb_state_machine.txt`.
""",
    ),

    SinglePhaseTask(
        id="tb_task2",
        title="VSR consensus flow + checkpointing + client sessions",
        description="""\
Continue exploring TigerBeetle. Build on any notes from previous sessions.
Work through the three areas below in order. Do not split into parallel
sub-investigations.

1. VSR message flow
   - Find the VSR implementation — which file(s) contain the main replica logic?
   - Map the message types used in normal operation (prepare, prepare_ok,
     commit, etc.).
   - Trace a client request end-to-end: client sends → primary assigns op number
     → replicas receive prepare → reply prepare_ok → primary commits → state
     machine applies → reply sent. Find the Zig code for each step.
   - Where does the primary validate that a prepare matches the expected view
     and op number?

2. Checkpointing and the superblock (build on the replica types above)
   - Find where TigerBeetle creates checkpoints — what triggers one?
   - Find the "superblock" — what is it, what does it store, why multiple copies?
   - What state is included in a checkpoint vs. what stays in the VSR log?
   - How does a recovering replica use checkpoints — replay from checkpoint,
     from log start, or a combination?

3. Client sessions (build on the replica state above)
   - Find where client sessions are stored.
   - How are duplicate requests detected — what is the deduplication key?
   - Find the maximum number of concurrent client sessions and where this limit
     is set.
   - What happens when the session limit is reached and a new client connects?
     Find the eviction policy.

Write all findings to `tb_consensus.txt`.
""",
    ),

    SinglePhaseTask(
        id="tb_task3",
        title="LSM storage: structure + compaction + read path",
        description="""\
Continue exploring TigerBeetle's storage engine. Build on any notes from
previous sessions. Work through the three areas below in order. Do not split
into parallel sub-investigations.

1. LSM tree structure
   - Find the LSM implementation — which file(s) define the core LSM types?
   - Map the key types: what is a Table, a Level, a Manifest?
   - How many levels does the LSM have? Configurable or fixed?
   - What does a single table file on disk contain — key format, value format,
     block structure, metadata header?

2. Compaction (build on the LSM types above)
   - Find where compaction is triggered — what condition causes it?
   - Map the compaction algorithm: how are two levels merged? Full or partial?
   - How are deleted/tombstoned entries handled during compaction?
   - Is compaction synchronous (blocking) or concurrent? Find the scheduler.

3. Read path (build on the compaction and level types above)
   - Trace an account lookup from the state machine through the LSM layers.
   - Which levels are checked, and in what order?
   - How are negative lookups (account doesn't exist) handled efficiently?
   - How are range queries (account filter with pagination) handled across
     multiple table files — is there a merge iterator?

Write all findings to `tb_storage.txt`.
""",
    ),

    SinglePhaseTask(
        id="tb_task_frozen",
        title="Implement AccountFlags.frozen — reversible account suspension with full client bindings",
        description="""\
Add a reversible frozen account state to TigerBeetle. A frozen account
rejects all new transfers until it is explicitly unfrozen. Freezing and
unfreezing is triggered by a special transfer flag on the relevant account
side — analogous to how account closure already works, but reversible.

Complete this as a single session. Do not spawn subagents.
Implement the four deliverables below in order — each builds on the previous.

DELIVERABLE 1 — Core types
Add a `frozen` boolean flag to the account flags type. Add four new transfer
flags: freeze and unfreeze variants for each account side (debit/credit). Add
two new error codes for transfers rejected due to a frozen account. A transfer
that both freezes and unfreezes the same side in one operation is invalid —
add an appropriate error for that too. Make sure all size and layout invariants
still hold after your additions.

DELIVERABLE 2 — State machine enforcement
Enforce the frozen state during transfer validation: reject transfers on frozen
accounts with the new error codes, in the same place existing closure checks
live. Apply freeze/unfreeze flag handling after a transfer commits, mirroring
how the existing closing flags work. Frozen accounts should share the same
exception as closed accounts for voiding pending transfers.

DELIVERABLE 3 — Tests
Write tests covering: freezing a debit account and confirming subsequent
transfers are rejected; freezing a credit account; unfreezing each side and
confirming transfers resume; the invalid freeze+unfreeze-same-side combination;
void-pending on a frozen account succeeding; post-pending on a frozen account
failing; interaction with the closed flag; and a linked chain where a freeze
failure rolls back the linked transfer.

DELIVERABLE 4 — All client bindings
Propagate the new account flag, the four transfer flags, and the new error
codes into all six client libraries: Go, Python, Node, Java, .NET, and Rust.
Find the existing flag and error-code definitions in each client to understand
the pattern, then add the new entries following exactly the same conventions.
""",
    ),

    SinglePhaseTask(
        id="tb_task_limits",
        title="Implement per-account balance limits — new BalanceLimits operation with full client bindings",
        description="""\
Add per-account balance limits to TigerBeetle. Once a limit is set for an
account, any transfer that would push that account's posted balance past the
limit is rejected with a new error code. The Account struct has no room for
additional fields, so limits live in a separate record keyed by account ID,
created via a new operation.

Complete this as a single session. Do not spawn subagents.
Implement the four deliverables below in order — each builds on the previous.

DELIVERABLE 1 — Core types
Understand the existing type system before adding anything: how the Account
struct is laid out and why it cannot grow, what other 128-byte record types
look like, how the Operation enum is structured, and how existing result enums
pattern their error codes. Then add: a new 128-byte AccountLimits struct keyed
by account_id with debit_limit and credit_limit fields (0 = unlimited); a new
set_account_limits operation; a result enum for that operation covering at
minimum ok, account_not_found, and sanity errors for limits that are already
violated; and two new transfer error codes for limit exceeded on each side.

DELIVERABLE 2 — State machine
Understand the grove architecture (how the state machine stores and prefetches
records) before touching anything. Then add: a new grove for AccountLimits; a
set_account_limits handler that validates the account exists and the requested
limits are not already violated, then upserts the record; prefetch of
AccountLimits for both account sides during transfer processing; and limit
enforcement in the transfer validation path immediately after the existing
balance checks, with limit == 0 meaning unlimited.

DELIVERABLE 3 — Tests
Write tests covering: a transfer within the limit succeeds; a transfer
exceeding the debit limit is rejected; a transfer exceeding the credit limit is
rejected; a limit of 0 is treated as unlimited; set_account_limits on a
non-existent account returns account_not_found; setting a limit already
violated by current balances returns the appropriate sanity error; overwriting
limits works; and a linked-transfer chain where the second transfer exceeds the
limit causes both to roll back.

DELIVERABLE 4 — All client bindings
Propagate the new AccountLimits type, the set_account_limits operation, its
result enum, and the two new transfer error codes into all six client libraries:
Go, Python, Node, Java, .NET, and Rust. In each client, find how an existing
operation like create_accounts is exposed to understand the pattern, then add
set_account_limits following the same conventions.
""",
    ),
]


# Active task list — switch between runs
TASKS = CAMEL_TASKS
