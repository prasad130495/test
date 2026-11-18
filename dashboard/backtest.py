"""20/50 EMA pullback backtester for gold (Yahoo GC=F as XAUUSD proxy)."""
import json, os, time, urllib.request
import numpy as np, pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE, exist_ok=True)
SYMBOL = "GC=F"

def fetch(tf: str, max_age=900) -> pd.DataFrame:
    path = os.path.join(CACHE, f"{tf}.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval={tf}&range=60d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    r = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")},
                      index=pd.to_datetime(r["timestamp"], unit="s", utc=True)).dropna()
    df.to_csv(path)
    return df

def backtest(tf="15m", fast=20, slow=50, atr_len=14, atr_mult=1.5, rr=3.0,
             risk_pct=1.0, capital=34000.0, spread=0.35, first_touch=True):
    df = fetch(tf).copy()
    c, h, l = df.close, df.high, df.low
    df["ef"] = c.ewm(span=fast, adjust=False).mean()
    df["es"] = c.ewm(span=slow, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / atr_len, adjust=False).mean()
    a = df[["open", "high", "low", "close", "ef", "es", "atr"]].to_numpy()
    idx = df.index
    equity, trades, curve = capital, [], [(idx[0].isoformat(), capital)]
    pos = None; used = False
    for i in range(slow + 1, len(a)):
        o, hi, lo, cl, ef, es, atr = a[i]
        if (a[i-1][4] - a[i-1][5]) * (ef - es) <= 0:   # cross
            used = False
        if pos:
            hit_sl = lo <= pos["sl"] if pos["d"] == 1 else hi >= pos["sl"]
            hit_tp = hi >= pos["tp"] if pos["d"] == 1 else lo <= pos["tp"]
            if hit_sl or hit_tp:                        # stop first if both (conservative)
                px = pos["sl"] if hit_sl else pos["tp"]
                pnl = (px - pos["e"]) * pos["d"] * pos["q"] - spread * pos["q"]
                equity += pnl
                trades.append(dict(entry_time=pos["t"], exit_time=idx[i].isoformat(),
                    side="Long" if pos["d"] == 1 else "Short", entry=round(pos["e"], 2),
                    exit=round(px, 2), qty=round(pos["q"], 2), pnl=round(pnl, 2),
                    r=round(pnl / pos["risk"], 2), result="TP" if not hit_sl else "SL"))
                curve.append((idx[i].isoformat(), round(equity, 2)))
                pos = None
            continue
        if first_touch and used or np.isnan(atr):
            continue
        d = 0
        if ef > es and lo <= ef and cl > ef: d = 1
        elif ef < es and hi >= ef and cl < ef: d = -1
        if d:
            dist = atr * atr_mult
            risk = equity * risk_pct / 100
            pos = dict(d=d, e=cl, sl=cl - d * dist, tp=cl + d * dist * rr,
                       q=risk / dist, risk=risk, t=idx[i].isoformat())
            used = True
    return stats(tf, trades, curve, capital, dict(fast=fast, slow=slow, atr_mult=atr_mult,
                 rr=rr, risk_pct=risk_pct, spread=spread, bars=len(df),
                 start=idx[0].isoformat(), end=idx[-1].isoformat()))

def stats(tf, trades, curve, cap, params):
    t = pd.DataFrame(trades)
    s = dict(tf=tf, params=params, trades=trades, equity=curve)
    if t.empty:
        s["summary"] = dict(n=0); return s
    w, lo = t[t.pnl > 0], t[t.pnl <= 0]
    eq = pd.Series([e for _, e in curve]); dd = (eq.cummax() - eq) / eq.cummax() * 100
    t["month"] = pd.to_datetime(t.exit_time).dt.strftime("%Y-%m-%d")
    daily = t.groupby("month").pnl.sum().round(2)
    s["daily"] = daily.to_dict()
    s["summary"] = dict(
        n=len(t), longs=int((t.side == "Long").sum()), shorts=int((t.side == "Short").sum()),
        net=round(t.pnl.sum(), 2), ret=round(t.pnl.sum() / cap * 100, 2),
        win_rate=round(len(w) / len(t) * 100, 1),
        be_win=round(100 / (1 + params["rr"]), 1),
        pf=round(w.pnl.sum() / abs(lo.pnl.sum()), 2) if len(lo) and lo.pnl.sum() else None,
        avg_win=round(w.pnl.mean(), 2) if len(w) else 0,
        avg_loss=round(lo.pnl.mean(), 2) if len(lo) else 0,
        exp_r=round(t.r.mean(), 2), max_dd=round(dd.max(), 2),
        best=round(t.pnl.max(), 2), worst=round(t.pnl.min(), 2),
        final=round(eq.iloc[-1], 2), trading_days=int(t.month.nunique()))
    return s

if __name__ == "__main__":
    for tf in ("5m", "15m"):
        s = backtest(tf); print(tf, json.dumps(s["summary"]))
