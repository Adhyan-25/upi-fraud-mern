"""Hybrid detection engine.

This module is the "brain" of the whole project. Given a table of UPI-style
transactions it:

  1. VALIDATES rows (bad rows are returned with a reason, never silently dropped),
  2. DEDUPLICATES and removes FAILED transactions,
  3. builds CAUSAL behavioural features (a row only "sees" rows strictly earlier
     in time, so the model could be run online without peeking into the future),
  4. applies six RULE detectors:
        cycle            - money going round in a circle (A -> B -> C -> A)
        rapid_onward     - money received and passed on almost immediately
        burst            - many transfers from one sender in a few minutes
        split            - one big amount chopped into many small transfers
        unusual_behavior - an amount far above this sender's own history
        irregular_ip     - an IP address shared by many accounts, or a brand-new IP for an
                           established sender making a large transfer
  5. adds an unsupervised IsolationForest anomaly score,
  6. combines everything into a 0-100 heuristic RISK SCORE (NOT a probability),
  7. explains every flagged transaction in plain English, and
  8. can evaluate itself against synthetic ground-truth labels (chronological holdout).

Everything here is pure pandas / numpy / networkx / scikit-learn. There is no web
code in this file - app.py wraps it in a Flask API.
"""
import os, re, time
import numpy as np, pandas as pd, networkx as nx
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, confusion_matrix

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------
# Every tunable number lives in this one dict so callers (the API, the UI) can override
# any of them per request. validate_config() below checks every override.
DEFAULT_CONFIG = {
    # threshold  : risk score (0-100) at or above which a transaction is "flagged"
    # train_fraction : the earliest X% of rows (by time) that the IsolationForest is trained on
    # ml_weight  : maximum number of points the ML anomaly percentile can add (percentile * ml_weight)
    "threshold": 50, "train_fraction": 0.6, "ml_weight": 30,
    # points each rule adds to the risk score when it fires
    "weights": {"cycle": 50, "rapid_onward": 40, "burst": 35, "split": 40, "unusual_behavior": 30, "irregular_ip": 30},
    # burst: >= burst_count transfers by the same sender inside burst_window_min minutes
    "burst_window_min": 10, "burst_count": 8,
    # rapid onward: the sender received 70%-150% of this amount in the previous 30 minutes
    # (and the amount is at least 5000, so tiny pass-throughs are ignored)
    "rapid_window_min": 30, "rapid_min_amount": 5000, "rapid_ratio": 0.7, "rapid_ratio_max": 1.5,
    # split: >= 4 transfers of <= 5000 each between the SAME pair within 60 min, totalling >= 10000
    "split_window_min": 60, "split_count": 4, "split_cap": 5000, "split_min_total": 10000,
    # unusual behaviour: log-amount is >= 4 standard deviations above the sender's history
    # (needs >= 5 prior transfers and an absolute amount >= 10000)
    "unusual_z": 4.0, "unusual_min_history": 5, "unusual_min_amount": 10000,
    # cycle search: cycles of 3-5 accounts, all inside a 48h window, edges >= 1000 each.
    # cycle_max_scc / cycle_max_per_window are hard safety bounds on the amount of work done.
    "cycle_min_len": 3, "cycle_max_len": 5, "cycle_window_hours": 48, "cycle_min_amount": 1000,
    "cycle_max_scc": 60, "cycle_max_per_window": 500,
    # irregular IP (only active when the data has an `ip_address` column):
    #   (a) >= ip_other_min transfers by OTHER accounts came from this same IP in the previous ip_window_min minutes
    #       (an "account farm" / one operator driving many accounts), OR
    #   (b) the IP is new for this sender, who already has >= ip_new_min_history transfers, and the amount is >= ip_new_min_amount
    # (rule (a) additionally needs the amount to be >= ip_min_amount so tiny payments on a shared mobile IP are ignored)
    "ip_window_min": 120, "ip_other_min": 3, "ip_min_amount": 1000, "ip_new_min_history": 5, "ip_new_min_amount": 5000,
    # Feature selection switch: names listed here are REMOVED from the ML feature matrix (see ALL_FEATURES below).
    # Example: {"disabled_features": ["note_len", "app_major"]}. Can also be preset via env var UPI_DISABLED_FEATURES="a,b".
    "disabled_features": [x.strip() for x in os.environ.get("UPI_DISABLED_FEATURES", "").split(",") if x.strip()],
}
# Columns every input table MUST have.
REQUIRED = ["tx_id", "timestamp", "sender", "receiver", "amount", "status"]
# Optional columns. If present in the input they are carried through and turned into the IP / EXTRA
# feature groups below; if absent the engine behaves exactly as it did before they existed.
OPTIONAL = ["ip_address", "device_type", "app_version", "channel", "sender_bank", "receiver_bank",
            "sender_state", "receiver_state", "account_age_days", "kyc_level", "txn_note_len", "network_type"]
# Fixed category lists (a fixed order makes the numeric codes stable between runs and datasets).
DEVICES = ["android", "ios", "web"]
NETWORKS = ["wifi", "4g", "5g"]
BANKS = [f"BNK{i:02d}" for i in range(1, 9)]  # fictional bank codes
# The numeric features handed to the IsolationForest, in three groups.
# Group 1 - CORE: derived only from the 6 required columns.
FEATURES = ["log_amount", "amount_z", "sender_cnt_win", "sender_cnt_1d", "receiver_in_cnt_1d",
            "log_since_prev", "inflow_ratio", "pair_cnt_win", "hour"]
# Group 2 - IP: derived from `ip_address` (all causal: they only look at earlier rows).
FEATURES_IP = ["ip_is_new", "ip_other_tx_win", "ip_cnt_win", "ip_distinct_senders"]
# Group 3 - EXTRA: per-row attributes (device, channel, bank, state, account age ...). Some carry a little signal,
# some are pure noise - they exist so you can practise FEATURE SELECTION (see detection/crossval.py and the README).
FEATURES_EXTRA = ["device_code", "channel_collect", "cross_state", "log_account_age", "kyc_full",
                  "note_len", "network_code", "app_major", "sender_bank_code", "receiver_bank_code"]
FEATURE_GROUPS = {"core": FEATURES, "ip": FEATURES_IP, "extra": FEATURES_EXTRA}
ALL_FEATURES = FEATURES + FEATURES_IP + FEATURES_EXTRA
# Columns that exist only in the synthetic LABEL file. They must NEVER be used as features
# (a unit test asserts this) - otherwise the model would be cheating by reading the answer.
FORBIDDEN_FEATURES = {"tx_id", "scenario_id", "scenario_type", "is_suspicious", "is_benign_lookalike"}
# Multiplier used to pack (key, time) into one sortable int64. Accepted timestamps are < 2100-01-01 (4.1e9 s),
# so 2**32 (4.29e9) is enough; keys are DENSE ids (<= number of rows) so key * BIG never overflows int64,
# even with 1,000,000 rows and 100,000 accounts.
BIG = 1 << 32
# Threads used by scikit-learn when scoring; results are identical for any value.
N_JOBS = min(4, os.cpu_count() or 1)
# Reject absurd amounts instead of letting them distort the log-based features.
MAX_AMOUNT = 1e7
# Accepted timestamp range (anything outside is treated as a data error).
TS_MIN, TS_MAX = pd.Timestamp("2000-01-01", tz="UTC"), pd.Timestamp("2100-01-01", tz="UTC")
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


# --------------------------------------------------------------------------------------
# Step 1: validation
# --------------------------------------------------------------------------------------
def validate(df):
    """Split a raw table into (valid rows, rejected rows with a `reject_reason`).

    Nothing is silently dropped: every invalid row comes back to the caller with the
    first rule it violated so the UI can show it. Valid rows also get an integer `ts`
    column (seconds since the Unix epoch) used everywhere else for fast arithmetic.
    """
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    # keep the required columns plus whichever optional columns the input happens to have
    d = df[REQUIRED + [c for c in OPTIONAL if c in df.columns]].copy()
    # errors="coerce" turns unparseable values into NaT / NaN instead of raising
    ts = pd.to_datetime(d["timestamp"], errors="coerce", utc=True)
    amt = pd.to_numeric(d["amount"], errors="coerce")
    # Ordered list of (reason, boolean mask). Order = priority when a row breaks several rules.
    checks = [("missing_tx_id", d.tx_id.isna()), ("invalid_timestamp", ts.isna()),
                      ("invalid_amount", amt.isna() | ~np.isfinite(amt) | (amt <= 0)),
                      ("amount_out_of_range", amt > MAX_AMOUNT),
                      ("timestamp_out_of_range", (ts < TS_MIN) | (ts > TS_MAX)),
                      ("invalid_account", d.sender.isna() | d.receiver.isna() | (d.sender == d.receiver))]
    # `code[i]` = index (1-based) of the highest-priority failed check, 0 = row is fine.
    code = np.zeros(len(d), dtype=np.int8)
    for i in range(len(checks), 0, -1):  # earlier checks take priority, so assign them last
        code[checks[i - 1][1].to_numpy(dtype=bool, na_value=False)] = i
    reason = pd.Series(np.array([""] + [c[0] for c in checks], dtype=object)[code], index=d.index)
    ok = reason == ""
    # seconds since epoch as int64 (rejected rows get a harmless 0 so the cast never fails)
    d["ts"] = ((ts - EPOCH).dt.total_seconds().round()).where(ok, 0).astype("int64")
    d["amount"] = amt
    rejected = df.loc[~ok].assign(reject_reason=reason[~ok])
    return d[ok], rejected


# --------------------------------------------------------------------------------------
# Step 2: dedup + status filtering + ordering
# --------------------------------------------------------------------------------------
def prepare(df):
    """validate() + dedup + drop FAILED rows + sort by time. Returns (rows, rejected, quality report)."""
    good, rejected = validate(df)
    n_dup = int(good.duplicated("tx_id").sum())
    # same tx_id appearing more than once
    dups = good[good.tx_id.duplicated(keep=False)]
    # "conflicting" duplicates: same tx_id but different sender/receiver/amount/time (a data-quality red flag)
    n_conflict = int(dups.groupby("tx_id")[["sender", "receiver", "amount", "ts"]].nunique().gt(1).any(axis=1).sum()) if len(dups) else 0
    good = good.drop_duplicates("tx_id", keep="first")  # dedup strategy: first occurrence of tx_id wins
    n_failed = int((good.status != "SUCCESS").sum())
    # only SUCCESSful transfers moved money; sort by (time, id) so every later step can rely on order
    good = good[good.status == "SUCCESS"].sort_values(["ts", "tx_id"], kind="stable").reset_index(drop=True)
    quality = {"rows_in": len(df), "rejected": len(rejected), "duplicates_removed": n_dup, "conflicting_duplicates": n_conflict,
               "failed_excluded": n_failed, "rows_analyzed": len(good)}
    return good, rejected, quality


# --------------------------------------------------------------------------------------
# Step 3: causal windowed lookups
# --------------------------------------------------------------------------------------
class _Events:
    """Events keyed by account, sorted once, answering "how many / how much in the last w seconds?".

    Trick: each event is encoded as ONE integer `key * BIG + time`. Sorting those integers
    groups events by key and orders them by time inside each key. A window query then becomes
    two binary searches (np.searchsorted) plus a prefix-sum difference - O(log n) per row,
    fully vectorised. Queries cover [t - w, t): strictly EARLIER events only => causal.
    """

    def __init__(self, c, t, v):
        comp = c.astype(np.int64) * BIG + t
        o = np.argsort(comp, kind="stable")
        self.s = comp[o]                                   # sorted composite keys
        self.cs = np.concatenate([[0.0], np.cumsum(v[o])])  # prefix sums of the values (amounts)

    def prior(self, q_c, q_t, windows):
        """For each query (key q_c at time q_t) and each window w return (count, sum) of earlier events."""
        qc = q_c.astype(np.int64) * BIG + q_t
        hi = np.searchsorted(self.s, qc, "left")  # shared by every window: first event NOT strictly before the query
        out = []
        for w in windows:
            lo = np.searchsorted(self.s, qc - w, "left")  # first event inside the window
            out.append((hi - lo, self.cs[hi] - self.cs[lo]))
        return out


def _dense(values):
    """Turn any column of labels into dense integer ids 0..n-1; missing / empty values become -1."""
    s = pd.Series(values).astype(object)
    s = s.mask(s == "")  # an empty string counts as "missing", just like NaN / None
    return pd.factorize(s)[0].astype(np.int64)


def _category_code(col, categories):
    """Position of each value in a FIXED category list (stable codes between runs); unknown / missing => -1."""
    lookup = {c: i for i, c in enumerate(categories)}
    return pd.Series(col).map(lookup).fillna(-1).to_numpy(float)


def _major_version(col):
    """'5.12.3' -> 5. Parsed once per DISTINCT version string (a million rows have only a handful of versions)."""
    codes, uniq = pd.factorize(pd.Series(col).astype(object))
    major = np.array([int(m.group(1)) if (m := re.match(r"\s*v?(\d+)", str(u))) else 0 for u in uniq], dtype=float)
    return np.where(codes >= 0, major[codes] if len(major) else 0.0, 0.0)


def _number(col):
    """Column -> non-negative float array; missing / unparseable values become 0."""
    return np.clip(pd.to_numeric(pd.Series(col), errors="coerce").fillna(0).to_numpy(float), 0, None)


def build_features(df, cfg):
    """Point-in-time features: each row only uses rows strictly earlier in time (or itself).

    Returned columns (one row per transaction, same index as `df`):
      log_amount        log(1 + amount)
      amount_z          how many "log-standard-deviations" this amount is above the sender's own past mean
      sender_prior_n    how many transfers the sender made before this one
      log_since_prev    log seconds since the sender's previous transfer
      hour              hour of day (0-23, UTC)
      sender_cnt_win    sender's transfers in the burst window (including this one)
      sender_cnt_1d     sender's transfers in the previous 24h
      receiver_in_cnt_1d  transfers received by the receiver in the previous 24h
      inflow_ratio      money the sender RECEIVED in the rapid window / this amount
      inflow_cnt        number of such incoming transfers
      pair_cnt_win      transfers from this sender to this receiver in the split window (including this one)
      pair_sum_win      their total amount (including this one)

    IP group (all zero when the data has no `ip_address` column):
      ip_is_new            1 if this sender has never used this IP before
      ip_other_tx_win      transfers by OTHER accounts from the same IP in the previous `ip_window_min` minutes
      ip_cnt_win           transfers from the same IP in that window (any account, including this one)
      ip_distinct_senders  how many OTHER accounts have ever used this IP before (expanding, causal)

    EXTRA group (each is neutral / zero when its source column is missing): device_code, channel_collect,
    cross_state, log_account_age, kyc_full, note_len, network_code, app_major, sender_bank_code, receiver_bank_code.
    Some carry a little signal, some are noise - see FEATURES_EXTRA.
    """
    # Map account names to integers 0..n-1 (much faster than string comparisons)
    accts = pd.Index(pd.unique(pd.concat([df.sender, df.receiver])))
    sc, rc = accts.get_indexer(df.sender), accts.get_indexer(df.receiver)
    t, a = df.ts.to_numpy(), df.amount.to_numpy(float)
    la = np.log1p(a)
    g = pd.DataFrame({"s": sc, "x": la, "t": t})
    # --- running mean / std of the sender's PAST log-amounts (expanding window, current row excluded) ---
    npr = g.groupby("s").cumcount().to_numpy()                      # number of earlier transfers by this sender
    c1 = g.groupby("s")["x"].cumsum().to_numpy() - la                # sum of earlier log-amounts
    c2 = (g.x ** 2).groupby(g.s).cumsum().to_numpy() - la ** 2       # sum of earlier squared log-amounts
    den = np.maximum(npr, 1)
    mean = c1 / den
    std = np.sqrt(np.clip(c2 / den - mean ** 2, 0, None))           # var = E[x^2] - E[x]^2
    # z-score is only trusted once there is enough history; std floor 0.5 avoids dividing by ~0
    z = np.where(npr >= cfg["unusual_min_history"], (la - mean) / np.maximum(std, 0.5), 0.0)
    since = g.groupby("s")["t"].diff().fillna(1e7).to_numpy()        # first-ever transfer => "very long ago"
    f = pd.DataFrame(index=df.index)
    f["log_amount"], f["amount_z"], f["sender_prior_n"] = la, z, npr
    f["log_since_prev"] = np.log1p(since)
    f["hour"] = (t // 3600) % 24
    # --- windowed counts using the _Events helper ---
    ev_s, ev_r = _Events(sc, t, a), _Events(rc, t, a)              # events keyed by sender / by receiver
    (c, _), (f["sender_cnt_1d"], _) = ev_s.prior(sc, t, [cfg["burst_window_min"] * 60, 86400]); f["sender_cnt_win"] = c + 1
    (f["receiver_in_cnt_1d"], _), = ev_r.prior(rc, t, [86400])
    # money the sender received just before sending: query the RECEIVER index with the SENDER as key
    (ic, isum), = ev_r.prior(sc, t, [cfg["rapid_window_min"] * 60])
    f["inflow_ratio"], f["inflow_cnt"] = isum / a, ic
    # sender->receiver pair encoded as a single integer key. pd.factorize makes the id DENSE (<= number of rows),
    # which keeps `key * BIG` inside int64 even with 100,000 accounts (raw sender*n_accounts+receiver would overflow).
    pc = pd.factorize(sc.astype(np.int64) * len(accts) + rc)[0]
    (c, sm), = _Events(pc, t, a).prior(pc, t, [cfg["split_window_min"] * 60])
    f["pair_cnt_win"], f["pair_sum_win"] = c + 1, sm + a

    # ---------------- IP group ----------------
    n = len(df)
    zero = np.zeros(n)
    f["ip_is_new"] = f["ip_other_tx_win"] = f["ip_cnt_win"] = f["ip_distinct_senders"] = zero
    if "ip_address" in df.columns:
        ipc = _dense(df["ip_address"])                    # IP -> dense id (-1 = missing)
        has = ipc >= 0
        if has.any():
            w = cfg["ip_window_min"] * 60
            (cw, _), = _Events(ipc, t, a).prior(ipc, t, [w])               # earlier transfers from this IP in the window
            ipair = pd.factorize(ipc * len(accts) + sc)[0]                  # (IP, sender) pair as a dense id
            (pw, _), = _Events(ipair, t, a).prior(ipair, t, [w])           # ... of which by THIS sender
            first = ~pd.Series(ipair).duplicated().to_numpy()              # first time this sender uses this IP (row order)
            cum = pd.Series(first.astype(np.int64)).groupby(ipc).cumsum().to_numpy()  # distinct senders seen on the IP so far, incl. this one
            f["ip_is_new"] = (first & has).astype(float)
            f["ip_other_tx_win"] = np.where(has, cw - pw, 0.0)             # other accounts' transfers = all minus this sender's own
            f["ip_cnt_win"] = np.where(has, cw + 1, 0.0)
            f["ip_distinct_senders"] = np.where(has, cum - 1, 0.0)         # minus 1 = exclude this sender itself

    # ---------------- EXTRA group (per-row attributes) ----------------
    col = lambda name: df[name].to_numpy() if name in df.columns else None
    dev, ch, net, ver = col("device_type"), col("channel"), col("network_type"), col("app_version")
    f["device_code"] = _category_code(dev, DEVICES) if dev is not None else zero
    f["channel_collect"] = (pd.Series(ch).astype(str).str.lower() == "collect").to_numpy(float) if ch is not None else zero
    ss, rs = col("sender_state"), col("receiver_state")
    f["cross_state"] = ((pd.Series(ss).astype(str) != pd.Series(rs).astype(str)) & pd.Series(ss).notna() & pd.Series(rs).notna()).to_numpy(float) if ss is not None and rs is not None else zero
    f["log_account_age"] = np.log1p(_number(col("account_age_days"))) if "account_age_days" in df.columns else zero
    kyc = col("kyc_level")
    f["kyc_full"] = (pd.Series(kyc).astype(str).str.lower() == "full").to_numpy(float) if kyc is not None else zero
    f["note_len"] = _number(col("txn_note_len")) if "txn_note_len" in df.columns else zero
    f["network_code"] = _category_code(net, NETWORKS) if net is not None else zero
    f["app_major"] = _major_version(ver) if ver is not None else zero
    sb, rb = col("sender_bank"), col("receiver_bank")
    f["sender_bank_code"] = _category_code(sb, BANKS) if sb is not None else zero
    f["receiver_bank_code"] = _category_code(rb, BANKS) if rb is not None else zero
    return f


# --------------------------------------------------------------------------------------
# Step 4: cycle detection (graph analysis)
# --------------------------------------------------------------------------------------
def detect_cycles(df, cfg):
    """Find circular money flows. Returns {tx_id: {cycle tuples that transaction belongs to}}.

    RETROSPECTIVE: a 48h window starting at each day boundary also contains rows AFTER a
    given transaction, so cycle results are not comparable with the causal features.

    Bounded work: window size, strongly-connected-component size, cycle length and
    number of cycles per window are all capped. Before building any graph we iteratively
    prune edges that cannot be on a cycle (sender never receives / receiver never sends
    inside the window) using numpy - same cycles, far less work.
    """
    found = {}
    sub = df[df.amount >= cfg["cycle_min_amount"]]  # tiny transfers are ignored for cycles
    if sub.empty:
        return found
    st, tx = sub.ts.to_numpy(), sub.tx_id.to_numpy()
    names = pd.Index(pd.unique(pd.concat([sub.sender, sub.receiver]))); nm = names.to_numpy()
    sc, rc, n_nodes = names.get_indexer(sub.sender), names.get_indexer(sub.receiver), len(names)
    # slide a `W`-second window forward one day at a time
    W, step, min_len = cfg["cycle_window_hours"] * 3600, 86400, cfg["cycle_min_len"]
    for start in range(int(st.min()), int(st.max()) + 1, step):
        lo, hi = np.searchsorted(st, start), np.searchsorted(st, start + W)
        s_, r_, idx = sc[lo:hi], rc[lo:hi], np.arange(lo, hi)
        # iterative pruning: keep only edges whose sender HAS an incoming edge and whose receiver HAS an outgoing edge
        while len(s_) >= min_len:
            keep = (np.bincount(r_, minlength=n_nodes)[s_] > 0) & (np.bincount(s_, minlength=n_nodes)[r_] > 0)
            if keep.all():
                break
            s_, r_, idx = s_[keep], r_[keep], idx[keep]
        if len(s_) < min_len:
            continue
        # edges[(from, to)] = list of tx_ids on that edge (several transfers can share an edge)
        edges = {}
        for a_, b_, i in zip(s_, r_, idx):
            edges.setdefault((nm[a_], nm[b_]), []).append(tx[i])
        G = nx.DiGraph(list(edges))
        n = 0
        # a cycle can only live inside a strongly connected component
        for comp in nx.strongly_connected_components(G):
            if n >= cfg["cycle_max_per_window"]:
                break  # hard bound on work per window
            if not (min_len <= len(comp) <= cfg["cycle_max_scc"]):
                continue
            for cyc in nx.simple_cycles(G.subgraph(comp), length_bound=cfg["cycle_max_len"]):
                if len(cyc) < min_len:
                    continue
                n += 1
                # canonical rotation (start at the smallest account) so the same cycle always gets the same key
                k = cyc.index(min(cyc)); cyc = tuple(cyc[k:] + cyc[:k])
                # attach the cycle to every transaction that forms one of its edges
                for i, u in enumerate(cyc):
                    for t_ in edges[(u, cyc[(i + 1) % len(cyc)])]:
                        found.setdefault(t_, set()).add(cyc)
                if n >= cfg["cycle_max_per_window"]:
                    break
    return found


def _risk_category(r):
    """Map a 0-100 risk score to a human label."""
    return "critical" if r >= 70 else "high" if r >= 50 else "medium" if r >= 30 else "low"


# --------------------------------------------------------------------------------------
# Config validation
# --------------------------------------------------------------------------------------
def validate_config(user):
    """Merge user overrides into the defaults; raise ValueError (never a raw TypeError/KeyError) on bad input."""
    if user is None:
        user = {}
    if not isinstance(user, dict):
        raise ValueError("config must be an object")
    unknown = set(user) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    # copy defaults (and the nested weights dict / lists) so we never mutate DEFAULT_CONFIG
    cfg = {**DEFAULT_CONFIG, "weights": dict(DEFAULT_CONFIG["weights"]), "disabled_features": list(DEFAULT_CONFIG["disabled_features"])}
    # a "number" = int/float, finite, and NOT a bool (True is an int in Python!)
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)
    for k, v in user.items():
        if k == "weights":
            if not isinstance(v, dict) or set(v) - set(cfg["weights"]) or not all(num(x) and 0 <= x <= 100 for x in v.values()):
                raise ValueError("weights must map known patterns to numbers in [0, 100]")
            cfg["weights"].update(v)
        elif k == "disabled_features":  # feature selection: a list of feature names to REMOVE from the ML model
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError("disabled_features must be a list of feature names")
            cfg["disabled_features"] = list(dict.fromkeys(v))  # de-duplicate, keep order
        elif not num(v):
            raise ValueError(f"config '{k}' must be a finite number")
        else:
            cfg[k] = v
    # range checks
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
    for k in ("ip_window_min", "ip_other_min", "ip_new_min_history"):
        if cfg[k] < 1:
            raise ValueError(f"{k} must be >= 1")
    bad = [x for x in cfg["disabled_features"] if x not in ALL_FEATURES]
    if bad:
        raise ValueError(f"unknown features in disabled_features: {bad}. Known features: {ALL_FEATURES}")
    if len(ALL_FEATURES) - len(cfg["disabled_features"]) < 3:
        raise ValueError("at least 3 features must stay enabled")
    return cfg


def selected_features(cfg):
    """The feature names (in a stable order) that the IsolationForest will actually use."""
    return [x for x in ALL_FEATURES if x not in cfg["disabled_features"]]


# --------------------------------------------------------------------------------------
# The main entry point
# --------------------------------------------------------------------------------------
def analyze(df, cfg=None, max_explain=None):
    """Run the complete pipeline on a raw transactions DataFrame.

    max_explain : explain only the N highest-risk flagged rows (None = all). Explaining is a Python loop,
                  so for million-row runs the API passes the number of cases it will actually show.

    Returns a dict with:
      scored    DataFrame, one row per analysed transaction (risk_score, patterns, flagged ...)
      flagged   list of explained flagged transactions, highest risk first
      summary   counts (flagged, by category, by pattern, accounts involved)
      quality   data-quality report (rejected, duplicates, failed ...)
      rejected  DataFrame of rows rejected by validation
      warnings  list of strings
      timings   seconds spent per stage
      cutoff    timestamp separating ML training rows from the holdout
      config    the effective (validated) configuration
      stage     intermediate arrays (prepared rows, features, rule flags, cycles, feature names) re-used by
                detection.crossval so cross-validation does not recompute them
    """
    cfg = validate_config(cfg)
    T = {}; t0 = time.time()
    d, rejected, quality = prepare(df)
    warnings = []
    if d.empty:  # nothing valid to analyse: return a well-formed empty result
        return {"scored": d, "flagged": [], "summary": {"records_analyzed": 0, "flagged": 0}, "quality": quality,
                "rejected": rejected, "warnings": ["no valid transactions"], "timings": {}, "cutoff": None, "config": cfg}
    T["prepare_s"] = time.time() - t0
    t_f = time.time(); f = build_features(d, cfg); T["features_s"] = time.time() - t_f
    t1 = time.time(); cycles = detect_cycles(d, cfg); T["cycles_s"] = time.time() - t1
    amt = d.amount.to_numpy()
    # boolean arrays: one per rule, True where the rule fires for that transaction
    flags = {
        "burst": (f.sender_cnt_win >= cfg["burst_count"]).to_numpy(),
        "split": ((f.pair_cnt_win >= cfg["split_count"]) & (amt <= cfg["split_cap"]) & (f.pair_sum_win >= cfg["split_min_total"])).to_numpy(),
        "rapid_onward": ((f.inflow_ratio >= cfg["rapid_ratio"]) & (f.inflow_ratio <= cfg["rapid_ratio_max"]) & (amt >= cfg["rapid_min_amount"])).to_numpy(),
        "unusual_behavior": ((f.amount_z >= cfg["unusual_z"]) & (amt >= cfg["unusual_min_amount"])).to_numpy(),
        "cycle": d.tx_id.isin(cycles).to_numpy(),
        # irregular IP: (a) other accounts used this very IP a moment ago, or (b) a brand-new IP for an established sender moving a lot
        "irregular_ip": (((f.ip_other_tx_win >= cfg["ip_other_min"]) & (amt >= cfg["ip_min_amount"]))
                         | ((f.ip_is_new == 1) & (f.sender_prior_n >= cfg["ip_new_min_history"]) & (amt >= cfg["ip_new_min_amount"]))).to_numpy(),
    }
    if "ip_address" not in d.columns:
        warnings.append("no ip_address column: IP features are all zero and the irregular_ip rule cannot fire")
    absent = [c for c in OPTIONAL if c != "ip_address" and c not in d.columns]
    if absent:
        warnings.append(f"optional columns not in the data (their features are constant): {', '.join(absent)}")
    # unsupervised anomaly score: fit on the chronologically earliest part only, then score everything
    feats = selected_features(cfg)  # feature selection: everything except cfg["disabled_features"]
    t2 = time.time(); X = f[feats].to_numpy(np.float32); n_tr = int(len(d) * cfg["train_fraction"])
    cutoff = int(d.ts.iloc[max(n_tr - 1, 0)])  # timestamp of the last training row
    if n_tr < 200:
        pct = np.zeros(len(d)); warnings.append("fewer than 200 training rows: ML component disabled, rules only")
    else:
        m = IsolationForest(n_estimators=100, random_state=0, n_jobs=N_JOBS).fit(X[:n_tr])
        a_all = -m.score_samples(X)  # one pass; higher = more anomalous; training scores are the first n_tr entries
        # convert raw scores to a PERCENTILE relative to the training rows (0..1, >1 clipped later by weights)
        pct = np.searchsorted(np.sort(a_all[:n_tr]), a_all) / n_tr
    T["ml_s"] = time.time() - t2
    # final score: ML part (0..ml_weight) + sum of weights of every rule that fired, clipped to [0, 100]
    risk = cfg["ml_weight"] * pct + sum(cfg["weights"][p] * flags[p] for p in flags)
    risk = np.clip(risk, 0, 100)
    # Build the "cycle|burst" style pattern label for every row with a bitmask lookup table
    # (avoids a slow Python loop over every row: 2**6 = 64 possible combinations are precomputed)
    names_ = list(flags)
    mask = sum(flags[p].astype(np.int64) << i for i, p in enumerate(names_))
    table = np.array(["|".join(p for i, p in enumerate(names_) if m_ >> i & 1) for m_ in range(1 << len(names_))], dtype=object)
    pats = table[mask]
    T["rules_ml_s"] = time.time() - t2
    t_s = time.time()
    keep = ["tx_id", "sender", "receiver", "amount", "ts"] + [c for c in ("ip_address", "device_type", "channel") if c in d.columns]
    scored = d[keep].assign(
        timestamp=np.char.add(np.datetime_as_string(d.ts.to_numpy().astype("datetime64[s]"), unit="s"), "Z"),
        risk_score=np.round(risk, 1), risk_category=np.select([risk >= 70, risk >= 50, risk >= 30], ["critical", "high", "medium"], "low"),
        ml_anomaly_percentile=np.round(pct, 3), patterns=pats, flagged=risk >= cfg["threshold"])
    # explain only the flagged rows (few) rather than all rows (many); optionally only the riskiest `max_explain`
    idx = np.where(scored.flagged)[0]
    idx = idx[np.argsort(-scored.risk_score.to_numpy()[idx], kind="stable")]  # riskiest first (ties keep time order)
    if max_explain is not None:
        idx = idx[:max_explain]
    flagged = [_explain(scored.iloc[i], f.iloc[i], cfg, cycles) for i in idx]
    fl = scored[scored.flagged]
    summary = {"records_analyzed": len(d), "flagged": int(len(fl)), "threshold": cfg["threshold"],
               "accounts_involved": int(len(set(fl.sender) | set(fl.receiver))),
               "by_category": fl.risk_category.value_counts().to_dict(),
               "by_pattern": pd.Series([p for s in fl.patterns for p in s.split("|") if p], dtype=str).value_counts().to_dict(),
               "note": "risk_score is a heuristic 0-100 ranking score, NOT a fraud probability"}
    T["scoring_output_s"] = time.time() - t_s
    T["total_s"] = time.time() - t0
    return {"scored": scored, "flagged": flagged, "summary": summary, "quality": quality, "rejected": rejected,
            "warnings": warnings, "timings": {k: round(v, 2) for k, v in T.items()}, "cutoff": cutoff, "config": cfg,
            "stage": {"d": d, "f": f, "flags": flags, "features": feats}}


def _explain(row, f, cfg, cycles):
    """Turn one flagged row into a JSON-friendly record with a plain-English reason per pattern."""
    out = []
    for p in [x for x in row.patterns.split("|") if x]:
        # the time window (in minutes) that the pattern looked at, for display
        win = {"burst": cfg["burst_window_min"], "split": cfg["split_window_min"], "rapid_onward": cfg["rapid_window_min"],
               "unusual_behavior": None, "cycle": cfg["cycle_window_hours"] * 60, "irregular_ip": cfg["ip_window_min"]}[p]
        # lambdas so only the selected explanation is actually formatted
        txt = {"burst": lambda: f"{row.sender} made {int(f.sender_cnt_win)} transfers within {win} minutes",
               "split": lambda: f"{int(f.pair_cnt_win)} transfers <= {cfg['split_cap']} from {row.sender} to {row.receiver} within {win} min, total {f.pair_sum_win:.0f}",
               "rapid_onward": lambda: f"{row.sender} received {f.inflow_ratio:.0%} of this amount in the {win} minutes before sending it on",
               "unusual_behavior": lambda: f"amount is {f.amount_z:.1f} log-std above the sender's prior history ({int(f.sender_prior_n)} prior transfers)",
               "cycle": lambda: "part of cycle " + "; ".join(" -> ".join(c + (c[0],)) for c in sorted(cycles.get(row.tx_id, []))),
               "irregular_ip": lambda: _ip_text(row, f, cfg, win)}[p]()
        out.append({"pattern": p, "contribution": cfg["weights"][p], "accounts": [row.sender, row.receiver],
                    "window_minutes": win, "explanation": txt})
    rec = {"tx_id": row.tx_id, "timestamp": row.timestamp, "sender": row.sender, "receiver": row.receiver,
           "amount": float(row.amount), "risk_score": float(row.risk_score), "risk_category": row.risk_category,
           "ml_anomaly_percentile": float(row.ml_anomaly_percentile), "status": "model_suspicion_unverified", "evidence": out}
    for extra in ("ip_address", "device_type", "channel"):  # context shown next to the case (present only if the data has them)
        if hasattr(row, extra):
            rec[extra] = None if pd.isna(getattr(row, extra)) else str(getattr(row, extra))
    return rec


def _ip_text(row, f, cfg, win):
    """Plain-English reason for an irregular_ip flag (rule (a), rule (b) or both)."""
    ip = getattr(row, "ip_address", "?")
    parts = []
    if f.ip_other_tx_win >= cfg["ip_other_min"] and row.amount >= cfg["ip_min_amount"]:
        parts.append(f"IP {ip} was used for {int(f.ip_other_tx_win)} transfers by other accounts in the previous {win} minutes")
    if f.ip_is_new == 1 and f.sender_prior_n >= cfg["ip_new_min_history"] and row.amount >= cfg["ip_new_min_amount"]:
        parts.append(f"first time {row.sender} used IP {ip}, after {int(f.sender_prior_n)} earlier transfers")
    return "; ".join(parts)


# --------------------------------------------------------------------------------------
# Evaluation against synthetic ground truth
# --------------------------------------------------------------------------------------
def evaluate(result, labels):
    """Chronological holdout evaluation: only rows AFTER the training cutoff are scored.

    `labels` comes from generate.generate()["labels"] and is used here and NOWHERE ELSE.
    Metrics: precision, recall, F1, false-positive rate, precision@k, PR-AUC, confusion
    matrix, recall per injected scenario and the flag rate of benign look-alikes.
    """
    if result.get("cutoff") is None or result["scored"].empty:
        return {"records": 0, "positives": 0, "precision": None, "recall": None, "f1": None, "false_positive_rate": None,
                "precision_at_k": None, "k": 0, "pr_auc": None, "confusion_matrix": {"tp": 0, "fp": 0, "tn": 0, "fn": 0},
                "recall_by_scenario": {}, "benign_lookalike_flag_rate": None, "caveat": "no data"}
    m = result["scored"].merge(labels[["tx_id", "is_suspicious", "scenario_type", "is_benign_lookalike"]], on="tx_id", how="left")
    m = m[m.ts > result["cutoff"]]  # holdout only: the ML model never saw these rows
    y = m.is_suspicious.fillna(0).astype(int).to_numpy(); sc = m.risk_score.to_numpy(); pred = m.flagged.to_numpy()
    tn, fp, fn, tp = confusion_matrix(y, pred.astype(int), labels=[0, 1]).ravel()
    # safe division: None instead of ZeroDivisionError when the denominator is 0
    div = lambda a, b: round(float(a) / b, 4) if b else None
    p, r = div(tp, tp + fp), div(tp, tp + fn)
    # precision@k with k = number of true positives (an "oracle" k, see README audit notes)
    k = int(y.sum()); top = np.argsort(-sc, kind="stable")[:k]
    sus = m[m.is_suspicious == 1]
    return {"records": int(len(m)), "positives": k, "precision": p, "recall": r,
            "f1": round(2 * p * r / (p + r), 4) if p and r else 0.0, "false_positive_rate": div(fp, fp + tn),
            "precision_at_k": div(y[top].sum(), k) if k else None, "k": k,
            "pr_auc": round(float(average_precision_score(y, sc)), 4) if 0 < k < len(y) else None,
            "confusion_matrix": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
            "recall_by_scenario": {t: round(float(g.flagged.mean()), 3) for t, g in sus.groupby("scenario_type")},
            "benign_lookalike_flag_rate": round(float(m[m.is_benign_lookalike == 1].flagged.mean()), 3) if (m.is_benign_lookalike == 1).any() else None,
            "caveat": "Synthetic data with injected scenarios; not evidence of real-world fraud detection performance."}
