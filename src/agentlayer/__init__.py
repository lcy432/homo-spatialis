"""M4: the language-model layer.

Named agentlayer rather than agents: `agents` is a real top-level package name on
PyPI (the openai-agents SDK installs as `agents`), and a collision would be
resolved silently by import order.

One vendor-neutral OpenAI-compatible client for both bases, differing only in
base_url, api_key and model. Adapter signatures accept Observable and never
GroundTruth (CLAUDE.md hard rule 1)."""
