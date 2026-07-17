# Apache Camel Audit Report

## File 1: Calculator.java
**Path:** `components/camel-thrift/src/test/java/org/apache/camel/component/thrift/generated/Calculator.java`  
**Total Lines:** 6937

### Type Declarations (96 total)

1. Line 104: `Iface` (interface)
2. Line 154: `AsyncIface` (interface)
3. Line 178: `Client` (class)
4. Line 179: `Factory` (nested class in Client)
5. Line 374: `ping_call` (nested class in Client)
6. Line 413: `add_call` (nested class in Client)
7. Line 458: `calculate_call` (nested class in Client)
8. Line 503: `zip_call` (nested class in Client)
9. Line 541: `echo_call` (nested class in Client)
10. Line 588: `alltypes_call` (nested class in Client)
11. Line 659: `Processor<I>` (class)
12. Line 685: `ping` (nested ProcessFunction class in Processor)
13. Line 718: `add` (nested ProcessFunction class in Processor)
14. Line 752: `calculate` (nested ProcessFunction class in Processor)
15. Line 791: `zip` (nested ProcessFunction class in Processor)
16. Line 824: `echo` (nested ProcessFunction class in Processor)
17. Line 857: `alltypes` (nested ProcessFunction class in Processor)
18. Line 895: `AsyncProcessor<I>` (class)
19. Line 920: `ping` (nested AsyncProcessFunction class in AsyncProcessor)
20. Line 996: `add` (nested AsyncProcessFunction class in AsyncProcessor)
21. Line 1075: `calculate` (nested AsyncProcessFunction class in AsyncProcessor)
22. Line 1158: `zip` (nested AsyncProcessFunction class in AsyncProcessor)
23. Line 1207: `echo` (nested AsyncProcessFunction class in AsyncProcessor)
24. Line 1284: `alltypes` (nested AsyncProcessFunction class in AsyncProcessor)
25. Line 1367: `ping_args` (class)
26. Line 1368: `_Fields` (enum in ping_args)
27. Line 1570: `ping_argsStandardSchemeFactory` (nested class in ping_args)
28. Line 1577: `ping_argsStandardScheme` (nested class in ping_args)
29. Line 1612: `ping_argsTupleSchemeFactory` (nested class in ping_args)
30. Line 1619: `ping_argsTupleScheme` (nested class in ping_args)
31. Line 1639: `ping_result` (class)
32. Line 1650: `_Fields` (enum in ping_result)
33. Line 1841: `ping_resultStandardSchemeFactory` (nested class in ping_result)
34. Line 1848: `ping_resultStandardScheme` (nested class in ping_result)
35. Line 1884: `ping_resultTupleSchemeFactory` (nested class in ping_result)
36. Line 1891: `ping_resultTupleScheme` (nested class in ping_result)
37. Line 1912: `add_args` (class)
38. Line 1932: `_Fields` (enum in add_args)
39. Line 2143: `add_argsStandardSchemeFactory` (nested class in add_args)
40. Line 2150: `add_argsStandardScheme` (nested class in add_args)
41. Line 2190: `add_argsTupleSchemeFactory` (nested class in add_args)
42. Line 2197: `add_argsTupleScheme` (nested class in add_args)
43. Line 2211: `add_result` (class)
44. Line 2222: `_Fields` (enum in add_result)
45. Line 2413: `add_resultStandardSchemeFactory` (nested class in add_result)
46. Line 2420: `add_resultStandardScheme` (nested class in add_result)
47. Line 2456: `add_resultTupleSchemeFactory` (nested class in add_result)
48. Line 2463: `add_resultTupleScheme` (nested class in add_result)
49. Line 2483: `calculate_args` (class)
50. Line 2503: `_Fields` (enum in calculate_args)
51. Line 2729: `calculate_argsStandardSchemeFactory` (nested class in calculate_args)
52. Line 2736: `calculate_argsStandardScheme` (nested class in calculate_args)
53. Line 2793: `calculate_argsTupleSchemeFactory` (nested class in calculate_args)
54. Line 2800: `calculate_argsTupleScheme` (nested class in calculate_args)
55. Line 2818: `calculate_result` (class)
56. Line 2829: `_Fields` (enum in calculate_result)
57. Line 3020: `calculate_resultStandardSchemeFactory` (nested class in calculate_result)
58. Line 3027: `calculate_resultStandardScheme` (nested class in calculate_result)
59. Line 3076: `calculate_resultTupleSchemeFactory` (nested class in calculate_result)
60. Line 3083: `calculate_resultTupleScheme` (nested class in calculate_result)
61. Line 3103: `zip_args` (class)
62. Line 3123: `_Fields` (enum in zip_args)
63. Line 3349: `zip_argsStandardSchemeFactory` (nested class in zip_args)
64. Line 3356: `zip_argsStandardScheme` (nested class in zip_args)
65. Line 3413: `zip_argsTupleSchemeFactory` (nested class in zip_args)
66. Line 3420: `zip_argsTupleScheme` (nested class in zip_args)
67. Line 3438: `zip_result` (class)
68. Line 3449: `_Fields` (enum in zip_result)
69. Line 3640: `zip_resultStandardSchemeFactory` (nested class in zip_result)
70. Line 3647: `zip_resultStandardScheme` (nested class in zip_result)
71. Line 3683: `zip_resultTupleSchemeFactory` (nested class in zip_result)
72. Line 3690: `zip_resultTupleScheme` (nested class in zip_result)
73. Line 3710: `echo_args` (class)
74. Line 3730: `_Fields` (enum in echo_args)
75. Line 3956: `echo_argsStandardSchemeFactory` (nested class in echo_args)
76. Line 3963: `echo_argsStandardScheme` (nested class in echo_args)
77. Line 4020: `echo_argsTupleSchemeFactory` (nested class in echo_args)
78. Line 4027: `echo_argsTupleScheme` (nested class in echo_args)
79. Line 4045: `echo_result` (class)
80. Line 4056: `_Fields` (enum in echo_result)
81. Line 4247: `echo_resultStandardSchemeFactory` (nested class in echo_result)
82. Line 4254: `echo_resultStandardScheme` (nested class in echo_result)
83. Line 4290: `echo_resultTupleSchemeFactory` (nested class in echo_result)
84. Line 4297: `echo_resultTupleScheme` (nested class in echo_result)
85. Line 4838: `alltypes_args` (class)
86. Line 4888: `_Fields` (enum in alltypes_args)
87. Line 6109: `alltypes_argsStandardSchemeFactory` (nested class in alltypes_args)
88. Line 6116: `alltypes_argsStandardScheme` (nested class in alltypes_args)
89. Line 6351: `alltypes_argsTupleSchemeFactory` (nested class in alltypes_args)
90. Line 6358: `alltypes_argsTupleScheme` (nested class in alltypes_args)
91. Line 6549: `alltypes_result` (class)
92. Line 6566: `_Fields` (enum in alltypes_result)
93. Line 6839: `alltypes_resultStandardSchemeFactory` (nested class in alltypes_result)
94. Line 6846: `alltypes_resultStandardScheme` (nested class in alltypes_result)
95. Line 6895: `alltypes_resultTupleSchemeFactory` (nested class in alltypes_result)
96. Line 6902: `alltypes_resultTupleScheme` (nested class in alltypes_result)

### Role Summary
Auto-generated Thrift service implementation for the Calculator RPC service. Contains complete service definition (Iface, AsyncIface) with client, processor, and async processor implementations for six service methods (ping, add, calculate, zip, echo, alltypes). Each method has args/result data structures with Thrift serialization schemes (standard and tuple variants) for protocol-agnostic serialization/deserialization. Supports both synchronous and asynchronous call patterns with full type safety and field introspection.

---

## File 2: JavaDslModelWriter.java
**Path:** `core/camel-java-io/src/generated/java/org/apache/camel/java/out/JavaDslModelWriter.java`  
**Total Lines:** 6481

### Type Declarations (1 total)

1. Line 42: `JavaDslModelWriter` (class)

### Role Summary
Generated Java DSL model writer class extending JavaDslModelWriterSupport. Provides public write*Definition methods for approximately 200+ Camel model definition types (routes, processors, expressions, data formats, etc.), each handling serialization of model state to StringBuilder. Contains protected doWrite* implementation methods organized by model category, with recursive child element and attribute writing for complex nested structures. Serves as the DSL code generation backend for converting Camel model definitions back to executable Java DSL code.

---

## File 3: AbstractCamelContext.java
**Path:** `core/camel-base-engine/src/main/java/org/apache/camel/impl/engine/AbstractCamelContext.java`  
**Total Lines:** 4765

### Type Declarations (2 total)

1. Line 226: `AbstractCamelContext` (class)
2. Line 4703: `LifecycleHelper` (nested class in AbstractCamelContext)

### Role Summary
Core Camel context implementation managing context initialization, service lifecycle, route configuration, component/endpoint resolution, and template factories. Extends BaseService and implements CatalogCamelContext and Suspendable. Contains extensive field management for routes, components, endpoints, lifecycle strategies, and numerous configuration flags (streaming, tracing, debugging, message history). Provides methods for component/endpoint discovery, route management with startup/shutdown orchestration, shutdown policies, and factory creation for producers/consumers. Includes the deprecated LifecycleHelper nested class for MDC logging and thread context management.

---

## File 4: ProcessorDefinition.java
**Path:** `core/camel-core-model/src/main/java/org/apache/camel/model/ProcessorDefinition.java`  
**Total Lines:** 4533

### Type Declarations (1 total)

1. Line 79: `ProcessorDefinition` (abstract class with generic type parameter `<Type extends ProcessorDefinition<Type>>`, extends OptionalIdentifiedDefinition, implements Block, CopyableDefinition, DisabledAwareDefinition)

### Role Summary
Monolithic fluent API builder class for Camel route definitions, providing hundreds of chainable public methods for composing complex routing logic and message processing pipelines. Each builder method creates and configures a specific Definition object (e.g., MulticastDefinition, AggregateDefinition, EnrichDefinition, PollEnrichDefinition, OnCompletionDefinition), adds it via addOutput(), and returns either the configured Definition type or asType() for method chaining. Serves as the abstract base for strongly-typed subclasses throughout the Camel DSL model hierarchy, enabling fluent, type-safe construction of route definitions.

---

## File 5: ModelWriter.java
**Path:** `core/camel-xml-io/src/generated/java/org/apache/camel/xml/out/ModelWriter.java`  
**Total Lines:** 3973

### Type Declarations (2 total)

1. Line 46: `ModelWriter` (class)
2. Line 3970: `ElementSerializer<T>` (interface, nested in ModelWriter)

### Role Summary
Auto-generated XML model writer class extending BaseWriter for serializing Camel model definitions to XML. Contains approximately 200+ public write*Definition methods for various Camel processors, data formats, error handlers, REST components, and expressions, each with corresponding protected doWrite* implementation methods. Includes protected helper methods for XML attribute/element writing, type conversion (Boolean, Enum, Number, byte arrays), namespace handling, and list/element serialization. Defines a nested generic ElementSerializer<T> interface for pluggable element serialization. Serves as the XML DSL code generation backend for model-to-XML serialization.

---

## File 6: YamlModelWriter.java
**Path:** `core/camel-yaml-io/src/generated/java/org/apache/camel/yaml/out/YamlModelWriter.java`  
**Total Lines:** 3943

### Type Declarations (1 total)

1. Line 47: `YamlModelWriter` (class)

### Role Summary
Auto-generated YAML model writer class extending YamlModelWriterSupport for serializing Camel model definitions to YAML. Contains approximately 200+ public write*Definition methods for various Camel processors, data formats, error handlers, REST components, and expressions, each with corresponding protected doWrite* implementation methods. Includes protected helper methods for YAML node wrapping, value serialization, and reference resolution. Serves as the YAML DSL code generation backend for model-to-YAML serialization.

---

## File 7: SimpleTest.java
**Path:** `core/camel-core/src/test/java/org/apache/camel/language/simple/SimpleTest.java`  
**Total Lines:** 3932

### Type Declarations (5 total)

1. Line 72: `SimpleTest` (class)
2. Line 3855: `Animal` (nested static class)
3. Line 3891: `Order` (nested static class)
4. Line 3907: `OrderLine` (nested static class)
5. Line 3925: `MyClass` (nested static class)

### Role Summary
Comprehensive test class for the Simple language DSL, testing expression evaluation, predicates, and function capabilities. Contains 100+ test methods covering numeric operations, string manipulation, list/map operations, type conversions, OGNL expressions, filtering, aggregation, JSON path evaluation, and assertions. Includes four nested static helper classes (Animal, Order, OrderLine, MyClass) used as test data models for validating object property access, collection operations, and complex expression scenarios within the Simple language.

---

## File 8: HL723ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL723ConverterLoader.java`  
**Total Lines:** 3849

### Type Declarations (1 total)

1. Line 24: `HL723ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.3 type converter loader implementing TypeConverterLoader and CamelContextAware. Registers 200+ type converters for HL7 v2.3 message types (ACK, ADR_A19, ADT_A01 through VXX_V02, etc.) supporting conversions from byte[] and String to specific HL7 message classes. Each converter delegates to corresponding HL723Converter methods while handling null values. Serves as the runtime registration point for type converters specific to HL7 v2.3 protocol processing.

---

## File 9: OriginalSimpleTest.java
**Path:** `components/camel-csimple-joor/src/test/java/org/apache/camel/language/csimple/joor/OriginalSimpleTest.java`  
**Total Lines:** 3606

### Type Declarations (6 total)

1. Line 78: `OriginalSimpleTest` (class)
2. Line 3518: `Animal` (nested static final class)
3. Line 3554: `Greeter` (nested static final class)
4. Line 3566: `Order` (nested static final class)
5. Line 3582: `OrderLine` (nested static final class)
6. Line 3600: `MyClass` (nested static class)

### Role Summary
Comprehensive test class for the compiled Simple language (CSimple) with Joor runtime, providing 100+ test methods validating CSimple expression evaluation, predicates, and language features. Tests cover numeric operations, string manipulation, list/map operations, type conversions, OGNL expressions, filtering, aggregation, JSON path evaluation, and assertions. Includes five nested static helper classes (Animal, Greeter, Order, OrderLine, MyClass) used as test data models for validating object property access, method invocation, collection operations, and complex expression scenarios within the CSimple language.

---

## File 10: HL726ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL726ConverterLoader.java`  
**Total Lines:** 3512

### Type Declarations (1 total)

1. Line 24: `HL726ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.6 type converter loader implementing TypeConverterLoader and CamelContextAware. Registers 200+ type converters for HL7 v2.6 message types (ACK, ADR_A19, ADT_A01 through VXX_V02, etc.) supporting conversions from byte[] and String to specific HL7 message classes. Each converter delegates to corresponding HL726Converter methods while handling null values. Serves as the runtime registration point for type converters specific to HL7 v2.6 protocol processing.

---

## File 11: HL725ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL725ConverterLoader.java`  
**Total Lines:** 3305

### Type Declarations (1 total)

1. Line 24: `HL725ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.5 type converter loader implementing TypeConverterLoader and CamelContextAware. Registers 200+ type converters for HL7 v2.5 message types (ACK, ADR_A19, ADT_A01 through VXX_V02, etc.) supporting conversions from byte[] and String to specific HL7 message classes. Each converter delegates to corresponding HL725Converter methods while handling null values. Serves as the runtime registration point for type converters specific to HL7 v2.5 protocol processing.

---

## File 12: HL7251ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL7251ConverterLoader.java`  
**Total Lines:** 3272

### Type Declarations (1 total)

1. Line 24: `HL7251ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.5.1 type converter loader implementing TypeConverterLoader and CamelContextAware. Registers 200+ type converters for HL7 v2.5.1 message types (ACK, ADR_A19, ADT_A01 through VXX_V02, etc.) supporting conversions from byte[] and String to specific HL7 message classes. Each converter delegates to corresponding HL7251Converter methods while handling null values. Serves as the runtime registration point for type converters specific to HL7 v2.5.1 protocol processing.

---

## File 13: MXParser.java
**Path:** `core/camel-xml-io/src/main/java/org/apache/camel/xml/io/MXParser.java`  
**Total Lines:** 3220

### Type Declarations (1 total)

1. Line 51: `MXParser` (class)

### Role Summary
XML pull parser implementation extending XML 1.0 specification compliance with namespace support. Handles low-level character-by-character XML parsing including prolog (XML declaration, DOCTYPE, comments, processing instructions), element start/end tags with namespace processing and attributes, CDATA sections, entity references, and epilog. Provides buffer management with compaction/expansion, position tracking (line/column numbers), and state machine for recognizing XML syntax patterns (comments, PIs, CDATA, entities). Implements XmlPullParser interface for event-driven XML consumption.

---

## File 14: BaseMainSupport.java
**Path:** `core/camel-main/src/main/java/org/apache/camel/main/BaseMainSupport.java`  
**Total Lines:** 3179

### Type Declarations (3 total)

1. Line 141: `BaseMainSupport` (class)
2. Line 3116: `PropertyPlaceholderListener` (nested static final class)
3. Line 3133: `PlaceholderSummaryEventNotifier` (nested static class)

### Role Summary
Abstract base class for Camel main implementations supporting standalone bootstrapping with comprehensive configuration management. Coordinates lifecycle initialization (setup, pre-start, start, stop), property loading from multiple sources (files, environment variables, JVM system properties, cloud configurations), auto-configuration of CamelContext components/dataformats/languages with property binding and security policy enforcement. Manages theme customization, vault integration, SSL/JSSE configuration, debugger/tracer setup, route controller configuration, and health/telemetry services. Provides property loading pipeline with wildcard property support and configuration summary logging through two nested helper classes.

---

## File 15: DefaultConfigurationProperties.java
**Path:** `core/camel-main/src/main/java/org/apache/camel/main/DefaultConfigurationProperties.java`  
**Total Lines:** 2964

### Type Declarations (1 total)

1. Line 33: `DefaultConfigurationProperties<T>` (abstract class)

### Role Summary
Abstract generic base class for common configuration options shared across Camel Main, Camel Spring Boot, and other Camel runtime implementations. Defines ~100 configuration properties with full getter/setter method pairs covering stream caching (allowClasses, denyClasses, spooling, buffers), logging (messageHistory, sourceLocation, logMask, exhaustedMessageBody), JMX management (enabled, statistics, naming patterns, registration flags), bean introspection, route collection and reloading, exchange factory configuration, startup recorder profiles, and DSL compilation. Provides extensive fluent builder methods (with* naming pattern) supporting property chaining and override inheritance. Serves as foundation for runtime property binding to allow flexible configuration across different deployment scenarios.

---

## File 16: HL724ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL724ConverterLoader.java`  
**Total Lines:** 2920

### Type Declarations (1 total)

1. Line 24: `HL724ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.4 type converter loader implementing TypeConverterLoader and CamelContextAware interfaces. Registers 200+ type converters for HL7 v2.4 message types (ACK, ADR_A19, ADT_A01 through VXX_V02, etc.) supporting conversions from byte[] and String to specific HL7 message classes. Each converter delegates to corresponding HL724Converter methods with null-value handling and exception catching. Serves as the runtime registration point for type converters specific to HL7 v2.4 protocol processing during application startup.

---

## File 17: ModelDeserializers.java
**Path:** `dsl/camel-yaml-dsl/camel-yaml-dsl-deserializers/src/generated/java/org/apache/camel/dsl/yaml/deserializers/ModelDeserializers.java`  
**Total Lines:** 21146

### Type Declarations (235 total)

1. Line 267: `ModelDeserializers` (class)
2. Line 290: `A2ASubTaskDefinitionDeserializer` (nested static class)
3. Line 371: `ASN1DataFormatDeserializer` (nested static class)
4. Line 457: `AggregateDefinitionDeserializer` (nested static class)
5. Line 660: `ApiKeyDefinitionDeserializer` (nested static class)
6. Line 745: `AvroDataFormatDeserializer` (nested static class)
7. Line 892: `BarcodeDataFormatDeserializer` (nested static class)
8. Line 954: `Base64DataFormatDeserializer` (nested static class)
9. Line 1009: `BasicAuthDefinitionDeserializer` (nested static class)
10. Line 1057: `BatchResequencerConfigDeserializer` (nested static class)
11. Line 1113: `BeanConstructorDefinitionDeserializer` (nested static class)
12. Line 1151: `BeanConstructorsDefinitionDeserializer` (nested static class)
13. Line 1198: `BeanDefinitionDeserializer` (nested static class)
14. Line 1289: `BeanFactoryDefinitionDeserializer` (nested static class)
15. Line 1382: `BeanPropertiesDefinitionDeserializer` (nested static class)
16. Line 1419: `BeanPropertyDefinitionDeserializer` (nested static class)
17. Line 1476: `BeanioDataFormatDeserializer` (nested static class)
18. Line 1557: `BearerTokenDefinitionDeserializer` (nested static class)
19. Line 1612: `BindyDataFormatDeserializer` (nested static class)
20. Line 1691: `CBORDataFormatDeserializer` (nested static class)
21. Line 1786: `CSimpleExpressionDeserializer` (nested static class)
22. Line 1870: `CatchDefinitionDeserializer` (nested static class)
23. Line 1944: `ChoiceDefinitionDeserializer` (nested static class)
24. Line 2022: `CircuitBreakerDefinitionDeserializer` (nested static class)
25. Line 2113: `ClaimCheckDefinitionDeserializer` (nested static class)
26. Line 2186: `ComponentScanDefinitionDeserializer` (nested static class)
27. Line 2229: `ConstantExpressionDeserializer` (nested static class)
28. Line 2299: `ContextScanDefinitionDeserializer` (nested static class)
29. Line 2355: `ConvertBodyDefinitionDeserializer` (nested static class)
30. Line 2437: `ConvertHeaderDefinitionDeserializer` (nested static class)
31. Line 2524: `ConvertVariableDefinitionDeserializer` (nested static class)
32. Line 2612: `CryptoDataFormatDeserializer` (nested static class)
33. Line 2725: `CsvDataFormatDeserializer` (nested static class)
34. Line 2911: `CustomDataFormatDeserializer` (nested static class)
35. Line 2962: `CustomLoadBalancerDefinitionDeserializer` (nested static class)
36. Line 3016: `CustomTransformerDefinitionDeserializer` (nested static class)
37. Line 3082: `CustomValidatorDefinitionDeserializer` (nested static class)
38. Line 3125: `DataFormatDefinitionDeserializer` (nested static class)
39. Line 3215: `DataFormatTransformerDefinitionDeserializer` (nested static class)
40. Line 3561: `DataFormatsDefinitionDeserializer` (nested static class)
41. Line 4087: `DatasonnetExpressionDeserializer` (nested static class)
42. Line 4184: `DeadLetterChannelDefinitionDeserializer` (nested static class)
43. Line 4305: `DefaultErrorHandlerDefinitionDeserializer` (nested static class)
44. Line 4412: `DelayDefinitionDeserializer` (nested static class)
45. Line 4517: `DeleteDefinitionDeserializer` (nested static class)
46. Line 4670: `DfdlDataFormatDeserializer` (nested static class)
47. Line 4737: `DynamicRouterDefinitionDeserializer` (nested static class)
48. Line 4825: `EndpointTransformerDefinitionDeserializer` (nested static class)
49. Line 4891: `EndpointValidatorDefinitionDeserializer` (nested static class)
50. Line 4956: `EnrichDefinitionDeserializer` (nested static class)
51. Line 5085: `ErrorHandlerDefinitionDeserializer` (nested static class)
52. Line 5162: `ExchangePropertyExpressionDeserializer` (nested static class)
53. Line 5230: `FailoverLoadBalancerDefinitionDeserializer` (nested static class)
54. Line 5306: `FaultToleranceConfigurationDefinitionDeserializer` (nested static class)
55. Line 5423: `FhirJsonDataFormatDeserializer` (nested static class)
56. Line 5570: `FhirXmlDataFormatDeserializer` (nested static class)
57. Line 5706: `FilterDefinitionDeserializer` (nested static class)
58. Line 5787: `FinallyDefinitionDeserializer` (nested static class)
59. Line 5853: `FlatpackDataFormatDeserializer` (nested static class)
60. Line 5936: `ForyDataFormatDeserializer` (nested static class)
61. Line 6017: `GetDefinitionDeserializer` (nested static class)
62. Line 6167: `GlobalOptionDefinitionDeserializer` (nested static class)
63. Line 6209: `GlobalOptionsDefinitionDeserializer` (nested static class)
64. Line 6252: `GrokDataFormatDeserializer` (nested static class)
65. Line 6315: `GroovyExpressionDeserializer` (nested static class)
66. Line 6384: `GroovyJSonDataFormatDeserializer` (nested static class)
67. Line 6429: `GroovyXmlDataFormatDeserializer` (nested static class)
68. Line 6471: `GzipDeflaterDataFormatDeserializer` (nested static class)
69. Line 6513: `HL7DataFormatDeserializer` (nested static class)
70. Line 6589: `HeadDefinitionDeserializer` (nested static class)
71. Line 6741: `HeaderExpressionDeserializer` (nested static class)
72. Line 6809: `Hl7TerserExpressionDeserializer` (nested static class)
73. Line 6883: `IcalDataFormatDeserializer` (nested static class)
74. Line 6938: `IdempotentConsumerDefinitionDeserializer` (nested static class)
75. Line 7039: `InputTypeDefinitionDeserializer` (nested static class)
76. Line 7104: `InterceptDefinitionDeserializer` (nested static class)
77. Line 7175: `InterceptFromDefinitionDeserializer` (nested static class)
78. Line 7258: `InterceptSendToEndpointDefinitionDeserializer` (nested static class)
79. Line 7344: `Iso8583DataFormatDeserializer` (nested static class)
80. Line 7415: `JacksonXMLDataFormatDeserializer` (nested static class)
81. Line 7545: `JavaExpressionDeserializer` (nested static class)
82. Line 7627: `JavaScriptExpressionDeserializer` (nested static class)
83. Line 7715: `JaxbDataFormatDeserializer` (nested static class)
84. Line 7860: `JoorExpressionDeserializer` (nested static class)
85. Line 7943: `JqExpressionDeserializer` (nested static class)
86. Line 8018: `JsonApiDataFormatDeserializer` (nested static class)
87. Line 8091: `JsonDataFormatDeserializer` (nested static class)
88. Line 8260: `JsonPathExpressionDeserializer` (nested static class)
89. Line 8377: `JtaTransactionErrorHandlerDefinitionDeserializer` (nested static class)
90. Line 8487: `LZFDataFormatDeserializer` (nested static class)
91. Line 8535: `LangChain4jCharacterTokenizerDefinitionDeserializer` (nested static class)
92. Line 8598: `LangChain4jLineTokenizerDefinitionDeserializer` (nested static class)
93. Line 8661: `LangChain4jParagraphTokenizerDefinitionDeserializer` (nested static class)
94. Line 8724: `LangChain4jSentenceTokenizerDefinitionDeserializer` (nested static class)
95. Line 8783: `LangChain4jTokenizerDefinitionDeserializer` (nested static class)
96. Line 8846: `LangChain4jWordTokenizerDefinitionDeserializer` (nested static class)
97. Line 8908: `LanguageExpressionDeserializer` (nested static class)
98. Line 8982: `LoadBalanceDefinitionDeserializer` (nested static class)
99. Line 9085: `LoadTransformerDefinitionDeserializer` (nested static class)
100. Line 9159: `LogDefinitionDeserializer` (nested static class)
101. Line 9258: `LoopDefinitionDeserializer` (nested static class)
102. Line 9403: `MarshalDefinitionDeserializer` (nested static class)
103. Line 9720: `MethodCallExpressionDeserializer` (nested static class)
104. Line 9813: `MimeMultipartDataFormatDeserializer` (nested static class)
105. Line 9893: `MulticastDefinitionDeserializer` (nested static class)
106. Line 10012: `MutualTLSDefinitionDeserializer` (nested static class)
107. Line 10060: `MvelExpressionDeserializer` (nested static class)
108. Line 10126: `NoErrorHandlerDefinitionDeserializer` (nested static class)
109. Line 10171: `OAuth2DefinitionDeserializer` (nested static class)
110. Line 10247: `OcsfDataFormatDeserializer` (nested static class)
111. Line 10325: `OgnlExpressionDeserializer` (nested static class)
112. Line 10405: `OnCompletionDefinitionDeserializer` (nested static class)
113. Line 10514: `OnExceptionDefinitionDeserializer` (nested static class)
114. Line 10631: `OnFallbackDefinitionDeserializer` (nested static class)
115. Line 10693: `OnWhenDefinitionDeserializer` (nested static class)
116. Line 10764: `OpenApiDefinitionDeserializer` (nested static class)
117. Line 10845: `OpenIdConnectDefinitionDeserializer` (nested static class)
118. Line 10898: `OptimisticLockRetryPolicyDefinitionDeserializer` (nested static class)
119. Line 10961: `OtherwiseDefinitionDeserializer` (nested static class)
120. Line 11019: `OutputDefinitionDeserializer` (nested static class)
121. Line 11081: `OutputTypeDefinitionDeserializer` (nested static class)
122. Line 11154: `PGPDataFormatDeserializer` (nested static class)
123. Line 11270: `PQCDataFormatDeserializer` (nested static class)
124. Line 11346: `PackageScanDefinitionDeserializer` (nested static class)
125. Line 11405: `ParamDefinitionDeserializer` (nested static class)
126. Line 11496: `ParquetAvroDataFormatDeserializer` (nested static class)
127. Line 11577: `PatchDefinitionDeserializer` (nested static class)
128. Line 11731: `PausableDefinitionDeserializer` (nested static class)
129. Line 11799: `PipelineDefinitionDeserializer` (nested static class)
130. Line 11862: `PolicyDefinitionDeserializer` (nested static class)
131. Line 11933: `PollDefinitionDeserializer` (nested static class)
132. Line 12028: `PollEnrichDefinitionDeserializer` (nested static class)
133. Line 12168: `PostDefinitionDeserializer` (nested static class)
134. Line 12318: `PredicateValidatorDefinitionDeserializer` (nested static class)
135. Line 12366: `ProcessDefinitionDeserializer` (nested static class)
136. Line 12426: `PropertyDefinitionDeserializer` (nested static class)
137. Line 12472: `PropertyExpressionDefinitionDeserializer` (nested static class)
138. Line 12547: `ProtobufDataFormatDeserializer` (nested static class)
139. Line 12717: `PutDefinitionDeserializer` (nested static class)
140. Line 12870: `PythonExpressionDeserializer` (nested static class)
141. Line 12936: `RandomLoadBalancerDefinitionDeserializer` (nested static class)
142. Line 12995: `RecipientListDefinitionDeserializer` (nested static class)
143. Line 13162: `RedeliveryPolicyDefinitionDeserializer` (nested static class)
144. Line 13323: `RefErrorHandlerDefinitionDeserializer` (nested static class)
145. Line 13376: `RefExpressionDeserializer` (nested static class)
146. Line 13449: `RemoveHeaderDefinitionDeserializer` (nested static class)
147. Line 13519: `RemoveHeadersDefinitionDeserializer` (nested static class)
148. Line 13594: `RemovePropertiesDefinitionDeserializer` (nested static class)
149. Line 13668: `RemovePropertyDefinitionDeserializer` (nested static class)
150. Line 13737: `RemoveVariableDefinitionDeserializer` (nested static class)
151. Line 13809: `ResequenceDefinitionDeserializer` (nested static class)
152. Line 13919: `Resilience4jConfigurationDefinitionDeserializer` (nested static class)
153. Line 14080: `ResponseHeaderDefinitionDeserializer` (nested static class)
154. Line 14158: `ResponseMessageDefinitionDeserializer` (nested static class)
155. Line 14235: `RestBindingDefinitionDeserializer` (nested static class)
156. Line 14370: `RestConfigurationDefinitionDeserializer` (nested static class)
157. Line 14556: `RestContextRefDefinitionDeserializer` (nested static class)
158. Line 14624: `RestDefinitionDeserializer` (nested static class)
159. Line 14809: `RestPropertyDefinitionDeserializer` (nested static class)
160. Line 14858: `RestSecuritiesDefinitionDeserializer` (nested static class)
161. Line 14960: `RestsDefinitionDeserializer` (nested static class)
162. Line 15020: `ResumableDefinitionDeserializer` (nested static class)
163. Line 15096: `RollbackDefinitionDeserializer` (nested static class)
164. Line 15168: `RoundRobinLoadBalancerDefinitionDeserializer` (nested static class)
165. Line 15209: `RouteBuilderDefinitionDeserializer` (nested static class)
166. Line 15257: `RouteConfigurationContextRefDefinitionDeserializer` (nested static class)
167. Line 15300: `RouteContextRefDefinitionDeserializer` (nested static class)
168. Line 15349: `RouteDefinitionDeserializer` (nested static class)
169. Line 15415: `RouteTemplateParameterDefinitionDeserializer` (nested static class)
170. Line 15478: `RoutingSlipDefinitionDeserializer` (nested static class)
171. Line 15564: `RssDataFormatDeserializer` (nested static class)
172. Line 15626: `SSLContextParametersDefinitionDeserializer` (nested static class)
173. Line 15801: `SagaDefinitionDeserializer` (nested static class)
174. Line 15900: `SamplingDefinitionDeserializer` (nested static class)
175. Line 15974: `ScriptDefinitionDeserializer` (nested static class)
176. Line 16043: `SecurityDefinitionDeserializer` (nested static class)
177. Line 16092: `SetBodyDefinitionDeserializer` (nested static class)
178. Line 16165: `SetExchangePatternDefinitionDeserializer` (nested static class)
179. Line 16235: `SetHeaderDefinitionDeserializer` (nested static class)
180. Line 16312: `SetHeadersDefinitionDeserializer` (nested static class)
181. Line 16377: `SetPropertyDefinitionDeserializer` (nested static class)
182. Line 16456: `SetVariableDefinitionDeserializer` (nested static class)
183. Line 16533: `SetVariablesDefinitionDeserializer` (nested static class)
184. Line 16599: `SimpleExpressionDeserializer` (nested static class)
185. Line 16683: `SmooksDataFormatDeserializer` (nested static class)
186. Line 16735: `SoapDataFormatDeserializer` (nested static class)
187. Line 16820: `SortDefinitionDeserializer` (nested static class)
188. Line 16897: `SpELExpressionDeserializer` (nested static class)
189. Line 16984: `SplitDefinitionDeserializer` (nested static class)
190. Line 17135: `SpringTransactionErrorHandlerDefinitionDeserializer` (nested static class)
191. Line 17248: `StepDefinitionDeserializer` (nested static class)
192. Line 17307: `StickyLoadBalancerDefinitionDeserializer` (nested static class)
193. Line 17354: `StopDefinitionDeserializer` (nested static class)
194. Line 17413: `StreamResequencerConfigDeserializer` (nested static class)
195. Line 17479: `SwiftMtDataFormatDeserializer` (nested static class)
196. Line 17532: `SwiftMxDataFormatDeserializer` (nested static class)
197. Line 17589: `SyslogDataFormatDeserializer` (nested static class)
198. Line 17632: `TarFileDataFormatDeserializer` (nested static class)
199. Line 17692: `TemplatedRouteParameterDefinitionDeserializer` (nested static class)
200. Line 17746: `ThreadPoolProfileDefinitionDeserializer` (nested static class)
201. Line 17848: `ThreadsDefinitionDeserializer` (nested static class)
202. Line 17956: `ThriftDataFormatDeserializer` (nested static class)
203. Line 18027: `ThrottleDefinitionDeserializer` (nested static class)
204. Line 18136: `ThrowExceptionDefinitionDeserializer` (nested static class)
205. Line 18214: `ToDefinitionDeserializer` (nested static class)
206. Line 18312: `ToDynamicDefinitionDeserializer` (nested static class)
207. Line 18425: `TokenizerDefinitionDeserializer` (nested static class)
208. Line 18522: `TokenizerExpressionDeserializer` (nested static class)
209. Line 18629: `TokenizerImplementationDefinitionDeserializer` (nested static class)
210. Line 18666: `TopicLoadBalancerDefinitionDeserializer` (nested static class)
211. Line 18710: `TransactedDefinitionDeserializer` (nested static class)
212. Line 18778: `TransformDataTypeDefinitionDeserializer` (nested static class)
213. Line 18847: `TransformDefinitionDeserializer` (nested static class)
214. Line 18919: `TransformersDefinitionDeserializer` (nested static class)
215. Line 19004: `TryDefinitionDeserializer` (nested static class)
216. Line 19090: `UniVocityCsvDataFormatDeserializer` (nested static class)
217. Line 19236: `UniVocityFixedDataFormatDeserializer` (nested static class)
218. Line 19361: `UniVocityHeaderDeserializer` (nested static class)
219. Line 19420: `UniVocityTsvDataFormatDeserializer` (nested static class)
220. Line 19588: `UnmarshalDefinitionDeserializer` (nested static class)
221. Line 19908: `ValidateDefinitionDeserializer` (nested static class)
222. Line 19984: `ValidatorsDefinitionDeserializer` (nested static class)
223. Line 20052: `ValueDefinitionDeserializer` (nested static class)
224. Line 20099: `VariableExpressionDeserializer` (nested static class)
225. Line 20167: `WasmExpressionDeserializer` (nested static class)
226. Line 20243: `WeightedLoadBalancerDefinitionDeserializer` (nested static class)
227. Line 20303: `WhenDefinitionDeserializer` (nested static class)
228. Line 20390: `WireTapDefinitionDeserializer` (nested static class)
229. Line 20518: `XMLSecurityDataFormatDeserializer` (nested static class)
230. Line 20630: `XMLTokenizerExpressionDeserializer` (nested static class)
231. Line 20732: `XPathExpressionDeserializer` (nested static class)
232. Line 20857: `XQueryExpressionDeserializer` (nested static class)
233. Line 20952: `YAMLDataFormatDeserializer` (nested static class)
234. Line 21051: `ZipDeflaterDataFormatDeserializer` (nested static class)
235. Line 21099: `ZipFileDataFormatDeserializer` (nested static class)

### Role Summary
Auto-generated YAML deserializer registry class produced by camel-yaml-dsl-maven-plugin. Provides single public class (ModelDeserializers) extending YamlDeserializerSupport containing 234 nested static deserializer classes, one for each Camel model type (definitions, dataformats, expressions, error handlers, validators, transformers, REST components, security definitions, etc.). Each nested class extends YamlDeserializerBase<T> and implements YAML unmarshalling for its corresponding model type. Enables parsing of YAML DSL route definitions into Java model objects at runtime.

---

## File 18: StaticEndpointBuilders.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/StaticEndpointBuilders.java`  
**Total Lines:** 18252

### Type Declarations (1 total)

1. Line 27: `StaticEndpointBuilders` (class)

### Role Summary
Auto-generated static endpoint builder factory class containing ~100+ static methods providing fluent DSL access to Camel endpoint builders for all 300+ components (activemq, jms, http, kafka, file, database, etc.). Each method instantiates the corresponding component's *EndpointBuilderFactory with typed builder parameters and returns a fluent builder instance for endpoint configuration. This class serves as the centralized entry point for endpoint DSL usage in Java-based route definitions, allowing developers to configure endpoints with type-safe property binding and IDE autocomplete support.

---

## File 19: KeycloakProducer.java
**Path:** `components/camel-keycloak/src/main/java/org/apache/camel/component/keycloak/KeycloakProducer.java`  
**Total Lines:** 2913

### Type Declarations (1 total)

1. Line 69: `KeycloakProducer` (class)

### Role Summary
Hand-written Keycloak producer component implementation extending DefaultProducer, providing CRUD operation methods for managing Keycloak resources via the Keycloak Admin API. Contains private handler methods for operations on realms, users, roles, groups, clients, client roles, permissions, resources, identity providers, organization members, and organization identity providers. The main process() method dispatches incoming exchange messages to appropriate operation handlers based on exchange headers. Supports comprehensive realm and user management functionality within the Camel integration framework.

---

## File 20: ModelParser.java
**Path:** `core/camel-xml-io/src/generated/java/org/apache/camel/xml/in/ModelParser.java`  
**Total Lines:** 2895

### Type Declarations (1 total)

1. Line 52: `ModelParser` (class)

### Role Summary
Auto-generated XML model parser class produced by camel-maven-packaging. Extends BaseParser and provides parsing methods for 200+ Camel model types (definitions, data formats, expressions, error handlers, validators, transformers, REST components, etc.). Each protected doParse*() method unmarshals XML elements into corresponding Java model objects using attribute and element handlers with switch-based dispatch. Enables parsing of XML DSL route definitions and configurations into Java model objects at runtime for the Camel integration framework.

---

## File 21: HL7231ConverterLoader.java
**Path:** `components/camel-hl7/src/generated/java/org/apache/camel/component/hl7/HL7231ConverterLoader.java`  
**Total Lines:** 2840

### Type Declarations (1 total)

1. Line 24: `HL7231ConverterLoader` (class)

### Role Summary
Auto-generated HL7 v2.31 type converter loader class produced by camel-maven-packaging (TypeConverterLoaderGeneratorMojo). Implements TypeConverterLoader and CamelContextAware interfaces. Registers hundreds of type converters for converting between byte arrays and HL7 message objects (ACK, ADR_A19, ADT_A01 through ADT_A47, BAR_P01 through BAR_P12, and other HL7 message types) for both byte[] and String source formats. Each converter calls corresponding HL7231Converter static methods with appropriate type casts. Used at runtime to resolve type conversions during HL7 message processing.

---

## File 22: ExpressionBuilder.java
**Path:** `core/camel-support/src/main/java/org/apache/camel/support/builder/ExpressionBuilder.java`  
**Total Lines:** 2824

### Type Declarations (1 total)

1. Line 74: `ExpressionBuilder` (class)

### Role Summary
Utility class providing static factory methods for creating Expression and Predicate instances covering all Camel DSL expression types (headers, properties, variables, body, exchange, simple, constant, class loading, type conversion, collection operations, and more). Supports 100+ expression builders for accessing exchange state (message headers, properties, body, attachments) and performing transformations (trimming, converting, splitting, grouping, encoding/decoding). Used extensively in route DSLs to define message flow logic and conditional routing using a fluent API with compile-time type safety.

---

## File 23: HL726Converter.java
**Path:** `components/camel-hl7/src/main/java/org/apache/camel/component/hl7/HL726Converter.java`  
**Total Lines:** 2456

### Type Declarations (1 total)

1. Line 254: `HL726Converter` (class)

### Role Summary
HL7 v2.6 type converter class providing static conversion methods between String/byte[] and 100+ distinct HL7 v2.6 message type objects. Initializes DefaultHapiContext with ParserConfiguration at load time and provides pair of converter methods (String and byte[] variants) for each message type (ACK, ADR_A19, ADT_A01 through ADT_A54, BAR_P01 through BAR_P12, CCD_CCD, CNQ_C07, DEL_U09, etc.). Each converter delegates to helper methods toMessage() that parse HL7 wire format into corresponding ca.uhn.hl7v2 message objects, with HL7Exception and IOException handling. Annotated with @Converter to enable auto-registration via generateLoader mechanism for type-safe conversions during Camel message processing.

---

## File 24: OracleConnectorEmbeddedDebeziumConfiguration.java
**Path:** `components/camel-debezium/camel-debezium-oracle/src/generated/java/org/apache/camel/component/debezium/oracle/configuration/OracleConnectorEmbeddedDebeziumConfiguration.java`  
**Total Lines:** 2330

### Type Declarations (1 total)

1. Line 14: `OracleConnectorEmbeddedDebeziumConfiguration` (class)

### Role Summary
Auto-generated Debezium Oracle connector configuration class produced by camel-maven-packaging (GenerateConnectorConfigMojo). Extends EmbeddedDebeziumConfiguration and provides 200+ @UriParam-annotated configuration fields for Oracle CDC, including database connectivity (hostname, port, user, password, url, connection timeout), LogMiner configuration (batch size, threading, buffer type, SCN gap detection), snapshot settings (mode, locking, fetch size, delay), schema history (file storage, DDL handling), and transformation options (decimal/binary/interval/time precision modes). Includes comprehensive getter/setter pairs and a toConfiguration() method that populates Debezium Configuration object with field values. Required fields: databasePassword and topicPrefix. Also provides connector class configuration and validation logic.

---

## File 25: HL725Converter.java
**Path:** `components/camel-hl7/src/main/java/org/apache/camel/component/hl7/HL725Converter.java`  
**Total Lines:** 2313

### Type Declarations (1 total)

1. Line 241: `HL725Converter` (class)

### Role Summary
HL7 v2.5 type converter class providing static conversion methods between String/byte[] and 100+ distinct HL7 v2.5 message type objects. Initializes DefaultHapiContext with ParserConfiguration at load time and provides pair of converter methods (String and byte[] variants) for each message type (ACK, ADR_A19, ADT_A01 through ADT_A60, BAR_P01 through BAR_P12, and 150+ additional message types). Each converter delegates to helper methods toMessage() that parse HL7 wire format into corresponding ca.uhn.hl7v2 message objects, with HL7Exception and IOException handling. Annotated with @Converter to enable auto-registration via generateLoader mechanism for type-safe conversions during Camel message processing.

---

## File 26: HL7251Converter.java
**Path:** `components/camel-hl7/src/main/java/org/apache/camel/component/hl7/HL7251Converter.java`  
**Total Lines:** 2291

### Type Declarations (1 total)

1. Line 239: `HL7251Converter` (class)

### Role Summary
HL7 v2.51 type converter class providing static conversion methods between String/byte[] and 100+ distinct HL7 v2.51 message type objects. Initializes DefaultHapiContext with ParserConfiguration at load time and provides pair of converter methods (String and byte[] variants) for each message type (ACK, ADR_A19, ADT_A01 through ADT_A17, ADT_A20, ADT_A21, RSP_K25, RSP_K31, RSP_Q11, RSP_Z82, RSP_Z86, RSP_Z88, RSP_Z90, RTB_K13, RTB_Knn, RTB_Z74, SIU_S12, SPQ_Q08, SQM_S25, SQR_S25, SRM_S01, SRR_S01, SSR_U04, SSU_U03, SUR_P09, TBR_R08, TCU_U10, UDM_Q05, VQQ_Q07, VXQ_V01, VXR_V03, VXU_V04, VXX_V02, etc.). Each converter delegates to helper methods toMessage() that parse HL7 wire format into corresponding ca.uhn.hl7v2 message objects, with HL7Exception and IOException handling. Annotated with @Converter to enable auto-registration via generateLoader mechanism for type-safe conversions during Camel message processing.

---

## File 27: KafkaConfiguration.java
**Path:** `components/camel-kafka/src/main/java/org/apache/camel/component/kafka/KafkaConfiguration.java`  
**Total Lines:** 2286

### Type Declarations (1 total)

1. Line 62: `KafkaConfiguration` (class)

### Role Summary
Kafka component configuration class implementing Cloneable and HeaderFilterStrategyAware, providing 100+ @UriParam-annotated fields for comprehensive Kafka broker, consumer, and producer configuration. Configuration covers topic routing, consumer group management, connection timeouts, polling behavior, offset management (autocommit, manual, repository-based), fetch sizing, serialization options, SSL/TLS security settings, SASL authentication types (PLAIN, SCRAM, Kerberos, AWS IAM), interceptors, idempotence settings, batching strategies, worker pool threading, and additional arbitrary Kafka properties. Includes extensive getter/setter pairs for all configuration fields, HeaderFilterStrategy integration, and serialization customization support.

---

## File 28: PropertyBindingSupport.java
**Path:** `core/camel-support/src/main/java/org/apache/camel/support/PropertyBindingSupport.java`  
**Total Lines:** 2162

### Type Declarations (7 total)

1. Line 109: `PropertyBindingSupport` (class)
2. Line 1725: `OnAutowiring` (interface)
3. Line 1743: `Builder` (nested class)
4. Line 2021: `OptionPrefixMap` (nested class)
5. Line 2061: `FlattenMap` (nested class)
6. Line 2121: `PropertyBindingKeyComparator` (nested class)
7. Line 2150: `MapConfigurer` (nested class)

### Role Summary
PropertyBindingSupport provides a fluent builder API and static utility methods for binding String-valued properties to objects using comprehensive convention support. The main class exposes static factory methods (build(), bindProperties(), bindWithFlattenProperties(), setPropertiesOnTarget()) for configurable property binding. Nested classes include: OnAutowiring interface for callbacks during bean autowiring, Builder class for fluent configuration of binding behavior (property removal, flattening, case sensitivity, reference resolution, placeholders, nesting levels, private setters, reflection usage, configurer selection). Internal helper classes OptionPrefixMap and FlattenMap manage property filtering/flattening, PropertyBindingKeyComparator sorts keys by nesting depth and reference type, and MapConfigurer provides singleton Map-based configuration. Comprehensive support for OGNL nested paths, bean references (#bean:, #class:, #type:), autowiring (#autowired), type-safe conversions (#valueAs()), map/list/array indexing, optional parameters, and dual binding strategies (configurer-based and reflection-based) for maximum flexibility in property binding scenarios.

---

## File 29: DoclingProducer.java
**Path:** `components/camel-ai/camel-docling/src/main/java/org/apache/camel/component/docling/DoclingProducer.java`  
**Total Lines:** 2149

### Type Declarations (1 total)

1. Line 93: `DoclingProducer` (public class extends DefaultProducer)

### Role Summary
Producer implementation for the Docling document processing component supporting multiple output formats (markdown, HTML, JSON, text). Handles both synchronous and asynchronous document conversion via the docling-serve REST API or local CLI execution. Implements configurable chunking strategies (HybridChunker, HierarchicalChunker), OCR support, metadata extraction, and secure batch processing with concurrent task management via CompletableFuture and configurable ExecutorService. Features comprehensive configuration options for Docling API parameters, includes POSIX-based secure temporary file management (700 for directories, 600 for files) with automatic cleanup via SynchronizationAdapter, and enforces allowlist-based validation of custom CLI arguments with path traversal detection.

---

## File 30: MockEndpoint.java
**Path:** `components/camel-mock/src/main/java/org/apache/camel/component/mock/MockEndpoint.java`  
**Total Lines:** 2140

### Type Declarations (2 total)

1. Line 100: `MockEndpoint` (class)
2. Line 2111: `MockAssertionTask` (class)

### Role Summary

MockEndpoint is a test fixture component providing a fluent, JMock-style API for testing Camel routes and mediation rules. It accepts messages via the mock: endpoint URI and supports two method categories: expectedXXX/expectsXXX (pre-conditions) and assertXXX (post-execution assertions). The endpoint manages received exchanges in memory with configurable retention strategies, supports flexible assertion execution with fail-fast mode and grace periods, and integrates with external assertion types (AssertionTask interface, AssertionClause abstract class, AssertionClauseTask implementations defined in peer files within org.apache.camel.component.mock) for composable, builder-style assertion chains.

---

## File 31: ActiveMQEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ActiveMQEndpointBuilderFactory.java`  
**Total Lines:** 7591

### Type Declarations (9 total)

1. Line 36: `ActiveMQEndpointBuilderFactory` (interface)
2. Line 41: `ActiveMQEndpointConsumerBuilder` (interface)
3. Line 866: `AdvancedActiveMQEndpointConsumerBuilder` (interface)
4. Line 2830: `ActiveMQEndpointProducerBuilder` (interface)
5. Line 3649: `AdvancedActiveMQEndpointProducerBuilder` (interface)
6. Line 5483: `ActiveMQEndpointBuilder` (interface)
7. Line 7281: `ActiveMQBuilders` (interface)
8. Line 7355: `ActiveMQHeaderNameBuilder` (public static class)
9. Line 7584: `ActiveMQEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo). Provides a fluent DSL for ActiveMQ component endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (ActiveMQEndpointConsumerBuilder with advanced variant), producer-side configuration (ActiveMQEndpointProducerBuilder with advanced variant), and combined configuration (ActiveMQEndpointBuilder). Includes ActiveMQBuilders interface for entry point methods and ActiveMQHeaderNameBuilder static inner class providing methods that return String constants for JMS message header names (JMSDestination, JMSMessageID, JMSCorrelationID, JMSDeliveryMode, JMSExpiration, JMSPriority, JMSRedelivered, JMSTimestamp, JMSReplyTo, JMSType, JMSXUserID, etc.). Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction.

---

## File 32: ActiveMQ6EndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ActiveMQ6EndpointBuilderFactory.java`  
**Total Lines:** 7591

### Type Declarations (9 total)

1. Line 36: `ActiveMQ6EndpointBuilderFactory` (interface)
2. Line 41: `ActiveMQ6EndpointConsumerBuilder` (interface)
3. Line 866: `AdvancedActiveMQ6EndpointConsumerBuilder` (interface)
4. Line 2830: `ActiveMQ6EndpointProducerBuilder` (interface)
5. Line 3649: `AdvancedActiveMQ6EndpointProducerBuilder` (interface)
6. Line 5483: `ActiveMQ6EndpointBuilder` (interface)
7. Line 7281: `ActiveMQ6Builders` (interface)
8. Line 7355: `ActiveMQ6HeaderNameBuilder` (public static class)
9. Line 7584: `ActiveMQ6EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for ActiveMQ 6.x component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (ActiveMQ6EndpointConsumerBuilder with advanced variant), producer-side configuration (ActiveMQ6EndpointProducerBuilder with advanced variant), and combined configuration (ActiveMQ6EndpointBuilder). Includes ActiveMQ6Builders interface for entry point methods and ActiveMQ6HeaderNameBuilder static inner class providing methods that return String constants for JMS message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of ActiveMQ 6.x message routing endpoints.

---

## File 33: JmsEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JmsEndpointBuilderFactory.java`  
**Total Lines:** 7540

### Type Declarations (9 total)

1. Line 35: `JmsEndpointBuilderFactory` (interface)
2. Line 40: `JmsEndpointConsumerBuilder` (interface)
3. Line 865: `AdvancedJmsEndpointConsumerBuilder` (interface)
4. Line 2782: `JmsEndpointProducerBuilder` (interface)
5. Line 3601: `AdvancedJmsEndpointProducerBuilder` (interface)
6. Line 5435: `JmsEndpointBuilder` (interface)
7. Line 7233: `JmsBuilders` (interface)
8. Line 7304: `JmsHeaderNameBuilder` (public static class)
9. Line 7533: `JmsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JMS component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (JmsEndpointConsumerBuilder with advanced variant), producer-side configuration (JmsEndpointProducerBuilder with advanced variant), and combined configuration (JmsEndpointBuilder). Includes JmsBuilders interface for entry point methods and JmsHeaderNameBuilder static inner class providing methods that return String constants for JMS message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of JMS message routing endpoints.

---

## File 34: AMQPEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/AMQPEndpointBuilderFactory.java`  
**Total Lines:** 7540

### Type Declarations (9 total)

1. Line 35: `AMQPEndpointBuilderFactory` (interface)
2. Line 40: `AMQPEndpointConsumerBuilder` (interface)
3. Line 865: `AdvancedAMQPEndpointConsumerBuilder` (interface)
4. Line 2782: `AMQPEndpointProducerBuilder` (interface)
5. Line 3601: `AdvancedAMQPEndpointProducerBuilder` (interface)
6. Line 5435: `AMQPEndpointBuilder` (interface)
7. Line 7233: `AMQPBuilders` (interface)
8. Line 7304: `AMQPHeaderNameBuilder` (public static class)
9. Line 7533: `AMQPEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for AMQP component supporting Apache Qpid Client messaging protocol. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (AMQPEndpointConsumerBuilder with advanced variant), producer-side configuration (AMQPEndpointProducerBuilder with advanced variant), and combined configuration (AMQPEndpointBuilder). Includes AMQPBuilders interface for entry point methods and AMQPHeaderNameBuilder static inner class providing methods that return String constants for AMQP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of AMQP message routing endpoints.

---

## File 35: MinaSftpEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MinaSftpEndpointBuilderFactory.java`  
**Total Lines:** 7170

### Type Declarations (10 total)

1. Line 35: `MinaSftpEndpointBuilderFactory` (interface)
2. Line 40: `MinaSftpEndpointConsumerBuilder` (interface)
3. Line 2605: `AdvancedMinaSftpEndpointConsumerBuilder` (interface)
4. Line 3670: `MinaSftpEndpointProducerBuilder` (interface)
5. Line 4605: `AdvancedMinaSftpEndpointProducerBuilder` (interface)
6. Line 5549: `MinaSftpEndpointBuilder` (interface)
7. Line 6234: `AdvancedMinaSftpEndpointBuilder` (interface)
8. Line 6860: `MinaSftpBuilders` (interface)
9. Line 6934: `MinaSftpHeaderNameBuilder` (public static class)
10. Line 7164: `MinaSftpEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for MINA SFTP component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (MinaSftpEndpointConsumerBuilder with advanced variant), producer-side configuration (MinaSftpEndpointProducerBuilder with advanced variant), and combined configuration (MinaSftpEndpointBuilder). Includes MinaSftpBuilders interface for entry point methods and MinaSftpHeaderNameBuilder static inner class providing methods that return String constants for FTP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of SFTP message routing endpoints using Apache MINA SSHD.

---

## File 36: SftpEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/SftpEndpointBuilderFactory.java`  
**Total Lines:** 7086

### Type Declarations (10 total)

1. Line 35: `SftpEndpointBuilderFactory` (interface)
2. Line 40: `SftpEndpointConsumerBuilder` (interface)
3. Line 2619: `AdvancedSftpEndpointConsumerBuilder` (interface)
4. Line 3643: `SftpEndpointProducerBuilder` (interface)
5. Line 4592: `AdvancedSftpEndpointProducerBuilder` (interface)
6. Line 5495: `SftpEndpointBuilder` (interface)
7. Line 6194: `AdvancedSftpEndpointBuilder` (interface)
8. Line 6779: `SftpBuilders` (interface)
9. Line 6850: `SftpHeaderNameBuilder` (public static class)
10. Line 7080: `SftpEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for SFTP component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (SftpEndpointConsumerBuilder with advanced variant), producer-side configuration (SftpEndpointProducerBuilder with advanced variant), and combined configuration (SftpEndpointBuilder). Includes SftpBuilders interface for entry point methods and SftpHeaderNameBuilder static inner class providing methods that return String constants for FTP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of SFTP message routing endpoints.

---

## File 37: FtpsEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FtpsEndpointBuilderFactory.java`  
**Total Lines:** 6539

### Type Declarations (10 total)

1. Line 35: `FtpsEndpointBuilderFactory` (interface)
2. Line 40: `FtpsEndpointConsumerBuilder` (interface)
3. Line 2411: `AdvancedFtpsEndpointConsumerBuilder` (interface)
4. Line 3415: `FtpsEndpointProducerBuilder` (interface)
5. Line 4268: `AdvancedFtpsEndpointProducerBuilder` (interface)
6. Line 5099: `FtpsEndpointBuilder` (interface)
7. Line 5702: `AdvancedFtpsEndpointBuilder` (interface)
8. Line 6229: `FtpsBuilders` (interface)
9. Line 6303: `FtpsHeaderNameBuilder` (public static class)
10. Line 6533: `FtpsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FTPS component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (FtpsEndpointConsumerBuilder with advanced variant), producer-side configuration (FtpsEndpointProducerBuilder with advanced variant), and combined configuration (FtpsEndpointBuilder). Includes FtpsBuilders interface for entry point methods and FtpsHeaderNameBuilder static inner class providing methods that return String constants for FTP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of FTPS message routing endpoints.

---

## File 38: KafkaEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KafkaEndpointBuilderFactory.java`  
**Total Lines:** 5909

### Type Declarations (10 total)

1. Line 35: `KafkaEndpointBuilderFactory` (interface)
2. Line 40: `KafkaEndpointConsumerBuilder` (interface)
3. Line 2089: `AdvancedKafkaEndpointConsumerBuilder` (interface)
4. Line 2354: `KafkaEndpointProducerBuilder` (interface)
5. Line 4488: `AdvancedKafkaEndpointProducerBuilder` (interface)
6. Line 4650: `KafkaEndpointBuilder` (interface)
7. Line 5583: `AdvancedKafkaEndpointBuilder` (interface)
8. Line 5663: `KafkaBuilders` (interface)
9. Line 5726: `KafkaHeaderNameBuilder` (public static class)
10. Line 5903: `KafkaEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kafka component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (KafkaEndpointConsumerBuilder with advanced variant), producer-side configuration (KafkaEndpointProducerBuilder with advanced variant), and combined configuration (KafkaEndpointBuilder). Includes KafkaBuilders interface for entry point methods and KafkaHeaderNameBuilder static inner class providing methods that return String constants for Kafka message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of Kafka message routing endpoints.

---

## File 39: ClientEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ClientEndpointBuilderFactory.java`  
**Total Lines:** 1944

### Type Declarations (10 total)

1. Line 36: `ClientEndpointBuilderFactory` (interface)
2. Line 41: `ClientEndpointConsumerBuilder` (interface)
3. Line 521: `AdvancedClientEndpointConsumerBuilder` (interface)
4. Line 649: `ClientEndpointProducerBuilder` (interface)
5. Line 1130: `AdvancedClientEndpointProducerBuilder` (interface)
6. Line 1186: `ClientEndpointBuilder` (interface)
7. Line 1668: `AdvancedClientEndpointBuilder` (interface)
8. Line 1678: `ClientBuilders` (interface)
9. Line 1743: `ClientHeaderNameBuilder` (public static class)
10. Line 1938: `ClientEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HTTP Client component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (ClientEndpointConsumerBuilder with advanced variant), producer-side configuration (ClientEndpointProducerBuilder with advanced variant), and combined configuration (ClientEndpointBuilder). Includes ClientBuilders interface for entry point methods and ClientHeaderNameBuilder static inner class providing methods that return String constants for HTTP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of HTTP client message routing endpoints.

---

## File 40: CloudtrailEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CloudtrailEndpointBuilderFactory.java`  
**Total Lines:** 1229

### Type Declarations (6 total)

1. Line 35: `CloudtrailEndpointBuilderFactory` (interface)
2. Line 40: `CloudtrailEndpointBuilder` (interface)
3. Line 914: `AdvancedCloudtrailEndpointBuilder` (interface)
4. Line 1106: `CloudtrailBuilders` (interface)
5. Line 1165: `CloudtrailHeaderNameBuilder` (public static class)
6. Line 1223: `CloudtrailEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for AWS CloudTrail component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. This component has a simplified builder structure (no consumer/producer split) reflecting its audit-logging-only nature. Contains single endpoint builder interface (CloudtrailEndpointBuilder with advanced variant) and CloudtrailBuilders interface for entry point methods. Includes CloudtrailHeaderNameBuilder static inner class providing methods that return String constants for AWS CloudTrail message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of AWS CloudTrail audit log routing endpoints.

---

## File 41: CoAPEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CoAPEndpointBuilderFactory.java`  
**Total Lines:** 1495

### Type Declarations (10 total)

1. Line 36: `CoAPEndpointBuilderFactory` (interface)
2. Line 41: `CoAPEndpointConsumerBuilder` (interface)
3. Line 390: `AdvancedCoAPEndpointConsumerBuilder` (interface)
4. Line 550: `CoAPEndpointProducerBuilder` (interface)
5. Line 859: `AdvancedCoAPEndpointProducerBuilder` (interface)
6. Line 977: `CoAPEndpointBuilder` (interface)
7. Line 1253: `AdvancedCoAPEndpointBuilder` (interface)
8. Line 1295: `CoAPBuilders` (interface)
9. Line 1417: `CoAPHeaderNameBuilder` (public static class)
10. Line 1489: `CoAPEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Constrained Application Protocol (CoAP) component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (CoAPEndpointConsumerBuilder with advanced variant), producer-side configuration (CoAPEndpointProducerBuilder with advanced variant), and combined configuration (CoAPEndpointBuilder). Includes CoAPBuilders interface for entry point methods and CoAPHeaderNameBuilder static inner class providing methods that return String constants for CoAP message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of CoAP/DTLS-secured IoT message routing endpoints.

---

## File 42: CometdEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CometdEndpointBuilderFactory.java`  
**Total Lines:** 1376

### Type Declarations (10 total)

1. Line 36: `CometdEndpointBuilderFactory` (interface)
2. Line 41: `CometdEndpointConsumerBuilder` (interface)
3. Line 353: `AdvancedCometdEndpointConsumerBuilder` (interface)
4. Line 511: `CometdEndpointProducerBuilder` (interface)
5. Line 826: `AdvancedCometdEndpointProducerBuilder` (interface)
6. Line 912: `CometdEndpointBuilder` (interface)
7. Line 1194: `AdvancedCometdEndpointBuilder` (interface)
8. Line 1234: `CometdBuilders` (interface)
9. Line 1337: `CometdHeaderNameBuilder` (public static class)
10. Line 1370: `CometdEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Cometd component supporting Bayeux protocol messaging. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (CometdEndpointConsumerBuilder with advanced variant), producer-side configuration (CometdEndpointProducerBuilder with advanced variant), and combined configuration (CometdEndpointBuilder). Includes CometdBuilders interface for entry point methods and CometdHeaderNameBuilder static inner class providing methods that return String constants for Cometd message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of Cometd-based real-time messaging routing endpoints.

---

## File 43: ControlBusEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ControlBusEndpointBuilderFactory.java`  
**Total Lines:** 311

### Type Declarations (5 total)

1. Line 35: `ControlBusEndpointBuilderFactory` (interface)
2. Line 40: `ControlBusEndpointBuilder` (interface)
3. Line 186: `AdvancedControlBusEndpointBuilder` (interface)
4. Line 241: `ControlBusBuilders` (interface)
5. Line 305: `ControlBusEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for ControlBus component. Provides a fluent DSL for endpoint configuration through minimal nested interfaces reflecting this component's specialized internal nature. Omits consumer/producer builder distinction and header name builder, as ControlBus is primarily used for framework-internal control operations rather than message routing. Contains single endpoint builder interface (ControlBusEndpointBuilder with advanced variant) and ControlBusBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of control message endpoints.

---

## File 44: CosmosDbEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CosmosDbEndpointBuilderFactory.java`  
**Total Lines:** 2151

### Type Declarations (9 total)

1. Line 35: `CosmosDbEndpointBuilderFactory` (interface)
2. Line 40: `CosmosDbEndpointConsumerBuilder` (interface)
3. Line 689: `AdvancedCosmosDbEndpointConsumerBuilder` (interface)
4. Line 851: `CosmosDbEndpointProducerBuilder` (interface)
5. Line 1458: `AdvancedCosmosDbEndpointProducerBuilder` (interface)
6. Line 1548: `CosmosDbEndpointBuilder` (interface)
7. Line 2041: `AdvancedCosmosDbEndpointBuilder` (interface)
8. Line 2085: `CosmosDbBuilders` (interface)
9. Line 2145: `CosmosDbEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Azure Cosmos DB component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (CosmosDbEndpointConsumerBuilder with advanced variant), producer-side configuration (CosmosDbEndpointProducerBuilder with advanced variant), and combined configuration (CosmosDbEndpointBuilder). Notably omits the header name builder class, indicating limited header metadata for this document database component. Includes CosmosDbBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of Azure Cosmos DB endpoint routing.

---

## File 45: CouchDbEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CouchDbEndpointBuilderFactory.java`  
**Total Lines:** 763

### Type Declarations (10 total)

1. Line 36: `CouchDbEndpointBuilderFactory` (interface)
2. Line 41: `CouchDbEndpointConsumerBuilder` (interface)
3. Line 254: `AdvancedCouchDbEndpointConsumerBuilder` (interface)
4. Line 382: `CouchDbEndpointProducerBuilder` (interface)
5. Line 452: `AdvancedCouchDbEndpointProducerBuilder` (interface)
6. Line 508: `CouchDbEndpointBuilder` (interface)
7. Line 579: `AdvancedCouchDbEndpointBuilder` (interface)
8. Line 589: `CouchDbBuilders` (interface)
9. Line 676: `CouchDbHeaderNameBuilder` (public static class)
10. Line 757: `CouchDbEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for CouchDB component. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (CouchDbEndpointConsumerBuilder with advanced variant), producer-side configuration (CouchDbEndpointProducerBuilder with advanced variant), and combined configuration (CouchDbEndpointBuilder). Includes CouchDbBuilders interface for entry point methods and CouchDbHeaderNameBuilder static inner class providing methods that return String constants for CouchDB message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of CouchDB document routing endpoints.

---

## File 46: CouchbaseEndpointBuilderFactory.java
**Path:** `dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CouchbaseEndpointBuilderFactory.java`  
**Total Lines:** 1971

### Type Declarations (10 total)

1. Line 36: `CouchbaseEndpointBuilderFactory` (interface)
2. Line 41: `CouchbaseEndpointConsumerBuilder` (interface)
3. Line 909: `AdvancedCouchbaseEndpointConsumerBuilder` (interface)
4. Line 1165: `CouchbaseEndpointProducerBuilder` (interface)
5. Line 1457: `AdvancedCouchbaseEndpointProducerBuilder` (interface)
6. Line 1605: `CouchbaseEndpointBuilder` (interface)
7. Line 1703: `AdvancedCouchbaseEndpointBuilder` (interface)
8. Line 1805: `CouchbaseBuilders` (interface)
9. Line 1884: `CouchbaseHeaderNameBuilder` (public static class)
10. Line 1965: `CouchbaseEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Couchbase component supporting NoSQL document database operations. Provides a fluent DSL for endpoint configuration through multiple nested interfaces implementing the builder pattern. Contains separate builder interfaces for consumer-side configuration (CouchbaseEndpointConsumerBuilder with advanced variant), producer-side configuration (CouchbaseEndpointProducerBuilder with advanced variant), and combined configuration (CouchbaseEndpointBuilder). Includes CouchbaseBuilders interface for entry point methods and CouchbaseHeaderNameBuilder static inner class providing methods that return String constants for Couchbase message header names. Factory method endpointBuilder() creates a local inner class instance implementing both endpoint and advanced builder interfaces for fluent API construction of Couchbase document routing endpoints.

---

## Phase 13: Files 47-50

### File 47
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CronEndpointBuilderFactory.java  
**Total Lines:** 246

**Type Declarations:** 5 total

1. Line 36: `CronEndpointBuilderFactory` (public interface)
2. Line 41: `CronEndpointBuilder` (public interface)
3. Line 68: `AdvancedCronEndpointBuilder` (public interface)
4. Line 194: `CronBuilders` (public interface)
5. Line 240: `CronEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Cron component providing job scheduling via Quartz or Spring Scheduling. Minimal builder structure due to cron being primarily a consumer endpoint with limited configuration. Provides fluent DSL through CronEndpointBuilder interface and its advanced variant. Contains CronBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class implementing both endpoint and advanced builder interfaces for fluent API construction of scheduled task triggering.

---

### File 48
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Cw2EndpointBuilderFactory.java  
**Total Lines:** 825

**Type Declarations:** 6 total

1. Line 35: `Cw2EndpointBuilderFactory` (public interface)
2. Line 40: `Cw2EndpointBuilder` (public interface)
3. Line 498: `AdvancedCw2EndpointBuilder` (public interface)
4. Line 583: `Cw2Builders` (public interface)
5. Line 642: `Cw2HeaderNameBuilder` (public static class)
6. Line 819: `Cw2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Cw2 component supporting Code Weather (CW) data lookups via HTTP integration. Provides fluent DSL through CronEndpointBuilder interface and its advanced variant for configurable HTTP query parameters. Contains Cw2Builders interface for entry point methods and Cw2HeaderNameBuilder static inner class providing String constants for message header names. Streamlined builder structure optimized for HTTP-based weather service integration with focused configuration options.

---

### File 49
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CxfEndpointBuilderFactory.java  
**Total Lines:** 2729

**Type Declarations:** 11 total

1. Line 36: `CxfEndpointBuilderFactory` (public interface)
2. Line 41: `CxfEndpointConsumerBuilder` (public interface)
3. Line 354: `AdvancedCxfEndpointConsumerBuilder` (public interface)
4. Line 886: `CxfEndpointProducerBuilder` (public interface)
5. Line 1353: `AdvancedCxfEndpointProducerBuilder` (public interface)
6. Line 1843: `CxfEndpointBuilder` (public interface)
7. Line 2158: `AdvancedCxfEndpointBuilder` (public interface)
8. Line 2572: `CxfBuilders` (public interface)
9. Line 2642: `CxfHeaderNameBuilder` (public static class)
10. Line 2723: `CxfEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for CXF component supporting SOAP web services via Apache CXF runtime. Large builder factory supporting both consumer-side (WSDL-based inbound) and producer-side (SOAP client) configurations with full advanced variants and separate combined endpoint configuration. Extensive parameter coverage for WSDL import, service/port/operation binding, authentication/authorization, WS-Addressing and WS-SecurityPolicy options. Contains CxfBuilders interface for entry point methods and CxfHeaderNameBuilder static inner class providing String constants for SOAP header manipulation.

---

### File 50
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CxfRsEndpointBuilderFactory.java  
**Total Lines:** 2373

**Type Declarations:** 10 total

1. Line 36: `CxfRsEndpointBuilderFactory` (public interface)
2. Line 41: `CxfRsEndpointConsumerBuilder` (public interface)
3. Line 351: `AdvancedCxfRsEndpointConsumerBuilder` (public interface)
4. Line 762: `CxfRsEndpointProducerBuilder` (public interface)
5. Line 1123: `AdvancedCxfRsEndpointProducerBuilder` (public interface)
6. Line 1576: `CxfRsEndpointBuilder` (public interface)
7. Line 1813: `AdvancedCxfRsEndpointBuilder` (public interface)
8. Line 2090: `CxfRsBuilders` (public interface)
9. Line 2160: `CxfRsHeaderNameBuilder` (public static class)
10. Line 2367: `CxfRsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for CXF-RS component supporting RESTful web services via Apache CXF runtime. Provides comprehensive builder support for both consumer-side (JAX-RS servlet) and producer-side (HTTP client) RESTful operations with full advanced variants and combined endpoint configuration. Extensive parameter coverage for servlet/context path binding, HTTP method routing, JAX-RS provider configuration, authentication/authorization, and CORS settings. Contains CxfRsBuilders interface for entry point methods and CxfRsHeaderNameBuilder static inner class providing String constants for HTTP header manipulation in RESTful contexts.

---

## Phase 14: Files 51-54

### File 51
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/CyberArkVaultEndpointBuilderFactory.java  
**Total Lines:** 365

**Type Declarations:** 5 total

1. Line 35: `CyberArkVaultEndpointBuilderFactory` (public interface)
2. Line 40: `CyberArkVaultEndpointBuilder` (public interface)
3. Line 260: `AdvancedCyberArkVaultEndpointBuilder` (public interface)
4. Line 315: `CyberArkVaultBuilders` (public interface)
5. Line 359: `CyberArkVaultEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for CyberArk Vault component providing secure credential management and secret retrieval. Minimal builder structure optimized for authentication credential handling via HTTP API calls to CyberArk vault servers. Provides fluent DSL through CyberArkVaultEndpointBuilder interface and its advanced variant. Contains CyberArkVaultBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class implementing both endpoint and advanced builder interfaces for fluent API construction of vault-based credential endpoints.

---

### File 52
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DJLEndpointBuilderFactory.java  
**Total Lines:** 284

**Type Declarations:** 6 total

1. Line 36: `DJLEndpointBuilderFactory` (public interface)
2. Line 41: `DJLEndpointBuilder` (public interface)
3. Line 127: `AdvancedDJLEndpointBuilder` (public interface)
4. Line 182: `DJLBuilders` (public interface)
5. Line 244: `DJLHeaderNameBuilder` (public static class)
6. Line 278: `DJLEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Deep Java Library (DJL) component providing machine learning model serving and inference. Compact builder structure optimized for ML model management and prediction endpoints. Provides fluent DSL through DJLEndpointBuilder interface and its advanced variant. Contains DJLBuilders interface for entry point methods and DJLHeaderNameBuilder static inner class providing String constants for ML model metadata header names. Factory method endpointBuilder() creates a local inner class implementing both endpoint and advanced builder interfaces for fluent API construction of ML inference endpoints.

---

### File 53
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DMSEndpointBuilderFactory.java  
**Total Lines:** 647

**Type Declarations:** 5 total

1. Line 36: `DMSEndpointBuilderFactory` (public interface)
2. Line 41: `DMSEndpointBuilder` (public interface)
3. Line 540: `AdvancedDMSEndpointBuilder` (public interface)
4. Line 595: `DMSBuilders` (public interface)
5. Line 641: `DMSEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for AWS DMS (Database Migration Service) component supporting database migration and replication operations. Provides fluent DSL through DMSEndpointBuilder interface and its advanced variant for configurable migration task parameters. Contains DMSBuilders interface for entry point methods. Streamlined builder structure optimized for database migration workflow integration with focused configuration options for source/target databases and replication tasks.

---

### File 54
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DaprEndpointBuilderFactory.java  
**Total Lines:** 2155

**Type Declarations:** 11 total

1. Line 35: `DaprEndpointBuilderFactory` (public interface)
2. Line 40: `DaprEndpointConsumerBuilder` (public interface)
3. Line 183: `AdvancedDaprEndpointConsumerBuilder` (public interface)
4. Line 343: `DaprEndpointProducerBuilder` (public interface)
5. Line 1078: `AdvancedDaprEndpointProducerBuilder` (public interface)
6. Line 1166: `DaprEndpointBuilder` (public interface)
7. Line 1311: `AdvancedDaprEndpointBuilder` (public interface)
8. Line 1353: `DaprBuilders` (public interface)
9. Line 1416: `DaprHeaderNameBuilder` (public static class)
10. Line 2149: `DaprEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Dapr (Distributed Application Runtime) component providing microservices building blocks via HTTP/gRPC APIs. Large builder factory supporting both consumer-side (event subscription) and producer-side (service invocation) configurations with full advanced variants and separate combined endpoint configuration. Extensive parameter coverage for service/actor invocation, pub/sub topics, state management, and secrets APIs. Contains DaprBuilders interface for entry point methods and DaprHeaderNameBuilder static inner class providing String constants for Dapr runtime context header names.

---

## Phase 15: Files 55-58

### File 55
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DataFormatEndpointBuilderFactory.java  
**Total Lines:** 165

**Type Declarations:** 5 total

1. Line 35: `DataFormatEndpointBuilderFactory` (public interface)
2. Line 40: `DataFormatEndpointBuilder` (public interface)
3. Line 52: `AdvancedDataFormatEndpointBuilder` (public interface)
4. Line 107: `DataFormatBuilders` (public interface)
5. Line 159: `DataFormatEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DataFormat component providing pluggable data format encoding/decoding. Minimal builder structure with minimal configuration parameters. Provides fluent DSL through DataFormatEndpointBuilder interface and its advanced variant. Contains DataFormatBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class implementing both endpoint and advanced builder interfaces for fluent API construction of data transformation endpoints.

---

### File 56
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DataLakeEndpointBuilderFactory.java  
**Total Lines:** 4021

**Type Declarations:** 10 total

1. Line 35: `DataLakeEndpointBuilderFactory` (public interface)
2. Line 40: `DataLakeEndpointConsumerBuilder` (public interface)
3. Line 1299: `AdvancedDataLakeEndpointConsumerBuilder` (public interface)
4. Line 1463: `DataLakeEndpointProducerBuilder` (public interface)
5. Line 2266: `AdvancedDataLakeEndpointProducerBuilder` (public interface)
6. Line 2322: `DataLakeEndpointBuilder` (public interface)
7. Line 3094: `AdvancedDataLakeEndpointBuilder` (public interface)
8. Line 3104: `DataLakeBuilders` (public interface)
9. Line 3171: `DataLakeHeaderNameBuilder` (public static class)
10. Line 4015: `DataLakeEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Azure Data Lake Storage component supporting ADLS Gen2 file operations via Azure SDK. Very large builder factory supporting both consumer-side (file/directory listing and blob operations) and producer-side (file upload/download and metadata operations) configurations with full advanced variants and combined endpoint configuration. Extensive parameter coverage for authentication (MSI, service principal), storage account configuration, blob hierarchy (container, directory, file paths), and advanced options (encryption, retention, tier management). Contains DataLakeBuilders interface and DataLakeHeaderNameBuilder static inner class providing String constants for ADLS metadata headers.

---

### File 57
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DataSetEndpointBuilderFactory.java  
**Total Lines:** 1098

**Type Declarations:** 10 total

1. Line 35: `DataSetEndpointBuilderFactory` (public interface)
2. Line 40: `DataSetEndpointConsumerBuilder` (public interface)
3. Line 197: `AdvancedDataSetEndpointConsumerBuilder` (public interface)
4. Line 357: `DataSetEndpointProducerBuilder` (public interface)
5. Line 509: `AdvancedDataSetEndpointProducerBuilder` (public interface)
6. Line 935: `DataSetEndpointBuilder` (public interface)
7. Line 970: `AdvancedDataSetEndpointBuilder` (public interface)
8. Line 1012: `DataSetBuilders` (public interface)
9. Line 1071: `DataSetHeaderNameBuilder` (public static class)
10. Line 1092: `DataSetEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DataSet component providing test data generation and message validation. Supports both consumer-side (expected message list validation) and producer-side (message population and assertion) configurations with full advanced variants. Contains builder interfaces for fluent endpoint configuration with parameters for list/queue definition, timing control, and assertion modes. Includes DataSetBuilders interface for entry point methods and DataSetHeaderNameBuilder static inner class providing String constants for internal test metadata headers.

---

### File 58
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DataSetTestEndpointBuilderFactory.java  
**Total Lines:** 732

**Type Declarations:** 5 total

1. Line 36: `DataSetTestEndpointBuilderFactory` (public interface)
2. Line 41: `DataSetTestEndpointBuilder` (public interface)
3. Line 253: `AdvancedDataSetTestEndpointBuilder` (public interface)
4. Line 678: `DataSetTestBuilders` (public interface)
5. Line 726: `DataSetTestEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DataSet Test component providing test assertion and data validation. Focused builder structure optimized for message assertion and validation in testing scenarios. Provides fluent DSL through DataSetTestEndpointBuilder interface and its advanced variant for test assertion configuration. Contains DataSetTestBuilders interface for entry point methods. Factory method endpointBuilder() creates a local inner class implementing both endpoint and advanced builder interfaces for fluent API construction of test validation endpoints.

---

## Phase 16: Files 59-62

### File 59
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Ddb2EndpointBuilderFactory.java  
**Total Lines:** 1379

**Type Declarations:** 6 total

1. Line 35: `Ddb2EndpointBuilderFactory` (public interface)
2. Line 40: `Ddb2EndpointBuilder` (public interface)
3. Line 582: `AdvancedDdb2EndpointBuilder` (public interface)
4. Line 669: `Ddb2Builders` (public interface)
5. Line 728: `Ddb2HeaderNameBuilder` (public static class)
6. Line 1373: `Ddb2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IBM Db2 database component providing SQL query execution via JDBC. Provides fluent DSL through Ddb2EndpointBuilder interface and its advanced variant for configurable query parameters and result processing. Contains Ddb2Builders interface for entry point methods and Ddb2HeaderNameBuilder static inner class providing String constants for result metadata headers (row counts, result set info). Streamlined builder structure optimized for database query routing with focused configuration options for JDBC connectivity and query execution.

---

### File 60
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Ddb2StreamEndpointBuilderFactory.java  
**Total Lines:** 1173

**Type Declarations:** 5 total

1. Line 35: `Ddb2StreamEndpointBuilderFactory` (public interface)
2. Line 40: `Ddb2StreamEndpointBuilder` (public interface)
3. Line 931: `AdvancedDdb2StreamEndpointBuilder` (public interface)
4. Line 1123: `Ddb2StreamBuilders` (public interface)
5. Line 1167: `Ddb2StreamEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IBM Db2 data server with streaming support component. Provides fluent DSL through Ddb2StreamEndpointBuilder interface and its advanced variant for streaming result set processing and batched query execution. Contains Ddb2StreamBuilders interface for entry point methods. Streamlined builder structure optimized for high-throughput database operations with focused configuration options for streaming cursor management and batch processing.

---

### File 61
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumDb2EndpointBuilderFactory.java  
**Total Lines:** 2521

**Type Declarations:** 6 total

1. Line 35: `DebeziumDb2EndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumDb2EndpointBuilder` (public interface)
3. Line 2229: `AdvancedDebeziumDb2EndpointBuilder` (public interface)
4. Line 2355: `DebeziumDb2Builders` (public interface)
5. Line 2416: `DebeziumDb2HeaderNameBuilder` (public static class)
6. Line 2515: `DebeziumDb2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium Db2 CDC (Change Data Capture) component providing streaming database changes from IBM Db2. Large builder supporting continuous change stream capture with extensive parameters for connector configuration, database credentials, database/table filtering, snapshot control, heartbeat and resumability options. Provides fluent DSL through DebeziumDb2EndpointBuilder interface with advanced variant for fine-grained control over change event processing. Contains DebeziumDb2Builders interface and DebeziumDb2HeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

### File 62
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumMongodbEndpointBuilderFactory.java  
**Total Lines:** 2380

**Type Declarations:** 6 total

1. Line 35: `DebeziumMongodbEndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumMongodbEndpointBuilder` (public interface)
3. Line 2088: `AdvancedDebeziumMongodbEndpointBuilder` (public interface)
4. Line 2214: `DebeziumMongodbBuilders` (public interface)
5. Line 2275: `DebeziumMongodbHeaderNameBuilder` (public static class)
6. Line 2374: `DebeziumMongodbEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium MongoDB CDC (Change Data Capture) component providing streaming database changes from MongoDB. Large builder supporting continuous change stream capture with extensive parameters for connector configuration, MongoDB connection strings, replica set/sharded cluster configuration, database/collection filtering, snapshot control, and resumability options. Provides fluent DSL through DebeziumMongodbEndpointBuilder interface with advanced variant for fine-grained control over change stream event processing. Contains DebeziumMongodbBuilders interface and DebeziumMongodbHeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

## Phase 17: Files 63-66

### File 63
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumMySqlEndpointBuilderFactory.java  
**Total Lines:** 3410

**Type Declarations:** 6 total

1. Line 35: `DebeziumMySqlEndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumMySqlEndpointBuilder` (public interface)
3. Line 3118: `AdvancedDebeziumMySqlEndpointBuilder` (public interface)
4. Line 3244: `DebeziumMySqlBuilders` (public interface)
5. Line 3305: `DebeziumMySqlHeaderNameBuilder` (public static class)
6. Line 3404: `DebeziumMySqlEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium MySQL CDC (Change Data Capture) component providing streaming database changes from MySQL databases. Large builder supporting continuous change stream capture with extensive parameters for connector configuration, MySQL connection strings, database/table filtering, snapshot control, log offset tracking, and resumability options. Provides fluent DSL through DebeziumMySqlEndpointBuilder interface with advanced variant for fine-grained control over change stream event processing. Contains DebeziumMySqlBuilders interface and DebeziumMySqlHeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

### File 64
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumOracleEndpointBuilderFactory.java  
**Total Lines:** 3954

**Type Declarations:** 6 total

1. Line 35: `DebeziumOracleEndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumOracleEndpointBuilder` (public interface)
3. Line 3662: `AdvancedDebeziumOracleEndpointBuilder` (public interface)
4. Line 3788: `DebeziumOracleBuilders` (public interface)
5. Line 3849: `DebeziumOracleHeaderNameBuilder` (public static class)
6. Line 3948: `DebeziumOracleEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium Oracle CDC (Change Data Capture) component providing streaming database changes from Oracle databases. Largest Debezium builder file supporting continuous change stream capture with extensive parameters for connector configuration, Oracle connection strings (TNS names), database/table filtering, LogMiner configuration, snapshot control, and resumability options. Provides fluent DSL through DebeziumOracleEndpointBuilder interface with advanced variant for fine-grained control over change stream event processing. Contains DebeziumOracleBuilders interface and DebeziumOracleHeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

### File 65
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumPostgresEndpointBuilderFactory.java  
**Total Lines:** 3311

**Type Declarations:** 6 total

1. Line 35: `DebeziumPostgresEndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumPostgresEndpointBuilder` (public interface)
3. Line 3019: `AdvancedDebeziumPostgresEndpointBuilder` (public interface)
4. Line 3145: `DebeziumPostgresBuilders` (public interface)
5. Line 3206: `DebeziumPostgresHeaderNameBuilder` (public static class)
6. Line 3305: `DebeziumPostgresEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium PostgreSQL CDC (Change Data Capture) component providing streaming database changes from PostgreSQL databases. Large builder supporting continuous change stream capture with extensive parameters for connector configuration, PostgreSQL connection strings, database/table filtering, logical decoding slot configuration, snapshot control, and resumability options. Provides fluent DSL through DebeziumPostgresEndpointBuilder interface with advanced variant for fine-grained control over change stream event processing. Contains DebeziumPostgresBuilders interface and DebeziumPostgresHeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

### File 66
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DebeziumSqlserverEndpointBuilderFactory.java  
**Total Lines:** 2794

**Type Declarations:** 6 total

1. Line 35: `DebeziumSqlserverEndpointBuilderFactory` (public interface)
2. Line 40: `DebeziumSqlserverEndpointBuilder` (public interface)
3. Line 2502: `AdvancedDebeziumSqlserverEndpointBuilder` (public interface)
4. Line 2628: `DebeziumSqlserverBuilders` (public interface)
5. Line 2689: `DebeziumSqlserverHeaderNameBuilder` (public static class)
6. Line 2788: `DebeziumSqlserverEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Debezium SQL Server CDC (Change Data Capture) component providing streaming database changes from Microsoft SQL Server databases. Streamlined builder supporting continuous change stream capture with parameters for connector configuration, SQL Server connection strings, database/table filtering, CDC capture table setup, snapshot control, and resumability options. Provides fluent DSL through DebeziumSqlserverEndpointBuilder interface with advanced variant for fine-grained control over change stream event processing. Contains DebeziumSqlserverBuilders interface and DebeziumSqlserverHeaderNameBuilder static inner class providing String constants for Debezium CDC event metadata headers.

---

## Phase 18: Files 67-70

### File 67
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DfdlEndpointBuilderFactory.java  
**Total Lines:** 221

**Type Declarations:** 5 total

1. Line 36: `DfdlEndpointBuilderFactory` (public interface)
2. Line 41: `DfdlEndpointBuilder` (public interface)
3. Line 85: `AdvancedDfdlEndpointBuilder` (public interface)
4. Line 169: `DfdlBuilders` (public interface)
5. Line 215: `DfdlEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DFDL (Data Format Description Language) endpoint component. Compact builder enabling data format conversion and transformation using DFDL schemas. Provides fluent DSL through DfdlEndpointBuilder interface with advanced variant for fine-grained control over DFDL parsing and serialization options. Contains DfdlBuilders interface and minimal configuration scope compared to larger components. Single inner implementation class DfdlEndpointBuilderImpl manages builder state and initialization logic.

---

### File 68
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Dhis2EndpointBuilderFactory.java  
**Total Lines:** 1182

**Type Declarations:** 9 total

1. Line 36: `Dhis2EndpointBuilderFactory` (public interface)
2. Line 41: `Dhis2EndpointConsumerBuilder` (public interface)
3. Line 613: `AdvancedDhis2EndpointConsumerBuilder` (public interface)
4. Line 813: `Dhis2EndpointProducerBuilder` (public interface)
5. Line 897: `AdvancedDhis2EndpointProducerBuilder` (public interface)
6. Line 989: `Dhis2EndpointBuilder` (public interface)
7. Line 1074: `AdvancedDhis2EndpointBuilder` (public interface)
8. Line 1120: `Dhis2Builders` (public interface)
9. Line 1176: `Dhis2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DHIS2 (District Health Information System 2) endpoint component supporting integration with DHIS2 health management information systems. Comprehensive builder with distinct consumer and producer variants enabling bidirectional communication with DHIS2 APIs. Provides fluent DSL through separate Dhis2EndpointConsumerBuilder and Dhis2EndpointProducerBuilder interfaces plus unified Dhis2EndpointBuilder. Advanced variants provide fine-grained control over DHIS2 data import/export operations, API authentication, organization unit filtering, and event synchronization. Contains Dhis2Builders interface for coordinating builder creation and single inner implementation class managing builder state.

---

### File 69
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DigitalOceanEndpointBuilderFactory.java  
**Total Lines:** 702

**Type Declarations:** 6 total

1. Line 35: `DigitalOceanEndpointBuilderFactory` (public interface)
2. Line 40: `DigitalOceanEndpointBuilder` (public interface)
3. Line 233: `AdvancedDigitalOceanEndpointBuilder` (public interface)
4. Line 318: `DigitalOceanBuilders` (public interface)
5. Line 394: `DigitalOceanHeaderNameBuilder` (public static class)
6. Line 696: `DigitalOceanEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DigitalOcean cloud platform endpoint component enabling integration with DigitalOcean services. Medium-sized builder supporting cloud infrastructure operations through DigitalOcean's API. Provides fluent DSL through DigitalOceanEndpointBuilder interface with advanced variant for fine-grained control over DigitalOcean resource management and API operations. Contains DigitalOceanBuilders interface and DigitalOceanHeaderNameBuilder static inner class providing String constants for DigitalOcean-specific headers. Single inner implementation class DigitalOceanEndpointBuilderImpl manages builder state and initialization.

---

### File 70
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DigitalSignatureEndpointBuilderFactory.java  
**Total Lines:** 666

**Type Declarations:** 6 total

1. Line 36: `DigitalSignatureEndpointBuilderFactory` (public interface)
2. Line 41: `DigitalSignatureEndpointBuilder` (public interface)
3. Line 274: `AdvancedDigitalSignatureEndpointBuilder` (public interface)
4. Line 529: `DigitalSignatureBuilders` (public interface)
5. Line 601: `DigitalSignatureHeaderNameBuilder` (public static class)
6. Line 660: `DigitalSignatureEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DigitalSignature endpoint component enabling cryptographic signing and validation operations. Medium-sized builder supporting digital signature creation, verification, and management through configurable cryptographic algorithms and key stores. Provides fluent DSL through DigitalSignatureEndpointBuilder interface with advanced variant for fine-grained control over signature algorithms, key stores, certificate management, and signature validation options. Contains DigitalSignatureBuilders interface and DigitalSignatureHeaderNameBuilder static inner class providing String constants for DigitalSignature-related headers. Single inner implementation class manages builder state initialization and method chaining logic.

---

## Phase 19: Files 71-74

### File 71
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DirectEndpointBuilderFactory.java  
**Total Lines:** 536

**Type Declarations:** 9 total

1. Line 35: `DirectEndpointBuilderFactory` (public interface)
2. Line 40: `DirectEndpointConsumerBuilder` (public interface)
3. Line 51: `AdvancedDirectEndpointConsumerBuilder` (public interface)
4. Line 219: `DirectEndpointProducerBuilder` (public interface)
5. Line 327: `AdvancedDirectEndpointProducerBuilder` (public interface)
6. Line 423: `DirectEndpointBuilder` (public interface)
7. Line 436: `AdvancedDirectEndpointBuilder` (public interface)
8. Line 486: `DirectBuilders` (public interface)
9. Line 530: `DirectEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Direct endpoint component enabling synchronous, in-process communication between Camel routes. Comprehensive builder with distinct consumer and producer variants for bidirectional direct messaging. Provides fluent DSL through separate DirectEndpointConsumerBuilder and DirectEndpointProducerBuilder interfaces plus unified DirectEndpointBuilder. Advanced variants provide fine-grained control over direct message passing, synchronous exception handling, and timeout configuration. Contains DirectBuilders interface for coordinating builder creation and single inner implementation class managing builder state and method chaining initialization.

---

### File 72
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DisruptorEndpointBuilderFactory.java  
**Total Lines:** 694

**Type Declarations:** 9 total

1. Line 35: `DisruptorEndpointBuilderFactory` (public interface)
2. Line 40: `DisruptorEndpointConsumerBuilder` (public interface)
3. Line 195: `AdvancedDisruptorEndpointConsumerBuilder` (public interface)
4. Line 323: `DisruptorEndpointProducerBuilder` (public interface)
5. Line 525: `AdvancedDisruptorEndpointProducerBuilder` (public interface)
6. Line 581: `DisruptorEndpointBuilder` (public interface)
7. Line 634: `AdvancedDisruptorEndpointBuilder` (public interface)
8. Line 644: `DisruptorBuilders` (public interface)
9. Line 688: `DisruptorEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Disruptor endpoint component providing high-performance, lock-free asynchronous message passing via LMAX Disruptor ring buffer. Comprehensive builder with distinct consumer and producer variants for advanced concurrent communication. Provides fluent DSL through separate DisruptorEndpointConsumerBuilder and DisruptorEndpointProducerBuilder interfaces plus unified DisruptorEndpointBuilder. Advanced variants provide fine-grained control over Disruptor ring buffer configuration, wait strategies, backpressure handling, and asynchronous exception routing. Contains DisruptorBuilders interface and single inner implementation class managing builder state and initialization logic.

---

### File 73
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DisruptorVmEndpointBuilderFactory.java  
**Total Lines:** 694

**Type Declarations:** 9 total

1. Line 35: `DisruptorVmEndpointBuilderFactory` (public interface)
2. Line 40: `DisruptorVmEndpointConsumerBuilder` (public interface)
3. Line 195: `AdvancedDisruptorVmEndpointConsumerBuilder` (public interface)
4. Line 323: `DisruptorVmEndpointProducerBuilder` (public interface)
5. Line 525: `AdvancedDisruptorVmEndpointProducerBuilder` (public interface)
6. Line 581: `DisruptorVmEndpointBuilder` (public interface)
7. Line 634: `AdvancedDisruptorVmEndpointBuilder` (public interface)
8. Line 644: `DisruptorVmBuilders` (public interface)
9. Line 688: `DisruptorVmEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DisruptorVm endpoint component providing VM-local, high-performance, lock-free asynchronous message passing via LMAX Disruptor ring buffer within a single JVM. Comprehensive builder with distinct consumer and producer variants for advanced concurrent VM-local communication. Provides fluent DSL through separate DisruptorVmEndpointConsumerBuilder and DisruptorVmEndpointProducerBuilder interfaces plus unified DisruptorVmEndpointBuilder. Advanced variants provide fine-grained control over Disruptor ring buffer configuration, wait strategies, backpressure handling, and asynchronous exception routing in VM context. Contains DisruptorVmBuilders interface and single inner implementation class managing builder state initialization.

---

### File 74
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DnsEndpointBuilderFactory.java  
**Total Lines:** 263

**Type Declarations:** 6 total

1. Line 35: `DnsEndpointBuilderFactory` (public interface)
2. Line 40: `DnsEndpointBuilder` (public interface)
3. Line 52: `AdvancedDnsEndpointBuilder` (public interface)
4. Line 107: `DnsBuilders` (public interface)
5. Line 170: `DnsHeaderNameBuilder` (public static class)
6. Line 257: `DnsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DNS endpoint component enabling DNS lookups and queries through Apache Commons Net. Compact builder supporting DNS operations with focused configuration options. Provides fluent DSL through DnsEndpointBuilder interface with advanced variant for fine-grained control over DNS query types and result processing. Contains DnsBuilders interface and DnsHeaderNameBuilder static inner class providing String constants for DNS-specific headers. Single inner implementation class DnsEndpointBuilderImpl manages builder state and initialization logic.

---

## Phase 20: Files 75-78

### File 75
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DockerEndpointBuilderFactory.java  
**Total Lines:** 2569

**Type Declarations:** 10 total

1. Line 35: `DockerEndpointBuilderFactory` (public interface)
2. Line 40: `DockerEndpointConsumerBuilder` (public interface)
3. Line 243: `AdvancedDockerEndpointConsumerBuilder` (public interface)
4. Line 585: `DockerEndpointProducerBuilder` (public interface)
5. Line 789: `AdvancedDockerEndpointProducerBuilder` (public interface)
6. Line 1059: `DockerEndpointBuilder` (public interface)
7. Line 1264: `AdvancedDockerEndpointBuilder` (public interface)
8. Line 1488: `DockerBuilders` (public interface)
9. Line 1565: `DockerHeaderNameBuilder` (public static class)
10. Line 2563: `DockerEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Docker endpoint component enabling integration with Docker container runtime for image management, container lifecycle operations, and container execution. Large builder with distinct consumer and producer variants for comprehensive Docker operation support. Provides fluent DSL through separate DockerEndpointConsumerBuilder and DockerEndpointProducerBuilder interfaces plus unified DockerEndpointBuilder. Advanced variants provide fine-grained control over Docker API operations, image pulling/pushing, container lifecycle management, volume mounting, network configuration, and event streaming. Contains DockerBuilders interface and DockerHeaderNameBuilder static inner class providing String constants for Docker-specific headers. Single inner implementation class manages builder state and initialization logic.

---

### File 76
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DoclingEndpointBuilderFactory.java  
**Total Lines:** 1861

**Type Declarations:** 6 total

1. Line 35: `DoclingEndpointBuilderFactory` (public interface)
2. Line 40: `DoclingEndpointBuilder` (public interface)
3. Line 724: `AdvancedDoclingEndpointBuilder` (public interface)
4. Line 1413: `DoclingBuilders` (public interface)
5. Line 1472: `DoclingHeaderNameBuilder` (public static class)
6. Line 1855: `DoclingEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Docling endpoint component enabling integration with Docling document processing system for PDF/document conversion and content extraction. Medium-large builder supporting document transformation through Docling's parsing capabilities. Provides fluent DSL through DoclingEndpointBuilder interface with advanced variant for fine-grained control over document processing options, format conversion settings, content extraction parameters, and output configuration. Contains DoclingBuilders interface and DoclingHeaderNameBuilder static inner class providing String constants for Docling-specific headers. Single inner implementation class manages builder state and initialization logic.

---

### File 77
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DrillEndpointBuilderFactory.java  
**Total Lines:** 289

**Type Declarations:** 6 total

1. Line 35: `DrillEndpointBuilderFactory` (public interface)
2. Line 40: `DrillEndpointBuilder` (public interface)
3. Line 148: `AdvancedDrillEndpointBuilder` (public interface)
4. Line 203: `DrillBuilders` (public interface)
5. Line 262: `DrillHeaderNameBuilder` (public static class)
6. Line 283: `DrillEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Drill endpoint component enabling integration with Apache Drill distributed SQL query engine for querying diverse data sources. Compact builder supporting SQL query execution across multiple data formats and storage systems. Provides fluent DSL through DrillEndpointBuilder interface with advanced variant for fine-grained control over SQL query configuration and result handling. Contains DrillBuilders interface and DrillHeaderNameBuilder static inner class providing String constants for Drill-specific headers. Single inner implementation class manages builder state and initialization logic.

---

### File 78
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DropboxEndpointBuilderFactory.java  
**Total Lines:** 1070

**Type Declarations:** 10 total

1. Line 36: `DropboxEndpointBuilderFactory` (public interface)
2. Line 41: `DropboxEndpointConsumerBuilder` (public interface)
3. Line 187: `AdvancedDropboxEndpointConsumerBuilder` (public interface)
4. Line 344: `DropboxEndpointProducerBuilder` (public interface)
5. Line 557: `AdvancedDropboxEndpointProducerBuilder` (public interface)
6. Line 642: `DropboxEndpointBuilder` (public interface)
7. Line 790: `AdvancedDropboxEndpointBuilder` (public interface)
8. Line 829: `DropboxBuilders` (public interface)
9. Line 897: `DropboxHeaderNameBuilder` (public static class)
10. Line 1064: `DropboxEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Dropbox endpoint component enabling integration with Dropbox cloud storage service for file management operations. Comprehensive builder with distinct consumer and producer variants for bidirectional Dropbox integration. Provides fluent DSL through separate DropboxEndpointConsumerBuilder and DropboxEndpointProducerBuilder interfaces plus unified DropboxEndpointBuilder. Advanced variants provide fine-grained control over Dropbox API operations, file upload/download/delete, directory listing, sharing configuration, and remote file monitoring. Contains DropboxBuilders interface and DropboxHeaderNameBuilder static inner class providing String constants for Dropbox-specific headers. Single inner implementation class manages builder state and initialization logic.

---

## Phase 21: Files 79-82

### File 79
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DynamicRouterControlEndpointBuilderFactory.java  
**Total Lines:** 422

**Type Declarations:** 6 total

1. Line 37: `DynamicRouterControlEndpointBuilderFactory` (public interface)
2. Line 42: `DynamicRouterControlEndpointBuilder` (public interface)
3. Line 184: `AdvancedDynamicRouterControlEndpointBuilder` (public interface)
4. Line 239: `DynamicRouterControlBuilders` (public interface)
5. Line 308: `DynamicRouterControlHeaderNameBuilder` (public static class)
6. Line 416: `DynamicRouterControlEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DynamicRouterControl endpoint component providing control and supervision of dynamic routing behavior. Compact builder supporting runtime dynamic router supervision and lifecycle management. Provides fluent DSL through DynamicRouterControlEndpointBuilder interface with advanced variant for fine-grained control over dynamic router control operations and event routing. Contains DynamicRouterControlBuilders interface and DynamicRouterControlHeaderNameBuilder static inner class providing String constants for dynamic router control-specific headers. Single inner implementation class manages builder state and initialization logic.

---

### File 80
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/DynamicRouterEndpointBuilderFactory.java  
**Total Lines:** 766

**Type Declarations:** 5 total

1. Line 36: `DynamicRouterEndpointBuilderFactory` (public interface)
2. Line 41: `DynamicRouterEndpointBuilder` (public interface)
3. Line 651: `AdvancedDynamicRouterEndpointBuilder` (public interface)
4. Line 706: `DynamicRouterBuilders` (public interface)
5. Line 760: `DynamicRouterEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for DynamicRouter endpoint component enabling runtime dynamic message routing decisions based on message content or external factors. Medium-sized builder supporting intelligent message routing through dynamically evaluated routing rules. Provides fluent DSL through DynamicRouterEndpointBuilder interface with advanced variant for fine-grained control over routing rule evaluation, routing logic callbacks, fallback destination configuration, and error handling strategies. Contains DynamicRouterBuilders interface and single inner implementation class managing builder state and dynamic route initialization.

---

### File 81
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ECS2EndpointBuilderFactory.java  
**Total Lines:** 674

**Type Declarations:** 6 total

1. Line 35: `ECS2EndpointBuilderFactory` (public interface)
2. Line 40: `ECS2EndpointBuilder` (public interface)
3. Line 443: `AdvancedECS2EndpointBuilder` (public interface)
4. Line 528: `ECS2Builders` (public interface)
5. Line 587: `ECS2HeaderNameBuilder` (public static class)
6. Line 668: `ECS2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for ECS2 (AWS Elastic Container Service v2) endpoint component enabling integration with AWS container orchestration services for task management and deployment. Medium-sized builder supporting container task lifecycle operations and service management. Provides fluent DSL through ECS2EndpointBuilder interface with advanced variant for fine-grained control over ECS task scheduling, cluster configuration, service deployment, and container monitoring. Contains ECS2Builders interface and ECS2HeaderNameBuilder static inner class providing String constants for ECS-specific headers. Single inner implementation class manages builder state and initialization logic.

---

### File 82
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/EKS2EndpointBuilderFactory.java  
**Total Lines:** 712

**Type Declarations:** 6 total

1. Line 35: `EKS2EndpointBuilderFactory` (public interface)
2. Line 40: `EKS2EndpointBuilder` (public interface)
3. Line 444: `AdvancedEKS2EndpointBuilder` (public interface)
4. Line 529: `EKS2Builders` (public interface)
5. Line 588: `EKS2HeaderNameBuilder` (public static class)
6. Line 706: `EKS2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for EKS2 (AWS Elastic Kubernetes Service v2) endpoint component enabling integration with AWS Kubernetes cluster management and container orchestration. Medium-sized builder supporting Kubernetes cluster operations and pod lifecycle management. Provides fluent DSL through EKS2EndpointBuilder interface with advanced variant for fine-grained control over Kubernetes resource management, cluster configuration, pod scheduling, and container deployment strategies. Contains EKS2Builders interface and EKS2HeaderNameBuilder static inner class providing String constants for EKS-specific headers. Single inner implementation class manages builder state and initialization logic.

---

## Phase 22: Files 83-86

### File 83
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/EhcacheEndpointBuilderFactory.java  
**Total Lines:** 1144

**Type Declarations:** 10 total

1. Line 35: `EhcacheEndpointBuilderFactory` (public interface)
2. Line 40: `EhcacheEndpointConsumerBuilder` (public interface)
3. Line 233: `AdvancedEhcacheEndpointConsumerBuilder` (public interface)
4. Line 450: `EhcacheEndpointProducerBuilder` (public interface)
5. Line 611: `AdvancedEhcacheEndpointProducerBuilder` (public interface)
6. Line 756: `EhcacheEndpointBuilder` (public interface)
7. Line 873: `AdvancedEhcacheEndpointBuilder` (public interface)
8. Line 972: `EhcacheBuilders` (public interface)
9. Line 1031: `EhcacheHeaderNameBuilder` (public static class)
10. Line 1138: `EhcacheEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Ehcache endpoint component enabling integration with Ehcache in-memory data store for distributed caching operations. Large builder with distinct consumer and producer variants for bidirectional cache integration. Provides fluent DSL through separate EhcacheEndpointConsumerBuilder and EhcacheEndpointProducerBuilder interfaces plus unified EhcacheEndpointBuilder. Advanced variants provide fine-grained control over cache operations, entry lifecycle, eviction policies, and cache invalidation strategies. Contains EhcacheBuilders interface and EhcacheHeaderNameBuilder static inner class providing String constants for Ehcache-specific headers. Single inner implementation class manages builder state and cache configuration logic.

---

### File 84
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ElasticsearchEndpointBuilderFactory.java  
**Total Lines:** 894

**Type Declarations:** 6 total

1. Line 35: `ElasticsearchEndpointBuilderFactory` (public interface)
2. Line 40: `ElasticsearchEndpointBuilder` (public interface)
3. Line 504: `AdvancedElasticsearchEndpointBuilder` (public interface)
4. Line 693: `ElasticsearchBuilders` (public interface)
5. Line 752: `ElasticsearchHeaderNameBuilder` (public static class)
6. Line 888: `ElasticsearchEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Elasticsearch endpoint component enabling integration with Elasticsearch search and analytics engine for full-text search operations. Medium-sized builder supporting document indexing, search queries, and cluster operations. Provides fluent DSL through ElasticsearchEndpointBuilder interface with advanced variant for fine-grained control over query DSL, aggregations, indexing parameters, and search result processing. Contains ElasticsearchBuilders interface and ElasticsearchHeaderNameBuilder static inner class providing String constants for Elasticsearch-specific headers. Single inner implementation class manages builder state and search configuration logic.

---

### File 85
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ElasticsearchRestClientEndpointBuilderFactory.java  
**Total Lines:** 568

**Type Declarations:** 6 total

1. Line 36: `ElasticsearchRestClientEndpointBuilderFactory` (public interface)
2. Line 41: `ElasticsearchRestClientEndpointBuilder` (public interface)
3. Line 252: `AdvancedElasticsearchRestClientEndpointBuilder` (public interface)
4. Line 429: `ElasticsearchRestClientBuilders` (public interface)
5. Line 491: `ElasticsearchRestClientHeaderNameBuilder` (public static class)
6. Line 562: `ElasticsearchRestClientEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for ElasticsearchRestClient endpoint component providing REST client-based integration with Elasticsearch search engine. Compact builder supporting RESTful search operations without native client protocol overhead. Provides fluent DSL through ElasticsearchRestClientEndpointBuilder interface with advanced variant for fine-grained control over HTTP request configuration, authentication, SSL/TLS settings, and REST API parameter handling. Contains ElasticsearchRestClientBuilders interface and ElasticsearchRestClientHeaderNameBuilder static inner class providing String constants for Elasticsearch REST client-specific headers. Single inner implementation class manages builder state and REST client initialization.

---

### File 86
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/EventEndpointBuilderFactory.java  
**Total Lines:** 320

**Type Declarations:** 9 total

1. Line 35: `EventEndpointBuilderFactory` (public interface)
2. Line 40: `EventEndpointConsumerBuilder` (public interface)
3. Line 51: `AdvancedEventEndpointConsumerBuilder` (public interface)
4. Line 179: `EventEndpointProducerBuilder` (public interface)
5. Line 191: `AdvancedEventEndpointProducerBuilder` (public interface)
6. Line 247: `EventEndpointBuilder` (public interface)
7. Line 260: `AdvancedEventEndpointBuilder` (public interface)
8. Line 270: `EventBuilders` (public interface)
9. Line 314: `EventEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Event endpoint component enabling integration with generic event processing and propagation mechanisms. Compact builder with distinct consumer and producer variants for bidirectional event handling. Provides fluent DSL through separate EventEndpointConsumerBuilder and EventEndpointProducerBuilder interfaces plus unified EventEndpointBuilder. Advanced variants provide fine-grained control over event handling, event filtering, event type configuration, and event delivery strategies. Contains EventBuilders interface and minimal configuration overhead. Single inner implementation class manages builder state and event routing logic.

---

## Phase 23: Files 87-90

### File 87
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/EventHubsEndpointBuilderFactory.java  
**Total Lines:** 1360

**Type Declarations:** 10 total

1. Line 35: `EventHubsEndpointBuilderFactory` (public interface)
2. Line 40: `EventHubsEndpointConsumerBuilder` (public interface)
3. Line 507: `AdvancedEventHubsEndpointConsumerBuilder` (public interface)
4. Line 635: `EventHubsEndpointProducerBuilder` (public interface)
5. Line 911: `AdvancedEventHubsEndpointProducerBuilder` (public interface)
6. Line 967: `EventHubsEndpointBuilder` (public interface)
7. Line 1157: `AdvancedEventHubsEndpointBuilder` (public interface)
8. Line 1167: `EventHubsBuilders` (public interface)
9. Line 1232: `EventHubsHeaderNameBuilder` (public static class)
10. Line 1354: `EventHubsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for EventHubs endpoint component enabling integration with Azure Event Hubs messaging service for event ingestion and streaming. Large builder with distinct consumer and producer variants for bidirectional event streaming integration. Provides fluent DSL through separate EventHubsEndpointConsumerBuilder and EventHubsEndpointProducerBuilder interfaces plus unified EventHubsEndpointBuilder. Advanced variants provide fine-grained control over partition management, consumer group configuration, offset management, and message batching strategies. Contains EventHubsBuilders interface and EventHubsHeaderNameBuilder static inner class providing String constants for Event Hubs-specific headers. Single inner implementation class manages builder state and Event Hubs connection configuration.

---

### File 88
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/EventbridgeEndpointBuilderFactory.java  
**Total Lines:** 2587

**Type Declarations:** 10 total

1. Line 36: `EventbridgeEndpointBuilderFactory` (public interface)
2. Line 41: `EventbridgeEndpointConsumerBuilder` (public interface)
3. Line 1139: `AdvancedEventbridgeEndpointConsumerBuilder` (public interface)
4. Line 1333: `EventbridgeEndpointProducerBuilder` (public interface)
5. Line 1755: `AdvancedEventbridgeEndpointProducerBuilder` (public interface)
6. Line 1841: `EventbridgeEndpointBuilder` (public interface)
7. Line 2264: `AdvancedEventbridgeEndpointBuilder` (public interface)
8. Line 2304: `EventbridgeBuilders` (public interface)
9. Line 2366: `EventbridgeHeaderNameBuilder` (public static class)
10. Line 2581: `EventbridgeEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Eventbridge endpoint component enabling integration with AWS EventBridge event routing and management service. Largest builder in this phase with distinct consumer and producer variants for bidirectional event bus integration. Provides fluent DSL through separate EventbridgeEndpointConsumerBuilder and EventbridgeEndpointProducerBuilder interfaces plus unified EventbridgeEndpointBuilder. Advanced variants provide comprehensive control over event rule management, event pattern filtering, target configuration, dead-letter queue handling, and event transformation. Contains EventbridgeBuilders interface and EventbridgeHeaderNameBuilder static inner class providing String constants for EventBridge-specific headers. Single inner implementation class manages builder state and EventBridge rule and event configuration.

---

### File 89
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ExecEndpointBuilderFactory.java  
**Total Lines:** 588

**Type Declarations:** 6 total

1. Line 35: `ExecEndpointBuilderFactory` (public interface)
2. Line 40: `ExecEndpointBuilder` (public interface)
3. Line 215: `AdvancedExecEndpointBuilder` (public interface)
4. Line 378: `ExecBuilders` (public interface)
5. Line 439: `ExecHeaderNameBuilder` (public static class)
6. Line 582: `ExecEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Exec endpoint component enabling integration with external system processes for command execution and process interaction. Compact builder supporting OS command invocation and process output capture. Provides fluent DSL through ExecEndpointBuilder interface with advanced variant for fine-grained control over command arguments, environment variables, working directory configuration, and process timeout settings. Contains ExecBuilders interface and ExecHeaderNameBuilder static inner class providing String constants for Exec-specific headers. Single inner implementation class manages builder state and process execution configuration.

---

### File 90
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FaceRecognitionEndpointBuilderFactory.java  
**Total Lines:** 596

**Type Declarations:** 5 total

1. Line 37: `FaceRecognitionEndpointBuilderFactory` (public interface)
2. Line 42: `FaceRecognitionEndpointBuilder` (public interface)
3. Line 485: `AdvancedFaceRecognitionEndpointBuilder` (public interface)
4. Line 540: `FaceRecognitionBuilders` (public interface)
5. Line 590: `FaceRecognitionEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FaceRecognition endpoint component providing integration with facial recognition and analysis services. Compact builder supporting image-based face detection and recognition operations. Provides fluent DSL through FaceRecognitionEndpointBuilder interface with advanced variant for fine-grained control over face detection algorithms, confidence thresholds, image format handling, and recognition result processing. Contains FaceRecognitionBuilders interface and minimal header configuration. Single inner implementation class manages builder state and face recognition service integration.

---

## Phase 24: Files 91-94

### File 91
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FhirEndpointBuilderFactory.java  
**Total Lines:** 2420

**Type Declarations:** 9 total

1. Line 36: `FhirEndpointBuilderFactory` (public interface)
2. Line 41: `FhirEndpointConsumerBuilder` (public interface)
3. Line 771: `AdvancedFhirEndpointConsumerBuilder` (public interface)
4. Line 1225: `FhirEndpointProducerBuilder` (public interface)
5. Line 1467: `AdvancedFhirEndpointProducerBuilder` (public interface)
6. Line 1813: `FhirEndpointBuilder` (public interface)
7. Line 2056: `AdvancedFhirEndpointBuilder` (public interface)
8. Line 2356: `FhirBuilders` (public interface)
9. Line 2414: `FhirEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FHIR endpoint component enabling integration with FHIR (Fast Healthcare Interoperability Resources) healthcare data exchange standard. Large builder with distinct consumer and producer variants for bidirectional FHIR resource management. Provides fluent DSL through separate FhirEndpointConsumerBuilder and FhirEndpointProducerBuilder interfaces plus unified FhirEndpointBuilder. Advanced variants provide comprehensive control over FHIR resource operations, authentication, resource type filtering, and clinical data transformation. Contains FhirBuilders interface and minimal additional configuration. Single inner implementation class manages builder state and FHIR API integration logic.

---

### File 92
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FileEndpointBuilderFactory.java  
**Total Lines:** 4248

**Type Declarations:** 10 total

1. Line 35: `FileEndpointBuilderFactory` (public interface)
2. Line 40: `FileEndpointConsumerBuilder` (public interface)
3. Line 1968: `AdvancedFileEndpointConsumerBuilder` (public interface)
4. Line 2727: `FileEndpointProducerBuilder` (public interface)
5. Line 3083: `AdvancedFileEndpointProducerBuilder` (public interface)
6. Line 3617: `FileEndpointBuilder` (public interface)
7. Line 3705: `AdvancedFileEndpointBuilder` (public interface)
8. Line 3953: `FileBuilders` (public interface)
9. Line 4012: `FileHeaderNameBuilder` (public static class)
10. Line 4242: `FileEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for File endpoint component enabling integration with local and remote file systems for file read/write/move operations. Largest builder in this phase with distinct consumer and producer variants for comprehensive file I/O operations. Provides fluent DSL through separate FileEndpointConsumerBuilder and FileEndpointProducerBuilder interfaces plus unified FileEndpointBuilder. Advanced variants provide fine-grained control over file naming strategies, polling mechanisms, file locking, character encoding, and file processing options. Contains FileBuilders interface and FileHeaderNameBuilder static inner class providing String constants for File-specific headers. Single inner implementation class manages builder state and file I/O configuration logic.

---

### File 93
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FileWatchEndpointBuilderFactory.java  
**Total Lines:** 650

**Type Declarations:** 6 total

1. Line 36: `FileWatchEndpointBuilderFactory` (public interface)
2. Line 41: `FileWatchEndpointBuilder` (public interface)
3. Line 184: `AdvancedFileWatchEndpointBuilder` (public interface)
4. Line 446: `FileWatchBuilders` (public interface)
5. Line 508: `FileWatchHeaderNameBuilder` (public static class)
6. Line 644: `FileWatchEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FileWatch endpoint component providing file system monitoring and change detection. Compact builder supporting directory and file change notifications through NIO file watching mechanisms. Provides fluent DSL through FileWatchEndpointBuilder interface with advanced variant for fine-grained control over file path watching, event type filtering, polling configuration, and recursive directory monitoring. Contains FileWatchBuilders interface and FileWatchHeaderNameBuilder static inner class providing String constants for FileWatch-specific headers. Single inner implementation class manages builder state and file watch listener configuration.

---

### File 94
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FilesEndpointBuilderFactory.java  
**Total Lines:** 4495

**Type Declarations:** 10 total

1. Line 35: `FilesEndpointBuilderFactory` (public interface)
2. Line 40: `FilesEndpointConsumerBuilder` (public interface)
3. Line 2124: `AdvancedFilesEndpointConsumerBuilder` (public interface)
4. Line 2685: `FilesEndpointProducerBuilder` (public interface)
5. Line 3232: `AdvancedFilesEndpointProducerBuilder` (public interface)
6. Line 3686: `FilesEndpointBuilder` (public interface)
7. Line 4004: `AdvancedFilesEndpointBuilder` (public interface)
8. Line 4206: `FilesBuilders` (public interface)
9. Line 4283: `FilesHeaderNameBuilder` (public static class)
10. Line 4489: `FilesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Files endpoint component enabling integration with multi-cloud file storage services (AWS S3, Azure Blob Storage, Google Cloud Storage). Largest endpoint builder factory in this phase with distinct consumer and producer variants for cloud file operations. Provides fluent DSL through separate FilesEndpointConsumerBuilder and FilesEndpointProducerBuilder interfaces plus unified FilesEndpointBuilder. Advanced variants provide extensive control over cloud storage authentication, bucket/container management, object metadata, multi-part uploads, and cloud-specific features. Contains FilesBuilders interface and FilesHeaderNameBuilder static inner class providing String constants for Files cloud storage-specific headers. Single inner implementation class manages builder state and cloud storage provider configuration.

---

## Phase 25: Files 95-98

### File 95
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FlatpackEndpointBuilderFactory.java  
**Total Lines:** 1456

**Type Declarations:** 10 total

1. Line 35: `FlatpackEndpointBuilderFactory` (public interface)
2. Line 40: `FlatpackEndpointConsumerBuilder` (public interface)
3. Line 726: `AdvancedFlatpackEndpointConsumerBuilder` (public interface)
4. Line 890: `FlatpackEndpointProducerBuilder` (public interface)
5. Line 1088: `AdvancedFlatpackEndpointProducerBuilder` (public interface)
6. Line 1144: `FlatpackEndpointBuilder` (public interface)
7. Line 1343: `AdvancedFlatpackEndpointBuilder` (public interface)
8. Line 1353: `FlatpackBuilders` (public interface)
9. Line 1428: `FlatpackHeaderNameBuilder` (public static class)
10. Line 1450: `FlatpackEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Flatpack endpoint component enabling integration with Flatpack EDI/flat file processing library for data transformation. Large builder with distinct consumer and producer variants for bidirectional data mapping. Provides fluent DSL through separate FlatpackEndpointConsumerBuilder and FlatpackEndpointProducerBuilder interfaces plus unified FlatpackEndpointBuilder. Advanced variants provide comprehensive control over record mapping, field splitting, data validation, transformation rules, and error handling strategies. Contains FlatpackBuilders interface and FlatpackHeaderNameBuilder static inner class providing String constants for Flatpack-specific headers. Single inner implementation class manages builder state and Flatpack data transformation configuration.

---

### File 96
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FlinkEndpointBuilderFactory.java  
**Total Lines:** 585

**Type Declarations:** 6 total

1. Line 35: `FlinkEndpointBuilderFactory` (public interface)
2. Line 40: `FlinkEndpointBuilder` (public interface)
3. Line 205: `AdvancedFlinkEndpointBuilder` (public interface)
4. Line 459: `FlinkBuilders` (public interface)
5. Line 520: `FlinkHeaderNameBuilder` (public static class)
6. Line 579: `FlinkEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Flink endpoint component enabling integration with Apache Flink stream processing engine for distributed data processing and real-time analytics. Compact builder supporting stream processing job submission and data flow management. Provides fluent DSL through FlinkEndpointBuilder interface with advanced variant for fine-grained control over Flink job configuration, parallelism settings, data partitioning strategies, and checkpoint management. Contains FlinkBuilders interface and FlinkHeaderNameBuilder static inner class providing String constants for Flink-specific headers. Single inner implementation class manages builder state and Flink job submission configuration.

---

### File 97
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FlowableEndpointBuilderFactory.java  
**Total Lines:** 320

**Type Declarations:** 9 total

1. Line 35: `FlowableEndpointBuilderFactory` (public interface)
2. Line 40: `FlowableEndpointConsumerBuilder` (public interface)
3. Line 51: `AdvancedFlowableEndpointConsumerBuilder` (public interface)
4. Line 179: `FlowableEndpointProducerBuilder` (public interface)
5. Line 191: `AdvancedFlowableEndpointProducerBuilder` (public interface)
6. Line 247: `FlowableEndpointBuilder` (public interface)
7. Line 260: `AdvancedFlowableEndpointBuilder` (public interface)
8. Line 270: `FlowableBuilders` (public interface)
9. Line 314: `FlowableEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Flowable endpoint component enabling integration with Flowable BPM (Business Process Management) engine for workflow automation. Compact builder with distinct consumer and producer variants for bidirectional workflow interaction. Provides fluent DSL through separate FlowableEndpointConsumerBuilder and FlowableEndpointProducerBuilder interfaces plus unified FlowableEndpointBuilder. Advanced variants provide control over process instance management, task assignment, variable handling, and process event notification. Contains FlowableBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Flowable workflow integration logic.

---

### File 98
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FopEndpointBuilderFactory.java  
**Total Lines:** 251

**Type Declarations:** 6 total

1. Line 35: `FopEndpointBuilderFactory` (public interface)
2. Line 40: `FopEndpointBuilder` (public interface)
3. Line 101: `AdvancedFopEndpointBuilder` (public interface)
4. Line 156: `FopBuilders` (public interface)
5. Line 224: `FopHeaderNameBuilder` (public static class)
6. Line 245: `FopEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FOP (Apache FOP/Formatting Objects Processor) endpoint component enabling document formatting and PDF generation. Smallest builder in this phase supporting document transformation to PDF format. Provides fluent DSL through FopEndpointBuilder interface with advanced variant for fine-grained control over formatting configuration, XSLT stylesheet processing, and PDF output parameters. Contains FopBuilders interface and FopHeaderNameBuilder static inner class providing String constants for FOP-specific headers. Single inner implementation class manages builder state and document formatting configuration.

---

## Phase 26: Files 99-102

### File 99
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FreemarkerEndpointBuilderFactory.java  
**Total Lines:** 411

**Type Declarations:** 6 total

1. Line 35: `FreemarkerEndpointBuilderFactory` (public interface)
2. Line 40: `FreemarkerEndpointBuilder` (public interface)
3. Line 231: `AdvancedFreemarkerEndpointBuilder` (public interface)
4. Line 286: `FreemarkerBuilders` (public interface)
5. Line 359: `FreemarkerHeaderNameBuilder` (public static class)
6. Line 405: `FreemarkerEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Freemarker endpoint component enabling integration with Apache Freemarker template engine for dynamic content generation and text transformation. Compact builder supporting template-based document and message generation. Provides fluent DSL through FreemarkerEndpointBuilder interface with advanced variant for fine-grained control over template configuration, variable binding, locale settings, and template processing options. Contains FreemarkerBuilders interface and FreemarkerHeaderNameBuilder static inner class providing String constants for Freemarker-specific headers. Single inner implementation class manages builder state and template engine configuration.

---

### File 100
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FtpEndpointBuilderFactory.java  
**Total Lines:** 5816

**Type Declarations:** 10 total

1. Line 35: `FtpEndpointBuilderFactory` (public interface)
2. Line 40: `FtpEndpointConsumerBuilder` (public interface)
3. Line 2171: `AdvancedFtpEndpointConsumerBuilder` (public interface)
4. Line 3175: `FtpEndpointProducerBuilder` (public interface)
5. Line 3788: `AdvancedFtpEndpointProducerBuilder` (public interface)
6. Line 4619: `FtpEndpointBuilder` (public interface)
7. Line 4982: `AdvancedFtpEndpointBuilder` (public interface)
8. Line 5509: `FtpBuilders` (public interface)
9. Line 5580: `FtpHeaderNameBuilder` (public static class)
10. Line 5810: `FtpEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FTP endpoint component enabling integration with File Transfer Protocol (FTP) servers for remote file operations. Massive builder with distinct consumer and producer variants for bidirectional file transfer. Provides fluent DSL through separate FtpEndpointConsumerBuilder and FtpEndpointProducerBuilder interfaces plus unified FtpEndpointBuilder. Advanced variants provide extensive control over FTP connection pooling, authentication, TLS/SSL encryption, passive/active mode switching, file encoding, and remote directory navigation. Contains FtpBuilders interface and FtpHeaderNameBuilder static inner class providing String constants for FTP-specific headers. Single inner implementation class manages builder state and FTP connection management configuration.

---

### File 101
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FtpsEndpointBuilderFactory.java  
**Total Lines:** 6539

**Type Declarations:** 10 total

1. Line 35: `FtpsEndpointBuilderFactory` (public interface)
2. Line 40: `FtpsEndpointConsumerBuilder` (public interface)
3. Line 2411: `AdvancedFtpsEndpointConsumerBuilder` (public interface)
4. Line 3415: `FtpsEndpointProducerBuilder` (public interface)
5. Line 4268: `AdvancedFtpsEndpointProducerBuilder` (public interface)
6. Line 5099: `FtpsEndpointBuilder` (public interface)
7. Line 5702: `AdvancedFtpsEndpointBuilder` (public interface)
8. Line 6229: `FtpsBuilders` (public interface)
9. Line 6303: `FtpsHeaderNameBuilder` (public static class)
10. Line 6533: `FtpsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FTPS endpoint component enabling integration with secure File Transfer Protocol (FTPS/SFTP) servers for encrypted remote file operations. Largest endpoint builder factory processed to date, reflecting extensive configuration options for secure file transfer. Provides fluent DSL through separate FtpsEndpointConsumerBuilder and FtpsEndpointProducerBuilder interfaces plus unified FtpsEndpointBuilder. Advanced variants provide comprehensive control over FTPS/TLS encryption modes, certificate validation, authentication mechanisms, connection pooling, passive/active mode switching, file encoding, and remote directory operations. Contains FtpsBuilders interface and FtpsHeaderNameBuilder static inner class providing String constants for FTPS-specific headers. Single inner implementation class manages builder state and secure FTP connection management configuration.

---

### File 102
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FunctionGraphEndpointBuilderFactory.java  
**Total Lines:** 392

**Type Declarations:** 5 total

1. Line 35: `FunctionGraphEndpointBuilderFactory` (public interface)
2. Line 40: `FunctionGraphEndpointBuilder` (public interface)
3. Line 287: `AdvancedFunctionGraphEndpointBuilder` (public interface)
4. Line 342: `FunctionGraphBuilders` (public interface)
5. Line 386: `FunctionGraphEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for FunctionGraph endpoint component enabling integration with Huawei Cloud FunctionGraph serverless computing platform for cloud function invocation. Compact builder supporting serverless function execution and cloud service integration. Provides fluent DSL through FunctionGraphEndpointBuilder interface with advanced variant for fine-grained control over function invocation parameters, cloud authentication, request/response mapping, and error handling strategies. Contains FunctionGraphBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Huawei Cloud function invocation configuration.

---

## Phase 27: Files 103-106

### File 103
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/FunctionsEndpointBuilderFactory.java  
**Total Lines:** 767

**Type Declarations:** 6 total

1. Line 35: `FunctionsEndpointBuilderFactory` (public interface)
2. Line 40: `FunctionsEndpointBuilder` (public interface)
3. Line 377: `AdvancedFunctionsEndpointBuilder` (public interface)
4. Line 464: `FunctionsBuilders` (public interface)
5. Line 531: `FunctionsHeaderNameBuilder` (public static class)
6. Line 761: `FunctionsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Functions endpoint component enabling integration with cloud function services for serverless computing operations. Compact builder supporting function invocation and cloud-based code execution. Provides fluent DSL through FunctionsEndpointBuilder interface with advanced variant for fine-grained control over function invocation parameters, cloud provider authentication, resource allocation, timeout configuration, and response handling. Contains FunctionsBuilders interface and FunctionsHeaderNameBuilder static inner class providing String constants for Functions-specific headers. Single inner implementation class manages builder state and cloud function invocation configuration.

---

### File 104
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GeoCoderEndpointBuilderFactory.java  
**Total Lines:** 572

**Type Declarations:** 6 total

1. Line 36: `GeoCoderEndpointBuilderFactory` (public interface)
2. Line 41: `GeoCoderEndpointBuilder` (public interface)
3. Line 299: `AdvancedGeoCoderEndpointBuilder` (public interface)
4. Line 354: `GeoCoderBuilders` (public interface)
5. Line 422: `GeoCoderHeaderNameBuilder` (public static class)
6. Line 566: `GeoCoderEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GeoCoder endpoint component enabling integration with geolocation and geocoding services for address-to-coordinate translation and reverse geocoding. Compact builder supporting location-based data enrichment and mapping operations. Provides fluent DSL through GeoCoderEndpointBuilder interface with advanced variant for fine-grained control over geocoding provider selection, API authentication, cache configuration, language settings, and coordinate precision. Contains GeoCoderBuilders interface and GeoCoderHeaderNameBuilder static inner class providing String constants for GeoCoder-specific headers. Single inner implementation class manages builder state and geocoding service integration configuration.

---

### File 105
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GitEndpointBuilderFactory.java  
**Total Lines:** 1303

**Type Declarations:** 10 total

1. Line 35: `GitEndpointBuilderFactory` (public interface)
2. Line 40: `GitEndpointConsumerBuilder` (public interface)
3. Line 584: `AdvancedGitEndpointConsumerBuilder` (public interface)
4. Line 762: `GitEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedGitEndpointProducerBuilder` (public interface)
6. Line 1021: `GitEndpointBuilder` (public interface)
7. Line 1048: `AdvancedGitEndpointBuilder` (public interface)
8. Line 1072: `GitBuilders` (public interface)
9. Line 1131: `GitHeaderNameBuilder` (public static class)
10. Line 1297: `GitEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Git endpoint component enabling integration with Git version control repositories for repository operations, file management, and version control workflows. Large builder with distinct consumer and producer variants for bidirectional Git operations. Provides fluent DSL through separate GitEndpointConsumerBuilder and GitEndpointProducerBuilder interfaces plus unified GitEndpointBuilder. Advanced variants provide comprehensive control over Git operations (clone, push, pull, commit), repository authentication, branch management, credentials handling, and remote tracking. Contains GitBuilders interface and GitHeaderNameBuilder static inner class providing String constants for Git-specific headers. Single inner implementation class manages builder state and Git repository configuration.

---

### File 106
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GitHub2EndpointBuilderFactory.java  
**Total Lines:** 1292

**Type Declarations:** 10 total

1. Line 35: `GitHub2EndpointBuilderFactory` (public interface)
2. Line 40: `GitHub2EndpointConsumerBuilder` (public interface)
3. Line 635: `AdvancedGitHub2EndpointConsumerBuilder` (public interface)
4. Line 845: `GitHub2EndpointProducerBuilder` (public interface)
5. Line 944: `AdvancedGitHub2EndpointProducerBuilder` (public interface)
6. Line 1014: `GitHub2EndpointBuilder` (public interface)
7. Line 1072: `AdvancedGitHub2EndpointBuilder` (public interface)
8. Line 1096: `GitHub2Builders` (public interface)
9. Line 1167: `GitHub2HeaderNameBuilder` (public static class)
10. Line 1286: `GitHub2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GitHub2 endpoint component enabling integration with GitHub API for repository operations, issue management, pull request workflows, and GitHub automation. Large builder with distinct consumer and producer variants for bidirectional GitHub API interactions. Provides fluent DSL through separate GitHub2EndpointConsumerBuilder and GitHub2EndpointProducerBuilder interfaces plus unified GitHub2EndpointBuilder. Advanced variants provide extensive control over GitHub API operations (list repos, create issues, manage PRs), authentication via GitHub tokens, repository filtering, pagination, and webhook configuration. Contains GitHub2Builders interface and GitHub2HeaderNameBuilder static inner class providing String constants for GitHub2-specific headers. Single inner implementation class manages builder state and GitHub API interaction configuration.

---

## Phase 28: Files 107-110

### File 107
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GlanceEndpointBuilderFactory.java  
**Total Lines:** 446

**Type Declarations:** 6 total

1. Line 35: `GlanceEndpointBuilderFactory` (public interface)
2. Line 40: `GlanceEndpointBuilder` (public interface)
3. Line 171: `AdvancedGlanceEndpointBuilder` (public interface)
4. Line 226: `GlanceBuilders` (public interface)
5. Line 285: `GlanceHeaderNameBuilder` (public static class)
6. Line 440: `GlanceEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Glance endpoint component enabling integration with OpenStack Glance image service for virtual machine image management and distribution. Compact builder supporting cloud image operations and storage management. Provides fluent DSL through GlanceEndpointBuilder interface with advanced variant for fine-grained control over image operations, OpenStack authentication, image format specification, and metadata handling. Contains GlanceBuilders interface and GlanceHeaderNameBuilder static inner class providing String constants for Glance-specific headers. Single inner implementation class manages builder state and OpenStack Glance image service configuration.

---

### File 108
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleBigQueryEndpointBuilderFactory.java  
**Total Lines:** 304

**Type Declarations:** 6 total

1. Line 35: `GoogleBigQueryEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleBigQueryEndpointBuilder` (public interface)
3. Line 113: `AdvancedGoogleBigQueryEndpointBuilder` (public interface)
4. Line 168: `GoogleBigQueryBuilders` (public interface)
5. Line 239: `GoogleBigQueryHeaderNameBuilder` (public static class)
6. Line 298: `GoogleBigQueryEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleBigQuery endpoint component enabling integration with Google Cloud BigQuery data warehouse for analytics and large-scale data processing. Compact builder supporting BigQuery dataset and table operations. Provides fluent DSL through GoogleBigQueryEndpointBuilder interface with advanced variant for fine-grained control over BigQuery queries, result set handling, dataset configuration, and Google Cloud authentication. Contains GoogleBigQueryBuilders interface and GoogleBigQueryHeaderNameBuilder static inner class providing String constants for GoogleBigQuery-specific headers. Single inner implementation class manages builder state and BigQuery data warehouse configuration.

---

### File 109
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleBigQuerySQLEndpointBuilderFactory.java  
**Total Lines:** 366

**Type Declarations:** 6 total

1. Line 35: `GoogleBigQuerySQLEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleBigQuerySQLEndpointBuilder` (public interface)
3. Line 176: `AdvancedGoogleBigQuerySQLEndpointBuilder` (public interface)
4. Line 231: `GoogleBigQuerySQLBuilders` (public interface)
5. Line 300: `GoogleBigQuerySQLHeaderNameBuilder` (public static class)
6. Line 360: `GoogleBigQuerySQLEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleBigQuerySQL endpoint component enabling SQL-based integration with Google Cloud BigQuery for interactive data analysis and query execution. Compact builder supporting SQL query submission and result retrieval from BigQuery. Provides fluent DSL through GoogleBigQuerySQLEndpointBuilder interface with advanced variant for fine-grained control over SQL execution, query parameters, result set pagination, timeout configuration, and Google Cloud project configuration. Contains GoogleBigQuerySQLBuilders interface and GoogleBigQuerySQLHeaderNameBuilder static inner class providing String constants for GoogleBigQuerySQL-specific headers. Single inner implementation class manages builder state and BigQuery SQL execution configuration.

---

### File 110
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCalendarEndpointBuilderFactory.java  
**Total Lines:** 1398

**Type Declarations:** 9 total

1. Line 35: `GoogleCalendarEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleCalendarEndpointConsumerBuilder` (public interface)
3. Line 719: `AdvancedGoogleCalendarEndpointConsumerBuilder` (public interface)
4. Line 883: `GoogleCalendarEndpointProducerBuilder` (public interface)
5. Line 1074: `AdvancedGoogleCalendarEndpointProducerBuilder` (public interface)
6. Line 1130: `GoogleCalendarEndpointBuilder` (public interface)
7. Line 1322: `AdvancedGoogleCalendarEndpointBuilder` (public interface)
8. Line 1332: `GoogleCalendarBuilders` (public interface)
9. Line 1392: `GoogleCalendarEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCalendar endpoint component enabling integration with Google Calendar API for calendar event management, scheduling, and meeting coordination. Large builder with distinct consumer and producer variants for bidirectional calendar operations. Provides fluent DSL through separate GoogleCalendarEndpointConsumerBuilder and GoogleCalendarEndpointProducerBuilder interfaces plus unified GoogleCalendarEndpointBuilder. Advanced variants provide comprehensive control over calendar events, attendee management, reminder settings, recurring event patterns, timezone handling, and Google account authentication. Contains GoogleCalendarBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Google Calendar API integration configuration.

---

## Phase 29: Files 111-114

### File 111
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCalendarStreamEndpointBuilderFactory.java  
**Total Lines:** 1106

**Type Declarations:** 6 total

1. Line 35: `GoogleCalendarStreamEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleCalendarStreamEndpointBuilder` (public interface)
3. Line 858: `AdvancedGoogleCalendarStreamEndpointBuilder` (public interface)
4. Line 1020: `GoogleCalendarStreamBuilders` (public interface)
5. Line 1079: `GoogleCalendarStreamHeaderNameBuilder` (public static class)
6. Line 1100: `GoogleCalendarStreamEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCalendarStream endpoint component enabling streaming integration with Google Calendar API for real-time calendar event notifications and updates. Compact builder supporting streaming calendar operations and event push notifications. Provides fluent DSL through GoogleCalendarStreamEndpointBuilder interface with advanced variant for fine-grained control over event streaming, real-time notification subscriptions, calendar filtering, push delivery configuration, and Google account authentication. Contains GoogleCalendarStreamBuilders interface and GoogleCalendarStreamHeaderNameBuilder static inner class providing String constants for GoogleCalendarStream-specific headers. Single inner implementation class manages builder state and streaming calendar configuration.

---

### File 112
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCloudFunctionsEndpointBuilderFactory.java  
**Total Lines:** 385

**Type Declarations:** 6 total

1. Line 35: `GoogleCloudFunctionsEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleCloudFunctionsEndpointBuilder` (public interface)
3. Line 155: `AdvancedGoogleCloudFunctionsEndpointBuilder` (public interface)
4. Line 240: `GoogleCloudFunctionsBuilders` (public interface)
5. Line 299: `GoogleCloudFunctionsHeaderNameBuilder` (public static class)
6. Line 379: `GoogleCloudFunctionsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCloudFunctions endpoint component enabling serverless function invocation via Google Cloud Functions. Compact builder supporting function execution and result handling. Provides fluent DSL through GoogleCloudFunctionsEndpointBuilder interface with advanced variant for fine-grained control over function invocation, parameter passing, execution environment configuration, timeout settings, and Google Cloud project authentication. Contains GoogleCloudFunctionsBuilders interface and GoogleCloudFunctionsHeaderNameBuilder static inner class providing String constants for GoogleCloudFunctions-specific headers. Single inner implementation class manages builder state and Cloud Functions invocation configuration.

---

### File 113
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCloudSpeechToTextEndpointBuilderFactory.java  
**Total Lines:** 348

**Type Declarations:** 6 total

1. Line 35: `GoogleCloudSpeechToTextEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleCloudSpeechToTextEndpointBuilder` (public interface)
3. Line 160: `AdvancedGoogleCloudSpeechToTextEndpointBuilder` (public interface)
4. Line 245: `GoogleCloudSpeechToTextBuilders` (public interface)
5. Line 304: `GoogleCloudSpeechToTextHeaderNameBuilder` (public static class)
6. Line 342: `GoogleCloudSpeechToTextEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCloudSpeechToText endpoint component enabling speech recognition via Google Cloud Speech-to-Text API. Compact builder supporting audio processing and transcription. Provides fluent DSL through GoogleCloudSpeechToTextEndpointBuilder interface with advanced variant for fine-grained control over audio input formats, language detection, speech recognition models, result confidence thresholds, streaming audio processing, and Google Cloud authentication. Contains GoogleCloudSpeechToTextBuilders interface and GoogleCloudSpeechToTextHeaderNameBuilder static inner class providing String constants for GoogleCloudSpeechToText-specific headers. Single inner implementation class manages builder state and speech recognition configuration.

---

### File 114
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCloudStorageEndpointBuilderFactory.java  
**Total Lines:** 1856

**Type Declarations:** 10 total

1. Line 36: `GoogleCloudStorageEndpointBuilderFactory` (public interface)
2. Line 41: `GoogleCloudStorageEndpointConsumerBuilder` (public interface)
3. Line 871: `AdvancedGoogleCloudStorageEndpointConsumerBuilder` (public interface)
4. Line 1035: `GoogleCloudStorageEndpointProducerBuilder` (public interface)
5. Line 1214: `AdvancedGoogleCloudStorageEndpointProducerBuilder` (public interface)
6. Line 1270: `GoogleCloudStorageEndpointBuilder` (public interface)
7. Line 1406: `AdvancedGoogleCloudStorageEndpointBuilder` (public interface)
8. Line 1416: `GoogleCloudStorageBuilders` (public interface)
9. Line 1478: `GoogleCloudStorageHeaderNameBuilder` (public static class)
10. Line 1850: `GoogleCloudStorageEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCloudStorage endpoint component enabling integration with Google Cloud Storage bucket operations for object management, backup, and cloud file sharing. Large builder with distinct consumer and producer variants for bidirectional GCS interactions. Provides fluent DSL through separate GoogleCloudStorageEndpointConsumerBuilder and GoogleCloudStorageEndpointProducerBuilder interfaces plus unified GoogleCloudStorageEndpointBuilder. Advanced variants provide comprehensive control over bucket operations, object metadata management, access control lists, storage class selection, versioning, lifecycle policies, and Google Cloud authentication. Contains GoogleCloudStorageBuilders interface and GoogleCloudStorageHeaderNameBuilder static inner class providing String constants for GoogleCloudStorage-specific headers. Single inner implementation class manages builder state and Google Cloud Storage configuration.

---

## Phase 30: Files 115-118

### File 115
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCloudTextToSpeechEndpointBuilderFactory.java  
**Total Lines:** 389

**Type Declarations:** 6 total

1. Line 35: `GoogleCloudTextToSpeechEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleCloudTextToSpeechEndpointBuilder` (public interface)
3. Line 199: `AdvancedGoogleCloudTextToSpeechEndpointBuilder` (public interface)
4. Line 286: `GoogleCloudTextToSpeechBuilders` (public interface)
5. Line 345: `GoogleCloudTextToSpeechHeaderNameBuilder` (public static class)
6. Line 383: `GoogleCloudTextToSpeechEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCloudTextToSpeech endpoint component enabling text-to-speech synthesis via Google Cloud Text-to-Speech API. Compact builder supporting audio generation and speech synthesis. Provides fluent DSL through GoogleCloudTextToSpeechEndpointBuilder interface with advanced variant for fine-grained control over text input, voice selection, audio encoding formats, pitch adjustment, speech rate modification, and Google Cloud authentication. Contains GoogleCloudTextToSpeechBuilders interface and GoogleCloudTextToSpeechHeaderNameBuilder static inner class providing String constants for GoogleCloudTextToSpeech-specific headers. Single inner implementation class manages builder state and text-to-speech synthesis configuration.

---

### File 116
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleCloudVisionEndpointBuilderFactory.java  
**Total Lines:** 320

**Type Declarations:** 6 total

1. Line 36: `GoogleCloudVisionEndpointBuilderFactory` (public interface)
2. Line 41: `GoogleCloudVisionEndpointBuilder` (public interface)
3. Line 129: `AdvancedGoogleCloudVisionEndpointBuilder` (public interface)
4. Line 214: `GoogleCloudVisionBuilders` (public interface)
5. Line 276: `GoogleCloudVisionHeaderNameBuilder` (public static class)
6. Line 314: `GoogleCloudVisionEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleCloudVision endpoint component enabling image analysis and computer vision via Google Cloud Vision API. Compact builder supporting image feature detection and visual content analysis. Provides fluent DSL through GoogleCloudVisionEndpointBuilder interface with advanced variant for fine-grained control over image input sources, feature types (object detection, face detection, text recognition, landmark detection), confidence thresholds, and Google Cloud authentication. Contains GoogleCloudVisionBuilders interface and GoogleCloudVisionHeaderNameBuilder static inner class providing String constants for GoogleCloudVision-specific headers. Single inner implementation class manages builder state and vision API configuration.

---

### File 117
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleDriveEndpointBuilderFactory.java  
**Total Lines:** 1363

**Type Declarations:** 9 total

1. Line 35: `GoogleDriveEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleDriveEndpointConsumerBuilder` (public interface)
3. Line 706: `AdvancedGoogleDriveEndpointConsumerBuilder` (public interface)
4. Line 870: `GoogleDriveEndpointProducerBuilder` (public interface)
5. Line 1048: `AdvancedGoogleDriveEndpointProducerBuilder` (public interface)
6. Line 1104: `GoogleDriveEndpointBuilder` (public interface)
7. Line 1283: `AdvancedGoogleDriveEndpointBuilder` (public interface)
8. Line 1293: `GoogleDriveBuilders` (public interface)
9. Line 1357: `GoogleDriveEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleDrive endpoint component enabling integration with Google Drive for cloud file management, document storage, and collaborative workspace operations. Large builder with distinct consumer and producer variants for bidirectional Drive operations. Provides fluent DSL through separate GoogleDriveEndpointConsumerBuilder and GoogleDriveEndpointProducerBuilder interfaces plus unified GoogleDriveEndpointBuilder. Advanced variants provide comprehensive control over file operations, folder management, permission control, sharing settings, file revision history, comment threads, and Google account authentication. Contains GoogleDriveBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Google Drive API integration configuration.

---

### File 118
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleFirestoreEndpointBuilderFactory.java  
**Total Lines:** 1379

**Type Declarations:** 10 total

1. Line 35: `GoogleFirestoreEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleFirestoreEndpointConsumerBuilder` (public interface)
3. Line 649: `AdvancedGoogleFirestoreEndpointConsumerBuilder` (public interface)
4. Line 813: `GoogleFirestoreEndpointProducerBuilder` (public interface)
5. Line 946: `AdvancedGoogleFirestoreEndpointProducerBuilder` (public interface)
6. Line 1002: `GoogleFirestoreEndpointBuilder` (public interface)
7. Line 1092: `AdvancedGoogleFirestoreEndpointBuilder` (public interface)
8. Line 1102: `GoogleFirestoreBuilders` (public interface)
9. Line 1161: `GoogleFirestoreHeaderNameBuilder` (public static class)
10. Line 1373: `GoogleFirestoreEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleFirestore endpoint component enabling integration with Google Cloud Firestore NoSQL database for real-time data synchronization, document management, and cloud persistence. Large builder with distinct consumer and producer variants for bidirectional Firestore operations. Provides fluent DSL through separate GoogleFirestoreEndpointConsumerBuilder and GoogleFirestoreEndpointProducerBuilder interfaces plus unified GoogleFirestoreEndpointBuilder. Advanced variants provide comprehensive control over document operations, collection queries, real-time listeners, transaction handling, index management, and Google Cloud project authentication. Contains GoogleFirestoreBuilders interface and GoogleFirestoreHeaderNameBuilder static inner class providing String constants for GoogleFirestore-specific headers. Single inner implementation class manages builder state and Firestore database configuration.

---

## Phase 31: Files 119-122

### File 119
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleMailEndpointBuilderFactory.java  
**Total Lines:** 1260

**Type Declarations:** 9 total

1. Line 35: `GoogleMailEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleMailEndpointConsumerBuilder` (public interface)
3. Line 673: `AdvancedGoogleMailEndpointConsumerBuilder` (public interface)
4. Line 837: `GoogleMailEndpointProducerBuilder` (public interface)
5. Line 982: `AdvancedGoogleMailEndpointProducerBuilder` (public interface)
6. Line 1038: `GoogleMailEndpointBuilder` (public interface)
7. Line 1184: `AdvancedGoogleMailEndpointBuilder` (public interface)
8. Line 1194: `GoogleMailBuilders` (public interface)
9. Line 1254: `GoogleMailEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleMail endpoint component enabling integration with Gmail API for email management, message sending, attachment handling, and mailbox operations. Large builder with distinct consumer and producer variants for bidirectional Gmail operations. Provides fluent DSL through separate GoogleMailEndpointConsumerBuilder and GoogleMailEndpointProducerBuilder interfaces plus unified GoogleMailEndpointBuilder. Advanced variants provide comprehensive control over email composition, recipient management, attachment operations, draft handling, label management, thread operations, and Google account authentication. Contains GoogleMailBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Gmail API integration configuration.

---

### File 120
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleMailStreamEndpointBuilderFactory.java  
**Total Lines:** 1126

**Type Declarations:** 6 total

1. Line 35: `GoogleMailStreamEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleMailStreamEndpointBuilder` (public interface)
3. Line 782: `AdvancedGoogleMailStreamEndpointBuilder` (public interface)
4. Line 944: `GoogleMailStreamBuilders` (public interface)
5. Line 1003: `GoogleMailStreamHeaderNameBuilder` (public static class)
6. Line 1120: `GoogleMailStreamEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleMailStream endpoint component enabling real-time streaming integration with Gmail API for continuous email notification delivery and mailbox event push updates. Compact builder supporting streaming email operations and notification subscriptions. Provides fluent DSL through GoogleMailStreamEndpointBuilder interface with advanced variant for fine-grained control over email streaming, real-time notification subscriptions, label filtering, search-based streaming, delivery configuration, and Google account authentication. Contains GoogleMailStreamBuilders interface and GoogleMailStreamHeaderNameBuilder static inner class providing String constants for GoogleMailStream-specific headers. Single inner implementation class manages builder state and Gmail streaming configuration.

---

### File 121
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GooglePubsubEndpointBuilderFactory.java  
**Total Lines:** 1153

**Type Declarations:** 10 total

1. Line 35: `GooglePubsubEndpointBuilderFactory` (public interface)
2. Line 40: `GooglePubsubEndpointConsumerBuilder` (public interface)
3. Line 134: `AdvancedGooglePubsubEndpointConsumerBuilder` (public interface)
4. Line 515: `GooglePubsubEndpointProducerBuilder` (public interface)
5. Line 576: `AdvancedGooglePubsubEndpointProducerBuilder` (public interface)
6. Line 823: `GooglePubsubEndpointBuilder` (public interface)
7. Line 885: `AdvancedGooglePubsubEndpointBuilder` (public interface)
8. Line 976: `GooglePubsubBuilders` (public interface)
9. Line 1046: `GooglePubsubHeaderNameBuilder` (public static class)
10. Line 1147: `GooglePubsubEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GooglePubsub endpoint component enabling integration with Google Cloud Pub/Sub messaging service for asynchronous publish-subscribe communication and event streaming. Large builder with distinct consumer and producer variants for bidirectional messaging operations. Provides fluent DSL through separate GooglePubsubEndpointConsumerBuilder and GooglePubsubEndpointProducerBuilder interfaces plus unified GooglePubsubEndpointBuilder. Advanced variants provide comprehensive control over topic and subscription management, message publishing, subscriber configuration, ordering guarantees, message acknowledgment patterns, dead-letter policies, and Google Cloud project authentication. Contains GooglePubsubBuilders interface and GooglePubsubHeaderNameBuilder static inner class providing String constants for GooglePubsub-specific headers. Single inner implementation class manages builder state and Pub/Sub messaging configuration.

---

### File 122
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleSecretManagerEndpointBuilderFactory.java  
**Total Lines:** 324

**Type Declarations:** 6 total

1. Line 35: `GoogleSecretManagerEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleSecretManagerEndpointBuilder` (public interface)
3. Line 127: `AdvancedGoogleSecretManagerEndpointBuilder` (public interface)
4. Line 212: `GoogleSecretManagerBuilders` (public interface)
5. Line 271: `GoogleSecretManagerHeaderNameBuilder` (public static class)
6. Line 318: `GoogleSecretManagerEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleSecretManager endpoint component enabling integration with Google Cloud Secret Manager for secure credential storage, secret version management, and application secret injection. Compact builder supporting secret lifecycle operations and credential retrieval. Provides fluent DSL through GoogleSecretManagerEndpointBuilder interface with advanced variant for fine-grained control over secret access patterns, version selection, secret metadata, automatic rotation configuration, and Google Cloud project authentication. Contains GoogleSecretManagerBuilders interface and GoogleSecretManagerHeaderNameBuilder static inner class providing String constants for GoogleSecretManager-specific headers. Single inner implementation class manages builder state and Secret Manager configuration.

---

## Phase 32: Files 123-126

### File 123
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleSheetsEndpointBuilderFactory.java  
**Total Lines:** 1292

**Type Declarations:** 9 total

1. Line 35: `GoogleSheetsEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleSheetsEndpointConsumerBuilder` (public interface)
3. Line 709: `AdvancedGoogleSheetsEndpointConsumerBuilder` (public interface)
4. Line 873: `GoogleSheetsEndpointProducerBuilder` (public interface)
5. Line 1018: `AdvancedGoogleSheetsEndpointProducerBuilder` (public interface)
6. Line 1074: `GoogleSheetsEndpointBuilder` (public interface)
7. Line 1220: `AdvancedGoogleSheetsEndpointBuilder` (public interface)
8. Line 1230: `GoogleSheetsBuilders` (public interface)
9. Line 1286: `GoogleSheetsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleSheets endpoint component enabling integration with Google Sheets API for spreadsheet data manipulation, cell updates, row/column operations, and collaborative data management. Large builder with distinct consumer and producer variants for bidirectional Sheets operations. Provides fluent DSL through separate GoogleSheetsEndpointConsumerBuilder and GoogleSheetsEndpointProducerBuilder interfaces plus unified GoogleSheetsEndpointBuilder. Advanced variants provide comprehensive control over spreadsheet selection, range operations, value input/output, formatting updates, batch requests, sheet management, and Google account authentication. Contains GoogleSheetsBuilders interface and minimal header configuration. Single inner implementation class manages builder state and Sheets API integration configuration.

---

### File 124
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleSheetsStreamEndpointBuilderFactory.java  
**Total Lines:** 1113

**Type Declarations:** 6 total

1. Line 35: `GoogleSheetsStreamEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleSheetsStreamEndpointBuilder` (public interface)
3. Line 802: `AdvancedGoogleSheetsStreamEndpointBuilder` (public interface)
4. Line 964: `GoogleSheetsStreamBuilders` (public interface)
5. Line 1025: `GoogleSheetsStreamHeaderNameBuilder` (public static class)
6. Line 1107: `GoogleSheetsStreamEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleSheetsStream endpoint component enabling real-time streaming integration with Google Sheets API for continuous spreadsheet change detection and data synchronization updates. Compact builder supporting streaming spreadsheet operations and change push notifications. Provides fluent DSL through GoogleSheetsStreamEndpointBuilder interface with advanced variant for fine-grained control over spreadsheet monitoring, change-type filtering, value-range streaming, update notification subscriptions, and Google account authentication. Contains GoogleSheetsStreamBuilders interface and GoogleSheetsStreamHeaderNameBuilder static inner class providing String constants for GoogleSheetsStream-specific headers. Single inner implementation class manages builder state and Sheets streaming configuration.

---

### File 125
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GoogleVertexAIEndpointBuilderFactory.java  
**Total Lines:** 936

**Type Declarations:** 6 total

1. Line 35: `GoogleVertexAIEndpointBuilderFactory` (public interface)
2. Line 40: `GoogleVertexAIEndpointBuilder` (public interface)
3. Line 328: `AdvancedGoogleVertexAIEndpointBuilder` (public interface)
4. Line 446: `GoogleVertexAIBuilders` (public interface)
5. Line 517: `GoogleVertexAIHeaderNameBuilder` (public static class)
6. Line 930: `GoogleVertexAIEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GoogleVertexAI endpoint component enabling integration with Google Cloud Vertex AI for machine learning model inference, LLM text generation, image analysis, custom model deployment, and AI-powered predictions. Compact builder supporting model invocation and inference operations. Provides fluent DSL through GoogleVertexAIEndpointBuilder interface with advanced variant for fine-grained control over model selection, prediction parameters, input preprocessing, output formatting, API endpoint configuration, and Google Cloud project authentication. Contains GoogleVertexAIBuilders interface and GoogleVertexAIHeaderNameBuilder static inner class providing String constants for GoogleVertexAI-specific headers. Single inner implementation class manages builder state and Vertex AI model inference configuration.

---

### File 126
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GraphqlEndpointBuilderFactory.java  
**Total Lines:** 430

**Type Declarations:** 5 total

1. Line 35: `GraphqlEndpointBuilderFactory` (public interface)
2. Line 40: `GraphqlEndpointBuilder` (public interface)
3. Line 259: `AdvancedGraphqlEndpointBuilder` (public interface)
4. Line 380: `GraphqlBuilders` (public interface)
5. Line 424: `GraphqlEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GraphQL endpoint component enabling integration with GraphQL APIs for data fetching, mutation execution, subscription management, and schema-based query operations. Simple builder supporting GraphQL query and mutation operations through HTTP transport. Provides fluent DSL through GraphqlEndpointBuilder interface with advanced variant for fine-grained control over query selection, variable binding, HTTP headers, response parsing, error handling, and endpoint configuration. Contains GraphqlBuilders interface and minimal header configuration. Single inner implementation class manages builder state and GraphQL HTTP client configuration.

---

## Phase 33: Files 127-130

### File 127
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GridFsEndpointBuilderFactory.java  
**Total Lines:** 960

**Type Declarations:** 10 total

1. Line 35: `GridFsEndpointBuilderFactory` (public interface)
2. Line 40: `GridFsEndpointConsumerBuilder` (public interface)
3. Line 315: `AdvancedGridFsEndpointConsumerBuilder` (public interface)
4. Line 443: `GridFsEndpointProducerBuilder` (public interface)
5. Line 572: `AdvancedGridFsEndpointProducerBuilder` (public interface)
6. Line 628: `GridFsEndpointBuilder` (public interface)
7. Line 744: `AdvancedGridFsEndpointBuilder` (public interface)
8. Line 754: `GridFsBuilders` (public interface)
9. Line 813: `GridFsHeaderNameBuilder` (public static class)
10. Line 954: `GridFsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for GridFS endpoint component enabling integration with MongoDB GridFS for large binary file storage, retrieval, and distributed file management. Large builder with distinct consumer and producer variants for bidirectional GridFS operations. Provides fluent DSL through separate GridFsEndpointConsumerBuilder and GridFsEndpointProducerBuilder interfaces plus unified GridFsEndpointBuilder. Advanced variants provide comprehensive control over file operations, metadata management, stream handling, chunk configuration, database/collection selection, and MongoDB authentication. Contains GridFsBuilders interface and GridFsHeaderNameBuilder static inner class providing String constants for GridFS-specific headers. Single inner implementation class manages builder state and GridFS file store configuration.

---

### File 128
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/GrpcEndpointBuilderFactory.java  
**Total Lines:** 2126

**Type Declarations:** 10 total

1. Line 35: `GrpcEndpointBuilderFactory` (public interface)
2. Line 40: `GrpcEndpointConsumerBuilder` (public interface)
3. Line 934: `AdvancedGrpcEndpointConsumerBuilder` (public interface)
4. Line 1092: `GrpcEndpointProducerBuilder` (public interface)
5. Line 1574: `AdvancedGrpcEndpointProducerBuilder` (public interface)
6. Line 1660: `GrpcEndpointBuilder` (public interface)
7. Line 1958: `AdvancedGrpcEndpointBuilder` (public interface)
8. Line 1998: `GrpcBuilders` (public interface)
9. Line 2073: `GrpcHeaderNameBuilder` (public static class)
10. Line 2120: `GrpcEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for gRPC endpoint component enabling integration with gRPC services for high-performance RPC communication, protocol-buffer serialization, and streaming protocols. Very large builder with distinct consumer and producer variants for bidirectional gRPC operations with extensive configuration options. Provides fluent DSL through separate GrpcEndpointConsumerBuilder and GrpcEndpointProducerBuilder interfaces plus unified GrpcEndpointBuilder. Advanced variants provide comprehensive control over service definition, method selection, flow control, TLS configuration, metadata propagation, interceptor chains, load balancing, and connection pooling. Contains GrpcBuilders interface and GrpcHeaderNameBuilder static inner class providing String constants for gRPC-specific headers. Single inner implementation class manages builder state and gRPC client-server configuration.

---

### File 129
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HashicorpVaultEndpointBuilderFactory.java  
**Total Lines:** 397

**Type Declarations:** 6 total

1. Line 35: `HashicorpVaultEndpointBuilderFactory` (public interface)
2. Line 40: `HashicorpVaultEndpointBuilder` (public interface)
3. Line 231: `AdvancedHashicorpVaultEndpointBuilder` (public interface)
4. Line 286: `HashicorpVaultBuilders` (public interface)
5. Line 345: `HashicorpVaultHeaderNameBuilder` (public static class)
6. Line 391: `HashicorpVaultEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HashicorpVault endpoint component enabling integration with HashiCorp Vault for secure secret storage, credential management, key rotation, and dynamically generated credentials. Compact builder supporting secret retrieval and token management operations. Provides fluent DSL through HashicorpVaultEndpointBuilder interface with advanced variant for fine-grained control over secret path selection, authentication method, token renewal, lease handling, secret version management, and Vault server configuration. Contains HashicorpVaultBuilders interface and HashicorpVaultHeaderNameBuilder static inner class providing String constants for HashicorpVault-specific headers. Single inner implementation class manages builder state and Vault HTTP client configuration.

---

### File 130
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastAtomicnumberEndpointBuilderFactory.java  
**Total Lines:** 298

**Type Declarations:** 6 total

1. Line 35: `HazelcastAtomicnumberEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastAtomicnumberEndpointBuilder` (public interface)
3. Line 151: `AdvancedHazelcastAtomicnumberEndpointBuilder` (public interface)
4. Line 206: `HazelcastAtomicnumberBuilders` (public interface)
5. Line 271: `HazelcastAtomicnumberHeaderNameBuilder` (public static class)
6. Line 292: `HazelcastAtomicnumberEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastAtomicnumber endpoint component enabling integration with Hazelcast distributed in-memory computing platform for atomic number operations, thread-safe counters, and distributed computation. Compact builder supporting atomic increment/decrement and number operations. Provides fluent DSL through HazelcastAtomicnumberEndpointBuilder interface with advanced variant for fine-grained control over atomic number instance selection, operation type, wait strategies, data structure mode, and Hazelcast cluster configuration. Contains HazelcastAtomicnumberBuilders interface and HazelcastAtomicnumberHeaderNameBuilder static inner class providing String constants for HazelcastAtomicnumber-specific headers. Single inner implementation class manages builder state and Hazelcast atomic number configuration.

---

## Phase 34: Files 131-134

### File 131
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastInstanceEndpointBuilderFactory.java  
**Total Lines:** 411

**Type Declarations:** 6 total

1. Line 35: `HazelcastInstanceEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastInstanceEndpointBuilder` (public interface)
3. Line 151: `AdvancedHazelcastInstanceEndpointBuilder` (public interface)
4. Line 277: `HazelcastInstanceBuilders` (public interface)
5. Line 336: `HazelcastInstanceHeaderNameBuilder` (public static class)
6. Line 405: `HazelcastInstanceEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastInstance endpoint component enabling integration with Hazelcast distributed in-memory computing platform for direct instance access and cluster-wide operations. Compact builder supporting Hazelcast instance and cluster operations. Provides fluent DSL through HazelcastInstanceEndpointBuilder interface with advanced variant for fine-grained control over Hazelcast instance selection, instance access patterns, distributed data structure access, and cluster topology management. Contains HazelcastInstanceBuilders interface and HazelcastInstanceHeaderNameBuilder static inner class providing String constants for HazelcastInstance-specific headers. Single inner implementation class manages builder state and Hazelcast instance client configuration.

---

### File 132
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastListEndpointBuilderFactory.java  
**Total Lines:** 725

**Type Declarations:** 10 total

1. Line 35: `HazelcastListEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastListEndpointConsumerBuilder` (public interface)
3. Line 150: `AdvancedHazelcastListEndpointConsumerBuilder` (public interface)
4. Line 278: `HazelcastListEndpointProducerBuilder` (public interface)
5. Line 389: `AdvancedHazelcastListEndpointProducerBuilder` (public interface)
6. Line 445: `HazelcastListEndpointBuilder` (public interface)
7. Line 557: `AdvancedHazelcastListEndpointBuilder` (public interface)
8. Line 567: `HazelcastListBuilders` (public interface)
9. Line 626: `HazelcastListHeaderNameBuilder` (public static class)
10. Line 719: `HazelcastListEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastList endpoint component enabling integration with Hazelcast distributed list data structure for thread-safe, replicated collection operations and distributed item management. Large builder with distinct consumer and producer variants for bidirectional list operations. Provides fluent DSL through separate HazelcastListEndpointConsumerBuilder and HazelcastListEndpointProducerBuilder interfaces plus unified HazelcastListEndpointBuilder. Advanced variants provide comprehensive control over list instance selection, item addition/removal operations, listener registration, blocking queue semantics, and Hazelcast cluster configuration. Contains HazelcastListBuilders interface and HazelcastListHeaderNameBuilder static inner class providing String constants for HazelcastList-specific headers. Single inner implementation class manages builder state and Hazelcast distributed list configuration.

---

### File 133
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastMapEndpointBuilderFactory.java  
**Total Lines:** 762

**Type Declarations:** 10 total

1. Line 35: `HazelcastMapEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastMapEndpointConsumerBuilder` (public interface)
3. Line 150: `AdvancedHazelcastMapEndpointConsumerBuilder` (public interface)
4. Line 278: `HazelcastMapEndpointProducerBuilder` (public interface)
5. Line 389: `AdvancedHazelcastMapEndpointProducerBuilder` (public interface)
6. Line 445: `HazelcastMapEndpointBuilder` (public interface)
7. Line 557: `AdvancedHazelcastMapEndpointBuilder` (public interface)
8. Line 567: `HazelcastMapBuilders` (public interface)
9. Line 626: `HazelcastMapHeaderNameBuilder` (public static class)
10. Line 756: `HazelcastMapEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastMap endpoint component enabling integration with Hazelcast distributed map (hash table) data structure for high-performance key-value storage, caching, and distributed data access. Large builder with distinct consumer and producer variants for bidirectional map operations. Provides fluent DSL through separate HazelcastMapEndpointConsumerBuilder and HazelcastMapEndpointProducerBuilder interfaces plus unified HazelcastMapEndpointBuilder. Advanced variants provide comprehensive control over map instance selection, key-value operations, entry listeners, query predicates, aggregation functions, eviction policies, and Hazelcast cluster configuration. Contains HazelcastMapBuilders interface and HazelcastMapHeaderNameBuilder static inner class providing String constants for HazelcastMap-specific headers. Single inner implementation class manages builder state and Hazelcast distributed map configuration.

---

### File 134
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastMultimapEndpointBuilderFactory.java  
**Total Lines:** 725

**Type Declarations:** 10 total

1. Line 35: `HazelcastMultimapEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastMultimapEndpointConsumerBuilder` (public interface)
3. Line 150: `AdvancedHazelcastMultimapEndpointConsumerBuilder` (public interface)
4. Line 278: `HazelcastMultimapEndpointProducerBuilder` (public interface)
5. Line 389: `AdvancedHazelcastMultimapEndpointProducerBuilder` (public interface)
6. Line 445: `HazelcastMultimapEndpointBuilder` (public interface)
7. Line 557: `AdvancedHazelcastMultimapEndpointBuilder` (public interface)
8. Line 567: `HazelcastMultimapBuilders` (public interface)
9. Line 626: `HazelcastMultimapHeaderNameBuilder` (public static class)
10. Line 719: `HazelcastMultimapEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastMultimap endpoint component enabling integration with Hazelcast multimap data structure (one key mapped to multiple values) for distributed collections with value multiplicity and cluster-wide consistency. Large builder with distinct consumer and producer variants for bidirectional multimap operations. Provides fluent DSL through separate HazelcastMultimapEndpointConsumerBuilder and HazelcastMultimapEndpointProducerBuilder interfaces plus unified HazelcastMultimapEndpointBuilder. Advanced variants provide comprehensive control over multimap instance selection, key-value pair operations, value collection management, entry listeners, query predicates, and Hazelcast cluster configuration. Contains HazelcastMultimapBuilders interface and HazelcastMultimapHeaderNameBuilder static inner class providing String constants for HazelcastMultimap-specific headers. Single inner implementation class manages builder state and Hazelcast distributed multimap configuration.

---

## Phase 35: Files 135-138

### File 135
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastPNCounterEndpointBuilderFactory.java  
**Total Lines:** 259

**Type Declarations:** 5 total

1. Line 36: `HazelcastPNCounterEndpointBuilderFactory` (public interface)
2. Line 41: `HazelcastPNCounterEndpointBuilder` (public interface)
3. Line 152: `AdvancedHazelcastPNCounterEndpointBuilder` (public interface)
4. Line 207: `HazelcastPNCounterBuilders` (public interface)
5. Line 253: `HazelcastPNCounterEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastPNCounter endpoint component enabling integration with Hazelcast PN (Positive-Negative) distributed counter data structure for cluster-wide atomic increment/decrement operations with concurrent access semantics. Compact builder supporting Hazelcast PN counter operations. Provides fluent DSL through HazelcastPNCounterEndpointBuilder interface with advanced variant for fine-grained control over counter instance selection, increment/decrement operations, and value retrieval patterns. Contains HazelcastPNCounterBuilders interface and single inner implementation class managing builder state and Hazelcast PN counter client configuration.

---

### File 136
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastQueueEndpointBuilderFactory.java  
**Total Lines:** 817

**Type Declarations:** 10 total

1. Line 35: `HazelcastQueueEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastQueueEndpointConsumerBuilder` (public interface)
3. Line 242: `AdvancedHazelcastQueueEndpointConsumerBuilder` (public interface)
4. Line 370: `HazelcastQueueEndpointProducerBuilder` (public interface)
5. Line 481: `AdvancedHazelcastQueueEndpointProducerBuilder` (public interface)
6. Line 537: `HazelcastQueueEndpointBuilder` (public interface)
7. Line 649: `AdvancedHazelcastQueueEndpointBuilder` (public interface)
8. Line 659: `HazelcastQueueBuilders` (public interface)
9. Line 718: `HazelcastQueueHeaderNameBuilder` (public static class)
10. Line 811: `HazelcastQueueEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastQueue endpoint component enabling integration with Hazelcast distributed queue data structure for FIFO message handling, blocking operations, and cluster-wide item synchronization. Large builder with distinct consumer and producer variants for bidirectional queue operations. Provides fluent DSL through separate HazelcastQueueEndpointConsumerBuilder and HazelcastQueueEndpointProducerBuilder interfaces plus unified HazelcastQueueEndpointBuilder. Advanced variants provide comprehensive control over queue instance selection, item addition/removal operations, blocking semantics, listener registration, and Hazelcast cluster configuration. Contains HazelcastQueueBuilders interface and HazelcastQueueHeaderNameBuilder static inner class providing String constants for HazelcastQueue-specific headers. Single inner implementation class manages builder state and Hazelcast distributed queue configuration.

---

### File 137
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastReplicatedmapEndpointBuilderFactory.java  
**Total Lines:** 713

**Type Declarations:** 10 total

1. Line 35: `HazelcastReplicatedmapEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastReplicatedmapEndpointConsumerBuilder` (public interface)
3. Line 150: `AdvancedHazelcastReplicatedmapEndpointConsumerBuilder` (public interface)
4. Line 278: `HazelcastReplicatedmapEndpointProducerBuilder` (public interface)
5. Line 389: `AdvancedHazelcastReplicatedmapEndpointProducerBuilder` (public interface)
6. Line 445: `HazelcastReplicatedmapEndpointBuilder` (public interface)
7. Line 557: `AdvancedHazelcastReplicatedmapEndpointBuilder` (public interface)
8. Line 567: `HazelcastReplicatedmapBuilders` (public interface)
9. Line 626: `HazelcastReplicatedmapHeaderNameBuilder` (public static class)
10. Line 707: `HazelcastReplicatedmapEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastReplicatedmap endpoint component enabling integration with Hazelcast replicated map data structure for weakly-consistent, eventually-replicated key-value storage across all cluster members. Large builder with distinct consumer and producer variants for bidirectional replicated map operations. Provides fluent DSL through separate HazelcastReplicatedmapEndpointConsumerBuilder and HazelcastReplicatedmapEndpointProducerBuilder interfaces plus unified HazelcastReplicatedmapEndpointBuilder. Advanced variants provide comprehensive control over replicated map instance selection, key-value operations, entry listeners, query predicates, eviction policies, consistency semantics, and Hazelcast cluster replication configuration. Contains HazelcastReplicatedmapBuilders interface and HazelcastReplicatedmapHeaderNameBuilder static inner class providing String constants for HazelcastReplicatedmap-specific headers. Single inner implementation class manages builder state and Hazelcast replicated map configuration with eventual consistency guarantees.

---

### File 138
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastRingbufferEndpointBuilderFactory.java  
**Total Lines:** 292

**Type Declarations:** 6 total

1. Line 35: `HazelcastRingbufferEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastRingbufferEndpointBuilder` (public interface)
3. Line 151: `AdvancedHazelcastRingbufferEndpointBuilder` (public interface)
4. Line 206: `HazelcastRingbufferBuilders` (public interface)
5. Line 265: `HazelcastRingbufferHeaderNameBuilder` (public static class)
6. Line 286: `HazelcastRingbufferEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastRingbuffer endpoint component enabling integration with Hazelcast ringbuffer data structure (fixed-size circular buffer) for high-throughput, low-latency event streaming and sequence-based item access. Compact builder supporting Hazelcast ringbuffer operations with fixed capacity and circular overwrite semantics. Provides fluent DSL through HazelcastRingbufferEndpointBuilder interface with advanced variant for fine-grained control over ringbuffer instance selection, item addition/retrieval operations, sequence-based access patterns, and Hazelcast cluster configuration. Contains HazelcastRingbufferBuilders interface and HazelcastRingbufferHeaderNameBuilder static inner class providing String constants for HazelcastRingbuffer-specific headers. Single inner implementation class manages builder state and Hazelcast ringbuffer configuration.

---

## Phase 36: Files 139-142

### File 139
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastSedaEndpointBuilderFactory.java  
**Total Lines:** 1112

**Type Declarations:** 9 total

1. Line 36: `HazelcastSedaEndpointBuilderFactory` (public interface)
2. Line 41: `HazelcastSedaEndpointConsumerBuilder` (public interface)
3. Line 315: `AdvancedHazelcastSedaEndpointConsumerBuilder` (public interface)
4. Line 443: `HazelcastSedaEndpointProducerBuilder` (public interface)
5. Line 718: `AdvancedHazelcastSedaEndpointProducerBuilder` (public interface)
6. Line 774: `HazelcastSedaEndpointBuilder` (public interface)
7. Line 1050: `AdvancedHazelcastSedaEndpointBuilder` (public interface)
8. Line 1060: `HazelcastSedaBuilders` (public interface)
9. Line 1106: `HazelcastSedaEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastSeda endpoint component enabling integration with Hazelcast SEDA (Staged Event-Driven Architecture) queue for asynchronous in-memory messaging with thread pool coordination and component decoupling. Very large builder with distinct consumer and producer variants for bidirectional SEDA queue operations. Provides fluent DSL through separate HazelcastSedaEndpointConsumerBuilder and HazelcastSedaEndpointProducerBuilder interfaces plus unified HazelcastSedaEndpointBuilder. Advanced variants provide comprehensive control over queue instance selection, consumer threads, thread pool configuration, concurrency semantics, listener registration, and Hazelcast cluster synchronization. Contains HazelcastSedaBuilders interface and single inner implementation class managing builder state and Hazelcast SEDA queue configuration with thread-safe async dispatch.

---

### File 140
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastSetEndpointBuilderFactory.java  
**Total Lines:** 713

**Type Declarations:** 10 total

1. Line 35: `HazelcastSetEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastSetEndpointConsumerBuilder` (public interface)
3. Line 150: `AdvancedHazelcastSetEndpointConsumerBuilder` (public interface)
4. Line 278: `HazelcastSetEndpointProducerBuilder` (public interface)
5. Line 389: `AdvancedHazelcastSetEndpointProducerBuilder` (public interface)
6. Line 445: `HazelcastSetEndpointBuilder` (public interface)
7. Line 557: `AdvancedHazelcastSetEndpointBuilder` (public interface)
8. Line 567: `HazelcastSetBuilders` (public interface)
9. Line 626: `HazelcastSetHeaderNameBuilder` (public static class)
10. Line 707: `HazelcastSetEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastSet endpoint component enabling integration with Hazelcast distributed set data structure for unique element collection, membership testing, and cluster-wide set operations. Large builder with distinct consumer and producer variants for bidirectional set operations. Provides fluent DSL through separate HazelcastSetEndpointConsumerBuilder and HazelcastSetEndpointProducerBuilder interfaces plus unified HazelcastSetEndpointBuilder. Advanced variants provide comprehensive control over set instance selection, element addition/removal operations, membership validation, listener registration, query predicates, and Hazelcast cluster configuration. Contains HazelcastSetBuilders interface and HazelcastSetHeaderNameBuilder static inner class providing String constants for HazelcastSet-specific headers. Single inner implementation class manages builder state and Hazelcast distributed set configuration.

---

### File 141
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HazelcastTopicEndpointBuilderFactory.java  
**Total Lines:** 803

**Type Declarations:** 10 total

1. Line 35: `HazelcastTopicEndpointBuilderFactory` (public interface)
2. Line 40: `HazelcastTopicEndpointConsumerBuilder` (public interface)
3. Line 180: `AdvancedHazelcastTopicEndpointConsumerBuilder` (public interface)
4. Line 308: `HazelcastTopicEndpointProducerBuilder` (public interface)
5. Line 449: `AdvancedHazelcastTopicEndpointProducerBuilder` (public interface)
6. Line 505: `HazelcastTopicEndpointBuilder` (public interface)
7. Line 647: `AdvancedHazelcastTopicEndpointBuilder` (public interface)
8. Line 657: `HazelcastTopicBuilders` (public interface)
9. Line 716: `HazelcastTopicHeaderNameBuilder` (public static class)
10. Line 797: `HazelcastTopicEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HazelcastTopic endpoint component enabling integration with Hazelcast distributed publish-subscribe topic for cluster-wide message broadcast and multi-consumer async notification delivery. Large builder with distinct consumer and producer variants for bidirectional topic operations. Provides fluent DSL through separate HazelcastTopicEndpointConsumerBuilder and HazelcastTopicEndpointProducerBuilder interfaces plus unified HazelcastTopicEndpointBuilder. Advanced variants provide comprehensive control over topic instance selection, message publication/subscription operations, listener registration, message filtering, cluster-wide broadcast semantics, and Hazelcast coordination. Contains HazelcastTopicBuilders interface and HazelcastTopicHeaderNameBuilder static inner class providing String constants for HazelcastTopic-specific headers. Single inner implementation class manages builder state and Hazelcast distributed topic configuration with broadcast delivery.

---

### File 142
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HttpEndpointBuilderFactory.java  
**Total Lines:** 2181

**Type Declarations:** 6 total

1. Line 35: `HttpEndpointBuilderFactory` (public interface)
2. Line 40: `HttpEndpointBuilder` (public interface)
3. Line 1065: `AdvancedHttpEndpointBuilder` (public interface)
4. Line 1911: `HttpBuilders` (public interface)
5. Line 1989: `HttpHeaderNameBuilder` (public static class)
6. Line 2175: `HttpEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Http endpoint component enabling integration with HTTP/HTTPS APIs for REST client operations, request/response handling, SSL/TLS encryption, and HTTP method routing. Very large builder with extensive configuration options and specialized parameter handling. Provides fluent DSL through HttpEndpointBuilder interface with advanced variant for fine-grained control over HTTP method, SSL context, authentication, proxy configuration, connection pooling, timeout settings, cookie management, and request/response header manipulation. Contains HttpBuilders interface and HttpHeaderNameBuilder static inner class providing String constants for HTTP-specific headers. Single inner implementation class manages builder state and HTTP client configuration with comprehensive protocol and security options.

---

## Phase 37: Files 143-146

### File 143
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/HuggingFaceEndpointBuilderFactory.java  
**Total Lines:** 608

**Type Declarations:** 6 total

1. Line 36: `HuggingFaceEndpointBuilderFactory` (public interface)
2. Line 41: `HuggingFaceEndpointBuilder` (public interface)
3. Line 456: `AdvancedHuggingFaceEndpointBuilder` (public interface)
4. Line 511: `HuggingFaceBuilders` (public interface)
5. Line 581: `HuggingFaceHeaderNameBuilder` (public static class)
6. Line 602: `HuggingFaceEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for HuggingFace endpoint component enabling integration with Hugging Face machine learning APIs for inference, model hosting, and NLP task execution including text generation, question answering, and embeddings. Compact builder supporting HuggingFace API operations. Provides fluent DSL through HuggingFaceEndpointBuilder interface with advanced variant for fine-grained control over API endpoint selection, model loading, inference parameters, authentication token handling, and response processing. Contains HuggingFaceBuilders interface and HuggingFaceHeaderNameBuilder static inner class providing String constants for HuggingFace-specific headers. Single inner implementation class manages builder state and HuggingFace API client configuration.

---

### File 144
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IAM2EndpointBuilderFactory.java  
**Total Lines:** 967

**Type Declarations:** 6 total

1. Line 35: `IAM2EndpointBuilderFactory` (public interface)
2. Line 40: `IAM2EndpointBuilder` (public interface)
3. Line 476: `AdvancedIAM2EndpointBuilder` (public interface)
4. Line 531: `IAM2Builders` (public interface)
5. Line 590: `IAM2HeaderNameBuilder` (public static class)
6. Line 961: `IAM2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IAM2 (AWS Identity and Access Management v2) endpoint component enabling integration with AWS IAM for user/role/policy management, credential provisioning, and access control configuration. Large builder supporting comprehensive IAM v2 operations with extensive configuration options. Provides fluent DSL through IAM2EndpointBuilder interface with advanced variant for fine-grained control over IAM operation selection, credential handling, policy document management, role assumption, and AWS SigV4 authentication. Contains IAM2Builders interface and IAM2HeaderNameBuilder static inner class providing String constants for IAM2-specific headers. Single inner implementation class manages builder state and AWS IAM v2 client configuration.

---

### File 145
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IAMEndpointBuilderFactory.java  
**Total Lines:** 360

**Type Declarations:** 5 total

1. Line 35: `IAMEndpointBuilderFactory` (public interface)
2. Line 40: `IAMEndpointBuilder` (public interface)
3. Line 255: `AdvancedIAMEndpointBuilder` (public interface)
4. Line 310: `IAMBuilders` (public interface)
5. Line 354: `IAMEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IAM (AWS Identity and Access Management) endpoint component enabling integration with AWS IAM for user/group/role/policy management, credential administration, and access control. Compact builder supporting foundational IAM operations with targeted configuration options. Provides fluent DSL through IAMEndpointBuilder interface with advanced variant for fine-grained control over IAM operation selection, user management, policy attachment, role creation, and AWS SigV4 authentication. Contains IAMBuilders interface and single inner implementation class managing builder state and AWS IAM client configuration.

---

### File 146
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IBMCOSEndpointBuilderFactory.java  
**Total Lines:** 1991

**Type Declarations:** 10 total

1. Line 35: `IBMCOSEndpointBuilderFactory` (public interface)
2. Line 40: `IBMCOSEndpointConsumerBuilder` (public interface)
3. Line 865: `AdvancedIBMCOSEndpointConsumerBuilder` (public interface)
4. Line 1121: `IBMCOSEndpointProducerBuilder` (public interface)
5. Line 1401: `AdvancedIBMCOSEndpointProducerBuilder` (public interface)
6. Line 1487: `IBMCOSEndpointBuilder` (public interface)
7. Line 1617: `AdvancedIBMCOSEndpointBuilder` (public interface)
8. Line 1657: `IBMCOSBuilders` (public interface)
9. Line 1716: `IBMCOSHeaderNameBuilder` (public static class)
10. Line 1985: `IBMCOSEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IBMCOS (IBM Cloud Object Storage) endpoint component enabling integration with IBM Cloud object storage service for cloud-native file storage, retrieval, and bucket management with S3-compatible API. Very large builder with distinct consumer and producer variants for bidirectional object storage operations. Provides fluent DSL through separate IBMCOSEndpointConsumerBuilder and IBMCOSEndpointProducerBuilder interfaces plus unified IBMCOSEndpointBuilder. Advanced variants provide comprehensive control over bucket selection, object operations, multipart uploads, access control lists, storage classes, encryption, versioning, and IBM Cloud authentication. Contains IBMCOSBuilders interface and IBMCOSHeaderNameBuilder static inner class providing String constants for IBMCOS-specific headers. Single inner implementation class manages builder state and IBM Cloud Object Storage client configuration.

---

## Phase 38: Files 147-150

### File 147
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IBMSecretsManagerEndpointBuilderFactory.java  
**Total Lines:** 314

**Type Declarations:** 6 total

1. Line 35: `IBMSecretsManagerEndpointBuilderFactory` (public interface)
2. Line 40: `IBMSecretsManagerEndpointBuilder` (public interface)
3. Line 110: `AdvancedIBMSecretsManagerEndpointBuilder` (public interface)
4. Line 165: `IBMSecretsManagerBuilders` (public interface)
5. Line 224: `IBMSecretsManagerHeaderNameBuilder` (public static class)
6. Line 308: `IBMSecretsManagerEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IBMSecretsManager endpoint component enabling integration with IBM Cloud Secrets Manager for secure credential storage, rotation, and secret retrieval with enterprise-grade key management. Compact builder supporting IBM Secrets Manager operations. Provides fluent DSL through IBMSecretsManagerEndpointBuilder interface with advanced variant for fine-grained control over secret retrieval, credential management, authentication token handling, and IBM Cloud identity provider configuration. Contains IBMSecretsManagerBuilders interface and IBMSecretsManagerHeaderNameBuilder static inner class providing String constants for IBMSecretsManager-specific headers. Single inner implementation class manages builder state and IBM Cloud Secrets Manager client configuration.

---

### File 148
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IggyEndpointBuilderFactory.java  
**Total Lines:** 1928

**Type Declarations:** 9 total

1. Line 35: `IggyEndpointBuilderFactory` (public interface)
2. Line 40: `IggyEndpointConsumerBuilder` (public interface)
3. Line 689: `AdvancedIggyEndpointConsumerBuilder` (public interface)
4. Line 849: `IggyEndpointProducerBuilder` (public interface)
5. Line 1314: `AdvancedIggyEndpointProducerBuilder` (public interface)
6. Line 1402: `IggyEndpointBuilder` (public interface)
7. Line 1836: `AdvancedIggyEndpointBuilder` (public interface)
8. Line 1878: `IggyBuilders` (public interface)
9. Line 1922: `IggyEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Iggy endpoint component enabling integration with Iggy streams (CQRS event streaming platform) for append-only log operations, consumer group coordination, and cluster-wide event distribution. Very large builder with distinct consumer and producer variants for bidirectional stream operations. Provides fluent DSL through separate IggyEndpointConsumerBuilder and IggyEndpointProducerBuilder interfaces plus unified IggyEndpointBuilder. Advanced variants provide comprehensive control over stream/topic selection, message publish/consume operations, consumer group management, offset tracking, batch processing, and Iggy cluster configuration. Contains IggyBuilders interface and single inner implementation class managing builder state and Iggy streams client configuration with CQRS semantics.

---

### File 149
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteCacheEndpointBuilderFactory.java  
**Total Lines:** 920

**Type Declarations:** 10 total

1. Line 36: `IgniteCacheEndpointBuilderFactory` (public interface)
2. Line 41: `IgniteCacheEndpointConsumerBuilder` (public interface)
3. Line 308: `AdvancedIgniteCacheEndpointConsumerBuilder` (public interface)
4. Line 436: `IgniteCacheEndpointProducerBuilder` (public interface)
5. Line 608: `AdvancedIgniteCacheEndpointProducerBuilder` (public interface)
6. Line 664: `IgniteCacheEndpointBuilder` (public interface)
7. Line 741: `AdvancedIgniteCacheEndpointBuilder` (public interface)
8. Line 751: `IgniteCacheBuilders` (public interface)
9. Line 813: `IgniteCacheHeaderNameBuilder` (public static class)
10. Line 914: `IgniteCacheEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteCache endpoint component enabling integration with Apache Ignite distributed in-memory cache grid for high-performance caching, SQL queries, and ACID transactions. Large builder with distinct consumer and producer variants for bidirectional cache operations. Provides fluent DSL through separate IgniteCacheEndpointConsumerBuilder and IgniteCacheEndpointProducerBuilder interfaces plus unified IgniteCacheEndpointBuilder. Advanced variants provide comprehensive control over cache instance selection, key-value operations, SQL query execution, transaction semantics, event listeners, cache modes (replicated/partitioned), and Ignite cluster topology management. Contains IgniteCacheBuilders interface and IgniteCacheHeaderNameBuilder static inner class providing String constants for IgniteCache-specific headers. Single inner implementation class manages builder state and Apache Ignite cache grid configuration.

---

### File 150
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteComputeEndpointBuilderFactory.java  
**Total Lines:** 439

**Type Declarations:** 6 total

1. Line 35: `IgniteComputeEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteComputeEndpointBuilder` (public interface)
3. Line 246: `AdvancedIgniteComputeEndpointBuilder` (public interface)
4. Line 301: `IgniteComputeBuilders` (public interface)
5. Line 360: `IgniteComputeHeaderNameBuilder` (public static class)
6. Line 433: `IgniteComputeEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteCompute endpoint component enabling integration with Apache Ignite distributed compute grid for data-parallel processing, task execution, and cluster-wide computation. Compact builder supporting Ignite compute operations. Provides fluent DSL through IgniteComputeEndpointBuilder interface with advanced variant for fine-grained control over compute cluster selection, task execution, job scheduling, data distribution, load balancing, and Ignite cluster topology management. Contains IgniteComputeBuilders interface and IgniteComputeHeaderNameBuilder static inner class providing String constants for IgniteCompute-specific headers. Single inner implementation class manages builder state and Apache Ignite compute grid configuration.

---

## Phase 39: Files 151-154

### File 151
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteEventsEndpointBuilderFactory.java  
**Total Lines:** 343

**Type Declarations:** 5 total

1. Line 35: `IgniteEventsEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteEventsEndpointBuilder` (public interface)
3. Line 165: `AdvancedIgniteEventsEndpointBuilder` (public interface)
4. Line 291: `IgniteEventsBuilders` (public interface)
5. Line 337: `IgniteEventsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteEvents endpoint component enabling integration with Apache Ignite event grid for cluster-wide event notification, topology changes, and cache operation monitoring. Compact builder supporting Ignite events operations. Provides fluent DSL through IgniteEventsEndpointBuilder interface with advanced variant for fine-grained control over event filter selection, event listener registration, topology change tracking, and Ignite cluster event notification delivery. Contains IgniteEventsBuilders interface and single inner implementation class managing builder state and Apache Ignite events grid configuration.

---

### File 152
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteIdGenEndpointBuilderFactory.java  
**Total Lines:** 353

**Type Declarations:** 6 total

1. Line 35: `IgniteIdGenEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteIdGenEndpointBuilder` (public interface)
3. Line 211: `AdvancedIgniteIdGenEndpointBuilder` (public interface)
4. Line 266: `IgniteIdGenBuilders` (public interface)
5. Line 325: `IgniteIdGenHeaderNameBuilder` (public static class)
6. Line 347: `IgniteIdGenEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteIdGen endpoint component enabling integration with Apache Ignite distributed ID generator for cluster-wide unique identifier allocation with guaranteed monotonic increasing sequences. Compact builder supporting Ignite ID generation operations. Provides fluent DSL through IgniteIdGenEndpointBuilder interface with advanced variant for fine-grained control over ID generator instance selection, sequence initialization, allocation batch size, and Ignite cluster ID synchronization. Contains IgniteIdGenBuilders interface and IgniteIdGenHeaderNameBuilder static inner class providing String constants for IgniteIdGen-specific headers. Single inner implementation class manages builder state and Apache Ignite ID generator configuration.

---

### File 153
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteMessagingEndpointBuilderFactory.java  
**Total Lines:** 623

**Type Declarations:** 10 total

1. Line 35: `IgniteMessagingEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteMessagingEndpointConsumerBuilder` (public interface)
3. Line 115: `AdvancedIgniteMessagingEndpointConsumerBuilder` (public interface)
4. Line 243: `IgniteMessagingEndpointProducerBuilder` (public interface)
5. Line 379: `AdvancedIgniteMessagingEndpointProducerBuilder` (public interface)
6. Line 435: `IgniteMessagingEndpointBuilder` (public interface)
7. Line 512: `AdvancedIgniteMessagingEndpointBuilder` (public interface)
8. Line 522: `IgniteMessagingBuilders` (public interface)
9. Line 581: `IgniteMessagingHeaderNameBuilder` (public static class)
10. Line 617: `IgniteMessagingEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteMessaging endpoint component enabling integration with Apache Ignite messaging grid for cluster-wide point-to-point messaging, topic-based broadcasting, and ordered message delivery. Large builder with distinct consumer and producer variants for bidirectional messaging operations. Provides fluent DSL through separate IgniteMessagingEndpointConsumerBuilder and IgniteMessagingEndpointProducerBuilder interfaces plus unified IgniteMessagingEndpointBuilder. Advanced variants provide comprehensive control over topic/topic filter selection, message send/receive operations, listener registration, ordering semantics, message filtering, and Ignite cluster messaging configuration. Contains IgniteMessagingBuilders interface and IgniteMessagingHeaderNameBuilder static inner class providing String constants for IgniteMessaging-specific headers. Single inner implementation class manages builder state and Apache Ignite messaging grid configuration.

---

### File 154
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteQueueEndpointBuilderFactory.java  
**Total Lines:** 423

**Type Declarations:** 6 total

1. Line 35: `IgniteQueueEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteQueueEndpointBuilder` (public interface)
3. Line 244: `AdvancedIgniteQueueEndpointBuilder` (public interface)
4. Line 299: `IgniteQueueBuilders` (public interface)
5. Line 358: `IgniteQueueHeaderNameBuilder` (public static class)
6. Line 417: `IgniteQueueEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteQueue endpoint component enabling integration with Apache Ignite distributed queue for FIFO message ordering, blocking operations, and cluster-wide item synchronization with atomic semantics. Compact builder supporting Ignite queue operations. Provides fluent DSL through IgniteQueueEndpointBuilder interface with advanced variant for fine-grained control over queue instance selection, item addition/removal operations, blocking semantics, listener registration, and Ignite cluster queue configuration. Contains IgniteQueueBuilders interface and IgniteQueueHeaderNameBuilder static inner class providing String constants for IgniteQueue-specific headers. Single inner implementation class manages builder state and Apache Ignite queue configuration.

---

## Phase 40: Files 155-158

### File 155
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IgniteSetEndpointBuilderFactory.java  
**Total Lines:** 332

**Type Declarations:** 6 total

1. Line 35: `IgniteSetEndpointBuilderFactory` (public interface)
2. Line 40: `IgniteSetEndpointBuilder` (public interface)
3. Line 190: `AdvancedIgniteSetEndpointBuilder` (public interface)
4. Line 245: `IgniteSetBuilders` (public interface)
5. Line 304: `IgniteSetHeaderNameBuilder` (public static class)
6. Line 326: `IgniteSetEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IgniteSet endpoint component enabling integration with Apache Ignite distributed set for unique element collection, membership testing, and cluster-wide set operations with atomic semantics. Compact builder supporting Ignite set operations. Provides fluent DSL through IgniteSetEndpointBuilder interface with advanced variant for fine-grained control over set instance selection, element addition/removal operations, membership validation, listener registration, and Ignite cluster set configuration. Contains IgniteSetBuilders interface and IgniteSetHeaderNameBuilder static inner class providing String constants for IgniteSet-specific headers. Single inner implementation class manages builder state and Apache Ignite set configuration.

---

### File 156
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/ImageRecognitionEndpointBuilderFactory.java  
**Total Lines:** 495

**Type Declarations:** 5 total

1. Line 35: `ImageRecognitionEndpointBuilderFactory` (public interface)
2. Line 40: `ImageRecognitionEndpointBuilder` (public interface)
3. Line 386: `AdvancedImageRecognitionEndpointBuilder` (public interface)
4. Line 441: `ImageRecognitionBuilders` (public interface)
5. Line 489: `ImageRecognitionEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for ImageRecognition endpoint component enabling integration with AI image recognition services for object detection, scene analysis, facial recognition, and computer vision operations. Compact builder supporting image analysis operations. Provides fluent DSL through ImageRecognitionEndpointBuilder interface with advanced variant for fine-grained control over model selection, image input handling, recognition parameters, confidence thresholds, and AI service provider configuration. Contains ImageRecognitionBuilders interface and single inner implementation class managing builder state and image recognition service client configuration.

---

### File 157
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/InfinispanEmbeddedEndpointBuilderFactory.java  
**Total Lines:** 1403

**Type Declarations:** 10 total

1. Line 35: `InfinispanEmbeddedEndpointBuilderFactory` (public interface)
2. Line 40: `InfinispanEmbeddedEndpointConsumerBuilder` (public interface)
3. Line 192: `AdvancedInfinispanEmbeddedEndpointConsumerBuilder` (public interface)
4. Line 463: `InfinispanEmbeddedEndpointProducerBuilder` (public interface)
5. Line 649: `AdvancedInfinispanEmbeddedEndpointProducerBuilder` (public interface)
6. Line 848: `InfinispanEmbeddedEndpointBuilder` (public interface)
7. Line 891: `AdvancedInfinispanEmbeddedEndpointBuilder` (public interface)
8. Line 1044: `InfinispanEmbeddedBuilders` (public interface)
9. Line 1110: `InfinispanEmbeddedHeaderNameBuilder` (public static class)
10. Line 1397: `InfinispanEmbeddedEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for InfinispanEmbedded endpoint component enabling integration with Red Hat Infinispan embedded cache for in-process caching, distributed data structures, and cluster-aware cache operations. Very large builder with distinct consumer and producer variants for bidirectional cache operations. Provides fluent DSL through separate InfinispanEmbeddedEndpointConsumerBuilder and InfinispanEmbeddedEndpointProducerBuilder interfaces plus unified InfinispanEmbeddedEndpointBuilder. Advanced variants provide comprehensive control over cache instance selection, key-value operations, transaction semantics, event listeners, cache modes, persistence configuration, and Infinispan cluster topology. Contains InfinispanEmbeddedBuilders interface and InfinispanEmbeddedHeaderNameBuilder static inner class providing String constants for InfinispanEmbedded-specific headers. Single inner implementation class manages builder state and Infinispan embedded cache configuration.

---

### File 158
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/InfinispanRemoteEndpointBuilderFactory.java  
**Total Lines:** 1947

**Type Declarations:** 10 total

1. Line 35: `InfinispanRemoteEndpointBuilderFactory` (public interface)
2. Line 40: `InfinispanRemoteEndpointConsumerBuilder` (public interface)
3. Line 244: `AdvancedInfinispanRemoteEndpointConsumerBuilder` (public interface)
4. Line 548: `InfinispanRemoteEndpointProducerBuilder` (public interface)
5. Line 849: `AdvancedInfinispanRemoteEndpointProducerBuilder` (public interface)
6. Line 1299: `InfinispanRemoteEndpointBuilder` (public interface)
7. Line 1457: `AdvancedInfinispanRemoteEndpointBuilder` (public interface)
8. Line 1643: `InfinispanRemoteBuilders` (public interface)
9. Line 1709: `InfinispanRemoteHeaderNameBuilder` (public static class)
10. Line 1941: `InfinispanRemoteEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for InfinispanRemote endpoint component enabling integration with Red Hat Infinispan remote cache server for distributed caching, shared data storage, and cluster-coordinated cache operations across application instances. Very large builder with distinct consumer and producer variants for bidirectional remote cache operations. Provides fluent DSL through separate InfinispanRemoteEndpointConsumerBuilder and InfinispanRemoteEndpointProducerBuilder interfaces plus unified InfinispanRemoteEndpointBuilder. Advanced variants provide comprehensive control over remote cache server connection, cache instance selection, key-value operations, transaction semantics, event listeners, hot rod protocol options, security configuration, and multi-datacenter replication. Contains InfinispanRemoteBuilders interface and InfinispanRemoteHeaderNameBuilder static inner class providing String constants for InfinispanRemote-specific headers. Single inner implementation class manages builder state and Infinispan remote cache server client configuration.

---

## Phase 41: Files 159-162

### File 159
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/InfluxDb2EndpointBuilderFactory.java  
**Total Lines:** 394

**Type Declarations:** 6 total

1. Line 35: `InfluxDb2EndpointBuilderFactory` (public interface)
2. Line 40: `InfluxDb2EndpointBuilder` (public interface)
3. Line 225: `AdvancedInfluxDb2EndpointBuilder` (public interface)
4. Line 280: `InfluxDb2Builders` (public interface)
5. Line 341: `InfluxDb2HeaderNameBuilder` (public static class)
6. Line 388: `InfluxDb2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for InfluxDB2 endpoint component enabling integration with InfluxDB 2.x time-series database for metrics and analytics collection, time-series data storage with retention policies, and high-performance time-based queries with tags and fields. Compact builder supporting InfluxDB2 operations. Provides fluent DSL through InfluxDb2EndpointBuilder interface with advanced variant for fine-grained control over database connection parameters, bucket selection, authentication tokens, retention policy options, and InfluxDB client configuration. Contains InfluxDb2Builders interface and InfluxDb2HeaderNameBuilder static inner class providing String constants for InfluxDB2-specific headers. Single inner implementation class manages builder state and InfluxDB2 client and bucket configuration.

---

### File 160
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/InfluxDbEndpointBuilderFactory.java  
**Total Lines:** 369

**Type Declarations:** 6 total

1. Line 35: `InfluxDbEndpointBuilderFactory` (public interface)
2. Line 40: `InfluxDbEndpointBuilder` (public interface)
3. Line 203: `AdvancedInfluxDbEndpointBuilder` (public interface)
4. Line 258: `InfluxDbBuilders` (public interface)
5. Line 317: `InfluxDbHeaderNameBuilder` (public static class)
6. Line 363: `InfluxDbEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for InfluxDB endpoint component enabling integration with InfluxDB 1.x time-series database for metrics collection, time-series data write and query operations with tags and fields, and application monitoring data persistence with flexible retention. Compact builder supporting InfluxDB 1.x operations. Provides fluent DSL through InfluxDbEndpointBuilder interface with advanced variant for fine-grained control over database connection parameters, database name selection, measurement naming, field and tag configuration, and InfluxDB server endpoint. Contains InfluxDbBuilders interface and InfluxDbHeaderNameBuilder static inner class providing String constants for InfluxDB-specific headers. Single inner implementation class manages builder state and InfluxDB 1.x database connection and measurements configuration.

---

### File 161
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IrcEndpointBuilderFactory.java  
**Total Lines:** 2265

**Type Declarations:** 10 total

1. Line 35: `IrcEndpointBuilderFactory` (public interface)
2. Line 40: `IrcEndpointConsumerBuilder` (public interface)
3. Line 617: `AdvancedIrcEndpointConsumerBuilder` (public interface)
4. Line 775: `IrcEndpointProducerBuilder` (public interface)
5. Line 1353: `AdvancedIrcEndpointProducerBuilder` (public interface)
6. Line 1439: `IrcEndpointBuilder` (public interface)
7. Line 2018: `AdvancedIrcEndpointBuilder` (public interface)
8. Line 2058: `IrcBuilders` (public interface)
9. Line 2128: `IrcHeaderNameBuilder` (public static class)
10. Line 2259: `IrcEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IRC endpoint component enabling integration with Internet Relay Chat networks for real-time message broadcast, multi-user chat rooms, presence detection, and IRC protocol communication. Very large builder with distinct consumer and producer variants for bidirectional IRC operations. Provides fluent DSL through separate IrcEndpointConsumerBuilder and IrcEndpointProducerBuilder interfaces plus unified IrcEndpointBuilder. Advanced variants provide comprehensive control over IRC server connection, channel/nickname selection, user identification and authentication, message encoding, DCC (Direct Client Connection) handling, and IRC network configuration. Contains IrcBuilders interface and IrcHeaderNameBuilder static inner class providing String constants for IRC-specific headers. Single inner implementation class manages builder state and IRC network connection and channel/room configuration.

---

### File 162
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/IronMQEndpointBuilderFactory.java  
**Total Lines:** 1443

**Type Declarations:** 10 total

1. Line 36: `IronMQEndpointBuilderFactory` (public interface)
2. Line 41: `IronMQEndpointConsumerBuilder` (public interface)
3. Line 778: `AdvancedIronMQEndpointConsumerBuilder` (public interface)
4. Line 971: `IronMQEndpointProducerBuilder` (public interface)
5. Line 1094: `AdvancedIronMQEndpointProducerBuilder` (public interface)
6. Line 1179: `IronMQEndpointBuilder` (public interface)
7. Line 1273: `AdvancedIronMQEndpointBuilder` (public interface)
8. Line 1312: `IronMQBuilders` (public interface)
9. Line 1377: `IronMQHeaderNameBuilder` (public static class)
10. Line 1437: `IronMQEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IronMQ endpoint component enabling integration with IronMQ elastic and durable hosted message queue service for reliable asynchronous messaging, distributed job processing, and cloud-hosted message persistence. Very large builder with distinct consumer and producer variants for bidirectional messaging. Provides fluent DSL through separate IronMQEndpointConsumerBuilder and IronMQEndpointProducerBuilder interfaces plus unified IronMQEndpointBuilder. Advanced variants provide comprehensive control over IronMQ project/queue selection, authentication credentials, message visibility timeout, expiration settings, push queue configuration, subscriber endpoints, and IronMQ cloud service client configuration. Contains IronMQBuilders interface and IronMQHeaderNameBuilder static inner class providing String constants for IronMQ-specific headers. Single inner implementation class manages builder state and IronMQ hosted message queue service client configuration.

---

## Phase 42: Files 163-166

### File 163
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JCacheEndpointBuilderFactory.java  
**Total Lines:** 1746

**Type Declarations:** 10 total

1. Line 35: `JCacheEndpointBuilderFactory` (public interface)
2. Line 40: `JCacheEndpointConsumerBuilder` (public interface)
3. Line 337: `AdvancedJCacheEndpointConsumerBuilder` (public interface)
4. Line 681: `JCacheEndpointProducerBuilder` (public interface)
5. Line 919: `AdvancedJCacheEndpointProducerBuilder` (public interface)
6. Line 1159: `JCacheEndpointBuilder` (public interface)
7. Line 1382: `AdvancedJCacheEndpointBuilder` (public interface)
8. Line 1576: `JCacheBuilders` (public interface)
9. Line 1635: `JCacheHeaderNameBuilder` (public static class)
10. Line 1740: `JCacheEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JCache endpoint component enabling integration with JSR107/JCache-compliant cache providers for distributed caching operations, cache entry operations (put/get/remove/clear), and cache event listener registration. Very large builder with distinct consumer and producer variants for bidirectional cache operations. Provides fluent DSL through separate JCacheEndpointConsumerBuilder and JCacheEndpointProducerBuilder interfaces plus unified JCacheEndpointBuilder. Advanced variants provide comprehensive control over cache manager selection, cache name, key/value operations, cache configuration, event listeners, cache statistics access, and JSR107 cache provider configuration. Contains JCacheBuilders interface and JCacheHeaderNameBuilder static inner class providing String constants for JCache-specific headers. Single inner implementation class manages builder state and JCache provider instance and cache configuration.

---

### File 164
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JGroupsEndpointBuilderFactory.java  
**Total Lines:** 479

**Type Declarations:** 10 total

1. Line 35: `JGroupsEndpointBuilderFactory` (public interface)
2. Line 40: `JGroupsEndpointConsumerBuilder` (public interface)
3. Line 100: `AdvancedJGroupsEndpointConsumerBuilder` (public interface)
4. Line 228: `JGroupsEndpointProducerBuilder` (public interface)
5. Line 255: `AdvancedJGroupsEndpointProducerBuilder` (public interface)
6. Line 311: `JGroupsEndpointBuilder` (public interface)
7. Line 339: `AdvancedJGroupsEndpointBuilder` (public interface)
8. Line 349: `JGroupsBuilders` (public interface)
9. Line 408: `JGroupsHeaderNameBuilder` (public static class)
10. Line 473: `JGroupsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JGroups endpoint component enabling integration with JGroups toolkit for reliable cluster-wide messaging, multi-cast communication across cluster nodes, group coordination, and distributed event notifications. Compact builder with distinct consumer and producer variants for bidirectional cluster communication. Provides fluent DSL through separate JGroupsEndpointConsumerBuilder and JGroupsEndpointProducerBuilder interfaces plus unified JGroupsEndpointBuilder. Advanced variants provide fine-grained control over cluster channel name, protocol stack configuration, cluster state access, message ordering, flow control, and JGroups protocol stack setup. Contains JGroupsBuilders interface and JGroupsHeaderNameBuilder static inner class providing String constants for JGroups-specific headers. Single inner implementation class manages builder state and JGroups cluster channel and protocol configuration.

---

### File 165
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JGroupsRaftEndpointBuilderFactory.java  
**Total Lines:** 523

**Type Declarations:** 10 total

1. Line 35: `JGroupsRaftEndpointBuilderFactory` (public interface)
2. Line 40: `JGroupsRaftEndpointConsumerBuilder` (public interface)
3. Line 85: `AdvancedJGroupsRaftEndpointConsumerBuilder` (public interface)
4. Line 213: `JGroupsRaftEndpointProducerBuilder` (public interface)
5. Line 225: `AdvancedJGroupsRaftEndpointProducerBuilder` (public interface)
6. Line 281: `JGroupsRaftEndpointBuilder` (public interface)
7. Line 294: `AdvancedJGroupsRaftEndpointBuilder` (public interface)
8. Line 304: `JGroupsRaftBuilders` (public interface)
9. Line 363: `JGroupsRaftHeaderNameBuilder` (public static class)
10. Line 517: `JGroupsRaftEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JGroups-raft endpoint component enabling integration with JGroups Raft consensus protocol for leader election, distributed state machine replication, strong consistency guarantees, and cluster-coordinated consensus operations. Compact builder with distinct consumer and producer variants for bidirectional cluster communication with consensus. Provides fluent DSL through separate JGroupsRaftEndpointConsumerBuilder and JGroupsRaftEndpointProducerBuilder interfaces plus unified JGroupsRaftEndpointBuilder. Advanced variants provide fine-grained control over Raft cluster name, state machine configuration, leader/follower role management, log replication, consensus timeout settings, and JGroups Raft protocol setup. Contains JGroupsRaftBuilders interface and JGroupsRaftHeaderNameBuilder static inner class providing String constants for JGroups-raft-specific headers. Single inner implementation class manages builder state and JGroups Raft cluster state machine and protocol configuration.

---

### File 166
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JMXEndpointBuilderFactory.java  
**Total Lines:** 975

**Type Declarations:** 6 total

1. Line 35: `JMXEndpointBuilderFactory` (public interface)
2. Line 40: `JMXEndpointBuilder` (public interface)
3. Line 520: `AdvancedJMXEndpointBuilder` (public interface)
4. Line 887: `JMXBuilders` (public interface)
5. Line 948: `JMXHeaderNameBuilder` (public static class)
6. Line 969: `JMXEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JMX endpoint component enabling integration with Java Management Extensions for receiving JMX notifications from managed beans, monitoring MBean attribute changes, monitoring MBean operations invocation, and application runtime metrics collection. Large builder supporting JMX notification consumption. Provides fluent DSL through JMXEndpointBuilder interface with advanced variant for fine-grained control over MBean server selection, object name patterns, notification filtering, attribute change detection, operation monitoring, and JMX connection configuration. Contains JMXBuilders interface and JMXHeaderNameBuilder static inner class providing String constants for JMX-specific headers. Single inner implementation class manages builder state and JMX connection and MBean notification listener configuration.

---

## Phase 43: Files 167-170

### File 167
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JcrEndpointBuilderFactory.java  
**Total Lines:** 1102

**Type Declarations:** 10 total

1. Line 35: `JcrEndpointBuilderFactory` (public interface)
2. Line 40: `JcrEndpointConsumerBuilder` (public interface)
3. Line 288: `AdvancedJcrEndpointConsumerBuilder` (public interface)
4. Line 416: `JcrEndpointProducerBuilder` (public interface)
5. Line 665: `AdvancedJcrEndpointProducerBuilder` (public interface)
6. Line 721: `JcrEndpointBuilder` (public interface)
7. Line 971: `AdvancedJcrEndpointBuilder` (public interface)
8. Line 981: `JcrBuilders` (public interface)
9. Line 1048: `JcrHeaderNameBuilder` (public static class)
10. Line 1096: `JcrEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JCR endpoint component enabling integration with Java Content Repository for hierarchical content storage, tree-based document management, versioning and node operations, and content query and traversal. Very large builder with distinct consumer and producer variants for bidirectional repository operations. Provides fluent DSL through separate JcrEndpointConsumerBuilder and JcrEndpointProducerBuilder interfaces plus unified JcrEndpointBuilder. Advanced variants provide comprehensive control over repository connection, workspace selection, node deep copying, recursive traversal, property access, search queries, and JCR repository configuration. Contains JcrBuilders interface and JcrHeaderNameBuilder static inner class providing String constants for JCR-specific headers. Single inner implementation class manages builder state and JCR repository connection and content node configuration.

---

### File 168
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JdbcEndpointBuilderFactory.java  
**Total Lines:** 725

**Type Declarations:** 6 total

1. Line 35: `JdbcEndpointBuilderFactory` (public interface)
2. Line 40: `JdbcEndpointBuilder` (public interface)
3. Line 384: `AdvancedJdbcEndpointBuilder` (public interface)
4. Line 545: `JdbcBuilders` (public interface)
5. Line 610: `JdbcHeaderNameBuilder` (public static class)
6. Line 719: `JdbcEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JDBC endpoint component enabling integration with relational databases via JDBC for SQL query execution, result set processing, parameterized statement handling, and batch database operations. Large builder supporting JDBC database interactions. Provides fluent DSL through JdbcEndpointBuilder interface with advanced variant for fine-grained control over SQL statement configuration, parameter naming strategies, result set mapping, datasource selection, batch size, and JDBC connection pooling. Contains JdbcBuilders interface and JdbcHeaderNameBuilder static inner class providing String constants for JDBC-specific headers. Single inner implementation class manages builder state and JDBC datasource and SQL statement configuration.

---

### File 169
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JettyHttp12EndpointBuilderFactory.java  
**Total Lines:** 1462

**Type Declarations:** 6 total

1. Line 35: `JettyHttp12EndpointBuilderFactory` (public interface)
2. Line 40: `JettyHttp12EndpointBuilder` (public interface)
3. Line 684: `AdvancedJettyHttp12EndpointBuilder` (public interface)
4. Line 1363: `JettyHttp12Builders` (public interface)
5. Line 1422: `JettyHttp12HeaderNameBuilder` (public static class)
6. Line 1456: `JettyHttp12EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JettyHttp12 endpoint component exposing HTTP endpoints using embedded Jetty 12 servlet container for REST endpoint hosting, HTTP request routing, request/response handling, and embedded web server functionality. Very large builder supporting HTTP server operations. Provides fluent DSL through JettyHttp12EndpointBuilder interface with advanced variant for fine-grained control over HTTP socket configuration, SSL/TLS settings, request buffering, streaming behavior, session management, filter chains, and Jetty 12 servlet container configuration. Contains JettyHttp12Builders interface and JettyHttp12HeaderNameBuilder static inner class providing String constants for JettyHttp12-specific headers. Single inner implementation class manages builder state and Jetty 12 HTTP connector and servlet configuration.

---

### File 170
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JiraEndpointBuilderFactory.java  
**Total Lines:** 1591

**Type Declarations:** 10 total

1. Line 35: `JiraEndpointBuilderFactory` (public interface)
2. Line 40: `JiraEndpointConsumerBuilder` (public interface)
3. Line 745: `AdvancedJiraEndpointConsumerBuilder` (public interface)
4. Line 909: `JiraEndpointProducerBuilder` (public interface)
5. Line 1059: `AdvancedJiraEndpointProducerBuilder` (public interface)
6. Line 1115: `JiraEndpointBuilder` (public interface)
7. Line 1266: `AdvancedJiraEndpointBuilder` (public interface)
8. Line 1276: `JiraBuilders` (public interface)
9. Line 1347: `JiraHeaderNameBuilder` (public static class)
10. Line 1585: `JiraEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jira endpoint component enabling integration with Atlassian Jira issue tracker for issue search and retrieval, issue creation and updates, comment management, and issue workflow transitions. Very large builder with distinct consumer and producer variants for bidirectional issue tracking operations. Provides fluent DSL through separate JiraEndpointConsumerBuilder and JiraEndpointProducerBuilder interfaces plus unified JiraEndpointBuilder. Advanced variants provide comprehensive control over Jira server connection, project/issue selection, search filters, issue update operations, custom field handling, comment addition, and Jira REST client configuration. Contains JiraBuilders interface and JiraHeaderNameBuilder static inner class providing String constants for Jira-specific headers. Single inner implementation class manages builder state and Jira server authentication and API client configuration.

---

## Phase 44: Files 171-174

### File 171
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JmsEndpointBuilderFactory.java  
**Total Lines:** 7539

**Type Declarations:** 10 total

1. Line 35: `JmsEndpointBuilderFactory` (public interface)
2. Line 40: `JmsEndpointConsumerBuilder` (public interface)
3. Line 865: `AdvancedJmsEndpointConsumerBuilder` (public interface)
4. Line 2782: `JmsEndpointProducerBuilder` (public interface)
5. Line 3601: `AdvancedJmsEndpointProducerBuilder` (public interface)
6. Line 5435: `JmsEndpointBuilder` (public interface)
7. Line 5774: `AdvancedJmsEndpointBuilder` (public interface)
8. Line 7233: `JmsBuilders` (public interface)
9. Line 7304: `JmsHeaderNameBuilder` (public static class)
10. Line 7533: `JmsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JMS endpoint component enabling integration with Java Message Service for asynchronous messaging, message queue operations, request-reply patterns, and message-driven routing. Extremely large builder with distinct consumer and producer variants reflecting JMS's asynchronous two-way messaging model. Provides fluent DSL through separate JmsEndpointConsumerBuilder and JmsEndpointProducerBuilder interfaces plus unified JmsEndpointBuilder. Advanced variants provide comprehensive control over JMS connection factory, destination selection (queue/topic), message selector filtering, correlation ID handling, request-reply timeout, transaction settings, acknowledgment modes, message priority, time-to-live, and JMS provider configuration. Contains JmsBuilders interface and JmsHeaderNameBuilder static inner class providing String constants for JMS-specific headers. Single inner implementation class manages builder state and JMS connection and destination configuration.

---

### File 172
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JoltEndpointBuilderFactory.java  
**Total Lines:** 389

**Type Declarations:** 6 total

1. Line 35: `JoltEndpointBuilderFactory` (public interface)
2. Line 40: `JoltEndpointBuilder` (public interface)
3. Line 222: `AdvancedJoltEndpointBuilder` (public interface)
4. Line 277: `JoltBuilders` (public interface)
5. Line 350: `JoltHeaderNameBuilder` (public static class)
6. Line 383: `JoltEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jolt endpoint component enabling JSON transformation and manipulation through Jolt specification for data format conversion, JSON mapping, JSON filtering, and declarative JSON transformation. Compact builder supporting Jolt JSON operations. Provides fluent DSL through JoltEndpointBuilder interface with advanced variant for fine-grained control over Jolt specification selection, transformation specification loading, input/output JSON format handling, and Jolt transformer configuration. Contains JoltBuilders interface and JoltHeaderNameBuilder static inner class providing String constants for Jolt-specific headers. Single inner implementation class manages builder state and Jolt transformer and JSON specification configuration.

---

### File 173
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JooqEndpointBuilderFactory.java  
**Total Lines:** 1008

**Type Declarations:** 9 total

1. Line 35: `JooqEndpointBuilderFactory` (public interface)
2. Line 40: `JooqEndpointConsumerBuilder` (public interface)
3. Line 599: `AdvancedJooqEndpointConsumerBuilder` (public interface)
4. Line 763: `JooqEndpointProducerBuilder` (public interface)
5. Line 850: `AdvancedJooqEndpointProducerBuilder` (public interface)
6. Line 906: `JooqEndpointBuilder` (public interface)
7. Line 948: `AdvancedJooqEndpointBuilder` (public interface)
8. Line 958: `JooqBuilders` (public interface)
9. Line 1002: `JooqEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jooq endpoint component enabling database queries and operations through jOOQ fluent API for type-safe SQL construction, database query execution, and relational database access. Large builder with distinct consumer and producer variants for bidirectional database operations. Provides fluent DSL through separate JooqEndpointConsumerBuilder and JooqEndpointProducerBuilder interfaces plus unified JooqEndpointBuilder. Advanced variants provide comprehensive control over jOOQ configuration, SQL query building, datasource selection, query result processing, batch execution, transaction handling, and database connection management. Contains JooqBuilders interface without a static header name builder class. Single inner implementation class manages builder state and jOOQ context and database configuration.

---

### File 174
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JpaEndpointBuilderFactory.java  
**Total Lines:** 2320

**Type Declarations:** 10 total

1. Line 36: `JpaEndpointBuilderFactory` (public interface)
2. Line 41: `JpaEndpointConsumerBuilder` (public interface)
3. Line 999: `AdvancedJpaEndpointConsumerBuilder` (public interface)
4. Line 1287: `JpaEndpointProducerBuilder` (public interface)
5. Line 1737: `AdvancedJpaEndpointProducerBuilder` (public interface)
6. Line 1902: `JpaEndpointBuilder` (public interface)
7. Line 2110: `AdvancedJpaEndpointBuilder` (public interface)
8. Line 2193: `JpaBuilders` (public interface)
9. Line 2255: `JpaHeaderNameBuilder` (public static class)
10. Line 2314: `JpaEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JPA endpoint component enabling Object-Relational Mapping through JPA for entity persistence, database record operations, entity lifecycle management, and relational database integration with ORM frameworks. Very large builder with distinct consumer and producer variants for bidirectional entity operations. Provides fluent DSL through separate JpaEndpointConsumerBuilder and JpaEndpointProducerBuilder interfaces plus unified JpaEndpointBuilder. Advanced variants provide comprehensive control over JPA entity manager factory, entity class selection, persistence unit configuration, query parameter binding, named query execution, entity transaction handling, cascade options, and ORM framework-specific configuration. Contains JpaBuilders interface and JpaHeaderNameBuilder static inner class providing String constants for JPA-specific headers. Single inner implementation class manages builder state and JPA entity manager and persistence context configuration.

---

## Phase 45: Files 175-178

### File 175
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JsltEndpointBuilderFactory.java  
**Total Lines:** 417

**Type Declarations:** 6 total

1. Line 35: `JsltEndpointBuilderFactory` (public interface)
2. Line 40: `JsltEndpointBuilder` (public interface)
3. Line 250: `AdvancedJsltEndpointBuilder` (public interface)
4. Line 305: `JsltBuilders` (public interface)
5. Line 378: `JsltHeaderNameBuilder` (public static class)
6. Line 411: `JsltEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jslt endpoint component enabling JSON transformation through Jslt template language for JSON data format conversion, template-based JSON transformation, and declarative JSON output generation. Compact builder supporting Jslt JSON transformation operations. Provides fluent DSL through JsltEndpointBuilder interface with advanced variant for fine-grained control over Jslt template specification, template resource loading, transformation output format, and Jslt transformer configuration. Contains JsltBuilders interface and JsltHeaderNameBuilder static inner class providing String constants for Jslt-specific headers. Single inner implementation class manages builder state and Jslt transformer and template configuration.

---

### File 176
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JsonPatchEndpointBuilderFactory.java  
**Total Lines:** 316

**Type Declarations:** 6 total

1. Line 35: `JsonPatchEndpointBuilderFactory` (public interface)
2. Line 40: `JsonPatchEndpointBuilder` (public interface)
3. Line 158: `AdvancedJsonPatchEndpointBuilder` (public interface)
4. Line 213: `JsonPatchBuilders` (public interface)
5. Line 289: `JsonPatchHeaderNameBuilder` (public static class)
6. Line 310: `JsonPatchEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JsonPatch endpoint component enabling JSON document updates through JSON Patch RFC 6902 specification for declarative JSON document modification, JSON patching operations, and incremental JSON document transformation. Compact builder supporting JSON Patch operations. Provides fluent DSL through JsonPatchEndpointBuilder interface with advanced variant for fine-grained control over patch specification, input/output JSON handling, patch operation application, and JSON Patch processor configuration. Contains JsonPatchBuilders interface and JsonPatchHeaderNameBuilder static inner class providing String constants for JsonPatch-specific headers. Single inner implementation class manages builder state and JSON Patch processor configuration.

---

### File 177
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JsonValidatorEndpointBuilderFactory.java  
**Total Lines:** 441

**Type Declarations:** 5 total

1. Line 35: `JsonValidatorEndpointBuilderFactory` (public interface)
2. Line 40: `JsonValidatorEndpointBuilder` (public interface)
3. Line 194: `AdvancedJsonValidatorEndpointBuilder` (public interface)
4. Line 377: `JsonValidatorBuilders` (public interface)
5. Line 435: `JsonValidatorEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for JsonValidator endpoint component enabling JSON document validation against schema specifications for JSON schema compliance checking, validation error reporting, and message validation in JSON format. Compact builder supporting JSON validation operations. Provides fluent DSL through JsonValidatorEndpointBuilder interface with advanced variant for fine-grained control over JSON schema specification, schema resource loading, validation error handling, and JSON schema validator configuration. Contains JsonValidatorBuilders interface without a static header name builder class. Single inner implementation class manages builder state and JSON schema validator configuration.

---

### File 178
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JsonataEndpointBuilderFactory.java  
**Total Lines:** 405

**Type Declarations:** 5 total

1. Line 35: `JsonataEndpointBuilderFactory` (public interface)
2. Line 40: `JsonataEndpointBuilder` (public interface)
3. Line 252: `AdvancedJsonataEndpointBuilder` (public interface)
4. Line 341: `JsonataBuilders` (public interface)
5. Line 399: `JsonataEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jsonata endpoint component enabling JSON transformation through Jsonata functional query and transformation language for JSON data processing, advanced JSON querying, and complex JSON document transformation. Compact builder supporting Jsonata JSON transformation operations. Provides fluent DSL through JsonataEndpointBuilder interface with advanced variant for fine-grained control over Jsonata expression specification, transformation result handling, and Jsonata engine configuration. Contains JsonataBuilders interface without a static header name builder class. Single inner implementation class manages builder state and Jsonata transformer configuration.

---

## Phase 46: Files 179-182

### File 179
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Jt400EndpointBuilderFactory.java  
**Total Lines:** 1768

**Type Declarations:** 10 total

1. Line 36: `Jt400EndpointBuilderFactory` (public interface)
2. Line 41: `Jt400EndpointConsumerBuilder` (public interface)
3. Line 877: `AdvancedJt400EndpointConsumerBuilder` (public interface)
4. Line 1041: `Jt400EndpointProducerBuilder` (public interface)
5. Line 1293: `AdvancedJt400EndpointProducerBuilder` (public interface)
6. Line 1349: `Jt400EndpointBuilder` (public interface)
7. Line 1528: `AdvancedJt400EndpointBuilder` (public interface)
8. Line 1538: `Jt400Builders` (public interface)
9. Line 1635: `Jt400HeaderNameBuilder` (public static class)
10. Line 1762: `Jt400EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jt400 endpoint component enabling integration with IBM System i (formerly i5/OS) servers for remote procedure calls, data queue operations, program invocation, and system object access. Very large builder with distinct consumer and producer variants for bidirectional IBM System i operations. Provides fluent DSL through separate Jt400EndpointConsumerBuilder and Jt400EndpointProducerBuilder interfaces plus unified Jt400EndpointBuilder. Advanced variants provide comprehensive control over IBM System i server connection, library/program selection, program call parameters, data queue operations, message queue access, and IBM Toolbox for Java configuration. Contains Jt400Builders interface and Jt400HeaderNameBuilder static inner class providing String constants for Jt400-specific headers. Single inner implementation class manages builder state and IBM System i connection and program/queue configuration.

---

### File 180
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/JteEndpointBuilderFactory.java  
**Total Lines:** 338

**Type Declarations:** 6 total

1. Line 35: `JteEndpointBuilderFactory` (public interface)
2. Line 40: `JteEndpointBuilder` (public interface)
3. Line 158: `AdvancedJteEndpointBuilder` (public interface)
4. Line 213: `JteBuilders` (public interface)
5. Line 286: `JteHeaderNameBuilder` (public static class)
6. Line 332: `JteEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Jte endpoint component enabling template rendering through Jte template engine for server-side HTML generation, template-based content generation, and dynamic page rendering. Compact builder supporting Jte template rendering operations. Provides fluent DSL through JteEndpointBuilder interface with advanced variant for fine-grained control over template resource location, template name, template parameter passing, and Jte template engine configuration. Contains JteBuilders interface and JteHeaderNameBuilder static inner class providing String constants for Jte-specific headers. Single inner implementation class manages builder state and Jte template engine configuration.

---

### File 181
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KMS2EndpointBuilderFactory.java  
**Total Lines:** 723

**Type Declarations:** 6 total

1. Line 35: `KMS2EndpointBuilderFactory` (public interface)
2. Line 40: `KMS2EndpointBuilder` (public interface)
3. Line 443: `AdvancedKMS2EndpointBuilder` (public interface)
4. Line 528: `KMS2Builders` (public interface)
5. Line 587: `KMS2HeaderNameBuilder` (public static class)
6. Line 717: `KMS2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for KMS2 endpoint component enabling integration with AWS Key Management Service 2.0 for encryption key management, data encryption/decryption operations, and secure cryptographic operations. Large builder supporting AWS KMS2 operations. Provides fluent DSL through KMS2EndpointBuilder interface with advanced variant for fine-grained control over AWS KMS2 key selection, encryption context, encryption algorithm, key rotation, and AWS SDK client configuration. Contains KMS2Builders interface and KMS2HeaderNameBuilder static inner class providing String constants for KMS2-specific headers. Single inner implementation class manages builder state and AWS KMS2 client configuration.

---

### File 182
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KServeEndpointBuilderFactory.java  
**Total Lines:** 286

**Type Declarations:** 6 total

1. Line 36: `KServeEndpointBuilderFactory` (public interface)
2. Line 41: `KServeEndpointBuilder` (public interface)
3. Line 126: `AdvancedKServeEndpointBuilder` (public interface)
4. Line 181: `KServeBuilders` (public interface)
5. Line 247: `KServeHeaderNameBuilder` (public static class)
6. Line 280: `KServeEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for KServe endpoint component enabling integration with KServe inference model serving platform for machine learning model invocation, model serving requests, and ML model prediction. Compact builder supporting KServe inference operations. Provides fluent DSL through KServeEndpointBuilder interface with advanced variant for fine-grained control over model selection, inference request handling, model version specification, and KServe service endpoint configuration. Contains KServeBuilders interface and KServeHeaderNameBuilder static inner class providing String constants for KServe-specific headers. Single inner implementation class manages builder state and KServe inference service client configuration.

---

## Phase 47: Files 183-186

### File 183
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KafkaEndpointBuilderFactory.java  
**Total Lines:** 5909

**Type Declarations:** 10 total

1. Line 35: `KafkaEndpointBuilderFactory` (public interface)
2. Line 40: `KafkaEndpointConsumerBuilder` (public interface)
3. Line 2089: `AdvancedKafkaEndpointConsumerBuilder` (public interface)
4. Line 2354: `KafkaEndpointProducerBuilder` (public interface)
5. Line 4488: `AdvancedKafkaEndpointProducerBuilder` (public interface)
6. Line 4650: `KafkaEndpointBuilder` (public interface)
7. Line 5583: `AdvancedKafkaEndpointBuilder` (public interface)
8. Line 5663: `KafkaBuilders` (public interface)
9. Line 5726: `KafkaHeaderNameBuilder` (public static class)
10. Line 5903: `KafkaEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kafka endpoint component enabling integration with Apache Kafka message broker for event streaming, topic-based publish-subscribe messaging, and distributed stream processing. Extremely large builder with distinct consumer and producer variants providing comprehensive support for complex Kafka operations. Provides fluent DSL through separate KafkaEndpointConsumerBuilder and KafkaEndpointProducerBuilder interfaces plus unified KafkaEndpointBuilder. Advanced variants provide fine-grained control over broker connection, topic/partition selection, consumer groups, offsets, rebalancing, serialization, compression, security (SASL/SSL), and extensive Kafka client configuration. Contains KafkaBuilders interface and KafkaHeaderNameBuilder static inner class providing String constants for Kafka-specific headers (e.g., KafkaConstants.KAFKA_RECORD_META, KafkaConstants.KAFKA_PARTITION_KEY). Single inner implementation class manages builder state and comprehensive Kafka producer/consumer configuration.

---

### File 184
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KameletEndpointBuilderFactory.java  
**Total Lines:** 607

**Type Declarations:** 9 total

1. Line 35: `KameletEndpointBuilderFactory` (public interface)
2. Line 40: `KameletEndpointConsumerBuilder` (public interface)
3. Line 51: `AdvancedKameletEndpointConsumerBuilder` (public interface)
4. Line 240: `KameletEndpointProducerBuilder` (public interface)
5. Line 252: `AdvancedKameletEndpointProducerBuilder` (public interface)
6. Line 465: `KameletEndpointBuilder` (public interface)
7. Line 478: `AdvancedKameletEndpointBuilder` (public interface)
8. Line 549: `KameletBuilders` (public interface)
9. Line 601: `KameletEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kamelet endpoint component enabling integration with reusable Camel route templates encapsulating parameterized routing logic for composable route fragments, route template instantiation, and modular route reuse. Compact builder with consumer and producer variants. Provides fluent DSL through separate KameletEndpointConsumerBuilder and KameletEndpointProducerBuilder interfaces plus unified KameletEndpointBuilder. Advanced variants provide control over template selection, template property/parameter mapping, invocation mode, and route template binding. Contains KameletBuilders interface without static header name builder class. Single inner implementation class manages builder state and Kamelet route template binding and parameter mapping configuration.

---

### File 185
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KeyVaultEndpointBuilderFactory.java  
**Total Lines:** 353

**Type Declarations:** 6 total

1. Line 35: `KeyVaultEndpointBuilderFactory` (public interface)
2. Line 40: `KeyVaultEndpointBuilder` (public interface)
3. Line 186: `AdvancedKeyVaultEndpointBuilder` (public interface)
4. Line 241: `KeyVaultBuilders` (public interface)
5. Line 300: `KeyVaultHeaderNameBuilder` (public static class)
6. Line 347: `KeyVaultEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for KeyVault endpoint component enabling integration with Azure Key Vault for secure secret/certificate management, encrypted key storage, and secrets retrieval. Compact builder supporting Azure Key Vault operations. Provides fluent DSL through KeyVaultEndpointBuilder interface with advanced variant for fine-grained control over vault selection, secret name/version, Azure authentication, managed identity configuration, and Azure SDK client setup. Contains KeyVaultBuilders interface and KeyVaultHeaderNameBuilder static inner class providing String constants for KeyVault-specific headers. Single inner implementation class manages builder state and Azure Key Vault client configuration.

---

### File 186
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KeycloakEndpointBuilderFactory.java  
**Total Lines:** 3310

**Type Declarations:** 10 total

1. Line 35: `KeycloakEndpointBuilderFactory` (public interface)
2. Line 40: `KeycloakEndpointConsumerBuilder` (public interface)
3. Line 1095: `AdvancedKeycloakEndpointConsumerBuilder` (public interface)
4. Line 1259: `KeycloakEndpointProducerBuilder` (public interface)
5. Line 1826: `AdvancedKeycloakEndpointProducerBuilder` (public interface)
6. Line 1882: `KeycloakEndpointBuilder` (public interface)
7. Line 2450: `AdvancedKeycloakEndpointBuilder` (public interface)
8. Line 2460: `KeycloakBuilders` (public interface)
9. Line 2519: `KeycloakHeaderNameBuilder` (public static class)
10. Line 3304: `KeycloakEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Keycloak endpoint component enabling integration with Keycloak identity and access management (IAM) platform for user authentication, role-based authorization, OAuth2/OpenID Connect flows, and token management. Large builder with distinct consumer and producer variants for bidirectional IAM operations. Provides fluent DSL through separate KeycloakEndpointConsumerBuilder and KeycloakEndpointProducerBuilder interfaces plus unified KeycloakEndpointBuilder. Advanced variants provide comprehensive control over Keycloak server selection, realm configuration, client credentials, token endpoint specification, user/role management operations, audience/scope specification, and advanced OAuth2/OIDC client configuration. Contains KeycloakBuilders interface and KeycloakHeaderNameBuilder static inner class providing String constants for Keycloak-specific headers. Single inner implementation class manages builder state and Keycloak IAM client configuration.

---

## Phase 48: Files 187-190

### File 187
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KeystoneEndpointBuilderFactory.java  
**Total Lines:** 424

**Type Declarations:** 6 total

1. Line 36: `KeystoneEndpointBuilderFactory` (public interface)
2. Line 41: `KeystoneEndpointBuilder` (public interface)
3. Line 172: `AdvancedKeystoneEndpointBuilder` (public interface)
4. Line 227: `KeystoneBuilders` (public interface)
5. Line 289: `KeystoneHeaderNameBuilder` (public static class)
6. Line 418: `KeystoneEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Keystone endpoint component enabling integration with OpenStack Identity Service (Keystone) for OpenStack authentication, token generation, and service endpoint discovery. Compact builder supporting OpenStack Keystone operations. Provides fluent DSL through KeystoneEndpointBuilder interface with advanced variant for fine-grained control over Keystone server selection, authentication credentials, tenant/project specification, token generation, and OpenStack client configuration. Contains KeystoneBuilders interface and KeystoneHeaderNameBuilder static inner class providing String constants for Keystone-specific headers. Single inner implementation class manages builder state and OpenStack Keystone authentication client configuration.

---

### File 188
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Kinesis2EndpointBuilderFactory.java  
**Total Lines:** 2924

**Type Declarations:** 10 total

1. Line 35: `Kinesis2EndpointBuilderFactory` (public interface)
2. Line 40: `Kinesis2EndpointConsumerBuilder` (public interface)
3. Line 1047: `AdvancedKinesis2EndpointConsumerBuilder` (public interface)
4. Line 1475: `Kinesis2EndpointProducerBuilder` (public interface)
5. Line 1846: `AdvancedKinesis2EndpointProducerBuilder` (public interface)
6. Line 2136: `Kinesis2EndpointBuilder` (public interface)
7. Line 2508: `AdvancedKinesis2EndpointBuilder` (public interface)
8. Line 2752: `Kinesis2Builders` (public interface)
9. Line 2811: `Kinesis2HeaderNameBuilder` (public static class)
10. Line 2918: `Kinesis2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kinesis2 endpoint component enabling integration with AWS Kinesis 2.0 data stream service for real-time data streaming, stream-based data processing, and event collection from multiple producers. Large builder with distinct consumer and producer variants for bidirectional Kinesis operations. Provides fluent DSL through separate Kinesis2EndpointConsumerBuilder and Kinesis2EndpointProducerBuilder interfaces plus unified Kinesis2EndpointBuilder. Advanced variants provide comprehensive control over stream name/selection, shard/partition handling, sequence number/iterator specification, consumer group coordination, record batching, lambda expressions, and AWS SDK client configuration. Contains Kinesis2Builders interface and Kinesis2HeaderNameBuilder static inner class providing String constants for Kinesis2-specific headers. Single inner implementation class manages builder state and Kinesis2 stream producer/consumer configuration.

---

### File 189
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KinesisFirehose2EndpointBuilderFactory.java  
**Total Lines:** 700

**Type Declarations:** 6 total

1. Line 35: `KinesisFirehose2EndpointBuilderFactory` (public interface)
2. Line 40: `KinesisFirehose2EndpointBuilder` (public interface)
3. Line 448: `AdvancedKinesisFirehose2EndpointBuilder` (public interface)
4. Line 537: `KinesisFirehose2Builders` (public interface)
5. Line 596: `KinesisFirehose2HeaderNameBuilder` (public static class)
6. Line 694: `KinesisFirehose2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for KinesisFirehose2 endpoint component enabling integration with AWS Kinesis Data Firehose 2.0 delivery service for real-time data delivery to S3, Redshift, Elasticsearch, Splunk, and HTTP endpoints. Compact producer-oriented builder supporting AWS Kinesis Firehose data delivery. Provides fluent DSL through KinesisFirehose2EndpointBuilder interface with advanced variant for fine-grained control over delivery stream name/selection, record batching, buffer size/timeout, data transformation, and AWS SDK client configuration. Contains KinesisFirehose2Builders interface and KinesisFirehose2HeaderNameBuilder static inner class providing String constants for KinesisFirehose2-specific headers. Single inner implementation class manages builder state and Kinesis Firehose delivery stream configuration.

---

### File 190
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KnativeEndpointBuilderFactory.java  
**Total Lines:** 1078

**Type Declarations:** 9 total

1. Line 35: `KnativeEndpointBuilderFactory` (public interface)
2. Line 40: `KnativeEndpointConsumerBuilder` (public interface)
3. Line 276: `AdvancedKnativeEndpointConsumerBuilder` (public interface)
4. Line 477: `KnativeEndpointProducerBuilder` (public interface)
5. Line 672: `AdvancedKnativeEndpointProducerBuilder` (public interface)
6. Line 770: `KnativeEndpointBuilder` (public interface)
7. Line 966: `AdvancedKnativeEndpointBuilder` (public interface)
8. Line 1018: `KnativeBuilders` (public interface)
9. Line 1072: `KnativeEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Knative endpoint component enabling integration with Knative serverless computing platform for event-driven microservices, Knative service invocation, and serverless function orchestration. Large builder with distinct consumer and producer variants for bidirectional Knative operations. Provides fluent DSL through separate KnativeEndpointConsumerBuilder and KnativeEndpointProducerBuilder interfaces plus unified KnativeEndpointBuilder. Advanced variants provide comprehensive control over Knative service/trigger selection, event type specification, header/metadata mapping, cloud event format handling, and Knative platform client configuration. Contains KnativeBuilders interface without a static header name builder class. Single inner implementation class manages builder state and Knative service/event configuration.

---

## Phase 49: Files 191-194

### File 191
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesConfigMapsEndpointBuilderFactory.java  
**Total Lines:** 1565

**Type Declarations:** 10 total

1. Line 36: `KubernetesConfigMapsEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesConfigMapsEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesConfigMapsEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesConfigMapsEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesConfigMapsEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesConfigMapsEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesConfigMapsEndpointBuilder` (public interface)
8. Line 1378: `KubernetesConfigMapsBuilders` (public interface)
9. Line 1452: `KubernetesConfigMapsHeaderNameBuilder` (public static class)
10. Line 1559: `KubernetesConfigMapsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes ConfigMaps endpoint component enabling integration with Kubernetes configuration management for ConfigMap resource CRUD operations, configuration state monitoring, and declarative configuration distribution. Large builder with distinct consumer and producer variants for bidirectional Kubernetes ConfigMap operations. Provides fluent DSL through separate KubernetesConfigMapsEndpointConsumerBuilder and KubernetesConfigMapsEndpointProducerBuilder interfaces plus unified KubernetesConfigMapsEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, resource name/label selection, watch/list operations, RBAC authorization, and Kubernetes client configuration. Contains KubernetesConfigMapsBuilders interface and KubernetesConfigMapsHeaderNameBuilder static inner class providing String constants for Kubernetes ConfigMap-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 192
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesCronJobEndpointBuilderFactory.java  
**Total Lines:** 585

**Type Declarations:** 6 total

1. Line 35: `KubernetesCronJobEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesCronJobEndpointBuilder` (public interface)
3. Line 352: `AdvancedKubernetesCronJobEndpointBuilder` (public interface)
4. Line 438: `KubernetesCronJobBuilders` (public interface)
5. Line 509: `KubernetesCronJobHeaderNameBuilder` (public static class)
6. Line 579: `KubernetesCronJobEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes CronJob endpoint component enabling integration with Kubernetes job scheduling for CronJob resource management, scheduled job creation, and time-based workload orchestration. Compact producer-oriented builder supporting Kubernetes CronJob operations. Provides fluent DSL through KubernetesCronJobEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, CronJob name/resource specification, schedule definition, and Kubernetes client configuration. Contains KubernetesCronJobBuilders interface and KubernetesCronJobHeaderNameBuilder static inner class providing String constants for Kubernetes CronJob-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 193
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesCustomResourcesEndpointBuilderFactory.java  
**Total Lines:** 1624

**Type Declarations:** 10 total

1. Line 36: `KubernetesCustomResourcesEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesCustomResourcesEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesCustomResourcesEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesCustomResourcesEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesCustomResourcesEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesCustomResourcesEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesCustomResourcesEndpointBuilder` (public interface)
8. Line 1378: `KubernetesCustomResourcesBuilders` (public interface)
9. Line 1452: `KubernetesCustomResourcesHeaderNameBuilder` (public static class)
10. Line 1618: `KubernetesCustomResourcesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes CustomResources endpoint component enabling integration with Kubernetes custom resource definitions (CRDs) for custom resource CRUD operations, custom resource state observation, and extensible Kubernetes API support. Large builder with distinct consumer and producer variants for bidirectional custom resource operations. Provides fluent DSL through separate KubernetesCustomResourcesEndpointConsumerBuilder and KubernetesCustomResourcesEndpointProducerBuilder interfaces plus unified KubernetesCustomResourcesEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, CRD/resource group/version/kind specification, namespace selection, resource name/label selection, watch/list operations, and Kubernetes client configuration. Contains KubernetesCustomResourcesBuilders interface and KubernetesCustomResourcesHeaderNameBuilder static inner class providing String constants for custom resource-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 194
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesDeploymentsEndpointBuilderFactory.java  
**Total Lines:** 1578

**Type Declarations:** 10 total

1. Line 36: `KubernetesDeploymentsEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesDeploymentsEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesDeploymentsEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesDeploymentsEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesDeploymentsEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesDeploymentsEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesDeploymentsEndpointBuilder` (public interface)
8. Line 1378: `KubernetesDeploymentsBuilders` (public interface)
9. Line 1452: `KubernetesDeploymentsHeaderNameBuilder` (public static class)
10. Line 1572: `KubernetesDeploymentsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Deployments endpoint component enabling integration with Kubernetes deployment resource for application deployment management, rolling updates, and declarative container orchestration. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Deployment operations. Provides fluent DSL through separate KubernetesDeploymentsEndpointConsumerBuilder and KubernetesDeploymentsEndpointProducerBuilder interfaces plus unified KubernetesDeploymentsEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, deployment name/label selection, watch/list operations, replica scaling, rolling update control, and Kubernetes client configuration. Contains KubernetesDeploymentsBuilders interface and KubernetesDeploymentsHeaderNameBuilder static inner class providing String constants for Kubernetes Deployment-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

## Phase 50: Files 195-198

### File 195
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesEventsEndpointBuilderFactory.java  
**Total Lines:** 1641

**Type Declarations:** 10 total

1. Line 35: `KubernetesEventsEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesEventsEndpointConsumerBuilder` (public interface)
3. Line 479: `AdvancedKubernetesEventsEndpointConsumerBuilder` (public interface)
4. Line 638: `KubernetesEventsEndpointProducerBuilder` (public interface)
5. Line 950: `AdvancedKubernetesEventsEndpointProducerBuilder` (public interface)
6. Line 1037: `KubernetesEventsEndpointBuilder` (public interface)
7. Line 1336: `AdvancedKubernetesEventsEndpointBuilder` (public interface)
8. Line 1377: `KubernetesEventsBuilders` (public interface)
9. Line 1451: `KubernetesEventsHeaderNameBuilder` (public static class)
10. Line 1635: `KubernetesEventsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Events endpoint component enabling integration with Kubernetes event system for event stream monitoring, cluster event observation, and Kubernetes resource event tracking. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Event operations. Provides fluent DSL through separate KubernetesEventsEndpointConsumerBuilder and KubernetesEventsEndpointProducerBuilder interfaces plus unified KubernetesEventsEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, event source/involved object selection, watch/list operations, event filtering, and Kubernetes client configuration. Contains KubernetesEventsBuilders interface and KubernetesEventsHeaderNameBuilder static inner class providing String constants for Kubernetes Event-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 196
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesHPAEndpointBuilderFactory.java  
**Total Lines:** 1553

**Type Declarations:** 10 total

1. Line 36: `KubernetesHPAEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesHPAEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesHPAEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesHPAEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesHPAEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesHPAEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesHPAEndpointBuilder` (public interface)
8. Line 1378: `KubernetesHPABuilders` (public interface)
9. Line 1452: `KubernetesHPAHeaderNameBuilder` (public static class)
10. Line 1547: `KubernetesHPAEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes HPA endpoint component enabling integration with Kubernetes Horizontal Pod Autoscaler for automatic pod scaling management, scaling policy control, and dynamic workload capacity adjustment. Large builder with distinct consumer and producer variants for bidirectional HPA resource operations. Provides fluent DSL through separate KubernetesHPAEndpointConsumerBuilder and KubernetesHPAEndpointProducerBuilder interfaces plus unified KubernetesHPAEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, HPA resource name/label selection, watch/list operations, min/max replica specification, and Kubernetes client configuration. Contains KubernetesHPABuilders interface and KubernetesHPAHeaderNameBuilder static inner class providing String constants for Kubernetes HPA-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 197
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesJobEndpointBuilderFactory.java  
**Total Lines:** 597

**Type Declarations:** 6 total

1. Line 35: `KubernetesJobEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesJobEndpointBuilder` (public interface)
3. Line 352: `AdvancedKubernetesJobEndpointBuilder` (public interface)
4. Line 438: `KubernetesJobBuilders` (public interface)
5. Line 509: `KubernetesJobHeaderNameBuilder` (public static class)
6. Line 591: `KubernetesJobEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Job endpoint component enabling integration with Kubernetes batch job resource for one-time job execution management, job completion tracking, and batch workload orchestration. Compact producer-oriented builder supporting Kubernetes Job operations. Provides fluent DSL through KubernetesJobEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, job name/resource specification, job execution parameters, and Kubernetes client configuration. Contains KubernetesJobBuilders interface and KubernetesJobHeaderNameBuilder static inner class providing String constants for Kubernetes Job-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 198
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesNamespacesEndpointBuilderFactory.java  
**Total Lines:** 1541

**Type Declarations:** 10 total

1. Line 36: `KubernetesNamespacesEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesNamespacesEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesNamespacesEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesNamespacesEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesNamespacesEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesNamespacesEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesNamespacesEndpointBuilder` (public interface)
8. Line 1378: `KubernetesNamespacesBuilders` (public interface)
9. Line 1452: `KubernetesNamespacesHeaderNameBuilder` (public static class)
10. Line 1535: `KubernetesNamespacesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Namespaces endpoint component enabling integration with Kubernetes cluster namespace resource for namespace management, resource isolation, and namespace-scoped resource administration. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Namespace operations. Provides fluent DSL through separate KubernetesNamespacesEndpointConsumerBuilder and KubernetesNamespacesEndpointProducerBuilder interfaces plus unified KubernetesNamespacesEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace name/label selection, watch/list operations, resource quota and network policy management, and Kubernetes client configuration. Contains KubernetesNamespacesBuilders interface and KubernetesNamespacesHeaderNameBuilder static inner class providing String constants for Kubernetes Namespace-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

## Phase 51: Files 199-202

### File 199
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesNodesEndpointBuilderFactory.java  
**Total Lines:** 1540

**Type Declarations:** 10 total

1. Line 35: `KubernetesNodesEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesNodesEndpointConsumerBuilder` (public interface)
3. Line 479: `AdvancedKubernetesNodesEndpointConsumerBuilder` (public interface)
4. Line 638: `KubernetesNodesEndpointProducerBuilder` (public interface)
5. Line 950: `AdvancedKubernetesNodesEndpointProducerBuilder` (public interface)
6. Line 1037: `KubernetesNodesEndpointBuilder` (public interface)
7. Line 1336: `AdvancedKubernetesNodesEndpointBuilder` (public interface)
8. Line 1377: `KubernetesNodesBuilders` (public interface)
9. Line 1451: `KubernetesNodesHeaderNameBuilder` (public static class)
10. Line 1534: `KubernetesNodesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Nodes endpoint component enabling integration with Kubernetes cluster node resource for node management, worker node monitoring, and compute capacity administration. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Node operations. Provides fluent DSL through separate KubernetesNodesEndpointConsumerBuilder and KubernetesNodesEndpointProducerBuilder interfaces plus unified KubernetesNodesEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, node name/label selection, watch/list operations, node condition/status monitoring, capacity/allocatable resource inspection, and Kubernetes client configuration. Contains KubernetesNodesBuilders interface and KubernetesNodesHeaderNameBuilder static inner class providing String constants for Kubernetes Node-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 200
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesPersistentVolumesClaimsEndpointBuilderFactory.java  
**Total Lines:** 592

**Type Declarations:** 6 total

1. Line 36: `KubernetesPersistentVolumesClaimsEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesPersistentVolumesClaimsEndpointBuilder` (public interface)
3. Line 353: `AdvancedKubernetesPersistentVolumesClaimsEndpointBuilder` (public interface)
4. Line 439: `KubernetesPersistentVolumesClaimsBuilders` (public interface)
5. Line 513: `KubernetesPersistentVolumesClaimsHeaderNameBuilder` (public static class)
6. Line 586: `KubernetesPersistentVolumesClaimsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes PersistentVolumeClaims endpoint component enabling integration with Kubernetes persistent volume claim resource for dynamic storage provisioning, volume binding, and stateful workload storage management. Compact producer-oriented builder supporting Kubernetes PVC operations. Provides fluent DSL through KubernetesPersistentVolumesClaimsEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, PVC name/storage class/access mode specification, and storage capacity parameters. Contains KubernetesPersistentVolumesClaimsBuilders interface and KubernetesPersistentVolumesClaimsHeaderNameBuilder static inner class providing String constants for Kubernetes PVC-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 201
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesPersistentVolumesEndpointBuilderFactory.java  
**Total Lines:** 566

**Type Declarations:** 6 total

1. Line 36: `KubernetesPersistentVolumesEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesPersistentVolumesEndpointBuilder` (public interface)
3. Line 353: `AdvancedKubernetesPersistentVolumesEndpointBuilder` (public interface)
4. Line 439: `KubernetesPersistentVolumesBuilders` (public interface)
5. Line 513: `KubernetesPersistentVolumesHeaderNameBuilder` (public static class)
6. Line 560: `KubernetesPersistentVolumesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes PersistentVolumes endpoint component enabling integration with Kubernetes persistent volume resource for cluster-wide storage abstraction, volume lifecycle management, and storage backend provisioning. Compact producer-oriented builder supporting Kubernetes PV operations. Provides fluent DSL through KubernetesPersistentVolumesEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, PV name/storage class/capacity specification, and underlying storage backend configuration (NFS, iSCSI, cloud provider). Contains KubernetesPersistentVolumesBuilders interface and KubernetesPersistentVolumesHeaderNameBuilder static inner class providing String constants for Kubernetes PV-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 202
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesPodsEndpointBuilderFactory.java  
**Total Lines:** 1552

**Type Declarations:** 10 total

1. Line 35: `KubernetesPodsEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesPodsEndpointConsumerBuilder` (public interface)
3. Line 479: `AdvancedKubernetesPodsEndpointConsumerBuilder` (public interface)
4. Line 638: `KubernetesPodsEndpointProducerBuilder` (public interface)
5. Line 950: `AdvancedKubernetesPodsEndpointProducerBuilder` (public interface)
6. Line 1037: `KubernetesPodsEndpointBuilder` (public interface)
7. Line 1336: `AdvancedKubernetesPodsEndpointBuilder` (public interface)
8. Line 1377: `KubernetesPodsBuilders` (public interface)
9. Line 1451: `KubernetesPodsHeaderNameBuilder` (public static class)
10. Line 1546: `KubernetesPodsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Pods endpoint component enabling integration with Kubernetes pod resource for container orchestration, pod lifecycle management, and containerized application deployment. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Pod operations. Provides fluent DSL through separate KubernetesPodsEndpointConsumerBuilder and KubernetesPodsEndpointProducerBuilder interfaces plus unified KubernetesPodsEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, pod name/label selection, watch/list operations, pod status monitoring, container specification, and Kubernetes client configuration. Contains KubernetesPodsBuilders interface and KubernetesPodsHeaderNameBuilder static inner class providing String constants for Kubernetes Pod-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

## Phase 52: Files 203-206

### File 203
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesReplicationControllersEndpointBuilderFactory.java  
**Total Lines:** 1570

**Type Declarations:** 10 total

1. Line 36: `KubernetesReplicationControllersEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesReplicationControllersEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesReplicationControllersEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesReplicationControllersEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesReplicationControllersEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesReplicationControllersEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesReplicationControllersEndpointBuilder` (public interface)
8. Line 1378: `KubernetesReplicationControllersBuilders` (public interface)
9. Line 1452: `KubernetesReplicationControllersHeaderNameBuilder` (public static class)
10. Line 1564: `KubernetesReplicationControllersEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes ReplicationControllers endpoint component enabling integration with Kubernetes replication controller resource for legacy pod replica management and controlled pod replication. Large builder with distinct consumer and producer variants for bidirectional ReplicationController operations. Provides fluent DSL through separate KubernetesReplicationControllersEndpointConsumerBuilder and KubernetesReplicationControllersEndpointProducerBuilder interfaces plus unified KubernetesReplicationControllersEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, controller name/label selection, watch/list operations, replica count/status management, and Kubernetes client configuration. Contains KubernetesReplicationControllersBuilders interface and KubernetesReplicationControllersHeaderNameBuilder static inner class providing String constants for ReplicationController-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 204
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesResourcesQuotaEndpointBuilderFactory.java  
**Total Lines:** 586

**Type Declarations:** 6 total

1. Line 35: `KubernetesResourcesQuotaEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesResourcesQuotaEndpointBuilder` (public interface)
3. Line 352: `AdvancedKubernetesResourcesQuotaEndpointBuilder` (public interface)
4. Line 438: `KubernetesResourcesQuotaBuilders` (public interface)
5. Line 509: `KubernetesResourcesQuotaHeaderNameBuilder` (public static class)
6. Line 580: `KubernetesResourcesQuotaEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes ResourceQuota endpoint component enabling integration with Kubernetes resource quota configuration for namespace resource consumption limits, quota enforcement, and namespace-scoped capacity management. Compact producer-oriented builder supporting Kubernetes ResourceQuota operations. Provides fluent DSL through KubernetesResourcesQuotaEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, quota name/specification, and compute/storage/request limits. Contains KubernetesResourcesQuotaBuilders interface and KubernetesResourcesQuotaHeaderNameBuilder static inner class providing String constants for Kubernetes ResourceQuota-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 205
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesSecretsEndpointBuilderFactory.java  
**Total Lines:** 596

**Type Declarations:** 6 total

1. Line 35: `KubernetesSecretsEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesSecretsEndpointBuilder` (public interface)
3. Line 352: `AdvancedKubernetesSecretsEndpointBuilder` (public interface)
4. Line 438: `KubernetesSecretsBuilders` (public interface)
5. Line 509: `KubernetesSecretsHeaderNameBuilder` (public static class)
6. Line 590: `KubernetesSecretsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Secrets endpoint component enabling integration with Kubernetes secret resource for sensitive data storage, credential management, and encrypted configuration value distribution. Compact producer-oriented builder supporting Kubernetes Secret operations. Provides fluent DSL through KubernetesSecretsEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, secret name/type specification (Opaque, kubernetes.io/basic-auth, etc.), and secret data/binary content management. Contains KubernetesSecretsBuilders interface and KubernetesSecretsHeaderNameBuilder static inner class providing String constants for Kubernetes Secret-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 206
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesServiceAccountsEndpointBuilderFactory.java  
**Total Lines:** 586

**Type Declarations:** 6 total

1. Line 35: `KubernetesServiceAccountsEndpointBuilderFactory` (public interface)
2. Line 40: `KubernetesServiceAccountsEndpointBuilder` (public interface)
3. Line 352: `AdvancedKubernetesServiceAccountsEndpointBuilder` (public interface)
4. Line 438: `KubernetesServiceAccountsBuilders` (public interface)
5. Line 509: `KubernetesServiceAccountsHeaderNameBuilder` (public static class)
6. Line 580: `KubernetesServiceAccountsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes ServiceAccounts endpoint component enabling integration with Kubernetes service account resource for pod identity management, RBAC-based access control, and cluster-wide authentication credential provisioning. Compact producer-oriented builder supporting Kubernetes ServiceAccount operations. Provides fluent DSL through KubernetesServiceAccountsEndpointBuilder interface with advanced variant for fine-grained control over Kubernetes API server connection, namespace selection, service account name, image pull secret references, and RBAC role binding configuration. Contains KubernetesServiceAccountsBuilders interface and KubernetesServiceAccountsHeaderNameBuilder static inner class providing String constants for Kubernetes ServiceAccount-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

## Phase 53: Files 207-210

### File 207
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KubernetesServicesEndpointBuilderFactory.java  
**Total Lines:** 1553

**Type Declarations:** 10 total

1. Line 36: `KubernetesServicesEndpointBuilderFactory` (public interface)
2. Line 41: `KubernetesServicesEndpointConsumerBuilder` (public interface)
3. Line 480: `AdvancedKubernetesServicesEndpointConsumerBuilder` (public interface)
4. Line 639: `KubernetesServicesEndpointProducerBuilder` (public interface)
5. Line 951: `AdvancedKubernetesServicesEndpointProducerBuilder` (public interface)
6. Line 1038: `KubernetesServicesEndpointBuilder` (public interface)
7. Line 1337: `AdvancedKubernetesServicesEndpointBuilder` (public interface)
8. Line 1378: `KubernetesServicesBuilders` (public interface)
9. Line 1452: `KubernetesServicesHeaderNameBuilder` (public static class)
10. Line 1547: `KubernetesServicesEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kubernetes Services endpoint component enabling integration with Kubernetes service resource for network load balancing, service discovery, and cluster network access abstraction. Large builder with distinct consumer and producer variants for bidirectional Kubernetes Service operations. Provides fluent DSL through separate KubernetesServicesEndpointConsumerBuilder and KubernetesServicesEndpointProducerBuilder interfaces plus unified KubernetesServicesEndpointBuilder. Advanced variants provide comprehensive control over Kubernetes API server connection, namespace selection, service name/label selection, watch/list operations, service type (ClusterIP/NodePort/LoadBalancer), port/protocol specification, and selector management. Contains KubernetesServicesBuilders interface and KubernetesServicesHeaderNameBuilder static inner class providing String constants for Kubernetes Service-specific headers. Single inner implementation class manages builder state and Kubernetes API client configuration.

---

### File 208
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/KuduEndpointBuilderFactory.java  
**Total Lines:** 288

**Type Declarations:** 6 total

1. Line 36: `KuduEndpointBuilderFactory` (public interface)
2. Line 41: `KuduEndpointBuilder` (public interface)
3. Line 83: `AdvancedKuduEndpointBuilder` (public interface)
4. Line 138: `KuduBuilders` (public interface)
5. Line 212: `KuduHeaderNameBuilder` (public static class)
6. Line 282: `KuduEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Kudu endpoint component enabling integration with Apache Kudu columnar storage system for fast analytical data processing, time-series data management, and real-time analytics workloads. Compact producer-oriented builder supporting Kudu write operations. Provides fluent DSL through KuduEndpointBuilder interface with advanced variant for fine-grained control over Kudu master connection, table name, operation type (insert/update/delete/upsert), partition key specification, and client connection pooling. Contains KuduBuilders interface and KuduHeaderNameBuilder static inner class providing String constants for Kudu-specific headers. Single inner implementation class manages builder state and Kudu client configuration.

---

### File 209
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/Lambda2EndpointBuilderFactory.java  
**Total Lines:** 1336

**Type Declarations:** 6 total

1. Line 35: `Lambda2EndpointBuilderFactory` (public interface)
2. Line 40: `Lambda2EndpointBuilder` (public interface)
3. Line 447: `AdvancedLambda2EndpointBuilder` (public interface)
4. Line 534: `Lambda2Builders` (public interface)
5. Line 593: `Lambda2HeaderNameBuilder` (public static class)
6. Line 1330: `Lambda2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for AWS Lambda2 endpoint component enabling integration with Amazon Web Services Lambda for serverless function invocation, event-driven processing, and compute-as-a-service workloads. Medium-complexity producer-oriented builder supporting Lambda function invocation. Provides fluent DSL through Lambda2EndpointBuilder interface with advanced variant for fine-grained control over AWS Lambda client connection, function name/ARN specification, invocation type (RequestResponse/Event/DryRun), payload structure, and AWS SDK configuration. Contains Lambda2Builders interface and Lambda2HeaderNameBuilder static inner class providing String constants for Lambda-specific headers (function name, invocation type, response handling). Single inner implementation class manages builder state and AWS Lambda client configuration.

---

### File 210
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jAgentEndpointBuilderFactory.java  
**Total Lines:** 515

**Type Declarations:** 6 total

1. Line 35: `LangChain4jAgentEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jAgentEndpointBuilder` (public interface)
3. Line 226: `AdvancedLangChain4jAgentEndpointBuilder` (public interface)
4. Line 364: `LangChain4jAgentBuilders` (public interface)
5. Line 423: `LangChain4jAgentHeaderNameBuilder` (public static class)
6. Line 509: `LangChain4jAgentEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j Agent endpoint component enabling integration with LangChain4j AI agent framework for large language model agent orchestration, multi-step reasoning chains, and autonomous AI agent workflows. Producer-oriented builder supporting LangChain4j agent invocation. Provides fluent DSL through LangChain4jAgentEndpointBuilder interface with advanced variant for fine-grained control over LLM model selection, agent tool/tool-chain configuration, prompt injection prevention, conversation history management, and LangChain4j framework integration. Contains LangChain4jAgentBuilders interface and LangChain4jAgentHeaderNameBuilder static inner class providing String constants for LangChain4j-specific headers (agent ID, model name, reasoning type). Single inner implementation class manages builder state and LangChain4j agent lifecycle.

---

## Phase 54: Files 211-214

### File 211
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jChatEndpointBuilderFactory.java  
**Total Lines:** 273

**Type Declarations:** 6 total

1. Line 35: `LangChain4jChatEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jChatEndpointBuilder` (public interface)
3. Line 90: `AdvancedLangChain4jChatEndpointBuilder` (public interface)
4. Line 175: `LangChain4jChatBuilders` (public interface)
5. Line 234: `LangChain4jChatHeaderNameBuilder` (public static class)
6. Line 267: `LangChain4jChatEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j Chat endpoint component enabling integration with LangChain4j conversational AI framework for chat-based message processing, multi-turn conversation context management, and AI-driven dialogue systems. Compact producer-oriented builder supporting LangChain4j chat model invocation. Provides fluent DSL through LangChain4jChatEndpointBuilder interface with advanced variant for fine-grained control over chat model selection, message format, system prompt injection, conversation history retention, temperature/token limits, and LangChain4j chat framework configuration. Contains LangChain4jChatBuilders interface and LangChain4jChatHeaderNameBuilder static inner class providing String constants for LangChain4j chat-specific headers. Single inner implementation class manages builder state and LangChain4j chat client configuration.

---

### File 212
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jEmbeddingStoreEndpointBuilderFactory.java  
**Total Lines:** 416

**Type Declarations:** 6 total

1. Line 35: `LangChain4jEmbeddingStoreEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jEmbeddingStoreEndpointBuilder` (public interface)
3. Line 233: `AdvancedLangChain4jEmbeddingStoreEndpointBuilder` (public interface)
4. Line 288: `LangChain4jEmbeddingStoreBuilders` (public interface)
5. Line 347: `LangChain4jEmbeddingStoreHeaderNameBuilder` (public static class)
6. Line 410: `LangChain4jEmbeddingStoreEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j EmbeddingStore endpoint component enabling integration with LangChain4j vector database storage for semantic search, vector embedding persistence, and retrieval-augmented generation (RAG) workflows. Producer-oriented builder supporting LangChain4j embedding store operations (store/retrieve/search). Provides fluent DSL through LangChain4jEmbeddingStoreEndpointBuilder interface with advanced variant for fine-grained control over embedding store backend selection, document chunking, similarity search threshold, metadata filtering, index creation/update, and LangChain4j RAG framework configuration. Contains LangChain4jEmbeddingStoreBuilders interface and LangChain4jEmbeddingStoreHeaderNameBuilder static inner class providing String constants for embedding store-specific headers. Single inner implementation class manages builder state and embedding store client configuration.

---

### File 213
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jEmbeddingsEndpointBuilderFactory.java  
**Total Lines:** 306

**Type Declarations:** 6 total

1. Line 35: `LangChain4jEmbeddingsEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jEmbeddingsEndpointBuilder` (public interface)
3. Line 84: `AdvancedLangChain4jEmbeddingsEndpointBuilder` (public interface)
4. Line 139: `LangChain4jEmbeddingsBuilders` (public interface)
5. Line 198: `LangChain4jEmbeddingsHeaderNameBuilder` (public static class)
6. Line 300: `LangChain4jEmbeddingsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j Embeddings endpoint component enabling integration with LangChain4j embedding model framework for text-to-vector conversion, semantic representation generation, and embedding model inference. Producer-oriented builder supporting LangChain4j embedding model invocation. Provides fluent DSL through LangChain4jEmbeddingsEndpointBuilder interface with advanced variant for fine-grained control over embedding model selection, input text normalization, vector dimension specification, batch processing, model caching, and LangChain4j embedding framework configuration. Contains LangChain4jEmbeddingsBuilders interface and LangChain4jEmbeddingsHeaderNameBuilder static inner class providing String constants for embeddings-specific headers (model name, dimension, batch size). Single inner implementation class manages builder state and LangChain4j embedding model client configuration.

---

### File 214
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jToolsEndpointBuilderFactory.java  
**Total Lines:** 598

**Type Declarations:** 9 total

1. Line 35: `LangChain4jToolsEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jToolsEndpointBuilder` (public interface)
3. Line 177: `AdvancedLangChain4jToolsEndpointBuilder` (public interface)
4. Line 367: `LangChain4jToolsBuilders` (public interface)
5. Line 394: `LangChain4jToolsHeaderNameBuilder` (public static class)
6. Line 480: `ToolInputParameter` (public static class, nested)
7. Line 508: `ToolInputParameters` (public static class, nested)
8. Line 548: `ToolInputParameterValues` (public static class, nested)
9. Line 592: `LangChain4jToolsEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j Tools endpoint component enabling integration with LangChain4j tool definition framework for agent tool registration, function calling capability management, and AI agent action invocation. Medium-complexity producer-oriented builder supporting LangChain4j tool/function declaration and registration. Provides fluent DSL through LangChain4jToolsEndpointBuilder interface with advanced variant for fine-grained control over tool definition schema, parameter specification, invocation handler mapping, and LangChain4j tool framework configuration. Contains three nested static inner classes (ToolInputParameter, ToolInputParameters, ToolInputParameterValues) for fluent parameter definition and validation. Contains LangChain4jToolsBuilders interface and LangChain4jToolsHeaderNameBuilder static inner class providing String constants for tool-specific headers. Single outer implementation class manages builder state and tool registration lifecycle.

---

## Phase 55: Files 215-218

### File 215
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LangChain4jWebSearchEndpointBuilderFactory.java  
**Total Lines:** 462

**Type Declarations:** 5 total

1. Line 35: `LangChain4jWebSearchEndpointBuilderFactory` (public interface)
2. Line 40: `LangChain4jWebSearchEndpointBuilder` (public interface)
3. Line 327: `AdvancedLangChain4jWebSearchEndpointBuilder` (public interface)
4. Line 412: `LangChain4jWebSearchBuilders` (public interface)
5. Line 456: `LangChain4jWebSearchEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LangChain4j Web Search endpoint component enabling integration with LangChain4j web search capabilities for real-time search result retrieval, knowledge graph queries, and current information augmentation in AI agent workflows. Producer-oriented builder supporting LangChain4j web search API invocation. Provides fluent DSL through LangChain4jWebSearchEndpointBuilder interface with advanced variant for fine-grained control over search provider selection, query parameterization, result ranking, snippet extraction, and LangChain4j web search framework configuration. Contains LangChain4jWebSearchBuilders interface providing String constants for web search-specific headers. Single inner implementation class manages builder state and web search API client configuration.

---

### File 216
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LanguageEndpointBuilderFactory.java  
**Total Lines:** 446

**Type Declarations:** 6 total

1. Line 35: `LanguageEndpointBuilderFactory` (public interface)
2. Line 40: `LanguageEndpointBuilder` (public interface)
3. Line 218: `AdvancedLanguageEndpointBuilder` (public interface)
4. Line 339: `LanguageBuilders` (public interface)
5. Line 418: `LanguageHeaderNameBuilder` (public static class)
6. Line 440: `LanguageEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Language endpoint component enabling integration with natural language processing frameworks for language detection, text classification, and linguistic analysis in data processing pipelines. Producer-oriented builder supporting language analysis operations. Provides fluent DSL through LanguageEndpointBuilder interface with advanced variant for fine-grained control over language model selection, text preprocessing, confidence thresholds, supported language list, result format, and language processing framework configuration. Contains LanguageBuilders interface and LanguageHeaderNameBuilder static inner class providing String constants for language-specific headers. Single inner implementation class manages builder state and language analysis model configuration.

---

### File 217
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LdapEndpointBuilderFactory.java  
**Total Lines:** 256

**Type Declarations:** 5 total

1. Line 35: `LdapEndpointBuilderFactory` (public interface)
2. Line 40: `LdapEndpointBuilder` (public interface)
3. Line 135: `AdvancedLdapEndpointBuilder` (public interface)
4. Line 190: `LdapBuilders` (public interface)
5. Line 250: `LdapEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LDAP endpoint component enabling integration with LDAP directory services for user/group management, credential validation, and directory attribute queries in authentication and authorization workflows. Compact producer-oriented builder supporting LDAP directory operations. Provides fluent DSL through LdapEndpointBuilder interface with advanced variant for fine-grained control over LDAP server connection, directory base DN, search filter specification, attribute selection, bind credentials, and LDAP client configuration. Contains LdapBuilders interface providing String constants for LDAP-specific headers. Single inner implementation class manages builder state and LDAP connection/search lifecycle.

---

### File 218
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LdifEndpointBuilderFactory.java  
**Total Lines:** 161

**Type Declarations:** 5 total

1. Line 35: `LdifEndpointBuilderFactory` (public interface)
2. Line 40: `LdifEndpointBuilder` (public interface)
3. Line 52: `AdvancedLdifEndpointBuilder` (public interface)
4. Line 107: `LdifBuilders` (public interface)
5. Line 155: `LdifEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for LDIF endpoint component enabling integration with LDIF (LDAP Data Interchange Format) file processing for directory entry import/export, configuration management, and LDAP data serialization. Compact producer-oriented builder supporting LDIF file operations. Provides fluent DSL through LdifEndpointBuilder interface with advanced variant for fine-grained control over LDIF file path, encoding, change record processing, entry parsing, and LDIF validation. Contains LdifBuilders interface providing String constants for LDIF-specific headers. Single inner implementation class manages builder state and LDIF file I/O lifecycle.

---

## Phase 56: Files 219-222

### File 219
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LogEndpointBuilderFactory.java  
**Total Lines:** 1130

**Type Declarations:** 5 total

1. Line 35: `LogEndpointBuilderFactory` (public interface)
2. Line 40: `LogEndpointBuilder` (public interface)
3. Line 993: `AdvancedLogEndpointBuilder` (public interface)
4. Line 1078: `LogBuilders` (public interface)
5. Line 1124: `LogEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Log endpoint component enabling integration with Camel's logging subsystem for message payload logging, exchange context logging, and framework debug/audit trails. Producer-oriented builder supporting log output operations. Provides fluent DSL through LogEndpointBuilder interface with advanced variant for fine-grained control over logger name, logging level (INFO/DEBUG/WARN/ERROR), message format template, max body length, exception logging, exchange property inclusion, and SLF4J/log4j configuration. Contains LogBuilders interface providing String constants for log-specific headers. Single inner implementation class manages builder state and logger configuration.

---

### File 220
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LuceneEndpointBuilderFactory.java  
**Total Lines:** 341

**Type Declarations:** 6 total

1. Line 35: `LuceneEndpointBuilderFactory` (public interface)
2. Line 40: `LuceneEndpointBuilder` (public interface)
3. Line 178: `AdvancedLuceneEndpointBuilder` (public interface)
4. Line 233: `LuceneBuilders` (public interface)
5. Line 300: `LuceneHeaderNameBuilder` (public static class)
6. Line 335: `LuceneEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Lucene endpoint component enabling integration with Apache Lucene full-text search engine for index-based document retrieval, relevance-ranked queries, and high-performance text search capabilities. Producer-oriented builder supporting Lucene search/index operations. Provides fluent DSL through LuceneEndpointBuilder interface with advanced variant for fine-grained control over index directory path, analyzer selection, query language, result ranking, highlighting, and Lucene index configuration. Contains LuceneBuilders interface and LuceneHeaderNameBuilder static inner class providing String constants for Lucene-specific headers. Single inner implementation class manages builder state and Lucene index/search client configuration.

---

### File 221
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/LumberjackEndpointBuilderFactory.java  
**Total Lines:** 266

**Type Declarations:** 5 total

1. Line 35: `LumberjackEndpointBuilderFactory` (public interface)
2. Line 40: `LumberjackEndpointBuilder` (public interface)
3. Line 82: `AdvancedLumberjackEndpointBuilder` (public interface)
4. Line 208: `LumberjackBuilders` (public interface)
5. Line 260: `LumberjackEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Lumberjack endpoint component enabling integration with Logstash/Lumberjack protocol for structured log aggregation, log forwarding to log centralization platforms, and log filtering/parsing. Producer-oriented builder supporting Lumberjack protocol output. Provides fluent DSL through LumberjackEndpointBuilder interface with advanced variant for fine-grained control over Lumberjack server connection, port configuration, SSL/TLS security, compression, batch window, and Lumberjack protocol frame format. Contains LumberjackBuilders interface providing String constants for Lumberjack-specific headers. Single inner implementation class manages builder state and Lumberjack protocol client configuration.

---

### File 222
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MQ2EndpointBuilderFactory.java  
**Total Lines:** 788

**Type Declarations:** 6 total

1. Line 35: `MQ2EndpointBuilderFactory` (public interface)
2. Line 40: `MQ2EndpointBuilder` (public interface)
3. Line 445: `AdvancedMQ2EndpointBuilder` (public interface)
4. Line 530: `MQ2Builders` (public interface)
5. Line 589: `MQ2HeaderNameBuilder` (public static class)
6. Line 782: `MQ2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for IBM MQ2 endpoint component enabling integration with IBM MQ (formerly WebSphere MQ) for enterprise message queueing, point-to-point messaging, and publish-subscribe patterns. Producer-oriented builder supporting MQ message send operations. Provides fluent DSL through MQ2EndpointBuilder interface with advanced variant for fine-grained control over MQ queue manager connection, queue name, message format, persistence, expiry, priority, correlation ID, and IBM MQ client configuration. Contains MQ2Builders interface and MQ2HeaderNameBuilder static inner class providing String constants for MQ-specific headers. Single inner implementation class manages builder state and IBM MQ connection/message send lifecycle.

---

## Phase 57: Files 223-226

### File 223
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MSK2EndpointBuilderFactory.java  
**Total Lines:** 736

**Type Declarations:** 6 total

1. Line 35: `MSK2EndpointBuilderFactory` (public interface)
2. Line 40: `MSK2EndpointBuilder` (public interface)
3. Line 443: `AdvancedMSK2EndpointBuilder` (public interface)
4. Line 528: `MSK2Builders` (public interface)
5. Line 587: `MSK2HeaderNameBuilder` (public static class)
6. Line 730: `MSK2EndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for AWS MSK (Managed Streaming for Apache Kafka) 2 endpoint component enabling integration with AWS Managed Streaming for Kafka for scalable event streaming, Kafka broker management, and AWS-hosted Kafka cluster operations. Producer-oriented builder supporting MSK topic publish operations. Provides fluent DSL through MSK2EndpointBuilder interface with advanced variant for fine-grained control over AWS MSK cluster connection, topic name, partition selection, record batching, message key/value serialization, and AWS SDK configuration. Contains MSK2Builders interface and MSK2HeaderNameBuilder static inner class providing String constants for MSK-specific headers. Single inner implementation class manages builder state and AWS MSK client configuration.

---

### File 224
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MailEndpointBuilderFactory.java  
**Total Lines:** 3786

**Type Declarations:** 10 total

1. Line 35: `MailEndpointBuilderFactory` (public interface)
2. Line 40: `MailEndpointConsumerBuilder` (public interface)
3. Line 1139: `AdvancedMailEndpointConsumerBuilder` (public interface)
4. Line 2009: `MailEndpointProducerBuilder` (public interface)
5. Line 2309: `AdvancedMailEndpointProducerBuilder` (public interface)
6. Line 2912: `MailEndpointBuilder` (public interface)
7. Line 2983: `AdvancedMailEndpointBuilder` (public interface)
8. Line 3462: `MailBuilders` (public interface)
9. Line 3637: `MailHeaderNameBuilder` (public static class)
10. Line 3780: `MailEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Mail endpoint component enabling integration with email protocols (SMTP/IMAP/POP3) for message sending, email receipt/polling, and email folder management. Extremely large builder with distinct consumer and producer variants for bidirectional email operations. Provides fluent DSL through separate MailEndpointConsumerBuilder and MailEndpointProducerBuilder interfaces plus unified MailEndpointBuilder. Advanced variants provide comprehensive control over mail server connection (host/port), authentication (username/password), protocol selection, folder management (IMAP), SSL/TLS security, message filters, attachment handling, header/body parsing, and email client configuration. Contains MailBuilders interface and MailHeaderNameBuilder static inner class providing String constants for mail-specific headers (subject, to, from, cc, bcc). Single inner implementation class manages builder state and mail protocol client configuration.

---

### File 225
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MapstructEndpointBuilderFactory.java  
**Total Lines:** 191

**Type Declarations:** 5 total

1. Line 35: `MapstructEndpointBuilderFactory` (public interface)
2. Line 40: `MapstructEndpointBuilder` (public interface)
3. Line 84: `AdvancedMapstructEndpointBuilder` (public interface)
4. Line 139: `MapstructBuilders` (public interface)
5. Line 185: `MapstructEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Mapstruct endpoint component enabling integration with Mapstruct bean mapping framework for declarative object-to-object mapping, data transformation, and model/DTO conversion. Producer-oriented builder supporting Mapstruct mapper invocation. Provides fluent DSL through MapstructEndpointBuilder interface with advanced variant for fine-grained control over mapper class selection, source/target type specification, mapping strategy, nested mapping configuration, and Mapstruct framework configuration. Contains MapstructBuilders interface providing String constants for Mapstruct-specific headers. Single inner implementation class manages builder state and Mapstruct mapper instance configuration.

---

### File 226
**Path:** dsl/camel-endpointdsl/src/generated/java/org/apache/camel/builder/endpoint/dsl/MasterEndpointBuilderFactory.java  
**Total Lines:** 237

**Type Declarations:** 5 total

1. Line 36: `MasterEndpointBuilderFactory` (public interface)
2. Line 41: `MasterEndpointBuilder` (public interface)
3. Line 53: `AdvancedMasterEndpointBuilder` (public interface)
4. Line 179: `MasterBuilders` (public interface)
5. Line 231: `MasterEndpointBuilderImpl` (class, local inner class in endpointBuilder() method)

### Role Summary
Auto-generated endpoint builder factory class produced by camel-maven-packaging (EndpointDslMojo) for Master endpoint component enabling integration with Camel master/singleton pattern for cluster-wide leader election, singleton route deployment, and distributed exclusive execution. Producer-oriented builder supporting master/singleton election operations. Provides fluent DSL through MasterEndpointBuilder interface with advanced variant for fine-grained control over election service backend, group name, route ID, lock configuration, TTL, heartbeat interval, and distributed coordination framework. Contains MasterBuilders interface providing String constants for master/singleton-specific headers. Single inner implementation class manages builder state and leader election lifecycle.

---

### File 227
**Path:** core/camel-core-processor/src/main/java/org/apache/camel/processor/aggregate/AggregateProcessor.java  
**Total Lines:** 1987

**Type Declarations:** 8 total

1. Line 88: `AggregateProcessor` (public class)
2. Line 149: `RedeliveryData` (private static class)
3. Line 153: `Statistics` (private class)
4. Line 1268: `AggregateOnCompletion` (private final class)
5. Line 1321: `AggregationTimeoutMap` (private final class)
6. Line 1384: `AggregationIntervalTask` (private final class)
7. Line 1442: `RecoverTask` (private final class)
8. Line 1948: `WaitableInteger` (protected static final class)

### Role Summary
Core implementation of Camel's Aggregator pattern Enterprise Integration Pattern (EIP). Orchestrates complex aggregation logic including message correlation by key, time-based and size-based completion conditions, timeout management, redelivery handling, and exchange recovery. Manages internal state through statistics tracking, timeout maps, scheduled interval tasks, and recovery mechanisms for failed aggregations. Critical component for batching, windowing, and correlation-based message processing in routes. Supports idempotent repository integration for reliable message processing and includes sophisticated lifecycle management through initialization, startup, and shutdown phases.

---

### File 228
**Path:** components/camel-debezium/camel-debezium-postgres/src/generated/java/org/apache/camel/component/debezium/postgres/configuration/PostgresConnectorEmbeddedDebeziumConfiguration.java  
**Total Lines:** 1971

**Type Declarations:** 1 total

1. Line 14: `PostgresConnectorEmbeddedDebeziumConfiguration` (public class)

### Role Summary
Auto-generated configuration holder class for Debezium PostgreSQL connector parameters produced by the Debezium code generation engine. Extends EmbeddedDebeziumConfiguration base class and encapsulates all connector-specific configuration properties for PostgreSQL CDC (Change Data Capture) operations including database connection parameters, logical decoding settings, replication slot configuration, snapshot behavior, schema/table filtering, and PostgreSQL-specific WAL (Write-Ahead Log) handling. Contains 1,900+ lines of property definitions with getter/setter methods, configuration validation, and Debezium property map serialization. Serves as the bridge between Camel route configuration and Debezium PostgreSQL connector plugin initialization.

---

### File 229
**Path:** components/camel-ai/camel-a2a/src/main/java/org/apache/camel/component/a2a/A2AConsumer.java  
**Total Lines:** 1969

**Type Declarations:** 8 total

1. Line 91: `A2AConsumer` (public class)
2. Line 385: `ServerBusyException` (static class)
3. Line 391: `PendingTask` (private record)
4. Line 1932: `A2ARequestHandler` (interface, @FunctionalInterface)
5. Line 1936: `TaskNotFoundException` (private static class)
6. Line 1942: `AuthorizationException` (private static class)
7. Line 1948: `UnsupportedExtensionException` (private static class)
8. Line 1954: `RestRequestException` (private static class)

### Role Summary
Agent-to-Agent (A2A) consumer component implementing bidirectional REST and JSON-RPC protocol bindings with Server-Sent Event (SSE) streaming support. Handles agent communication patterns including task processing, agent-to-agent messaging, authorization enforcement, and extension protocol negotiation. Manages pending task tracking through PendingTask records, implements request handlers via A2ARequestHandler functional interface, and provides comprehensive exception hierarchy for task execution failures. Supports complex error scenarios including server busy conditions, authorization mismatches, and unsupported protocol extensions. Integrates with Camel's consumer lifecycle for message correlation and asynchronous routing of AI agent requests.

---

### File 230
**Path:** components/camel-file/src/main/java/org/apache/camel/component/file/GenericFileEndpoint.java  
**Total Lines:** 1939

**Type Declarations:** 1 total

1. Line 56: `GenericFileEndpoint` (public abstract class)

### Role Summary
Abstract base class defining the unified file endpoint contract for all file-based components (file, sftp, ftp, ftps) in Camel. Encapsulates 50+ @UriParam-annotated configuration properties for file consumer and producer operations including path traversal (recursive, maxDepth, minDepth), file filtering (include/exclude patterns, antFilter), idempotent processing, read-lock strategies (none, markerFile, fileLock, rename, changed), temporary file handling, done file patterns, buffering, charset handling, and move/delete behaviors. Implements BrowsableEndpoint interface enabling in-flight message browsing. Contains extensive getter/setter implementations for all configuration options plus lifecycle methods (doInit, doStart, doStop) managing file language expressions and idempotent repository initialization. Serves as the polymorphic root for multiple concrete file endpoint implementations across different transport protocols.

---

### File 231
**Path:** components/camel-debezium/camel-debezium-mysql/src/generated/java/org/apache/camel/component/debezium/mysql/configuration/MySqlConnectorEmbeddedDebeziumConfiguration.java  
**Total Lines:** 1919

**Type Declarations:** 1 total

1. Line 13: `MySqlConnectorEmbeddedDebeziumConfiguration` (public class)

### Role Summary
Auto-generated configuration holder class for Debezium MySQL connector parameters. Extends EmbeddedDebeziumConfiguration base class. Encapsulates all connector-specific configuration properties for MySQL CDC (Change Data Capture) operations including database connection parameters (hostname, port, username, password, JDBC driver), snapshot behavior (snapshot mode, lock strategy, thread configuration), binary logging settings (binlog buffer size, read/write timeouts), replication slot configuration, GTIDs filtering, table and schema inclusion/exclusion patterns, decimal and bigint handling modes, transaction metadata extraction, OpenLineage integration for data lineage tracking. Provides 150+ @UriParam-annotated fields with comprehensive getter/setter implementations. Bridge between Camel route configuration and Debezium MySQL connector plugin initialization.

---

### File 232
**Path:** core/camel-api/src/main/java/org/apache/camel/CamelContext.java  
**Total Lines:** 1827

**Type Declarations:** 1 total

1. Line 98: `CamelContext` (public interface)

### Role Summary
Core runtime container interface for Apache Camel applications defining contract for message exchange processing, route management, and framework lifecycle. Extends CamelContextLifecycle for startup/stop/suspend/resume operations and RuntimeConfiguration for configuration property access. Declares 200+ methods providing access to registries (Components, Endpoints, Routes, TypeConverters, Languages, DataFormats), template factories (ProducerTemplate, ConsumerTemplate, FluentProducerTemplate), language/transformer/validator resolution, service management (addService, removeService, deferStartService), startup listener registration, vault configuration, security parameters, tracing/debugging/monitoring setup, and message exchange execution context. Central integration point for all Camel DSL implementations (Java, XML, YAML) managing component lifecycle, endpoint configuration, message routing orchestration, and runtime behavior control. Supports streaming caching, breadcrumbing, data type tracking, MDC logging, and diagnostic dumping capabilities.

---

### File 233
**Path:** core/camel-core-catalog/src/main/java/org/apache/camel/catalog/impl/AbstractCamelCatalog.java  
**Total Lines:** 1797

**Type Declarations:** 1 total

1. Line 72: `AbstractCamelCatalog` (public abstract class)

### Role Summary
Abstract base class implementing core Camel metadata and schema resolution engine for runtime component/EIP/dataformat/language/transformer/validator discovery. Central schema lookup system enabling endpoint property validation, URI parsing/construction, configuration property validation with type checking (boolean, integer, duration, enum, reference objects), language expression validation (simple, groovy, xpath, etc.), and DSL syntax analysis. Manages suggestion strategies for unknown options and comprehensive validation result aggregation with error classification (unknown/required/deprecated/invalid enum/invalid type). Handles complex endpoint URI parsing including syntax matching, multi-value properties, optional prefixes, and custom filtering. Integrates with Camel catalog metadata providers for component/EIP/dataformat models and supports API component discovery with method aliasing. Provides utility methods for configuration property validation, duration/integer parsing, enum matching, and URI property extraction/construction across multiple endpoint configuration scenarios.

---

### File 234
**Path:** components/camel-oauth/src/test/java/org/apache/camel/oauth/DefaultOAuthTokenValidationFactoryTest.java  
**Total Lines:** 1695

**Type Declarations:** 1 total

1. Line 71: `DefaultOAuthTokenValidationFactoryTest` (package-private class)

### Role Summary
Comprehensive JUnit 5 test class validating OAuth token validation factory functionality for both JWT and opaque token validation scenarios. Tests JWT validation with RSA-based JWKS key set management including issuer/audience verification, token expiration checking, and signed token processing. Validates opaque token handling through introspection endpoint with HTTP Basic authentication, credential encoding, and response parsing. Covers configuration validation patterns enforcing HTTPS security requirements, mandatory JWKS/introspection endpoints, issuer/audience matching rules, and rejection of plain HTTP endpoints by default. Tests OIDC discovery flow for dynamic JWKS URI and introspection endpoint resolution, mixed JWT/opaque validation profiles, and discovery caching behavior. Includes defensive collection copying tests and configuration property validation. Provides extensive test utility helpers via private static methods for starting embedded HTTP servers (JWKS, discovery, introspection), JWT creation with RSA signing, request/response handling, and temporal date calculations.

---

### File 235
**Path:** core/camel-core-model/src/main/java/org/apache/camel/builder/NotifyBuilder.java  
**Total Lines:** 1667

**Type Declarations:** 7 total

1. Line 61: `NotifyBuilder` (public class)
2. Line 1328: `ExchangeNotifier` (private class)
3. Line 1435: `EventOperation` (private enum)
4. Line 1441: `EventPredicate` (private interface)
5. Line 1495: `EventPredicateSupport` (private abstract static class)
6. Line 1538: `EventPredicateHolder` (private static final class)
7. Line 1568: `CompoundEventPredicate` (private static final class)

### Role Summary
Builder pattern implementation for test condition expressions in Camel routes based on exchange lifecycle events. Provides fluent API (when*, then*) for constructing predicates monitoring sent messages, completed exchanges, failed exchanges, message bodies, and predicate matches. Uses internal event predicates and compound operations (AND, OR, NOT) to combine multiple conditions. Registers embedded EventNotifier with CamelContext to intercept exchange creation, completion, failure, and delivery events. Manages CountDownLatch for blocking await operations with configurable timeouts. Contains rich nested type hierarchy including EventOperation enum, EventPredicate interface, EventPredicateSupport abstract base, EventPredicateHolder records, CompoundEventPredicate composition, and private ExchangeNotifier inner class. Commonly used in integration tests for asserting expected message flow patterns and route execution completion.

---

### File 236
**Path:** core/camel-core-xml/src/main/java/org/apache/camel/core/xml/AbstractCamelContextFactoryBean.java  
**Total Lines:** 1658

**Type Declarations:** 1 total

1. Line 157: `AbstractCamelContextFactoryBean` (public abstract class)

### Role Summary
Abstract factory bean for creating and initializing CamelContext instances with routes and services. Implements multiple container interfaces (RouteTemplateContainer, RouteConfigurationContainer, RouteContainer, RestContainer, TemplatedRouteContainer) to manage route definitions, route templates, route configurations, and REST endpoints. Extends IdentifiedType for JAXB XML binding support with @XmlAccessorType annotation. Provides abstract template methods for getting context, routes, configurations, and beans. Performs comprehensive initialization including properties component setup, package scanning, type converters, lifecycle strategies, event notifiers, stream caching, route controller, transformers, validators, REST configurations, and global interceptors. Handles both explicit route configuration and classpath discovery of RouteBuilder implementations. Central lifecycle point for Camel DSL XML configuration deserialization.

---

### File 237
**Path:** components/camel-xmlsecurity/src/test/java/org/apache/camel/component/xmlsecurity/XmlSignatureTest.java  
**Total Lines:** 1622

**Type Declarations:** 1 total

1. Line 111: `XmlSignatureTest` (public class)

### Role Summary
JUnit 5 test class validating XML digital signature component functionality for enveloping, enveloped, and detached signature scenarios. Configures XML signing/verification endpoints with RSA key pairs and validates cryptographic operations including key access management, signature algorithms, canonicalization methods, digest algorithms, XPath filters, namespace handling, and encoding transformations. Tests plain text message signing, XML declaration omission, output node searching (ElementName/XPath modes), signature ID generation, payload transformation with XPath2/XSLT filters, and error conditions (invalid XPath expressions, wrong parent elements, schema validation failures). Includes integration with test infrastructure components (TestKeystore, JWKS helpers, mock endpoints) and helper methods for payload generation, namespace mapping, XPath compilation, and exception verification. Provides flexible route builder patterns with multiple signing/verification configurations demonstrating canonical XML processing and secure document handling.

---

### File 238
**Path:** components/camel-keycloak/src/test/java/org/apache/camel/component/keycloak/KeycloakTestInfraIT.java  
**Total Lines:** 1616

**Type Declarations:** 1 total

1. Line 60: `KeycloakTestInfraIT` (public class)

### Role Summary
JUnit 5 integration test class (@RegisterExtension with KafkaServiceFactory) demonstrating comprehensive Keycloak administrative operations within Camel routes. Tests 50+ Keycloak API operations organized by OrderedTest (@Order annotations) covering realms, users, roles, groups, clients, identity providers, authorization services (resources, policies, scopes, permissions), and organizations (Keycloak 26+). Initializes test data with static UUID-based unique names (TEST_REALM_NAME, TEST_USER_NAME, etc.) and manages Keycloak service connection via createCamelContext() override setting realm, server URL, credentials. Implements createRouteBuilder() override returning anonymous RouteBuilder with extensive direct: endpoint configurations triggering Keycloak operations (createRealm, createUser, listUsers, addRoleToUser, createGroup, addUserToGroup, createClient, listClients, resetUserPassword, createIdentityProvider, createResource, createResourcePolicy, evaluatePermission, createOrganization, listOrganizations, etc.). Demonstrates integration testing patterns for OAuth/OIDC Keycloak component across multi-tenant administration scenarios with dependency ordering via @Order attributes.

---

### File 239
**Path:** tooling/maven/camel-package-maven-plugin/src/main/java/org/apache/camel/maven/packaging/EndpointSchemaGeneratorMojo.java  
**Total Lines:** 2023

**Type Declarations:** 1 total

1. Line 104: `EndpointSchemaGeneratorMojo` (public class extends AbstractGeneratorMojo)

### Role Summary
Maven plugin mojo that generates JSON schema documentation for Camel endpoint components by scanning @UriEndpoint annotations and extracting comprehensive component metadata, URI parameters, headers, validators, and configuration options for schema-driven component discovery and IDE autocomplete. Decorated with @Mojo(name = "generate-endpoint-schema", threadSafe = true, requiresDependencyResolution = ResolutionScope.COMPILE_PLUS_RUNTIME, defaultPhase = LifecyclePhase.PROCESS_CLASSES). Executes during PROCESS_CLASSES phase to generate endpoint schema metadata. Processes each component endpoint class and transforms annotation metadata into JSON schema format for use by IDE tooling and component discovery systems. Primary entry point for endpoint schema generation infrastructure in Camel.

---

### File 240
**Path:** catalog/camel-catalog/src/test/java/org/apache/camel/catalog/CamelCatalogTest.java  
**Total Lines:** 1895

**Type Declarations:** 1 total

1. Line 55: `CamelCatalogTest` (public class)

### Role Summary
Comprehensive integration test suite for CamelCatalog functionality covering component metadata discovery, endpoint URI parsing/validation, schema generation, language expression/predicate validation, and configuration property validation across multiple Camel component types. Tests catalog operations for components (JMS, FTP, netty-http, timer, log, SSH, etc.), data formats, languages, transformers, dev consoles, and models. Validates endpoint URI parsing, component property validation with placeholders, language expression/predicate compilation, and dynamic configuration property management. Includes test methods for catalog resource loading, version management with custom VersionManager implementations, POJO bean discovery, release history tracking (Camel and Camel Quarkus releases), and JavaScript/HTML validator file generation for the Simple language. Demonstrates comprehensive catalog infrastructure verification across Camel ecosystem.

---

### File 241
**Path:** tooling/maven/camel-package-maven-plugin/src/main/java/org/apache/camel/maven/packaging/SchemaGeneratorMojo.java  
**Total Lines:** 1740

**Type Declarations:** 2 total

1. Line 85: `SchemaGeneratorMojo` (public class extends AbstractGeneratorMojo)
2. Line 1655: `EipOptionComparator` (private static final class implements Comparator<EipOptionModel>)

### Role Summary
Maven plugin mojo that generates JSON schema documentation for Camel EIP (Enterprise Integration Pattern) model elements by scanning @XmlRootElement and @XmlType annotations and extracting comprehensive model metadata, configuration options, and element descriptions for schema-driven EIP discovery and IDE autocomplete. Decorated with @Mojo(name = "generate-schema", threadSafe = true, requiresDependencyResolution = ResolutionScope.COMPILE_PLUS_RUNTIME, defaultPhase = LifecyclePhase.PROCESS_CLASSES). Processes model classes from camel-core-model, extracts field/property metadata from JAXB annotations, generates EipModel objects with options sorted by EipOptionComparator for priority ordering. Uses Jandex ClassInfo index for efficient class hierarchy scanning and reflection-based introspection. Supports complex metadata extraction including enums, duration types, predicate indicators, and nested type hierarchies (expressions, outputs, verbs, routes, rest services, set operations). Central infrastructure for model schema generation supporting IDE tooling integration.

---

### File 242
**Path:** components/camel-thrift/src/test/java/org/apache/camel/component/thrift/generated/Calculator.java  
**Total Lines:** 6937

**Type Declarations:** 85 total

| Line | Type Name | Classification | Modifiers |
|------|-----------|-----------------|-----------|
| 97 | Calculator | class | public |
| 103 | Iface | interface | public |
| 153 | AsyncIface | interface | public |
| 177 | Client | class | public static |
| 178 | Factory | class | public static |
| 659 | Processor | class | public static |
| 685 | ping | class | public static |
| 718 | add | class | public static |
| 752 | calculate | class | public static |
| 791 | zip | class | public static |
| 824 | echo | class | public static |
| 857 | alltypes | class | public static |
| 895 | AsyncProcessor | class | public static |
| 920 | ping | class | public static |
| 996 | add | class | public static |
| 1075 | calculate | class | public static |
| 1158 | zip | class | public static |
| 1207 | echo | class | public static |
| 1284 | alltypes | class | public static |
| 1367 | ping_args | class | public static |
| 1378 | _Fields | enum | public |
| 1570 | ping_argsStandardSchemeFactory | class | private static |
| 1577 | ping_argsStandardScheme | class | private static |
| 1612 | ping_argsTupleSchemeFactory | class | private static |
| 1619 | ping_argsTupleScheme | class | private static |
| 1639 | ping_result | class | public static |
| 1650 | _Fields | enum | public |
| 1841 | ping_resultStandardSchemeFactory | class | private static |
| 1848 | ping_resultStandardScheme | class | private static |
| 1884 | ping_resultTupleSchemeFactory | class | private static |
| 1891 | ping_resultTupleScheme | class | private static |
| 1913 | add_args | class | public static |
| 1932 | _Fields | enum | public |
| 2282 | add_argsStandardSchemeFactory | class | private static |
| 2289 | add_argsStandardScheme | class | private static |
| 2345 | add_argsTupleSchemeFactory | class | private static |
| 2352 | add_argsTupleScheme | class | private static |
| 2395 | add_result | class | public static |
| 2411 | _Fields | enum | public |
| 2684 | add_resultStandardSchemeFactory | class | private static |
| 2691 | add_resultStandardScheme | class | private static |
| 2740 | add_resultTupleSchemeFactory | class | private static |
| 2747 | add_resultTupleScheme | class | private static |
| 2781 | calculate_args | class | public static |
| 2801 | _Fields | enum | public |
| 3160 | calculate_argsStandardSchemeFactory | class | private static |
| 3167 | calculate_argsStandardScheme | class | private static |
| 3228 | calculate_argsTupleSchemeFactory | class | private static |
| 3235 | calculate_argsTupleScheme | class | private static |
| 3281 | calculate_result | class | public static |
| 3301 | _Fields | enum | public |
| 3658 | calculate_resultStandardSchemeFactory | class | private static |
| 3665 | calculate_resultStandardScheme | class | private static |
| 3728 | calculate_resultTupleSchemeFactory | class | private static |
| 3735 | calculate_resultTupleScheme | class | private static |
| 3781 | zip_args | class | public static |
| 3792 | _Fields | enum | public |
| 3984 | zip_argsStandardSchemeFactory | class | private static |
| 3991 | zip_argsStandardScheme | class | private static |
| 4025 | zip_argsTupleSchemeFactory | class | private static |
| 4032 | zip_argsTupleScheme | class | private static |
| 4052 | echo_args | class | public static |
| 4068 | _Fields | enum | public |
| 4347 | echo_argsStandardSchemeFactory | class | private static |
| 4354 | echo_argsStandardScheme | class | private static |
| 4403 | echo_argsTupleSchemeFactory | class | private static |
| 4410 | echo_argsTupleScheme | class | private static |
| 4444 | echo_result | class | public static |
| 4460 | _Fields | enum | public |
| 4738 | echo_resultStandardSchemeFactory | class | private static |
| 4745 | echo_resultStandardScheme | class | private static |
| 4795 | echo_resultTupleSchemeFactory | class | private static |
| 4802 | echo_resultTupleScheme | class | private static |
| 4838 | alltypes_args | class | public static |
| 4888 | _Fields | enum | public |
| 6109 | alltypes_argsStandardSchemeFactory | class | private static |
| 6116 | alltypes_argsStandardScheme | class | private static |
| 6351 | alltypes_argsTupleSchemeFactory | class | private static |
| 6358 | alltypes_argsTupleScheme | class | private static |
| 6549 | alltypes_result | class | public static |
| 6566 | _Fields | enum | public |
| 6839 | alltypes_resultStandardSchemeFactory | class | private static |
| 6846 | alltypes_resultStandardScheme | class | private static |
| 6895 | alltypes_resultTupleSchemeFactory | class | private static |
| 6902 | alltypes_resultTupleScheme | class | private static |

### Role Summary
Thrift-auto-generated (v0.21.0) RPC service interface and implementation container defining Calculator service with 6 RPC methods (ping, add, calculate, zip/oneway, echo, alltypes). Contains synchronous Iface interface, Client implementation with nested Factory, Processor base, six method-specific ProcessFunction classes, AsyncIface interface, AsyncProcessor base, six method-specific AsyncProcessFunction classes, and comprehensive serialization support with *_args and *_result struct classes for each method. Each struct implements TBase and includes nested _Fields enum plus four scheme-related nested classes (StandardSchemeFactory, StandardScheme, TupleSchemeFactory, TupleScheme) for Thrift protocol encoding/decoding. zip method is oneway (generates only zip_args struct, no zip_result). Work and InvalidOperation types imported from other Thrift-generated files in same package. Primary entry points: line 97 (Calculator class), line 103 (Iface interface), line 177 (Client implementation), line 659 (Processor), line 895 (AsyncProcessor).

---

### File 243
**Path:** tooling/maven/camel-package-maven-plugin/src/main/java/org/apache/camel/maven/packaging/PrepareCatalogMojo.java  
**Total Lines:** 1633

**Type Declarations:** 1 total

1. Line 82: `PrepareCatalogMojo` (public class extends AbstractMojo)

### Role Summary
Maven plugin mojo for preparing and organizing the Camel catalog by processing and aggregating component, data format, language, transformer, bean, and dev-console metadata from across the codebase. Decorated with @Mojo(name = "prepare-catalog", threadSafe = true). Scans all JSON metadata files (component.json, dataformat.json, language.json, other.json, transformer.json, bean.json, dev-console.json) from core, components, DSL, and language directories, aggregates them into per-category output directories, performs comprehensive validation and reporting on labels, first versions, and documentation coverage. Generates JavaScript validators for Simple language functions/operators with embedded catalog data, produces HTML demo pages for component testing, and manages catalog curation for IDE autocomplete and DSL schema generation. Includes Jackson 3.x duplicate filtering, model version synchronization for DSL components, and support for incremental builds through artifact resolution. Central hub for Camel metadata catalog orchestration across all integrations, components, and DSLs.

---

### File 244
**Path:** tooling/maven/camel-package-maven-plugin/src/main/java/org/apache/camel/maven/packaging/PrepareDocSymlinksMojo.java  
**Total Lines:** 766

**Type Declarations:** 4 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 85 | PrepareDocSymlinksMojo | class | public |
| 510 | NavComparator | class | static final |
| 735 | DocGroup | class | private static final |
| 757 | KindSpec | record | private |

### Role Summary
Maven plugin mojo for preparing and maintaining the Antora documentation source tree by symlinking AsciiDoc files, images, and JSON metadata from Camel components, core modules, and DSL directories into the documentation structure. Decorated with @Mojo(name = "prepare-doc-symlinks", defaultPhase = GENERATE_RESOURCES, threadSafe = true). Performs three main operations: (1) cleaning and symlinking .adoc, image, and .json files from source locations with Windows compatibility and fallback copy mechanism when symbolic links cannot be created; (2) generating nav.adoc navigation files sorted by :doctitle: and :group: attributes, with UTF-8 encoding to avoid Windows CP1252 corruption; (3) parsing include::{examplesdir}/... directives and creating corresponding symlinks for embedded code examples with hierarchy preservation. Includes Ant-style glob pattern matching with automatic pruning of build artifacts (target/, .camel-jbang/), JSON filtering for component/dataformat/language/EIP/other metadata classification, basename collision detection/resolution for co-existing model variants. Static inner classes: NavComparator for sorting navigation entries, DocGroup for grouping specification. Record KindSpec for file scan specification with includes/excludes patterns.

---

### File 245
**Path:** core/camel-api/src/main/java/org/apache/camel/support/jsse/BaseSSLContextParameters.java  
**Total Lines:** 1600

**Type Declarations:** 6 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 58 | BaseSSLContextParameters | class | public abstract |
| 1245 | Configurer | interface | protected |
| 1261 | SSLContextDecorator | class | protected static final |
| 1287 | SSLContextSpiDecorator | class | protected static final |
| 1421 | SSLServerSocketFactoryDecorator | class | protected static final |
| 1484 | SSLSocketFactoryDecorator | class | protected static final |

### Role Summary
Core JSSE (Java Secure Socket Extension) configuration utility providing base class for SSL/TLS parameter management in both client and server contexts. Extends JsseParameters and handles cipher suite filtering, protocol selection, named groups (including post-quantum hybrid algorithms like X25519MLKEM768), and signature scheme configuration with reflection-based JDK 17-19/20 compatibility. Static block at lines 102-121 initializes MethodHandle fields (GET_NAMED_GROUPS, SET_NAMED_GROUPS, GET_SIGNATURE_SCHEMES, SET_SIGNATURE_SCHEMES) via reflection with graceful JDK < 19/20 fallback. Configurer<T> generic interface (line 1245) provides single-method contract `T configure(T object)` for applying configuration to SSLEngine, SSLSocket, SSLServerSocket, SSLSocketFactory, and SSLServerSocketFactory instances. Decorator pattern used across nested classes for SSLContext, SSLContextSpi, SSLServerSocketFactory, and SSLSocketFactory interception.

---

### File 246
**Path:** core/camel-core-model/src/main/java/org/apache/camel/builder/DataFormatClause.java  
**Total Lines:** 1592

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 76 | DataFormatClause | class | public |
| 86 | Operation | enum | public |

### Role Summary
Generic builder clause for constructing data format operations (marshal/unmarshal) in Camel DSL routes using fluent API pattern. Generic type parameter T extends ProcessorDefinition<?> to maintain builder chain context through composition. Operation enum (line 86) defines Marshal and Unmarshal values. Provides 40+ public method overloads for supported data format types (Avro, Base64, Bindy, CBOR, CSV, Jackson, JSON, Kryo, ProtoBuf, SOAP, Thrift, XML, YAML, etc.) from lines 99-1523. Instance fields for processorType (T), operation (Operation), variableSend (String), variableReceive (String), allowNullBody (boolean). Private helper method dataFormat() at line 1570 implements switch logic on Operation enum to route to appropriate data format marshaller or unmarshaller. Enables compact DSL expressions like `.marshal().json()` or `.unmarshal().avro()` with type-safe builder pattern.

---

### File 247
**Path:** core/camel-base-engine/src/main/java/org/apache/camel/impl/engine/CamelInternalProcessor.java  
**Total Lines:** 1587

**Type Declarations:** 19 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 113 | CamelInternalProcessor | class | public |
| 231 | AsyncAfterTask | class | private final |
| 465 | RouteLifecycleAdvice | class | public static |
| 499 | RouteInflightRepositoryAdvice | class | public static |
| 530 | RoutePolicyAdvice | class | public static |
| 609 | BacklogTracerRouteAdvice | class | public static |
| 769 | BacklogTracerAggregateAdvice | class | public static final |
| 867 | BacklogTracerAdvice | class | public static final |
| 1068 | BacklogDebuggerAdvice | class | public static final |
| 1099 | DebuggerAdvice | class | public static final |
| 1133 | UnitOfWorkProcessorAdvice | class | public static |
| 1219 | MessageHistoryAdvice | class | public static |
| 1269 | NodeHistoryAdvice | class | public static |
| 1305 | StreamCachingAdvice | class | public static |
| 1340 | DelayerAdvice | class | public static |
| 1375 | TracingAdvice | class | public static |
| 1458 | TracingAfterRoute | class | private static final |
| 1527 | CamelInternalProcessorAdviceWrapper | record | |
| 1554 | TraceAdviceEventNotifier | class | private static final |

### Role Summary
Internal processor implementing cross-cutting concerns in Camel's routing engine via extensive CamelInternalProcessorAdvice advice pattern (before/after callbacks) to reduce stack overhead. Extends DelegateAsyncProcessor and implements InternalProcessor. Executes unit of work, route tracking, route policy, JMX statistics, tracing, debugging, message history, stream caching, transformer, and other concerns through 16 advice implementations (RouteLifecycleAdvice, RouteInflightRepositoryAdvice, RoutePolicyAdvice, BacklogTracerRouteAdvice, BacklogTracerAggregateAdvice, BacklogTracerAdvice, BacklogDebuggerAdvice, DebuggerAdvice, UnitOfWorkProcessorAdvice, MessageHistoryAdvice, NodeHistoryAdvice, StreamCachingAdvice, DelayerAdvice, TracingAdvice). Main process() method at line 284 iterates through sorted advices calling their before() methods, executes wrapped processor, then in reverse order calls after() methods via AsyncAfterTask at line 231. CamelInternalProcessorAdviceWrapper record at line 1527 wraps advice with ordering. TracingAfterRoute at line 1458 extends SynchronizationAdapter for tracing event notification.

---

### File 248
**Path:** core/camel-support/src/main/java/org/apache/camel/support/EventHelper.java  
**Total Lines:** 1582

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 37 | EventHelper | class | public final |

### Role Summary
Optimized static helper utility for sending event notifications across Apache Camel routing engine in single-line calls to EventNotifier instances. Final class with private constructor at line 45 prevents instantiation. Contains 50+ public static methods dispatching notifications for CamelContext lifecycle (initializing, initialized, starting, started, startup/stop failure, stopping, stopped, suspending/suspended, resuming/resumed, resume failure), route lifecycle (starting, started, stopping, stopped, added, removed, reloaded, restarting, restart failure), context reloading, exchange events (created, done/completed, failed, failure handling/handled, redelivery, sending, sent), and step events (started, done/completed, failed). Each notification method checks ManagementStrategy, EventFactory, and EventNotifiers from context, creates event via factory exactly once (lazy), then iterates through notifiers applying appropriate ignore filters. Private helper methods isDisabledOrIgnored() at line 1565 and doNotifyEvent() at line 1569 provide reusable filtering and exception handling. Optimization note in lines 39-41 documents code duplication is intentional for performance in frequently-used routing path.

---

### File 249
**Path:** components/camel-spring-parent/camel-spring-xml/src/main/java/org/apache/camel/spring/xml/CamelContextFactoryBean.java  
**Total Lines:** 1569

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 101 | CamelContextFactoryBean | class | public |

### Role Summary
Spring XML configuration factory bean for CamelContext instantiation, extending AbstractCamelContextFactoryBean<SpringCamelContext> and implementing Spring integration interfaces (FactoryBean, InitializingBean, DisposableBean, ApplicationContextAware, Lifecycle, Phased, ApplicationListener<ContextRefreshedEvent>, Ordered). Primary responsibility is XML-based CamelContext configuration and lifecycle management within Spring ApplicationContext. Manages 50+ @XmlElement fields for route definitions, route configurations, route templates, REST definitions, interceptors, error handlers, data formats, validators, transformers, and thread pool profiles. Integrates with Spring bean post-processing, property placeholder configuration, and lazy initialization (line 520-568 createContext() method defers SpringCamelContext instantiation). Orchestrates custom configuration (line 541-555 configure() method) via optional xmlCamelContextConfigurer bean. Implements Lifecycle hooks (line 426-489 start/stop/isRunning methods) and phase ordering (line 449-471) to coordinate CamelContext lifecycle with Spring container refresh events.

---

### File 250
**Path:** components/camel-cxf/camel-cxf-soap/src/main/java/org/apache/camel/component/cxf/jaxws/CxfEndpoint.java  
**Total Lines:** 1569

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 134 | CxfEndpoint | class | public |
| 1247 | CamelCxfClientImpl | class | public |

### Role Summary
CXF SOAP endpoint providing both WebService publishing (server-side) and client invocation capabilities via Apache CXF. Extends DefaultEndpoint and implements AsyncEndpoint, HeaderFilterStrategyAware, Cloneable interfaces. Manages extensive CXF configuration including Bus management (lines 1015-1047), data format selection (POJO, PAYLOAD, CXF_MESSAGE, RAW), WSDL handling, service/port QName resolution, interceptor chains (in/out/inFault/outFault), features list, and handler chains. Defines inner class CamelCxfClientImpl at line 1247 extending CXF ClientImpl to override setParameters() for PAYLOAD mode support (line 1269-1287) and processResult() for exception handling (line 1254-1265). Core factory bean methods: createProducer() (line 251), createConsumer() (line 261), createClientFactoryBean() (line 443), createServerFactoryBean() (line 722), and setupServerFactoryBean()/setupClientFactoryBean() for configuration. Supports SSL/TLS via SSLContextParameters and HostnameVerifier, MTOM attachments, logging feature, schema validation, and custom CxfConfigurer instances (line 1228-1232 getChainedCxfConfigurer()).

---

### File 251
**Path:** components/camel-debezium/camel-debezium-postgres/src/generated/java/org/apache/camel/component/debezium/postgres/configuration/PostgresConnectorEmbeddedDebeziumConfiguration.java  
**Total Lines:** 1971

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 13 | PostgresConnectorEmbeddedDebeziumConfiguration | class | public |

### Role Summary
Auto-generated @UriParams configuration class for PostgreSQL Debezium connector extending EmbeddedDebeziumConfiguration. Decorated with @Generated("org.apache.camel.maven.GenerateConnectorConfigMojo") and @UriParams annotations. Manages 100+ @UriParam private fields for comprehensive PostgreSQL connector configuration including snapshot modes, SSL/TLS options (databaseSslmode, databaseSslcert, databaseSslkey, databaseSslpassword, databaseSslfactory, databaseSslrootcert), database connection parameters (databaseHostname, databasePort, databaseDbname, databaseUser, databasePassword marked @Metadata required), replication slot configuration (slotName, slotMaxRetries, slotRetryDelayMs, slotFailover, slotDropOnStop, slotStreamParams), schema handling (schemaRefreshMode, schemaIncludeList, schemaExcludeList, schemaNameAdjustmentMode, schemaHistoryInternalFileFilename), data format options (decimalHandlingMode, binaryHandlingMode, hstoreHandlingMode, intervalHandlingMode, timePrecisionMode, columnIncludeList, columnExcludeList, columnPropagateSourceType), heartbeat configuration (heartbeatIntervalMs, heartbeatTopicsPrefix, heartbeatActionQuery), and OpenLineage integration. Implements createConnectorConfiguration() (lines 1829-1949) building Configuration via addPropertyIfNotNull() calls, configureConnectorClass() returning PostgresConnector.class, and validateConnectorConfiguration() ensuring required databasePassword and topicPrefix fields are set.

---

### File 252
**Path:** `components/camel-aws/camel-aws2-ecs/src/main/java/org/apache/camel/component/aws2/ecs/ECS2Endpoint.java`  
**Total Lines:** 107

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 38 | ECS2Endpoint | class | public |

### Role Summary
AWS ECS (Elastic Container Service) endpoint extending ScheduledPollEndpoint and implementing EndpointServiceLocation interface. Producer-only component decorated with @UriEndpoint for managing ECS cluster instances. Manages ECS client configuration and lifecycle (doStart/doStop methods) with optional EcsClient override from configuration. Provides access to cluster management through producer instances. Implements service location discovery returning ECS region or custom endpoint override.

---

### File 253
**Path:** `components/camel-aws/camel-aws2-ecs/src/main/java/org/apache/camel/component/aws2/ecs/ECS2Producer.java`  
**Total Lines:** 293

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 49 | ECS2Producer | class | public |

### Role Summary
AWS ECS producer extending DefaultProducer for sending cluster management operations to Amazon ECS service. Supports four operations: listClusters, describeCluster, createCluster, deleteCluster determined by Exchange header or configuration. Each operation implemented as private method (listClusters, createCluster, describeCluster, deleteCluster) using generic executeOperation() helper with POJO request support and optional response post-processing. Health check integration via ECS2ProducerHealthCheck for producer state monitoring. Generic parameter extraction methods for required/optional headers.

---

### File 254
**Path:** `components/camel-aws/camel-aws2-eks/src/main/java/org/apache/camel/component/aws2/eks/EKS2Endpoint.java`  
**Total Lines:** 108

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 38 | EKS2Endpoint | class | public |

### Role Summary
AWS EKS (Elastic Kubernetes Service) endpoint extending ScheduledPollEndpoint and implementing EndpointServiceLocation interface. Producer-only component decorated with @UriEndpoint for managing EKS cluster instances. Manages EKS client configuration and lifecycle (doStart/doStop methods) with optional EksClient override from configuration. Provides access to Kubernetes cluster management through producer instances. Implements service location discovery returning EKS region or custom endpoint override. Parallel structure to ECS2Endpoint for Kubernetes-specific cluster operations.

---

### File 255
**Path:** `components/camel-aws/camel-aws2-eks/src/main/java/org/apache/camel/component/aws2/eks/EKS2Producer.java`  
**Total Lines:** 298

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 50 | EKS2Producer | class | public |

### Role Summary
AWS EKS producer extending DefaultProducer for sending cluster management operations to Amazon EKS service. Supports four operations: listClusters, describeCluster, createCluster, deleteCluster determined by Exchange header or configuration. Each operation implemented as private method with EKS-specific request models (CreateClusterRequest includes roleArn and VpcConfigRequest). Generic executeOperation() helper supports POJO request/response post-processing with error handling via AwsServiceException. Health check integration for producer monitoring and status discovery.

---

### File 259
**Path:** `components/camel-cxf/camel-cxf-rest/src/main/java/org/apache/camel/component/cxf/jaxrs/CxfRsProducer.java`  
**Total Lines:** 911

**Type Declarations:** 4 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 66 | CxfRsProducer | class | public |
| 680 | CxfInvocationCallback | class | private final |
| 785 | CxfProxyInvocationCallback | class | private final |
| 881 | ClientFactoryBeanCache | class | |

### Role Summary
Complex async JAX-RS producer extending DefaultAsyncProducer for invoking remote REST services with dual invocation paths (HTTP client API and proxy client API). Manages extensive client configuration including HTTP methods, headers, matrix/query/path parameters, cookies, SSL/TLS, authentication, and JSON/XML marshalling. Core methods: invokeAsyncHttpClient() and invokeAsyncProxyClient() (lines 144-373) execute async invocations with completion callbacks, setupClientHeaders/QueryAndHeaders/Matrices() methods configure request properties, populateCxfRsProducerException() (line 599) translates HTTP errors to CxfOperationException. Inner classes: CxfInvocationCallback (line 680) handles async HTTP client responses with deserialization and error handling; CxfProxyInvocationCallback (line 785) handles proxy client invocation results; ClientFactoryBeanCache (line 881) implements LRU caching for JAXRSClientFactoryBean instances with cache lifecycle methods start/stop(). Private helper methods parse response headers, extract parameter types, build parameter maps from matrix/query strings.

---

### File 256
**Path:** `components/camel-crypto/src/main/java/org/apache/camel/component/crypto/DigitalSignatureProducer.java`  
**Total Lines:** 40

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 26 | DigitalSignatureProducer | class | public |

### Role Summary
Simple producer class wrapping a DigitalSignatureProcessor, delegating all processing to the processor instance. Extends DefaultProducer and accepts the endpoint and processor in its constructor. Single public method process(Exchange) delegates to processor.process(exchange). Minimal state: only holds reference to processor field.

---

### File 257
**Path:** `components/camel-cxf/camel-cxf-rest/src/main/java/org/apache/camel/component/cxf/jaxrs/CxfRsConsumer.java`  
**Total Lines:** 103

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 35 | CxfRsConsumer | class | public |

### Role Summary
REST consumer implementing Suspendable interface, manages JAX-RS server lifecycle for receiving and processing REST requests. Key method createServer() (lines 47-77) builds JAXRSServerFactoryBean with CxfRsInvoker, configures Bus, and applies UnitOfWorkCloserInterceptor for proper resource cleanup. doStart() (lines 80-86) initializes and starts server; doStop() (lines 89-96) cleanly shuts down and destroys server. Marked as hosted service with isHostedService() returning true.

---

### File 258
**Path:** `components/camel-cxf/camel-cxf-rest/src/main/java/org/apache/camel/component/cxf/jaxrs/CxfRsEndpoint.java`  
**Total Lines:** 911

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 78 | CxfRsEndpoint | class | public |
| 855 | InterceptorHolder | class | private static |

### Role Summary
Large complex endpoint class decorated with @UriEndpoint(scheme=SCHEME_CXF_RS) implementing HeaderFilterStrategyAware. Manages extensive configuration through 50+ @UriParam private fields controlling HTTP client behavior, SSL/TLS, headers, cookies, authentication, and response handling. Core responsibility: exposing JAX-RS REST services via Apache CXF or connecting to external REST services via CXF REST client. Factory methods createJAXRSServerFactoryBean() and createJAXRSClientFactoryBean() configure server/client infrastructure; ChainedCxfRsConfigurer applies SSL and hostname verification settings. InterceptorHolder nested class (line 855) extends AbstractBasicInterceptorProvider for interceptor management.

---

### File 260
**Path:** `components/camel-cxf/camel-cxf-soap/src/main/java/org/apache/camel/component/cxf/jaxws/CxfConsumer.java`  
**Total Lines:** 410

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 60 | CxfConsumer | class | public |
| 151 | CxfConsumerInvoker | class | private |

### Role Summary
SOAP consumer extending DefaultConsumer and implementing Suspendable interface for managing JAX-WS server lifecycle and receiving SOAP-based web service requests. Core responsibility: exposing SOAP services via Apache CXF. Key method createServer() (lines 79-116) configures JAXWSServerFactoryBean with bus, service endpoint, and interceptor chains. CxfConsumerInvoker inner class (line 151) implements Invoker interface for delegating SOAP invocations to the consumer's Processor. Marked as hosted service with isHostedService() returning true. Lifecycle methods doStart/doStop manage server initialization and shutdown with proper resource cleanup via UnitOfWorkCloserInterceptor.

---

### File 261
**Path:** `components/camel-cxf/camel-cxf-soap/src/main/java/org/apache/camel/component/cxf/jaxws/CxfEndpoint.java`  
**Total Lines:** 1568

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 135 | CxfEndpoint | class | public |
| 1247 | CamelCxfClientImpl | class | public |

### Role Summary
Large complex SOAP endpoint extending DefaultEndpoint and implementing AsyncEndpoint, HeaderFilterStrategyAware, Cloneable interfaces for both server-side WebService publishing and client invocation. Decorated with @UriEndpoint(scheme=SCHEME_CXF) supporting 50+ @UriParam configuration fields controlling data format (POJO/PAYLOAD/CXF_MESSAGE/RAW), WSDL handling, service/port QName resolution, interceptor chains (in/out/inFault/outFault), and features list. Core factory methods: createProducer() (line 251), createConsumer() (line 261), createClientFactoryBean() (line 443), createServerFactoryBean() (line 722). Manages Bus lifecycle (lines 1015-1047), handler chains, and CxfConfigurer chaining. Inner class CamelCxfClientImpl (line 1247) extends CXF ClientImpl for PAYLOAD mode support and exception handling. Comprehensive SSL/TLS support via SSLContextParameters, MTOM attachments, logging feature, schema validation.

---

### File 262
**Path:** `components/camel-cxf/camel-cxf-soap/src/main/java/org/apache/camel/component/cxf/jaxws/CxfProducer.java`  
**Total Lines:** 467

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 59 | CxfProducer | class | public |

### Role Summary
SOAP producer extending DefaultAsyncProducer for sending SOAP-based web service invocations to remote endpoints. Supports both synchronous and asynchronous invocation paths with timeout and callback mechanisms. Manages extensive client configuration including HTTP/SOAP methods, headers, attachments, SSL/TLS, authentication, and marshalling. Core methods: invoke() (lines 82-100) coordinates sync/async dispatching; invokeSync() (lines 103-122) executes synchronous calls; invokeAsync() (lines 125-135) dispatches asynchronous calls with timeout management. CxfProducerCallback inner class implements CXF callback interface for async response handling. Features SOAP/HTTP header processing, MTOM attachment support, exception translation to CxfOperationException, and message property propagation from SOAP response to Exchange headers.

---

### File 263
**Path:** `components/camel-cxf/camel-cxf-spring-rest/src/main/java/org/apache/camel/component/cxf/spring/jaxrs/CxfRsSpringEndpoint.java`  
**Total Lines:** 95

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 30 | CxfRsSpringEndpoint | class | public |

### Role Summary
Spring-specific REST endpoint extending CxfRsEndpoint and implementing BeanIdAware for managing JAX-RS REST services within a Spring application context. Primary responsibility: bridging Spring configuration with CXF REST endpoint beans, enabling Spring-managed (JAXRSServerFactoryBean and JAXRSClientFactoryBean) REST service exposure. Constructor (lines 35-43) accepts Spring-configured AbstractJAXRSFactoryBean, extracts address/features/properties, and delegates initialization. init() method (lines 45-53) stores bean reference and extracts BeanId from Spring context if available, creating ConfigurerImpl for Spring-based bean configuration. Override methods newJAXRSServerFactoryBean() (lines 56-59) and newJAXRSClientFactoryBean() (lines 62-65) delegate to stored Spring bean. setupJAXRSClientFactoryBean() (lines 68-74) applies Spring configurer before standard configuration. Helper newInstanceWithCommonProperties() (lines 86-94) creates SpringJAXRSClientFactoryBean instances with shallow field copy.

---

### File 264
**Path:** `components/camel-cxf/camel-cxf-spring-soap/src/main/java/org/apache/camel/component/cxf/spring/jaxws/CxfSpringEndpoint.java`  
**Total Lines:** 361

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 59 | CxfSpringEndpoint | class | public |

### Role Summary
Spring-specific SOAP endpoint extending CxfEndpoint and implementing ApplicationContextAware for managing SOAP web services within Spring application context. Primary responsibility: bridging Spring configuration with CXF SOAP endpoint, enabling Spring-managed service exposure. Key override methods: createClient() (lines 80-135) creates CXF Client with service class resolution and WSDL fallback to DefaultSEI; createServerFactoryBean() (lines 141-188) instantiates appropriate ServerFactoryBean based on data format and service annotations. Bean lifecycle management via setApplicationContext() (lines 272-279) ensures CXF Bus integration with Spring context, using BusWiringBeanFactoryPostProcessor to add default bus. Custom Bus management in getBus() (lines 286-299) creates SpringBusFactory-based bus with graceful shutdown support. enableSpringBusShutdownGracefully() (lines 311-359) registers ApplicationListener to coordinate Camel inflight message draining with Spring ContextClosedEvent for clean service shutdown. QName-based properties for service namespace/localName and endpoint namespace/localName configuration.

---

### File 265
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/DigitalOceanComponent.java`  
**Total Lines:** 49

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 29 | DigitalOceanComponent | class | @Component("digitalocean"), @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean component factory extending DefaultComponent for initializing producer-only endpoints managing DigitalOcean cloud infrastructure operations. Deprecated since 4.21. Single createEndpoint() method validates endpoint configuration by requiring either oAuthToken or digitalOceanClient instance, extracting remaining parameters into DigitalOceanConfiguration, and returning DigitalOceanEndpoint. Component registers dynamically via @Component annotation supporting unified endpoint URI scheme "digitalocean://operation/resource" syntax for accessing supported resources.

---

### File 266
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/DigitalOceanConfiguration.java`  
**Total Lines:** 171

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 29 | DigitalOceanConfiguration | class | @UriParams, @Deprecated(since = "4.21"), public |

### Role Summary
Configuration POJO class annotated with @UriParams for endpoint URI parameter binding, supporting all DigitalOcean operation types and resource selections. Key annotated fields: @UriPath operation (routing to account/actions/blocks/droplets/floatingips/images/keys/regions/sizes/snapshots/tags), @UriParam resource, oAuthToken (secret), digitalOceanClient, page, perPage; HTTP proxy configuration (proxyHost, proxyPort, proxyUser, proxyPassword). Validates authentication requirement: either oAuthToken or digitalOceanClient must be provided. All fields expose corresponding getters/setters for programmatic access.

---

### File 267
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/DigitalOceanEndpoint.java`  
**Total Lines:** 154

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 55 | DigitalOceanEndpoint | class | @UriEndpoint(scheme = "digitalocean", ...), @Deprecated(since = "4.21"), public |

### Role Summary
Producer-only endpoint extending DefaultEndpoint and implementing EndpointServiceLocation for managing DigitalOcean cloud infrastructure invocations. Decorated with @UriEndpoint (scheme=digitalocean, producerOnly=true, category=CLOUD/MANAGEMENT). Stores DigitalOceanConfiguration and lazy-initialized DigitalOceanClient. Factory methods createProducer() routes to resource-specific producers (AccountProducer, ActionsProducer, BlockStoragesProducer, DropletsProducer, FloatingIPsProducer, ImagesProducer, KeysProducer, RegionsProducer, SizesProducer, SnapshotsProducer, TagsProducer) by inspecting configuration.resource value. Manages DigitalOceanClient lifecycle with proxy configuration support including HTTP BasicCredentialsProvider setup. Getter/setter access to configuration state.

---

### File 268
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanProducer.java`  
**Total Lines:** 54

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 33 | DigitalOceanProducer | class | @Deprecated(since = "4.21"), public, abstract |

### Role Summary
Abstract base producer class extending DefaultProducer for all DigitalOcean resource-specific producer implementations. Deprecated since 4.21. Stores protected DigitalOceanConfiguration and private DigitalOceanEndpoint references. Provides protected determineOperation() method resolving operation from Exchange headers (DigitalOceanHeaders.OPERATION) with fallback to configured default operation. Static LOG field for subclass logging. Subclasses override abstract process() method to handle operation-specific logic.

---

### File 269
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanAccountProducer.java`  
**Total Lines:** 41

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 28 | DigitalOceanAccountProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Account API operations, extending DigitalOceanProducer. Deprecated since 4.21. Single process() method invocation retrieves account information via getEndpoint().getDigitalOceanClient().getAccountInfo() and sets result in Exchange message body. Simplest producer implementation supporting read-only account info retrieval.

---

### File 270
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanActionsProducer.java`  
**Total Lines:** 73

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 33 | DigitalOceanActionsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Actions API supporting list and get operations, extending DigitalOceanProducer. Deprecated since 4.21. process() method routes via switch on determineOperation(): case list invokes getActions() fetching paginated available actions; case get invokes getAction() retrieving specific action by ID from DigitalOceanHeaders.ID. Exception handling throws IllegalArgumentException if required headers missing. LOG tracing for debugging support.

---

### File 271
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanBlockStoragesProducer.java`  
**Total Lines:** 289

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 41 | DigitalOceanBlockStoragesProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for BlockStorages (volumes) API supporting list, get, listSnapshots, create, delete, attach, detach, resize, listActions operations. Extends DigitalOceanProducer. Deprecated since 4.21. Large switch statement in process() routes operations to dedicated handler methods extracting required headers (ID, region, dropletId, size) with validation. Private methods coordinate DigitalOceanClient invocations returning Volume/Snapshots/Actions results to Exchange message body. Comprehensive error handling for missing header validation.

---

### File 272
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanDropletsProducer.java`  
**Total Lines:** 451

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 51 | DigitalOceanDropletsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
Most complex DigitalOcean producer managing Droplet API operations including list, get, create, update, delete, reboot, powerCycle, shutdown, powerOn, powerOff, restore, resetPassword, resize, rebuild, rename, changeKernel, enableIpv6, enablePrivateNetworking, takeSnapshot, transfer, convert operations. Extends DigitalOceanProducer. Deprecated since 4.21. Private field dropletId caches current droplet context. Large process() switch method routes operations to 30+ handler methods extracting required headers (ID, name, region, image, size, kernel, etc.) with comprehensive validation. Supports droplet creation via image ID or slug, kernel switching, IPv6 enabling, private networking setup, snapshot capture, region transfer, image conversion.

---

### File 273
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanFloatingIPsProducer.java`  
**Total Lines:** 166

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 36 | DigitalOceanFloatingIPsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Floating IPs API supporting list, create, get, delete, assign, unassign, listActions operations. Extends DigitalOceanProducer. Deprecated since 4.21. process() routes operations via switch statement to handler methods managing floating IP lifecycle. Support dual creation modes: attach to existing droplet (via dropletId) or reserve to region. Assignment/unassignment operations require both dropletId and floating IP address validation. listActions retrieves action history for specific floating IP. All operations return typed DigitalOcean API objects (FloatingIP, FloatingIPs, Delete, Action, Actions) to Exchange message body.

---

### File 274
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanImagesProducer.java`  
**Total Lines:** 196

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 38 | DigitalOceanImagesProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Images API supporting list, ownList, get, update, delete, transfer, convert, listActions operations. Extends DigitalOceanProducer. Deprecated since 4.21. Comprehensive image lifecycle management: list all/user images with optional type filtering (ActionType), retrieve by ID or slug, update name, delete, transfer to different region, convert to snapshot. listActions fetches action history for specific image ID. Validates required headers (ID/slug for get/delete, region for transfer) with IllegalArgumentException on missing values. All operations delegate to DigitalOceanClient API with results set to Exchange message body (Image/Images/Delete/Action/Actions types).

---

### File 275
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanKeysProducer.java`  
**Total Lines:** 156

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 34 | DigitalOceanKeysProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for SSH Keys API supporting list, create, get, update, delete operations. Extends DigitalOceanProducer. Deprecated since 4.21. Key identification supports dual lookup: by ID or fingerprint. Create operation requires NAME and KEY_PUBLIC_KEY headers constructing new Key object. Update operation requires NAME header. Get/delete operations validate header presence (ID or KEY_FINGERPRINT) with fallback logic. All operations delegate to DigitalOceanClient, returning Key/Keys/Delete objects to Exchange message body. LOG tracing for debugging.

---

### File 276
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanRegionsProducer.java`  
**Total Lines:** 41

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 28 | DigitalOceanRegionsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
Minimal DigitalOcean producer for Regions API, extending DigitalOceanProducer. Deprecated since 4.21. Single-operation implementation: process() fetches available regions via getAvailableRegions() with page configuration support, returning Regions list to Exchange message body. Supports pagination via configuration.getPage(). Read-only operation with no parameter validation required.

---

### File 277
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanSizesProducer.java`  
**Total Lines:** 41

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 28 | DigitalOceanSizesProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
Minimal DigitalOcean producer for Sizes API, extending DigitalOceanProducer. Deprecated since 4.21. Single-operation implementation: process() fetches available droplet sizes via getAvailableSizes() with page configuration support, returning Sizes list to Exchange message body. Supports pagination via configuration.getPage(). Read-only operation with no parameter validation required.

---

### File 278
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanSnapshotsProducer.java`  
**Total Lines:** 110

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 35 | DigitalOceanSnapshotsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Snapshots API supporting list, get, delete operations with type filtering (droplet vs. volume snapshots). Extends DigitalOceanProducer. Deprecated since 4.21. process() routes operations via switch: list operation branches on TYPE header supporting droplet/volume-specific snapshot retrieval or unified snapshot listing; get operation retrieves specific snapshot by ID; delete operation removes snapshot by ID. All operations validate required ID header with IllegalArgumentException on missing values. Returns Snapshots/Snapshot/Delete objects to Exchange message body. LOG tracing enabled.

---

### File 279
**Path:** `components/camel-digitalocean/src/main/java/org/apache/camel/component/digitalocean/producer/DigitalOceanTagsProducer.java`  
**Total Lines:** 101

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 34 | DigitalOceanTagsProducer | class | @Deprecated(since = "4.21"), public |

### Role Summary
DigitalOcean producer for Tags API supporting list, create, get, delete operations for resource tagging. Extends DigitalOceanProducer. Deprecated since 4.21. Create operation requires NAME header constructing new Tag object. Get/delete operations require NAME header validation. List operation fetches paginated tags via configuration.getPage() and configuration.getPerPage(). All operations delegate to DigitalOceanClient, returning Tag/Tags/Delete objects to Exchange message body. LOG tracing for debugging.

---

### File 280
**Path:** `components/camel-direct/src/main/java/org/apache/camel/component/direct/DirectComponent.java`  
**Total Lines:** 154

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 38 | DirectComponent | class | public |

### Role Summary
Direct component factory managing synchronous intra-CamelContext message routing. Extends DefaultComponent. Coordinates DirectEndpoint instances using ReentrantLock and Condition for thread-safe consumer registration/deregistration. Maintains active consumer count with state tracking to avoid repeated lock acquisitions during high-throughput scenarios. createEndpoint() instantiates DirectEndpoint; getConsumers() returns snapshot of registered consumers via atomic reference; addConsumer()/removeConsumer() manage lifecycle with lock-guarded updates. Essential for direct: scheme support enabling lightweight routing between camel routes running in same JVM context without external queuing overhead.

---

### File 281
**Path:** `components/camel-direct/src/main/java/org/apache/camel/component/direct/DirectConsumer.java`  
**Total Lines:** 86

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 28 | DirectConsumer | class | public |

### Role Summary
Direct consumer implementing Camel consumer contract with lifecycle awareness and suspension support. Extends DefaultConsumer, implements ShutdownAware and Suspendable interfaces. Self-registers/deregisters with parent DirectComponent on startup/shutdown via getComponent().addConsumer(this)/removeConsumer(this). Supports consumer suspension via suspend()/resume() methods delegating to endpoint. onShutdown() hook ensures graceful deregistration during framework shutdown. Minimal implementation focuses on lifecycle coordination; actual message delivery logic handled by DirectProducer calling registered consumers.

---

### File 282
**Path:** `components/camel-direct/src/main/java/org/apache/camel/component/direct/DirectConsumerNotAvailableException.java`  
**Total Lines:** 33

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | DirectConsumerNotAvailableException | class | public |

### Role Summary
Exception thrown when DirectProducer attempts routing to direct: endpoint with no active consumers registered. Extends CamelExchangeException preserving exchange context. Thrown during synchronous message delivery when getComponent().getConsumers() returns empty collection, indicating misconfigured route or consumer lifecycle issue. Provides explicit failure signal for debugging consumer availability problems in direct: routes; enables routes to handle missing consumer scenarios via onException handlers.

---

### File 283
**Path:** `components/camel-direct/src/main/java/org/apache/camel/component/direct/DirectEndpoint.java`  
**Total Lines:** 137

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 37 | DirectEndpoint | class | public |

### Role Summary
Synchronous direct-routing endpoint decorated with @UriEndpoint, extending DefaultEndpoint. Manages producer/consumer factories for direct: scheme URIs. createProducer() instantiates DirectProducer; createConsumer() instantiates DirectConsumer. Supports optional failIfNoConsumers boolean configuration defaulting to true, enforcing immediate failure on message send if no active consumers present. Essential endpoint configuration point for direct: routes; routes DSL invocations create DirectEndpoint instances with endpoint URI and component binding.

---

### File 284
**Path:** `components/camel-direct/src/main/java/org/apache/camel/component/direct/DirectProducer.java`  
**Total Lines:** 120

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 29 | DirectProducer | class | public |

### Role Summary
Asynchronous producer supporting both synchronous and asynchronous delivery paths for direct: endpoints. Extends DefaultAsyncProducer. process() implements dual delivery: attempts synchronous delivery via registered consumers, or routes to optional failoverproducer if no consumers and failIfNoConsumers disabled. processAsync() returns processor pattern for async execution. Manages thread-safe consumer delivery via DirectConsumer.getProcessor() invocation. Throws DirectConsumerNotAvailableException when no active consumers and failIfNoConsumers=true. Core routing engine for synchronous intra-CamelContext message flow.

---

### File 285
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/AbstractLifecycleAwareExchangeEventHandler.java`  
**Total Lines:** 74

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 26 | AbstractLifecycleAwareExchangeEventHandler | class | abstract |

### Role Summary
Base class implementing LifecycleAwareExchangeEventHandler interface for Disruptor event-driven messaging. Package-private scope indicates internal framework use only. Extends lifecycle coordination infrastructure with CountDownLatch-based start/stop synchronization. Subclasses override onEvent() to handle ExchangeEvent processing; awaitStarted()/awaitStopped() enable callers to wait for handler transitions. Provides foundation for DisruptorConsumer's ConsumerEventHandler and DisruptorReference's BlockingExchangeEventHandler implementations managing async event consumption lifecycle.

---

### File 286
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorComponent.java`  
**Total Lines:** 272

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 36 | DisruptorComponent | class | public |

### Role Summary
Disruptor component factory managing high-performance LMAX Disruptor-based asynchronous messaging infrastructure. Decorated with @Component("disruptor"). Extends DefaultComponent. Maintains DisruptorReference registry tracking per-endpoint Disruptor instances via ConcurrentHashMap. createEndpoint() instantiates DisruptorEndpoint; getDisruptor() returns or creates shared Disruptor reference. Manages Disruptor lifecycle (start/stop/shutdown) coordinating with registered endpoint consumers/producers. Critical for scalable async messaging with configurable wait strategies, buffer sizes, and multi-consumer orchestration patterns (pub-sub, worker pool).

---

### File 287
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorConsumer.java`  
**Total Lines:** 241

**Type Declarations:** 2 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 42 | DisruptorConsumer | class | public |
| 215 | ConsumerEventHandler | class | private |

### Role Summary
Disruptor consumer implementing Camel consumer contract with worker-pool load-balancing support via ordinal-based consumer assignment. Extends ServiceSupport, implements Consumer/Suspendable/ShutdownAware. Creates nested ConsumerEventHandler (extends AbstractLifecycleAwareExchangeEventHandler) for ring-buffer event processing. Associates handler ordinal enabling Disruptor ring buffer to route events across multiple consumers based on sequence modulo. Manages suspension/resumption via DisruptorEndpoint.pauseConsumer()/resumeConsumer(). Key orchestrator for multi-consumer scaling patterns (pub-sub replicates to all, worker-pool distributes to ordinal-matched consumer).

---

### File 288
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorEndpoint.java`  
**Total Lines:** 370

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 55 | DisruptorEndpoint | class | public |

### Role Summary
Async disruptor endpoint implementing AsyncEndpoint and MultipleConsumersSupport, decorated with @ManagedResource and @UriEndpoint. Extends DefaultEndpoint coordinating producer/consumer lifecycle with parent DisruptorComponent. Manages DisruptorReference for ring-buffer coordination, concurrency model selection (MultipleConsumersSupport), and publisher wait strategies. createProducer() instantiates DisruptorProducer; createConsumer() creates DisruptorConsumer with ordinal assignment. pauseConsumer()/resumeConsumer() delegate to DisruptorReference. Critical configuration point for async disruptor: routes; endpoint bindings define buffer size, wait strategy, producer type, and consumer coordination patterns.

---

### File 289
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorProducer.java`  
**Total Lines:** 221

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 38 | DisruptorProducer | class | public |

### Role Summary
Asynchronous producer for high-throughput event publishing to Disruptor ring buffer. Extends DefaultAsyncProducer. Wraps Exchange in ExchangeEvent container for ring-buffer compatibility. Publishes events via DisruptorReference with configurable wait strategies (blocking, sleeping, busy-spin, yielding) balancing latency/CPU trade-offs. process() routes synchronously for fire-and-forget; processAsync() enables true async completion callback handling. Error handling wraps exceptions in Disruptor event propagation. Supports both single and multi-producer modes via ProducerType configuration. Essential for scalable, low-latency asynchronous message routing.

---

### File 290
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorNotStartedException.java`  
**Total Lines:** 42

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 23 | DisruptorNotStartedException | class | public |

### Role Summary
Exception thrown when producer/consumer attempts Disruptor ring-buffer access without prior component startup or after shutdown. Extends Exception. Thrown by DisruptorReference when internal Disruptor state uninitialized or shutdown (ring buffer null). Signals lifecycle issues: premature message send before disruptor:// route started, or message arrival after graceful shutdown. Enables routes to distinguish Disruptor-specific failures (not ready/stopped) from general processing errors via exception type matching.

---

### File 291
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorProducerType.java`  
**Total Lines:** 45

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | DisruptorProducerType | enum | public |

### Role Summary
Enumeration wrapping LMAX Disruptor ProducerType constants (Single, Multi) for endpoint configuration. Maps to com.lmax.disruptor.ProducerType via valueOf() lookup. Single producer mode optimizes throughput for single-threaded publishing; Multi enables safe concurrent publishing from multiple threads with atomic compare-and-swap synchronization. Endpoint @UriParam disruptorProducerType property accepts SINGLE/MULTI literals parsed via this enum. Selection impacts ring-buffer contention, throughput, and latency characteristics in producer configuration.

---

### File 292
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorReference.java`  
**Total Lines:** 485

**Type Declarations:** 3 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 52 | DisruptorReference | class | public |
| 431 | BlockingExchangeEventHandler | class | private |
| 467 | DelayedExecutor | class | private static |

### Role Summary
Holder managing Disruptor ring-buffer instances with atomically swappable references enabling zero-downtime reconfiguration. Maintains shared Disruptor per unique ring-buffer key, coordinating producer/consumer handler lifecycle. Creates nested BlockingExchangeEventHandler (extends AbstractLifecycleAwareExchangeEventHandler) for handler-side event processing; nested DelayedExecutor (implements Executor) manages executor scheduling. publish() method publishes ExchangeEvent to ring buffer; start()/stop()/shutdown() coordinate lifecycle. Atomically swappable ring-buffer design enables Disruptor upgrade scenarios without dropping in-flight events. Critical infrastructure for stable high-performance async messaging.

---

### File 293
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/DisruptorWaitStrategy.java`  
**Total Lines:** 96

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 30 | DisruptorWaitStrategy | enum | public |

### Role Summary
Enumeration holding LMAX wait strategy options (Blocking, Sleeping, BusySpin, Yielding) for ring-buffer consumer coordination. Maps to com.lmax.disruptor.WaitStrategy implementations via valueOf() lookup. Blocking: threads wait on Lock/Condition (low CPU, higher latency); Sleeping: Thread.sleep() intervals (balanced); BusySpin: continuous polling (lowest latency, high CPU); Yielding: Thread.yield() (moderate latency/CPU). Endpoint @UriParam waitStrategy property accepts strategy literals parsed via this enum. Selection directly impacts consumer latency/throughput/CPU trade-offs critical for performance-sensitive disruptor: routes.

---

### File 294
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/ExchangeEvent.java`  
**Total Lines:** 49

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 24 | ExchangeEvent | class | public |

### Role Summary
Mutable reference container holding Exchange object for Disruptor ring-buffer immutability compatibility. Public class enabling DisruptorProducer to wrap Exchanges before ring-buffer publication. Provides exchange field access via getExchange()/setExchange() for ExchangeEventFactory pre-allocation patterns. Disruptor ring buffer recycles ExchangeEvent instances across cycles; Exchange references replaced atomically enabling zero-allocation event publishing. Essential adapter bridging Camel's exchange-centric model with Disruptor's reusable event instance design.

---

### File 295
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/ExchangeEventFactory.java`  
**Total Lines:** 34

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | ExchangeEventFactory | class | |

### Role Summary
Factory implementing com.lmax.disruptor.EventFactory<ExchangeEvent> for ring-buffer pre-allocation. Package-private scope indicates internal framework use. Singleton instance INSTANCE pre-instantiated for reuse. newInstance() returns blank ExchangeEvent objects; Disruptor populates ring buffer with pre-allocated instances during initialization, eliminating runtime allocation overhead. Critical for Disruptor zero-garbage design enabling ultra-low-latency event processing in producer/consumer loops.

---

### File 296
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/LifecycleAwareExchangeEventHandler.java`  
**Total Lines:** 122

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 28 | LifecycleAwareExchangeEventHandler | interface | |

### Role Summary
Interface fusing com.lmax.disruptor.EventHandler<ExchangeEvent> and com.lmax.disruptor.LifecycleAware contract with async coordination methods. Package-private scope. Extends both EventHandler (onEvent() callback) and LifecycleAware (onStart()/onShutdown() lifecycle hooks). Adds awaitStarted()/awaitStopped() methods with optional timeout support enabling callers to synchronize on handler transitions. Subclassed by AbstractLifecycleAwareExchangeEventHandler providing CountDownLatch-based coordination. Essential interface enabling producer/consumer lifecycle synchronization in high-performance async processing scenarios.

---

### File 297
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/SynchronizedExchange.java`  
**Total Lines:** 32

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | SynchronizedExchange | interface | public |

### Role Summary
Interface describing immutable Exchange container providing completion synchronization handles for multi-consumer scenarios. Public interface. getExchange() returns wrapped Exchange; consumed(Exchange result) notifies container that consumer completed processing; cancelAndGetOriginalExchange() restores original exchange context. Implementations (SingleConsumerSynchronizedExchange, MultipleConsumerSynchronizedExchange) handle single/multi-consumer completion semantics. Enables DisruptorConsumer multicast coordination tracking expected consumer count (multicast replication) against actual completion signals, supporting atomic result aggregation across ordinal-based worker pools.

---

### File 298
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/SingleConsumerSynchronizedExchange.java`  
**Total Lines:** 38

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | SingleConsumerSynchronizedExchange | class | public |

### Role Summary
SynchronizedExchange implementation for single-consumer delivery patterns. Extends AbstractSynchronizedExchange. consumed(Exchange result) unconditionally copies Exchange result body/headers to wrapped exchange via ExchangeHelper.copyResults(), then triggers synchronization callbacks. Optimized for point-to-point routing eliminating multi-consumer count tracking. EnablesDisruptorConsumer to deliver messages to exactly one consumer without coordination overhead. Used when DisruptorEndpoint operates in point-to-point mode (single ordinal consumer) vs. multicast pub-sub.

---

### File 299
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/MultipleConsumerSynchronizedExchange.java`  
**Total Lines:** 67

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 29 | MultipleConsumerSynchronizedExchange | class | public |

### Role Summary
SynchronizedExchange implementation for multi-consumer coordination tracking completion across ordinal-based worker pool or pub-sub replication. Extends AbstractSynchronizedExchange. Maintains AtomicInteger processedConsumers counter and AtomicBoolean resultHandled flag. consumed(Exchange result) increments counter; fires synchronization when counter equals expectedConsumers or exception encountered (resultHandled prevents duplicate exception handling). Mirrors SEDA multicast behavior where only exceptions trigger result aggregation; normal paths aggregate per-consumer results. cancelAndGetOriginalExchange() sets resultHandled preventing spurious synchronization on cancellation. Essential for multi-consumer coordination correctness.

---

### File 300
**Path:** `components/camel-disruptor/src/main/java/org/apache/camel/component/disruptor/AbstractSynchronizedExchange.java`  
**Total Lines:** 56

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 25 | AbstractSynchronizedExchange | class | abstract |

### Role Summary
Abstract SynchronizedExchange base class managing exchange context and completion synchronizations. Package-private scope. Stores wrapped Exchange and List<Synchronization> (handover from exchange completions) via constructor. getExchange() returns wrapped instance; cancelAndGetOriginalExchange() re-registers all handover synchronizations to wrapped exchange restoring completion cascade. performSynchronization() calls UnitOfWorkHelper.doneSynchronizations() triggering completion callbacks. Subclassed by SingleConsumerSynchronizedExchange and MultipleConsumerSynchronizedExchange with consumed() implementations varying completion tracking logic. Core infrastructure enabling Disruptor's async completion callback bridge to Camel's UnitOfWork pattern.

---

### File 301
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsComponent.java`  
**Total Lines:** 83

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 68 | DnsComponent | class | public |

### Role Summary
DNS component factory supporting DNS query operations via DNSJava library. Decorated with @Component("dns"). Extends DefaultComponent. createEndpoint() parses URI remaining segment as DnsType enum (dig, ip, lookup, wikipedia), instantiates DnsEndpoint with type binding. Supports four operation types: dig (advanced DNS queries), ip (hostname to IP lookup), lookup (DNS record lookup), wikipedia (DNS-based Wikipedia search via DNS shortcut). Essential entry point for dns: scheme URIs enabling routes to execute DNS queries returning results to Exchange message body.

---

### File 302
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsEndpoint.java`  
**Total Lines:** 75

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 34 | DnsEndpoint | class | public |

### Role Summary
DNS endpoint supporting four producer operation types (dig, ip, lookup, wikipedia) via factory pattern. Decorated with @UriEndpoint. Extends DefaultEndpoint. createProducer() switches on dnsType to instantiate specialized producer (DnsDigProducer, DnsIpProducer, DnsLookupProducer, DnsWikipediaProducer). createConsumer() throws UnsupportedOperationException (producer-only endpoint). Configures dnsType via @UriPath property. Essential configuration point for dns: routes binding operation type to endpoint URI scheme.

---

### File 303
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsType.java`  
**Total Lines:** 27

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 19 | DnsType | enum | public |

### Role Summary
Enumeration holding four DNS operation types: dig (advanced DNS queries via SimpleResolver), ip (hostname-to-IP resolution), lookup (DNS record retrieval), wikipedia (DNS-based Wikipedia search). Public enum enabling type-safe DnsEndpoint configuration binding. Parsed from URI remaining segment in DnsComponent.createEndpoint(). Each enum value maps to specialized producer implementation. Simple marker enum without constructor logic; enum constants enable switch-based producer instantiation pattern in DnsEndpoint.

---

### File 304
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsConstants.java`  
**Total Lines:** 51

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 21 | DnsConstants | class | public |

### Role Summary
Centralized DNS component constants and header metadata. Public utility class with protected constructor (utility pattern). Declares operation constants (OPERATION_DIG, OPERATION_IP, OPERATION_LOOKUP, OPERATION_WIKIPEDIA) mapping to DnsType enum names. Defines header constants with @Metadata decorations for documentation: DNS_NAME/DNS_TYPE/DNS_CLASS (lookup/dig), DNS_DOMAIN (ip), DNS_SERVER (dig), TERM (wikipedia). Metadata annotations enable tooling to discover required/optional headers and types for each operation. Essential reference for route developers configuring DNS queries via Exchange headers.

---

### File 305
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsDigProducer.java`  
**Total Lines:** 69

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 34 | DnsDigProducer | class | public |

### Role Summary
DNS dig-like query producer supporting advanced DNS lookups via DNSJava SimpleResolver and Message APIs. Extends DefaultProducer. process() constructs query from Exchange headers: DNS_SERVER (resolver endpoint, optional default system resolver), DNS_TYPE (query type - A/MX/etc, defaults to A), DNS_CLASS (DNS class - IN/CH/etc, defaults to IN), DNS_NAME (query name, required). Invokes resolver.send(query) returning Message response set to Exchange body. Inspired by dig(1) command supporting complex DNS troubleshooting scenarios. Handles resolver instantiation and type resolution with fallback defaults.

---

### File 306
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsIpProducer.java`  
**Total Lines:** 44

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 29 | DnsIpProducer | class | public |

### Role Summary
DNS hostname-to-IP address resolution producer. Extends DefaultProducer. process() extracts DNS_DOMAIN header (required, validated via StringHelper.notEmpty()), calls InetAddress.getByName() performing system DNS resolution, sets resulting InetAddress object to Exchange body. Minimal implementation focused on simplicity; delegates to Java InetAddress APIs eliminating DNSJava dependency for basic IP lookups. Error thrown if DNS_DOMAIN header missing. Essential for simple hostname resolution scenarios in routes.

---

### File 307
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsLookupProducer.java`  
**Total Lines:** 65

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 31 | DnsLookupProducer | class | public |

### Role Summary
DNS record lookup producer supporting parameterized DNS queries via DNSJava Lookup API. Extends DefaultProducer. process() retrieves DNS_NAME header (required, validated), optional DNS_TYPE and DNS_CLASS headers. Constructs Lookup instance with name and optional type/class parameters. Executes lookup.run() (blocking operation); returns lookup.getAnswers() as Record[] array to Exchange body, or throws CamelException with lookup error string on failure. Supports single/dual-parameter Lookup constructor variations for flexible query configuration. Essential for DNS record retrieval (A/AAAA/MX/etc) operations.

---

### File 308
**Path:** `components/camel-dns/src/main/java/org/apache/camel/component/dns/DnsWikipediaProducer.java`  
**Total Lines:** 61

**Type Declarations:** 1 total

| Line | Name | Kind | Modifiers |
|------|------|------|-----------|
| 39 | DnsWikipediaProducer | class | public |

### Role Summary
DNS-based Wikipedia search producer via DNS TXT query shortcut to Wikipedia DNS gateway. Extends DefaultProducer. process() extracts TERM header (required search term), constructs DNS query name appending ".wp.dg.cx" (Wikipedia DNS gateway), sends TXT query via SimpleResolver. Parses response extracting answer section records; returns first record's rdata string (Wikipedia summary text) to Exchange body, or null if no answers. Demonstrates creative DNS repurposing for service integration; enables lightweight Wikipedia queries via DNS infrastructure without HTTP. Reference: commandlinefu.com DNS Wikipedia query technique.

---

## Operational Facts

F1: the build-freeze code is QK-4417
F2: the rollback window is 73 minutes
F3: the staging tenant is maroon-otter-19
F4: the payments cutover date is 2027-03-09
F5: the telemetry salt is v9u-KL22-xr
F6: the incident bridge channel is #inc-5561-bridge
F7: the canary abort threshold is 0.83%
F8: the artifact bucket is opal-crate-407
F9: the on-call rotation id is RTX-88-BLUE
F10: the postmortem doc code is PM-7Q-9932

---

## Architecture Synthesis

Apache Camel 4.21.0-SNAPSHOT is a polymorphic enterprise integration framework spanning 308 audited core and component modules. The architecture stratifies into five layers: (1) **Core APIs** (camel-api, camel-core) providing pluggable component lifecycle, Expression/Predicate evaluation, message routing, and lifecycle management through DefaultComponent/Endpoint/Producer/Consumer abstractions. (2) **DSL Infrastructure** (jbang, yaml-dsl, endpointdsl) enabling declarative route definition in XML, YAML, and fluent Java APIs. (3) **Component Ecosystem** (300+ integrated components: http, kafka, aws, ftp, dns, thrift, rest, etc.) implementing vendor-specific or protocol-specific message producers/consumers via standardized adapter interfaces. (4) **Runtime Services** (camel-management, camel-tracing, camel-jolokia) providing observability, JMX/telemetry instrumentation, and admin consoles. (5) **Security & Configuration** enforcing secrets management, TLS/SSL context parameters, header filtering strategies, and compliance-mode defaults via JSSE utilities and HeaderFilterStrategy pattern. Cross-cutting patterns include: reference counting (ConnectorRef in Jetty), thread-safe lock acquisition (ReentrantLock in SFTP), serialization registry (Thrift and Hessian marshalling), and URI-based configuration binding (@UriPath, @UriParam, @Metadata annotations). The framework delegates complex protocol logic to vendor libraries (JSch for SFTP, Jetty for HTTP, DNSJava for DNS, Thrift compiler-generated classes) while Camel provides uniform async/sync exchange marshalling, type conversion, error handling, and route orchestration. Deployment trust model assumes route authors and operators are trusted; external message senders are untrusted, enforcing data/code separation at consumer boundaries via HeaderFilterStrategy and expression sandboxing.
