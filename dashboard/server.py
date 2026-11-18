"""Serve the dashboard; optionally expose via ngrok (set NGROK_AUTHTOKEN)."""
import json, os, sys, subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from backtest import backtest, save_tv, data_info, fetch_tradingview

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8050))

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            tf = q["tf"][0]; assert tf in ("5m", "15m")
            if u.path == "/api/fetch":
                src = q.get("source", ["tradingview"])[0]; days = str(int(q.get("days", ["90"])[0]))
                fn = "fetch_dukascopy" if src == "dukascopy" else "fetch_tradingview"
                args = [tf, days] if src == "dukascopy" else [tf]
                r = subprocess.run([sys.executable, "-c", f"import sys,json;from backtest import {fn} as f;print(json.dumps(f(*sys.argv[1:])))", *args],
                                   cwd=HERE, capture_output=True, text=True, timeout=1500)
                if r.returncode: raise RuntimeError((r.stderr.strip().splitlines() or ["fetch failed"])[-1])
                return self.send(200, r.stdout.strip().splitlines()[-1].encode(), "application/json")
            assert u.path == "/api/upload"
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8-sig")
            self.send(200, json.dumps(save_tv(tf, body)).encode(), "application/json")
        except Exception as e:
            self.send(400, json.dumps({"error": str(e)}).encode(), "application/json")
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/data":
            self.send(200, json.dumps(data_info()).encode(), "application/json")
        elif u.path == "/api/backtest":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                kw = dict(tf=q.get("tf", "15m"), atr_mult=float(q.get("atr", 1.5)),
                          rr=float(q.get("rr", 3)), risk_pct=float(q.get("risk", 1)),
                          spread=float(q.get("spread", 0.35)), capital=float(q.get("cap", 34000)),
                          first_touch=q.get("first", "1") == "1")
                assert kw["tf"] in ("5m", "15m")
                self.send(200, json.dumps(backtest(**kw)).encode(), "application/json")
            except Exception as e:
                self.send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        elif u.path in ("/", "/index.html"):
            self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        else:
            self.send(404, b"not found", "text/plain")

if __name__ == "__main__":
    share = bool(os.environ.get("NGROK_AUTHTOKEN"))
    srv = ThreadingHTTPServer(("127.0.0.1" if share else "0.0.0.0", PORT), H)
    print(f"Local: http://localhost:{PORT}")
    if share:
        import secrets, string
        from pyngrok import ngrok, conf
        auth = os.path.join(HERE, ".auth")        # `user:password`, 8-128 chars (ngrok basic-auth rule)
        if not os.path.exists(auth) or not open(auth).read().strip():
            pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
            fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, f"gold:{pw}\n".encode()); os.close(fd)
        login = open(auth).read().strip()
        conf.get_default().auth_token = os.environ["NGROK_AUTHTOKEN"]
        url = ngrok.connect(PORT, basic_auth=[login]).public_url
        print(f"ngrok URL: {url}\nLogin:     {login.replace(':', '  /  password: ')}\n(saved in dashboard/.auth)", flush=True)
    srv.serve_forever()
