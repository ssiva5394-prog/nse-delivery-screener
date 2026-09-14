import io, os, re, sys, time
from datetime import date, datetime, timedelta
import pandas as pd
import requests

PREVIOUS_DAY_MULTIPLIER = 2
HISTORY_FILE = "five_year_history.csv"
SIGNALS_FILE = "signals_new.csv"
HISTORICAL_SIGNALS_FILE = "historical_signals_5y.csv"
DAILY_DATA_FILE = "daily_data_new.csv"

NSE_HOME = "https://www.nseindia.com"
NSE_FNO_PAGE = NSE_HOME + "/static/products-services/equity-derivatives-list-underlyings-information"

def session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_HOME + "/"
    })
    return s

def fno_symbols(s):
    r = s.get(NSE_FNO_PAGE, timeout=30); r.raise_for_status()
    symbols = set()
    try:
        for t in pd.read_html(io.StringIO(r.text)):
            col = next((c for c in t.columns if str(c).strip().upper()=="SYMBOL"), None)
            if col is not None:
                for x in t[col].dropna():
                    x = str(x).strip()
                    if x and x.upper() not in {"SYMBOL","NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTYNXT50"}:
                        symbols.add(x)
    except Exception as e:
        print("F&O table parse:", e)
    if len(symbols) < 100:
        symbols.update(x.strip() for x in re.findall(r'/get-quotes/equity\?symbol=([^"&]+)', r.text, re.I))
    if len(symbols) < 100:
        raise RuntimeError("Could not obtain NSE F&O stock universe")
    print("F&O symbols:", len(symbols))
    return symbols

def download(s, d):
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
    err = None
    for n in range(1,4):
        try:
            r=s.get(url,timeout=40)
            if r.status_code==200 and len(r.text)>500: return r.text
            err=f"HTTP {r.status_code}"
        except Exception as e: err=str(e)
        time.sleep(2*n)
    raise RuntimeError(f"{d}: {err}")

def parse(text,d,symbols):
    df=pd.read_csv(io.StringIO(text),skipinitialspace=True)
    df.columns=[str(c).strip().upper() for c in df.columns]
    need={"SYMBOL","SERIES","PREV_CLOSE","CLOSE_PRICE","TTL_TRD_QNTY","DELIV_QTY","DELIV_PER"}
    if need-set(df.columns): raise RuntimeError("Missing columns: "+",".join(sorted(need-set(df.columns))))
    df=df[df.SERIES.astype(str).str.strip().str.upper()=="EQ"].copy()
    df["SYMBOL"]=df.SYMBOL.astype(str).str.strip()
    df=df[df.SYMBOL.isin(symbols)].copy()
    for c in ["PREV_CLOSE","CLOSE_PRICE","TTL_TRD_QNTY","DELIV_QTY","DELIV_PER"]:
        df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0)
    out=df[["SYMBOL","SERIES","CLOSE_PRICE","PREV_CLOSE","TTL_TRD_QNTY","DELIV_QTY","DELIV_PER"]].copy()
    out.insert(0,"Date",d.isoformat())
    out.columns=["Date","Symbol","Series","Close","Prev_Close","Traded_Qty","Delivery_Qty","Delivery_Percent"]
    return out

def dates_between(a,b):
    out=[]; d=a
    while d<=b:
        if d.weekday()<5: out.append(d)
        d+=timedelta(days=1)
    return out

def month_back(d):
    return (pd.Timestamp(d)-pd.DateOffset(months=1)).date()

def load():
    if not os.path.exists(HISTORY_FILE): return pd.DataFrame()
    x=pd.read_csv(HISTORY_FILE)
    if x.empty: return x
    x["Date"]=pd.to_datetime(x["Date"]).dt.date
    return x

def build_signals(df):
    cols=["Date","Symbol","Close","Price_Change_Percent","Delivery_Qty","Previous_Day_Delivery","Delivery_Multiple","Previous_Calendar_Month_Max","Delivery_Percent"]
    if df.empty: return pd.DataFrame(columns=cols)
    df=df.copy(); df["Date"]=pd.to_datetime(df["Date"]).dt.date
    results=[]
    for symbol,g in df.groupby("Symbol",sort=False):
        g=g.sort_values("Date").copy()
        g["Previous_Day_Delivery"]=g["Delivery_Qty"].shift(1)
        ds=g["Date"].tolist(); vals=g["Delivery_Qty"].tolist()
         maxes = []
        has_month_history = []
        left = 0

        for i, d in enumerate(ds):s
            cutoff = month_back(d)

            while left < i and ds[left] < cutoff:
                left += 1

        # Require actual history in the preceding calendar month.
        has_history = left < i
        has_month_history.append(has_history)

        if has_history:
            maxes.append(max(vals[left:i]))
        else:
            maxes.append(pd.NA)

    g["Previous_Calendar_Month_Max"] = maxes
    g["Has_Calendar_Month_History"] = has_month_history

    # Rule 1: today's delivery must exceed the
    # previous calendar month's maximum.
    c1 = (
        g["Has_Calendar_Month_History"]
        & g["Previous_Calendar_Month_Max"].notna()
        & (
            g["Delivery_Qty"]
            > g["Previous_Calendar_Month_Max"]
        )
    )

    # Rule 2: today's delivery must exceed
    # 2 x previous trading day's delivery.
    c2 = (
        g["Previous_Day_Delivery"].notna()
        & (
            g["Delivery_Qty"]
            > PREVIOUS_DAY_MULTIPLIER
            * g["Previous_Day_Delivery"]
        )
    )

    # Both rules must pass.
    z = g[c1 & c2].copy()
        if z.empty: continue
        z["Delivery_Multiple"]=z["Delivery_Qty"]/z["Previous_Day_Delivery"]
        z["Price_Change_Percent"]=((z["Close"]-z["Prev_Close"])/z["Prev_Close"].replace(0,pd.NA))*100
        results.append(z[cols])
    if not results: return pd.DataFrame(columns=cols)
    return pd.concat(results,ignore_index=True).sort_values(["Date","Delivery_Multiple"],ascending=[True,False])

def save_all(df):
    sig=build_signals(df)
    sig.to_csv(HISTORICAL_SIGNALS_FILE,index=False)
    return sig

def backfill(start,end):
    s=session(); symbols=fno_symbols(s); h=load()
    existing=set(h["Date"]) if not h.empty else set()
    pieces=[]
    ds=dates_between(start,end)
    for i,d in enumerate(ds,1):
        if d in existing: continue
        try:
            print(f"[{i}/{len(ds)}] {d}")
            x=parse(download(s,d),d,symbols)
            if not x.empty: pieces.append(x)
        except Exception as e: print("Skip:",e)
        time.sleep(.5)
    if pieces: h=pd.concat([h]+pieces,ignore_index=True)
    if h.empty: raise RuntimeError("No data downloaded")
    h["Date"]=pd.to_datetime(h["Date"]).dt.date
    h=h.drop_duplicates(["Date","Symbol"],keep="last").sort_values(["Date","Symbol"])
    h.to_csv(HISTORY_FILE,index=False)
    sig=save_all(h)
    print("Saved",HISTORY_FILE,len(h),"rows")
    print("Saved",HISTORICAL_SIGNALS_FILE,len(sig),"signals")

def latest(s,symbols):
    for n in range(10):
        d=date.today()-timedelta(days=n)
        if d.weekday()>=5: continue
        try:
            x=parse(download(s,d),d,symbols)
            if not x.empty:return d,x
        except Exception as e: print("Not available",d,e)
    raise RuntimeError("No recent trading day")

def daily():
    h=load()
    if h.empty: raise RuntimeError("Run the 5-year backfill first")
    s=session(); symbols=fno_symbols(s); d,today=latest(s,symbols)
    h=h[h["Date"]!=d].copy()
    h=pd.concat([h,today],ignore_index=True)
    h["Date"]=pd.to_datetime(h["Date"]).dt.date
    h=h.drop_duplicates(["Date","Symbol"],keep="last").sort_values(["Date","Symbol"])
    h.to_csv(HISTORY_FILE,index=False)
    allsig=save_all(h)
    latestsig=allsig[allsig["Date"]==str(d)]
    latestsig.to_csv(SIGNALS_FILE,index=False)
    today.to_csv(DAILY_DATA_FILE,index=False)
    print("Latest:",d,"today signals:",len(latestsig),"5y signals:",len(allsig))

if __name__=="__main__":
    mode=sys.argv[1] if len(sys.argv)>1 else "daily"
    if mode=="backfill":
        start=datetime.strptime(sys.argv[2],"%Y-%m-%d").date()
        end=datetime.strptime(sys.argv[3],"%Y-%m-%d").date()
        backfill(start,end)
    elif mode=="daily":
        daily()
    else:
        raise SystemExit("Use backfill YYYY-MM-DD YYYY-MM-DD or daily")
