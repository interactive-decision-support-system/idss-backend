"""
Query parser: extract structured hardware specs and use-case filters from
natural-language queries.

Handles Reddit-style complex queries like:
  "I need a laptop for web development and ML, at least 16 GB RAM,
   512 GB SSD, 15.6-inch screen, 8+ hours battery, under $2000"

Extracted specs are returned as additional filters that endpoints.py applies
against the kg_features JSON column on the products table.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


#  RAM extraction 

_RAM_PATTERNS = [
    # "at least 16 GB of RAM", "16GB RAM", "minimum 16 gigs ram"
    re.compile(
        r"(?:at\s+least|minimum|min\.?|>=?)\s*(\d{1,3})\s*(?:gb|gigs?)\s*(?:of\s+)?(?:ram|memory|ddr)",
        re.IGNORECASE,
    ),
    # "16 GB RAM", "32gb ram", "16 gigs of ram"
    re.compile(
        r"(\d{1,3})\s*(?:gb|gigs?)\s*(?:of\s+)?(?:ram|memory|ddr)",
        re.IGNORECASE,
    ),
]


def _extract_min_ram(text: str) -> Optional[int]:
    for pat in _RAM_PATTERNS:
        m = pat.search(text)
        if m:
            val = int(m.group(1))
            if 2 <= val <= 256:  # sanity: valid RAM range
                return val
    return None


#  Storage extraction 

_STORAGE_PATTERNS = [
    # "at least 512 GB SSD", "512GB storage", "minimum 1 TB"
    re.compile(
        r"(?:at\s+least|minimum|min\.?|>=?)\s*(\d{1,4})\s*(?:gb|tb)\s*(?:of\s+)?(?:ssd|storage|hard\s*drive|hdd|nvme|disk)",
        re.IGNORECASE,
    ),
    # "512 GB SSD", "1TB storage", "256gb ssd"
    re.compile(
        r"(\d{1,4})\s*(gb|tb)\s*(?:of\s+)?(?:ssd|storage|hard\s*drive|hdd|nvme|disk)",
        re.IGNORECASE,
    ),
]


def _extract_min_storage(text: str) -> Optional[int]:
    for pat in _STORAGE_PATTERNS:
        m = pat.search(text)
        if m:
            val = int(m.group(1))
            unit = m.group(2).lower() if m.lastindex >= 2 else "gb"
            if unit == "tb":
                val *= 1024
            if 64 <= val <= 8192:  # sanity: 64 GB to 8 TB
                return val
    return None


#  Screen size extraction 

_SCREEN_PATTERNS = [
    # '15.6"', '15.6-inch', '16 inch screen', '14" display'
    re.compile(
        r"(\d{2}(?:\.\d)?)\s*(?:\"|″|inch(?:es)?|-inch)\s*(?:screen|display|laptop)?",
        re.IGNORECASE,
    ),
    # "at least 15.6 inch"
    re.compile(
        r"(?:at\s+least|minimum|min\.?)\s*(\d{2}(?:\.\d)?)\s*(?:\"|″|inch(?:es)?|-inch)",
        re.IGNORECASE,
    ),
]


def _extract_min_screen(text: str) -> Optional[float]:
    for pat in _SCREEN_PATTERNS:
        m = pat.search(text)
        if m:
            val = float(m.group(1))
            if 10.0 <= val <= 21.0:  # sanity: valid laptop screen range
                return val
    return None


#  Battery life extraction 

_BATTERY_PATTERNS = [
    # "at least 8 hours battery", "8+ hours of battery life", "minimum 10 hour battery"
    re.compile(
        r"(?:at\s+least|minimum|min\.?|>=?)\s*(\d{1,2})\+?\s*(?:hours?|hrs?)\s*(?:of\s+)?(?:battery|batt)",
        re.IGNORECASE,
    ),
    # "8+ hours battery life", "8 hours of battery"
    re.compile(
        r"(\d{1,2})\+?\s*(?:hours?|hrs?)\s*(?:of\s+)?(?:battery|batt)",
        re.IGNORECASE,
    ),
]


def _extract_min_battery(text: str) -> Optional[int]:
    for pat in _BATTERY_PATTERNS:
        m = pat.search(text)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 30:  # sanity: valid battery range
                return val
    return None


#  Use-case extraction 

# Maps keyword phrases to canonical use-case tags (matching kg_features keys)
USE_CASE_MAP: Dict[str, str] = {
    # ML / deep learning
    "machine learning": "ml",
    "deep learning": "ml",
    "pytorch": "ml",
    "tensorflow": "ml",
    "ml": "ml",
    "ai ": "ml",
    "neural net": "ml",
    # Web development
    "web development": "web_dev",
    "web dev": "web_dev",
    "webflow": "web_dev",
    "figma": "web_dev",
    "frontend": "web_dev",
    "fullstack": "web_dev",
    "full stack": "web_dev",
    "react": "web_dev",
    "node": "web_dev",
    "xano": "web_dev",
    # Programming / general dev
    "programming": "programming",
    "pycharm": "programming",
    "python": "programming",
    "coding": "programming",
    "software development": "programming",
    "developer": "programming",
    "vscode": "programming",
    "ide": "programming",
    # Gaming
    "gaming": "gaming",
    "games": "gaming",
    "esports": "gaming",
    "steam": "gaming",
    "godot": "gaming",
    "unity": "gaming",
    "unreal": "gaming",
    # Creative / design
    "video editing": "creative",
    "photo editing": "creative",
    "photoshop": "creative",
    "premiere": "creative",
    "davinci resolve": "creative",
    "creative work": "creative",
    "3d modeling": "creative",
    "blender": "creative",
    "autocad": "creative",
    "cad": "creative",
    # GIS / scientific
    "qgis": "programming",
    "gis": "programming",
    "data science": "ml",
    "data analysis": "ml",
    "jupyter": "ml",
    # Linux
    "linux": "linux",
    "ubuntu": "linux",
    "fedora": "linux",
    "arch linux": "linux",
    "runs linux": "linux",
}


#  Year extraction 

_YEAR_PATTERN = re.compile(
    r"\b(20[12]\d)\b\s*(?:model|edition|laptop|macbook|notebook)?",
    re.IGNORECASE,
)


def _extract_year(text: str) -> Optional[int]:
    """Extract product/model year from query. e.g. '2024 laptop', 'laptop (2023)'."""
    m = _YEAR_PATTERN.search(text)
    if m:
        year = int(m.group(1))
        if 2015 <= year <= 2026:
            return year
    return None


#  Use-case extraction 

def _extract_use_cases(text: str) -> List[str]:
    """Extract canonical use-case tags from query text."""
    lower = text.lower()
    found: List[str] = []
    for phrase, tag in USE_CASE_MAP.items():
        if phrase in lower and tag not in found:
            found.append(tag)
    return found


#  Main entry point 

def enhance_search_request(
    normalized_query: str, filters: Dict[str, object]
) -> Tuple[str, Dict[str, object]]:
    """
    Parse hardware specs and use-cases from a natural-language query.

    Returns (cleaned_query, extra_filters) where extra_filters may contain:
      - min_ram_gb: int
      - min_storage_gb: int
      - min_screen_inches: float
      - min_battery_hours: int
      - min_year: int           e.g. 2024
      - use_cases: List[str]   e.g. ["ml", "web_dev", "linux"]
    """
    cleaned_query = (normalized_query or "").strip()
    if not cleaned_query:
        return cleaned_query, {}

    extra: Dict[str, object] = {}

    ram = _extract_min_ram(cleaned_query)
    if ram is not None:
        extra["min_ram_gb"] = ram

    storage = _extract_min_storage(cleaned_query)
    if storage is not None:
        extra["min_storage_gb"] = storage

    screen = _extract_min_screen(cleaned_query)
    if screen is not None:
        extra["min_screen_inches"] = screen

    battery = _extract_min_battery(cleaned_query)
    if battery is not None:
        extra["min_battery_hours"] = battery

    year = _extract_year(cleaned_query)
    if year is not None:
        extra["min_year"] = year

    use_cases = _extract_use_cases(cleaned_query)
    if use_cases:
        extra["use_cases"] = use_cases

    return cleaned_query, extra
