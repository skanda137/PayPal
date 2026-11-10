import json, os, base64, urllib.request
from dotenv import load_dotenv
load_dotenv(override=True)
base = os.environ["PAYPAL_BASE_URL"].rstrip("/")
auth = base64.b64encode(f'{os.environ["PAYPAL_CLIENT_ID"]}:{os.environ["PAYPAL_CLIENT_SECRET"]}'.encode()).decode()

def call(path, data=None, headers=None):
    req = urllib.request.Request(base + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

token = call("/v1/oauth2/token", b"grant_type=client_credentials",
             {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})["access_token"]

ids = []
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "payout_batch_id" and v not in ids:
                ids.append(v)
            else:
                walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
walk(json.load(open("smoke_report.json")))

for i in ids:
    b = call(f"/v1/payments/payouts/{i}?page_size=20", headers={"Authorization": "Bearer " + token})
    h = b["batch_header"]
    print(h["sender_batch_header"]["sender_batch_id"], h["batch_status"])
    for it in b["items"]:
        print("   ", it["payout_item"]["sender_item_id"], it["payout_item"]["receiver"], it["transaction_status"],
              (it.get("errors") or {}).get("name"))