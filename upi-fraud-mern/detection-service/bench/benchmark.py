"""A/B benchmark: pre-optimization engine (bench/engine_baseline.py) vs the current engine (detection/engine.py).

Usage (run from detection-service/):

    python -m bench.benchmark

What it does:
  1. Generates one synthetic dataset of 100,000 transactions (fixed seed, so every run sees the same input).
  2. Runs analyze() from BOTH engines RUNS times on that same input, alternating between them each round.
  3. Verifies the two engines produce the same results, so the speed-up is not bought by changing behaviour.
  4. Prints a JSON report: median/min/max total time, per-stage median times, equality checks and the speed-up factor.

Why median and interleaving: a single timing is noisy (other processes, CPU frequency, caches). The median of
several runs ignores outliers, and alternating the engines makes slow moments hit both equally instead of
penalising whichever happened to run second.

LEGACY MODE: the baseline engine predates the IP / extra features and the irregular_ip rule. For a like-for-like
comparison the current engine is run with those switched off (LEGACY_CFG below: disabled_features + unreachable
IP thresholds), and only the baseline's columns / record keys are compared. The cost of the full feature set is
then reported separately as "full_features_total_s_median".

Equality checking is strict for the scored table (assert_frame_equal raises on any difference) and slightly
relaxed for the cycle explanation text, which may list the same cycle starting at a different account
(see norm() below). Takes a while: 2 engines x RUNS runs on 100k rows.
"""
# Note: `re` and `sys` are imported but not used by this script (left as-is). The others are used below:
# json for the report, time for timers, statistics for the median, importlib to load the engines by name.
import re, sys, json, time, statistics, importlib
import pandas as pd
from detection import generate, engine

# Configuration that makes the current engine behave like the old one: only the 9 CORE features and no irregular_ip rule
# (the IP thresholds are set so high that the rule can never fire; 1e7 is also the largest amount the engine accepts).
LEGACY_CFG = {"disabled_features": engine.FEATURES_IP + engine.FEATURES_EXTRA, "ip_other_min": 1e9, "ip_new_min_amount": 1e7}
# Number of timed rounds per engine. Odd, so the median is an actual measured value.
RUNS = 7
# Both engines expose the same analyze(df) function, so they can be driven through one loop. They are imported
# with importlib (by dotted name) so the dict is keyed by the label used in the report.
mods = {"baseline": importlib.import_module("bench.engine_baseline"), "optimized": importlib.import_module("detection.engine")}
# One shared input for both engines (10,000 accounts, 100,000 transactions); ["raw"] is the messy pre-cleaning
# data, so the benchmark includes validation and de-duplication, like a real request.
raw = generate.generate(10000, 100000, seed=42)["raw"]
# res: last result per engine (kept for the equality checks); times: wall-clock seconds per run;
# parts: each run's internal per-stage timing dict returned by analyze() under "timings".
res, times, parts = {}, {k: [] for k in mods}, {k: [] for k in mods}
for _ in range(RUNS):
    for k, m in mods.items():  # interleave to cancel machine noise
        # perf_counter is a monotonic high-resolution clock, the right tool for measuring elapsed time.
        t = time.perf_counter(); res[k] = m.analyze(raw, LEGACY_CFG) if k == "optimized" else m.analyze(raw); times[k].append(time.perf_counter() - t); parts[k].append(res[k]["timings"])
# Median rounded to milliseconds, for compact reporting.
med = lambda xs: round(statistics.median(xs), 3)


def norm(flagged, keys=None):  # cycle text differs only by rotation / duplicate listings of the same cycle: compare node sets
    # The baseline lists a cycle in whatever order the graph search found it (A->B->C vs B->C->A); the optimized
    # engine rotates it to a canonical start. Both describe the same cycle, so for comparison each cycle is reduced
    # to an unordered set of accounts, and a cycle explanation to a set of such sets (order and duplicates vanish).
    out = []
    for f in flagged:
        ev = []
        for e in f["evidence"]:
            if e["pattern"] == "cycle":
                # Explanation looks like "part of cycle A -> B -> C; D -> E -> F": strip the fixed prefix,
                # split the cycles on "; " and each cycle's nodes on " -> ".
                e = {**e, "explanation": frozenset(frozenset(c.split(" -> ")) for c in e["explanation"][len("part of cycle "):].split("; "))}
            elif e["pattern"] == "split":
                # The split text prints a rounded total ("total 19993"). The two engines add the amounts in a different
                # order, so a sum that lands exactly on x.5 can round either way (19992 vs 19993). That is a display-only
                # difference (the scored table is compared strictly above), so the digits are masked for the comparison.
                e = {**e, "explanation": re.sub(r"total \d+", "total N", e["explanation"])}
            ev.append(e)
        # keep only `keys` (the baseline's record fields): the current engine adds ip_address / device_type / channel
        out.append({**{k: v for k, v in f.items() if keys is None or k in keys}, "evidence": ev})
    return out


# Compare the final run of each engine. b = baseline result, o = optimized result.
b, o = res["baseline"], res["optimized"]
# Hard check: the full per-transaction table must match exactly (scores, flags, patterns, timestamps).
# If it does not, this raises AssertionError and no report is printed, so the report's True below is trustworthy.
pd.testing.assert_frame_equal(b["scored"], o["scored"][list(b["scored"].columns)])  # raises if any score/flag/pattern/timestamp differs
# Build the report. The nested comprehensions are:
#   total_s_median / total_s_min_max : wall-clock totals per engine
#   stage_s_median : for each engine, median seconds of each stage (stage names come from the first run's timings;
#                    the two engines do not report identical stage names because the optimized one splits stages finer)
# scored_frames_identical is a literal True because assert_frame_equal above would have raised otherwise.
report = {"runs": RUNS, "rows_analyzed": len(o["scored"]),
          "total_s_median": {k: med(v) for k, v in times.items()},
          "total_s_min_max": {k: [round(min(v), 3), round(max(v), 3)] for k, v in times.items()},
          "stage_s_median": {k: {s: med(p[s] for p in parts[k]) for s in parts[k][0]} for k in mods},
          "scored_frames_identical": True, "flagged_identical_after_cycle_norm": norm(b["flagged"]) == norm(o["flagged"], set(b["flagged"][0])),
          "summary_identical": b["summary"] == o["summary"], "flagged_count": len(o["flagged"])}
# Cost of the FULL feature set (23 features + IP rule), measured separately (3 runs, median).
full = []
for _ in range(3):
    t = time.perf_counter(); engine.analyze(raw); full.append(time.perf_counter() - t)
report["full_features_total_s_median"] = med(full)
# Speed-up factor: how many times faster the optimized engine is (>1 means faster), based on the medians.
report["speedup_x"] = round(report["total_s_median"]["baseline"] / report["total_s_median"]["optimized"], 2)
# default=str converts anything json cannot serialise (e.g. numpy scalars) to text instead of crashing.
print(json.dumps(report, indent=1, default=str))
