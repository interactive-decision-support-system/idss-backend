#!/usr/bin/env python3
"""
Build FAISS index for IDSS vehicle semantic search.

Creates the index files expected by DenseEmbeddingStore:
- faiss_flat_all_mpnet_base_v2_v1.index
- vins_flat_all_mpnet_base_v2_v1.pkl
- metadata_flat_all_mpnet_base_v2_v1.pkl

Run from project root:
    python scripts/build_faiss_vehicle_index.py

Requires: sentence-transformers, faiss-cpu
"""

import pickle
import sqlite3
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "car_dataset_idss" / "uni_vehicles.db"
INDEX_DIR = PROJECT_ROOT / "data" / "car_dataset_idss" / "faiss_indices"
MODEL_NAME = "all-mpnet-base-v2"
INDEX_TYPE = "Flat"
VERSION = "v1"


def _vehicle_to_text(row: dict) -> str:
    """Build text representation of vehicle for embedding (matches dense_ranker query format)."""
    parts = []
    if row.get("make"):
        parts.append(str(row["make"]))
    if row.get("model"):
        parts.append(str(row["model"]))
    if row.get("year"):
        parts.append(str(row["year"]))
    if row.get("trim"):
        parts.append(str(row["trim"]))
    if row.get("body_style") or row.get("norm_body_type"):
        b = row.get("norm_body_type") or row.get("body_style")
        parts.append(f"{b} body style")
    if row.get("fuel_type") or row.get("norm_fuel_type"):
        f = row.get("norm_fuel_type") or row.get("fuel_type")
        parts.append(f"{f} fuel")
    if row.get("drivetrain"):
        parts.append(f"{row['drivetrain']} drivetrain")
    if row.get("transmission"):
        parts.append(f"{row['transmission']} transmission")
    if row.get("is_used") is not None:
        parts.append("used vehicle" if row["is_used"] else "new vehicle")
    if row.get("exterior_color"):
        parts.append(f"{row['exterior_color']} color")
    if row.get("engine"):
        parts.append(str(row["engine"]))
    return " ".join(parts) if parts else "vehicle"


def main():
    print("Building FAISS index for IDSS vehicles...")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"[FAIL] Vehicle database not found at {DB_PATH}")
        print("   Ensure data/car_dataset_idss/uni_vehicles.db exists.")
        sys.exit(1)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"   Index output: {INDEX_DIR}")

    # 1. Load all vehicles from SQLite
    print("\n1. Loading vehicles from database...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT vin, year, make, model, trim, body_style, drivetrain, engine,
               fuel_type, transmission, exterior_color, is_used,
               norm_body_type, norm_fuel_type, norm_is_used
        FROM unified_vehicle_listings
        WHERE vin IS NOT NULL AND vin != ''
        ORDER BY vin
    """).fetchall()
    conn.close()

    vehicles = [dict(row) for row in rows]
    vins = [v["vin"] for v in vehicles]
    print(f"   Loaded {len(vehicles)} vehicles")

    if not vehicles:
        print("[FAIL] No vehicles found in database.")
        sys.exit(1)

    # 2. Load SentenceTransformer and encode
    print("\n2. Loading embedding model and encoding vehicles...")
    try:
        from sentence_transformers import SentenceTransformer
        import faiss
    except ImportError as e:
        print(f"[FAIL] Missing dependency: {e}")
        print("   Run: pip install sentence-transformers faiss-cpu")
        sys.exit(1)

    model = SentenceTransformer(MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    print(f"   Model: {MODEL_NAME} (dim={dim})")

    texts = [_vehicle_to_text(v) for v in vehicles]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True)
    embeddings = embeddings.astype("float32")

    # 3. Build FAISS index
    print("\n3. Building FAISS index...")
    index = faiss.IndexFlatL2(dim)
    index.add(embeddings)
    print(f"   Index size: {index.ntotal} vectors")

    # 4. Save files
    model_slug = MODEL_NAME.replace("/", "_").replace("-", "_")
    index_path = INDEX_DIR / f"faiss_{INDEX_TYPE.lower()}_{model_slug}_{VERSION}.index"
    vins_path = INDEX_DIR / f"vins_{INDEX_TYPE.lower()}_{model_slug}_{VERSION}.pkl"
    metadata_path = INDEX_DIR / f"metadata_{INDEX_TYPE.lower()}_{model_slug}_{VERSION}.pkl"

    faiss.write_index(index, str(index_path))
    with open(vins_path, "wb") as f:
        pickle.dump(vins, f)
    with open(metadata_path, "wb") as f:
        pickle.dump({"model": MODEL_NAME, "version": VERSION, "count": len(vins)}, f)

    print("\n4. Saved files:")
    print(f"   {index_path}")
    print(f"   {vins_path}")
    print(f"   {metadata_path}")

    print("\n" + "=" * 60)
    print("[OK] FAISS index built successfully!")
    print("   Restart the MCP server to use the new index.\n")


if __name__ == "__main__":
    main()
