"""M1.5 entry point: build and freeze reference populations and quantile tables.

Planned behaviour (README §5.3): compute the empirical quantile function once
over the reference population, write it to data/frozen/<reference_id>.parquet,
and never recompute at query time -- a city whose quantile depends on who it is
compared against does not have a stable ground truth.
"""

import sys

print(__doc__)
print("M1.5 is not implemented.")
sys.exit(1)
