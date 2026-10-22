"""Peak-memory probe for a detection engine module.

Usage (run from detection-service/, in a FRESH process per module so the numbers are not polluted by earlier runs):

    python -m bench.memory detection.engine          # the current (optimized) engine
    python -m bench.memory bench.engine_baseline     # the pre-optimization engine, for comparison

Input: data/transactions_raw.csv, which is written by `python -m detection.run` (about 100k transactions).
Run that first, otherwise pd.read_csv raises FileNotFoundError. The path is relative, hence "run from detection-service/".

What it measures: the peak resident set size (RSS, physical RAM held by the process) before and after calling
analyze(). The difference ("growth") approximates the extra memory the analysis itself needed, excluding the
interpreter, libraries and the loaded CSV. Because the peak is a high-water mark that never decreases within a
process, this must be run once per module in a new process; running both engines in one process would mix them up.
"""
# sys: read the module name from the command line; resource: Unix-only access to the process's memory statistics
# (so this script does not work on Windows); importlib: import an engine whose name is only known at run time.
import sys, resource, importlib, pandas as pd
# Import the engine named in argv[1] (e.g. "detection.engine"); both engines expose the same analyze(df) function.
eng = importlib.import_module(sys.argv[1])
# Load the saved raw transactions; this is the realistic input the service would receive.
df = pd.read_csv("data/transactions_raw.csv")
# ru_maxrss is the PEAK RSS so far. On Linux it is reported in kilobytes, so dividing by 1024 gives megabytes
# (on macOS it is bytes, so the numbers would be off there). Wrapped in a lambda so it can be sampled twice.
mb = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
# Sample the peak after loading but BEFORE analysis, so growth isolates the cost of analyze() itself.
before = mb(); r = eng.analyze(df); after = mb()
# One summary line: baseline footprint, peak after analysis, their difference, and the row count as a sanity check
# that the engine actually processed the data.
print(f"{sys.argv[1]}: rss_after_load={before:.0f}MB peak_after_analyze={after:.0f}MB growth={after - before:.0f}MB rows={len(r['scored'])}")
