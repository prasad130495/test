"""20/50 EMA pullback backtester on TradingView-exported OANDA:XAUUSD data."""
import json, os, time
import numpy as np, pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE, exist_ok=True)

def tv_path(tf): return os.path.join(CACHE, f"tv_{tf}.csv")

def save_tv(tf: str, text: str) -> dict:
    """Parse a TradingView 'Export chart data' CSV and store it normalised."""
    import io
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().lower() for c in df.columns]
    if "time" not in df.columns or not {"open", "high", "low", "close"} <= set(df.columns):
        raise ValueError("CSV needs time, open, high, low, close columns (TradingView export format)")
    t = df["time"]
    if pd.api.types.is_numeric_dtype(t):
        idx = pd.to_datetime(t, unit="s", utc=True)
    else:
        idx = pd.to_datetime(t, utc=True)
    out = pd.DataFrame({k: df[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")}, index=pd.DatetimeIndex(idx))
    out = out[~out.index.duplicated()].sort_index().dropna()
    if len(out) < 120:
        raise ValueError(f"only {len(out)} bars - need at least 120")
    out.index.name = "time"
    out.to_csv(tv_path(tf))
    return dict(bars=len(out), start=out.index[0].isoformat(), end=out.index[-1].isoformat())

def load_tv(tf: str) -> pd.DataFrame:
    if not os.path.exists(tv_path(tf)):
        raise FileNotFoundError(f"No TradingView data uploaded for {tf} yet")
    return pd.read_csv(tv_path(tf), index_col=0, parse_dates=True)

# ---- Live fetch from TradingView (same websocket approach as the xauusd-ai project) ----
import re, logging
_RES = {"5m": "5", "15m": "15"}
_MINS = {"5m": 5, "15m": 15}
_HB = re.compile(r"~m~\d+~m~~h~\d+")
_SPLIT = re.compile(r"~m~\d+~m~")

def fetch_tradingview(tf: str, symbol="OANDA:XAUUSD", max_bars=20000, idle=10.0, max_wait=90.0) -> dict:
    """Download bars for `symbol` from TradingView and store them. Free accounts get ~1 month of 5m;
    set TV_JWT to a TradingView websocket token for deeper paid-plan history."""
    from tradingview_scraper.symbols.stream import Streamer
    from websocket import WebSocketTimeoutException
    lvl = logging.getLogger().level
    h = Streamer(websocket_jwt_token=os.environ.get("TV_JWT", "unauthorized_user_token")).stream_obj
    logging.getLogger().setLevel(lvl)
    ws = h.ws
    sym = json.dumps({"adjustment": "splits", "symbol": symbol})
    h.send_message("quote_add_symbols", [h.quote_session, "=" + sym])
    h.send_message("resolve_symbol", [h.chart_session, "sds_sym_1", "=" + sym])
    h.send_message("create_series", [h.chart_session, "sds_1", "s1", "sds_sym_1", _RES[tf], max_bars, ""])
    bars, done = {}, False
    ws.settimeout(2)
    deadline, hard = time.time() + idle, time.time() + max_wait
    try:
        while not done and len(bars) < max_bars and time.time() < min(deadline, hard):
            try:
                msg = ws.recv()
            except WebSocketTimeoutException:
                continue
            if _HB.fullmatch(msg):
                ws.send(msg); continue
            for item in filter(None, _SPLIT.split(msg)):
                try: pkt = json.loads(item)
                except json.JSONDecodeError: continue
                if pkt.get("m") in ("symbol_error", "critical_error", "protocol_error"):
                    raise RuntimeError(f"TradingView error: {pkt}")
                if pkt.get("m") == "series_completed": done = True
                if pkt.get("m") in ("timescale_update", "du"):
                    for e in pkt["p"][1].get("sds_1", {}).get("s", []):
                        v = e.get("v", [])
                        if len(v) >= 5:
                            bars[v[0]] = v[:5]; deadline = time.time() + idle
    finally:
        ws.close()
    if not bars:
        raise RuntimeError(f"no data from TradingView for {symbol} {tf}")
    df = pd.DataFrame(sorted(bars.values()), columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[df["time"] + pd.Timedelta(minutes=_MINS[tf]) <= pd.Timestamp.now(tz="UTC")]  # closed bars only
    csv = df.to_csv(index=False)
    return save_tv(tf, csv)

def data_info():
    r = {}
    for tf in ("5m", "15m"):
        try:
            d = load_tv(tf); r[tf] = dict(bars=len(d), start=d.index[0].isoformat(), end=d.index[-1].isoformat())
        except FileNotFoundError:
            r[tf] = None
    return r

def backtest(tf="15m", fast=20, slow=50, atr_len=14, atr_mult=1.5, rr=3.0,
             risk_pct=1.0, capital=34000.0, spread=0.35, first_touch=True):
    df = load_tv(tf).copy()
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

