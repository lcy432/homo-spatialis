"""M1 entry point: fetch and clean source statistics. Not implemented yet.

Planned behaviour (CLAUDE.md, 数据获取决策): OEWS by bulk download into
data/raw/oews/<vintage>/ recording URL and retrieval date, BEA RPP through the
API using BEA_API_KEY, idempotent so an existing file is skipped.
"""

import sys

print(__doc__)
print("M1 is not implemented. It is deliberately off the critical path: synthetic")
print("worlds supply their own city attributes, so M2-M5 do not wait on this.")
sys.exit(1)
