import os, json, time, threading, urllib.request
from flask import Flask, request, jsonify, send_from_directory
from pywebpush import webpush, WebPushException

app = Flask(__name__, static_folder="static")
subs = []
PUB = os.getenv("VAPID_PUBLIC_KEY", "")
PRIV = os.getenv("VAPID_PRIVATE_KEY", "")
EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@example.com")
last_signal = None

def ema(values, period):
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = x*k + value*(1-k)
    return value

def analyze():
    url = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=220"
    req = urllib.request.Request(url, headers={"User-Agent": "GIO-BTC/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        rows = json.loads(response.read().decode())
    closes = [float(row[4]) for row in rows[:-1]]
    diffs = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gains = [max(x, 0) for x in diffs[-14:]]
    losses = [max(-x, 0) for x in diffs[-14:]]
    avg_gain, avg_loss = sum(gains)/14, sum(losses)/14
    rsi = 100 if avg_loss == 0 else 100 - 100/(1 + avg_gain/avg_loss)
    e12, e26, macds = sum(closes[:12])/12, sum(closes[:26])/26, []
    for i, x in enumerate(closes):
        if i >= 12: e12 = x*(2/13) + e12*(11/13)
        if i >= 26: e26 = x*(2/27) + e26*(25/27)
        if i >= 26: macds.append(e12-e26)
    macd_signal = ema(macds, 9)
    e20, e50, e200 = ema(closes,20), ema(closes,50), ema(closes,200)
    price, macd = closes[-1], macds[-1]
    signal = "ESPERAR"
    if price > e20 > e50 and price > e200 and macd > macd_signal and 50 < rsi < 70:
        signal = "LONG"
    elif price < e20 < e50 and price < e200 and macd < macd_signal and 30 < rsi < 50:
        signal = "SHORT"
    return {"signal": signal, "price": price, "rsi": rsi, "ema20": e20, "ema50": e50, "ema200": e200}

def push_all(payload):
    if not PUB or not PRIV: return
    for sub in list(subs):
        try:
            webpush(subscription_info=sub, data=json.dumps(payload),
                    vapid_private_key=PRIV, vapid_claims={"sub": EMAIL})
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                if sub in subs: subs.remove(sub)
        except Exception:
            pass

def monitor():
    global last_signal
    while True:
        try:
            data = analyze()
            sig = data["signal"]
            if sig in ("LONG", "SHORT") and sig != last_signal:
                direction = "posible subida" if sig == "LONG" else "posible bajada"
                push_all({"title": "GIO BTC — " + sig,
                          "body": f"{direction}. BTC/USDT ${data['price']:,.2f}. Señal técnica, no garantía."})
            last_signal = sig
        except Exception as exc:
            app.logger.warning("Market check failed: %s", exc)
        time.sleep(60)

@app.get("/")
def index(): return send_from_directory("static", "index.html")
@app.get("/sw.js")
def sw(): return send_from_directory("static", "sw.js", mimetype="application/javascript")
@app.get("/manifest.json")
def manifest(): return send_from_directory("static", "manifest.json")
@app.get("/api/config")
def config(): return jsonify({"vapidPublicKey": PUB})
@app.get("/api/signal")
def signal():
    try: return jsonify(analyze())
    except Exception as exc: return jsonify({"error": str(exc)}), 503
@app.post("/api/subscribe")
def subscribe():
    sub = request.get_json(silent=True)
    if not sub or "endpoint" not in sub: return jsonify({"error":"invalid subscription"}), 400
    if sub not in subs: subs.append(sub)
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
