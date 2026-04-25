"""Two orchestration modes share a single runner.

  - FixedOrchestrator: runs every assessor-recommended strategy on every product.
  - LLMOrchestrator: per product, asks an LLM which subset of strategies to run.

Both produce an OrchestratorPlan; the runner is mode-agnostic.
"""
