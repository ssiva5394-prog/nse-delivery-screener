import io
import os
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests


# ============================================================
# SETTINGS
# ============================================================

LOOKBACK_DAYS = 30
PREVIOUS_DAY_MULTIPLIER = 2

NSE_HOME = "https://www.nseindia.com"
NSE_FNO_PAGE = (
    "https://www.nseindia.com/"
    "static/products-services/equity-derivatives-list-underlyings-information"
)

DATA_FILE = "daily_data.csv"
SIGNALS_FILE = "signals.csv"


# ============================================================
# NSE SESSION
# ============================================================

def create_nse_session():

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": NSE_HOME + "/",
    })

    print("NSE session prepared.")


    return session


# ============================================================
# F&O UNIVERSE
# ============================================================

def get_fno_symbols(session):

    print("Getting current NSE F&O stock universe...")

    response = session.get(
        NSE_FNO_PAGE,
        timeout=20
    )

    response.raise_for_status()

    html = response.text

    symbols = set()

    # First try the HTML tables.
    try:

        tables = pd.read_html(io.StringIO(html))

        for table in tables:

            columns = [
                str(c).strip().upper()
                for c in table.columns
            ]

            symbol_col = None

            for col in table.columns:
                if str(col).strip().upper() == "SYMBOL":
                    symbol_col = col
                    break

            if symbol_col is not None:

                for value in table[symbol_col].dropna():

                    symbol = str(value).strip()

                    if (
                        symbol
                        and symbol.upper() != "SYMBOL"
                        and symbol.upper() not in {
                            "NIFTY",
                            "BANKNIFTY",
                            "FINNIFTY",
                            "MIDCPNIFTY",
                            "NIFTYNXT50",
                        }
                    ):
                        symbols.add(symbol)

    except Exception as exc:
        print("HTML table parsing failed:", exc)


    # Fallback: extract symbol links from the HTML.
    if len(symbols) < 100:

        matches = re.findall(
            r'/get-quotes/equity\?symbol=([^"&]+)',
            html,
            flags=re.IGNORECASE
        )

        for symbol in matches:
            symbols.add(symbol.strip())


    if len(symbols) < 100:

        raise RuntimeError(
            "Could not obtain the NSE F&O stock universe."
        )


    print(
        "F&O symbols found:",
        len(symbols)
    )

    return symbols


# ============================================================
# DOWNLOAD FULL BHAVCOPY
# ============================================================

def download_bhavcopy(session, trading_date):

    dd = trading_date.strftime("%d")
    mm = trading_date.strftime("%m")
    yyyy = trading_date.strftime("%Y")

    url = (
        "https://nsearchives.nseindia.com/"
        "products/content/"
        f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
    )

    print(
        "Downloading:",
        trading_date.strftime("%d-%m-%Y")
    )

    last_error = None

    for attempt in range(1, 4):

        try:

            response = session.get(
                url,
                timeout=30
            )

            if response.status_code == 200:

                text = response.text

                if len(text) > 500:

                    print(
                        "Downloaded:",
                        len(text),
                        "characters"
                    )

                    return text

            last_error = (
                f"HTTP {response.status_code}"
            )

        except Exception as exc:

            last_error = str(exc)

        print(
            f"Attempt {attempt}/3 failed:",
            last_error
        )

        time.sleep(2 * attempt)


    raise RuntimeError(
        f"Could not download NSE bhavcopy for "
        f"{trading_date}: {last_error}"
    )


# ============================================================
# PARSE BHAVCOPY
# ============================================================

def parse_bhavcopy(text, trading_date, fno_symbols):

    df = pd.read_csv(
        io.StringIO(text),
        skipinitialspace=True
    )

    # Clean column names.
    df.columns = [
        str(c).strip().upper()
        for c in df.columns
    ]

    required = {
        "SYMBOL",
        "SERIES",
        "PREV_CLOSE",
        "CLOSE_PRICE",
        "TTL_TRD_QNTY",
        "DELIV_QTY",
        "DELIV_PER",
    }

    missing = required - set(df.columns)

    if missing:

        raise RuntimeError(
            "Missing NSE columns: " +
            ", ".join(sorted(missing))
        )


    # Only normal equity series.
    df["SERIES"] = (
        df["SERIES"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df = df[
        df["SERIES"] == "EQ"
    ].copy()


    # Keep only F&O stocks.
    df["SYMBOL"] = (
        df["SYMBOL"]
        .astype(str)
        .str.strip()
    )

    df = df[
        df["SYMBOL"].isin(fno_symbols)
    ].copy()


    # Numeric columns.
    numeric_columns = [
        "PREV_CLOSE",
        "CLOSE_PRICE",
        "TTL_TRD_QNTY",
        "DELIV_QTY",
        "DELIV_PER",
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        ).fillna(0)


    df["DATE"] = trading_date.isoformat()


    result = df[
        [
            "DATE",
            "SYMBOL",
            "SERIES",
            "CLOSE_PRICE",
            "PREV_CLOSE",
            "TTL_TRD_QNTY",
            "DELIV_QTY",
            "DELIV_PER",
        ]
    ].copy()


    result.columns = [
        "Date",
        "Symbol",
        "Series",
        "Close",
        "Prev_Close",
        "Traded_Qty",
        "Delivery_Qty",
        "Delivery_Percent",
    ]


    return result


# ============================================================
# FIND LATEST TRADING DAY
# ============================================================

def find_latest_trading_day(session, fno_symbols):

    today = date.today()

    for days_back in range(0, 10):

        candidate = (
            today -
            timedelta(days=days_back)
        )

        if candidate.weekday() >= 5:
            continue

        try:

            text = download_bhavcopy(
                session,
                candidate
            )

            data = parse_bhavcopy(
                text,
                candidate,
                fno_symbols
            )

            if len(data) > 0:

                print(
                    "Latest trading day:",
                    candidate
                )

                return candidate, data

        except Exception as exc:

            print(
                "Not available:",
                candidate,
                exc
            )

    raise RuntimeError(
        "Could not find a recent NSE trading day."
    )


# ============================================================
# GET WEEKDAYS
# ============================================================

def weekdays_between(start_date, end_date):

    dates = []

    current = start_date

    while current <= end_date:

        if current.weekday() < 5:
            dates.append(current)

        current += timedelta(days=1)

    return dates


# ============================================================
# LOAD EXISTING HISTORY
# ============================================================

def load_history():

    if not os.path.exists(DATA_FILE):
        return pd.DataFrame()

    try:

        df = pd.read_csv(
            DATA_FILE
        )

        if df.empty:
            return pd.DataFrame()

        df["Date"] = pd.to_datetime(
            df["Date"]
        ).dt.date

        return df

    except Exception as exc:

        print(
            "Could not load previous history:",
            exc
        )

        return pd.DataFrame()


# ============================================================
# UPDATE HISTORY
# ============================================================

def update_history(
    session,
    fno_symbols,
    latest_date,
    latest_data
):

    history = load_history()

    if history.empty:

        start_date = (
            latest_date -
            timedelta(days=LOOKBACK_DAYS + 7)
        )

        dates_needed = weekdays_between(
            start_date,
            latest_date
        )

        # Latest day already downloaded.
        dates_needed = [
            d for d in dates_needed
            if d != latest_date
        ]

        pieces = []

        for d in dates_needed:

            try:

                text = download_bhavcopy(
                    session,
                    d
                )

                data = parse_bhavcopy(
                    text,
                    d,
                    fno_symbols
                )

                if not data.empty:
                    pieces.append(data)

            except Exception as exc:

                print(
                    "Skipping:",
                    d,
                    exc
                )

        if pieces:
            history = pd.concat(
                pieces,
                ignore_index=True
            )

        else:
            history = pd.DataFrame()


    else:

        history["Date"] = pd.to_datetime(
            history["Date"]
        ).dt.date

        existing_dates = set(
            history["Date"]
        )

        start_date = (
            latest_date -
            timedelta(days=LOOKBACK_DAYS + 7)
        )

        dates_needed = weekdays_between(
            start_date,
            latest_date
        )

        dates_needed = [
            d for d in dates_needed
            if (
                d not in existing_dates
                and d != latest_date
            )
        ]

        pieces = []

        for d in dates_needed:

            try:

                text = download_bhavcopy(
                    session,
                    d
                )

                data = parse_bhavcopy(
                    text,
                    d,
                    fno_symbols
                )

                if not data.empty:
                    pieces.append(data)

            except Exception as exc:

                print(
                    "Skipping:",
                    d,
                    exc
                )

        if pieces:

            history = pd.concat(
                [history] + pieces,
                ignore_index=True
            )


    # Always replace today's data.
    history = history[
        history["Date"] != latest_date
    ].copy()

    history = pd.concat(
        [history, latest_data],
        ignore_index=True
    )


    # Keep only rolling history.
    cutoff = (
        latest_date -
        timedelta(days=LOOKBACK_DAYS + 2)
    )

    history = history[
        history["Date"] >= cutoff
    ].copy()


    # Remove duplicate Date + Symbol.
    history = history.drop_duplicates(
        subset=["Date", "Symbol"],
        keep="last"
    )


    history = history.sort_values(
        ["Date", "Symbol"]
    )


    history.to_csv(
        DATA_FILE,
        index=False
    )


    return history


# ============================================================
# BUILD SIGNALS
# ============================================================

def build_signals(
    history,
    latest_date
):

    if history.empty:
        return pd.DataFrame()


    history = history.copy()

    history["Date"] = pd.to_datetime(
        history["Date"]
    ).dt.date


    today = history[
        history["Date"] == latest_date
    ].copy()


    if today.empty:
        return pd.DataFrame()


    # Previous trading date.
    previous_dates = sorted(
        d for d in history["Date"].unique()
        if d < latest_date
    )


    if not previous_dates:
        return pd.DataFrame()


    previous_date = previous_dates[-1]


    previous = history[
        history["Date"] == previous_date
    ].copy()


    previous = previous[
        [
            "Symbol",
            "Delivery_Qty"
        ]
    ].rename(
        columns={
            "Delivery_Qty":
            "Previous_Day_Delivery"
        }
    )


    today = today.merge(
        previous,
        on="Symbol",
        how="inner"
    )


    # Previous 30-day history, excluding today.
    cutoff = (
        latest_date -
        timedelta(days=LOOKBACK_DAYS)
    )


    prior_history = history[
        (history["Date"] < latest_date) &
        (history["Date"] >= cutoff)
    ].copy()


    max_delivery = (
        prior_history
        .groupby("Symbol")["Delivery_Qty"]
        .max()
        .rename("Previous_30_Day_Max")
        .reset_index()
    )


    today = today.merge(
        max_delivery,
        on="Symbol",
        how="left"
    )


    today["Previous_30_Day_Max"] = (
        today["Previous_30_Day_Max"]
        .fillna(0)
    )


    today["Previous_Day_Delivery"] = (
        today["Previous_Day_Delivery"]
        .fillna(0)
    )


    # --------------------------------------------------------
    # CONDITION 1
    # Today's delivery > previous 30-day maximum
    # --------------------------------------------------------

    condition_1 = (
        today["Delivery_Qty"] >
        today["Previous_30_Day_Max"]
    )


    # --------------------------------------------------------
    # CONDITION 2
    # Today's delivery > 2 x previous day
    # --------------------------------------------------------

    condition_2 = (
        today["Delivery_Qty"] >
        PREVIOUS_DAY_MULTIPLIER *
        today["Previous_Day_Delivery"]
    )


    signals = today[
        condition_1 & condition_2
    ].copy()


    if signals.empty:
        return pd.DataFrame()


    signals["Delivery_Multiple"] = (
        signals["Delivery_Qty"] /
        signals["Previous_Day_Delivery"]
    )


    signals["Price_Change_Percent"] = (
        (
            signals["Close"] -
            signals["Prev_Close"]
        )
        /
        signals["Prev_Close"].replace(
            0,
            pd.NA
        )
    ) * 100


    signals["Date"] = (
        signals["Date"]
        .astype(str)
    )


    signals = signals[
        [
            "Date",
            "Symbol",
            "Close",
            "Price_Change_Percent",
            "Delivery_Qty",
            "Previous_Day_Delivery",
            "Delivery_Multiple",
            "Previous_30_Day_Max",
            "Delivery_Percent",
        ]
    ]


    signals = signals.sort_values(
        "Delivery_Multiple",
        ascending=False
    )


    return signals


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("NSE DELIVERY SCREENER")
    print("=" * 60)

    session = create_nse_session()

    fno_symbols = get_fno_symbols(
        session
    )


    latest_date, latest_data = (
        find_latest_trading_day(
            session,
            fno_symbols
        )
    )


    history = update_history(
        session,
        fno_symbols,
        latest_date,
        latest_data
    )


    signals = build_signals(
        history,
        latest_date
    )


    if signals.empty:

        print(
            "No stocks matched both conditions."
        )

        # Create an empty file with headers.
        signals = pd.DataFrame(
            columns=[
                "Date",
                "Symbol",
                "Close",
                "Price_Change_Percent",
                "Delivery_Qty",
                "Previous_Day_Delivery",
                "Delivery_Multiple",
                "Previous_30_Day_Max",
                "Delivery_Percent",
            ]
        )


    signals.to_csv(
        SIGNALS_FILE,
        index=False
    )


    print()
    print(
        "Latest trading day:",
        latest_date
    )

    print(
        "History rows:",
        len(history)
    )

    print(
        "Signals:",
        len(signals)
    )

    print()
    print("Files created:")
    print(" -", DATA_FILE)
    print(" -", SIGNALS_FILE)

    print("=" * 60)


if __name__ == "__main__":
    main()
