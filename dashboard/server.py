"""Serve the dashboard; optionally expose via ngrok (set NGROK_AUTHTOKEN)."""
import json, os, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from backtest import backtest, save_tv, data_info

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
            tf = q["tf"][0]; assert u.path == "/api/upload" and tf in ("5m", "15m")
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
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"Local: http://localhost:{PORT}")
    if os.environ.get("NGROK_AUTHTOKEN"):
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = os.environ["NGROK_AUTHTOKEN"]
        print("ngrok URL:", ngrok.connect(PORT).public_url, flush=True)
    srv.serve_forever()
