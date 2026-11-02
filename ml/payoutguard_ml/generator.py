"""Synthetic payout-ledger generator with documented error types and separate ground truth.

Everything here is SYNTHETIC. Results measured on this data say how well a method recovers the
error patterns written below. They are not evidence about real payout operations.

Outputs (out_dir):
  ledger.csv, history.csv, current_batch.csv   - what the product would see (no labels)
  labels/row_labels.csv                        - per-row ground truth (entity, injected errors)
  labels/entity_labels.csv                     - per-entity ground truth (cluster, kind, canonical)
  labels/manifest.json                         - seed, params, counts, hashes, error-type docs
"""
from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import SEED, __version__
from .names import (BIZ_LEGAL, BIZ_SUFFIXES, COUNTRIES, COUNTRY_P, FIRST_NAMES, FREE_DOMAINS,
                    LAST_NAMES, NICKNAMES_GEN)

ERROR_TYPES = {
    "alias_duplicate": (
        "An existing payee is paid again under a different identity string (name variant and/or "
        "new email). Two sub-cases: 'both' (canonical row AND alias row are in the batch) and "
        "'drift' (only the alias row is in the batch; canonical was paid in earlier batches)."),
    "email_typo": (
        "Name is correct but the email has exactly one character edit (substitution, deletion, "
        "insertion or adjacent transposition) relative to the payee's known email."),
    "exact_duplicate": "A row is repeated verbatim (same record, same amount, same note) in the batch.",
    "amount_outlier": (
        "Amount multiplied by a decimal-shift factor (x10, x100, /10, /100) or a random high/low "
        "factor. Only injected for payees with >=3 prior payouts (otherwise undetectable by design)."),
}
ALIAS_KINDS = {
    "reorder": "'Last, First' or 'Last First'",
    "initial": "first name reduced to an initial",
    "middle_add": "middle initial added",
    "middle_drop": "middle initial dropped (canonical had one)",
    "nickname": "formal first name replaced by a nickname (table in names.py)",
    "typo_name": "one character edit in first or last name",
    "honorific": "title or generational suffix added (normalises away)",
    "legal_suffix": "business legal suffix added/dropped",
    "email_change": "a new unrelated email address (new domain and/or pattern)",
    "email_variant": "same mailbox written differently (+tag, dots, trailing digits)",
}
DEFAULT_RATES = {"alias_duplicate": 0.04, "email_typo": 0.02, "exact_duplicate": 0.015, "amount_outlier": 0.03}
HISTORY_MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
CURRENT_MONTH = "2026-10"
CURRENCY_BY_COUNTRY = {"US": "USD", "IN": "USD", "BR": "USD", "JP": "USD", "GB": "GBP", "DE": "EUR",
                       "FR": "EUR", "ES": "EUR", "CA": "CAD", "AU": "AUD"}
NEIGHBOURS = {c: n for c, n in zip(string.ascii_lowercase, ["s", "v", "x", "s", "w", "d", "f", "g", "o", "h", "j",
                                                            "k", "n", "m", "i", "o", "w", "e", "a", "r", "y", "c", "q", "z", "t", "x"])}


@dataclass
class Entity:
    entity_id: str
    cluster_id: str
    kind: str                     # person | business
    first: str
    last: str
    middle: str | None
    suffix: str | None            # business word
    legal: str | None             # business legal suffix
    country: str
    currency: str
    email: str
    base: float
    sigma: float
    p_active: float
    start: int
    alias: tuple[str, str, str] | None = None   # (name, email, kind) used in history drift
    alias_from: int = 99
    alias_mix: bool = False
    hist_rows: list = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.kind == "business":
            return " ".join(x for x in [self.last, self.suffix, self.legal] if x)
        mid = f" {self.middle}." if self.middle else ""
        return f"{self.first}{mid} {self.last}"


# ---------------------------------------------------------------- string edits
def edit_once(rng, s: str, protect_first: bool = False) -> str:
    """Exactly one edit: substitute (keyboard neighbour), delete, insert, or transpose adjacent."""
    letters = [i for i, c in enumerate(s) if c.isalpha()]
    if protect_first and len(letters) > 1:
        letters = letters[1:]
    for _ in range(20):
        i = int(rng.choice(letters))
        kind = rng.choice(["sub", "del", "ins", "swap"])
        if kind == "sub":
            c = s[i].lower()
            out = s[:i] + NEIGHBOURS.get(c, "e") + s[i + 1:]
        elif kind == "del" and len(s) > 3:
            out = s[:i] + s[i + 1:]
        elif kind == "ins":
            out = s[:i] + s[i] + s[i:]  # doubled letter
        elif kind == "swap" and i + 1 < len(s) and s[i] != s[i + 1] and s[i + 1].isalpha():
            out = s[:i] + s[i + 1] + s[i] + s[i + 2:]
        else:
            continue
        if out != s:
            return out
    return s + s[-1]


def _email_for(rng, first: str, last: str, domain: str, used: set) -> str:
    f, l = first.lower(), last.lower()
    pats = [f"{f}.{l}", f"{f}{l}", f"{f[0]}.{l}", f"{f[0]}{l}", f"{f}_{l}", f"{l}.{f}", f"{f}{l}{int(rng.integers(1, 99))}"]
    local = pats[int(rng.choice(len(pats), p=[.30, .20, .10, .10, .10, .05, .15]))]
    email, n = f"{local}@{domain}", 1
    while email in used:
        n += 1
        email = f"{local}{n}@{domain}"
    used.add(email)
    return email


def _domain(rng, last: str, used_biz: list) -> str:
    if rng.random() < 0.70:
        return str(rng.choice(FREE_DOMAINS, p=[.55, .15, .12, .08, .05, .05]))
    return f"{last.lower()}{rng.choice(['mail', 'works', 'co', 'hq'])}.com"


# ---------------------------------------------------------------- entity population
def build_entities(rng, n_entities: int, hard: bool = False) -> list[Entity]:
    ents: list[Entity] = []
    used_emails: set = set()
    cid = 0

    def make(first, last, cluster, kind="person", domain=None, country=None) -> Entity:
        country = country or str(rng.choice(COUNTRIES, p=COUNTRY_P))
        if kind == "business":
            suffix = str(rng.choice(BIZ_SUFFIXES))
            legal = str(rng.choice(BIZ_LEGAL)) if rng.random() < 0.5 else None
            dom = f"{last.lower()}{suffix.lower()}.com"
            local = str(rng.choice(["accounts", "billing", "hello", "finance"]))
            email = f"{local}@{dom}"
            n = 1
            while email in used_emails:
                n += 1
                email = f"{local}{n}@{dom}"
            used_emails.add(email)
            middle = None
        else:
            suffix = legal = None
            middle = str(rng.choice(list(string.ascii_uppercase))) if rng.random() < 0.30 else None
            email = _email_for(rng, first, last, domain or _domain(rng, last, []), used_emails)
        u = rng.random()
        sigma = 0.0 if u < 0.35 else (float(rng.uniform(0.05, 0.15)) if u < 0.75 else float(rng.uniform(0.2, 0.5)))
        start = 6 if rng.random() < 0.12 else min(int(rng.exponential(1.8)), 5)
        e = Entity(
            entity_id=f"E{len(ents):05d}", cluster_id=f"C{cluster:05d}", kind=kind, first=first, last=last,
            middle=middle, suffix=suffix, legal=legal, country=country,
            currency=CURRENCY_BY_COUNTRY[country], email=email,
            base=float(np.clip(np.exp(rng.normal(np.log(150), 1.0)), 5, 20000)), sigma=sigma,
            p_active=float(rng.beta(5, 3)), start=start)
        ents.append(e)
        return e

    while len(ents) < n_entities:
        first, last = str(rng.choice(FIRST_NAMES)), str(rng.choice(LAST_NAMES))
        kind = "business" if rng.random() < 0.08 else "person"
        src = make(first, last, cid, kind)
        if kind == "person":
            r = rng.random() / (2.0 if hard else 1.0)     # hard: hard-negative rates doubled
            if r < 0.04:      # relative: same surname, other first name, same domain (hard negative)
                f2 = str(rng.choice([f for f in FIRST_NAMES if f != first]))
                make(f2, last, cid, domain=src.email.split("@")[1], country=src.country)
            elif r < 0.07:    # look-alike: one-edit variant of the name, different person (hard negative)
                f2 = edit_once(rng, first, protect_first=True).capitalize()
                make(f2, last, cid, country=src.country)
            elif r < 0.085:   # homonym: identical name, different mailbox/country (hard negative)
                make(first, last, cid)
        cid += 1

    # Legit identity drift in history: some payees were paid under two records.
    for e in ents:
        if e.kind == "person" and rng.random() < 0.20 or e.kind == "business" and rng.random() < 0.10:
            e.alias = make_alias(rng, e, used_emails, injected=False, hard=hard)
            e.alias_from = int(rng.integers(1, 6))
            e.alias_mix = bool(rng.random() < 0.3)
    return ents


# ---------------------------------------------------------------- aliases
def _name_variant(rng, e: Entity) -> tuple[str, str] | None:
    """Return (display name, kind) for a name-only alias of e, or None."""
    if e.kind == "business":
        opts = ["legal_suffix", "typo_name"]
        k = str(rng.choice(opts))
        if k == "legal_suffix":
            base = f"{e.last} {e.suffix}"
            nm = base if e.legal else f"{base} {rng.choice(BIZ_LEGAL)}"
            return (nm, k) if nm != e.name else None
        return f"{edit_once(rng, e.last, True).capitalize()} {e.suffix}" + (f" {e.legal}" if e.legal else ""), k
    kinds = ["reorder", "initial", "middle_add" if not e.middle else "middle_drop", "nickname", "typo_name", "honorific"]
    k = str(rng.choice(kinds, p=[.17, .17, .14, .17, .20, .15]))
    first, last, mid = e.first, e.last, e.middle
    if k == "reorder":
        return (f"{last}, {first}" if rng.random() < .6 else f"{last} {first}"), k
    if k == "initial":
        return f"{first[0]}. {last}", k
    if k == "middle_add":
        return f"{first} {rng.choice(list(string.ascii_uppercase))}. {last}", k
    if k == "middle_drop":
        return f"{first} {last}", k
    if k == "nickname":
        rev = [n for n, f in NICKNAMES_GEN.items() if f == first]
        if not rev:
            return None
        return f"{rev[int(rng.integers(len(rev)))]} {last}", k
    if k == "typo_name":
        if rng.random() < .5:
            return f"{edit_once(rng, first, True).capitalize()}{f' {mid}.' if mid else ''} {last}", k
        return f"{first}{f' {mid}.' if mid else ''} {edit_once(rng, last, True).capitalize()}", k
    return (f"Dr. {first} {last}" if rng.random() < .5 else f"{first} {last} Jr."), k


def _email_variant(rng, e: Entity, used: set) -> tuple[str, str]:
    local, dom = e.email.split("@")
    if rng.random() < .5:   # same mailbox written differently
        opts = []
        if dom in ("gmail.com",):
            opts.append(f"{local}+payouts@{dom}")
            if "." not in local and len(local) > 4:
                opts.append(f"{local[:3]}.{local[3:]}@{dom}")
        opts.append(f"{local}{int(rng.integers(1, 99))}@{dom}")
        alt = opts[int(rng.integers(len(opts)))]
        if alt != e.email and alt not in used:
            return alt, "email_variant"
    new_dom = str(rng.choice([d for d in FREE_DOMAINS if d != dom]))
    if e.kind == "business":
        return f"{rng.choice(['pay', 'ap', 'team'])}@{e.last.lower()}{(e.suffix or '').lower()}.net", "email_change"
    return _email_for(rng, e.first, e.last, new_dom, used), "email_change"


def make_alias(rng, e: Entity, used: set, injected: bool, hard: bool = False) -> tuple[str, str, str]:
    """(display name, email, kind-string). Name-only, email-only or both changed."""
    for _ in range(30):
        mode = rng.choice(["name", "email", "both"], p=[.15, .15, .70] if hard else [.38, .38, .24])
        name, email, kinds = e.name, e.email, []
        if mode in ("name", "both"):
            v = _name_variant(rng, e)
            if v is None:
                continue
            name, k = v
            kinds.append(k)
        if mode in ("email", "both"):
            email, k = _email_variant(rng, e, used)
            kinds.append(k)
        if (name.lower(), email) != (e.name.lower(), e.email):
            used.add(email)
            return name, email, "+".join(kinds)
    return e.name, e.email.replace("@", "1@", 1), "email_variant"


def email_typo(rng, e: Entity, used: set) -> str:
    local, dom = e.email.split("@")
    for _ in range(20):
        t = edit_once(rng, local, protect_first=False)
        if t != local and f"{t}@{dom}" not in used:
            return f"{t}@{dom}"
    return f"{local}x@{dom}"


# ---------------------------------------------------------------- ledger
def _alias_noise(rng, e: Entity, country: str, amount: float, hard: bool):
    """Hard mode only: alias records may lose or change their country and drift in amount."""
    if not hard:
        return country, amount
    u = rng.random()
    country = "" if u < 0.4 else (str(rng.choice([c for c in COUNTRIES if c != e.country])) if u < 0.5 else country)
    return country, round(amount * float(np.exp(rng.normal(0, 0.5))), 2)


def _amount(rng, e: Entity) -> float:
    if e.sigma == 0.0:
        return float(round(e.base / 5) * 5 or 5)
    return float(round(e.base * np.exp(rng.normal(0, e.sigma)), 2))


def generate(seed: int = SEED, n_entities: int = 6000, rates: dict | None = None, hard: bool = False) -> dict:
    rates = {**DEFAULT_RATES, **(rates or {})}
    rng = np.random.default_rng(np.random.SeedSequence(seed))
    ents = build_entities(rng, n_entities, hard)
    used_emails = {e.email for e in ents} | {e.alias[1] for e in ents if e.alias}
    batches = HISTORY_MONTHS + [CURRENT_MONTH]
    rows: list[dict] = []
    uid = 0
    for bi, b in enumerate(batches):
        for e in ents:
            if bi < e.start:
                continue
            if bi > e.start and rng.random() > e.p_active:
                continue
            name, email = e.name, e.email
            country, amt = e.country, _amount(rng, e)
            if e.alias and bi >= e.alias_from and bi < 6:
                if not e.alias_mix or rng.random() < 0.5:
                    name, email = e.alias[0], e.alias[1]
                    country, amt = _alias_noise(rng, e, country, amt, hard)
            rows.append(dict(uid=uid, batch=bi, entity=e.entity_id, name=name, email=email,
                             country=country, amount=amt, currency=e.currency,
                             errors=[], partner=None, ent=e))
            uid += 1
    by_ent_hist: dict[str, list[dict]] = {}
    for r in rows:
        if r["batch"] < 6:
            by_ent_hist.setdefault(r["entity"], []).append(r)
    cur = [r for r in rows if r["batch"] == 6]
    n_cur = len(cur)

    def pick(pool, k):
        k = min(k, len(pool))
        return [pool[i] for i in rng.choice(len(pool), size=k, replace=False)] if k else []

    free = [r for r in cur]
    taken: set[int] = set()

    def avail(pred):
        return [r for r in free if r["uid"] not in taken and pred(r)]

    # alias_duplicate
    n_alias = int(round(rates["alias_duplicate"] * n_cur))
    pool = avail(lambda r: r["entity"] in by_ent_hist)
    for j, r in enumerate(pick(pool, n_alias)):
        e = r["ent"]
        name, email, kind = make_alias(rng, e, used_emails, injected=True, hard=hard)
        last_hist = by_ent_hist[e.entity_id][-1]
        taken.add(r["uid"])
        if j % 2 == 0:   # 'both': keep canonical row, add alias row in same batch
            c2, a2 = _alias_noise(rng, e, r["country"], _amount(rng, e), hard)
            rows.append(dict(uid=uid, batch=6, entity=e.entity_id, name=name, email=email, country=c2,
                             amount=a2, currency=r["currency"], errors=["alias_duplicate"],
                             partner=r["uid"], ent=e, sub="both", alias_kind=kind))
            uid += 1
        else:            # 'drift': the batch row itself uses the alias record
            c2, a2 = _alias_noise(rng, e, r["country"], r["amount"], hard)
            r.update(name=name, email=email, country=c2, amount=a2, errors=["alias_duplicate"], partner=last_hist["uid"],
                     sub="drift", alias_kind=kind)
    # email_typo
    n_typo = int(round(rates["email_typo"] * n_cur))
    for r in pick(avail(lambda r: r["entity"] in by_ent_hist and (r["name"], r["email"]) == (r["ent"].name, r["ent"].email)), n_typo):
        e = r["ent"]
        taken.add(r["uid"])
        r.update(email=email_typo(rng, e, used_emails), errors=["email_typo"], partner=by_ent_hist[e.entity_id][-1]["uid"],
                 alias_kind="email_typo")
    # exact_duplicate
    for r in pick(avail(lambda r: True), int(round(rates["exact_duplicate"] * n_cur))):
        taken.add(r["uid"])
        rows.append(dict(r, uid=uid, errors=["exact_duplicate"], partner=r["uid"]))
        uid += 1
    # amount_outlier
    n_out = int(round(rates["amount_outlier"] * n_cur))
    shifts = [10.0, 100.0, 0.1, 0.01, None, None]
    for r in pick(avail(lambda r: len(by_ent_hist.get(r["entity"], [])) >= 3), n_out):
        taken.add(r["uid"])
        f = shifts[int(rng.integers(len(shifts)))]
        if f is None:
            f = float(rng.uniform(3, 8)) if rng.random() < .6 else float(rng.uniform(0.05, 0.3))
        r.update(amount=round(r["amount"] * f, 2), errors=["amount_outlier"], outlier_factor=round(f, 4))

    # deterministic final ordering and ids
    order = sorted(rows, key=lambda r: (r["batch"], float(rng.random())))
    id_of = {r["uid"]: f"R{i + 1:06d}" for i, r in enumerate(order)}
    ledger, labels = [], []
    for r in order:
        rid = id_of[r["uid"]]
        month = batches[r["batch"]]
        ledger.append(dict(row_id=rid, batch_id=f"B{month}", batch_date=f"{month}-28", payee_name=r["name"],
                           payee_email=r["email"], country=r["country"], amount=f"{r['amount']:.2f}",
                           currency=r["currency"], note=f"Monthly payout {month}"))
        labels.append(dict(row_id=rid, entity_id=r["entity"], error_types="|".join(r["errors"]),
                           partner_row_id=id_of[r["partner"]] if r["partner"] is not None else "",
                           sub_case=r.get("sub", ""), alias_kind=r.get("alias_kind", ""),
                           outlier_factor=r.get("outlier_factor", "")))
    ent_rows = [dict(entity_id=e.entity_id, cluster_id=e.cluster_id, kind=e.kind, canonical_name=e.name,
                     canonical_email=e.email, has_history_alias=bool(e.alias),
                     history_alias_name=e.alias[0] if e.alias else "", history_alias_email=e.alias[1] if e.alias else "",
                     history_alias_kind=e.alias[2] if e.alias else "") for e in ents]
    return dict(ledger=pd.DataFrame(ledger), row_labels=pd.DataFrame(labels), entity_labels=pd.DataFrame(ent_rows),
                params=dict(seed=seed, n_entities=n_entities, rates=rates))


def write_dataset(out_dir: str | Path, seed: int = SEED, n_entities: int = 6000, rates: dict | None = None, hard: bool = False) -> dict:
    out = Path(out_dir)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    d = generate(seed, n_entities, rates, hard)
    led, lab, ent = d["ledger"], d["row_labels"], d["entity_labels"]
    cur_id = f"B{CURRENT_MONTH}"
    led.to_csv(out / "ledger.csv", index=False)
    led[led.batch_id != cur_id].to_csv(out / "history.csv", index=False)
    led[led.batch_id == cur_id].to_csv(out / "current_batch.csv", index=False)
    lab.to_csv(out / "labels" / "row_labels.csv", index=False)
    ent.to_csv(out / "labels" / "entity_labels.csv", index=False)
    cur_lab = lab[lab.row_id.isin(led[led.batch_id == cur_id].row_id)]
    counts = {t: int(cur_lab.error_types.str.contains(t).sum()) for t in ERROR_TYPES}
    manifest = dict(
        generator_version=__version__, synthetic=True, difficulty="hard" if hard else "standard", **d["params"], batches=HISTORY_MONTHS + [CURRENT_MONTH],
        n_rows=len(led), n_current_rows=int((led.batch_id == cur_id).sum()), n_entities_actual=len(ent),
        n_distinct_records=int(led[["payee_name", "payee_email"]].drop_duplicates().shape[0]),
        injected_counts_current_batch=counts, error_types=ERROR_TYPES, alias_kinds=ALIAS_KINDS,
        notes=["difficulty=hard: alias records are 70% name+email changes, lose/alter country (50%) and drift in amount (lognormal 0.5); hard-negative rates doubled.",
               "History is clean except for 'history identity drift' (~20% of persons, ~10% of businesses are "
               "legitimately paid under two records); those are true same-entity pairs but not injected errors.",
               "Hard negatives: relatives (same surname/domain), look-alike names (one-edit), homonyms (same name).",
               "Amounts are not currency-converted; per-payee spread is 0 (fixed fee, 35%), small or larger."],
        sha256=dict(ledger=hashlib.sha256((out / "ledger.csv").read_bytes()).hexdigest(),
                    row_labels=hashlib.sha256((out / "labels" / "row_labels.csv").read_bytes()).hexdigest()))
    (out / "labels" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
