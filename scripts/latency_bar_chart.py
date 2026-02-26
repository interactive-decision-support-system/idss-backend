#!/usr/bin/env python3
"""
Generate combined latency table (all operations).
For separate merchant/agent tables, use: python scripts/latency_tables.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import matplotlib.pyplot as plt
from latency_tables import ALL_DATA, COLUMNS, _make_table

if __name__ == "__main__":
    df = pd.DataFrame(ALL_DATA, columns=COLUMNS)
    fig, ax = plt.subplots(figsize=(7, 3.2))
    _make_table(ax, df, "Operation Latency")
    plt.tight_layout()
    plt.savefig("latency_table_all.png", bbox_inches="tight", dpi=300)
    plt.close()
    print("Saved: latency_table_all.png")
