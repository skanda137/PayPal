export const FLAG_LABEL = {
  duplicate_payee: "Possible duplicate payee",
  typo_receiver: "Possible email typo",
  amount_anomaly: "Unusual amount",
  invalid_input: "Invalid input",
};

export const CATEGORY_LABEL = {
  new_record_matches_known_payee: "New record matches a known payee",
  two_records_same_payee: "Two paid records, likely the same payee (informational)",
};

// PayPal item transaction_status -> [plain label, tone]. Only SUCCESS means the recipient has the money.
export const ITEM_STATUS = {
  null: ["Waiting for PayPal", "neutral"],
  PENDING: ["Pending at PayPal", "neutral"],
  SUCCESS: ["Delivered to the recipient's PayPal account", "good"],
  UNCLAIMED: ["Not delivered: recipient has no PayPal account (funds return after 30 days if unclaimed)", "bad"],
  ONHOLD: ["Not delivered yet: on hold at PayPal", "warn"],
  FAILED: ["Not delivered: failed", "bad"],
  RETURNED: ["Not delivered: returned to your account", "bad"],
  BLOCKED: ["Not delivered: blocked by PayPal", "bad"],
  REFUNDED: ["Refunded by the recipient", "bad"],
  REVERSED: ["Reversed by PayPal", "bad"],
};

export const itemStatus = (s) => ITEM_STATUS[s ?? "null"] || [`Unknown status ${s}: check PayPal`, "warn"];

export const SHORT_FLAG = { duplicate_payee: "duplicate", typo_receiver: "email typo", amount_anomaly: "amount", invalid_input: "invalid" };
