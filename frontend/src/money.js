// Money as integer cents from the backend's decimal strings. No float arithmetic on amounts.
export function toCents(amount) {
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(amount));
  if (!m) throw new Error(`unexpected amount ${amount}`);
  return BigInt(m[1]) * 100n + BigInt((m[2] || "0").padEnd(2, "0"));
}

export function fmtCents(cents, currency) {
  const neg = cents < 0n;
  const abs = neg ? -cents : cents;
  const whole = (abs / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (abs % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${whole}.${frac} ${currency}`;
}

// rows -> [{currency, cents, n}] sorted by currency
export function totalsByCurrency(rows) {
  const by = new Map();
  for (const r of rows) {
    const t = by.get(r.currency) || { currency: r.currency, cents: 0n, n: 0 };
    t.cents += toCents(r.amount);
    t.n += 1;
    by.set(r.currency, t);
  }
  return [...by.values()].sort((a, b) => a.currency.localeCompare(b.currency));
}

export const fmtTotals = (rows) =>
  totalsByCurrency(rows)
    .map((t) => fmtCents(t.cents, t.currency))
    .join(" · ") || "0.00";
