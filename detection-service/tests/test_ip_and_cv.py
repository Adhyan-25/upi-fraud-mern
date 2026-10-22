"""Tests for the IP features, the irregular_ip rule, feature selection, the extra columns,
large-scale safety and the K-fold cross-validation (detection/crossval.py).

Run from detection-service/:  python -m unittest discover -s tests -t .
"""
import unittest
import numpy as np, pandas as pd
from detection import crossval, engine, generate

# One small dataset shared by all tests in this file (generation + analysis takes about a second).
DATA = generate.generate(800, 10000, seed=7)
RES = engine.analyze(DATA["raw"])


class GeneratorTests(unittest.TestCase):
    def test_optional_columns_present(self):
        self.assertTrue(set(engine.OPTIONAL) <= set(DATA["raw"].columns))
        self.assertTrue(set(engine.OPTIONAL) <= set(DATA["clean"].columns))

    def test_labels_are_not_in_the_raw_table(self):
        self.assertFalse({"scenario_id", "scenario_type", "is_suspicious", "is_benign_lookalike"} & set(DATA["raw"].columns))

    def test_ip_farm_and_ip_lookalikes_exist(self):
        types = set(DATA["labels"].scenario_type)
        self.assertTrue({"ip_farm", "benign_office_ip", "benign_travel"} <= types)

    def test_farm_members_share_one_ip(self):
        m = DATA["clean"].merge(DATA["labels"], on="tx_id")
        farm = m[m.scenario_type == "ip_farm"]
        per_scenario = farm.groupby("scenario_id").agg(ips=("ip_address", "nunique"), senders=("sender", "nunique"))
        self.assertTrue((per_scenario.ips == 1).all())
        self.assertTrue((per_scenario.senders >= 6).all())

    def test_default_accounts_scales_with_volume(self):
        self.assertEqual(generate.default_accounts(1_000_000), 100_000)
        self.assertEqual(generate.default_accounts(500), 100)

    def test_ip_strings_look_like_ipv4(self):
        ips = DATA["raw"].ip_address.drop_duplicates().head(200)
        self.assertTrue(ips.str.fullmatch(r"\d{1,3}(\.\d{1,3}){3}").all())


class IpFeatureTests(unittest.TestCase):
    def test_ip_features_match_bruteforce_definition(self):
        d, _, _ = engine.prepare(DATA["clean"]); cfg = engine.DEFAULT_CONFIG
        f = engine.build_features(d, cfg)
        w = cfg["ip_window_min"] * 60
        for i in range(0, len(d), 7):
            r, before = d.iloc[i], d.iloc[:i]                     # rows earlier in processing order
            prev = d[d.ts < r.ts]                                   # rows strictly earlier in TIME
            same_ip = prev[(prev.ip_address == r.ip_address) & (prev.ts >= r.ts - w)]
            self.assertEqual(f.ip_cnt_win[i], len(same_ip) + 1)
            self.assertEqual(f.ip_other_tx_win[i], (same_ip.sender != r.sender).sum())
            seen = before[before.ip_address == r.ip_address]
            self.assertEqual(f.ip_is_new[i], float(not (seen.sender == r.sender).any()))
            self.assertEqual(f.ip_distinct_senders[i], len(set(seen.sender) - {r.sender}))

    def test_pair_keys_do_not_overflow_with_many_accounts(self):
        """200,000 distinct accounts used to overflow the packed (pair, time) key. A repeated pair must still be counted."""
        n = 3000
        rows = [{"tx_id": f"T{i}", "timestamp": f"2026-07-01T00:{i // 60:02d}:{i % 60:02d}Z", "sender": f"S{i}", "receiver": f"R{i}",
                 "amount": 100, "status": "SUCCESS"} for i in range(n)]
        rows += [{"tx_id": f"X{j}", "timestamp": f"2026-07-02T01:00:{j:02d}Z", "sender": "S199999", "receiver": "R199999",
                  "amount": 100, "status": "SUCCESS"} for j in range(5)]
        d, _, _ = engine.prepare(pd.DataFrame(rows))
        f = engine.build_features(d, engine.DEFAULT_CONFIG)
        self.assertEqual(int(f.pair_cnt_win.iloc[-1]), 5)

    def test_missing_optional_columns_are_neutral(self):
        res = engine.analyze(DATA["clean"][engine.REQUIRED])
        st = res["stage"]
        for name in engine.FEATURES_IP + engine.FEATURES_EXTRA:
            self.assertTrue((st["f"][name] == 0).all(), name)
        self.assertFalse(st["flags"]["irregular_ip"].any())
        self.assertTrue(any("ip_address" in w for w in res["warnings"]))

    def test_blank_ip_cells_are_ignored(self):
        raw = DATA["clean"].copy()
        raw.loc[raw.index[:50], "ip_address"] = ""
        res = engine.analyze(raw)
        self.assertEqual(res["quality"]["rejected"], 0)   # missing optional values are not an error


class IrregularIpRuleTests(unittest.TestCase):
    def test_rule_fires_on_ip_farm_rows(self):
        m = RES["scored"].merge(DATA["labels"], on="tx_id")
        farm = m[m.scenario_type == "ip_farm"]
        self.assertGreater(farm.patterns.str.contains("irregular_ip").mean(), 0.3)

    def test_rule_is_rare_on_normal_traffic(self):
        m = RES["scored"].merge(DATA["labels"], on="tx_id")
        legit = m[m.scenario_type == "legit"]
        self.assertLess(legit.patterns.str.contains("irregular_ip").mean(), 0.005)

    def test_first_transfers_of_a_farm_have_not_seen_enough_other_accounts(self):
        """Causality: rule (a) counts EARLIER transfers by other accounts, so the first `ip_other_min` farm
        transfers cannot satisfy it (rule (b), a brand-new IP, may still fire on them - that is a different signal)."""
        st = RES["stage"]
        pos = {t: i for i, t in enumerate(st["d"].tx_id)}
        m = RES["scored"].merge(DATA["labels"], on="tx_id")
        need = engine.DEFAULT_CONFIG["ip_other_min"]
        for _, g in m[m.scenario_type == "ip_farm"].groupby("scenario_id"):
            first = g.sort_values("ts").head(need)
            seen = st["f"].ip_other_tx_win.to_numpy()[[pos[t] for t in first.tx_id]]
            self.assertTrue((seen < need).all())

    def test_evidence_mentions_the_ip(self):
        ev = [e for f in RES["flagged"] for e in f["evidence"] if e["pattern"] == "irregular_ip"]
        self.assertTrue(ev)
        self.assertTrue(all("IP " in e["explanation"] for e in ev))

    def test_flagged_records_carry_ip_context(self):
        f = RES["flagged"][0]
        self.assertIn("ip_address", f)


class FeatureSelectionTests(unittest.TestCase):
    def test_all_groups_are_disjoint_and_complete(self):
        names = [n for g in engine.FEATURE_GROUPS.values() for n in g]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, engine.ALL_FEATURES)

    def test_every_feature_exists_in_the_feature_table(self):
        self.assertTrue(set(engine.ALL_FEATURES) <= set(RES["stage"]["f"].columns))

    def test_disabled_features_are_removed_from_the_model(self):
        res = engine.analyze(DATA["raw"], {"disabled_features": ["note_len", "app_major"]})
        self.assertNotIn("note_len", res["stage"]["features"])
        self.assertNotIn("app_major", res["stage"]["features"])
        self.assertEqual(len(res["stage"]["features"]), len(engine.ALL_FEATURES) - 2)

    def test_dropping_features_does_not_change_the_rules(self):
        a = engine.analyze(DATA["raw"])
        b = engine.analyze(DATA["raw"], {"disabled_features": engine.FEATURES_EXTRA})
        for k in a["stage"]["flags"]:
            np.testing.assert_array_equal(a["stage"]["flags"][k], b["stage"]["flags"][k])

    def test_bad_feature_config_is_rejected(self):
        for bad in ({"disabled_features": ["nope"]}, {"disabled_features": "note_len"}, {"disabled_features": [1]},
                    {"disabled_features": engine.ALL_FEATURES[:-2]}, {"ip_other_min": 0}, {"unknown_key": 1}):
            with self.assertRaises(ValueError, msg=str(bad)):
                engine.validate_config(bad)

    def test_default_config_is_not_mutated(self):
        engine.validate_config({"disabled_features": ["note_len"]})
        self.assertNotIn("note_len", engine.DEFAULT_CONFIG["disabled_features"])

    def test_univariate_ranking_puts_noise_below_signal(self):
        rank = {r["feature"]: r["strength"] for r in crossval.univariate_auc(RES, DATA["labels"])}
        self.assertGreater(rank["log_amount"], 0.4)
        self.assertGreater(rank["ip_cnt_win"], 0.3)
        # noise columns: clearly weaker than the real signals (on tiny datasets chance alone can give ~0.2, hence relative bounds)
        for noise in ("device_code", "receiver_bank_code", "cross_state", "network_code"):
            self.assertLess(rank[noise], rank["log_amount"] / 2, noise)
            self.assertLess(rank[noise], rank["ip_cnt_win"] / 2, noise)


class CrossValidationTests(unittest.TestCase):
    def setUp(self):
        d = RES["stage"]["d"]
        lab = d[["tx_id"]].merge(DATA["labels"], on="tx_id", how="left")
        self.y = lab.is_suspicious.fillna(0).astype(int).to_numpy()
        self.scen = lab.scenario_id.to_numpy()
        self.ts = d.ts.to_numpy()

    def test_five_folds_partition_the_rows(self):
        for mode in ("stratified", "grouped"):
            folds = crossval._make_folds(self.y, self.ts, self.scen, 5, mode, 1)
            self.assertEqual(len(folds), 5)
            test_rows = np.concatenate([te for _, te in folds])
            self.assertEqual(len(test_rows), len(self.y))
            self.assertEqual(len(np.unique(test_rows)), len(self.y))          # every row tested exactly once
            for tr, te in folds:
                self.assertEqual(len(np.intersect1d(tr, te)), 0)               # no row in both train and test

    def test_stratified_folds_share_the_positives_evenly(self):
        folds = crossval._make_folds(self.y, self.ts, self.scen, 5, "stratified", 1)
        pos = [int(self.y[te].sum()) for _, te in folds]
        self.assertLessEqual(max(pos) - min(pos), 1)

    def test_grouped_folds_never_split_a_scenario(self):
        folds = crossval._make_folds(self.y, self.ts, self.scen, 5, "grouped", 1)
        fold_of = {}
        for i, (_, te) in enumerate(folds):
            for s in set(self.scen[te]):
                if s:                                                           # "" = ordinary traffic
                    self.assertNotIn(s, fold_of, f"scenario {s} appears in two folds")
                    fold_of[s] = i

    def test_time_folds_train_only_on_the_past(self):
        folds = crossval._make_folds(self.y, self.ts, self.scen, 5, "time", 1)
        self.assertEqual(len(folds), 5)
        for tr, te in folds:
            self.assertLessEqual(self.ts[tr].max(), self.ts[te].min())
        self.assertTrue(all(len(folds[i][0]) < len(folds[i + 1][0]) for i in range(4)))   # training window grows

    def test_cross_validate_output_shape(self):
        for mode in crossval.MODES:
            cv = crossval.cross_validate(RES, DATA["labels"], folds=5, mode=mode)
            self.assertEqual(cv["folds"], 5)
            self.assertEqual(len(cv["per_fold"]), 5)
            for k in crossval.METRICS:
                self.assertGreaterEqual(cv["mean"][k], 0)
                self.assertLessEqual(cv["mean"][k], 1)
            self.assertEqual(sum(m["n"] for m in cv["per_fold"]), cv["pooled"]["n"])
            for m in cv["per_fold"]:
                self.assertEqual(m["tp"] + m["fp"] + m["tn"] + m["fn"], m["n"])

    def test_cross_validation_beats_chance_and_is_reproducible(self):
        a = crossval.cross_validate(RES, DATA["labels"], 5, "stratified", seed=3)
        b = crossval.cross_validate(RES, DATA["labels"], 5, "stratified", seed=3)
        self.assertEqual(a["mean"], b["mean"])
        self.assertGreater(a["mean"]["pr_auc"], 5 * self.y.mean())              # far better than a random ranking
        self.assertGreater(a["mean"]["pr_auc"], a["mean"]["pr_auc_ml_only"])   # rules add value on top of the forest alone

    def test_other_fold_counts_work(self):
        self.assertEqual(len(crossval.cross_validate(RES, DATA["labels"], folds=3)["per_fold"]), 3)
        self.assertEqual(len(crossval.cross_validate(RES, DATA["labels"], folds=10)["per_fold"]), 10)

    def test_feature_subset_is_used(self):
        cv = crossval.cross_validate(RES, DATA["labels"], features=engine.FEATURES)
        self.assertEqual(cv["n_features"], len(engine.FEATURES))

    def test_bad_arguments_raise_value_error(self):
        for kw in ({"folds": 1}, {"folds": 11}, {"mode": "random"}, {"features": ["nope"]}):
            with self.assertRaises(ValueError, msg=str(kw)):
                crossval.cross_validate(RES, DATA["labels"], **kw)

    def test_too_few_positives_is_a_clear_error(self):
        labels = DATA["labels"].copy(); labels["is_suspicious"] = 0; labels.loc[labels.index[:2], "is_suspicious"] = 1
        with self.assertRaisesRegex(ValueError, "at least 5"):
            crossval.cross_validate(RES, labels, folds=5)

    def test_empty_analysis_cannot_be_cross_validated(self):
        with self.assertRaises(ValueError):
            crossval.cross_validate(engine.analyze(DATA["raw"].iloc[0:0]), DATA["labels"])


class ScaleTests(unittest.TestCase):
    def test_max_explain_limits_explanations_but_not_counts(self):
        res = engine.analyze(DATA["raw"], max_explain=5)
        self.assertEqual(len(res["flagged"]), 5)
        self.assertEqual(res["summary"]["flagged"], RES["summary"]["flagged"])
        self.assertEqual([f["tx_id"] for f in res["flagged"]], [f["tx_id"] for f in RES["flagged"][:5]])

    def test_100k_accounts_run_end_to_end(self):
        """A 5x-smaller version of the million-row setting: many accounts, one account per ~10 transactions."""
        d = generate.generate(generate.default_accounts(50000), 50000, seed=1)
        res = engine.analyze(d["raw"], max_explain=10)
        ev = engine.evaluate(res, d["labels"])
        self.assertEqual(res["quality"]["rejected"], 0)
        self.assertGreater(ev["recall"], 0.3)
        self.assertGreater(ev["precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
