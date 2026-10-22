"""Dashboard report builder.

engine.analyze() returns raw DataFrames and lists. The React dashboard needs a more
presentation-shaped payload, so this module (a generalisation of the original
`export_data.py` script that used to write data.json / index.html) builds:

  * meta    - headline numbers for the summary bar,
  * volume  - transactions per day and flagged per day (for the timeline chart),
  * patterns- how many flagged transactions each detector contributed to,
  * evaluation - the metrics from engine.evaluate() (only when labels exist),
  * cases   - one record per flagged transaction (highest risk first, capped) INCLUDING a
              small "money flow" graph of the transfers around it.

The Express server stores this payload in MongoDB; nothing in here touches a database.
"""
import re
import numpy as np, pandas as pd

# +/- 3 hours around a flagged transaction are searched for related transfers (graph neighbourhood)
GRAPH_WINDOW_S = 3 * 3600
# maximum number of edges drawn in one case graph (keeps the SVG readable)
GRAPH_MAX_EDGES = 10


def _cycle_accounts(evidence):
    """Extract every account named in a 'part of cycle A -> B -> C -> A; ...' explanation."""
    accts = set()
    for e in evidence:
        if e["pattern"] == "cycle":
            text = e["explanation"][len("part of cycle "):]
            accts |= set(re.split(r" -> |; ", text))
    return accts


# snake_case (Python) -> camelCase (JavaScript) names for the metrics inside the cross-validation block
_CV_KEYS = {"precision": "precision", "recall": "recall", "f1": "f1", "false_positive_rate": "falsePositiveRate",
            "pr_auc": "prAuc", "pr_auc_ml_only": "prAucMlOnly", "n": "n", "positives": "positives",
            "tp": "tp", "fp": "fp", "tn": "tn", "fn": "fn", "fold": "fold"}


def _cv_payload(cv):
    """engine-style cross-validation result -> camelCase dict for the dashboard (None stays None)."""
    if cv is None:
        return None
    conv = lambda m: {_CV_KEYS[k]: v for k, v in m.items() if k in _CV_KEYS}
    return {"folds": cv["folds"], "mode": cv["mode"], "seed": cv["seed"], "features": cv["features"],
            "perFold": [conv(m) for m in cv["per_fold"]], "mean": conv(cv["mean"]), "std": conv(cv["std"]),
            "pooled": conv(cv["pooled"]), "seconds": cv["seconds"], "note": cv["note"]}


def build_report(res, labels=None, accounts_total=None, max_cases=100, cv=None):
    """Turn an engine.analyze() result into the JSON-friendly dashboard payload.

    res            : dict returned by engine.analyze()
    labels         : ground-truth DataFrame (synthetic runs only) or None
    accounts_total : number of distinct accounts in the source data (if known)
    max_cases      : cap on the number of cases (with graphs) included
    cv             : result of crossval.cross_validate() (or None)
    """
    sc, summ, q = res["scored"], res["summary"], res["quality"]
    if sc.empty:  # no valid rows => nothing to chart
        return {"meta": {"records": 0, "flagged": 0, "critical": 0, "high": 0, "accounts": 0,
                         "accounts_total": accounts_total or 0, "days": 0, "start": None,
                         "duplicates": q["duplicates_removed"], "rejected": q["rejected"],
                         "threshold": res["config"]["threshold"]},
                "volume": {"total": [], "flagged": []}, "patterns": {}, "evaluation": None, "crossValidation": None, "cases": []}

    ts = sc.ts.to_numpy()
    # Day 0 = midnight (UTC) of the first transaction's day. Works for ANY dataset, not just the synthetic one.
    base = int(ts.min() // 86400 * 86400)
    day = (ts - base) // 86400
    n_days = int(day.max()) + 1
    flagged_mask = sc.flagged.to_numpy()
    volume_total = np.bincount(day, minlength=n_days)
    volume_flagged = np.bincount(day[flagged_mask], minlength=n_days)

    # ---- cases: the top `max_cases` flagged transactions by risk (engine already sorted them) ----
    ids = {t: i for i, t in enumerate(sc.tx_id)}  # tx_id -> row position, for fast lookup
    cases = []
    for f in res["flagged"][:max_cases]:
        t = ts[ids[f["tx_id"]]]
        # accounts that belong to the story of this case (sender, receiver and any cycle members)
        accts = {f["sender"], f["receiver"]} | _cycle_accounts(f["evidence"])
        # all transfers within +/- 3h (rows are sorted by ts, so two binary searches find the slice)
        w = sc.iloc[np.searchsorted(ts, t - GRAPH_WINDOW_S):np.searchsorted(ts, t + GRAPH_WINDOW_S, "right")]
        # priority 1: transfers where BOTH ends are in the story; priority 2: touching one end, riskiest first
        both = w[w.sender.isin(accts) & w.receiver.isin(accts)]
        one = w[(w.sender.isin(accts) | w.receiver.isin(accts)) & ~w.tx_id.isin(both.tx_id)].sort_values("risk_score", ascending=False)
        own = w[w.tx_id == f["tx_id"]]  # the flagged transaction itself is always drawn
        g = pd.concat([own, both, one]).drop_duplicates("tx_id").head(GRAPH_MAX_EDGES)
        cases.append({
            "txId": f["tx_id"], "timestamp": f["timestamp"], "sender": f["sender"], "receiver": f["receiver"],
            "amount": round(f["amount"]), "riskScore": f["risk_score"], "riskCategory": f["risk_category"],
            "mlPercentile": f["ml_anomaly_percentile"],
            # context columns (None when the data does not have them)
            "ip": f.get("ip_address"), "device": f.get("device_type"), "channel": f.get("channel"),
            "evidence": [{"pattern": e["pattern"], "weight": e["contribution"], "text": e["explanation"]} for e in f["evidence"]],
            # graph edge = {from, to, amount, flagged?, focus?}; focus marks the case's own transaction
            "graph": [{"from": r.sender, "to": r.receiver, "amount": round(r.amount), "flagged": bool(r.flagged),
                       "focus": bool(r.tx_id == f["tx_id"])} for r in g.itertuples()],
        })

    ev = None
    if labels is not None:
        from . import engine  # local import: avoids a hard dependency at module import time
        E = engine.evaluate(res, labels)
        ev = {"records": E["records"], "positives": E["positives"], "precision": E["precision"], "recall": E["recall"],
              "f1": E["f1"], "falsePositiveRate": E["false_positive_rate"], "precisionAtK": E["precision_at_k"],
              "k": E["k"], "prAuc": E["pr_auc"], "benignFlagRate": E["benign_lookalike_flag_rate"],
              "recallByScenario": E["recall_by_scenario"], "confusion": E["confusion_matrix"], "caveat": E["caveat"]}

    all_accts = len(set(sc.sender) | set(sc.receiver))
    return {
        "meta": {"records": summ["records_analyzed"], "flagged": summ["flagged"],
                 "critical": summ["by_category"].get("critical", 0), "high": summ["by_category"].get("high", 0),
                 "accounts": summ["accounts_involved"], "accounts_total": accounts_total or all_accts,
                 "days": n_days, "start": str(pd.Timestamp(base, unit="s", tz="UTC").date()),
                 "duplicates": q["duplicates_removed"], "rejected": q["rejected"], "threshold": res["config"]["threshold"],
                 "runtime": res["timings"].get("total_s")},
        "volume": {"total": volume_total.tolist(), "flagged": volume_flagged.tolist()},
        "patterns": summ["by_pattern"],
        "evaluation": ev,
        "crossValidation": _cv_payload(cv),
        "cases": cases,
    }
