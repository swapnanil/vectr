You are working in the Apache Camel repository checkout at the current directory.

The Resequencer EIP supports a `reverse` option in batch mode only: calling
`.resequence(...).stream().reverse()` in the Java DSL currently throws
IllegalStateException. Your task: implement `reverse` for the **stream**
resequencer so that reversed ordering works end-to-end.

Requirements:

1. The acceptance test at
   `core/camel-core/src/test/java/org/apache/camel/processor/StreamResequencerReverseGateTest.java`
   must pass, run with:
   `mvn -pl core/camel-core test -Dtest=StreamResequencerReverseGateTest`
   Do NOT modify that test file in any way.
2. All existing resequencer tests must stay green:
   `mvn -pl core/camel-core test -Dtest='*Resequencer*'`
3. Follow the codebase's own conventions: Apache license headers, `@Metadata`
   annotation patterns on model options, XML attribute style used by the other
   options in the same config class, and javadoc consistent with neighboring
   methods.
4. Reversed stream ordering must be correct at the engine level, not just a
   post-hoc sort: mind how the stream resequencer decides element release
   (successor/predecessor relations), not only pairwise comparison.
5. Keep the change scoped to the resequencer feature; do not reformat or touch
   unrelated files.

When done, summarize: files changed, how reverse ordering interacts with the
stream engine's release logic, and any behavioral corner cases you deliberately
left out of scope (for example interactions with rejectOld), with reasoning.
