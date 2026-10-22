"""BASELINE (pre-optimization) copy of detection/engine.py, kept ONLY for A/B benchmarking.

Do not use this in the service and do not "fix" it: its whole purpose is to stay slow and unchanged so that
`python -m bench.benchmark` can compare it with the current detection/engine.py and prove two things:
(1) the optimized engine is faster, and (2) it returns the same results. Memory can be compared the same way:
`python -m bench.memory bench.engine_baseline` vs `python -m bench.memory detection.engine`
(run both from detection-service/).

What the engine does (hybrid detection, in the order analyze() runs it):
  1. validate / prepare : reject bad rows with a reason, drop duplicate tx_ids, keep SUCCESS rows, sort by time.
  2. causal features    : per-transaction behaviour (burst counts, inflow ratio, amount z-score ...) computed using
                          only EARLIER transactions, so nothing leaks from the future.
  3. rules              : burst, split (structuring), rapid_onward (pass-through), unusual_behavior (amount outlier),
                          and cycle (money going round a loop of accounts, found with networkx).
  4. IsolationForest    : an unsupervised anomaly score, trained on the chronologically earliest part of the data.
  5. risk score         : ML percentile * ml_weight + the weights of triggered rules, clipped to 0-100.
  6. explanations       : human-readable evidence per flagged transaction; evaluate() scores results against labels.

Main differences from the optimized engine (the things the benchmark measures):
  * _prior() re-sorts the events on every call; the optimized version sorts once in an _Events class and reuses it.
  * validate() assigns reject reasons with a Python loop over pandas masks; the optimized one uses a numpy code array.
  * detect_cycles() builds a networkx graph for every window and only then finds components; the optimized one
    first prunes edges that cannot lie on a cycle with numpy, and canonicalises cycle rotation.
  * IsolationForest is scored twice (train, then all rows), single-threaded, on float64 data.
  * pattern strings, timestamps and risk categories are built row by row in Python instead of with lookup tables / numpy.
"""
import time
import numpy as np, pandas as pd, networkx as nx
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, confusion_matrix

# ---- Configuration ------------------------------------------------------------------------------------------------
# Every tunable threshold lives here; callers may override keys via validate_config() (unknown keys are rejected).
DEFAULT_CONFIG = {
    # threshold: risk score (0-100) at or above which a transaction is "flagged".
    # train_fraction: share of the earliest transactions the IsolationForest is fitted on (the rest is a holdout).
    # ml_weight: how many risk points the ML anomaly percentile can add (percentile 1.0 -> +30).
    # weights: risk points added when each rule fires.
    "threshold": 50, "train_fraction": 0.6, "ml_weight": 30,
    "weights": {"cycle": 50, "rapid_onward": 40, "burst": 35, "split": 40, "unusual_behavior": 30},
    # burst: >= burst_count transfers by the same sender within burst_window_min minutes (including this one).
    "burst_window_min": 10, "burst_count": 8,
    # rapid_onward: the sender received 70%-150% of this amount in the last 30 minutes and this amount is >= 5000
    # (money pushed straight through an account; the upper ratio bound avoids unrelated large inflows).
    "rapid_window_min": 30, "rapid_min_amount": 5000, "rapid_ratio": 0.7, "rapid_ratio_max": 1.5,
    # split (structuring): >= 4 transfers sender->same receiver within 60 min, each <= 5000, totalling >= 10000
    # (a big payment broken into small ones).
    "split_window_min": 60, "split_count": 4, "split_cap": 5000, "split_min_total": 10000,
    # unusual_behavior: log-amount is >= 4 standard deviations above the sender's own history, needs >= 5 prior
    # transfers to have a history, and the amount must be >= 10000 (ignores outliers that are just small payments).
    "unusual_z": 4.0, "unusual_min_history": 5, "unusual_min_amount": 10000,
    # cycle: loops of 3-5 accounts within a 48h window, only counting transfers >= 1000. cycle_max_scc and
    # cycle_max_per_window are safety bounds on graph size and amount of search work (cycle enumeration can explode).
    "cycle_min_len": 3, "cycle_max_len": 5, "cycle_window_hours": 48, "cycle_min_amount": 1000,
    "cycle_max_scc": 60, "cycle_max_per_window": 500,
}
# Input columns every request must provide; extra columns are ignored.
REQUIRED = ["tx_id", "timestamp", "sender", "receiver", "amount", "status"]
# Columns of the feature table fed to the IsolationForest (other computed columns, e.g. pair_sum_win, are used
# by rules/explanations only).
FEATURES = ["log_amount", "amount_z", "sender_cnt_win", "sender_cnt_1d", "receiver_in_cnt_1d",
            "log_since_prev", "inflow_ratio", "pair_cnt_win", "hour"]
# Label / ground-truth columns that must never become model inputs (checked by tests/test_detection.py).
FORBIDDEN_FEATURES = {"tx_id", "scenario_id", "scenario_type", "is_suspicious", "is_benign_lookalike"}
# Multiplier that packs (key, time) into one sortable integer: key * BIG + timestamp. It only works because every
# timestamp (seconds since 1970) is far smaller than BIG, so keys never overlap; see _prior().
BIG = 10 ** 10
MAX_AMOUNT = 1e7  # reject absurd amounts instead of letting them distort log-features
TS_MIN, TS_MAX = pd.Timestamp("2000-01-01", tz="UTC"), pd.Timestamp("2100-01-01", tz="UTC")
# Reference point for converting timestamps to integer seconds.
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


# ---- Validation and preparation -----------------------------------------------------------------------------------
def validate(df):
    """Returns (valid rows, rejected rows with reason). Never silently drops."""
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    d = df[REQUIRED].copy()
    # errors="coerce" turns unparsable values into NaT/NaN instead of raising, so they can be reported per row.
    ts = pd.to_datetime(d["timestamp"], errors="coerce", utc=True)
    amt = pd.to_numeric(d["amount"], errors="coerce")
    # reason: "" means valid so far; the first failing check below writes its name here.
    reason = pd.Series("", index=d.index)
    # Checks are ordered by priority: a row failing several only gets the first reason (the `reason == ""` guard
    # in the loop stops later checks from overwriting it). Self-transfers (sender == receiver) count as invalid accounts.
    for name, bad in [("missing_tx_id", d.tx_id.isna()), ("invalid_timestamp", ts.isna()),
                      ("invalid_amount", amt.isna() | ~np.isfinite(amt) | (amt <= 0)),
                      ("amount_out_of_range", amt > MAX_AMOUNT),
                      ("timestamp_out_of_range", (ts < TS_MIN) | (ts > TS_MAX)),
                      ("invalid_account", d.sender.isna() | d.receiver.isna() | (d.sender == d.receiver))]:
        reason[(reason == "") & bad] = name
    ok = reason == ""
    # Convert to integer epoch seconds. Rejected rows get 0 (via .where) because NaT cannot be cast to int64; they
    # are removed on the last line anyway.
    d["ts"] = ((ts - EPOCH).dt.total_seconds().round()).where(ok, 0).astype("int64")
    d["amount"] = amt
    # Rejected rows are returned with the ORIGINAL columns plus the reason, so callers can show or log them.
    rejected = df.loc[~ok].assign(reject_reason=reason[~ok])
    return d[ok], rejected


def prepare(df):
    """Validate, de-duplicate and sort. Returns (clean rows, rejected rows, quality report dict)."""
    good, rejected = validate(df)
    # Count rows whose tx_id already appeared earlier (these will be dropped below).
    n_dup = int(good.duplicated("tx_id").sum())
    # "Conflicting" duplicates: same tx_id but different sender/receiver/amount/time. These suggest data corruption
    # rather than a harmless retry, so they are counted separately in the quality report.
    dups = good[good.tx_id.duplicated(keep=False)]
    n_conflict = int(dups.groupby("tx_id")[["sender", "receiver", "amount", "ts"]].nunique().gt(1).any(axis=1).sum()) if len(dups) else 0
    good = good.drop_duplicates("tx_id", keep="first")  # dedup strategy: first occurrence of tx_id wins
    # Only completed payments are analysed; failed ones are counted but excluded.
    n_failed = int((good.status != "SUCCESS").sum())
    # Sort by time, tx_id as tie-breaker, with a stable sort: identical input always gives identical order, which
    # makes every later step (features, cycles, ML) deterministic. Rows must be time-ordered for the causal features.
    good = good[good.status == "SUCCESS"].sort_values(["ts", "tx_id"], kind="stable").reset_index(drop=True)
    quality = {"rows_in": len(df), "rejected": len(rejected), "duplicates_removed": n_dup, "conflicting_duplicates": n_conflict,
               "failed_excluded": n_failed, "rows_analyzed": len(good)}
    return good, rejected, quality


# ---- Causal features ----------------------------------------------------------------------------------------------
def _prior(ev_c, ev_t, ev_v, q_c, q_t, w):
    """count/sum of events with the same key in [q_t - w, q_t). Strictly earlier => causal."""
    # Parameters: ev_* describe the EVENTS (key, time, value); q_* are the QUERIES (key, time); w is the window in seconds.
    # Trick: combine key and time into one number so a single sorted array groups events by key, then by time.
    comp = ev_c.astype(np.int64) * BIG + ev_t
    # BASELINE COST: this sort runs on every call, even when the same events are queried repeatedly.
    o = np.argsort(comp, kind="stable"); s = comp[o]
    # Same encoding for the queries. Searching for qc - w .. qc finds events of the same key in the time window;
    # "left" on the upper bound excludes events at exactly the query time (strictly earlier only => causal).
    qc = q_c.astype(np.int64) * BIG + q_t
    lo = np.searchsorted(s, qc - w, "left"); hi = np.searchsorted(s, qc, "left")
    # Prefix sums with a leading 0: the sum over events [lo, hi) is cs[hi] - cs[lo], so window sums cost O(1).
    cs = np.concatenate([[0.0], np.cumsum(ev_v[o])])
    return hi - lo, cs[hi] - cs[lo]


def build_features(df, cfg):
    """Point-in-time features: each row only uses rows strictly earlier in time (or itself)."""
    # Map account names to integer codes (fast numpy arithmetic instead of string comparisons). Senders and
    # receivers share one code space so an account is the same code in both roles.
    accts = pd.Index(pd.unique(pd.concat([df.sender, df.receiver])))
    sc, rc = accts.get_indexer(df.sender), accts.get_indexer(df.receiver)
    # t: timestamps (seconds), a: amounts as floats. Log of the amount tames the heavy right tail of payment sizes;
    # log1p(x) = log(1 + x) is safe for small x.
    t, a = df.ts.to_numpy(), df.amount.to_numpy(float)
    la = np.log1p(a)
    # Per-sender running statistics of log-amount WITHOUT using the current row ("prior" stats):
    # npr = number of earlier transfers by this sender; c1 / c2 = running sum / sum of squares, minus the current
    # row's own contribution (cumsum includes it, so we subtract it back out).
    g = pd.DataFrame({"s": sc, "x": la, "t": t})
    npr = g.groupby("s").cumcount().to_numpy()
    c1 = g.groupby("s")["x"].cumsum().to_numpy() - la
    c2 = (g.x ** 2).groupby(g.s).cumsum().to_numpy() - la ** 2
    # den avoids division by zero for a sender's first transfer. mean/std then follow from E[x] and E[x^2]
    # (variance = E[x^2] - mean^2; clip guards tiny negative values from float rounding).
    den = np.maximum(npr, 1)
    mean = c1 / den
    std = np.sqrt(np.clip(c2 / den - mean ** 2, 0, None))
    # z-score of this amount against the sender's own history. Needs a minimum history, otherwise 0 (no evidence).
    # std is floored at 0.5 so a sender with near-constant amounts does not get absurd z-scores from tiny deviations.
    z = np.where(npr >= cfg["unusual_min_history"], (la - mean) / np.maximum(std, 0.5), 0.0)
    # Seconds since the sender's previous transfer; 1e7 (about 4 months) stands in for "no previous transfer".
    since = g.groupby("s")["t"].diff().fillna(1e7).to_numpy()
    # Assemble the feature table, aligned with df rows. sender_prior_n is kept for explanations (not an ML feature).
    f = pd.DataFrame(index=df.index)
    f["log_amount"], f["amount_z"], f["sender_prior_n"] = la, z, npr
    f["log_since_prev"] = np.log1p(since)
    # Hour of day (UTC): night-time activity can be unusual.
    f["hour"] = (t // 3600) % 24
    # Windowed counts. +1 on sender_cnt_win counts the current transfer itself, so "burst_count" reads naturally
    # as "this many transfers in the window including this one". Windows in config are minutes -> *60 for seconds.
    c, _ = _prior(sc, t, a, sc, t, cfg["burst_window_min"] * 60); f["sender_cnt_win"] = c + 1
    f["sender_cnt_1d"], _ = _prior(sc, t, a, sc, t, 86400)
    f["receiver_in_cnt_1d"], _ = _prior(rc, t, a, rc, t, 86400)
    ic, isum = _prior(rc, t, a, sc, t, cfg["rapid_window_min"] * 60)  # money the sender received just before sending
    # Events = payments RECEIVED (keyed by receiver), queries = this row's SENDER: how much did the sender receive
    # recently, relative to what they are now sending. A ratio near 1 means almost everything is passed straight on.
    f["inflow_ratio"], f["inflow_cnt"] = isum / a, ic
    # Pair key: one integer per (sender, receiver) combination. Used for the splitting rule: many transfers on the
    # same pair in a short window. Totals include the current row (+1, + a).
    pc = sc.astype(np.int64) * len(accts) + rc
    c, sm = _prior(pc, t, a, pc, t, cfg["split_window_min"] * 60)
    f["pair_cnt_win"], f["pair_sum_win"] = c + 1, sm + a
    return f


# ---- Cycle detection (graph analysis) -----------------------------------------------------------------------------
def detect_cycles(df, cfg):
    """Retrospective graph analysis (uses a window that extends past T). Bounded: window, SCC size, length, count."""
    # found: tx_id -> set of cycles (tuples of accounts) that this transaction lies on.
    found = {}
    # Only meaningful amounts take part, which also shrinks the graph.
    sub = df[df.amount >= cfg["cycle_min_amount"]]
    if sub.empty:
        return found
    W, step = cfg["cycle_window_hours"] * 3600, 86400
    # W = window length in seconds, step = windows start one day apart (so they overlap by W - 1 day and a cycle
    # is unlikely to be cut in half by a window boundary). st = sorted timestamps for binary search.
    st = sub.ts.to_numpy()
    for start in range(int(st.min()), int(st.max()) + 1, step):
        # The transactions inside this window (found by binary search on the sorted timestamps).
        w = sub.iloc[np.searchsorted(st, start):np.searchsorted(st, start + W)]
        if len(w) < cfg["cycle_min_len"]:
            continue
        # Multi-graph collapsed to edges: (sender, receiver) -> list of tx_ids. Several payments on one edge are
        # all attributed to any cycle through that edge.
        edges = {}
        for tx, s, r in zip(w.tx_id, w.sender, w.receiver):
            edges.setdefault((s, r), []).append(tx)
        # BASELINE COST: the full graph is built for every window, with no pruning of edges that cannot be on a cycle.
        G = nx.DiGraph(list(edges))
        # n counts cycles found in this window, to enforce cycle_max_per_window.
        n = 0
        # Every cycle lies inside one strongly connected component (SCC), so search each SCC separately.
        for comp in nx.strongly_connected_components(G):
            if n >= cfg["cycle_max_per_window"]:
                break  # hard bound on work per window
            # Skip components too small to hold a long-enough cycle or so large that enumeration could explode.
            if not (cfg["cycle_min_len"] <= len(comp) <= cfg["cycle_max_scc"]):
                continue
            # length_bound caps the cycle length, which is what keeps cycle enumeration tractable.
            for cyc in nx.simple_cycles(G.subgraph(comp), length_bound=cfg["cycle_max_len"]):
                if len(cyc) < cfg["cycle_min_len"]:
                    continue
                n += 1
                # Attribute every transaction on each edge of the cycle (edge i goes cyc[i] -> next, wrapping round).
                # BASELINE: the cycle is stored as found, so the same cycle can appear under different rotations.
                for i, u in enumerate(cyc):
                    for tx in edges[(u, cyc[(i + 1) % len(cyc)])]:
                        found.setdefault(tx, set()).add(tuple(cyc))
                if n >= cfg["cycle_max_per_window"]:
                    break  # leaves only the inner loop; the outer loop re-checks the bound for the next component
    return found


# ---- Scoring ------------------------------------------------------------------------------------------------------
def _risk_category(r):
    # Score bands: >=70 critical, >=50 high, >=30 medium, otherwise low. Called once per row in the baseline.
    return "critical" if r >= 70 else "high" if r >= 50 else "medium" if r >= 30 else "low"


def validate_config(user):
    """Merge user overrides into defaults; raise ValueError (never a raw TypeError/KeyError) on bad input."""
    # Order of checks: type -> unknown keys -> per-key type/range -> cross-field constraints. Bad input is the
    # caller's mistake, so it always becomes ValueError (the Flask layer turns that into HTTP 400).
    if user is None:
        user = {}
    if not isinstance(user, dict):
        raise ValueError("config must be an object")
    unknown = set(user) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    # Copy defaults (weights copied separately because it is a nested dict that must not be mutated in place).
    cfg = {**DEFAULT_CONFIG, "weights": dict(DEFAULT_CONFIG["weights"])}
    # num: a real, finite number. bool is excluded because isinstance(True, int) is True in Python.
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)
    for k, v in user.items():
        # Weights merge key by key, so overriding one rule keeps the other defaults.
        if k == "weights":
            if not isinstance(v, dict) or set(v) - set(cfg["weights"]) or not all(num(x) and 0 <= x <= 100 for x in v.values()):
                raise ValueError("weights must map known patterns to numbers in [0, 100]")
            cfg["weights"].update(v)
        elif not num(v):
            raise ValueError(f"config '{k}' must be a finite number")
        else:
            cfg[k] = v
    # Cross-field sanity checks after merging: the limits also protect the server from expensive settings
    # (long cycles, huge windows, huge components).
    if not 0 <= cfg["threshold"] <= 100 or not 0 <= cfg["ml_weight"] <= 100:
        raise ValueError("threshold and ml_weight must be within [0, 100]")
    if not 0.1 <= cfg["train_fraction"] <= 0.9:
        raise ValueError("train_fraction must be within [0.1, 0.9]")
    for k in ("burst_window_min", "burst_count", "rapid_window_min", "split_window_min", "split_count", "cycle_window_hours", "cycle_max_scc", "cycle_max_per_window", "unusual_min_history"):
        if cfg[k] < 1:
            raise ValueError(f"{k} must be >= 1")
    if not 3 <= cfg["cycle_min_len"] <= cfg["cycle_max_len"] <= 6 or cfg["cycle_window_hours"] > 168 or cfg["cycle_max_scc"] > 200:
        raise ValueError("cycle bounds: 3 <= cycle_min_len <= cycle_max_len <= 6, cycle_window_hours <= 168, cycle_max_scc <= 200")
    if cfg["rapid_ratio_max"] < cfg["rapid_ratio"]:
        raise ValueError("rapid_ratio_max must be >= rapid_ratio")
    return cfg


def analyze(df, cfg=None):
    """Full pipeline: raw transactions DataFrame -> dict with scored rows, flagged rows + evidence, summary,
    data-quality report, rejected rows, warnings, per-stage timings, training cutoff and the effective config."""
    cfg = validate_config(cfg)
    # T collects per-stage wall-clock seconds; the benchmark reads it from result["timings"].
    T = {}; t0 = time.time()
    d, rejected, quality = prepare(df)
    warnings = []
    # Nothing valid to analyse: return a complete but empty result so callers need no special-casing.
    if d.empty:
        return {"scored": d, "flagged": [], "summary": {"records_analyzed": 0, "flagged": 0}, "quality": quality,
                "rejected": rejected, "warnings": ["no valid transactions"], "timings": {}, "cutoff": None, "config": cfg}
    f = build_features(d, cfg); T["features_s"] = time.time() - t0
    t1 = time.time(); cycles = detect_cycles(d, cfg); T["cycles_s"] = time.time() - t1
    # Each rule is a boolean array over all rows (True = rule fires for that transaction), using the thresholds in cfg.
    amt = d.amount.to_numpy()
    flags = {
        "burst": (f.sender_cnt_win >= cfg["burst_count"]).to_numpy(),
        "split": ((f.pair_cnt_win >= cfg["split_count"]) & (amt <= cfg["split_cap"]) & (f.pair_sum_win >= cfg["split_min_total"])).to_numpy(),
        "rapid_onward": ((f.inflow_ratio >= cfg["rapid_ratio"]) & (f.inflow_ratio <= cfg["rapid_ratio_max"]) & (amt >= cfg["rapid_min_amount"])).to_numpy(),
        "unusual_behavior": ((f.amount_z >= cfg["unusual_z"]) & (amt >= cfg["unusual_min_amount"])).to_numpy(),
        "cycle": d.tx_id.isin(cycles).to_numpy(),
    }
    # ML component: unsupervised anomaly score (no labels are used). Fit on the chronologically earliest part only,
    # so later rows are scored by a model that never saw them, then score everything.
    t2 = time.time(); X = f[FEATURES].to_numpy(float); n_tr = int(len(d) * cfg["train_fraction"])
    # cutoff: timestamp of the last training row; evaluate() only judges rows after it (a true holdout).
    cutoff = int(d.ts.iloc[max(n_tr - 1, 0)])
    # Too little training data makes IsolationForest unreliable: fall back to rules only (percentile 0) and warn.
    if n_tr < 200:
        pct = np.zeros(len(d)); warnings.append("fewer than 200 training rows: ML component disabled, rules only")
    else:
        m = IsolationForest(n_estimators=100, random_state=0).fit(X[:n_tr])
        # score_samples is higher for NORMAL points, so negate it: higher = more anomalous. tr = sorted training scores.
        # BASELINE COST: the training rows are scored here and then again in the next line (double work).
        tr = np.sort(-m.score_samples(X[:n_tr]))
        # Percentile of each row's anomaly score within the training distribution (0..1; a value near 1 means "more anomalous than almost all training rows"). Percentiles are easier to weigh
        # against rule weights than raw scores.
        pct = np.searchsorted(tr, -m.score_samples(X)) / n_tr
    T["ml_s"] = time.time() - t2
    # Final risk = ML contribution + sum of weights of rules that fired (booleans act as 0/1), capped to 0-100.
    # This is a ranking heuristic, not a probability (see the summary note).
    risk = cfg["ml_weight"] * pct + sum(cfg["weights"][p] * flags[p] for p in flags)
    risk = np.clip(risk, 0, 100)
    # Pattern label per row, e.g. "burst|split" or "" (BASELINE: Python loop over every row and rule).
    pats = np.array(["|".join(p for p in flags if flags[p][i]) for i in range(len(d))])
    # The per-transaction output table. Timestamps are turned back into ISO-8601 text for API consumers.
    scored = d[["tx_id", "sender", "receiver", "amount", "ts"]].assign(
        timestamp=pd.to_datetime(d.ts, unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        risk_score=np.round(risk, 1), risk_category=[_risk_category(r) for r in risk],
        ml_anomaly_percentile=np.round(pct, 3), patterns=pats, flagged=risk >= cfg["threshold"])
    # Only flagged rows get explanations, sorted so the most risky come first.
    flagged = [_explain(scored.iloc[i], f.iloc[i], cfg, cycles) for i in np.where(scored.flagged)[0]]
    flagged.sort(key=lambda x: -x["risk_score"])
    # Aggregate summary over flagged rows only.
    fl = scored[scored.flagged]
    summary = {"records_analyzed": len(d), "flagged": int(len(fl)), "threshold": cfg["threshold"],
               "accounts_involved": int(len(set(fl.sender) | set(fl.receiver))),
               "by_category": fl.risk_category.value_counts().to_dict(),
               "by_pattern": pd.Series([p for s in fl.patterns for p in s.split("|") if p], dtype=str).value_counts().to_dict(),
               "note": "risk_score is a heuristic 0-100 ranking score, NOT a fraud probability"}
    T["total_s"] = time.time() - t0
    return {"scored": scored, "flagged": flagged, "summary": summary, "quality": quality, "rejected": rejected,
            "warnings": warnings, "timings": {k: round(v, 2) for k, v in T.items()}, "cutoff": cutoff, "config": cfg}


def _explain(row, f, cfg, cycles):
    """Build the evidence list for one flagged transaction: one entry per triggered pattern with a readable sentence.
    row = scored row, f = its feature row, cycles = result of detect_cycles()."""
    out = []
    for p in [x for x in row.patterns.split("|") if x]:
        # win = the time window (minutes) the pattern was judged over; None for unusual_behavior (history-based).
        # cycle windows are configured in hours, hence *60. (The dict is indexed by the pattern name `p`.)
        win = {"burst": cfg["burst_window_min"], "split": cfg["split_window_min"], "rapid_onward": cfg["rapid_window_min"],
               "unusual_behavior": None, "cycle": cfg["cycle_window_hours"] * 60}[p]
        # lambdas delay formatting until the chosen pattern is selected with [p]() (only that one is evaluated; the
        # others would fail or waste work, e.g. cycles.get for a non-cycle row).
        txt = {"burst": lambda: f"{row.sender} made {int(f.sender_cnt_win)} transfers within {win} minutes",
               "split": lambda: f"{int(f.pair_cnt_win)} transfers <= {cfg['split_cap']} from {row.sender} to {row.receiver} within {win} min, total {f.pair_sum_win:.0f}",
               "rapid_onward": lambda: f"{row.sender} received {f.inflow_ratio:.0%} of this amount in the {win} minutes before sending it on",
               "unusual_behavior": lambda: f"amount is {f.amount_z:.1f} log-std above the sender's prior history ({int(f.sender_prior_n)} prior transfers)",
               "cycle": lambda: "part of cycle " + "; ".join(" -> ".join(c + (c[0],)) for c in sorted(cycles.get(row.tx_id, [])))}[p]()
        out.append({"pattern": p, "contribution": cfg["weights"][p], "accounts": [row.sender, row.receiver],
                    "window_minutes": win, "explanation": txt})
    # status says "unverified suspicion": the output is a model hint for human review, not a fraud verdict.
    return {"tx_id": row.tx_id, "timestamp": row.timestamp, "sender": row.sender, "receiver": row.receiver,
            "amount": float(row.amount), "risk_score": float(row.risk_score), "risk_category": row.risk_category,
            "ml_anomaly_percentile": float(row.ml_anomaly_percentile), "status": "model_suspicion_unverified", "evidence": out}


# ---- Evaluation against ground-truth labels -----------------------------------------------------------------------
def evaluate(result, labels):
    """Chronological holdout: only rows after the training cutoff. Labels are used here and nowhere else."""
    # No cutoff or no rows means there is nothing to evaluate: return the same shape with empty values.
    if result.get("cutoff") is None or result["scored"].empty:
        return {"records": 0, "positives": 0, "precision": None, "recall": None, "f1": None, "false_positive_rate": None,
                "precision_at_k": None, "k": 0, "pr_auc": None, "confusion_matrix": {"tp": 0, "fp": 0, "tn": 0, "fn": 0},
                "recall_by_scenario": {}, "benign_lookalike_flag_rate": None, "caveat": "no data"}
    # Attach labels by tx_id (left join, so unlabeled rows are kept and treated as negatives below), then keep only
    # the holdout: rows after the training cutoff, which the ML model never saw.
    m = result["scored"].merge(labels[["tx_id", "is_suspicious", "scenario_type", "is_benign_lookalike"]], on="tx_id", how="left")
    m = m[m.ts > result["cutoff"]]
    y = m.is_suspicious.fillna(0).astype(int).to_numpy(); sc = m.risk_score.to_numpy(); pred = m.flagged.to_numpy()
    # labels=[0, 1] keeps the matrix 2x2 even if one class is missing; ravel() order is tn, fp, fn, tp.
    tn, fp, fn, tp = confusion_matrix(y, pred.astype(int), labels=[0, 1]).ravel()
    # Safe division: None instead of ZeroDivisionError when the denominator is 0 (e.g. no predicted positives).
    div = lambda a, b: round(float(a) / b, 4) if b else None
    p, r = div(tp, tp + fp), div(tp, tp + fn)
    # precision@k: take the k highest-scored rows, where k = number of real positives, and see how many are real
    # (stable sort so ties break deterministically). Independent of the flagging threshold.
    k = int(y.sum()); top = np.argsort(-sc, kind="stable")[:k]
    # sus: only the truly suspicious rows, used for recall per scenario type.
    sus = m[m.is_suspicious == 1]
    # pr_auc (average precision) needs both classes present, hence the 0 < k < len(y) guard. f1 is 0.0 if p or r is
    # None/0. The caveat string reminds readers the data is synthetic.
    return {"records": int(len(m)), "positives": k, "precision": p, "recall": r,
            "f1": round(2 * p * r / (p + r), 4) if p and r else 0.0, "false_positive_rate": div(fp, fp + tn),
            "precision_at_k": div(y[top].sum(), k) if k else None, "k": k,
            "pr_auc": round(float(average_precision_score(y, sc)), 4) if 0 < k < len(y) else None,
            "confusion_matrix": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
            "recall_by_scenario": {t: round(float(g.flagged.mean()), 3) for t, g in sus.groupby("scenario_type")},
            "benign_lookalike_flag_rate": round(float(m[m.is_benign_lookalike == 1].flagged.mean()), 3) if (m.is_benign_lookalike == 1).any() else None,
            "caveat": "Synthetic data with injected scenarios; not evidence of real-world fraud detection performance."}
