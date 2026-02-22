# MCP Pipeline Unit Test Coverage

## Total Tests: 48

### TestGetProduct (5 tests)
1. `test_get_product_mcp_protocol` - MCP protocol endpoint
2. `test_get_product_ucp_protocol` - UCP protocol endpoint
3. `test_get_product_tool_protocol` - Tool protocol endpoint
4. `test_get_product_not_found` - Error handling for non-existent products
5. `test_get_product_field_projection` - Field projection feature

### TestGetProductEdgeCases (5 tests)
6. `test_get_product_empty_product_id` - Empty product_id handling
7. `test_get_product_missing_product_id` - Missing required field validation
8. `test_get_product_invalid_fields` - Invalid field names in projection
9. `test_get_product_all_fields` - Verify all fields returned when no projection
10. `test_get_product_trace_information` - Trace information completeness

### TestSearchProducts (5 tests)
11. `test_search_products_mcp_protocol` - MCP protocol endpoint
12. `test_search_products_ucp_protocol` - UCP protocol endpoint
13. `test_search_products_tool_protocol` - Tool protocol endpoint
14. `test_search_with_hard_constraints` - Hard constraints (product_type, gpu_vendor, price_max)
15. `test_search_with_soft_constraints` - Soft constraints (implicit preferences)

### TestSearchProductsEdgeCases (10 tests)
16. `test_search_empty_query` - Empty query handling
17. `test_search_no_filters` - Search without filters
18. `test_search_invalid_limit` - Invalid limit validation
19. `test_search_zero_limit` - Zero limit rejection
20. `test_search_pagination_cursor` - Pagination with cursor
21. `test_search_multiple_hard_constraints` - Multiple hard constraints
22. `test_search_price_range` - Price range filtering
23. `test_search_brand_filter` - Brand filtering
24. `test_search_category_books` - Books category search
25. `test_search_response_structure` - Response structure validation
26. `test_search_trace_information` - Trace information completeness

### TestIDSSIntegration (3 tests)
27. `test_idss_recommendation_used_for_laptops` - IDSS routing for laptops
28. `test_idss_diversification` - IDSS diversification
29. `test_idss_semantic_similarity` - IDSS semantic similarity ranking

### TestLatencyMetrics (2 tests)
30. `test_get_product_latency` - get_product latency measurement
31. `test_search_products_latency` - search_products latency measurement

### TestAddToCart (5 tests)
32. `test_add_product_to_cart` - Basic add to cart functionality
33. `test_add_to_cart_ucp_protocol` - UCP protocol for add_to_cart
34. `test_add_to_cart_tool_protocol` - Tool protocol for add_to_cart
35. `test_add_to_cart_multiple_products` - Multiple products in cart
36. `test_add_to_cart_out_of_stock` - Out of stock handling

### TestProtocolCompatibility (3 tests)
37. `test_mcp_to_ucp_response_format` - MCP/UCP response format conversion
38. `test_tool_execute_error_handling` - Invalid tool name handling
39. `test_tool_execute_missing_parameters` - Missing parameters validation

### TestResponseEnvelope (3 tests)
40. `test_response_always_has_trace` - Trace information in all responses
41. `test_response_always_has_version` - Version information in all responses
42. `test_constraints_on_error` - Constraint details in error responses

### TestDomainDetection (3 tests)
43. `test_domain_detection_laptops` - Laptop domain detection
44. `test_domain_detection_books` - Book domain detection
45. `test_domain_detection_electronics` - Electronics domain detection

### TestQueryProcessing (3 tests)
46. `test_query_with_typos` - Typo correction in queries
47. `test_query_with_synonyms` - Synonym expansion
48. `test_complex_query_parsing` - Complex query parsing (multiple filters)

## Test Coverage Areas

### Protocols
-  MCP Protocol (native)
-  UCP Protocol (conversion)
-  Tool Protocol (tool execution)

### Endpoints
-  get_product (all protocols, edge cases)
-  search_products (all protocols, edge cases)
-  add_to_cart (all protocols, edge cases)

### Features
-  Hard constraints (product_type, gpu_vendor, price_max, brand)
-  Soft constraints (implicit preferences)
-  Field projection
-  Pagination
-  Error handling (NOT_FOUND, OUT_OF_STOCK)
-  Domain detection (laptops, books, electronics)
-  Query normalization (typos, synonyms)
-  Response envelope structure
-  Trace information
-  Latency metrics

### Integration
-  IDSS backend routing
-  IDSS recommendation algorithms
-  IDSS diversification

## Running Tests

```bash
# Run all tests
pytest mcp-server/tests/test_mcp_pipeline.py -v

# Run specific test class
pytest mcp-server/tests/test_mcp_pipeline.py::TestGetProduct -v

# Run with coverage
pytest mcp-server/tests/test_mcp_pipeline.py --cov=app --cov-report=html
```

## Test Statistics

- **Total Tests**: 48
- **Test Classes**: 10
- **Protocol Coverage**: 3 (MCP, UCP, Tool)
- **Endpoint Coverage**: 3 (get_product, search_products, add_to_cart)
- **Edge Cases**: 20+
- **Integration Tests**: 3
