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

// PayPal item transaction_status -> plain label + tone
export const ITEM_STATUS = {
  null: ["Waiting for PayPal", "neutral"],
  PENDING: ["Pending at PayPal", "neutral"],
  SUCCESS: ["Paid", "good"],
  UNCLAIMED: ["Unclaimed: the receiver has no PayPal account for this email yet", "warn"],
  ONHOLD: ["On hold at PayPal", "warn"],
  FAILED: ["Failed: no money was taken", "bad"],
  RETURNED: ["Returned to your account", "bad"],
  BLOCKED: ["Blocked by PayPal", "bad"],
  REFUNDED: ["Refunded by the receiver", "bad"],
  REVERSED: ["Reversed by PayPal", "bad"],
};

export const itemStatus = (s) => ITEM_STATUS[s ?? "null"] || [s, "neutral"];
