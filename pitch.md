# 🛡️ UPI Fraud Pattern Detector — Project Pitch

> **Prototype disclaimer:** This system works on **synthetic (made-up) data only**. A high risk score is a *model suspicion*, not proof of fraud. This is not a law-enforcement or financial enforcement system.

---

## 📌 One-Line Summary

A full-stack, containerised web application that ingests UPI-style transaction data, identifies suspicious money-movement patterns using a hybrid rule + ML engine, and lets fraud analysts review, annotate, and escalate flagged cases — all in real time.

---

## 🧠 What Problem Does It Solve?

India's UPI ecosystem processes **hundreds of millions of transactions daily**. Manual review of fraud is impossible at scale. Existing rule-only engines miss subtle patterns; pure ML models are opaque and hard to audit.

This project demonstrates a **hybrid detection approach** that:
- Combines hand-crafted fraud rules (explainable, auditable) with an unsupervised ML anomaly model (catches what rules miss)
- Provides **plain-English reasons** for every flag
- Gives analysts a full **case-management workflow** (Open → Investigating → Escalated → Resolved)
- Scales to **1,000,000 transactions in ~39 seconds** end-to-end

---

## 🏗️ Architecture Overview

```
 ┌────────────┐  /api/*   ┌──────────────────┐   HTTP    ┌────────────────────┐
 │  Browser   │ ────────► │ Express API      │ ────────► │ Flask Detection    │
 │  React UI  │ ◄──────── │ (Node.js :5000)  │ ◄──────── │ Service (:5001)    │
 └────────────┘   JSON    │ Zod validation   │  JSON     │ pandas, sklearn,   │
   (Vite dev /            │ Mongoose ORM     │           │ networkx           │
    nginx prod)           └────────┬─────────┘           └────────────────────┘
                                   │ Mongoose
                                   ▼
                          ┌──────────────────┐
                          │ MongoDB (:27017) │  analyses + cases collections
                          └──────────────────┘
```

| Layer | Technology | Folder | Port |
|---|---|---|---|
| **M**ongoDB | MongoDB 6/7 + Mongoose | – | 27017 |
| **E**xpress | Node.js 18+ / Express 4 | `server/` | 5000 |
| **R**eact | React 18 + Vite | `client/` | 5173 (dev) / 8080 (Docker) |
| **N**ode.js | Runtime for API + build tooling | – | – |
| Detection Engine | Flask + pandas + scikit-learn + networkx | `detection-service/` | 5001 |

> **Why Python for detection?** scikit-learn's `IsolationForest`, pandas vectorised operations, and networkx cycle search are mature, battle-tested Python libraries. Rewriting them in JavaScript would add risk and lose those libraries. The MERN app wraps the Python service as a **stateless microservice** — everything persistent and user-facing stays in MongoDB/Express/React/Node.

---

## ⚙️ How the Detection Works (Step-by-Step Pipeline)

### Step 1 — Validation
Every row is checked for:
- Missing `tx_id`, unparseable timestamp, non-positive or absurd amount (> ₹1 crore), sender == receiver
- **Bad rows are never silently dropped** — they come back with a `reject_reason` so the UI can display them

### Step 2 — Cleaning
- Duplicate `tx_id`s removed (first occurrence wins)
- `FAILED` transfers excluded from analysis (but counted in the quality report)
- Rows sorted chronologically

### Step 3 — Causal Feature Engineering (23 Features)
Features are computed so that **each row only uses strictly earlier rows** — meaning the model could run online (streaming) without peeking into the future. Unit tests enforce this causality guarantee.

| Group | Features (9) |
|---|---|
| **Core** (9) | `log_amount`, `amount_z`, `sender_cnt_win`, `sender_cnt_1d`, `receiver_in_cnt_1d`, `log_since_prev`, `inflow_ratio`, `pair_cnt_win`, `hour` |
| **IP** (4) | `ip_is_new`, `ip_other_tx_win`, `ip_cnt_win`, `ip_distinct_senders` |
| **Extra** (10) | `device_code`, `channel_collect`, `cross_state`, `log_account_age`, `kyc_full`, `note_len`, `network_code`, `app_major`, `sender_bank_code`, `receiver_bank_code` |

The vectorised `_Events` class answers "how many transfers from this sender in the last N minutes?" in **O(log n)** using sorted packed integers and prefix sums — no Python loops over rows.

### Step 4 — Six Rule Detectors

| Pattern | What It Catches | Trigger Condition |
|---|---|---|
| **Circular Transfer** (`cycle`) | Money round-tripping: A→B→C→A (layering) | Cycle of 3–5 accounts within 48 h, each edge ≥ ₹1,000 |
| **Rapid Onward Transfer** (`rapid_onward`) | Mule behaviour: receive and immediately pass on | Sender received 70%–150% of this amount within 30 min; amount ≥ ₹5,000 |
| **Burst** (`burst`) | Account takeover: sudden spray of transfers | ≥ 8 transfers by one sender in 10 minutes |
| **Split** (`split`) | Structuring: breaking one large payment into many small ones | ≥ 4 transfers ≤ ₹5,000 between the same pair in 60 min, totalling ≥ ₹10,000 |
| **Unusual Behaviour** (`unusual_behavior`) | Amount far above the sender's own history | Log-amount ≥ 4 std-devs above sender's past mean; needs ≥ 5 prior transfers and amount ≥ ₹10,000 |
| **Irregular IP** (`irregular_ip`) | Account farm or account takeover via IP signals | **(a)** ≥ 3 other accounts used this IP in the last 2 h, **OR** **(b)** brand-new IP for a sender with ≥ 5 prior transfers and amount ≥ ₹5,000 |

### Step 5 — Cycle Detection (Graph Analysis)
Uses **NetworkX** on 48-hour sliding windows:
- Pre-prunes edges iteratively using `np.bincount` (senders without incoming / receivers without outgoing are removed before building the graph)
- Finds all simple cycles of length 3–5 within each strongly-connected component
- Hard safety bounds: `cycle_max_scc=60`, `cycle_max_per_window=500` — cannot blow up on adversarial data
- On 1,000,000 rows: takes **< 1 second** thanks to pre-pruning

> **Note:** Cycle detection is *retrospective* (uses a window that includes future rows), so its recall (≈ 1.0) is not comparable with the causal rules.

### Step 6 — ML Anomaly Score (IsolationForest)
- Trained **unsupervised** on the chronologically earliest 60% of rows (never sees fraud labels)
- Scores all rows; converts raw anomaly scores to a **percentile** relative to training rows
- Only the ML percentile changes when features are disabled — the rules are unaffected

### Step 7 — Risk Score Computation
```
risk_score = (ml_weight × ml_percentile) + Σ(weight_of_each_fired_rule)
```
Clipped to [0, 100].

| Score Range | Category |
|---|---|
| ≥ 70 | 🔴 **Critical** |
| 50 – 69 | 🟠 **High** |
| 30 – 49 | 🟡 **Medium** |
| < 30 | 🟢 **Low** |

Default flag threshold: **50**. No single rule (except `cycle` at weight 50) can reach the threshold alone — most flags require two signals or ML support.

### Step 8 — Human-Readable Explanation
Every flagged case gets a plain-English explanation per fired pattern. Examples:
- *"ACC00023 made 12 transfers within 10 minutes"*
- *"IP 57.134.53.219 was used by 4 other accounts in the previous 120 minutes"*
- *"amount is 5.2 log-std above the sender's prior history (8 prior transfers)"*

### Step 9 — Evaluation (Synthetic Data Only)
Measured on the holdout rows (after the 60% training cut-off) against hidden ground-truth labels:
- Precision, Recall, F1, False Positive Rate
- Precision@k (k = number of true positives — an oracle ranking metric)
- PR-AUC, Confusion Matrix
- Recall per injected scenario type
- Benign look-alike flag rate (how often innocent-but-suspicious behaviour is flagged)

---

## 📊 Performance Benchmarks

### On 20,000 Transactions (2,000 Accounts, Seed 42)

| Mode | Precision | Recall | F1 | PR-AUC |
|---|---|---|---|---|
| Stratified 5-fold CV | 0.866 ± 0.027 | 0.630 ± 0.065 | 0.728 ± 0.046 | 0.772 ± 0.040 |
| Grouped 5-fold CV | 0.871 ± 0.080 | 0.630 ± 0.010 | 0.730 ± 0.029 | 0.781 ± 0.048 |
| Time-series CV | 0.847 ± 0.063 | 0.636 ± 0.063 | 0.724 ± 0.042 | 0.776 ± 0.065 |

### On 1,000,000 Transactions (100,000 Accounts, Stratified CV)

| Metric | Value |
|---|---|
| Precision | 0.850 ± 0.008 |
| Recall | 0.617 ± 0.006 |
| F1 | 0.715 ± 0.005 |
| PR-AUC | 0.762 ± 0.010 |

### Timing (1,000,000 Transactions — 2 CPUs, 8 GB RAM)

| Step | Time |
|---|---|
| Generate synthetic data | ~9 s |
| Validate + features + rules + IsolationForest + scoring | ~18 s |
| 5-fold cross-validation | ~10 s |
| **Full end-to-end via Express API + MongoDB** | **~39 s** |
| Peak Python process memory | **1.7 GB** |

### Speed Improvement vs. Baseline Engine (100,000 rows)
- Old engine: **~2.27 s**
- New engine (same 9 core features, no IP rule): **~1.62 s** (**≈ 1.4× faster**)
- With all 23 features + IP rule: **~1.8 s** (still faster, with more capability)
- Output is **byte-identical** between old and new engines (verified by A/B benchmark)

---

## 🗄️ Database Design

### MongoDB Database: `upi_fraud`

**`analyses` collection** — one document per detection run

| Field | Purpose |
|---|---|
| `name`, `source`, `params` | Label and origin (synthetic vs upload) |
| `config` | Effective detector settings used |
| `meta` | Headline counts: records, flagged, critical, high, accounts, days, duplicates, rejected, runtime |
| `volume.total[]`, `volume.flagged[]` | Per-day counts for the timeline chart |
| `patterns` | `{ burst: 49, cycle: 40, … }` |
| `evaluation` | Precision/recall/F1/PR-AUC (synthetic) or `null` (uploads) |
| `crossValidation` | Per-fold metrics, mean ± std, pooled out-of-fold metrics |
| `quality`, `rejectedRows[]`, `warnings[]`, `timings` | Data quality report, rejected row samples, engine warnings |
| `caseCount` | How many Case documents were stored (≤ requested limit) |

**`cases` collection** — one document per flagged transaction

| Field | Purpose |
|---|---|
| `analysis` | Reference to the run (indexed) |
| `txId`, `timestamp`, `sender`, `receiver`, `amount` | Transaction data |
| `riskScore`, `riskCategory`, `mlPercentile` | Detection output (**immutable**) |
| `ip`, `device`, `channel` | Transfer context (from optional columns) |
| `evidence[]` | `{pattern, weight, text}` — why it was flagged |
| `graph[]` | `{from, to, amount, flagged, focus}` — nearby transfers for the money-flow graph |
| `status` | `Open` → `Investigating` → `Escalated` → `Resolved` |
| `notes` | Analyst notes (≤ 5,000 chars) |
| `history[]` | Audit trail of status changes with timestamps |

**Indexes:**
- `(analysis, txId)` — unique; ensures one case per transaction per run
- `(analysis, riskScore DESC)` — powers the default "riskiest first" queue
- `(analysis, evidence.pattern)` — powers the pattern filter
- Deleting a run cascades to delete all its cases

---

## 🔌 REST API Reference

| Method & Endpoint | Purpose |
|---|---|
| `GET /api/health` | `{status, mongo, detection}` — 503 if any dependency is down |
| `GET /api/detectors` | Default config, feature groups, scoring formula |
| `POST /api/analyses` | Run on synthetic data → 201 + analysis |
| `POST /api/analyses/upload` | Run on uploaded CSV/JSON (multipart) → 201 + analysis |
| `GET /api/analyses` | List all runs (newest first, light fields) |
| `GET /api/analyses/:id` | Full run details + per-status case counts |
| `GET /api/analyses/:id/cases` | Paged/filtered review queue |
| `DELETE /api/analyses/:id` | Delete run and all its cases |
| `GET /api/cases/:id` | Full case with evidence, graph, IP/device/channel, status, notes, history |
| `PATCH /api/cases/:id` | Update `status` and/or `notes` only |

**Key API parameters (`POST /api/analyses`):**
```json
{
  "nTransactions": 20000,
  "nAccounts": 2000,
  "seed": 42,
  "limit": 100,
  "cv": true,
  "cvFolds": 5,
  "cvMode": "stratified",
  "disabledFeatures": ["note_len", "app_major"],
  "config": { "threshold": 60, "weights": { "irregular_ip": 25 } }
}
```

**Case queue filters (`GET /api/analyses/:id/cases`):**
- `category` — `critical` | `high` | `medium` | `low`
- `pattern` — `cycle` | `rapid_onward` | `burst` | `split` | `unusual_behavior` | `irregular_ip`
- `status` — `Open` | `Investigating` | `Escalated` | `Resolved`
- `q` — text search on transaction ID, sender, or receiver
- `page`, `limit` — pagination (up to 200 per page)

---

## 🎨 Dashboard Features

| Component | What It Shows |
|---|---|
| **Header** | 3 status dots: API · MongoDB · Detection (auto-refreshed every 15s) |
| **Run Bar** | Select/delete a run; generate synthetic data with presets (20K → 1M); upload CSV/JSON; configure CV settings and feature selection |
| **Summary Bar** | Total records, flagged, critical, high counts; case workflow status breakdown |
| **Timeline Chart** | Grey = total transactions/day; red bars = flagged/day; hover for counts |
| **Review Queue** | Sorted by risk (riskiest first); filter by risk level, pattern, or status; text search; pagination |
| **Case Detail Panel** | Risk score badge, IP/device/channel chips, evidence list with explanations, money-flow graph (click an account to isolate it), status dropdown (auto-saved), notes (auto-saved ~0.7s after typing stops), status history |
| **Pattern Bars** | Flagged transactions per detector |
| **Evaluation Panel** | Precision, recall, F1, false-positive rate, PR-AUC, confusion matrix (synthetic only) |
| **Scenario Recall Panel** | Per-scenario recall rates + benign look-alike flag rate |
| **Cross-Validation Panel** | Per-fold table, mean ± std, pooled out-of-fold metrics |

---

## 📈 Feature Selection

### Which Features Carry Signal?

| Group | Signal Level | Key Insight |
|---|---|---|
| `log_amount`, `amount_z` (core) | **Strong** — strength ≈ 0.78 | The most important ML features |
| `ip_cnt_win` (IP group) | **Strong** — strength ≈ 0.63 | IP group clearly worth keeping |
| `log_account_age`, `note_len`, `channel_collect` (extra) | **Weak** — strength 0.3–0.35 | Borderline; worth comparing with/without |
| `device_code`, `network_code`, `app_major`, `cross_state`, `sender_bank_code` (extra) | **Noise** — strength ≈ 0.01–0.02 | Good candidates to drop |

### Impact of Removing Groups (50,000 tx example)

| Scenario | F1 | PR-AUC | ML-only PR-AUC |
|---|---|---|---|
| All 23 features | 0.717 ± 0.025 | 0.778 ± 0.041 | 0.645 ± 0.049 |
| Without `core` group | 0.725 ± 0.025 | 0.675 ± 0.040 | 0.271 ± 0.035 |
| Without `ip` group | 0.716 ± 0.025 | 0.733 ± 0.035 | 0.490 ± 0.025 |
| Without `extra` group | 0.716 ± 0.025 | **0.813 ± 0.038** | 0.741 ± 0.027 |

> Removing the **entire `extra` group raises PR-AUC** (0.778 → 0.813) because noise features hurt the ML model. The `ip` group is clearly valuable (PR-AUC drops to 0.733 without it).

### Four Ways to Disable Features
1. In the dashboard — type feature names into the "drop features" box
2. Per API request — `"disabledFeatures": ["note_len", "app_major"]`
3. Inside `config` — `"config": { "disabled_features": ["note_len"] }`
4. Process-wide — environment variable `UPI_DISABLED_FEATURES="note_len,app_major"`

**Rules never change when features are dropped** — only the ML percentile is affected. At least 3 features must remain enabled.

---

## 🔄 5-Fold Cross-Validation

### What Is Being Cross-Validated?
Only the **IsolationForest** (the trained part). The rule thresholds are fixed and hand-set. For each of the 5 folds:
1. Fresh forest fitted on 4/5 of the rows (unsupervised — never sees labels)
2. Anomaly scores converted to percentiles relative to those training rows
3. Risk scores computed for the held-out 1/5 (rule contributions are identical in every fold)
4. Precision, Recall, F1, FPR, PR-AUC measured against ground-truth labels

### Three Split Modes

| Mode | How Folds Are Split | Best For |
|---|---|---|
| `stratified` (default) | Random, equal fraud share in each fold | Classic 5-fold CV |
| `grouped` | All rows of one scenario stay in one fold | Strictest: forest never trains on the scenario it tests |
| `time` | Forward chaining — train on the past, test on the next slice | Closest to real production use |

> Cross-validation requires ground-truth labels — it only runs for **synthetic data**. Upload runs show a note explaining the skip.

---

## 📂 Data Columns: Signal vs. Noise

| Column | Signal Level | How the Generator Uses It |
|---|---|---|
| `tx_id`, `timestamp`, `sender`, `receiver`, `amount`, `status` | Required | Core 9 features |
| `ip_address` | **Real signal** | Fraud: shared IPs for account farms; new IPs for takeovers. Normal: home IP or 25% shared mobile-carrier NAT |
| `channel` | **Weak signal** | Fraud rows use "collect" (pull request) 25% vs. 5% normally |
| `account_age_days` | **Weak signal** | ~40% of fraud accounts are 3–60 days old (mule accounts) |
| `kyc_level` | **Weak signal** | Mule accounts are more often minimum-KYC |
| `txn_note_len` | **Weak signal** | Fraud notes are more often empty or ≤ 3 characters |
| `device_type` | **Noise** | Generated independently of fraud status |
| `network_type` | **Noise** | Generated independently of fraud status |
| `app_version` | **Noise** | Generated independently of fraud status |
| `sender_bank`, `receiver_bank` | **Noise** | Generated independently of fraud status |
| `sender_state`, `receiver_state` | **Noise** | Generated independently of fraud status |

---

## 🧪 Testing

| Suite | Count | What's Covered |
|---|---|---|
| Python (pytest) | **71 tests** | Engine validation, causality/leakage proofs, IP features vs. brute-force definitions, feature selection, cross-validation fold properties, report building, Flask API |
| Node.js (node:test + supertest) | **28 tests** | REST API endpoints, Zod validation, CSV/JSON parsing, analyst workflow (status/notes), error handling |

**Key test highlights:**
- IP features numerically equal their brute-force reference implementations
- The first transfers of an IP farm *cannot* fire the "shared IP" rule (causality verified)
- Every fold partitions rows exactly once (never in both train and test simultaneously)
- Grouped folds never split a scenario across fold boundaries
- Time folds only train on chronologically earlier data
- Cross-validation is reproducible and beats random chance
- 200,000 accounts do not overflow the packed 64-bit integer keys

---

## 🔒 Security

| Control | Implementation |
|---|---|
| Security headers | `helmet` middleware |
| CORS | Restricted to configured origins only |
| Input validation | Zod schemas on the Express API; strict config validation in Python |
| Rate limiting | 300 req/min/IP general; 20/min on the two engine-triggering endpoints |
| Upload size limit | 50 MB default (`MAX_UPLOAD_MB`) |
| ObjectId validation | All `:id` params validated before DB queries |
| Text search safety | Search strings are regex-escaped |
| `PATCH` protection | Only `status` and `notes` are accepted — `riskScore`, `evidence`, etc. cannot be overwritten |
| Error responses | Generic JSON messages; stack traces are logged server-side only, never sent to clients |
| Input caps (Python) | ≤ 1,500,000 transactions, ≤ 200,000 accounts, ≤ 300 MB request body |
| **No authentication** | This is a local prototype — authentication (JWT/sessions + roles) must be added before any network exposure |

---

## ⚠️ Honest Limitations

| Limitation | Detail |
|---|---|
| Synthetic-only evaluation | Real UPI fraud data is private; all metrics are on simulated data |
| Optimistic fraud rate | Synthetic holdout has ~1% fraud — real-world rates are orders of magnitude lower, so precision/PR-AUC would be much lower in production |
| Optimistic thresholds | Rules were designed knowing the generator's scenario shapes |
| Seed variance | Across 4 other seeds (2,000 accounts / 20,000 tx): precision 0.77–0.88, recall 0.57–0.69 |
| Causal recall caps | Burst (~0.42), split (~0.50), IP-farm (~0.64–0.83) recall are capped because the *first* transfers of a burst/split/farm cannot see prior evidence yet |
| Cycle recall (1.0) is retrospective | Not comparable with the causal rule recalls |
| Benign traveller false positives | An established account paying from a new IP is flagged ~5% of the time |
| Rules not cross-validated | Only IsolationForest is retrained per fold; rule thresholds are fixed (designed with the generator in mind) |
| Synchronous analyses | All analyses run inside the HTTP request; a job queue would be needed for concurrent users or very large files |
| No streaming upload | Uploading 1M rows parses everything in Node memory first; for very large files, the Python service should be called directly |

---

## ❓ Anticipated Interview / Presentation Questions

### Architecture & Design

**Q: Why split the backend into Express + Flask instead of doing everything in Node.js?**
> The fraud detection engine relies heavily on pandas (vectorised DataFrames), scikit-learn (IsolationForest), and networkx (graph cycle search). These are mature Python libraries with no equivalent in the JS ecosystem. Rewriting them in Node would add significant engineering risk. By wrapping Python as a stateless microservice, the MERN stack handles all user-facing concerns (auth, persistence, API validation, rate limiting) while Python handles analytics. The boundary is clean: Express sends a JSON payload, Flask returns a JSON result.

**Q: How does the system scale?**
> Currently analyses run synchronously inside the HTTP request. For production use, the next step would be a job queue (e.g. Bull/Redis) so requests return a job ID immediately and the client polls for completion. The Python engine itself is already fully vectorised and handles 1M transactions in ~18 seconds with 1.7 GB peak RAM.

**Q: Why use MongoDB instead of PostgreSQL?**
> The analysis result is a large, deeply nested JSON object (timelines, per-fold metrics, evidence arrays, money-flow graphs). MongoDB's document model stores this naturally without JOIN tables. Cases and analyses have a simple parent-child relationship that is easy to express with ObjectId references. For a production system with complex reporting queries, a relational DB or a hybrid approach might be preferable.

---

### Detection Engine

**Q: What is the risk score and what does it mean?**
> It is a 0–100 **heuristic ranking score**, not a fraud probability. It is computed as `ml_weight × IsolationForest_percentile + sum_of_fired_rule_weights`, clipped to [0, 100]. It is designed for **ranking** transactions from most to least suspicious so analysts can prioritise their review queue. No single rule (except `cycle`) can reach the 50-point threshold alone — most flags require two signals or ML support, reducing noise.

**Q: Why IsolationForest rather than a supervised model?**
> Supervised models require labelled data, which is unavailable for real UPI transactions. IsolationForest is **unsupervised** — it identifies anomalies by observing which transactions are hardest to isolate in a random tree ensemble. This means it can be deployed without any labelled fraud history and can still catch novel patterns. The trade-off is lower precision vs. a well-tuned supervised classifier.

**Q: What does "causal" mean in this context, and why does it matter?**
> Every feature is computed using only transactions that occurred **strictly before** the current row in time. This means: (1) the model could be deployed in a real-time streaming setting without modification, (2) there is no data leakage (the model cannot "cheat" by using future information), and (3) performance metrics are realistic. Unit tests verify causality by asserting that adding future rows never changes the computed features for past rows.

**Q: How does cycle detection work at scale without blowing up?**
> The cycle detector uses several bounding strategies: (1) a 48-hour sliding window; (2) edges below ₹1,000 are excluded before graph construction; (3) edges whose sender never receives (or whose receiver never sends) within the window are iteratively pruned using `np.bincount` before building the NetworkX graph; (4) only edges within strongly-connected components of size 3–60 are explored; (5) at most 500 cycles are reported per window. These bounds make it safe on adversarial data while running in under a second on 1M rows.

**Q: Why can burst/split recall never reach 100%?**
> Because the rules are causal. For a **burst**, the first (n-1) transfers of an n-transfer burst are sent before the sender count reaches the threshold — they cannot be flagged when they occur. Similarly for **split**: the first few transfers of a splitting pattern haven't yet accumulated enough pair count. This is not a bug; it is an inherent property of real-time, non-peeking detection. Cycle recall is 100% only because cycle detection is retrospective (it uses the full 48-hour window, including future rows).

**Q: How does the IP address signal work?**
> Two complementary rules:
> - **(a) Account farm:** ≥ 3 transfers from *other* accounts came from the same IP in the previous 2 hours, AND this transfer is ≥ ₹1,000. Signals that one operator is driving many accounts from the same device.
> - **(b) Account takeover:** The sender is using an IP never seen before for them, has ≥ 5 prior transfers (established account), and the amount is ≥ ₹5,000. Signals that someone else has the credentials.
> All IP features are causal — they only count earlier rows.

---

### Data & Evaluation

**Q: What is the synthetic data generator doing?**
> It creates fictional accounts (`ACC00000`…) and generates "legitimate" background traffic (lognormal amounts, daytime-heavy timestamps, 40% going to merchants). It then **injects suspicious scenarios**: circular transfers, rapid onward transfers, splitting, bursts, unusual-behaviour, and IP farms. It also injects **benign look-alikes** (innocent behaviour that resembles fraud, used to measure false-positive rate). Ground-truth labels are stored separately and are **never passed to the detection engine** — only used in `evaluate()` and cross-validation.

**Q: What does PR-AUC mean and why use it instead of AUC-ROC?**
> PR-AUC (Precision-Recall Area Under Curve) is better suited for **imbalanced classes** like fraud detection, where the negative class (legitimate transactions) vastly outnumbers the positive class (fraud). AUC-ROC can look artificially high when negatives dominate because true-negative rate is easy to maximise. PR-AUC focuses on how well the model ranks actual fraud cases at the top, which is what matters for an analyst review queue.

**Q: What is Precision@k?**
> An "oracle" ranking metric: k is set to the number of true positives in the holdout. It answers "if an analyst reviewed the top k cases by risk score, what fraction would be true fraud?" It is oracle (unrealistic in deployment because k is known from labels), but it cleanly separates **ranking quality** from threshold choice.

**Q: Why is the synthetic fraud rate (~1%) still much higher than real life?**
> Real UPI fraud rates are fractions of a percent (often 0.01%–0.1%). The synthetic generator uses ~1% to give the model enough signal to learn from and to make evaluation metrics meaningful with smaller datasets. In production, with far fewer true positives, precision and PR-AUC would be considerably lower — a limitation explicitly noted in the README.

---

### Feature Selection

**Q: What is the purpose of including noise features?**
> To simulate a realistic feature-selection problem. In practice, data scientists receive datasets with many candidate features of unknown quality. The noise columns (`device_type`, `network_type`, `app_version`, `sender_bank`, `receiver_bank`, `sender_state`, `receiver_state`) exist so users can **practise identifying and removing useless features**, observe that IsolationForest sometimes performs *better* without them, and use the built-in ablation report to guide their choices.

**Q: Does dropping features affect the fraud rules?**
> No. The six fraud rules (cycle, burst, split, rapid_onward, unusual_behavior, irregular_ip) use the raw transaction columns directly. Only the **IsolationForest ML score** is affected by feature selection. At least 3 features must remain enabled.

---

### Infrastructure & Deployment

**Q: How is the app deployed?**
> Two options: (1) **Docker Compose** (easiest) — a single `docker compose up --build` starts MongoDB, the Flask detection service, the Express API, and the React frontend served by nginx. (2) **Manual** — run three terminal processes: Python Flask service, Node Express API, and React Vite dev server. All ports, timeouts, and MongoDB URIs are configurable via environment variables.

**Q: What resources does a 1M-transaction run need?**
> Peak Python process memory: **1.7 GB**. Docker Desktop should be given at least **4 GB**. Outside Docker you need roughly **3 GB** free RAM. The analysis timeout is set to 15 minutes (`DETECTION_TIMEOUT_MS=900000`), though a 1M-row run typically completes in ~39 seconds end-to-end.

**Q: How do I upload my own transaction data?**
> CSV (with a header row) or JSON (array or `{"transactions": [...]}` wrapper). Required columns: `tx_id`, `timestamp`, `sender`, `receiver`, `amount`, `status`. Optional columns (any subset): `ip_address`, `channel`, `account_age_days`, `kyc_level`, `txn_note_len`, `device_type`, `network_type`, `app_version`, `sender_bank`, `receiver_bank`, `sender_state`, `receiver_state`. Bad rows are rejected with reasons, not silently dropped. The default upload limit is 50 MB.

---

## 🚀 Extending the Project

| Extension | How |
|---|---|
| Add a new rule | Implement a boolean array in `engine.py:analyze()`, add it to `flags` dict, give it a weight in `DEFAULT_CONFIG`, add to `PATTERNS` in `server/src/models/Case.js` |
| Add a new feature | Add the computation to `build_features()` in `engine.py`, add the name to the appropriate `FEATURES_*` list |
| Add authentication | Implement JWT or session middleware in `server/src/app.js`; add a `User` model and login/logout routes; protect all `/api` routes with an `authenticate` middleware |
| Async job queue | Replace the synchronous `detectionClient.js` call with a Bull job; have the client poll `GET /api/analyses/:id` until `meta.runtime` is populated |
| Real-time streaming | Replace the Python batch engine with an online version that processes one row at a time; all features are already causal, so this is architecturally possible |
| Connect to real UPI data | Map your data schema to the 6 required columns + any available optional columns; upload as CSV |

---

## 📚 Glossary

| Term | Meaning |
|---|---|
| **UPI** | Unified Payments Interface — India's real-time interbank digital payment system |
| **Mule account** | A bank account controlled by fraudsters (often young, minimum-KYC) used to receive and forward stolen funds |
| **Layering** | Moving money through multiple accounts to obscure its origin (AML technique) |
| **Structuring** | Splitting a large sum into many small transfers to avoid detection thresholds |
| **Account farm** | Multiple accounts operated by one person/device (same IP) to amplify fraudulent activity |
| **Account takeover** | An attacker gains control of a legitimate account (new IP, unusual amount signal) |
| **IsolationForest** | An unsupervised anomaly detection algorithm that isolates anomalies by randomly partitioning the feature space |
| **PR-AUC** | Precision-Recall Area Under Curve — a metric for imbalanced classification |
| **Causal feature** | A feature computed using only past data, so the model can run on a real-time stream |
| **Benign look-alike** | Innocent behaviour that superficially resembles a fraud pattern (e.g. a traveller paying from a new IP) |
| **Holdout** | The chronologically later 40% of transactions, never used for training, used only for evaluation |
| **CV** | Cross-Validation — evaluating a model's performance across multiple train/test splits |
| **F1 score** | Harmonic mean of Precision and Recall |
| **Risk score** | Heuristic 0–100 ranking score (NOT a probability); higher = more suspicious |

---

*Generated: November 2026 — UPI Fraud Pattern Detector MERN Edition*
