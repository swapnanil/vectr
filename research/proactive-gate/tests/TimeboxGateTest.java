/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.apache.camel.processor;

import java.io.StringReader;
import java.io.StringWriter;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import org.apache.camel.ContextTestSupport;
import org.apache.camel.Exchange;
import org.apache.camel.ExchangePattern;
import org.apache.camel.ExchangeTimedOutException;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.component.mock.MockEndpoint;
import org.apache.camel.model.ProcessorDefinition;
import org.apache.camel.model.RouteDefinition;
import org.apache.camel.model.RoutesDefinition;
import org.apache.camel.model.TimeboxDefinition;
import org.apache.camel.xml.in.ModelParser;
import org.apache.camel.xml.out.ModelWriter;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Acceptance test for the timebox EIP. Provided by the harness — implementations must make it pass unmodified.
 */
public class TimeboxGateTest extends ContextTestSupport {

    // static so a fresh CamelContext per test method does not reset counts kept purely to observe how many
    // times a section actually re-ran; each test that uses one resets it to 0 as its first statement
    private static final AtomicInteger REDELIVERY_COUNTER = new AtomicInteger();
    private static final AtomicInteger TOTAL_DELIVERIES = new AtomicInteger();

    @Test
    public void testHappyPathPropagatesBodyHeadersPropertiesAndMep() throws Exception {
        MockEndpoint result = getMockEndpoint("mock:happyResult");
        result.expectedBodiesReceived("Hello");
        result.expectedHeaderReceived("insideHeader", "insideVal");

        Exchange out = template.send("direct:happy", ExchangePattern.InOut, exchange -> {
            exchange.getIn().setBody("Hello");
            exchange.getIn().setHeader("beforeHeader", "beforeVal");
        });

        assertMockEndpointsSatisfied();
        assertNull(out.getException(), "no exception expected on the happy path");

        Exchange received = result.getExchanges().get(0);
        assertEquals("beforeVal", received.getIn().getHeader("beforeHeader"), "header set before timebox must survive");
        assertEquals("propVal", received.getProperty("propKey"), "property set before timebox must survive");
        assertEquals(ExchangePattern.InOut, received.getPattern(), "exchange pattern (MEP) must be preserved");
    }

    @Test
    public void testTimeoutThrowsExchangeTimedOutExceptionAndSkipsStepsAfterEnd() throws Exception {
        MockEndpoint afterEnd = getMockEndpoint("mock:afterEndNotReached");
        afterEnd.expectedMessageCount(0);

        Exchange out = template.send("direct:timeout", exchange -> exchange.getIn().setBody("start"));

        assertMockEndpointsSatisfied();
        assertNotNull(out.getException(), "exchange must fail once the budget is exceeded");
        ExchangeTimedOutException timedOut = assertInstanceOf(ExchangeTimedOutException.class, out.getException());
        assertEquals(300L, timedOut.getTimeout(), "the exception must carry the configured budget");
    }

    @Test
    public void testOnExceptionHandledRoutesTimeoutAndRouteStaysHealthy() throws Exception {
        MockEndpoint handled = getMockEndpoint("mock:onExceptionHandled");
        handled.expectedMessageCount(1);
        MockEndpoint result = getMockEndpoint("mock:onExceptionResult");
        result.expectedMessageCount(1);

        // first exchange: child sleeps well past the budget, exception is caught and handled
        template.sendBody("direct:onExceptionHealth", "slow-input");

        // second exchange, sent right after: child completes well within the budget
        // this proves the route (and the timebox instance within it) is still healthy for subsequent traffic
        template.sendBodyAndHeader("direct:onExceptionHealth", "fast-input", "fast", true);

        assertMockEndpointsSatisfied();
        assertEquals("slow-input", handled.getExchanges().get(0).getIn().getBody());
        assertEquals("fast-input", result.getExchanges().get(0).getIn().getBody());
    }

    @Test
    public void testRedeliveryReRunsTimeboxSectionFresh() throws Exception {
        REDELIVERY_COUNTER.set(0);

        MockEndpoint result = getMockEndpoint("mock:redeliveryResult");
        result.expectedMessageCount(1);

        template.sendBody("direct:redelivery", "payload");

        assertMockEndpointsSatisfied();
        // two failing attempts (each re-entering the timebox section, each incrementing the counter) followed
        // by one successful attempt: three fresh runs total, proving redelivery does not reuse or resume the
        // previous attempt's state
        assertEquals(3, REDELIVERY_COUNTER.get());
    }

    @Test
    public void testLateFinishingChildDoesNotMutateAlreadyTimedOutExchange() throws Exception {
        MockEndpoint timedOut = getMockEndpoint("mock:lateChildTimedOut");
        timedOut.expectedMessageCount(1);
        MockEndpoint childFinished = getMockEndpoint("mock:lateChildFinishedLate");
        childFinished.expectedMessageCount(1);
        childFinished.setResultWaitTime(5000);

        template.sendBody("direct:lateChild", "original");

        // the timed-out result must appear quickly, well before the late child finishes
        timedOut.assertIsSatisfied();
        assertEquals("original", timedOut.getExchanges().get(0).getIn().getBody(),
                "a late-finishing child must not mutate the exchange that already timed out");

        // now wait for the late child itself to actually finish, proving it kept running (was not killed) and
        // that its mutation only ever landed on the isolated copy, never on the exchange the route continued with
        childFinished.assertIsSatisfied();
    }

    @Test
    public void testExactlyOnceDeliveryUnderCompletionTimeoutRace() throws Exception {
        TOTAL_DELIVERIES.set(0);

        int fastCount = 20;
        int slowCount = 20;
        int total = fastCount + slowCount;

        MockEndpoint result = getMockEndpoint("mock:exactlyOnceResult");
        result.expectedMessageCount(fastCount);
        MockEndpoint timedOut = getMockEndpoint("mock:exactlyOnceTimedOut");
        timedOut.expectedMessageCount(slowCount);

        List<CompletableFuture<Exchange>> futures = new ArrayList<>();
        for (int i = 0; i < fastCount; i++) {
            futures.add(template.asyncSend("direct:exactlyOnce", exchange -> exchange.getIn().setHeader("slow", false)));
        }
        for (int i = 0; i < slowCount; i++) {
            futures.add(template.asyncSend("direct:exactlyOnce", exchange -> exchange.getIn().setHeader("slow", true)));
        }
        for (CompletableFuture<Exchange> future : futures) {
            future.get(15, TimeUnit.SECONDS);
        }

        assertMockEndpointsSatisfied();
        assertEquals(total, TOTAL_DELIVERIES.get(), "every exchange must be counted exactly once, never twice");
    }

    @Test
    public void test200ConcurrentExchangesCompleteWithBoundedThreadGrowth() throws Exception {
        int count = 200;

        MockEndpoint result = getMockEndpoint("mock:burstResult");
        result.expectedMessageCount(count);

        ThreadMXBean threadBean = ManagementFactory.getThreadMXBean();
        int before = threadBean.getThreadCount();
        final int[] peak = { before };

        AtomicBoolean polling = new AtomicBoolean(true);
        ExecutorService poller = Executors.newSingleThreadExecutor();
        poller.submit(() -> {
            while (polling.get()) {
                peak[0] = Math.max(peak[0], threadBean.getThreadCount());
                try {
                    Thread.sleep(20);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        });

        // use a small, bounded sender pool (well under the <64 bound below) so the assertion still meaningfully
        // reflects timebox's own thread usage, not just the harness's own concurrency machinery
        ExecutorService senders = Executors.newFixedThreadPool(20);
        try {
            List<Callable<Exchange>> tasks = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                tasks.add(() -> template.send("direct:burst", exchange -> exchange.getIn().setBody("burst")));
            }
            List<Future<Exchange>> results = senders.invokeAll(tasks, 30, TimeUnit.SECONDS);
            for (Future<Exchange> f : results) {
                f.get();
            }
        } finally {
            senders.shutdown();
            senders.awaitTermination(10, TimeUnit.SECONDS);
        }

        polling.set(false);
        poller.shutdown();
        poller.awaitTermination(5, TimeUnit.SECONDS);

        assertMockEndpointsSatisfied();

        int delta = peak[0] - before;
        assertTrue(delta < 64,
                "thread count must not grow roughly per-exchange (200 in flight); observed delta=" + delta);
    }

    @Test
    public void testNestingInnerSmallerBudgetWins() throws Exception {
        MockEndpoint result = getMockEndpoint("mock:nestInnerResult");
        result.expectedMessageCount(0);

        Exchange out = template.send("direct:nestInnerSmaller", exchange -> exchange.getIn().setBody("x"));

        assertMockEndpointsSatisfied();
        assertNotNull(out.getException());
        ExchangeTimedOutException timedOut = assertInstanceOf(ExchangeTimedOutException.class, out.getException());
        assertEquals(300L, timedOut.getTimeout(), "the smaller, inner budget must be the one that fires");
    }

    @Test
    public void testNestingOuterSmallerBudgetWins() throws Exception {
        MockEndpoint result = getMockEndpoint("mock:nestOuterResult");
        result.expectedMessageCount(0);

        Exchange out = template.send("direct:nestOuterSmaller", exchange -> exchange.getIn().setBody("x"));

        assertMockEndpointsSatisfied();
        assertNotNull(out.getException());
        ExchangeTimedOutException timedOut = assertInstanceOf(ExchangeTimedOutException.class, out.getException());
        assertEquals(300L, timedOut.getTimeout(), "the smaller, outer budget must be the one that fires");
    }

    @Test
    public void testAsyncChildEnforcedWithoutBlockingCallerThread() throws Exception {
        MockEndpoint timedOut = getMockEndpoint("mock:asyncChildTimedOut");
        timedOut.expectedMessageCount(1);

        long start = System.nanoTime();
        template.sendBody("direct:asyncChild", "x");
        long elapsedMillis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start);

        assertMockEndpointsSatisfied();
        // the child is asynchronous and sleeps for 1500ms; if the timeout mechanism genuinely does not rely on
        // blocking or interrupting the caller thread, the overall call returns close to the 300ms budget, not
        // anywhere near the child's full 1500ms duration
        assertTrue(elapsedMillis < 1200,
                "an async child's timeout must be enforced without waiting out the child's full duration; took "
                                         + elapsedMillis + "ms");
    }

    @Test
    public void testXmlRoundTripPreservesTimeoutMillisAttribute() throws Exception {
        RouteDefinition route = new RouteDefinition();
        route.from("direct:xmlRoundTrip");
        route.timebox(500).to("mock:xmlRoundTripResult").end();

        RoutesDefinition routes = new RoutesDefinition();
        routes.getRoutes().add(route);

        StringWriter sw = new StringWriter();
        new ModelWriter(sw).writeRoutesDefinition(routes);
        String xml = sw.toString();

        assertTrue(xml.contains("<timebox"), "dumped XML must contain the <timebox> element: " + xml);
        assertTrue(xml.contains("timeoutMillis=\"500\""), "dumped XML must contain the timeoutMillis attribute: " + xml);

        RoutesDefinition parsedRoutes = new ModelParser(new StringReader(xml)).parseRoutesDefinition()
                .orElseThrow(() -> new AssertionError("failed to parse dumped XML back into a model"));
        RouteDefinition parsedRoute = parsedRoutes.getRoutes().get(0);

        TimeboxDefinition timeboxDef = null;
        for (ProcessorDefinition<?> def : parsedRoute.getOutputs()) {
            if (def instanceof TimeboxDefinition td) {
                timeboxDef = td;
                break;
            }
        }
        assertNotNull(timeboxDef, "the parsed route must contain the round-tripped timebox definition");
        assertEquals("500", timeboxDef.getTimeoutMillis());
        assertEquals(1, timeboxDef.getOutputs().size());
    }

    @Override
    protected RouteBuilder createRouteBuilder() {
        return new RouteBuilder() {
            @Override
            public void configure() {
                onException(ExchangeTimedOutException.class)
                        .maximumRedeliveries(2)
                        .redeliveryDelay(0)
                        .handled(false);

                from("direct:happy")
                        .setProperty("propKey", constant("propVal"))
                        .timebox(5000)
                            .setHeader("insideHeader", constant("insideVal"))
                        .end()
                        .to("mock:happyResult");

                from("direct:timeout")
                        .onException(ExchangeTimedOutException.class).handled(false).end()
                        .timebox(300)
                            .delay(1500)
                            .to("mock:afterEndNotReachedInner")
                        .end()
                        .to("mock:afterEndNotReached");

                from("direct:onExceptionHealth")
                        .onException(ExchangeTimedOutException.class)
                            .handled(true)
                            .to("mock:onExceptionHandled")
                        .end()
                        .timebox(300)
                            .process(exchange -> {
                                Boolean fast = exchange.getIn().getHeader("fast", Boolean.class);
                            if (fast == null || !fast) {
                                Thread.sleep(1500);
                            }
                        })
                        .end()
                        .to("mock:onExceptionResult");

                from("direct:redelivery")
                        .timebox(300)
                        .process(exchange -> {
                            int count = REDELIVERY_COUNTER.incrementAndGet();
                            if (count < 3) {
                                Thread.sleep(1500);
                            }
                        })
                        .end()
                        .to("mock:redeliveryResult");

                from("direct:lateChild")
                        .onException(ExchangeTimedOutException.class)
                            .handled(true)
                            .to("mock:lateChildTimedOut")
                        .end()
                        .timebox(300)
                            .delay(1500)
                            .setBody(constant("MUTATED-BY-LATE-CHILD"))
                            .to("mock:lateChildFinishedLate")
                        .end()
                        .to("mock:lateChildResult");

                from("direct:exactlyOnce")
                        .onException(ExchangeTimedOutException.class)
                            .handled(true)
                            .process(exchange -> TOTAL_DELIVERIES.incrementAndGet())
                            .to("mock:exactlyOnceTimedOut")
                        .end()
                        .timebox(300)
                            .process(exchange -> {
                                Boolean slow = exchange.getIn().getHeader("slow", Boolean.class);
                            Thread.sleep(slow != null && slow ? 1500 : 10);
                        })
                        .end()
                        .process(exchange -> TOTAL_DELIVERIES.incrementAndGet())
                        .to("mock:exactlyOnceResult");

                from("direct:burst")
                        .onException(ExchangeTimedOutException.class).handled(false).end()
                        .timebox(1000)
                            .delay(10)
                        .end()
                        .to("mock:burstResult");

                from("direct:nestInnerSmaller")
                        .onException(ExchangeTimedOutException.class).handled(false).end()
                        .timebox(2000)
                            .timebox(300)
                                .delay(1500)
                            .end()
                        .end()
                        .to("mock:nestInnerResult");

                from("direct:nestOuterSmaller")
                        .onException(ExchangeTimedOutException.class).handled(false).end()
                        .timebox(300)
                            .timebox(2000)
                                .delay(1500)
                            .end()
                        .end()
                        .to("mock:nestOuterResult");

                from("direct:asyncChild")
                        .onException(ExchangeTimedOutException.class)
                            .handled(true)
                            .to("mock:asyncChildTimedOut")
                        .end()
                        .timebox(300)
                            .delay(1500).asyncDelayed()
                        .end()
                        .to("mock:asyncChildResult");
            }
        };
    }
}
