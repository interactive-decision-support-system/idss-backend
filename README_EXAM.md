# IDSS Brand Exclusion Exam - Test Instructions

## Overview

This document describes how to run the brand exclusion test suite for the IDSS system.

## Test Group: brand_exclusion_exam

The `brand_exclusion_exam` test group contains 5 test cases that verify the system's ability to handle brand exclusions in various scenarios:

| # | Query | Description |
|---|-------|-------------|
| 44 | "I want a laptop, no mac" | Alias resolution test - "mac" should be normalized to "Apple" |
| 45 | "we hate ASUS, find me a gaming laptop" | Direct negation with use case |
| 46 | "steer clear of HP, bad experience" | Indirect phrasing exclusion |
| 47 | "I don't want a 14 inch screen" | Screen size exclusion |
| 48 | "I want a laptop, no mac" → "actually show me Apple" | Multi-turn brand override |

## Running the Tests

### Run All Brand Exclusion Exam Tests

```bash
python scripts/test_demo_queries.py --group brand_exclusion_exam
```

### Run Individual Tests

```bash
# Test 44: no mac alias resolution
python scripts/test_demo_queries.py --query 44

# Test 45: hate ASUS
python scripts/test_demo_queries.py --query 45

# Test 46: steer clear of HP
python scripts/test_demo_queries.py --query 46

# Test 47: 14 inch screen exclusion
python scripts/test_demo_queries.py --query 47

# Test 48: multi-turn brand override
python scripts/test_demo_queries.py --query 48
```

## Expected Results

All 5 tests should pass with 25 total checks:
- 5 domain checks
- 5 filter extraction checks
- 5 recommendation checks
- 5 non-empty response checks
- 5 brand exclusion verification checks

```
RESULTS: 25 checks passed | 0 failures | 0 warnings
All queries passed! 🎉
```


