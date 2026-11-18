"""Dukascopy public datafeed: spot XAU/USD M1 bid candles -> 5m/15m bars (years of history)."""
import lzma, os, random, struct, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "duka")
_REC = struct.Struct(">IIIIIf")  # secs, open, close, low, high, volume (price x1000)

def _get(day: pd.Timestamp) -> bytes | None:
    rel = f"{day.year}/{day.month-1:02d}/{day.day:02d}/BID_candles_min_1.bi5"
    path = os.path.join(CACHE, rel.replace("/", "_"))
    if os.path.exists(path):
        return open(path, "rb").read() or None
    req = urllib.request.Request(f"{BASE}/{rel}", headers={"User-Agent": "Mozilla/5.0"})
    for a in range(6):
        try:
            blob = urllib.request.urlopen(req, timeout=45).read()
            if blob[:1] == b"<": raise urllib.error.HTTPError(req.full_url, 503, "html", None, None)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404: blob = b""; break
            if e.code not in (429, 500, 502, 503, 504) or a == 5: raise
        except urllib.error.URLError:
            if a == 5: raise
        time.sleep(min(20, 2 ** a) * (0.5 + random.random()))
    if day.normalize() < pd.Timestamp.now(tz="UTC").normalize():   # cache completed days only
        os.makedirs(CACHE, exist_ok=True); open(path, "wb").write(blob)
    return blob or None

def _day(day):
    try:
        blob = _get(day)
    except Exception:
        return None
    if not blob: return None
    raw = lzma.decompress(blob); n = len(raw) // 24
    a = np.array([_REC.unpack_from(raw, i * 24) for i in range(n)], dtype="float64")
    s, o, c, lo, hi, v = a.T
    df = pd.DataFrame({"open": o/1000, "high": hi/1000, "low": lo/1000, "close": c/1000, "volume": v},
                      index=pd.DatetimeIndex(day + pd.to_timedelta(s, unit="s")))
    return df[df.volume > 0]

def fetch_m1(days: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    rng = [d for d in pd.date_range((now - pd.Timedelta(days=days)).normalize(), now.normalize(), freq="D") if d.weekday() != 5]
    with ThreadPoolExecutor(2) as ex:
        fr = [f for f in ex.map(_day, rng) if f is not None and len(f)]
    if not fr: raise RuntimeError("no Dukascopy data returned")
    return pd.concat(fr).sort_index()

def fetch_bars(tf: str, days: int) -> pd.DataFrame:
    m1 = fetch_m1(days)
    r = m1.resample({"5m": "5min", "15m": "15min"}[tf], label="left", closed="left")
    out = pd.DataFrame({"open": r.open.first(), "high": r.high.max(), "low": r.low.min(), "close": r.close.last()}).dropna()
    mins = {"5m": 5, "15m": 15}[tf]
    out = out[out.index + pd.Timedelta(minutes=mins) <= pd.Timestamp.now(tz="UTC")]
    out.index.name = "time"
    return out
