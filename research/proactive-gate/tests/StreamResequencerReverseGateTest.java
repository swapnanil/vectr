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

import org.apache.camel.ContextTestSupport;
import org.apache.camel.Exchange;
import org.apache.camel.Message;
import org.apache.camel.Processor;
import org.apache.camel.builder.RouteBuilder;
import org.junit.jupiter.api.Test;

/**
 * Acceptance test for stream-mode {@code reverse} on the Resequencer EIP.
 * Provided by the harness — implementations must make it pass unmodified.
 */
public class StreamResequencerReverseGateTest extends ContextTestSupport {

    protected void sendBodyAndHeader(String endpointUri, final Object body, final Object seqno) {
        template.send(endpointUri, new Processor() {
            public void process(Exchange exchange) {
                Message in = exchange.getIn();
                in.setBody(body);
                in.setHeader("seqnum", seqno);
            }
        });
    }

    @Test
    public void testStreamReverseDeliversDescendingSequenceOrder() throws Exception {
        getMockEndpoint("mock:result").expectedBodiesReceived("msg4", "msg3", "msg2", "msg1");

        sendBodyAndHeader("direct:start", "msg2", 2L);
        sendBodyAndHeader("direct:start", "msg4", 4L);
        sendBodyAndHeader("direct:start", "msg1", 1L);
        sendBodyAndHeader("direct:start", "msg3", 3L);

        assertMockEndpointsSatisfied();
    }

    @Test
    public void testStreamReverseInOrderInputIsDeliveredReversed() throws Exception {
        getMockEndpoint("mock:result").expectedBodiesReceived("msg3", "msg2", "msg1");

        sendBodyAndHeader("direct:start", "msg1", 1L);
        sendBodyAndHeader("direct:start", "msg2", 2L);
        sendBodyAndHeader("direct:start", "msg3", 3L);

        assertMockEndpointsSatisfied();
    }

    @Override
    protected RouteBuilder createRouteBuilder() {
        return new RouteBuilder() {
            @Override
            public void configure() {
                from("direct:start")
                        .resequence(header("seqnum")).stream().reverse()
                        .timeout(750)
                        .to("mock:result");
            }
        };
    }
}
