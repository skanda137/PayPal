"""Plain-language explanations built ONLY from evidence fields.

Deterministic templates produce the text. An LLM may optionally reword it, but its output is
accepted only if every number, email address and quoted name it uses already appears in the
template text, and it uses no banned wording; otherwise the template text is returned.
"""
from __future__ import annotations
import re

BANNED = ("fraud", "scam", "criminal", "guarantee", "definitely", "certainly", "proves", "stolen", "illegal")
LLM_SYSTEM_PROMPT = (
    "Rewrite the explanation below in a calm, plain tone for a payments operator. Use ONLY the facts "
    "given. Do not add, remove or change any number, name or email address. Do not speculate about "
    "intent. Do not call anything fraud. Keep it under 60 words. Return only the rewritten text.")


def severity_of(prob: float) -> str:
    return "high" if prob >= 0.9 else "medium" if prob >= 0.7 else "low"


def _label(prob: float) -> str:
    return "Very likely" if prob >= 0.9 else "Likely" if prob >= 0.7 else "Possible" if prob >= 0.4 else "Weak signal"


def pair_signals(f: dict, a: dict, b: dict) -> list[str]:
    """Evidence codes from feature values (f: feature name -> value) and the two record dicts."""
    s = []
    if f["name_equal"]:
        s.append("NAME_IDENTICAL")
    elif f["tok_jaccard"] >= 0.99 and f["nm_ratio"] < 0.95:
        s.append("NAME_REORDERED")
    elif f["initial_compat"]:
        s.append("NAME_INITIAL")
    elif f["nick_used"]:
        s.append("NICKNAME")
    elif f["nm_token_set"] >= 0.99:
        s.append("NAME_SUBSET")
    elif f["nm_ratio"] >= 0.80:
        s.append("NAME_CLOSE")
    if a["email"].lower() == b["email"].lower():
        s.append("EMAIL_IDENTICAL")
    elif f["em_canon_equal"]:
        s.append("EMAIL_SAME_MAILBOX")
    elif f["em_lev"] == 1:
        s.append("EMAIL_ONE_EDIT")
    elif f["em_alpha_equal"] or (f["em_lev"] <= 3 and f["em_local_jw"] >= 0.9):
        s.append("EMAIL_CLOSE")
    elif f["em_local_jw"] < 0.6:
        s.append("EMAIL_UNRELATED")
    if f["amt_logratio"] <= 0.2:
        s.append("AMOUNT_SIMILAR")
    elif f["amt_logratio"] >= 0.7:
        s.append("AMOUNT_DIFFERENT")
    if (a["n_hist_rows"] == 0) != (b["n_hist_rows"] == 0):
        s.append("ONE_RECORD_NEW")
    if "NAME_IDENTICAL" in s and "EMAIL_UNRELATED" in s:
        s.append("CAUTION_HOMONYM")
    return s


def _money(x, cur):
    return f"{x:,.2f} {cur}"


def explain_pair(prob: float, a: dict, b: dict, signals: list[str]) -> dict:
    """a, b: {name,email,n_hist_rows,median_amount,currency,last_hist_batch}. Returns explanation dict."""
    p = round(prob * 100)
    bullets = []
    na, nb = f"'{a['name']}'", f"'{b['name']}'"
    T = {
        "NAME_IDENTICAL": (f"Both records use the name {na}." if a["name"] == b["name"]
                           else f"The names {na} and {nb} are the same after normalisation."),
        "NAME_REORDERED": f"{na} and {nb} contain the same words in a different order.",
        "NAME_INITIAL": f"{na} and {nb} look like the same name with an initial in place of a full word.",
        "NICKNAME": f"{na} and {nb} differ only by a common short form of a first name.",
        "NAME_SUBSET": f"One name contains all the words of the other ({na} / {nb}).",
        "NAME_CLOSE": f"The names {na} and {nb} are very similar but not identical.",
        "EMAIL_IDENTICAL": "Both records use the same email address.",
        "EMAIL_SAME_MAILBOX": f"The emails '{a['email']}' and '{b['email']}' point to the same mailbox once tags and dots are ignored.",
        "EMAIL_ONE_EDIT": f"The emails '{a['email']}' and '{b['email']}' differ by exactly one character.",
        "EMAIL_CLOSE": f"The emails '{a['email']}' and '{b['email']}' are nearly the same.",
        "EMAIL_UNRELATED": f"The emails '{a['email']}' and '{b['email']}' do not look related.",
        "AMOUNT_SIMILAR": "Their typical payout amounts are similar.",
        "AMOUNT_DIFFERENT": "Their typical payout amounts are quite different.",
    }
    for code in signals:
        if code in T:
            bullets.append(T[code])
    old, new = (a, b) if a["n_hist_rows"] >= b["n_hist_rows"] else (b, a)
    if "ONE_RECORD_NEW" in signals:
        bullets.append(f"'{new['email']}' has not been paid before; '{old['email']}' has been paid {old['n_hist_rows']} "
                       f"time(s), last in batch {old['last_hist_batch']}.")
    caution = ("The names are identical but the emails are unrelated, so these could be two different people." 
               if "CAUTION_HOMONYM" in signals else "")
    headline = (f"{_label(prob)}: {na} <{a['email']}> may be the same payee as {nb} <{b['email']}> "
                f"(model-estimated probability {p}%).")
    facts = dict(probability_percent=p, a=a, b=b, signals=signals)
    return dict(headline=headline, bullets=bullets, caution=caution, severity=severity_of(prob), facts=facts)


def explain_amount(ev: dict, payee: str, currency: str) -> dict:
    bullets = []
    if ev["basis"] == "payee_history":
        bullets.append(f"{_money(ev['amount'], currency)} is {ev['ratio']:.2f}x this payee's median of "
                       f"{_money(ev['payee_median'], currency)} across {ev['n_history']} earlier payouts.")
        bullets.append(f"Their usual range is about {_money(ev['typical_low'], currency)} to {_money(ev['typical_high'], currency)}.")
    else:
        bullets.append(f"There are only {ev['n_history']} earlier payouts for this payee, so the amount "
                       f"{_money(ev['amount'], currency)} was compared with other {currency} payees (median {_money(ev['payee_median'], currency)}).")
    if ev.get("decimal_shift"):
        f = ev["decimal_shift"]
        bullets.append(f"The ratio is close to {f:g}x, which can happen when a decimal point is misplaced.")
    headline = f"Unusual amount for '{payee}': {_money(ev['amount'], currency)}."
    sev = "high" if ev["score"] >= 8 else "medium"
    return dict(headline=headline, bullets=bullets, caution="", severity=sev, facts=dict(evidence=ev, payee=payee, currency=currency))


def render_text(exp: dict) -> str:
    parts = [exp["headline"], *exp["bullets"]]
    if exp.get("caution"):
        parts.append(exp["caution"])
    return " ".join(parts)


# ---------------------------------------------------------------- constrained LLM wording
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_QUOTED = re.compile(r"'([^']+)'")


def _tokens(text: str) -> set:
    emails = set(_EMAIL.findall(text))
    nums = set(_NUM.findall(_EMAIL.sub(" ", text)))
    names = {q.lower() for q in _QUOTED.findall(text)}
    return emails | nums | names


def validate_llm_text(candidate: str, template_text: str) -> tuple[bool, list[str]]:
    """Accept only if every number/email/quoted name in `candidate` is present in the template text."""
    problems = []
    allowed_lower = {t.lower() for t in _tokens(template_text)}
    for tok in _tokens(candidate):
        if tok.lower() not in allowed_lower:
            problems.append(f"unsupported token: {tok}")
    low = candidate.lower()
    problems += [f"banned word: {w}" for w in BANNED if w in low]
    if not candidate.strip():
        problems.append("empty")
    return (not problems), problems


def build_llm_prompt(exp: dict) -> str:
    return f"{LLM_SYSTEM_PROMPT}\n\nEXPLANATION:\n{render_text(exp)}"


def finalize(exp: dict, llm_fn=None) -> dict:
    """Attach `text` (template) and, if llm_fn is given and its output validates, `text_llm`."""
    base = render_text(exp)
    out = dict(exp, text=base, text_source="template")
    if llm_fn is not None:
        try:
            cand = llm_fn(build_llm_prompt(exp))
            ok, probs = validate_llm_text(cand, base)
            if ok:
                out.update(text=cand.strip(), text_source="llm_validated", template_text=base)
            else:
                out["llm_rejected_reasons"] = probs
        except Exception as e:  # LLM failure never blocks the template text
            out["llm_error"] = str(e)[:200]
    return out
