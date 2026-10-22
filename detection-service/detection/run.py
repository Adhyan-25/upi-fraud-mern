"""Command-line runner:  python -m detection.run [n_accounts] [n_transactions]

Generates a synthetic dataset, writes it to data/*.csv (the benchmark scripts read
data/transactions_raw.csv), runs the engine, then prints evaluation metrics, a 5-fold
cross-validation summary and timings. Handy for checking the Python side of the project
without starting any server.

Examples
    python -m detection.run                      # 2,000 accounts, 20,000 transactions (a few seconds)
    python -m detection.run 100000 1000000       # one MILLION transactions (needs ~4 GB RAM, about 1-2 minutes)
"""
import json, os, sys, time
from . import crossval, engine, generate


def main():
    n_tx = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    n_acc = int(sys.argv[1]) if len(sys.argv) > 1 else generate.default_accounts(n_tx) if len(sys.argv) <= 2 and n_tx != 20000 else 2000
    t = time.time()
    d = generate.generate(n_acc, n_tx, seed=42)
    print(f"generated {len(d['raw']):,} rows ({n_acc:,} accounts) in {time.time() - t:.1f}s")
    os.makedirs("data", exist_ok=True)
    d["raw"].to_csv("data/transactions_raw.csv", index=False)          # what the engine receives
    d["labels"].to_csv("data/scenario_labels.csv", index=False)        # ground truth (evaluation only)
    d["users"].to_csv("data/users.csv", index=False)
    res = engine.analyze(d["raw"], max_explain=100)
    ev = engine.evaluate(res, d["labels"])
    print("summary   :", json.dumps(res["summary"], indent=1))
    print("quality   :", res["quality"])
    print("timings   :", res["timings"])
    print("evaluation:", json.dumps(ev, indent=1))
    cv = crossval.cross_validate(res, d["labels"], folds=5, mode="stratified")
    print(f"\n5-fold cross-validation ({cv['seconds']}s, {cv['mode']}):")
    for k in crossval.METRICS:
        print(f"  {k:<22} {cv['mean'][k]} +- {cv['std'][k]}")


if __name__ == "__main__":
    main()
