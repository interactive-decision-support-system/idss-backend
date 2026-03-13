# Recommendations Evaluation: Case Report

This report summarizes the **recommendation pipeline** evaluation: Shopping Agent (query→UCP) match rate, Merchant Agent (UCP→recs) hard pass rate and mean similarity, and baseline comparison.

---

## Summary

- **Queries evaluated:** 12
- **Skipped (insufficient ground truth):** 38

### Shopping Agent (query→UCP)
- **Mean UCP match rate:** 0.775

### Baseline (query→UCP)
- **Mean UCP match rate:** 0.556

### Merchant Agent (UCP→recs)
- **Mean hard pass rate:** 0.083
- **Mean similarity:** 0.592

### Baseline (recs)
- **Mean hard pass rate:** 0.208
- **Mean similarity:** 0.558

---

## Queries with lowest UCP match rate (Shopping Agent)

### laptop_13

**Query:** Looking for a new work laptop under $1,000 USD with strong battery life (at least 6 hours) and high reliability. Prefer ...

**Expected UCP match:** 0.571

### laptop_48

**Query:** Need an inexpensive laptop ($500–600, USA) that can connect to a Dell WD15 USB-C dock and extend the desktop to two Dell...

**Expected UCP match:** 0.571

### laptop_18

**Query:** US aerospace engineering student with a ~$2000 budget wants a Windows 2-in-1 laptop with touchscreen/stylus note-taking ...

**Expected UCP match:** 0.667

### laptop_41

**Query:** Need a well-built laptop with great battery life for a Mechanical Engineering BCS, budget $1500–$2000, open to used/refu...

**Expected UCP match:** 0.667

### laptop_27

**Query:** Looking for a basic, cheap laptop under $350 (USD) for web browsing, Microsoft Office, and reading PDFs. Performance and...

**Expected UCP match:** 0.800

---

## Queries with zero hard pass rate (Merchant Agent)

These queries had no recommended products satisfying all hard constraints from expected_ucp.

- **laptop_1** — mean similarity: 0.644. "Need an HP laptop for everyday use under $650. Prefer a 15.6-inch touchscreen with at least 16GB RAM..."
- **laptop_5** — mean similarity: 0.579. "Looking for a Windows laptop for a neuroscience/business student under $1200 with a ~14-inch matte, ..."
- **laptop_7** — mean similarity: 0.601. "Need a portable ~14" laptop for an incoming biology major, with good battery life and a good keyboar..."
- **laptop_13** — mean similarity: 0.588. "Looking for a new work laptop under $1,000 USD with strong battery life (at least 6 hours) and high ..."
- **laptop_17** — mean similarity: 0.654. "Looking for a laptop in the US for $500 or less (refurb/used OK) that can run Foundry VTT at recomme..."
- **laptop_18** — mean similarity: 0.552. "US aerospace engineering student with a ~$2000 budget wants a Windows 2-in-1 laptop with touchscreen..."
- **laptop_40** — mean similarity: 0.546. "Need a Windows laptop for music production with some casual gaming. Prioritize overall performance a..."
- **laptop_41** — mean similarity: 0.538. "Need a well-built laptop with great battery life for a Mechanical Engineering BCS, budget $1500–$200..."

---

## Patterns

1. **Hard pass rate** is the fraction of recommended products that satisfy *all* hard constraints in expected_ucp (e.g. price_max_cents, brand, min_ram_gb, min/max_screen_size, good_for_*). It is often 0 because search (with relaxation) may return products that miss one or more of these constraints. Improving filter application or catalog coverage would increase hard pass rate.

2. **Mean similarity** measures how close recommended products are to the ground-truth set in embedding space (1 = closest). Merchant Agent typically outperforms the baseline when the Shopping Agent produces better-aligned filters.

3. **Shopping Agent (query→UCP) match rate** is key-wise agreement between produced filters and expected_ucp; mismatches often come from brand/screen/ram interpretation (e.g. agent says 14.0–14.5", expected 13.5–14.5").

---

## Baseline (query→UCP): lowest UCP match queries

- **laptop_1** — 0.444. "Need an HP laptop for everyday use under $650. Prefer a 15.6-inch touchscreen with at least 16GB RAM..."
- **laptop_18** — 0.500. "US aerospace engineering student with a ~$2000 budget wants a Windows 2-in-1 laptop with touchscreen..."
- **laptop_40** — 0.500. "Need a Windows laptop for music production with some casual gaming. Prioritize overall performance a..."
- **laptop_41** — 0.500. "Need a well-built laptop with great battery life for a Mechanical Engineering BCS, budget $1500–$200..."
- **laptop_47** — 0.500. "Looking for a well-built 15-inch laptop under $900 (US), preferably new (not refurb/used). Must supp..."

---

*Report generated from recommendation eval results (summary, query_to_ucp, ucp_to_recs, baseline, query_to_ucp_baseline).*
