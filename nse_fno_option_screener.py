"""
NSE Stock Options OI Screener
----------------------------
Uses NSE's UDiFF F&O end-of-day bhavcopy.

For each stock option contract (Symbol + Expiry + Strike + CE/PE):
  A) Change in OI today > preceding one-calendar-month maximum Change in OI
  B) Change in OI today > 2 x previous available trading day's Change in OI
  C) Total OI today > preceding one-calendar-month maximum Total OI
  D) Total OI today > 2 x previous available trading day's Total OI

A combined signal is emitted only when all four conditions pass.
The original delivery screener files are not read or modified.
"""

import io
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
import requests

from nse_delivery_screener_5y import fno_symbols, session

HISTORY_FILE = "fno_option_history.csv"
DAILY_FILE = "fno_option_daily.csv"
SIGNALS_FILE = "fno_option_signals.csv"
MULTIPLIER = 2
LOOKBACK_CALENDAR_DAYS = 40

NSE_ARCHIVE = "https://nsearchives.nseindia.com/content/fo/"


def download_bhavcopy(sess, trading_date):
    stamp = trading_date.strftime("%Y%m%d")
    filename = f"BhavCopy_NSE_FO_0_0_0_{stamp}_F_0000.csv.zip"
    url = urljoin(NSE_ARCHIVE, filename)

    last_error = None
    for attempt in range(1, 4):
        try:
            response = sess.get(url, timeout=60)
            if response.status_code == 200 and len(response.content) > 1000:
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                    if not csv_names:
                        raise RuntimeError("ZIP archive contains no CSV file")
                    with archive.open(csv_names[0]) as handle:
                        return pd.read_csv(handle, low_memory=False)
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < 3:
            time.sleep(attempt * 2)
    raise RuntimeError(f"{trading_date}: NSE F&O bhavcopy download failed: {last_error}")


def _col(df, *names, required=True):
    lookup = {str(c).strip().upper(): c for c in df.columns}
    for name in names:
        if name.upper() in lookup:
            return lookup[name.upper()]
    if required:
        raise RuntimeError("Required UDiFF column missing. Tried: " + ", ".join(names))
    return None


def parse_bhavcopy(raw, trading_date, stock_symbols):
    if raw.empty:
        return pd.DataFrame()

    # UDiFF ISO column names; aliases keep the parser tolerant of minor naming changes.
    c_date = _col(raw, "BIZDT", "TRADDT", required=False)
    c_segment = _col(raw, "SGMT", required=False)
    c_inst = _col(raw, "FININSTRMTP", "INSTRUMENT", required=False)
    c_symbol = _col(raw, "TCKRSYMB", "SYMBOL")
    c_expiry = _col(raw, "XPRYDT", "EXPIRY_DT")
    c_strike = _col(raw, "STRKPRIC", "STRIKE_PR")
    c_type = _col(raw, "OPTNTP", "OPTION_TYP")
    c_open = _col(raw, "OPNPRIC", "OPEN")
    c_high = _col(raw, "HG HPRIC".replace(" ", ""), "HIGH")
    c_low = _col(raw, "LWPRIC", "LOW")
    c_close = _col(raw, "CLSPRIC", "CLOSE")
    c_ltp = _col(raw, "LASTPRIC", "LTP", required=False)
    c_prev = _col(raw, "PRVSCLSGPRIC", "PRVSCLSGPRC", "PREV_CLOSE", required=False)
    c_oi = _col(raw, "OPNINTRST", "OPEN_INT")
    c_chg_oi = _col(raw, "CHNGINOPNINTRST", "CHG_IN_OI")
    c_volume = _col(raw, "TTLTRADGVOL", "CONTRACTS", required=False)
    c_underlying = _col(raw, "UNDRLYGPRIC", "UNDERLYING_VALUE", required=False)

    df = raw.copy()
    print("UDiFF columns:", list(raw.columns))
    if c_segment:
        print("UDiFF segment values:", df[c_segment].astype(str).str.strip().value_counts().head(10).to_dict())
    if c_inst:
        print("UDiFF instrument type values:", df[c_inst].astype(str).str.strip().value_counts().head(20).to_dict())
    print("UDiFF option type values:", df[c_type].astype(str).str.strip().value_counts().head(10).to_dict())
    print("UDiFF sample symbols:", df[c_symbol].astype(str).str.strip().drop_duplicates().head(20).tolist())
    print("F&O stock symbol sample:", sorted({str(x).strip().upper() for x in stock_symbols})[:20])
    if c_segment:
        df = df[df[c_segment].astype(str).str.strip().str.upper().isin(["FO", "D"])]
        print("Rows after segment filter:", len(df))
    if c_inst:
        inst = df[c_inst].astype(str).str.strip().str.upper()
        df = df[inst.isin(["OPTSTK", "OPTSTK "])]
        print("Rows after stock-option instrument filter:", len(df))

    df["_SYMBOL"] = df[c_symbol].astype(str).str.strip().str.upper()
    allowed = {str(x).strip().upper() for x in stock_symbols}
    print("Rows before stock-symbol filter:", len(df))
    df = df[df["_SYMBOL"].isin(allowed)].copy()
    print("Rows after stock-symbol filter:", len(df))
    df["_OPTION_TYPE"] = df[c_type].astype(str).str.strip().str.upper()
    df = df[df["_OPTION_TYPE"].isin(["CE", "PE"])].copy()
    print("Rows after CE/PE filter:", len(df))
    if df.empty:
        return pd.DataFrame()

    def numeric(column, default=0):
        if column is None:
            return pd.Series(default, index=df.index, dtype="float64")
        return pd.to_numeric(df[column], errors="coerce").fillna(default)

    out = pd.DataFrame(index=df.index)
    out["Date"] = pd.to_datetime(
        df[c_date], errors="coerce"
    ).dt.date if c_date else trading_date
    out["Date"] = out["Date"].fillna(trading_date)
    out["Symbol"] = df["_SYMBOL"]
    out["Expiry"] = pd.to_datetime(df[c_expiry], errors="coerce").dt.date
    out["Strike"] = numeric(c_strike)
    out["Option_Type"] = df["_OPTION_TYPE"]
    out["Open"] = numeric(c_open)
    out["High"] = numeric(c_high)
    out["Low"] = numeric(c_low)
    out["Close"] = numeric(c_close)
    out["LTP"] = numeric(c_ltp) if c_ltp else out["Close"]
    out["Prev_Close"] = numeric(c_prev) if c_prev else pd.NA
    out["Total_OI"] = numeric(c_oi)
    out["Change_in_OI"] = numeric(c_chg_oi)
    out["Volume_Contracts"] = numeric(c_volume) if c_volume else 0
    out["Underlying_Value"] = numeric(c_underlying) if c_underlying else pd.NA

    out = out.dropna(subset=["Expiry", "Strike", "Date"])
    out = out[(out["Strike"] > 0) & (out["Total_OI"] >= 0)]
    out = out.drop_duplicates(
        ["Date", "Symbol", "Expiry", "Strike", "Option_Type"], keep="last"
    )
    return out.reset_index(drop=True)


CONTRACT_KEYS = ["Symbol", "Expiry", "Strike", "Option_Type"]


def build_signals(history):
    signal_columns = [
        "Date", *CONTRACT_KEYS, "Open", "High", "Low", "Close", "LTP",
        "Prev_Close", "Underlying_Value", "Volume_Contracts",
        "Change_in_OI", "Previous_Day_Change_in_OI",
        "Previous_Month_Max_Change_in_OI", "Change_OI_Month_Max_Date",
        "Change_OI_Month_Max_Pass", "Change_OI_2x_Previous_Day_Pass",
        "Total_OI", "Previous_Day_Total_OI", "Previous_Month_Max_Total_OI",
        "Total_OI_Month_Max_Date", "Total_OI_Month_Max_Pass",
        "Total_OI_2x_Previous_Day_Pass", "Combined_Signal",
        "Price_Change_Percent", "Candle_Direction"
    ]
    if history.empty:
        return pd.DataFrame(columns=signal_columns)

    df = history.copy()
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    df["Expiry"] = pd.to_datetime(df["Expiry"]).dt.date
    df = df.sort_values(CONTRACT_KEYS + ["Date"])
    results = []

    for _, group in df.groupby(CONTRACT_KEYS, dropna=False, sort=False):
        g = group.sort_values("Date").copy().reset_index(drop=True)
        dates = g["Date"].tolist()
        chg_values = pd.to_numeric(g["Change_in_OI"], errors="coerce")
        oi_values = pd.to_numeric(g["Total_OI"], errors="coerce")

        g["Previous_Day_Change_in_OI"] = chg_values.shift(1)
        g["Previous_Day_Total_OI"] = oi_values.shift(1)

        chg_max, chg_max_date, oi_max, oi_max_date = [], [], [], []
        for i, current_date in enumerate(dates):
            cutoff = (pd.Timestamp(current_date) - pd.DateOffset(months=1)).date()
            prior_positions = [j for j in range(i) if dates[j] >= cutoff]
            if not prior_positions:
                chg_max.append(pd.NA)
                chg_max_date.append(pd.NA)
                oi_max.append(pd.NA)
                oi_max_date.append(pd.NA)
                continue

            chg_max_value = max(chg_values.iloc[j] for j in prior_positions)
            oi_max_value = max(oi_values.iloc[j] for j in prior_positions)
            chg_max.append(chg_max_value)
            oi_max.append(oi_max_value)
            chg_max_date.append(max(
                dates[j] for j in prior_positions
                if chg_values.iloc[j] == chg_max_value
            ))
            oi_max_date.append(max(
                dates[j] for j in prior_positions
                if oi_values.iloc[j] == oi_max_value
            ))

        g["Previous_Month_Max_Change_in_OI"] = chg_max
        g["Change_OI_Month_Max_Date"] = chg_max_date
        g["Previous_Month_Max_Total_OI"] = oi_max
        g["Total_OI_Month_Max_Date"] = oi_max_date

        # Use numeric NaN and fill missing comparisons with False. This avoids
        # pandas.NA truth-value errors for contracts without enough history.
        g["Change_OI_Month_Max_Pass"] = (
            pd.to_numeric(g["Change_in_OI"], errors="coerce")
            .gt(pd.to_numeric(g["Previous_Month_Max_Change_in_OI"], errors="coerce"))
            .fillna(False)
        )
        g["Change_OI_2x_Previous_Day_Pass"] = (
            pd.to_numeric(g["Change_in_OI"], errors="coerce")
            .gt(MULTIPLIER * pd.to_numeric(g["Previous_Day_Change_in_OI"], errors="coerce"))
            .fillna(False)
        )
        g["Total_OI_Month_Max_Pass"] = (
            pd.to_numeric(g["Total_OI"], errors="coerce")
            .gt(pd.to_numeric(g["Previous_Month_Max_Total_OI"], errors="coerce"))
            .fillna(False)
        )
        g["Total_OI_2x_Previous_Day_Pass"] = (
            pd.to_numeric(g["Total_OI"], errors="coerce")
            .gt(MULTIPLIER * pd.to_numeric(g["Previous_Day_Total_OI"], errors="coerce"))
            .fillna(False)
        )

        g["Combined_Signal"] = (
            g["Change_OI_Month_Max_Pass"]
            & g["Change_OI_2x_Previous_Day_Pass"]
            & g["Total_OI_Month_Max_Pass"]
            & g["Total_OI_2x_Previous_Day_Pass"]
        )
        g["Price_Change_Percent"] = (
            (g["Close"] - g["Prev_Close"]) / g["Prev_Close"].replace(0, pd.NA)
        ) * 100
        g["Candle_Direction"] = "Neutral"
        g.loc[g["Close"] > g["Open"], "Candle_Direction"] = "Bullish"
        g.loc[g["Close"] < g["Open"], "Candle_Direction"] = "Bearish"

        selected = g[g["Combined_Signal"]].copy()
        if not selected.empty:
            results.append(selected)

    if not results:
        return pd.DataFrame(columns=signal_columns)
    out = pd.concat(results, ignore_index=True)
    return out.reindex(columns=signal_columns).sort_values(
        ["Date", "Symbol", "Expiry", "Strike", "Option_Type"]
    )


def _trading_dates(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _load_history():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    df = pd.read_csv(HISTORY_FILE)
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    df["Expiry"] = pd.to_datetime(df["Expiry"]).dt.date
    return df


def update_range(start, end):
    sess = session()
    # NSE archive requests require a warmed-up session.
    sess.get("https://www.nseindia.com", timeout=30)
    symbols = fno_symbols(sess)
    history = _load_history()
    existing_dates = set(history["Date"]) if not history.empty else set()
    chunks = []

    for index, day in enumerate(_trading_dates(start, end), start=1):
        if day in existing_dates:
            continue
        try:
            print(f"[{index}] Downloading F&O option bhavcopy for {day}")
            raw = download_bhavcopy(sess, day)
            parsed = parse_bhavcopy(raw, day, symbols)
            if not parsed.empty:
                chunks.append(parsed)
                print(f"  Parsed stock-option contracts: {len(parsed)}")
            else:
                print("  No eligible stock-option rows for this date.")
        except Exception as exc:
            print(f"  Skipping {day}: {exc}")
        time.sleep(0.5)

    if chunks:
        history = pd.concat([history] + chunks, ignore_index=True)
    if history.empty:
        raise RuntimeError("No stock-option history available after download.")

    history["Date"] = pd.to_datetime(history["Date"]).dt.date
    history["Expiry"] = pd.to_datetime(history["Expiry"]).dt.date
    history = history.drop_duplicates(
        ["Date", *CONTRACT_KEYS], keep="last"
    ).sort_values(["Date", *CONTRACT_KEYS])

    # Keep enough days to cover a full calendar month and holiday gaps.
    cutoff = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    history = history[history["Date"] >= cutoff].copy()
    history.to_csv(HISTORY_FILE, index=False)

    signals = build_signals(history)
    signals.to_csv(SIGNALS_FILE, index=False)
    latest_rows = history[history["Date"] == end]
    latest_rows.to_csv(DAILY_FILE, index=False)
    print(f"Saved {HISTORY_FILE}: {len(history)} rows")
    print(f"Saved {DAILY_FILE}: {len(latest_rows)} rows for {end}")
    print(f"Saved {SIGNALS_FILE}: {len(signals)} combined signals")


def latest_available_date(sess, end_date):
    for offset in range(0, 10):
        candidate = end_date - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        try:
            raw = download_bhavcopy(sess, candidate)
            return candidate, raw
        except Exception as exc:
            print(f"Not available {candidate}: {exc}")
    raise RuntimeError("Could not find a recent NSE F&O bhavcopy.")


def daily():
    sess = session()
    sess.get("https://www.nseindia.com", timeout=30)
    today, _ = latest_available_date(sess, date.today())
    start = today - timedelta(days=LOOKBACK_CALENDAR_DAYS + 7)
    update_range(start, today)


def backfill(start, end):
    update_range(start, end)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode == "backfill":
        if len(sys.argv) != 4:
            raise SystemExit("Usage: python nse_fno_option_screener.py backfill YYYY-MM-DD YYYY-MM-DD")
        start = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
        end = datetime.strptime(sys.argv[3], "%Y-%m-%d").date()
        backfill(start, end)
    elif mode == "daily":
        daily()
    else:
        raise SystemExit("Use daily or backfill YYYY-MM-DD YYYY-MM-DD")
