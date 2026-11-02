"""Text normalisation shared by blocking, features and the pipeline."""
from __future__ import annotations
import re
import unicodedata

from .names import NICKNAMES_FEAT

HONORIFICS = {"dr", "mr", "mrs", "ms", "miss", "prof", "sir"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "llc", "inc", "ltd", "co", "corp", "company"}
DOT_INSENSITIVE_DOMAINS = {"gmail.com", "googlemail.com"}
_NICK_FEAT_LOWER = {k.lower(): v.lower() for k, v in NICKNAMES_FEAT.items()}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_name(raw: str) -> str:
    """Lowercase, accent-free, 'Last, First' reordered, honorifics/suffixes removed."""
    s = strip_accents(str(raw or "")).lower().strip()
    if "," in s:
        a, _, b = s.partition(",")
        s = f"{b.strip()} {a.strip()}"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [t for t in s.split() if t not in HONORIFICS and t not in SUFFIXES]
    return " ".join(toks)


def name_tokens(raw: str) -> list[str]:
    return norm_name(raw).split()


def expand_token(t: str) -> str:
    """Map a nickname to its formal form using the matcher's (partial) nickname table."""
    return _NICK_FEAT_LOWER.get(t, t)


def split_email(raw: str) -> tuple[str, str]:
    s = str(raw or "").strip().lower()
    local, _, domain = s.partition("@")
    return local, domain


def canon_local(local: str, domain: str) -> str:
    """Strip +tag; drop dots for gmail-like providers."""
    local = local.split("+", 1)[0]
    if domain in DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")
    return local


def local_alpha(local: str) -> str:
    return re.sub(r"[^a-z]", "", strip_accents(local))


def soundex(word: str) -> str:
    """Classic American Soundex (letter + 3 digits); empty string for empty input."""
    w = re.sub(r"[^a-z]", "", strip_accents(word).lower())
    if not w:
        return ""
    codes = {c: d for d, cs in {"1": "bfpv", "2": "cgjkqsxz", "3": "dt", "4": "l", "5": "mn", "6": "r"}.items() for c in cs}
    out, prev = w[0].upper(), codes.get(w[0], "")
    for c in w[1:]:
        d = codes.get(c, "")
        if d and d != prev:
            out += d
        if c not in "hw":
            prev = d
    return (out + "000")[:4]


def record_key(name: str, email: str) -> str:
    """Distinct payee record = normalised (display name, email) string pair."""
    return f"{' '.join(str(name or '').lower().split())}|{str(email or '').strip().lower()}"
