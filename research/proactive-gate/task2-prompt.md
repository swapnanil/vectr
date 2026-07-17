You are working in the Apache Camel repository checkout at the current directory.

Camel has no way to bound the wall-clock time of a route *segment*: EIPs like
multicast and resequencer have their own timeout options, but an arbitrary
sequence of steps cannot be given a deadline. Your task: implement a new
`timebox` EIP — a wrapping definition that runs its child steps under a
wall-clock budget and fails the exchange with
`org.apache.camel.ExchangeTimedOutException` when the budget is exceeded.

Java DSL shape (pinned by the acceptance test):

    from("direct:a")
        .timebox(500)          // budget in milliseconds
            .to("bean:slow")
            .transform(...)
        .end()
        .to("mock:result");

XML DSL shape: `<timebox timeoutMillis="500">` with nested outputs.

Required semantics:

1. Happy path: children complete within budget → body, headers, properties and
   MEP propagate exactly as if the timebox were absent.
2. Timeout: the exchange fails with `ExchangeTimedOutException` (carrying the
   budget), steps after `.end()` do not run, and the route's error handling
   (`onException`, redelivery) sees the exception like any other.
3. Isolation: children execute against an isolated copy of the exchange. A
   late-finishing child must not mutate or corrupt the exchange after the
   timeout has already won — the winner (completion or timeout) is decided
   exactly once, and only a within-budget completion copies results back.
4. Redelivery re-runs the whole timebox section fresh on each attempt.
5. No thread-per-exchange: the timeout mechanism must use a shared timer /
   scheduled executor (see how the codebase manages executors and timeout
   maps), not a dedicated thread or busy-wait per in-flight exchange. Hundreds
   of concurrent exchanges must not create hundreds of threads.
6. Timeouts must be enforced for asynchronous children too (async delayer,
   seda round-trips) — do not rely on Thread.interrupt of a caller thread.
7. Nesting: a timebox inside a timebox behaves correctly — whichever budget
   expires first wins, and the inner exception propagates through the outer.
8. XML round-trip: the definition (with its timeout attribute) survives
   model → XML → model round-tripping like neighboring definitions do.

Requirements:

1. The acceptance test at
   `core/camel-core/src/test/java/org/apache/camel/processor/TimeboxGateTest.java`
   must pass, run with:
   `mvn -pl core/camel-core test -Dtest=TimeboxGateTest`
   Do NOT modify that test file in any way.
2. Regression sets must stay green:
   `mvn -pl core/camel-core test -Dtest='*Pipeline*,*Step*,*Multicast*'`
   `mvn -pl core/camel-core-model,core/camel-xml-io test`
3. Follow the codebase's own conventions: Apache license headers, `@Metadata`
   patterns on model options, how other wrapping definitions (e.g. step,
   saga) integrate with the reifier and the generated XML/YAML io, and javadoc
   consistent with neighboring code.
4. The subtle part is the completion/timeout race and exchange isolation
   (semantics 3, 5, 6) — get the async engine integration right at the
   processor level, not with ad-hoc threads and joins.
5. Keep the change scoped to the new feature; do not reformat or touch
   unrelated files.

When done, summarize: files changed, how the completion/timeout race is
decided and results copied back, the executor/timer strategy, and any
behavioral corner cases you deliberately left out of scope, with reasoning.
