"""
Unit Tests for RCA Analyzer.

Tests all failure categories and edge cases.
"""

import pytest
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.rca_analyzer import RCAAnalyzer, RCACategory, RCASeverity


class TestRCAAnalyzer:
    """Test suite for RCA Analyzer."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.analyzer = RCAAnalyzer()
    
    # ========================================================================
    # Test NOT_FOUND failures
    # ========================================================================
    
    def test_not_found_analysis(self):
        """Test analysis of NOT_FOUND status."""
        response = {
            "status": "NOT_FOUND",
            "constraints": [{
                "code": "PRODUCT_NOT_FOUND",
                "message": "Product does not exist",
                "details": {"product_id": "VIN-12345"}
            }]
        }
        
        trace = {
            "timings_ms": {"total": 50, "db": 45},
            "sources": ["idss_sqlite"],
            "cache_hit": False
        }
        
        result = self.analyzer.analyze_failure(response, trace)
        
        assert result["category"] == RCACategory.RETRIEVAL_MISS
        assert result["severity"] == RCASeverity.MEDIUM
        assert "VIN-12345" in str(result["evidence"])
        assert len(result["suggested_fix"]) > 0
    
    # ========================================================================
    # Test OUT_OF_STOCK failures
    # ========================================================================
    
    def test_out_of_stock_analysis(self):
        """Test analysis of OUT_OF_STOCK status."""
        response = {
            "status": "OUT_OF_STOCK",
            "constraints": [{
                "code": "INSUFFICIENT_INVENTORY",
                "message": "Not enough stock",
                "details": {
                    "requested_qty": 5,
                    "available_qty": 2,
                    "product_id": "PROD-001"
                }
            }]
        }
        
        result = self.analyzer.analyze_failure(response)
        
        assert result["category"] == RCACategory.EXECUTION_CONSTRAINT
        assert result["severity"] == RCASeverity.HIGH
        assert "Requested 5" in str(result["evidence"])
        assert "2 available" in str(result["evidence"])
    
    # ========================================================================
    # Test INVALID (schema misuse) failures
    # ========================================================================
    
    def test_invalid_schema_analysis(self):
        """Test analysis of INVALID status (schema errors)."""
        response = {
            "status": "INVALID",
            "constraints": [{
                "code": "MISSING_REQUIRED_FIELD",
                "message": "Field 'product_id' is required",
                "details": {}
            }]
        }
        
        result = self.analyzer.analyze_failure(response)
        
        assert result["category"] == RCACategory.SCHEMA_MISUSE
        assert result["severity"] == RCASeverity.HIGH
        assert "required" in str(result["evidence"]).lower()
    
    # ========================================================================
    # Test NEEDS_CLARIFICATION
    # ========================================================================
    
    def test_needs_clarification_analysis(self):
        """Test analysis of NEEDS_CLARIFICATION status."""
        response = {
            "status": "NEEDS_CLARIFICATION",
            "constraints": [{
                "code": "AMBIGUOUS_QUERY",
                "message": "Please specify budget",
                "allowed_fields": ["price_min", "price_max"],
                "suggested_actions": ["What's your budget range?"]
            }]
        }
        
        result = self.analyzer.analyze_failure(response)
        
        assert result["category"] == RCACategory.GROUNDING_FAILURE
        assert result["severity"] == RCASeverity.MEDIUM
        assert "ambiguous" in str(result["evidence"]).lower()
    
    # ========================================================================
    # Test Latency Analysis
    # ========================================================================
    
    def test_latency_analysis_slow_db(self):
        """Test latency analysis with slow database."""
        response = {"status": "OK"}
        
        trace = {
            "timings_ms": {"total": 1500, "db": 1400, "cache": 10},
            "cache_hit": False
        }
        
        result = self.analyzer.analyze_failure(response, trace)
        
        assert result["category"] == RCACategory.LATENCY_ISSUE
        assert result["severity"] in [RCASeverity.MEDIUM, RCASeverity.HIGH]  # 1-2s range
        assert "Database query" in str(result["evidence"]) or "DB" in str(result["evidence"])
    
    def test_latency_analysis_cache_miss(self):
        """Test latency analysis with cache miss."""
        response = {"status": "OK"}
        
        trace = {
            "timings_ms": {"total": 1200, "db": 300, "cache": 20},
            "cache_hit": False
        }
        
        result = self.analyzer.analyze_failure(response, trace)
        
        assert result["category"] == RCACategory.LATENCY_ISSUE
        assert "Cache miss" in str(result["evidence"])
        assert any("cache" in fix.lower() for fix in result["suggested_fix"])
    
    def test_latency_analysis_no_issue(self):
        """Test latency analysis with acceptable performance."""
        response = {"status": "OK"}
        
        trace = {
            "timings_ms": {"total": 50, "db": 30, "cache": 5},
            "cache_hit": True
        }
        
        result = self.analyzer.analyze_failure(response, trace)
        
        assert result["category"] == "success"
        assert result["severity"] == RCASeverity.LOW
    
    # ========================================================================
    # Test Report Generation
    # ========================================================================
    
    def test_generate_report(self):
        """Test aggregated report generation."""
        # Analyze multiple failures
        failures = [
            {"status": "NOT_FOUND", "constraints": [{"code": "NOT_FOUND", "details": {"product_id": "1"}}]},
            {"status": "OUT_OF_STOCK", "constraints": [{"code": "OUT_OF_STOCK", "details": {"requested_qty": 5, "available_qty": 0}}]},
            {"status": "NOT_FOUND", "constraints": [{"code": "NOT_FOUND", "details": {"product_id": "2"}}]},
        ]
        
        results = [self.analyzer.analyze_failure(f) for f in failures]
        
        report = self.analyzer.generate_report(results)
        
        assert report["total_analyzed"] == 3
        assert RCACategory.RETRIEVAL_MISS in report["category_breakdown"]
        assert report["category_breakdown"][RCACategory.RETRIEVAL_MISS] == 2
        assert len(report["top_failures"]) > 0
    
    # ========================================================================
    # Test Edge Cases
    # ========================================================================
    
    def test_unknown_status(self):
        """Test handling of unknown status codes."""
        response = {"status": "UNKNOWN_STATUS"}
        
        result = self.analyzer.analyze_failure(response)
        
        assert result["category"] == RCACategory.UNKNOWN
        assert "Unknown status" in str(result["evidence"])
    
    def test_missing_trace(self):
        """Test handling of missing trace data."""
        response = {"status": "OK"}
        
        result = self.analyzer.analyze_failure(response, trace=None)
        
        assert result["category"] == "success"
    
    def test_empty_constraints(self):
        """Test handling of empty constraints."""
        response = {
            "status": "INVALID",
            "constraints": []
        }
        
        result = self.analyzer.analyze_failure(response)
        
        assert result["category"] == RCACategory.SCHEMA_MISUSE
        # Should not crash even with no constraints


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
