# Design decisions (ADRs)

English · [简体中文](README.zh-CN.md)

Each record explains why a design decision was made. Records are numbered and remain as written; a later decision identifies any earlier one it supersedes. The original records are in Chinese.

- 0001–0015: research methods, including leakage checks, collapse proportions, floor calibration, and estimation.
- 0016–0033, 0035, and 0037: the life engine and agent layer, formerly E1-1 through E1-7 and their extensions. Decision 0037 keeps only the product version.
- 0034: product intake; 0036: the offline interface prototype; 0045: the service-connected bilingual interface and its paper and globe views.

For reading routes, see the [documentation index](../docs/INDEX.en.md). The [module status log (Chinese)](../docs/INDEX.md#status) maps decisions to layers and development rounds.

Older references to README or PLAN sections retain their original section names. The [history index (Chinese)](../docs/history/README.md) locates those documents. New records link directly to the current topic guides.

**Development convention, since 2026-10-18:** write a short ADR for a substantive design decision, covering context, decision, and consequences. Acceptance checklists, trial results, and detailed implementation status belong elsewhere. Earlier records retain their implementation details and “awaiting review” notes as history.

**Run evidence stays local, since 2026-10-19:** `runs/` is not tracked in Git. Links to it in earlier records refer to evidence from the original machine. For older committed evidence, consult history before the `pre-legacy-removal` tag. Legacy configurations such as `synthetic_e1_*` and `run_e1_7b_*` were removed by [decision 0037 (Chinese)](0037-life-keep-only-product-version.md).
