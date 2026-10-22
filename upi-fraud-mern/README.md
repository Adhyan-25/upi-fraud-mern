# UPI Fraud Pattern Detector — MERN edition

A full-stack web app that finds **suspicious money-movement patterns** in UPI-style transactions and lets
analysts review them. It is a **prototype that works on synthetic (made-up) data only**.

> ⚠️ A high risk score is a *model suspicion*, not proof of fraud. This is not an enforcement system.

| Layer | Technology | Folder | Port |
|---|---|---|---|
| **M**ongoDB – stores analysis runs, flagged cases, analyst status & notes | MongoDB 6/7 + Mongoose | – | 27017 |
| **E**xpress – REST API, validation, uploads, talks to the detection service | Node.js 18+ / Express 4 | `server/` | 5000 |
| **R**eact – the dashboard | React 18 + Vite | `client/` | 5173 (dev) / 8080 (Docker) |
| **N**ode.js – runtime for the API and the build tooling | Node 18+ | | |
| Detection engine – the actual fraud analytics (kept in Python) | Flask, pandas, scikit-learn, networkx | `detection-service/` | 5001 |

**What's in this version (on top of the first release)**

* **IP address as a fraud signal** – a new `ip_address` column, 4 IP features and a sixth detector, **Irregular IP**
  (an IP shared by several accounts at once, or a brand-new IP on an established account making a big transfer).
* **11 more optional data columns** (device, channel, bank, state, account age, KYC level, note length, network, app version) —
  some carry a little signal, some are pure noise, so you can **practise feature selection** (`disabled_features`, a ranking tool, per-group ablation).
* **K-fold cross-validation (5 folds by default)** with three split modes (stratified / grouped / time) and mean ± std, in the API, the dashboard and a CLI.
* **Datasets up to ONE MILLION transactions** (100,000 accounts): generated in ~10 s, analysed in ~20 s, cross-validated in ~10 s.

---

## Contents
1. [Quick start after unzipping](#1-quick-start-after-unzipping) ← **start here**
2. [What the app does](#2-what-the-app-does)
3. [Architecture](#3-architecture)
4. [Project structure](#4-project-structure)
5. [How the detection works](#5-how-the-detection-works)
6. [The data columns (signal vs noise)](#6-the-data-columns-signal-vs-noise)
7. [Feature selection](#7-feature-selection)
8. [5-fold cross-validation](#8-5-fold-cross-validation)
9. [Working with a million transactions](#9-working-with-a-million-transactions)
10. [Database design](#10-database-design)
11. [REST API reference](#11-rest-api-reference)
12. [Using the dashboard](#12-using-the-dashboard)
13. [Uploading your own transactions](#13-uploading-your-own-transactions)
14. [Configuration](#14-configuration)
15. [Running the tests](#15-running-the-tests)
16. [Benchmarks](#16-benchmarks)
17. [Security notes](#17-security-notes)
18. [Honest limitations (read before quoting any numbers)](#18-honest-limitations)
19. [Troubleshooting](#19-troubleshooting)
20. [Extending the project](#20-extending-the-project)
21. [Glossary](#21-glossary)

---

## 1. Quick start after unzipping

Unzip the archive anywhere. You get a folder called `upi-fraud-mern/`. Open a terminal **inside that folder**.
Pick **one** of the two options below.

### Option A — Docker (easiest, nothing else to install)

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + the Compose plugin).

```bash
cd upi-fraud-mern
docker compose up --build
```

The first build takes a few minutes (it downloads images and installs dependencies). When the logs settle, open:

* **http://localhost:8080** – the dashboard
* http://localhost:5000/api/health – API health check (optional)

Click **“Generate & analyse”** in the top bar to create your first run. Stop everything with `Ctrl+C`, then
`docker compose down`. Data lives in a Docker volume and survives restarts; wipe it with `docker compose down -v`.

> To use different ports, copy `.env.example` to `.env` and edit `WEB_PORT` / `API_PORT`.
>
> **Memory:** the default 20 000-transaction run needs very little. A **1 000 000-transaction** run peaks at about **2 GB**
> (we measured 1.7 GB), so give Docker Desktop at least 4 GB (Settings → Resources) before trying it.

### Option B — Run the three parts on your machine (for development)

**You need (install once):**

| Tool | Version | Check with | Get it |
|---|---|---|---|
| Node.js (includes npm) | 18 or newer (22 recommended) | `node -v` | https://nodejs.org |
| Python | 3.10 or newer | `python --version` (or `python3 --version`) | https://python.org |
| MongoDB Community Server | 6 or 7 | `mongod --version` | https://www.mongodb.com/try/download/community — **or** use a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster, **or** run just Mongo in Docker: `docker run -d -p 27017:27017 --name upi-mongo mongo:7` |

**Step 1 – install dependencies** (from inside `upi-fraud-mern/`):

```bash
# Python (a virtual environment keeps things tidy – recommended)
python -m venv .venv
#   macOS / Linux:   source .venv/bin/activate
#   Windows (cmd):   .venv\Scripts\activate
#   Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r detection-service/requirements.txt

# Node – root helper, API and client
npm install
npm --prefix server install
npm --prefix client install
```

(Or in one go, if you do not use a virtualenv: `npm run install:all`.)

**Step 2 – create the API settings file** (the defaults already match a local MongoDB, so this is only needed
if you change something):

```bash
# macOS / Linux
cp server/.env.example server/.env
# Windows (cmd)
copy server\.env.example server\.env
```

**Step 3 – make sure MongoDB is running** on `mongodb://127.0.0.1:27017` (start the `MongoDB` service, run
`mongod`, or the Docker command above). If you use Atlas, put its connection string in `server/.env` as `MONGODB_URI`.

**Step 4 – start everything** with one command:

```bash
npm run dev
```

This starts, side by side (colour-coded in the terminal):

* `detect` – Python detection service on http://localhost:5001
* `api` – Express API on http://localhost:5000
* `web` – React dev server on **http://localhost:5173** ← open this one

> If `npm run dev` complains it cannot find `python`, either activate the virtualenv first or run the three
> parts in three terminals instead:
> ```bash
> cd detection-service && python -m detection.app       # terminal 1 (use python3 on macOS/Linux if needed)
> cd server && npm run dev                              # terminal 2
> cd client && npm run dev                              # terminal 3
> ```

**Step 5 – use it.** Open http://localhost:5173. In the header you should see three green dots
(API · MongoDB · Detection). Click **Generate & analyse**; after a few seconds the dashboard fills in. To try the big dataset, choose the **1 million** size
preset in the “Synthetic data” box first (about 40 s, see [§9](#9-working-with-a-million-transactions)).

### Quick sanity checks

```bash
curl http://localhost:5000/api/health
# → {"status":"ok","mongo":"connected","detection":"up"}
```

If one of the three dots is red, see [Troubleshooting](#15-troubleshooting).

---

## 2. What the app does

1. **Create a run** – either generate reproducible synthetic data (from 100 up to **1 000 000** transactions, with fraud-like scenarios hidden
   inside) or upload your own CSV/JSON.
2. **The detection engine** cleans the data, computes **23 features**, applies **six pattern detectors** plus an
   anomaly-detection model, and produces a 0–100 **risk score** per transaction with a plain-English
   explanation of *why* it was flagged.
3. **Cross-validation (synthetic runs)** – the model part is re-trained and re-tested on 5 different folds, so you see
   mean ± standard deviation instead of one lucky split.
4. **The API stores** the run and the top flagged *cases* in MongoDB.
5. **The dashboard** shows a timeline, a prioritised **review queue**, a **case page** with a money-flow graph and the IP / device / channel
   of the transfer, quality metrics against the hidden ground truth, and the cross-validation table.
6. **Analysts work cases**: set a status (Open → Investigating → Escalated → Resolved) and write notes. These are saved in MongoDB.
7. **Feature selection**: switch features off per run (`drop features` box or `config.disabled_features`) and compare runs, or use the
   command-line report in [§7](#7-feature-selection).

The six patterns:

| Pattern | Plain meaning |
|---|---|
| **Circular transfer** (`cycle`) | Money goes A → B → C → A within 48 h (round-tripping / layering) |
| **Rapid onward transfer** (`rapid_onward`) | An account receives money and passes ~all of it on within 30 min (mule behaviour) |
| **Burst of transfers** (`burst`) | One sender makes ≥ 8 transfers in 10 minutes |
| **Split into small transfers** (`split`) | ≥ 4 transfers ≤ ₹5,000 between the same pair in 60 min totalling ≥ ₹10,000 (structuring) |
| **Unusual for this sender** (`unusual_behavior`) | An amount far above that sender’s own history |
| **Irregular IP address** (`irregular_ip`) | (a) ≥ 3 transfers by *other* accounts came from the same IP in the previous 2 h (an account farm), **or** (b) the sender uses an IP never seen for them before, has ≥ 5 earlier transfers, and the amount is ≥ ₹5,000 (possible account takeover) |

---

## 3. Architecture

```
 ┌────────────┐  /api/*   ┌──────────────────┐   HTTP    ┌────────────────────┐
 │  Browser   │ ────────► │ Express API      │ ────────► │ Flask detection    │
 │  React UI  │ ◄──────── │ (Node, :5000)    │ ◄──────── │ service (:5001)    │
 └────────────┘   JSON    │ validation,      │  JSON     │ pandas, sklearn,   │
   (Vite dev proxy /      │ uploads, Mongoose│           │ networkx           │
    nginx in Docker)      └────────┬─────────┘           └────────────────────┘
                                   │ Mongoose
                                   ▼
                          ┌──────────────────┐
                          │ MongoDB (:27017) │  analyses + cases collections
                          └──────────────────┘
```

**Why is part of it in Python?** The detection engine (pandas, scikit-learn’s IsolationForest, networkx cycle
search) is mature, tested and benchmarked Python. Rewriting it in JavaScript would add risk and remove
the libraries it depends on, so the MERN app *wraps* it as a stateless microservice. Everything user-facing
and everything persistent is MongoDB/Express/React/Node.

**Request flow for “Generate & analyse”:**
1. React `POST /api/analyses` with `{nAccounts, nTransactions, seed}`.
2. Express validates the body (zod) and calls Flask `POST /analyze`.
3. Flask generates the data, runs the engine and returns a result including a presentation-shaped `dashboard` block.
4. Express saves one `Analysis` document + up to 100 `Case` documents (bulk insert) and returns the analysis.
5. React selects the new run; the review queue pages through `GET /api/analyses/:id/cases`.

---

## 4. Project structure

```
upi-fraud-mern/
├── README.md                  ← you are here
├── package.json               root helper scripts (npm run dev / test / build)
├── docker-compose.yml         whole stack: mongo + detection + server + client
├── .env.example               ports for docker compose
├── .gitignore
│
├── detection-service/         ── Python: the analytics engine ──
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── detection/
│   │   ├── engine.py          validation, 23 features (core / ip / extra), 6 detectors, IsolationForest, scoring, evaluation
│   │   ├── generate.py        reproducible synthetic data (up to 1M rows) + hidden ground-truth labels
│   │   ├── crossval.py        K-fold cross-validation, univariate feature ranking, group ablation (also a CLI)
│   │   ├── report.py          builds the dashboard payload (timeline, cases, money-flow graphs, CV table)
│   │   ├── app.py             Flask API: GET /health, GET /detectors, POST /analyze
│   │   └── run.py             command-line runner that prints metrics and the CV summary
│   ├── bench/                 benchmark scripts + the pre-optimisation engine for A/B comparison
│   └── tests/                 71 unit tests (engine, IP rule, feature selection, cross-validation, report, API)
│
├── server/                    ── Node: Express + Mongoose API ──
│   ├── package.json
│   ├── .env.example
│   ├── Dockerfile
│   ├── src/
│   │   ├── index.js           entry point: connect DB, listen, graceful shutdown
│   │   ├── app.js             builds the Express app (security middleware, rate limits, routes)
│   │   ├── config.js          the only place that reads environment variables
│   │   ├── db.js              Mongoose connection helpers
│   │   ├── models/            Analysis.js (a run), Case.js (a flagged transaction + analyst workflow)
│   │   ├── routes/            analyses.js, cases.js, health.js, detectors.js
│   │   ├── services/          detectionClient.js (HTTP to Flask), analysisService.js (run + store)
│   │   ├── middleware/        validate.js (zod), errorHandler.js (uniform JSON errors)
│   │   └── utils/             parseTransactions.js (CSV/JSON), asyncHandler.js, httpError.js
│   └── tests/                 28 API + parser tests (node:test + supertest)
│
└── client/                    ── React dashboard (Vite) ──
    ├── index.html, vite.config.js, nginx.conf, Dockerfile
    └── src/
        ├── main.jsx           mounts <App/>
        ├── App.jsx            top-level state: runs, selected run, selected case
        ├── styles.css         theme tokens (light/dark) + layout
        ├── lib/api.js         the only file that calls fetch()
        ├── lib/format.js      ₹ formatting, labels, size presets, feature names
        ├── hooks/useDebounce.js
        └── components/        Header, RunBar, SummaryBar, VolumeChart, ReviewQueue,
                               CaseDetail, MoneyGraph, Panels (patterns / quality / recall / cross-validation)
```

Every source file starts with a comment block explaining its role, and the code is commented line-by-line
where the logic is not obvious — reading `server/src/app.js` and `detection-service/detection/engine.py`
top to bottom is a good way to learn the whole system.

---

## 5. How the detection works

Implemented in `detection-service/detection/engine.py`.

1. **Validation** – each row is checked (missing id, bad timestamp, non-positive/absurd amount, sender = receiver…).
   Invalid rows are **returned with a `reject_reason`, never silently dropped**. Empty optional cells (a missing IP, say) are *not* errors.
2. **Cleaning** – duplicate `tx_id`s are removed (first occurrence wins), `FAILED` transfers are excluded.
3. **Causal features** – for every transaction, features use **only earlier rows**, so the model could run
   online. (Unit tests prove this: adding future rows never changes past features, and every feature matches a slow brute-force definition.)
   There are **23 features in three groups** (listed in §7).
4. **Rules** – the six detectors in the table in §2, each with tunable thresholds.
5. **Cycle search** – a graph search (networkx) on 48-hour windows. It is *retrospective* (looks at the whole
   window) and heavily bounded (window size, component size, cycle length, cycles per window), so it cannot
   blow up on pathological data. With a million rows it takes well under a second thanks to pre-pruning.
6. **ML anomaly score** – an unsupervised **IsolationForest** on the selected features, trained only on the
   chronologically first 60 % of rows, converted into a percentile.
7. **Risk score** = `ml_weight × percentile + Σ weights of fired rules`, clipped to 0–100.
   Categories: **critical ≥ 70**, **high ≥ 50**, **medium ≥ 30**, else low. Default flag threshold: **50**.
   *It is a ranking score, not a probability.* (No single rule can reach 50 on its own except `cycle`, so most flags need two signals or ML support.)
8. **Explanation** – every flagged row gets human-readable evidence per pattern
   (“ACC00023 made 12 transfers within 10 minutes”, “IP 57.134.53.219 was used by 4 other accounts in the previous 120 minutes”).
9. **Evaluation (synthetic data only)** – on the *holdout* rows after the training cut-off, compared with the
   hidden labels: precision, recall, F1, false-positive rate, precision@k, PR-AUC, confusion matrix,
   per-scenario recall, and how often *benign look-alikes* (innocent behaviour that resembles fraud) were flagged.
10. **Cross-validation (synthetic data only)** – see §8.

**How the IP signal is built** (all causal, `build_features` in `engine.py`):

| Feature | Meaning |
|---|---|
| `ip_is_new` | 1 if this sender has never used this IP before |
| `ip_other_tx_win` | transfers by **other** accounts from this IP in the previous `ip_window_min` (default 120) minutes |
| `ip_cnt_win` | all transfers from this IP in that window, including this one |
| `ip_distinct_senders` | how many other accounts have ever used this IP so far |

All thresholds are in `DEFAULT_CONFIG` and can be overridden per request via the API’s `config` field
(validated: unknown keys, wrong types and out-of-range values are rejected with HTTP 400).

---

## 6. The data columns (signal vs noise)

Every transaction has **6 required columns** and up to **12 optional ones**. The optional columns are what make
feature selection interesting: the synthetic generator (`detection/generate.py`) deliberately mixes
**real signal**, **weak signal** and **pure noise**, and labels fraud *separately* so the engine never sees the answer.

| Column | Example | How the generator uses it | Becomes feature(s) |
|---|---|---|---|
| `tx_id`, `timestamp`, `sender`, `receiver`, `amount`, `status` | | required | the 9 **core** features |
| **`ip_address`** | `57.134.53.219` | **Real signal.** Normally the sender’s home IP, 25 % of the time a shared mobile-carrier IP. Fraud: IP farms share *one* IP; takeovers use a brand-new IP | `ip_is_new`, `ip_other_tx_win`, `ip_cnt_win`, `ip_distinct_senders` |
| `channel` | `pay` / `qr` / `intent` / `collect` | **Weak signal** – fraud rows use “collect” requests more often (25 % vs 5 %) | `channel_collect` |
| `account_age_days` | `37` | **Weak signal** – about 40 % of fraud accounts are young (3–60 days) | `log_account_age` |
| `kyc_level` | `full` / `min` | **Weak signal** – mule accounts are more often minimum-KYC | `kyc_full` |
| `txn_note_len` | `0` | **Weak signal** – fraud notes are more often empty / ≤ 3 characters | `note_len` |
| `device_type` | `android` / `ios` / `web` | noise | `device_code` |
| `network_type` | `wifi` / `4g` / `5g` | noise | `network_code` |
| `app_version` | `5.12.3` | noise | `app_major` |
| `sender_bank`, `receiver_bank` | `BNK03` | noise | `sender_bank_code`, `receiver_bank_code` |
| `sender_state`, `receiver_state` | `MH`, `DL` | noise | `cross_state` (1 if the two states differ) |

Missing optional columns are fine: their features become constant 0 and the engine adds a warning (an upload with only the
6 required columns behaves exactly like the original version).

---

## 7. Feature selection

The ML part (IsolationForest) sees **23 numeric features**, grouped so you can switch whole groups on and off:

| Group | Features |
|---|---|
| **core** (9) | `log_amount`, `amount_z`, `sender_cnt_win`, `sender_cnt_1d`, `receiver_in_cnt_1d`, `log_since_prev`, `inflow_ratio`, `pair_cnt_win`, `hour` |
| **ip** (4) | `ip_is_new`, `ip_other_tx_win`, `ip_cnt_win`, `ip_distinct_senders` |
| **extra** (10) | `device_code`, `channel_collect`, `cross_state`, `log_account_age`, `kyc_full`, `note_len`, `network_code`, `app_major`, `sender_bank_code`, `receiver_bank_code` |

**Removing features – four equivalent ways**

```bash
# 1) per request, in the dashboard: type names into the “drop features” box (comma separated)
# 2) per request, via the API
curl -X POST localhost:5000/api/analyses -H 'Content-Type: application/json' \
     -d '{"nTransactions":200000,"disabledFeatures":["note_len","app_major","sender_bank_code"]}'
# 3) the same inside the detector config
#    "config": { "disabled_features": ["note_len", "app_major"] }
# 4) as a process-wide default: UPI_DISABLED_FEATURES="note_len,app_major" before starting the Python service
```

The **rules never change** when you drop features (they use their own columns); only the ML percentile does. At least 3 features must remain.

**Which ones should I drop? – the built-in report**

```bash
cd detection-service
python -m detection.crossval 200000                           # CV + ablation per group + univariate ranking
python -m detection.crossval 200000 --drop note_len,app_major # evaluate a specific selection
python -m detection.crossval 200000 --mode time --folds 5     # same, with forward-chaining folds
```

Sample output on 50 000 transactions (your numbers will differ slightly):

```
current selection            F1 0.717+-0.025   PR-AUC 0.778+-0.041   ML-only PR-AUC 0.645+-0.049   (23 features)
without group 'core'         F1 0.725+-0.025   PR-AUC 0.675+-0.040   ML-only PR-AUC 0.271+-0.035   (14 features)
without group 'ip'           F1 0.716+-0.025   PR-AUC 0.733+-0.035   ML-only PR-AUC 0.490+-0.025   (19 features)
without group 'extra'        F1 0.716+-0.025   PR-AUC 0.813+-0.038   ML-only PR-AUC 0.741+-0.027   (13 features)

log_amount          core   strength 0.777        log_account_age  extra  strength 0.344
ip_cnt_win          ip     strength 0.631        note_len         extra  strength 0.319
...                                              ip_distinct_senders  ip   strength 0.023
device_code         extra  strength 0.021        network_code     extra  strength 0.010
app_major           extra  strength 0.016        cross_state      extra  strength 0.006   sender_bank_code 0.002
```

How to read it: **strength** = |2·AUC − 1| of the feature on its own against the hidden labels (0 = useless, 1 = perfect).
In this example the **noise columns sit at ≈ 0** and removing the whole `extra` group *raises* the PR-AUC (0.778 → 0.813) —
exactly the situation feature selection is for. The `ip` group is clearly worth keeping (PR-AUC falls to 0.733 without it).
Note that `log_account_age`, `note_len` and `channel_collect` are weak-but-real, so keep them or drop them and compare.

> Caution: because the labels come from the same generator that wrote the noise columns, “noise” is *known* noise here. On real
> data, decide with cross-validation on real labels, not with this table.

---

## 8. 5-fold cross-validation

**What is being cross-validated?** The risk score is `ml_weight × IsolationForest percentile + sum of rule weights`.
The rules have fixed thresholds — nothing is learned there. The only *trained* part is the IsolationForest. For each of the 5 folds the
code (`detection/crossval.py`):

1. fits a fresh forest on 4/5 of the rows (it is unsupervised, so it never sees labels),
2. converts its anomaly scores into percentiles relative to those training rows,
3. builds the risk score for the held-out 1/5 (the rule points are identical in every fold),
4. measures **precision, recall, F1, false-positive rate, PR-AUC** on the held-out fold with the ground-truth labels, plus the
   **PR-AUC of the forest alone** (“forest only”), which is the number that changes most when you drop features.

The dashboard (bottom panel), the API response and `python -m detection.run` show every fold plus **mean ± standard deviation** and the pooled
out-of-fold metrics. A small standard deviation means the result does not hinge on one lucky split.

**Three split modes** (`mode`)

| Mode | Folds are… | Use it for |
|---|---|---|
| `stratified` (default) | random, with the same share of fraud rows in each | the classic “5-fold CV” |
| `grouped` | like stratified, but all rows of one injected scenario stay in **one** fold | strictest test: the forest never trains on rows of the scenario it is tested on |
| `time` | forward chaining: train on the past, test on the next slice (the first fold has the least training data) | closest to production use |

`folds` can be 2–10. Cross-validation needs labels, so it runs for **synthetic data only**; for uploads it is skipped with a note.

Example result, 2 000 accounts / 20 000 transactions, seed 42 (mean over 5 folds):

| Mode | Precision | Recall | F1 | PR-AUC | PR-AUC forest only |
|---|---|---|---|---|---|
| stratified | 0.866 ± 0.027 | 0.630 ± 0.065 | 0.728 ± 0.046 | 0.772 ± 0.040 | 0.596 ± 0.037 |
| grouped | 0.871 ± 0.080 | 0.630 ± 0.010 | 0.730 ± 0.029 | 0.781 ± 0.048 | 0.617 ± 0.060 |
| time | 0.847 ± 0.063 | 0.636 ± 0.063 | 0.724 ± 0.042 | 0.776 ± 0.065 | 0.572 ± 0.162 |

and on 1 000 000 transactions (stratified): precision 0.850 ± 0.008, recall 0.617 ± 0.006, F1 0.715 ± 0.005, PR-AUC 0.762 ± 0.010
(10 s for all 5 folds). Larger data ⇒ much smaller standard deviations.

Ways to run it:

```bash
# in the app: tick “5-fold CV” in the run bar (default on), choose the mode
# API:
curl -X POST localhost:5000/api/analyses -H 'Content-Type: application/json' \
     -d '{"nTransactions":50000,"cv":true,"cvFolds":5,"cvMode":"grouped"}'
# command line:
cd detection-service && python -m detection.run                 # prints the 5-fold summary at the end
python -m detection.crossval 100000 --folds 5 --mode time       # per-fold table + feature-selection report
```

---

## 9. Working with a million transactions

The synthetic generator and the engine are fully vectorised, so **one million transactions** is routine:

```bash
cd detection-service
python -m detection.run 100000 1000000        # 100,000 accounts, 1,000,000 background transactions
```

Measured in the build sandbox (2 CPUs, 8 GB RAM), seed 42 — the run contains 1 069 140 raw rows including injected scenarios and 1 % duplicates:

| Step | Time |
|---|---|
| generate the data | 9 s |
| validate + dedupe + features + 6 detectors + IsolationForest + scoring | 18 s |
| 5-fold cross-validation | 10 s |
| **whole run through the Express API (incl. storing in MongoDB)** | **39 s** |
| peak memory of the Python process | **1.7 GB** |

What changed to make this possible: IP strings are stored once in a pool (rows keep an index), per-account attributes are vectorised, the
packed (key, time) integers were widened so 100 000 accounts cannot overflow, the account count scales with the data
(`n_accounts` defaults to *n_transactions / 10*), and only the top `limit` flagged cases are explained and stored.

Practical notes

* Give Docker Desktop **≥ 4 GB**; outside Docker you need roughly **3 GB** free RAM.
* Analyses are synchronous. Timeouts were raised accordingly (`DETECTION_TIMEOUT_MS=900000`, nginx `proxy_read_timeout 900s`, gunicorn `--timeout 900`).
* The UI limits synthetic runs to 1 000 000 transactions (the Python service itself accepts up to 1 500 000).
* **Uploading** a million-row file works in principle but is heavy (the file is parsed in Node, then sent to Python as JSON). Raise `MAX_UPLOAD_MB`
  (default 50 MB ≈ 400 000 rows) and expect several GB of RAM; for very large files call the Python service directly or add a streaming loader.
* Only the top `limit` flagged cases (default 100, max 1 000) become reviewable cases; counts, charts and metrics cover **all** flagged rows
  (about 12 000 on the million-row demo).

---

## 10. Database design

MongoDB database `upi_fraud` (name comes from the connection string), two collections:

**`analyses`** – one document per run (see `server/src/models/Analysis.js`)

| Field | Meaning |
|---|---|
| `name`, `source` (`synthetic`\|`upload`), `params` | label and how the data was produced |
| `config` | effective detector settings used |
| `meta` | headline counts: records, flagged, critical, high, accounts, days, start date, duplicates, rejected, threshold, runtime |
| `volume.total[]`, `volume.flagged[]` | per-day counts for the timeline |
| `patterns` | `{ burst: 49, cycle: 40, … }` |
| `evaluation` | metrics (synthetic) or `null` (uploads) |
| `crossValidation` | K-fold result: `mode`, `folds`, `perFold[]`, `mean`, `std`, `pooled`, `seconds` (synthetic) or `null` |
| `quality`, `rejectedRows[]`, `warnings[]`, `timings` | data-quality report, a sample of rejected rows with reasons, engine warnings, per-stage seconds |
| `caseCount`, `createdAt`, `updatedAt` | bookkeeping |

**`cases`** – one document per flagged transaction (see `server/src/models/Case.js`)

| Field | Meaning |
|---|---|
| `analysis` | reference to the run (indexed) |
| `txId`, `timestamp`, `sender`, `receiver`, `amount` | the transaction |
| `riskScore`, `riskCategory`, `mlPercentile` | detection output (**immutable**) |
| `ip`, `device`, `channel` | context of the transfer (shown next to the case; present when the data has those columns) |
| `evidence[]` | `{pattern, weight, text}` – why it was flagged |
| `graph[]` | `{from, to, amount, flagged, focus}` – nearby transfers for the money-flow picture |
| `status` | `Open` \| `Investigating` \| `Escalated` \| `Resolved` |
| `notes` | analyst notes (≤ 5000 chars) |
| `history[]` | audit trail `{from, to, at}` of status changes |

Indexes: `(analysis, txId)` unique, `(analysis, riskScore desc)` for the queue, `(analysis, evidence.pattern)` for the pattern filter.
Deleting a run deletes its cases.

---

## 11. REST API reference

Base URL: `http://localhost:5000` (or `/api` through the web UI / nginx). All bodies are JSON unless stated.
All errors look like `{ "error": "human readable message" }`.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | `{status, mongo, detection}`; HTTP 503 if a dependency is down |
| `GET /api/detectors` | Default config, feature groups and scoring formula (proxied from Python) |
| `POST /api/analyses` | Run on **synthetic** data and store it → `201` + analysis |
| `POST /api/analyses/upload` | Run on an **uploaded CSV/JSON** (multipart, field `file`) → `201` + analysis |
| `GET /api/analyses` | List runs, newest first (light fields) |
| `GET /api/analyses/:id` | One run plus `statusCounts` (how many cases per workflow status) |
| `GET /api/analyses/:id/cases` | Paged/filtered review queue |
| `DELETE /api/analyses/:id` | Delete a run and its cases |
| `GET /api/cases/:id` | Full case (evidence, graph, IP/device/channel, status, notes, history) |
| `PATCH /api/cases/:id` | Update `status` and/or `notes` only |

**`POST /api/analyses`** body (everything optional, defaults shown):
```json
{ "name": "optional label",
  "nTransactions": 10000, "nAccounts": null, "seed": 42,
  "limit": 100,
  "cv": true, "cvFolds": 5, "cvMode": "stratified",
  "disabledFeatures": ["note_len", "app_major"],
  "config": { "threshold": 60, "weights": { "irregular_ip": 25 }, "ip_other_min": 4 } }
```
Limits: `nTransactions` 100–1 000 000; `nAccounts` 60–200 000 (omitted ⇒ about nTransactions / 10); `limit` 1–1000 (max cases stored);
`cvFolds` 2–10; `cvMode` `stratified` | `grouped` | `time`; `disabledFeatures` = ML feature names to switch off (§7).
`config` accepts every key of the detector configuration (§14); the server validates them and answers 400 with the reason if one is wrong.

**`GET /api/analyses/:id/cases`** query parameters: `category` (`critical`|`high`…), `pattern`
(`cycle`|`rapid_onward`|`burst`|`split`|`unusual_behavior`|`irregular_ip`), `status`, `q` (text search on tx id / sender / receiver),
`page` (default 1), `limit` (1–200, default 50). Response: `{ items, total, page, pages, limit }`.

**`PATCH /api/cases/:id`** body: `{ "status": "Investigating", "notes": "called the bank" }` – at least one key;
any other key (e.g. `riskScore`) is rejected with 400.

**`POST /api/analyses/upload`** form fields: `file` (required), `name`, `limit`, `config` (JSON string).

Examples:
```bash
# one million transactions with 5-fold cross-validation (about 40 s)
curl -X POST localhost:5000/api/analyses -H 'Content-Type: application/json' \
     -d '{"nTransactions":1000000}'

# upload a CSV
curl -X POST localhost:5000/api/analyses/upload -F "file=@my_transactions.csv" -F "name=October batch"

# first page of critical IP cases
curl "localhost:5000/api/analyses/<ID>/cases?category=critical&pattern=irregular_ip&limit=10"

# mark a case
curl -X PATCH localhost:5000/api/cases/<CASE_ID> -H 'Content-Type: application/json' \
     -d '{"status":"Escalated","notes":"matches known mule account"}'
```

The Python service has its own small API (normally you do not call it directly):
`GET :5001/health`, `GET :5001/detectors`, `POST :5001/analyze` (body: `transactions` **or** `generate`, plus `config`, `limit`,
`cross_validation: {enabled, folds, mode}`).

---

## 12. Using the dashboard

* **Header** – three dots show API / MongoDB / Detection status (refreshed every 15 s).
* **Run bar** – pick a previous run or delete it; generate a new synthetic run (size preset from 20 thousand to **1 million**, optional
  account count, seed, the **“5-fold CV”** checkbox with number of folds and split mode, and a **“drop features”** box for feature selection);
  or upload a file.
* **Summary line** – counts and how many cases are in each workflow status.
* **Timeline** – grey area = transactions per day; red bars = flagged per day (hover for numbers).
* **Review queue** – riskiest first. Filter by risk level, pattern (including *Irregular IP address*) or workflow status; search by
  account or transaction ID; page through results.
* **Case panel** – risk score, **IP address / channel / device chips**, reasons, a money-flow graph (click an account to isolate its transfers),
  status dropdown (saved immediately) and notes (auto-saved ~0.7 s after you stop typing), plus the status history.
* **Bottom strip** – flagged transactions per pattern; detection quality; recall per injected scenario and the confusion matrix
  (synthetic runs only — uploads have no ground truth).
* **Cross-validation panel** (synthetic runs) – every fold, mean ± std and pooled out-of-fold metrics.

---

## 13. Uploading your own transactions

CSV (header row required) or JSON (an array, or `{ "transactions": [...] }`). Required columns:

| Column | Example | Notes |
|---|---|---|
| `tx_id` | `TX0000123` | unique id; duplicates are removed (first wins) |
| `timestamp` | `2026-07-01T10:15:00Z` | any format pandas can parse; treated as UTC |
| `sender` | `ACC00012` | account id |
| `receiver` | `ACC00345` | must differ from sender |
| `amount` | `2500.50` | > 0 and ≤ 10,000,000 |
| `status` | `SUCCESS` | only `SUCCESS` rows are analysed; others are counted as “failed excluded” |

**Optional columns** (any subset; see §6): `ip_address`, `channel`, `account_age_days`, `kyc_level` (`full`/`min`), `txn_note_len`,
`device_type` (`android`/`ios`/`web`), `network_type` (`wifi`/`4g`/`5g`), `app_version`, `sender_bank`, `receiver_bank`, `sender_state`,
`receiver_state`. Without `ip_address` the IP features are 0 and the *Irregular IP* rule cannot fire (a warning says so).

Bad rows are not fatal: they are rejected with a reason and counted in the summary. Empty CSV cells are treated as missing values.
Limits: 50 MB per file by default (`MAX_UPLOAD_MB`), at most 1 500 000 rows (see §9 for very large files).
Tip: create a sample file with `cd detection-service && python -m detection.run 500 5000`, which writes
`data/transactions_raw.csv` in exactly this format (with all optional columns).

---

## 14. Configuration

**API – `server/.env`** (all optional; defaults in `server/src/config.js`)

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `5000` | API port |
| `MONGODB_URI` | `mongodb://127.0.0.1:27017/upi_fraud` | MongoDB connection string (Atlas works too) |
| `DETECTION_URL` | `http://127.0.0.1:5001` | where the Python service lives |
| `CORS_ORIGIN` | `http://localhost:5173` | allowed browser origin(s), comma-separated |
| `DETECTION_TIMEOUT_MS` | `900000` | how long to wait for an analysis (15 min; a million rows takes well under one) |
| `MAX_UPLOAD_MB` | `50` | upload size limit |
| `NODE_ENV` | `development` | |

**Client** – optional `VITE_API_TARGET` (default `http://localhost:5000`) changes where the dev server proxies `/api`.

**Docker Compose – root `.env`**: `WEB_PORT` (default 8080), `API_PORT` (default 5000).

**Detector configuration** (the `config` field of an API call; defaults in `detection-service/detection/engine.py`):

| Key | Default | Meaning |
|---|---|---|
| `threshold` | 50 | risk score at or above which a transaction is flagged |
| `train_fraction` | 0.6 | earliest share of rows the main IsolationForest is fitted on |
| `ml_weight` | 30 | maximum points the ML percentile adds |
| `weights` | cycle 50, rapid_onward 40, burst 35, split 40, unusual_behavior 30, **irregular_ip 30** | points per rule |
| `burst_window_min`, `burst_count` | 10, 8 | burst rule |
| `rapid_window_min`, `rapid_min_amount`, `rapid_ratio`, `rapid_ratio_max` | 30, 5000, 0.7, 1.5 | rapid-onward rule |
| `split_window_min`, `split_count`, `split_cap`, `split_min_total` | 60, 4, 5000, 10000 | split rule |
| `unusual_z`, `unusual_min_history`, `unusual_min_amount` | 4.0, 5, 10000 | unusual-behaviour rule |
| `cycle_*` | 3–5 accounts, 48 h, ≥ 1000 | cycle search and its safety bounds |
| **`ip_window_min`**, **`ip_other_min`**, **`ip_min_amount`** | 120, 3, 1000 | irregular IP, rule (a): ≥ `ip_other_min` transfers by other accounts on this IP in the window, amount ≥ `ip_min_amount` |
| **`ip_new_min_history`**, **`ip_new_min_amount`** | 5, 5000 | irregular IP, rule (b): a new IP for a sender with ≥ 5 earlier transfers and amount ≥ 5000 |
| **`disabled_features`** | `[]` | ML features to switch off (§7) |

---

## 15. Running the tests

```bash
npm run test:detection     # Python: 71 tests (engine, causality, leakage, IP features, feature selection, cross-validation, report, API)
npm run test:server        # Node: 28 tests (API, validation, uploads, workflow, errors)
npm test                   # both
```

* The Python tests need no database (about 30 s; they include a 50 000-transaction / 5 000-account end-to-end run).
* The server tests use a **fake detection client** (so Python is not required) and a **real MongoDB**:
  they use `TEST_MONGODB_URI` if set (e.g. `TEST_MONGODB_URI=mongodb://127.0.0.1:27017/upi_fraud_test npm run test:server`),
  otherwise `mongodb-memory-server` downloads a MongoDB binary the first time. If neither is possible the
  database-backed tests are **skipped** (not failed) with a message.

Highlights of what is tested: IP features equal their brute-force definitions; the first transfers of an IP farm *cannot* fire the
“shared IP” rule (causality); every fold partitions the rows (each row tested exactly once, never in train and test); grouped folds never split a scenario;
time folds only train on the past; cross-validation is reproducible and beats chance; dropping features never changes the rules;
200 000 accounts do not overflow the packed integer keys.

---

## 16. Benchmarks

```bash
cd detection-service
python -m detection.run 100000 1000000   # one million transactions: metrics, timings, 5-fold CV
python -m bench.benchmark                # old engine vs. current engine; asserts identical output
python -m bench.memory detection.engine  # peak memory growth in a fresh process (needs data/transactions_raw.csv from detection.run)
```

* **Million rows:** see the table in §9 (≈ 48 s for generate + analyse + 5-fold CV, 1.7 GB peak).
* **A/B on 100 000 rows** (1 CPU-ish sandbox, median of 7 interleaved runs): old engine ≈ **2.27 s**, current engine restricted to the same
  9 core features and without the IP rule ≈ **1.62 s** (≈ 1.4×), with **identical** scored output. With all 23 features and the IP rule the
  current engine takes ≈ 1.8 s. Your machine will differ.
  (`bench/engine_baseline.py` is the first-release engine, kept only for this comparison.)

---

## 17. Security notes

* `helmet` security headers, CORS restricted to configured origins, request body limit (1 MB JSON, 50 MB uploads by default).
* Rate limits: 300 requests/min/IP on the API, 20/min on the two endpoints that run the engine.
* Every input is validated (zod on the API, strict config validation in Python); ObjectIds are checked; search text is
  regex-escaped; Mongoose `strictQuery` drops unknown filter keys; `PATCH /api/cases` accepts only `status` and `notes`.
* Errors return generic JSON; stack traces and internals are logged, never sent to the client.
* Python caps generation/analysis size (≤ 1 500 000 transactions, ≤ 200 000 accounts) and request size (300 MB).
* **There is no authentication.** This is a local prototype. Do not expose it to the internet as-is; add
  authentication (e.g. JWT/sessions + roles) first.

---

## 18. Honest limitations

These come from the original project’s audit, re-measured for this version:

* The synthetic data has **≈ 1 % fraud in the holdout**, far above real life. Precision and PR-AUC would be much
  lower on real data.
* Thresholds and windows were written knowing the generator’s scenario shapes, so scores are **optimistic**.
  Across 4 other seeds (2 000 accounts / 20 000 tx) precision was **0.77–0.88** and recall **0.57–0.69** (seed 42: 0.87 / 0.64).
* **Burst (~0.42), split (~0.5) and IP-farm (~0.64–0.83) recall are capped** by causal scoring: the first transfers of a burst/split cannot
  be flagged when they happen, and the first 3 members of an IP farm have not yet “seen” 3 other accounts on that IP.
  **Cycle recall (1.0) is retrospective** and not comparable to the others.
* **The extra columns and the IP behaviour are simulated by the same code that evaluates them.** “Noise” columns are noise *by construction*, and
  shared-IP farms look the way the generator draws them. Real IP data brings VPNs, carrier-grade NAT, university networks and
  IPv6 privacy addresses that this generator only imitates (the shared mobile-carrier pool and the office-NAT look-alike are simple stand-ins).
* The IP rule also flags the **benign traveller** look-alike (an established account paying ₹10k–30k from a brand-new IP): benign look-alikes
  are flagged about 5 % of the time.
* K-fold cross-validation only re-trains the **IsolationForest**; the rules have fixed hand-set thresholds, so their performance is *not*
  cross-validated (they were designed with the generator in view). `grouped` and `time` modes are stricter than the default `stratified`.
* `precision@k` uses *k = number of true positives* (an oracle choice): it shows ranking quality, not a deployable operating point.
* Only the top `limit` (default 100) flagged cases per run are stored as reviewable cases; the run’s counts and
  charts cover **all** flagged transactions.
* Analyses run synchronously inside the HTTP request (fine up to a million rows). A job queue would be the next step for bigger data or many users.

**Verification status of this package:** the Python test-suite (71 tests) and the Node test-suite (28 tests) pass; a 1 000 000-transaction run through
the full stack (Flask → Express → MongoDB) took 39 s, and the dashboard (feature filter, IP pattern, case page, cross-validation panel) was exercised in a
browser. The sandbox it was built in has no MongoDB binary, so the Node tests and the end-to-end run used *FerretDB* (a MongoDB-wire-compatible server) as the
database; the Docker Compose files were written but **not run** there. If `docker compose up` fails for you, please see Troubleshooting.

---

## 19. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Red **MongoDB** dot, API log says `could not start: ... ECONNREFUSED` | MongoDB is not running or `MONGODB_URI` is wrong. Start `mongod` / the Docker container, or fix `server/.env`. |
| Red **Detection** dot / “Detection service is unreachable” (HTTP 502) | Flask is not running on :5001. Start it (`cd detection-service && python -m detection.app`) and check that `pip install -r detection-service/requirements.txt` succeeded. |
| “Detection service timed out” (504) | Dataset too large for `DETECTION_TIMEOUT_MS`; raise it or use fewer transactions. |
| Million-row run is killed / “internal error” / Docker container restarts | Out of memory. Give Docker ≥ 4 GB (about 3 GB free RAM without Docker) or use a smaller size preset. |
| “unknown feature” / “at least 3 features must remain” | Check the spelling against §7 (e.g. `note_len`, not `note length`); you cannot drop (almost) everything. |
| No cross-validation panel | Uploads have no labels, so it is skipped; for synthetic runs make sure the “5-fold CV” box was ticked. |
| Irregular IP never fires on my upload | The file needs an `ip_address` column (the run shows a warning when it is missing). |
| `npm run dev` → `python: command not found` | Activate your virtualenv, use `python3`, or start the three parts in separate terminals (see §1). |
| `ModuleNotFoundError: No module named 'detection'` | Run Python commands from inside `detection-service/` (the folder that contains `detection/`). |
| `ModuleNotFoundError` for pandas/flask/sklearn | Activate the virtualenv where you ran `pip install`. |
| Browser shows “Cannot reach the API” | Express is not running on :5000, or you opened a different port than 5173 (dev) / 8080 (Docker). |
| CORS error in the browser console | Open the app on the origin listed in `CORS_ORIGIN` (default `http://localhost:5173`) or add yours. |
| Port already in use (5000 / 5001 / 5173 / 8080 / 27017) | Stop the other program or change the port (`PORT`, `WEB_PORT`, …). On macOS, port 5000 is often taken by AirPlay Receiver — set `PORT=5050` in `server/.env` and `VITE_API_TARGET=http://localhost:5050` for the client. |
| Upload says “Missing required columns” | Header must include `tx_id,timestamp,sender,receiver,amount,status` (the other columns are optional). |
| Everything is rejected (“no valid transactions”) | Check the “rejected” count and the sample reasons on the run (`rejectedRows`), usually bad timestamps or amounts. |
| `mongodb-memory-server` download fails during `npm test` | Offline/proxy. Point `TEST_MONGODB_URI` at any running MongoDB, or accept the skipped tests. |
| Fonts look different | The UI uses IBM Plex Sans from Google Fonts and falls back to system fonts offline. |

---

## 20. Extending the project

**Add a new detector** (e.g. “round-amount transfers”):
1. `detection/engine.py`: add its parameters to `DEFAULT_CONFIG` (+ a weight in `weights`), compute a boolean array in
   `analyze()` inside the `flags` dict, and add a sentence in `_explain()`; add any needed range checks in `validate_config()`.
2. Add a unit test in `detection-service/tests/`.
3. `server/src/models/Case.js`: add the id to `PATTERNS` (the API filter enum follows automatically).
4. `client/src/lib/format.js`: add a label to `PAT`.

**Add a new data column / feature:** add the column name to `OPTIONAL` and a feature to one of the groups (`FEATURES_*`) in `engine.py`, compute it in
`build_features()` (use only earlier rows!), teach `generate.py` to produce it, and add its name to `FEATURE_GROUPS` in `client/src/lib/format.js`.
The feature-selection report (§7) picks it up automatically.

**Other ideas:** authentication and roles, assigning cases to analysts, CSV export of cases, an async job queue
(BullMQ/Redis) for big uploads, a supervised model (e.g. gradient boosting) whose cross-validation would then also cover the rules, real IP
enrichment (ASN / geolocation / VPN lists), scheduled re-analysis.

---

## 21. Glossary

* **UPI** – India’s Unified Payments Interface (instant bank-to-bank transfers).
* **Synthetic data** – invented data with a known answer key, used because real payment data is private.
* **Causal / point-in-time feature** – computed using only information available *at that moment*.
* **IsolationForest** – an unsupervised model that scores how easy a point is to “isolate”; easy to isolate = unusual.
* **Precision / recall** – of everything flagged, the share that was really suspicious / of everything suspicious, the share that got flagged.
* **PR-AUC** – area under the precision-recall curve; a ranking-quality number for rare events.
* **Holdout** – data the model was not trained on (the later 40 % in time).
* **Benign look-alike** – innocent behaviour that resembles a fraud pattern, used to measure false positives.
* **Mule account** – an account used to pass illicit money along.
* **MERN** – MongoDB, Express, React, Node.js.
* **K-fold cross-validation** – split the data into K parts; train on K−1, test on the held-out one, repeat K times and average.
* **Stratified / grouped / time folds** – equal fraud share per fold / a scenario never split across folds / only the past is used for training.
* **Feature selection** – choosing which input columns the model should use, e.g. dropping noisy ones.
* **Univariate AUC (“strength”)** – how well a single feature separates fraud from normal on its own.
* **Ablation** – removing a feature (group) and measuring how much worse (or better) the result gets.
* **NAT / carrier-grade NAT** – many users sharing one public IP address (offices, mobile carriers); why “shared IP” alone is not proof of fraud.
* **Account farm / mule farm** – many accounts driven by one operator, often from the same IP or device.
* **KYC** – “know your customer”: the identity-verification level of an account (full vs minimum).
* **UPI collect request** – a payment *pulled* by the payee (as opposed to a normal “pay”), a channel that fraudsters favour.
