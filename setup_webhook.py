import os
import sys
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable not set")
    sys.exit(1)

VERCEL_URL = os.environ.get("VERCEL_URL")
if not VERCEL_URL:
    print("Error: VERCEL_URL environment variable not set")
    print("Set it to your Vercel app URL, e.g., https://your-app.vercel.app")
    sys.exit(1)

WEBHOOK_URL = f"https://{VERCEL_URL}/webhook"

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={"url": WEBHOOK_URL},
    timeout=30,
)

result = resp.json()
if result.get("ok"):
    print(f"✅ Webhook set successfully to: {WEBHOOK_URL}")
else:
    print(f"❌ Failed to set webhook: {result}")
    sys.exit(1)
