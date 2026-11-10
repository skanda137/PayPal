"""Check the local setup before any live sandbox call. No network. Prints PASS/WARN/FAIL per check, never a value.

Run from the repo root:  python demo/preflight.py
Exit code 1 if any check fails. WARN does not fail: it marks something unusual that may still work.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
ACCOUNTS = ROOT / "demo" / "sandbox_accounts.json"
REQUIRED = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "RECEIVERS", "TYPO_RECEIVER"]
ROLES = ["clean", "clean2", "clean3"]   # the registered accounts that get paid in the live run; other roles are optional
SANDBOX_URL = "https://api-m.sandbox.paypal.com"
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
CREDENTIAL_LENGTH_RANGE = (60, 100)   # working sandbox client ids and secrets have been 80 to 82 characters

failed = False


def report(level, check, detail=""):
    global failed
    failed = failed or level == "FAIL"
    print(f"{level:<4}  {check}" + (f"  ({detail})" if detail else ""))


def check(ok, name, detail_if_bad="", level_if_bad="FAIL"):
    report("PASS" if ok else level_if_bad, name, "" if ok else detail_if_bad)
    return ok


def parse(path):
    """[(key, raw_value)] for KEY=value lines, comments and blanks skipped. Raw value keeps quotes and spaces."""
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.strip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out.append((key, value))
    return out


def one_edit_apart(a, b):
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = next((k for k in range(len(a)) if a[k] != b[k]), len(a))
    return a[i + 1:] == b[i + 1:] if len(a) == len(b) else a[i:] == b[i + 1:]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).returncode


def main():
    if not check(ENV.exists(), ".env exists", "copy .env.example to .env and fill it in"):
        return
    check(git("check-ignore", "-q", ".env") == 0, ".env is ignored by git", "add .env to .gitignore")
    check(git("ls-files", "--error-unmatch", ".env") != 0, ".env is not tracked by git", "it is committed: remove it and rotate the secret")

    pairs = parse(ENV)
    keys = [k.strip() for k, _ in pairs]
    env = {k.strip(): v.strip().strip("\"'") for k, v in pairs}
    required = REQUIRED + [k.strip() for k, _ in parse(EXAMPLE) if k.strip() not in REQUIRED]
    missing = [k for k in required if not env.get(k)]
    check(not missing, f"required names are set ({len(required)})", f"missing or empty: {missing}")
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    check(not dupes, "no duplicate keys", f"duplicated: {dupes}")
    quoted = [k.strip() for k, v in pairs if v.strip()[:1] in "\"'" or v.strip()[-1:] in "\"'"]
    check(not quoted, "no quotes around values", f"quoted: {quoted}")
    spaced = [k.strip() for k, v in pairs if k != k.strip() or v != v.strip()]
    check(not spaced, "no spaces around names or values", f"spaces in: {spaced}")

    for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
        value = env.get(name, "")
        if value:
            check(CREDENTIAL_LENGTH_RANGE[0] <= len(value) <= CREDENTIAL_LENGTH_RANGE[1], f"{name} length",
                  f"{len(value)} characters; expected {CREDENTIAL_LENGTH_RANGE[0]} to {CREDENTIAL_LENGTH_RANGE[1]}", "WARN")
            check(re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None, f"{name} has only letters, digits, - and _",
                  "contains other characters")
    if env.get("PAYPAL_CLIENT_ID") and env.get("PAYPAL_CLIENT_SECRET"):
        check(env["PAYPAL_CLIENT_ID"] != env["PAYPAL_CLIENT_SECRET"], "client id and secret differ", "the same value is in both")
    if env.get("PAYPAL_BASE_URL"):
        check(env["PAYPAL_BASE_URL"] == SANDBOX_URL, "PAYPAL_BASE_URL is the sandbox URL", f"must be {SANDBOX_URL}")

    receivers = [r.strip() for r in env.get("RECEIVERS", "").split(",") if r.strip()]
    typo = env.get("TYPO_RECEIVER", "")
    if receivers:
        check(all(EMAIL.fullmatch(r) for r in receivers), f"RECEIVERS are email addresses ({len(receivers)})",
              "at least one entry is not an email address")
        check(len(set(receivers)) == len(receivers), "RECEIVERS has no repeats", "the same address appears twice")
    if typo:
        check(EMAIL.fullmatch(typo) is not None, "TYPO_RECEIVER is an email address", "not an email address")
        check(typo not in receivers, "TYPO_RECEIVER is not one of RECEIVERS", "it must be a mistyped address, not a real one")
        check(any(one_edit_apart(typo, r) for r in receivers), "TYPO_RECEIVER is one character away from a receiver",
              "more than one character differs from every receiver", "WARN")

    if not check(ACCOUNTS.exists(), "demo/sandbox_accounts.json exists", "needed for the seeded live subset"):
        return
    accounts = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
    absent = [r for r in ROLES if not isinstance(accounts.get(r), str) or not EMAIL.fullmatch(accounts[r])]
    check(not absent, f"sandbox_accounts.json resolves the {len(ROLES)} roles {ROLES} to email addresses", f"missing or invalid: {absent}")
    values = [v for v in accounts.values() if isinstance(v, str)]
    check(len(set(v.lower() for v in values)) == len(values), "each role has its own address", "two roles share an address")
    not_registered = sorted(r for r in ROLES if isinstance(accounts.get(r), str) and accounts[r].lower() not in {x.lower() for x in receivers})
    check(not not_registered, "the paid roles are RECEIVERS (accounts that delivered SUCCESS in the smoke test)",
          f"not in RECEIVERS: {not_registered}; a payout to an unregistered email comes back UNCLAIMED", "WARN")
    check(git("check-ignore", "-q", "demo/sandbox_accounts.json") == 0, "demo/sandbox_accounts.json is ignored by git",
          "add it to .gitignore")


if __name__ == "__main__":
    main()
    print("\nRESULT:", "FAIL" if failed else "PASS", "(no network calls were made; this does not prove the credentials are valid)")
    sys.exit(1 if failed else 0)
