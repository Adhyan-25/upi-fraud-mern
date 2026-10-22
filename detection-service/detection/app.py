"""Flask HTTP wrapper around the detection engine.

This is the Python "microservice" of the MERN app. The Express server calls it over HTTP:

  GET  /health     liveness probe (used by docker-compose and by Express /api/health)
  GET  /detectors  describes the default configuration, features and scoring method
  POST /analyze    run the pipeline on uploaded transactions OR on freshly generated synthetic data

It is stateless: nothing is stored here. MongoDB persistence is Express's job.
"""
import logging
import pandas as pd
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from . import crossval, engine, generate, report

log = logging.getLogger("detection")
# Safety caps so one request cannot exhaust the server's memory / CPU.
# 1,500,000 rows needs roughly 4-6 GB of RAM for generation + analysis; one million fits comfortably in 8 GB.
MAX_TX, MAX_ACCOUNTS = 1_500_000, 200_000


def create_app():
    """Application factory (also used by the unit tests via app.test_client())."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # Flask answers 413 above 300 MB (a few hundred thousand uploaded rows)

    @app.errorhandler(Exception)
    def on_error(e):
        """One handler for every error => consistent JSON; internals are logged, never returned."""
        if isinstance(e, HTTPException):  # 404, 405, 413 ... keep their own status code
            return jsonify(error=e.description), e.code
        log.exception("unhandled error")
        return jsonify(error="internal error"), 500

    @app.get("/health")
    def health():
        return jsonify(status="ok", service="detection")

    @app.get("/detectors")
    def detectors():
        return jsonify(config=engine.DEFAULT_CONFIG, features=engine.ALL_FEATURES, feature_groups=engine.FEATURE_GROUPS,
                       required_columns=engine.REQUIRED, optional_columns=engine.OPTIONAL,
                       cross_validation={"modes": list(crossval.MODES), "folds": "2-10 (default 5)"},
                       ml="IsolationForest (unsupervised, fit on earliest train_fraction)",
                       scoring="risk_score = ml_weight*percentile + sum(rule weights), clipped 0-100; not a probability")

    @app.post("/analyze")
    def analyze():
        """Body: {"transactions":[...]}  OR  {"generate":{n_accounts,n_transactions,seed}},
        plus optional "config" (overrides, see engine.DEFAULT_CONFIG), "limit" (1-1000, default 100) and
        "cross_validation": {"enabled": true, "folds": 5, "mode": "stratified"|"grouped"|"time"} (synthetic data only,
        because cross-validation needs ground-truth labels).
        """
        body = request.get_json(silent=True)  # silent => None instead of raising on bad JSON
        if not isinstance(body, dict):
            return jsonify(error="JSON object body required"), 400
        labels = None
        n_accounts_total = None
        cv_req = body.get("cross_validation") or {}
        try:
            if not isinstance(cv_req, dict):
                raise ValueError("cross_validation must be an object")
            limit = int(body.get("limit", 100))
            if not 1 <= limit <= 1000:
                raise ValueError("limit must be within [1, 1000]")
            if "transactions" in body:
                # ---- mode 1: caller supplied their own transactions ----
                tx = body["transactions"]
                if not isinstance(tx, list) or not all(isinstance(r, dict) for r in tx):
                    raise ValueError("transactions must be a list of objects")
                if len(tx) > MAX_TX:
                    return jsonify(error=f"too many records (max {MAX_TX})"), 413
                df = pd.DataFrame(tx) if tx else pd.DataFrame(columns=engine.REQUIRED)
            else:
                # ---- mode 2: generate synthetic data (labels are kept so we can evaluate) ----
                g = body.get("generate", {})
                nt = int(g.get("n_transactions", 10000))
                na = int(g["n_accounts"]) if g.get("n_accounts") is not None else generate.default_accounts(nt)  # ~1 account per 10 transactions
                if not (60 <= na <= MAX_ACCOUNTS and 100 <= nt <= MAX_TX):
                    raise ValueError(f"n_accounts in [60, {MAX_ACCOUNTS}] and n_transactions in [100, {MAX_TX}] required")
                data = generate.generate(na, nt, seed=int(g.get("seed", 42)))
                df, labels = data["raw"], data["labels"]
                n_accounts_total = na
            # only the riskiest `limit` flagged rows are explained (explaining is a Python loop; it matters at 1M rows)
            res = engine.analyze(df, body.get("config"), max_explain=limit)  # raises ValueError on bad config / missing columns
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400
        # ---- optional K-fold cross-validation (needs labels, so synthetic runs only) ----
        cv = None
        if cv_req.get("enabled"):
            if labels is None:
                res["warnings"].append("cross-validation skipped: it needs ground-truth labels, which only synthetic runs have")
            else:
                try:
                    cv = crossval.cross_validate(res, labels, folds=cv_req.get("folds", 5), mode=cv_req.get("mode", "stratified"))
                    res["timings"]["cv_s"] = cv["seconds"]
                except (ValueError, TypeError) as e:  # a failed CV must not throw away the whole analysis
                    res["warnings"].append(f"cross-validation skipped: {e}")
        out = {k: res[k] for k in ("summary", "quality", "warnings", "timings")}
        out["flagged"] = res["flagged"][:limit]
        # NaN is not valid JSON (JavaScript's JSON.parse rejects it), so turn missing cells into null first
        rej = res["rejected"].head(50).astype(object)
        out["rejected_rows"] = rej.where(rej.notna(), None).to_dict("records")
        if labels is not None:
            out["evaluation"] = engine.evaluate(res, labels)
        # presentation-shaped payload consumed by the Express server / React dashboard
        out["cross_validation"] = cv
        out["dashboard"] = report.build_report(res, labels, accounts_total=n_accounts_total, max_cases=limit, cv=cv)
        out["config"] = res["config"]
        return jsonify(out)

    return app


if __name__ == "__main__":
    # `python -m detection.app` => development server on :5001 (use gunicorn in production, see Dockerfile)
    create_app().run(host="0.0.0.0", port=5001)
