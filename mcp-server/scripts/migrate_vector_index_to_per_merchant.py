"""
Migrate the legacy global FAISS index into the per-(merchant, strategy) layout.

Before this migration, ``UniversalEmbeddingStore`` wrote every merchant's
embeddings to a single global directory:

    mcp-server/vector_indices/mcp_index_<model>_<ts>.index
    mcp-server/vector_indices/mcp_ids_<model>_<ts>.pkl

and the legacy health check also probed::

    mcp-server/data/vector_cache/faiss_index.bin

After issue #56 the index lives under::

    mcp-server/data/merchants/<merchant_id>/<strategy>/faiss.bin
    mcp-server/data/merchants/<merchant_id>/<strategy>/ids.pkl

This script copies (not moves — we keep the legacy copy until a human says
it's safe to delete) the most recent legacy index into the default merchant's
per-strategy directory so the default merchant's retrieval stays green while
other merchants build their own.

Usage:
    python mcp-server/scripts/migrate_vector_index_to_per_merchant.py
    python mcp-server/scripts/migrate_vector_index_to_per_merchant.py --dry-run
    python mcp-server/scripts/migrate_vector_index_to_per_merchant.py --delete-legacy
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


DEFAULT_MERCHANT = "default"
DEFAULT_STRATEGY = "normalizer_v1"


def _repo_mcp_root() -> Path:
    """mcp-server/ root, regardless of where the script runs from."""
    return Path(__file__).resolve().parent.parent


def _find_legacy_paths(mcp_root: Path) -> tuple[Path | None, Path | None, Path | None]:
    """Return (latest_index_file, matching_ids_file, legacy_vector_cache_bin).

    Latest index file is picked by mtime among the ``vector_indices/*.index``
    files produced by the old ``_save_index`` path. Any of the three may be
    ``None`` if that legacy artifact is absent.
    """
    legacy_dir = mcp_root / "vector_indices"
    latest_idx: Path | None = None
    matching_ids: Path | None = None

    if legacy_dir.exists():
        index_files = list(legacy_dir.glob("mcp_index_*.index"))
        if index_files:
            latest_idx = max(index_files, key=lambda p: p.stat().st_mtime)
            candidate = legacy_dir / latest_idx.stem.replace("index", "ids")
            candidate = candidate.with_suffix(".pkl")
            if candidate.exists():
                matching_ids = candidate

    legacy_cache = mcp_root / "data" / "vector_cache" / "faiss_index.bin"
    legacy_cache = legacy_cache if legacy_cache.exists() else None

    return latest_idx, matching_ids, legacy_cache


def migrate(
    merchant_id: str = DEFAULT_MERCHANT,
    strategy: str = DEFAULT_STRATEGY,
    dry_run: bool = False,
    delete_legacy: bool = False,
) -> int:
    mcp_root = _repo_mcp_root()
    dest_dir = mcp_root / "data" / "merchants" / merchant_id / strategy
    dest_index = dest_dir / "faiss.bin"
    dest_ids = dest_dir / "ids.pkl"

    legacy_idx, legacy_ids, legacy_cache = _find_legacy_paths(mcp_root)

    # Prefer the timestamped vector_indices pair (it has a matching id-map).
    source_idx: Path | None = legacy_idx
    source_ids: Path | None = legacy_ids

    # Fall back to the data/vector_cache/faiss_index.bin sentinel. It has no
    # id-map sidecar so the destination index will be unusable at search time
    # until ``refresh_vector_index`` is run — but we still preserve the file
    # under the new path so ops scripts that look there keep working.
    if source_idx is None and legacy_cache is not None:
        source_idx = legacy_cache

    if source_idx is None and source_ids is None and legacy_cache is None:
        print(
            f"[skip] no legacy FAISS artifacts found under {mcp_root}; "
            "nothing to migrate."
        )
        return 0

    print(f"Migrating legacy FAISS index to per-merchant layout:")
    print(f"  merchant_id = {merchant_id}")
    print(f"  strategy    = {strategy}")
    print(f"  dest dir    = {dest_dir}")
    if source_idx:
        print(f"  source idx  = {source_idx}")
    if source_ids:
        print(f"  source ids  = {source_ids}")

    if dry_run:
        print("[dry-run] no files were copied.")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    if source_idx is not None:
        shutil.copy2(source_idx, dest_index)
        print(f"[copied] {source_idx} -> {dest_index}")
        copied += 1
    if source_ids is not None:
        shutil.copy2(source_ids, dest_ids)
        print(f"[copied] {source_ids} -> {dest_ids}")
        copied += 1

    if delete_legacy:
        for p in (legacy_idx, legacy_ids, legacy_cache):
            if p is not None and p.exists():
                p.unlink()
                print(f"[removed legacy] {p}")

    print(f"[done] {copied} file(s) migrated for ({merchant_id}, {strategy}).")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merchant", default=DEFAULT_MERCHANT)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="Delete the legacy files after copying. Off by default so the "
             "migration is reversible; re-run with this flag once search is "
             "verified against the new layout.",
    )
    args = parser.parse_args()
    migrate(
        merchant_id=args.merchant,
        strategy=args.strategy,
        dry_run=args.dry_run,
        delete_legacy=args.delete_legacy,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
