"""Test suite for the UPI fraud-pattern detection service.

Run from the detection-service/ directory:

    python -m unittest discover -s tests -t .

(-t . makes detection-service/ the top-level package directory, so `from detection import ...` resolves.)

What is covered, in groups:
  * Data generation   - the synthetic generator is reproducible and keeps its timeline inside 90 days.
  * Input validation  - bad rows are rejected with a reason, duplicates are handled, config is validated.
  * Feature building  - features are causal (no peeking at the future) and match a slow brute-force definition.
  * Detectors         - every rule (cycle, rapid_onward, split, burst, unusual_behavior) fires on its scenario,
                        and the optimized cycle search returns exactly what an unpruned reference search returns.
  * Scoring/metrics   - risk bounds, evaluation metrics, determinism, independence from row order.
  * Flask API         - /health, /detectors and /analyze, including error handling (HTTP 400s).

Important background: the generator produces synthetic data with *injected* fraud scenarios plus a separate
labels table. Labels may only be used for evaluation, never as model inputs (see test_no_label_leakage).
"""
import unittest, numpy as np, pandas as pd, networkx as nx
from detection import generate, engine
from detection.app import create_app

# ---- Shared fixtures, computed ONCE at import time (module level) -------------------------------------------------
# Generating data and running the whole pipeline is the slow part, so the heavier tests below reuse these
# instead of recomputing them in every test. The fixed seed makes every run see exactly the same data.
# DATA: dict with "raw" (messy transactions incl. duplicates/failures), "clean" (de-duplicated ground truth)
#       and "labels" (which rows are injected fraud and of which scenario type; evaluation only).
DATA = generate.generate(800, 10000, seed=7)
# RES: full result of the detection pipeline on the raw data (scored rows, flagged rows, summary, quality...).
RES = engine.analyze(DATA["raw"])
# EV: precision/recall/etc. of RES against the hidden labels, computed on the chronological holdout only.
EV = engine.evaluate(RES, DATA["labels"])


class T(unittest.TestCase):
    """All tests live in one TestCase; unittest runs every method whose name starts with `test`."""

    # --- Data generation ----------------------------------------------------------------------------------------
    def test_reproducible(self):
        # Same seed twice must give identical data (so results and benchmarks are repeatable)...
        a, b = generate.generate(200, 1000, seed=1), generate.generate(200, 1000, seed=1)
        pd.testing.assert_frame_equal(a["raw"], b["raw"])
        # ...and a different seed must give different data (otherwise the seed would be ignored).
        self.assertFalse(a["raw"].equals(generate.generate(200, 1000, seed=2)["raw"]))

    # --- Input validation ---------------------------------------------------------------------------------------
    def test_validation_rejects_bad_rows(self):
        # Four rows, only the first is valid. Row b has an unparsable timestamp, row c a negative amount,
        # and row d is fine except sender == receiver ("y" -> "y"), i.e. a self-transfer, which is an invalid account pair.
        df = pd.DataFrame({"tx_id": ["a", "b", "c", "d"], "timestamp": ["2026-07-01T00:00:00Z", "nope", "2026-07-01T00:00:00Z", "2026-07-01T00:00:00Z"],
                           "sender": ["x", "x", "x", "y"], "receiver": ["y", "y", "y", "y"], "amount": [10, 10, -5, 10], "status": ["SUCCESS"] * 4})
        good, rej = engine.validate(df)
        # Nothing may be dropped silently: every row is either in `good` or in `rej` with a stated reason.
        self.assertEqual(len(good), 1); self.assertEqual(len(rej), 3)
        self.assertEqual(set(rej.reject_reason), {"invalid_timestamp", "invalid_amount", "invalid_account"})
        # A missing required column is a caller error, not a bad row, so it raises instead of rejecting rows.
        with self.assertRaises(ValueError):
            engine.validate(df.drop(columns=["amount"]))

    def test_duplicates_removed(self):
        # The generator injects duplicate tx_ids into "raw"; "clean" is the version without them.
        # The pipeline's own quality report must count exactly the difference.
        self.assertGreater(len(DATA["raw"]), len(DATA["clean"]))
        self.assertEqual(RES["quality"]["duplicates_removed"], len(DATA["raw"]) - len(DATA["clean"]))

    # --- Feature building ---------------------------------------------------------------------------------------
    def test_features_are_causal(self):
        # Causality test: build features on the full history, and again on only the first half. If features for
        # the first half are identical in both runs, no row's features depended on later rows (no future leakage).
        # k is the split point; reset_index so the two frames align; check_exact=False tolerates float rounding.
        d, _, _ = engine.prepare(DATA["clean"]); cfg = engine.DEFAULT_CONFIG; k = len(d) // 2
        full, part = engine.build_features(d, cfg), engine.build_features(d.iloc[:k].reset_index(drop=True), cfg)
        pd.testing.assert_frame_equal(full.iloc[:k].reset_index(drop=True), part, check_exact=False)

    def test_no_label_leakage(self):
        # Guard rails against cheating: no model feature and no required input column may be a label-like field,
        # and the raw data handed to the detector must not even contain the "is_suspicious" column.
        # tx_id is excluded from the REQUIRED check because it is a legitimate identifier input, not a label.
        self.assertFalse(set(engine.FEATURES) & engine.FORBIDDEN_FEATURES)
        self.assertFalse(set(engine.REQUIRED) & (engine.FORBIDDEN_FEATURES - {"tx_id"}))
        self.assertNotIn("is_suspicious", DATA["raw"].columns)

    # --- Detectors ----------------------------------------------------------------------------------------------
    def test_each_detector_fires_on_its_scenario(self):
        # Join scored rows with the labels, then for every injected scenario type check that at least one of its
        # transactions carries the matching pattern name. Pairs are (scenario_type in labels, pattern name in results).
        m = RES["scored"].merge(DATA["labels"], on="tx_id")
        for typ, pat in [("circular", "cycle"), ("rapid_onward", "rapid_onward"), ("splitting", "split"), ("burst", "burst"), ("unusual_behavior", "unusual_behavior")]:
            self.assertTrue(m[m.scenario_type == typ].patterns.str.contains(pat).any(), typ)

    def test_benign_lookalikes_flagged_less(self):
        # "Benign lookalikes" are innocent traffic shaped like fraud (e.g. a family sending money around).
        # The detector should flag them less often than real fraud, and a family group should never be called a cycle/burst.
        m = RES["scored"].merge(DATA["labels"], on="tx_id")
        self.assertLess(m[m.is_benign_lookalike == 1].flagged.mean(), m[m.is_suspicious == 1].flagged.mean())
        self.assertFalse(m[m.scenario_type == "benign_family"].patterns.str.contains("cycle|burst").any())

    # --- Scoring and metrics ------------------------------------------------------------------------------------
    def test_risk_bounds(self):
        # Scores are a 0-100 ranking score and categories come from a closed set of four labels.
        s = RES["scored"]; self.assertTrue(s.risk_score.between(0, 100).all())
        self.assertTrue(set(s.risk_category) <= {"low", "medium", "high", "critical"})

    def test_metrics(self):
        # Hand-built mini example to verify the metric arithmetic. Rows a..d have scores 90,80,10,5; a and b are flagged.
        # Truth: a and c are fraud. So: a=TP, b=FP, c=FN, d=TN -> precision 1/2, recall 1/2, FPR 1/2.
        # k = number of true positives (2); the top-2 by score are a and b, of which one is fraud -> precision@k 1/2.
        # "ts" is 5 for all rows and "cutoff" is 0, so every row is after the training cutoff and is evaluated.
        r = {"scored": pd.DataFrame({"tx_id": list("abcd"), "ts": [5] * 4, "risk_score": [90., 80, 10, 5], "flagged": [True, True, False, False]}), "cutoff": 0}
        lab = pd.DataFrame({"tx_id": list("abcd"), "is_suspicious": [1, 0, 1, 0], "scenario_type": ["x", "y", "x", "y"], "is_benign_lookalike": [0] * 4})
        e = engine.evaluate(r, lab)
        self.assertEqual((e["precision"], e["recall"], e["false_positive_rate"], e["precision_at_k"]), (0.5, 0.5, 0.5, 0.5))
        self.assertEqual(e["confusion_matrix"], {"tp": 1, "fp": 1, "tn": 1, "fn": 1})

    def test_evaluation_beats_chance(self):
        # Loose sanity floor, not a quality claim: the detector must do clearly better than random on synthetic data.
        self.assertGreater(EV["recall"], 0.3); self.assertGreater(EV["pr_auc"], 0.2)

    def test_tiny_and_empty_input_do_not_crash(self):
        # With only 50 rows there are too few training rows for the IsolationForest, so the engine must degrade
        # gracefully (rules only) and say so in `warnings`. Zero rows must give an empty, well-formed result.
        self.assertIn("ML component disabled", engine.analyze(DATA["raw"].head(50))["warnings"][0])
        self.assertEqual(engine.analyze(DATA["raw"].head(0))["summary"]["records_analyzed"], 0)

    # --- Flask API ----------------------------------------------------------------------------------------------
    def test_flask(self):
        # Smoke test of the HTTP layer through Flask's in-process test client (no real server or port needed).
        c = create_app().test_client()
        self.assertEqual(c.get("/health").json["status"], "ok")
        self.assertIn("features", c.get("/detectors").json)
        # Ask the service to generate its own small dataset and analyze it, then inspect the JSON reply.
        r = c.post("/analyze", json={"generate": {"n_accounts": 300, "n_transactions": 4000}}).json
        self.assertIn("evaluation", r)
        # Wording matters: flagged items are only model suspicion, never presented as confirmed fraud.
        self.assertTrue(all(f["status"] == "model_suspicion_unverified" for f in r["flagged"]))
        # Transactions missing required fields must be a client error (400), not a server crash (500).
        self.assertEqual(c.post("/analyze", json={"transactions": [{"tx_id": 1}]}).status_code, 400)

    def test_out_of_range_values_rejected(self):
        # Three crafted bad rows on a copy of real data: an infinite amount, an absurdly large amount (1e15 exceeds
        # the engine's MAX_AMOUNT) and a 1850 timestamp (before the accepted range). Each must get its own reason.
        b = DATA["raw"].head(200).copy()
        b.loc[b.index[0], "amount"] = float("inf"); b.loc[b.index[1], "amount"] = 1e15
        b.loc[b.index[2], "timestamp"] = "1850-01-01T00:00:00Z"
        _, rej = engine.validate(b)
        self.assertEqual(set(rej.reject_reason), {"invalid_amount", "amount_out_of_range", "timestamp_out_of_range"})

    def test_conflicting_duplicates_reported(self):
        # Build a duplicate of the first row's tx_id but with a different amount: same id, conflicting content.
        # prepare() returns (good, rejected, quality); index [2] is the quality dict, which must count this conflict.
        b = DATA["raw"].head(300).copy()
        c = b.head(1).copy(); c["amount"] = c["amount"] + 1
        self.assertEqual(engine.prepare(pd.concat([b, c]))[2]["conflicting_duplicates"], 1)

    def test_config_validation(self):
        # Each bad config must raise ValueError (not TypeError/KeyError): wrong type, unknown key, value out of range,
        # unknown weight name, cycle length above the allowed maximum, a zero-length window, and a non-dict config.
        # msg=str(bad) shows which input failed if one of them does not raise.
        for bad in [{"threshold": "high"}, {"bogus": 1}, {"train_fraction": 2}, {"weights": {"nope": 1}}, {"cycle_max_len": 99}, {"burst_window_min": 0}, "x"]:
            with self.assertRaises(ValueError, msg=str(bad)):
                engine.validate_config(bad)
        # Partial overrides are merged into defaults: overriding only "cycle" leaves "burst" at its default of 35.
        self.assertEqual(engine.validate_config({"weights": {"cycle": 5}})["weights"]["burst"], 35)

    def test_row_order_does_not_change_scores(self):
        # Shuffle the input rows; the engine sorts chronologically itself, so every transaction must get the same
        # score. Compare by tx_id (set_index) because row positions differ; reindex lines the two series up.
        a = RES["scored"].set_index("tx_id").risk_score
        b = engine.analyze(DATA["raw"].sample(frac=1, random_state=3))["scored"].set_index("tx_id").risk_score
        pd.testing.assert_series_equal(a, b.reindex(a.index))

    def test_repeat_analysis_is_identical(self):
        # Determinism: running the pipeline twice on the same input (IsolationForest is seeded) gives identical output.
        pd.testing.assert_frame_equal(RES["scored"], engine.analyze(DATA["raw"])["scored"])

    def test_api_error_handling(self):
        # `ok` is a small valid request body reused below; each case alters one thing and expects HTTP 400.
        c = create_app().test_client(); ok = {"generate": {"n_accounts": 300, "n_transactions": 3000}}
        # Invalid config value.
        self.assertEqual(c.post("/analyze", json={**ok, "config": {"threshold": "x"}}).status_code, 400)
        # Request to generate an absurd amount of data (denial-of-service guard).
        self.assertEqual(c.post("/analyze", json={"generate": {"n_accounts": 10 ** 9, "n_transactions": 10 ** 9}}).status_code, 400)
        # Wrong type for "transactions" (string instead of list).
        self.assertEqual(c.post("/analyze", json={"transactions": "abc"}).status_code, 400)
        # Body that is not valid JSON at all.
        self.assertEqual(c.post("/analyze", data="not json", content_type="application/json").status_code, 400)
        # Invalid "limit" (how many flagged rows to return).
        self.assertEqual(c.post("/analyze", json={**ok, "limit": 0}).status_code, 400)
        # The opposite case: an empty list is valid input and gives a normal 200 reply with zero records analyzed.
        self.assertEqual(c.post("/analyze", json={"transactions": []}).json["summary"]["records_analyzed"], 0)

    def test_features_match_bruteforce_definition(self):
        # The engine computes windowed features with fast vectorised sorting/cumsum tricks. Here each feature is
        # recomputed the slow, obvious way (filter the DataFrame per row) on a small dataset and compared, so a
        # mistake in the clever version cannot hide. Only every 5th row (range step) is checked to keep it fast.
        # For row r (index i): `prev` = rows strictly earlier in time (strict "<" keeps the features causal),
        # `snd` = those earlier rows by the same sender.
        d, _, _ = engine.prepare(generate.generate(60, 600, seed=5)["clean"]); cfg = engine.DEFAULT_CONFIG
        f = engine.build_features(d, cfg)
        for i in range(0, len(d), 5):
            r, prev = d.iloc[i], d.iloc[:i]
            prev = prev[prev.ts < r.ts]  # strictly earlier
            snd = prev[prev.sender == r.sender]
            # Burst feature: sender's earlier transfers in the burst window, +1 to count the current row itself.
            self.assertEqual(f.sender_cnt_win[i], len(snd[snd.ts >= r.ts - cfg["burst_window_min"] * 60]) + 1)
            # Sender's transfers in the previous 24h (86400 s); the current row is not counted.
            self.assertEqual(f.sender_cnt_1d[i], len(snd[snd.ts >= r.ts - 86400]))
            # Incoming transfers already received by this row's receiver in the previous 24h (from anyone).
            self.assertEqual(f.receiver_in_cnt_1d[i], len(prev[(prev.receiver == r.receiver) & (prev.ts >= r.ts - 86400)]))
            # Splitting feature: earlier transfers on the same sender->receiver pair inside the split window;
            # count and amount sum both include the current transaction (+1, + r.amount).
            pair = snd[(snd.receiver == r.receiver) & (snd.ts >= r.ts - cfg["split_window_min"] * 60)]
            self.assertEqual(f.pair_cnt_win[i], len(pair) + 1); self.assertAlmostEqual(f.pair_sum_win[i], pair.amount.sum() + r.amount, 6)
            # Rapid-onward feature: money the sender RECEIVED shortly before sending this payment. The engine stores it
            # as a ratio to this amount, so multiply back by r.amount to compare with the raw inflow total.
            inflow = prev[(prev.receiver == r.sender) & (prev.ts >= r.ts - cfg["rapid_window_min"] * 60)]
            self.assertAlmostEqual(f.inflow_ratio[i] * r.amount, inflow.amount.sum(), 6)

    def test_pruned_cycle_search_equals_unpruned_reference(self):
        # The optimized engine prunes edges that cannot be part of any cycle before searching. This test re-implements
        # the search naively (no pruning) and requires the exact same result, proving pruning is only a speed-up.
        # `ref` maps tx_id -> set of cycles (tuples of accounts) it belongs to. Only transfers >= cycle_min_amount count.
        d, _, _ = engine.prepare(DATA["clean"]); cfg = engine.DEFAULT_CONFIG
        ref, sub = {}, d[d.amount >= cfg["cycle_min_amount"]]
        # W = window length in seconds; st = sorted timestamps of the candidate transfers (for binary search).
        W, st = cfg["cycle_window_hours"] * 3600, sub.ts.to_numpy()
        for start in range(int(st.min()), int(st.max()) + 1, 86400):  # naive: full graph per window, no pruning
            # Slice out the transactions falling in [start, start + W): one sliding window per day.
            w = sub.iloc[np.searchsorted(st, start):np.searchsorted(st, start + W)]
            # Group transaction ids by (sender, receiver) edge; several payments can share one edge.
            edges = {}
            for tx, a, b in zip(w.tx_id, w.sender, w.receiver):
                edges.setdefault((a, b), []).append(tx)
            # Enumerate every simple cycle up to the max length on the WHOLE window graph (no SCC pruning).
            for cyc in nx.simple_cycles(nx.DiGraph(list(edges)), length_bound=cfg["cycle_max_len"]):
                if len(cyc) < cfg["cycle_min_len"]:
                    continue  # too short to count (e.g. A->B->A ping-pong)
                # Canonical rotation: start the cycle at its smallest account so A-B-C, B-C-A, C-A-B are one key.
                k = cyc.index(min(cyc)); cyc = tuple(cyc[k:] + cyc[:k])
                # Every transaction on each edge of the cycle is attributed to that cycle.
                for i, u in enumerate(cyc):
                    for tx in edges[(u, cyc[(i + 1) % len(cyc)])]:
                        ref.setdefault(tx, set()).add(cyc)
        got = engine.detect_cycles(d, cfg)
        # assertGreater guards against a vacuous pass: the reference must actually have found some cycles.
        self.assertGreater(len(ref), 0); self.assertEqual(got, ref)

    def test_cycle_work_is_bounded(self):
        # With cycle_max_per_window=1 the search must stop early in each window. The per-transaction cycle count
        # can then never be large (30 is a generous ceiling), showing the work limit really is enforced.
        d, _, _ = engine.prepare(DATA["clean"]); cfg = {**engine.DEFAULT_CONFIG, "cycle_max_per_window": 1}
        self.assertLessEqual(max(len(v) for v in engine.detect_cycles(d, cfg).values()), 30)

    # --- Generator sanity ---------------------------------------------------------------------------------------
    def test_timeline_stays_inside_90_days(self):
        # span = days between the generator's START and the latest transaction; injected scenarios must not run past day 90.
        span = (pd.to_datetime(DATA["clean"].timestamp, utc=True).max() - generate.START).total_seconds() / 86400
        self.assertLess(span, 90)


# Allows `python tests/test_detection.py` as a shortcut, though the documented way is unittest discover (see top).
if __name__ == "__main__":
    unittest.main()
