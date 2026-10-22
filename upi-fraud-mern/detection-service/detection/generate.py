"""Reproducible SYNTHETIC UPI-like transaction generator (vectorised: 1,000,000 rows in seconds).

Why synthetic? Real payment data is private, so this project invents its own:
  * fictional accounts (ACC00000, ACC00001 ...),
  * "normal" background traffic,
  * deliberately injected SUSPICIOUS scenarios (what the detectors should find),
  * deliberately injected BENIGN LOOK-ALIKES (innocent behaviour that resembles fraud,
    to measure false positives),
  * duplicated rows (to exercise de-duplication).

All IDs and IP addresses are fictional. Ground-truth labels are returned SEPARATELY from the
raw data so the detection engine can never read them (they are only used by engine.evaluate()
and by the cross-validation module).

Columns of the raw table
------------------------
REQUIRED (COLS)          tx_id, timestamp, sender, receiver, amount, status
OPTIONAL (OPTIONAL_COLS) extra "data points". They exist so you can practise FEATURE SELECTION:
    ip_address                      -> REAL signal (shared IPs = account farms, new IPs = takeovers)
    channel ("collect" requests), account_age_days (young mule accounts), kyc_level (minimum KYC),
    txn_note_len (empty / very short notes)
                                    -> WEAK signal (fraud scenarios lean slightly this way)
    device_type, network_type, app_version, sender_bank, receiver_bank, sender_state, receiver_state
                                    -> essentially NOISE (generated independently of fraud) - good
                                       candidates to drop later with `disabled_features`
"""
import numpy as np, pandas as pd

# Day 0 of every generated dataset (all timestamps are offsets from this instant).
START = pd.Timestamp("2026-07-01", tz="UTC")
# The columns every transaction feed must have - exactly what the engine requires.
COLS = ["tx_id", "timestamp", "sender", "receiver", "amount", "status"]
# Optional extra columns (see module docstring). The engine works without them; features that need
# them are simply constant zero when they are missing. Same names as engine.OPTIONAL.
OPTIONAL_COLS = ["ip_address", "device_type", "app_version", "channel", "sender_bank", "receiver_bank",
                 "sender_state", "receiver_state", "account_age_days", "kyc_level", "txn_note_len", "network_type"]
ALL_COLS = COLS + OPTIONAL_COLS

# Small categorical vocabularies used for the "extra" columns.
DEVICES = ["android", "ios", "web"]
NETWORKS = ["wifi", "4g", "5g"]
CHANNELS = ["pay", "qr", "intent", "collect"]        # how the payment was initiated ("collect" = pull request)
CHANNEL_P = np.array([.70, .15, .10, .05])           # background channel mix
APP_VERSIONS = ["5.8.1", "5.9.0", "5.10.2", "5.11.0", "5.12.3", "6.0.1", "6.1.0", "6.2.4"]
BANKS = [f"BNK{i:02d}" for i in range(1, 9)]         # fictional bank codes
STATES = ["MH", "DL", "KA", "TN", "UP", "GJ", "WB", "RJ", "KL", "HR", "PB", "MP", "BR", "TS", "AP", "OR"]
# First octets that are valid public unicast IPv4 space (skip 0, 10, 127, 169, 172, 192 and >= 224).
PUBLIC_FIRST = np.array([x for x in range(11, 224) if x not in (127, 169, 172, 192)])


def default_accounts(n_tx):
    """How many accounts to simulate for `n_tx` transactions: about one account per 10 transactions (at least 100).

    This ratio keeps the per-account activity realistic as the data grows: 1,000,000 transactions -> 100,000 accounts."""
    return max(100, int(n_tx) // 10)


def _ipv4_strings(rng, n):
    """n fictional public-looking IPv4 addresses as an object array of strings."""
    o = rng.integers(1, 255, (n, 4))
    o[:, 0] = rng.choice(PUBLIC_FIRST, n)
    return np.array([f"{a}.{b}.{c}.{d}" for a, b, c, d in o.tolist()], dtype=object)


def generate(n_accounts=2000, n_tx=20000, days=90, seed=42, scenario_per_type=None):
    """Create a synthetic dataset.

    Parameters
    ----------
    n_accounts : number of fictional accounts (>= 50). Rule of thumb: default_accounts(n_tx) = n_tx / 10.
    n_tx       : number of background (legitimate) transactions (1,000,000 is fine)
    days       : length of the timeline in days
    seed       : RNG seed - the same seed always gives byte-identical output
    scenario_per_type : how many instances of each scenario to inject
                        (default: max(3, n_tx // 2000))

    Returns a dict of DataFrames:
      users  - account_id + profile (regular / high_activity / merchant)
      raw    - transactions WITH duplicates, shuffled (what the engine receives)
      clean  - the same transactions without duplicates, sorted (used by tests)
      labels - ground truth per tx_id (evaluation / cross-validation only!)
    """
    if n_accounts < 50:
        raise ValueError("n_accounts must be >= 50")
    rng = np.random.default_rng(seed)
    ids = np.array([f"ACC{i:05d}" for i in range(n_accounts)], dtype=object)
    # Account profiles: 86% regular people, 10% very active people, 4% merchants (always at least 3 merchants)
    prof = rng.choice(["regular", "high_activity", "merchant"], n_accounts, p=[.86, .10, .04])
    prof[rng.choice(n_accounts, 3, replace=False)] = "merchant"
    users = pd.DataFrame({"account_id": ids, "profile": prof})
    merchants = np.where(prof == "merchant")[0]   # indices of merchant accounts
    regular = np.where(prof != "merchant")[0]     # scenarios are acted out by non-merchants
    # Sampling weights: high-activity accounts send 8x as often, merchants rarely send
    w = np.where(prof == "high_activity", 8.0, 1.0)
    w[prof == "merchant"] = 0.3
    w /= w.sum()
    k = scenario_per_type or max(3, n_tx // 2000)   # scenario instances per type

    # ---------------- IP address pool ----------------
    # One shared pool of IP strings; a row stores only an INDEX into it (cheap in memory at 1M rows).
    #   [0, n_accounts)              home IP of each account (a few households share one)
    #   [n_accounts, +carrier_n)     mobile-carrier NAT pool shared by everyone (noisy but benign)
    #   [extra_start, ...)           addresses allocated on demand by the scenarios below
    carrier_n = max(500, n_tx // 100)
    extra_start = n_accounts + carrier_n
    ip_pool = _ipv4_strings(rng, extra_start + 10 * k + 50)
    ip_cursor = [extra_start]

    def new_ip():
        """Allocate a fresh scenario IP (an address no account has used before)."""
        i = ip_cursor[0]
        ip_cursor[0] += 1
        return i

    # ---------------- per-account attributes ----------------
    home_ip = np.arange(n_accounts)
    hh = rng.random(n_accounts) < .03                       # 3% of accounts live in a shared household/office
    home_ip[hh] = rng.integers(0, n_accounts, hh.sum())
    acct_dev = np.array(DEVICES, dtype=object)[rng.choice(3, n_accounts, p=[.75, .20, .05])]      # NOISE
    acct_ver = np.array(APP_VERSIONS, dtype=object)[rng.integers(0, len(APP_VERSIONS), n_accounts)]  # NOISE
    acct_bank = np.array(BANKS, dtype=object)[rng.integers(0, len(BANKS), n_accounts)]            # NOISE
    acct_state = np.array(STATES, dtype=object)[rng.integers(0, len(STATES), n_accounts)]         # NOISE
    acct_age = np.clip(rng.lognormal(np.log(400), 1.0, n_accounts), 1, 3650).astype(np.int64)     # days since opening
    acct_kyc = np.where(rng.random(n_accounts) < .70, "full", "min").astype(object)               # weak signal
    age_override = {}   # account index -> young "mule" age used by suspicious scenarios
    kyc_override = {}   # account index -> "min" KYC level used by suspicious scenarios

    # ---------------- legitimate background traffic (fully vectorised) ----------------
    # lognormal amounts (median ~400 INR), daytime-heavy hours, merchants as common counterparties
    hp = np.array([1, 1, 1, 1, 1, 2, 3, 5, 6, 7, 7, 7, 7, 7, 7, 7, 7, 8, 9, 9, 8, 5, 3, 2], float)  # relative traffic per hour
    hp /= hp.sum()
    s = rng.choice(n_accounts, n_tx, p=w)  # senders
    # 40% of payments go to a merchant, the rest to a random account
    r = np.where(rng.random(n_tx) < 0.4, rng.choice(merchants, n_tx), rng.choice(n_accounts, n_tx))
    r = np.where(r == s, (r + 1) % n_accounts, r)  # nobody pays themselves
    amt = np.clip(rng.lognormal(np.log(400), 1.0, n_tx), 1, 50000)
    # timestamp = random day + hour (weighted by `hp`) + random second within the hour
    t = rng.integers(0, days, n_tx) * 86400 + rng.choice(24, n_tx, p=hp) * 3600 + rng.integers(0, 3600, n_tx)
    failed = rng.random(n_tx) < 0.03  # ~3% of transfers fail
    # IP per row: usually the sender's home IP; 25% of the time the shared mobile-carrier pool
    ip_i = np.where(rng.random(n_tx) < .25, n_accounts + rng.integers(0, carrier_n, n_tx), home_ip[s])
    note = np.where(rng.random(n_tx) < .25, 0, np.clip(rng.lognormal(np.log(12), .8, n_tx), 1, 60)).astype(np.int64)
    bg = pd.DataFrame({
        "t": t.astype(float), "s_i": s, "r_i": r, "amount": np.round(amt, 2),
        "status": np.array(["SUCCESS", "FAILED"], dtype=object)[failed.astype(int)],
        "scenario_id": np.full(n_tx, "", dtype=object), "scenario_type": np.full(n_tx, "legit", dtype=object),
        "is_suspicious": np.zeros(n_tx, dtype=np.int64), "is_benign_lookalike": np.zeros(n_tx, dtype=np.int64),
        "ip_i": ip_i,
        "channel": np.array(CHANNELS, dtype=object)[rng.choice(4, n_tx, p=CHANNEL_P)],
        "note": note,
        "network": np.array(NETWORKS, dtype=object)[rng.choice(3, n_tx, p=[.35, .50, .15])],   # NOISE
    })

    # ---------------- injected scenarios (a few thousand rows - a plain loop is fine) ----------------
    rows = []
    cp = np.cumsum(CHANNEL_P)

    def add(t, s_, r_, amt_, typ="legit", scen="", sus=0, benign=0, status="SUCCESS", ip=None, collect_p=None, short_note_p=None):
        """Append one scenario transaction. Unspecified IP falls back to the sender's habits.

        collect_p / short_note_p: for FRAUD rows the probability of a "collect" request / a 0-3 character note
        (a weak signal; ordinary rows use the background mix)."""
        if ip is None:
            ip = n_accounts + int(rng.integers(0, carrier_n)) if rng.random() < .25 else int(home_ip[s_])
        if collect_p is not None and rng.random() < collect_p:
            ch = "collect"
        else:
            ch = CHANNELS[int(np.searchsorted(cp, rng.random()))]
        if short_note_p is not None and rng.random() < short_note_p:
            nl = int(rng.integers(0, 4))
        else:
            nl = 0 if rng.random() < .25 else int(np.clip(rng.lognormal(np.log(12), .8), 1, 60))
        rows.append((float(t), int(s_), int(r_), round(float(amt_), 2), status, scen, typ, sus, benign, ip, ch, nl,
                     rng.choice(NETWORKS, p=[.35, .50, .15])))

    def mark_young(accts, p=.4):
        """With probability p an account in a suspicious scenario is a young (3-60 day old) mule account, often min-KYC."""
        for a in accts:
            if rng.random() < p:
                age_override[int(a)] = int(rng.integers(3, 60))
                if rng.random() < .5:
                    kyc_override[int(a)] = "min"

    pick = lambda m: rng.choice(regular, m, replace=False)          # m distinct non-merchant accounts
    t0 = lambda lo=5: rng.uniform(lo * 86400, (days - 3) * 86400)   # random start time, away from the edges
    F = dict(collect_p=.25, short_note_p=.6)                       # the weak "fraud-leaning" extras for suspicious rows
    for i in range(k):
        # 1) CIRCULAR: 3-5 accounts pass ~the same amount around a ring, 5-60 min apart (loses 1% each hop)
        sc = f"CYC{i}"; nodes = pick(int(rng.integers(3, 6))); a = rng.uniform(5000, 40000); tt = t0(); mark_young(nodes)
        for j in range(len(nodes)):
            add(tt, nodes[j], nodes[(j + 1) % len(nodes)], a, "circular", sc, 1, **F); tt += rng.uniform(300, 3600); a *= .99
        # 2) RAPID ONWARD: a -> b, then b forwards ~97% to c within minutes, c forwards ~94% to d.
        #    The first hop (a->b) is NOT labelled suspicious. Half of the time one operator controls b and c (same IP).
        sc = f"RAP{i}"; a_, b, c, d = pick(4); x = rng.uniform(8000, 50000); tt = t0(); mark_young([b, c])
        op_ip = new_ip() if rng.random() < .5 else None
        add(tt, a_, b, x, "rapid_onward_source", sc)
        add(tt + rng.uniform(120, 900), b, c, x * .97, "rapid_onward", sc, 1, ip=op_ip, **F)
        add(tt + rng.uniform(1000, 1800), c, d, x * .94, "rapid_onward", sc, 1, ip=op_ip, **F)
        # 3) SPLITTING: 6 transfers of 1500-4900 from a to b within about 30 minutes; half from a brand-new IP
        sc = f"SPL{i}"; a_, b = pick(2); tt = t0(); mark_young([a_])
        sp_ip = new_ip() if rng.random() < .5 else None
        for j in range(6):
            add(tt + j * rng.uniform(200, 500), a_, b, rng.uniform(1500, 4900), "splitting", sc, 1, ip=sp_ip, **F)
        # 4) BURST: one account sends 12 transfers to 12 different accounts in a few minutes.
        #    60% are account takeovers (a new IP).
        sc = f"BUR{i}"; p = pick(13); tt = t0(); mark_young([p[0]])
        bu_ip = new_ip() if rng.random() < .6 else None
        for j in range(12):
            add(tt + j * rng.uniform(10, 40), p[0], p[j + 1], rng.uniform(100, 2000), "burst", sc, 1, ip=bu_ip, **F)
        # 5) UNUSUAL BEHAVIOUR: ten small (100-300) historical transfers, then one huge (30k-60k) transfer
        #    (half the time from a brand-new IP)
        sc = f"UNU{i}"; a_, b = pick(2); tt = t0(25)
        for h in range(10):
            add(tt - rng.uniform(86400, 20 * 86400), a_, b, rng.uniform(100, 300), "history", sc)
        add(tt, a_, b, rng.uniform(30000, 60000), "unusual_behavior", sc, 1, ip=new_ip() if rng.random() < .5 else None, **F)
        # 6) IP FARM ("irregular IP"): 6-10 DIFFERENT accounts all send 2k-9k to one collector from the SAME IP
        #    within about an hour - typical of a mule farm / one operator driving many accounts.
        sc = f"IPF{i}"; grp = pick(int(rng.integers(6, 11)) + 1); collector, members = grp[-1], grp[:-1]
        mark_young(members); farm_ip = new_ip(); tt = t0()
        for j, mb in enumerate(members):
            add(tt + j * rng.uniform(60, 400), mb, collector, rng.uniform(2000, 9000), "ip_farm", sc, 1, ip=farm_ip, **F)
        # ---------------- benign look-alikes (label 0) ----------------
        # one large payment to a merchant (e.g. buying a motorbike) - looks like "unusual behaviour"
        a_, = pick(1); add(t0(), a_, rng.choice(merchants), rng.uniform(40000, 90000), "benign_high_value", f"BHV{i}", 0, 1)
        # a pass-through that is innocent (e.g. a shop forwarding a customer's payment) - looks like "rapid onward"
        a_, b, c = pick(3); tt = t0()
        add(tt, a_, b, 20000, "benign_rapid", f"BRP{i}", 0, 1)
        add(tt + rng.uniform(300, 1500), b, c, 18000, "benign_rapid", f"BRP{i}", 0, 1)
        # two relatives (same household => SAME home IP) sending small money back and forth 25 times
        a_, b = pick(2); fam_ip = int(home_ip[a_])
        for tt in rng.uniform(0, (days - 1) * 86400, 25):
            x_, y_ = (a_, b) if rng.random() < .5 else (b, a_)
            add(tt, x_, y_, rng.uniform(1500, 3000), "benign_family", f"BFA{i}", 0, 1, ip=fam_ip)
        # a weekly standing payment to a merchant (rent, tuition, subscription)
        a_, = pick(1); m = rng.choice(merchants); amt_ = rng.uniform(500, 2000); tt = rng.uniform(0, 5 * 86400)
        for j in range(12):  # weekly payment: last one lands on day ~82, inside the 90-day timeline
            add(tt + j * 7 * 86400, a_, m, amt_, "benign_repeated", f"BRE{i}", 0, 1)
        # an OFFICE behind one NAT address: 8 colleagues share one public IP but pay spread over weeks - looks
        # like an IP farm, is not (never 3+ different senders within a couple of hours)
        sc = f"BNA{i}"; office_ip = new_ip()
        for mb in pick(8):
            for _ in range(4):
                add(rng.integers(5, 25) * 86400 + rng.uniform(9 * 3600, 18 * 3600), mb, rng.choice(merchants),
                    rng.uniform(300, 3000), "benign_office_ip", sc, 0, 1, ip=office_ip)
        # a TRAVELLER: one established account pays 10k-30k once from a brand-new IP - looks like account takeover
        a_, = pick(1); add(t0(25), a_, rng.choice(merchants), rng.uniform(10000, 30000), "benign_travel", f"BTR{i}", 0, 1, ip=new_ip())

    sc_df = pd.DataFrame(rows, columns=["t", "s_i", "r_i", "amount", "status", "scenario_id", "scenario_type", "is_suspicious",
                                        "is_benign_lookalike", "ip_i", "channel", "note", "network"])
    df = pd.concat([bg, sc_df], ignore_index=True)
    del bg, sc_df
    df = df.sort_values("t", kind="stable").reset_index(drop=True)   # chronological order
    n = len(df)
    s_i, r_i = df.s_i.to_numpy(), df.r_i.to_numpy()
    # ---------------- turn indices into the final column values (vectorised) ----------------
    df["tx_id"] = np.array([f"TX{i:07d}" for i in range(n)], dtype=object)           # ids assigned AFTER sorting
    secs = np.floor(df.t.to_numpy()).astype("timedelta64[s]")
    df["timestamp"] = np.char.add(np.datetime_as_string(np.datetime64("2026-07-01T00:00:00") + secs, unit="s"), "Z").astype(object)
    df["sender"], df["receiver"] = ids[s_i], ids[r_i]
    df["ip_address"] = ip_pool[df.ip_i.to_numpy()]
    df["device_type"], df["app_version"] = acct_dev[s_i], acct_ver[s_i]
    df["sender_bank"], df["receiver_bank"] = acct_bank[s_i], acct_bank[r_i]
    df["sender_state"], df["receiver_state"] = acct_state[s_i], acct_state[r_i]
    # young / min-KYC mule accounts: apply the overrides at ACCOUNT level (a mule account looks young on every row)
    if age_override:
        acct_age[list(age_override)] = list(age_override.values())
    if kyc_override:
        acct_kyc[list(kyc_override)] = list(kyc_override.values())
    df["account_age_days"], df["kyc_level"] = acct_age[s_i], acct_kyc[s_i]
    df["txn_note_len"], df["network_type"] = df.note, df.network
    # duplicated source records (1% of rows are repeated) - the engine must de-duplicate these
    dup = df.sample(n=max(2, n // 100), random_state=seed)
    raw = pd.concat([df[ALL_COLS], dup[ALL_COLS]]).sample(frac=1, random_state=seed).reset_index(drop=True)
    clean = df[ALL_COLS].copy()
    labels = df[["tx_id", "scenario_id", "scenario_type", "is_suspicious", "is_benign_lookalike"]].copy()
    return {"users": users, "raw": raw, "clean": clean, "labels": labels}
