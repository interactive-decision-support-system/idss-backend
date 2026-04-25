"""HTTP scraper for the web-augmentation agent.

Tight v1 scope: respects robots.txt, on-disk cache (24h TTL), per-domain
rate limit, append-only query log. Allowed domains live in
config/scraper_sources.yaml — anything else is rejected.

The follow-up PR is expected to (a) move cache to Postgres,
(b) expand scraper_sources.yaml beyond a single domain,
(c) add a reviews/Q&A scraper agent that feeds soft_tagger_v1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.parse
import urllib.robotparser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_CACHE_ROOT = Path(__file__).resolve().parents[1] / "cache" / "scraped"
_LOG_PATH = Path(__file__).resolve().parents[1] / "cache" / "scraper_log.jsonl"
_DEFAULT_TTL_SECONDS = 24 * 3600
_DEFAULT_TIMEOUT_SECONDS = 10
_PER_DOMAIN_MIN_INTERVAL = 1.0  # seconds between requests to the same domain

_DOMAIN_LAST_HIT: dict[str, float] = {}
_DOMAIN_LOCK = threading.Lock()
_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}


@dataclass
class ScrapedDoc:
    url: str
    domain: str
    category: str
    status_code: int
    text: str
    fetched_at: str
    from_cache: bool


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "scraper_sources.yaml"


def _load_allowed_domains() -> dict[str, list[str]]:
    """Return category → list of allowed domain templates. Tiny YAML parser
    to avoid pulling pyyaml into requirements just for this; format is:

        electronics:
          - example-manufacturer.com
        laptop:
          - example-manufacturer.com
    """
    path = _config_path()
    if not path.exists():
        return {}
    out: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            current = line.rstrip(":").strip()
            out[current] = []
        elif current and line.lstrip().startswith("-"):
            out[current].append(line.lstrip("- ").strip())
    return out


def is_allowed(category: str, url: str) -> bool:
    allowed = _load_allowed_domains()
    domain = urllib.parse.urlparse(url).netloc.lower()
    candidates = allowed.get(category, []) + allowed.get("*", [])
    return any(domain == d or domain.endswith("." + d) for d in candidates)


# ---------------------------------------------------------------------------
# Cache + log
# ---------------------------------------------------------------------------


def _cache_path(category: str, url: str) -> Path:
    domain = urllib.parse.urlparse(url).netloc.lower()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _CACHE_ROOT / category / domain / f"{digest}.json"


def _read_cache(path: Path, ttl: int) -> ScrapedDoc | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["from_cache"] = True
        return ScrapedDoc(**data)
    except Exception as exc:  # noqa: BLE001 - corrupt cache shouldn't fail the run
        logger.warning("scraper_cache_read_failed: %s", exc)
        return None


def _write_cache(path: Path, doc: ScrapedDoc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(doc)
    payload["from_cache"] = False  # store the canonical version
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _append_log(record: dict[str, Any]) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Robots + rate limiting
# ---------------------------------------------------------------------------


def _robots_for(url: str, http_get: Any) -> urllib.robotparser.RobotFileParser:
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[base]
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(base + "/robots.txt")
    try:
        resp = http_get(base + "/robots.txt", timeout=_DEFAULT_TIMEOUT_SECONDS)
        if getattr(resp, "status_code", 0) == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])  # treat as fully allowed when robots.txt missing
    except Exception as exc:  # noqa: BLE001 - missing robots.txt = treat as allowed
        logger.info("scraper_robots_missing for %s: %s", base, exc)
        rp.parse([])
    _ROBOTS_CACHE[base] = rp
    return rp


def _wait_rate_limit(domain: str) -> None:
    with _DOMAIN_LOCK:
        last = _DOMAIN_LAST_HIT.get(domain, 0.0)
        delta = time.time() - last
        if delta < _PER_DOMAIN_MIN_INTERVAL:
            time.sleep(_PER_DOMAIN_MIN_INTERVAL - delta)
        _DOMAIN_LAST_HIT[domain] = time.time()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ScraperClient:
    """v1 scraper. Pass an httpx-style client (must expose .get(url, timeout=...))
    so tests can inject respx mocks without network."""

    def __init__(self, http_client: Any | None = None, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        if http_client is None:
            try:
                import httpx

                http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS, follow_redirects=True)
            except ImportError:
                http_client = None
        self._http = http_client
        self._ttl = ttl_seconds

    def fetch(self, url: str, *, category: str) -> ScrapedDoc | None:
        if not is_allowed(category, url):
            logger.info("scraper_blocked_by_allowlist", extra={"url": url, "category": category})
            _append_log(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "category": category,
                    "status": "blocked_allowlist",
                }
            )
            return None

        cache_path = _cache_path(category, url)
        cached = _read_cache(cache_path, self._ttl)
        if cached is not None:
            return cached

        if self._http is None:
            logger.warning("scraper_no_http_client — install httpx to enable network fetches")
            return None

        rp = _robots_for(url, self._http.get)
        if not rp.can_fetch("*", url):
            logger.info("scraper_blocked_by_robots", extra={"url": url})
            _append_log(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "category": category,
                    "status": "blocked_robots",
                }
            )
            return None

        domain = urllib.parse.urlparse(url).netloc.lower()
        _wait_rate_limit(domain)

        start = time.perf_counter()
        try:
            resp = self._http.get(url, timeout=_DEFAULT_TIMEOUT_SECONDS)
            latency_ms = int((time.perf_counter() - start) * 1000)
            text = getattr(resp, "text", "") or ""
            doc = ScrapedDoc(
                url=url,
                domain=domain,
                category=category,
                status_code=getattr(resp, "status_code", 0),
                text=text,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                from_cache=False,
            )
            _write_cache(cache_path, doc)
            _append_log(
                {
                    "ts": doc.fetched_at,
                    "url": url,
                    "category": category,
                    "status": "ok",
                    "status_code": doc.status_code,
                    "latency_ms": latency_ms,
                    "bytes": len(text),
                }
            )
            return doc
        except Exception as exc:  # noqa: BLE001 - log and return None, never crash the run
            logger.warning("scraper_fetch_failed: %s", exc)
            _append_log(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "category": category,
                    "status": "error",
                    "error": str(exc),
                }
            )
            return None
