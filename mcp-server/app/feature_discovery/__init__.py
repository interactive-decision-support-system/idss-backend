"""feature_discovery — mine third-party shopper demand signal to learn
which product attributes the enrichment pipeline actually needs to
extract.

The enrichment agents (``specialist_v1``, ``soft_tagger_v1``, ...)
currently choose what features to emit from the LLM's training prior
plus a handful of handwritten prompt fragments (see
``enrichment/agents/specialist_prompts/``). That is fine as a starting
point but is demand-blind: it does not tell us whether the attributes
we emit are the ones real shoppers actually shop on.

Closing that loop requires a **merchant-independent** view of demand.
This package therefore reads only from sources that sit outside any
individual storefront — Reddit threads and external forums, with room
for YouTube comments / Quora / Trends behind the same adapter. We
deliberately exclude storefront-hosted Q&A and on-site reviews: those
are filtered by what a retailer chose to host and leak merchant-side
selection bias into what should be a neutral demand estimate.

Pipeline:

  1. harvest shopper-authored text from third-party sources scoped to
     a product_type,
  2. run an LLM extractor over each piece to pull out the attributes,
     use cases, hard constraints, and soft preferences the shopper is
     actually expressing,
  3. aggregate across N samples into a frequency map per product type,
     and
  4. diff that map against the attribute keys the existing enrichment
     pipeline populates in ``merchants.products_enriched_<merchant>``
     so we can see which real-world features are covered, which are
     missing, and which are over-emitted.

Output artifacts land under ``runs/feature_discovery/<category>/<ts>/``
as JSONL/JSON. This is a research prototype and is not wired into the
live enrichment runner.
"""
