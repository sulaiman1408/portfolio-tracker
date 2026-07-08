import os
import re
import uuid
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

TASI_TICKER = "^TASI.SR"
REQUIRED_COLS = ["ticker", "company", "date", "purchase price", "quantity", "status", "cost"]

PORTFOLIOS = {
    "My Portfolio": "https://docs.google.com/spreadsheets/d/1qy6kPvDVB4pn16LX5grFznmSZv-NbyrBjrO1qYcgEkw/edit?usp=sharing",
    "My Father's Portfolio": "https://docs.google.com/spreadsheets/d/12Fc63gUKdXcI2STDKaKe-wKHfEc7teUdS5A4UdRPZ6w/edit?usp=sharing",
}

# In-memory session store: session_id -> DataFrame
_sessions = {}


# ── data loading ──────────────────────────────────────────────────────────────

def normalize_df(df):
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    df["date"] = pd.to_datetime(df["date"])
    for col in ["purchase price", "quantity", "cost"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def compute_positions(df):
    """FIFO lot tracking → remaining quantity/cost and realized P&L per ticker."""
    positions = []
    for ticker, group in df.groupby("ticker"):
        lots, realized_pl = [], 0.0
        for _, row in group.iterrows():
            status, qty, cost = row["status"], row["quantity"], row["cost"]
            if status in ("buy", "stock split"):
                lots.append([float(qty), float(cost)])
            elif status == "sell":
                to_sell, proceeds = -float(qty), -float(cost)
                cost_removed = 0.0
                while to_sell > 0 and lots:
                    lq, lc = lots[0]
                    lps = lc / lq if lq else 0
                    if lq <= to_sell:
                        cost_removed += lc
                        to_sell -= lq
                        lots.pop(0)
                    else:
                        cost_removed += lps * to_sell
                        lots[0] = [lq - to_sell, lc - lps * to_sell]
                        to_sell = 0
                realized_pl += proceeds - cost_removed
        positions.append({
            "ticker": ticker,
            "company": group["company"].iloc[0].title(),
            "quantity": round(sum(l[0] for l in lots), 6),
            "cost_basis": sum(l[1] for l in lots),
            "realized_pl": realized_pl,
        })
    return positions


def fetch_prices(tickers):
    prices = {}
    for t in tickers:
        try:
            p = yf.Ticker(t).fast_info.get("lastPrice")
            if p:
                prices[t] = float(p)
        except Exception:
            pass
    return prices


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


def sheet_id_from_url(url):
    """Extract Google Sheet ID from any share/edit URL."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError("Could not find a Google Sheet ID in that URL.")
    return m.group(1)

def gid_from_url(url):
    """Extract the sheet tab gid from the URL fragment, if present."""
    m = re.search(r"gid=(\d+)", url)
    return m.group(1) if m else "0"

def load_sheet(url):
    sheet_id = sheet_id_from_url(url)
    gid = gid_from_url(url)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    resp = requests.get(csv_url, timeout=15)
    if resp.status_code == 403:
        raise ValueError(
            "Access denied. Please share the sheet: click Share → "
            "change to 'Anyone with the link' → Viewer."
        )
    if resp.status_code != 200:
        raise ValueError(f"Could not fetch sheet (HTTP {resp.status_code}).")
    from io import StringIO
    df = pd.read_csv(StringIO(resp.text))
    return normalize_df(df)


@app.route("/api/load-file", methods=["POST"])
def load_file():
    """Load from an uploaded .xlsx file (for local use)."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided."}), 400
    try:
        df = pd.read_excel(f)
        df = normalize_df(df)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    sid = str(uuid.uuid4())
    _sessions[sid] = df
    return jsonify({"session_id": sid, "rows": len(df), "tickers": df["ticker"].nunique()})


@app.route("/api/portfolios")
def list_portfolios():
    return jsonify({"portfolios": list(PORTFOLIOS.keys())})


@app.route("/api/load", methods=["POST"])
def load():
    data = request.get_json(silent=True) or {}

    # Load by name (dropdown selection)
    name = (data.get("name") or "").strip()
    if name:
        if name not in PORTFOLIOS:
            return jsonify({"error": f"Unknown portfolio: {name}"}), 400
        url = PORTFOLIOS[name]
    else:
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "No portfolio selected."}), 400

    try:
        df = load_sheet(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    sid = str(uuid.uuid4())
    _sessions[sid] = df
    return jsonify({"session_id": sid, "rows": len(df), "tickers": df["ticker"].nunique()})


@app.route("/api/summary")
def api_summary():
    sid = request.args.get("session_id")
    if not sid or sid not in _sessions:
        return jsonify({"error": "Session not found. Please upload your portfolio."}), 404

    df = _sessions[sid]
    positions = compute_positions(df)
    holdings = [p for p in positions if p["quantity"] > 0.5]

    prices = fetch_prices([h["ticker"] for h in holdings])

    for h in holdings:
        cp = prices.get(h["ticker"])
        h["current_price"] = cp
        h["average_cost"] = h["cost_basis"] / h["quantity"] if h["quantity"] else None
        h["current_value"] = cp * h["quantity"] if cp else None
        h["unrealized_pl"] = (h["current_value"] - h["cost_basis"]) if h["current_value"] is not None else None
        h["unrealized_pl_pct"] = (
            h["unrealized_pl"] / h["cost_basis"] * 100
            if h["unrealized_pl"] is not None and h["cost_basis"] else None
        )

    priced = [h for h in holdings if h["current_value"] is not None]
    total_cost = sum(h["cost_basis"] for h in holdings)
    priced_cost = sum(h["cost_basis"] for h in priced)
    total_value = sum(h["current_value"] for h in priced)
    total_unrealized = total_value - priced_cost
    total_realized = sum(p["realized_pl"] for p in positions)
    total_return_pct = (total_unrealized + total_realized) / total_cost * 100 if total_cost else None

    # TASI
    earliest = df["date"].min()
    try:
        tasi_tk = yf.Ticker(TASI_TICKER)
        tasi_current = float(tasi_tk.fast_info.get("lastPrice"))
        hist = tasi_tk.history(
            start=earliest.strftime("%Y-%m-%d"),
            end=(earliest + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
        tasi_baseline = float(hist["Close"].iloc[0]) if not hist.empty else None
        tasi_pct = (tasi_current - tasi_baseline) / tasi_baseline * 100 if tasi_baseline else None
    except Exception:
        tasi_current = tasi_baseline = tasi_pct = None

    vs_tasi = (total_return_pct - tasi_pct) if (total_return_pct is not None and tasi_pct is not None) else None

    # Chart data
    pie = sorted(
        [{"label": h["company"], "ticker": h["ticker"], "value": round(h["current_value"], 2)}
         for h in priced],
        key=lambda x: -x["value"],
    )
    pl_bars = sorted(
        [{"ticker": h["ticker"], "company": h["company"],
          "unrealized_pl": round(h["unrealized_pl"], 2),
          "pct": round(h["unrealized_pl_pct"], 2)}
         for h in holdings if h["unrealized_pl"] is not None],
        key=lambda x: x["unrealized_pl"],
    )

    return jsonify({
        "holdings": holdings,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_unrealized_pl": round(total_unrealized, 2),
            "total_unrealized_pl_pct": round(total_unrealized / priced_cost * 100, 2) if priced_cost else None,
            "total_realized_pl": round(total_realized, 2),
            "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
        },
        "tasi": {
            "current": tasi_current,
            "pct_change": round(tasi_pct, 2) if tasi_pct is not None else None,
            "baseline_date": earliest.strftime("%Y-%m-%d"),
            "vs_portfolio": round(vs_tasi, 2) if vs_tasi is not None else None,
        },
        "charts": {"pie": pie, "pl_bars": pl_bars},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def _build_daily_portfolio(df):
    """Return (port_value, port_cost, price_data) as daily Series."""
    positions = compute_positions(df)
    all_tickers = [p["ticker"] for p in positions]
    start_date = df["date"].min().strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    symbols = all_tickers + [TASI_TICKER]
    raw = yf.download(symbols, start=start_date, end=today, progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        price_data = raw["Close"]
    else:
        price_data = raw[["Close"]].rename(columns={"Close": symbols[0]})

    txn = df.copy()
    txn["date"] = txn["date"].dt.normalize()
    daily_qty = (
        txn.groupby(["date", "ticker"])["quantity"]
        .sum().unstack(fill_value=0)
        .reindex(price_data.index, fill_value=0).cumsum()
    )
    daily_cost = (
        txn.groupby(["date", "ticker"])["cost"]
        .sum().unstack(fill_value=0)
        .reindex(price_data.index, fill_value=0).cumsum()
    )

    port_value = pd.Series(0.0, index=price_data.index)
    for ticker in all_tickers:
        if ticker in price_data.columns and ticker in daily_qty.columns:
            port_value += daily_qty[ticker].ffill().fillna(0) * price_data[ticker].ffill()

    port_cost = daily_cost.sum(axis=1)
    first_inv = txn["date"].min()
    return port_value, port_cost, price_data, first_inv


@app.route("/api/history")
def api_history():
    sid = request.args.get("session_id")
    if not sid or sid not in _sessions:
        return jsonify({"error": "Session not found."}), 404
    try:
        port_value, port_cost, price_data, first_inv = _build_daily_portfolio(_sessions[sid])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    valid_idx = price_data.index[price_data.index >= first_inv]
    if valid_idx.empty:
        return jsonify({"error": "No price data found for portfolio period."}), 500

    # Apply optional date range filter
    date_from = request.args.get("from")
    date_to   = request.args.get("to")
    anchor = valid_idx[0]
    if date_from:
        try:
            ts_from = pd.Timestamp(date_from)
            # Never go before the first investment
            anchor = max(anchor, ts_from)
        except Exception:
            pass

    end_ts = price_data.index[-1]
    if date_to:
        try:
            end_ts = min(end_ts, pd.Timestamp(date_to))
        except Exception:
            pass

    pv = port_value.loc[anchor:end_ts].resample("W").last().dropna()
    pc = port_cost.loc[anchor:end_ts].resample("W").last().reindex(pv.index).ffill().fillna(0)

    if pv.empty:
        return jsonify({"error": "No data in selected date range."}), 400

    # Use P&L change (value - cost) to avoid spikes from new purchases
    pl = pv - pc
    pl_anchor = float(pl.iloc[0])
    pv_anchor = float(pv.iloc[0]) if pv.iloc[0] != 0 else 1
    port_return = ((pl - pl_anchor) / pv_anchor * 100).round(2)

    tasi_return = None
    if TASI_TICKER in price_data.columns:
        tasi_s = price_data[TASI_TICKER].loc[anchor:end_ts].resample("W").last().reindex(pv.index).ffill()
        t_anchor = tasi_s.iloc[0] if tasi_s.iloc[0] != 0 else 1
        tasi_return = ((tasi_s - t_anchor) / t_anchor * 100).round(2).tolist()

    pl_sar = (pl - pl_anchor).round(2)

    return jsonify({
        "dates": [d.strftime("%Y-%m-%d") for d in pv.index],
        "portfolio_return": port_return.tolist(),
        "portfolio_pl_sar": pl_sar.tolist(),
        "cost_invested": pc.round(2).tolist(),
        "portfolio_value": pv.round(2).tolist(),
        "tasi_return": tasi_return,
    })


@app.route("/api/performance")
def api_performance():
    sid = request.args.get("session_id")
    if not sid or sid not in _sessions:
        return jsonify({"error": "Session not found."}), 404

    freq   = request.args.get("freq", "M")   # "W" or "M"
    date_from = request.args.get("from")
    date_to   = request.args.get("to")

    if freq not in ("W", "ME"):
        freq = "ME"

    try:
        port_value, _, _, first_inv = _build_daily_portfolio(_sessions[sid])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Apply date range filter
    pv = port_value.copy()
    if date_from:
        pv = pv[pv.index >= pd.Timestamp(date_from)]
    if date_to:
        pv = pv[pv.index <= pd.Timestamp(date_to)]
    if pv.empty:
        return jsonify({"error": "No data in selected date range."}), 400

    # Resample to period end
    pv_period = pv.resample(freq).last().dropna()

    rows = []
    for i, (date, end_val) in enumerate(pv_period.items()):
        start_val = float(pv_period.iloc[i - 1]) if i > 0 else float(pv[pv.index < date].iloc[-1]) if not pv[pv.index < date].empty else end_val
        pl = end_val - start_val
        pl_pct = (pl / start_val * 100) if start_val else 0

        if freq == "W":
            label = date.strftime("Week of %b %d, %Y")
        else:
            label = date.strftime("%B %Y")

        rows.append({
            "period": label,
            "end_date": date.strftime("%Y-%m-%d"),
            "start_value": round(start_val, 2),
            "end_value": round(float(end_val), 2),
            "pl": round(pl, 2),
            "pl_pct": round(pl_pct, 2),
        })

    return jsonify({"rows": rows})


if __name__ == "__main__":
    app.run(debug=True, port=5050)
