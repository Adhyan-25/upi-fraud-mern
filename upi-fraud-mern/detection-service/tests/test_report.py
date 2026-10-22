"""Tests for the dashboard payload (detection/report.py) and the /analyze `dashboard` block.

Run from detection-service/:  python -m unittest discover -s tests -t .
"""
import unittest
from detection import crossval, generate, engine, report
from detection.app import create_app

# Small dataset, built once and shared by every test in this file (keeps the suite fast).
DATA = generate.generate(400, 6000, seed=11)
RES = engine.analyze(DATA["raw"])


class ReportTests(unittest.TestCase):
    def test_volume_adds_up(self):
        """Per-day totals must sum to the number of analysed rows, flagged per day to the flagged count."""
        r = report.build_report(RES, DATA["labels"])
        self.assertEqual(sum(r["volume"]["total"]), RES["summary"]["records_analyzed"])
        self.assertEqual(sum(r["volume"]["flagged"]), RES["summary"]["flagged"])

    def test_cases_are_capped_and_sorted(self):
        r = report.build_report(RES, DATA["labels"], max_cases=5)
        self.assertLessEqual(len(r["cases"]), 5)
        scores = [c["riskScore"] for c in r["cases"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_every_case_graph_contains_its_own_transaction(self):
        for c in report.build_report(RES, DATA["labels"])["cases"]:
            focus = [e for e in c["graph"] if e["focus"]]
            self.assertEqual(len(focus), 1)
            self.assertEqual((focus[0]["from"], focus[0]["to"]), (c["sender"], c["receiver"]))
            self.assertLessEqual(len(c["graph"]), report.GRAPH_MAX_EDGES)

    def test_evaluation_only_with_labels(self):
        self.assertIsNone(report.build_report(RES, None)["evaluation"])
        self.assertIsNotNone(report.build_report(RES, DATA["labels"])["evaluation"])

    def test_empty_result_is_well_formed(self):
        empty = engine.analyze(DATA["raw"].iloc[0:0])
        r = report.build_report(empty)
        self.assertEqual(r["cases"], [])
        self.assertEqual(r["meta"]["records"], 0)


class ReportNewFieldTests(unittest.TestCase):
    def test_cases_carry_ip_context(self):
        cases = report.build_report(RES, DATA["labels"])["cases"]
        self.assertTrue(all(c["ip"] for c in cases))
        self.assertTrue(all(c["device"] and c["channel"] for c in cases))

    def test_cross_validation_payload_is_camel_case(self):
        cv = crossval.cross_validate(RES, DATA["labels"], folds=5)
        p = report.build_report(RES, DATA["labels"], cv=cv)["crossValidation"]
        self.assertEqual(p["folds"], 5)
        self.assertEqual(len(p["perFold"]), 5)
        self.assertIn("falsePositiveRate", p["mean"])
        self.assertIn("prAucMlOnly", p["std"])
        self.assertIsNone(report.build_report(RES, DATA["labels"])["crossValidation"])


class ApiDashboardTests(unittest.TestCase):
    def test_analyze_returns_dashboard_block(self):
        c = create_app().test_client()
        r = c.post("/analyze", json={"generate": {"n_accounts": 200, "n_transactions": 3000, "seed": 3}, "limit": 10})
        self.assertEqual(r.status_code, 200)
        self.assertIn("dashboard", r.json)
        self.assertLessEqual(len(r.json["dashboard"]["cases"]), 10)
        self.assertEqual(r.json["config"]["threshold"], 50)

    def test_cross_validation_option(self):
        c = create_app().test_client()
        body = {"generate": {"n_accounts": 300, "n_transactions": 6000, "seed": 3}, "limit": 5,
                "cross_validation": {"enabled": True, "folds": 5, "mode": "grouped"}}
        r = c.post("/analyze", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json["dashboard"]["crossValidation"]["perFold"]), 5)
        self.assertIn("cv_s", r.json["timings"])
        # bad mode => the analysis still succeeds, CV is skipped with a warning
        body["cross_validation"]["mode"] = "bogus"
        r = c.post("/analyze", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json["cross_validation"])
        self.assertTrue(any("cross-validation skipped" in w for w in r.json["warnings"]))

    def test_cross_validation_needs_labels(self):
        c = create_app().test_client()
        rows = DATA["raw"].head(800).to_dict("records")
        r = c.post("/analyze", json={"transactions": rows, "cross_validation": {"enabled": True}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("needs ground-truth labels" in w for w in r.json["warnings"]))

    def test_account_count_defaults_to_a_tenth_of_the_transactions(self):
        c = create_app().test_client()
        r = c.post("/analyze", json={"generate": {"n_transactions": 3000}, "limit": 5})
        self.assertEqual(r.json["dashboard"]["meta"]["accounts_total"], 300)

    def test_disabled_features_config_and_errors(self):
        c = create_app().test_client()
        g = {"generate": {"n_accounts": 200, "n_transactions": 3000}, "limit": 5}
        self.assertEqual(c.post("/analyze", json={**g, "config": {"disabled_features": ["note_len"]}}).status_code, 200)
        self.assertEqual(c.post("/analyze", json={**g, "config": {"disabled_features": ["nope"]}}).status_code, 400)
        self.assertEqual(c.post("/analyze", json={"generate": {"n_accounts": 10, "n_transactions": 3000}}).status_code, 400)

    def test_detectors_lists_feature_groups(self):
        r = create_app().test_client().get("/detectors")
        self.assertEqual(set(r.json["feature_groups"]), {"core", "ip", "extra"})
        self.assertIn("ip_address", r.json["optional_columns"])

    def test_uploaded_transactions_have_no_evaluation(self):
        c = create_app().test_client()
        rows = DATA["raw"].head(500).to_dict("records")
        r = c.post("/analyze", json={"transactions": rows})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json["dashboard"]["evaluation"])


if __name__ == "__main__":
    unittest.main()
