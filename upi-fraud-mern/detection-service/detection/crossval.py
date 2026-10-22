"""K-fold cross-validation (default 5 folds) and feature-selection helpers.

WHAT IS BEING CROSS-VALIDATED?
  The risk score is   ml_weight * IsolationForest percentile  +  sum of rule weights.
  The RULES have fixed thresholds - nothing is learned, so there is nothing to cross-validate there.
  The only part that is TRAINED is the IsolationForest. For each fold we therefore
      1. fit a fresh IsolationForest on the TRAIN part (labels are never shown to it - it is unsupervised),
      2. turn its anomaly scores into percentiles relative to the train part,
      3. build the risk score for the TEST part (rule points are the same in every fold),
      4. measure precision / recall / F1 / FPR / PR-AUC on the TEST part with the ground-truth labels.
  Averaging over 5 different test parts tells you how STABLE the numbers are (mean +- standard deviation)
  instead of relying on one lucky or unlucky split.

THREE WAYS TO SPLIT (parameter `mode`)
  "stratified" (default)  random folds; every fold gets the same share of fraud rows.
  "grouped"               like stratified, but all rows of one injected scenario stay in ONE fold, so the
                          forest never trains on (unlabelled) rows from the very scenario it is tested on.
  "time"                  forward chaining: train on the past, test on the next slice (the honest setting
                          for production use; the first fold has the least training data).

FEATURE SELECTION (see the README, "Feature selection")
  * cross_validate(..., features=[...]) re-runs the folds with a different feature subset, cheaply, because
    features and rules are computed only once.
  * univariate_auc() ranks every feature by how well it separates fraud from non-fraud on its own.
  * run as a script for a printed report:
        python -m detection.crossval 200000                 # CV + per-group ablation + univariate ranking
        python -m detection.crossval 200000 --drop note_len,app_major,sender_bank_code
        python -m detection.crossval 200000 --mode time --folds 5
"""
import argparse, time
import numpy as np, pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, StratifiedGroupKFold
from . import engine

MODES = ("stratified", "grouped", "time")
# Metrics that are averaged over folds (name -> key in the per-fold dict).
METRICS = ("precision", "recall", "f1", "false_positive_rate", "pr_auc", "pr_auc_ml_only")
# At most this many rows are used to fit each forest (it only draws 256 rows per tree anyway) ...
FIT_MAX = 200_000
# ... and at most this many training rows are scored to build the percentile reference.
REF_MAX = 100_000


def _make_folds(y, ts, scenario_ids, k, mode, seed):
    """Return a list of (train_indices, test_indices) pairs. Indices refer to rows of the prepared table."""
    n = len(y)
    if mode == "time":
        # rows are already sorted by time; cut them into k+1 consecutive slices
        chunks = np.array_split(np.arange(n), k + 1)
        return [(np.concatenate(chunks[: i + 1]), chunks[i + 1]) for i in range(k)]
    if int(y.sum()) < k or int((1 - y).sum()) < k:
        raise ValueError(f"need at least {k} suspicious and {k} normal rows for {k}-fold cross-validation")
    if mode == "grouped":
        # one group per scenario; ordinary rows are spread over 997 pseudo-random buckets so groups stay few
        scen = pd.Series(scenario_ids).fillna("").astype(str)
        codes = pd.factorize(scen.mask(scen == ""))[0]                       # -1 for rows without a scenario
        bucket = (np.arange(n, dtype=np.int64) * 2654435761 % 997) + codes.max() + 2
        groups = np.where(codes >= 0, codes, bucket)
        return list(StratifiedGroupKFold(k, shuffle=True, random_state=seed).split(np.zeros(n), y, groups))
    return list(StratifiedKFold(k, shuffle=True, random_state=seed).split(np.zeros(n), y))


def _metrics(y, risk, pct, threshold):
    """All headline metrics for one set of predictions. Undefined values (e.g. no positives) are None."""
    pred = risk >= threshold
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum()); tn = int((~pred & (y == 0)).sum())
    div = lambda a, b: round(a / b, 4) if b else None
    p, r = div(tp, tp + fp), div(tp, tp + fn)
    ok = 0 < y.sum() < len(y)  # PR-AUC needs both classes present
    return {"n": int(len(y)), "positives": int(y.sum()), "precision": p, "recall": r,
            "f1": round(2 * p * r / (p + r), 4) if p and r else (0.0 if p is not None and r is not None else None),
            "false_positive_rate": div(fp, fp + tn),
            "pr_auc": round(float(average_precision_score(y, risk)), 4) if ok else None,
            "pr_auc_ml_only": round(float(average_precision_score(y, pct)), 4) if ok else None,  # how good is the forest ALONE?
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def cross_validate(res, labels, folds=5, mode="stratified", seed=42, features=None):
    """K-fold cross-validation of the ML component on an engine.analyze() result.

    res      : dict returned by engine.analyze() (must contain `stage`)
    labels   : ground-truth DataFrame from generate.generate()["labels"]
    folds    : number of folds (2-10)
    mode     : "stratified" | "grouped" | "time"
    seed     : controls the fold assignment and the forests (same seed => same numbers)
    features : feature names to use for the forest (default: the run's selected features)
    """
    if mode not in MODES:
        raise ValueError(f"cross-validation mode must be one of {MODES}")
    if not 2 <= int(folds) <= 10:
        raise ValueError("folds must be between 2 and 10")
    folds = int(folds)
    st = res.get("stage")
    if st is None:
        raise ValueError("no analysable rows to cross-validate")
    t0 = time.time()
    cfg, d, f, flags = res["config"], st["d"], st["f"], st["flags"]
    feats = list(features) if features is not None else st["features"]
    unknown = [x for x in feats if x not in engine.ALL_FEATURES]
    if unknown:
        raise ValueError(f"unknown features: {unknown}")
    X = f[feats].to_numpy(np.float32)
    # rule points are independent of the folds: compute once
    rule_pts = sum(cfg["weights"][p] * flags[p] for p in flags).astype(float)
    # ground truth aligned with the prepared rows (left merge keeps the row order)
    lab = d[["tx_id"]].merge(labels[["tx_id", "is_suspicious", "scenario_id"]], on="tx_id", how="left")
    y = lab.is_suspicious.fillna(0).astype(int).to_numpy()
    rng = np.random.default_rng(seed)

    per_fold, oof_risk, oof_pct, oof_y = [], [], [], []
    for i, (tr, te) in enumerate(_make_folds(y, d.ts.to_numpy(), lab.scenario_id.to_numpy(), folds, mode, seed), 1):
        if len(tr) < 200:  # same rule as the main run: too little data to train a forest
            pct = np.zeros(len(te))
        else:
            fit_rows = tr if len(tr) <= FIT_MAX else rng.choice(tr, FIT_MAX, replace=False)
            model = IsolationForest(n_estimators=100, random_state=seed, n_jobs=engine.N_JOBS).fit(X[fit_rows])
            ref_rows = tr if len(tr) <= REF_MAX else rng.choice(tr, REF_MAX, replace=False)
            ref = np.sort(-model.score_samples(X[ref_rows]))                      # anomaly scores of TRAIN rows
            pct = np.searchsorted(ref, -model.score_samples(X[te])) / len(ref)    # percentile of each TEST row among them
        risk = np.clip(cfg["ml_weight"] * pct + rule_pts[te], 0, 100)
        m = _metrics(y[te], risk, pct, cfg["threshold"])
        m["fold"] = i
        per_fold.append(m)
        oof_risk.append(risk); oof_pct.append(pct); oof_y.append(y[te])

    # mean +- std over folds (folds where a metric is undefined are skipped)
    mean, std = {}, {}
    for k in METRICS:
        v = np.array([m[k] for m in per_fold if m[k] is not None], dtype=float)
        mean[k] = round(float(v.mean()), 4) if len(v) else None
        std[k] = round(float(v.std(ddof=1)), 4) if len(v) > 1 else None
    pooled = _metrics(np.concatenate(oof_y), np.concatenate(oof_risk), np.concatenate(oof_pct), cfg["threshold"])
    return {"folds": folds, "mode": mode, "seed": seed, "features": feats, "n_features": len(feats),
            "per_fold": per_fold, "mean": mean, "std": std, "pooled": pooled, "seconds": round(time.time() - t0, 2),
            "note": {"stratified": "Random folds with equal fraud share. Rows of one scenario may sit in different folds.",
                     "grouped": "Folds never split an injected scenario: the forest is tested on scenarios it has not seen.",
                     "time": "Forward chaining: each fold trains on the past and tests on the next time slice."}[mode]}


def univariate_auc(res, labels):
    """ROC-AUC of each feature ALONE as a fraud score, folded so 0.5 = useless and 1.0 = perfect (either direction).

    A feature with strength near 0.5 on its own *may* still help in combination with others, but
    pure-noise features sit at 0.50 +- 0.01 and are the first candidates to drop."""
    st = res["stage"]
    lab = st["d"][["tx_id"]].merge(labels[["tx_id", "is_suspicious"]], on="tx_id", how="left")
    y = lab.is_suspicious.fillna(0).astype(int).to_numpy()
    rows = []
    for name in engine.ALL_FEATURES:
        x = st["f"][name].to_numpy(float)
        auc = 0.5 if np.ptp(x) == 0 else float(roc_auc_score(y, x))  # constant column => useless by definition
        group = next(g for g, cols in engine.FEATURE_GROUPS.items() if name in cols)
        rows.append({"feature": name, "group": group, "auc": round(auc, 4), "strength": round(abs(auc - 0.5) * 2, 4)})
    return sorted(rows, key=lambda r: -r["strength"])


# ---------------------------------------------------------------------------------------------
# command-line report
# ---------------------------------------------------------------------------------------------
def _line(title, cv):
    m, s = cv["mean"], cv["std"]
    f = lambda k: f"{m[k]:.3f}+-{s[k]:.3f}" if m[k] is not None and s[k] is not None else "n/a"
    return f"{title:<34} F1 {f('f1'):<14} PR-AUC {f('pr_auc'):<14} ML-only PR-AUC {f('pr_auc_ml_only'):<14} ({cv['n_features']} features, {cv['seconds']}s)"


def main():
    ap = argparse.ArgumentParser(description="Cross-validation and feature-selection report on synthetic data")
    ap.add_argument("n_tx", nargs="?", type=int, default=100_000, help="background transactions to generate (default 100000)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--mode", choices=MODES, default="stratified")
    ap.add_argument("--drop", default="", help="comma separated feature names to disable")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    from . import generate
    data = generate.generate(generate.default_accounts(a.n_tx), a.n_tx, seed=a.seed)
    drop = [x for x in a.drop.split(",") if x]
    res = engine.analyze(data["raw"], {"disabled_features": drop} if drop else None)
    print(f"{len(res['scored']):,} rows analysed in {res['timings']['total_s']}s; {a.folds}-fold CV, mode={a.mode}\n")

    base = cross_validate(res, data["labels"], a.folds, a.mode, a.seed)
    print(f"{'fold':<6}{'n':>9}{'pos':>7}{'precision':>11}{'recall':>9}{'F1':>8}{'PR-AUC':>9}{'ML-only':>9}")
    for m in base["per_fold"]:
        g = lambda k: "n/a" if m[k] is None else f"{m[k]:.3f}"
        print(f"{m['fold']:<6}{m['n']:>9,}{m['positives']:>7}{g('precision'):>11}{g('recall'):>9}{g('f1'):>8}{g('pr_auc'):>9}{g('pr_auc_ml_only'):>9}")
    print("\n" + _line("current selection", base))

    print("\n--- ablation: drop one whole group ---")
    for grp, cols in engine.FEATURE_GROUPS.items():
        keep = [x for x in base["features"] if x not in cols]
        if len(keep) >= 3:
            print(_line(f"without group '{grp}'", cross_validate(res, data["labels"], a.folds, a.mode, a.seed, keep)))

    print("\n--- univariate ranking (strength 0 = useless, 1 = perfect) ---")
    for r in univariate_auc(res, data["labels"]):
        print(f"{r['feature']:<22}{r['group']:<7}AUC {r['auc']:.3f}  strength {r['strength']:.3f}  {'#' * int(r['strength'] * 40)}")
    print("\nTip: drop the weakest with  --drop name1,name2  or per request via config {\"disabled_features\": [...]}")


if __name__ == "__main__":
    main()
