// Thin wrapper over the backend API. Errors carry the HTTP status and the backend's `detail`.
export class ApiError extends Error {
  constructor(status, detail) {
    const msg = typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail);
    super(msg);
    this.status = status;
    this.detail = detail;
  }
}

async function call(method, path, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(0, `Cannot reach the backend (${e.message}). Is it running on :8000?`);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data ?? res.statusText);
  return data;
}

export const api = {
  upload(file, history) {
    const fd = new FormData();
    fd.append("file", file);
    if (history) fd.append("history", history);
    return call("POST", "/api/batches", fd, true);
  },
  batch: (id) => call("GET", `/api/batches/${id}`),
  decide: (id, decisions, override = false) =>
    call("PUT", `/api/batches/${id}/decisions${override ? "?override=true" : ""}`, decisions),
  send: (id) => call("POST", `/api/batches/${id}/send`),
  payout: (id) => call("GET", `/api/batches/${id}/payout`),
  audit: (id) => call("GET", `/api/batches/${id}/audit`),
  eval: () => call("GET", "/api/eval"),
};
