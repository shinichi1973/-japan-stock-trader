import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

st.set_page_config(page_title="日本株 AI投資アシスタント Ver.5.5 RC3.1", page_icon="📈", layout="wide")

STOCK_NAMES = {
    "7203":"トヨタ自動車","6758":"ソニーグループ","9984":"ソフトバンクグループ",
    "8306":"三菱UFJフィナンシャル・グループ","9432":"NTT","6501":"日立製作所",
    "8035":"東京エレクトロン","8058":"三菱商事","7267":"ホンダ","2914":"JT",
    "9433":"KDDI","8316":"三井住友フィナンシャルグループ","8411":"みずほフィナンシャルグループ",
    "6098":"リクルートホールディングス","4063":"信越化学工業","4519":"中外製薬",
    "6367":"ダイキン工業","6857":"アドバンテスト","7974":"任天堂","8766":"東京海上ホールディングス",
    "5401":"日本製鉄","8801":"三井不動産","8802":"三菱地所","4502":"武田薬品工業",
    "4503":"アステラス製薬","4523":"エーザイ","4755":"楽天グループ","6594":"ニデック",
    "7741":"HOYA","6981":"村田製作所","3382":"セブン＆アイ・ホールディングス",
    "4661":"オリエンタルランド","6146":"ディスコ","6920":"レーザーテック",
    "7832":"バンダイナムコホールディングス","4568":"第一三共","4452":"花王",
    "6503":"三菱電機","6701":"NEC","6702":"富士通"
}
DEFAULT = "7203,6758,9984,8306,9432,6501,8035,8058,7267,2914,9433,8316,8411,6098,4063,4519,6367,6857,7974,8766,5401,8801,8802,4502,4503,4523,4755,6594,7741,6981"

def code(t): return t.replace(".T","")
def name(t): return STOCK_NAMES.get(code(t),code(t))
def tickers(s):
    return list(dict.fromkeys([x.strip() if x.strip().endswith(".T") else x.strip()+".T"
                               for x in s.replace("\n",",").split(",") if x.strip()]))
def parse_codes(s): return [x.strip().replace(".T","") for x in s.replace("\n",",").split(",") if x.strip()]
def parse_entries(s):
    d={}
    for x in s.replace("\n",",").split(","):
        if ":" in x:
            a,b=x.split(":",1)
            try: d[a.strip().replace(".T","")]=float(b)
            except: pass
    return d

def csv_bytes(df):
    if df is None: df = pd.DataFrame()
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

@st.cache_data(ttl=3600)
def stock_data(t, years=5):
    end=datetime.now(); start=end-timedelta(days=365*years+300)
    try:
        df=yf.download(t,start=start,end=end+timedelta(days=1),auto_adjust=False,progress=False,threads=False)
        if df.empty:return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        cols=["Open","High","Low","Close","Volume"]
        if not all(c in df.columns for c in cols):return pd.DataFrame()
        df=df[cols].copy()
        for c in cols: df[c]=pd.to_numeric(df[c],errors="coerce")
        df["MA25"]=df.Close.rolling(25).mean(); df["MA75"]=df.Close.rolling(75).mean()
        df["MA200"]=df.Close.rolling(200).mean(); df["MA25_Slope"]=df.MA25-df.MA25.shift(5)
        df["MA75_Slope"]=df.MA75-df.MA75.shift(5); df["VOL20"]=df.Volume.rolling(20).mean()
        df["Turnover"]=df.Close*df.Volume
        delta=df.Close.diff(); gain=delta.clip(lower=0).rolling(14).mean()
        loss=(-delta.clip(upper=0)).rolling(14).mean(); rs=gain/loss.replace(0,np.nan)
        df["RSI"]=100-(100/(1+rs))
        return df.dropna()
    except:return pd.DataFrame()

@st.cache_data(ttl=3600)
def market_data():
    end=datetime.now(); start=end-timedelta(days=365*5+300)
    try:
        df=yf.download("^N225",start=start,end=end+timedelta(days=1),auto_adjust=False,progress=False,threads=False)
        if df.empty:return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex):df.columns=df.columns.get_level_values(0)
        c=pd.to_numeric(df["Close"],errors="coerce")
        o=pd.DataFrame({"Close":c}); o["MA25"]=c.rolling(25).mean(); o["MA75"]=c.rolling(75).mean()
        o["MA200"]=c.rolling(200).mean(); o["MA25_Slope"]=o.MA25-o.MA25.shift(5)
        return o.dropna()
    except:return pd.DataFrame()


@st.cache_data(ttl=3600)
def overseas_data():
    end=datetime.now(); start=end-timedelta(days=365*5+300)
    symbols={"S&P500":"^GSPC","NASDAQ":"^IXIC","NYダウ":"^DJI",
             "SOX":"^SOX","USDJPY":"USDJPY=X","米10年金利":"^TNX"}
    out={}
    for label,symbol in symbols.items():
        try:
            df=yf.download(symbol,start=start,end=end+timedelta(days=1),
                           auto_adjust=False,progress=False,threads=False)
            if not df.empty:
                if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
                out[label]=pd.to_numeric(df["Close"],errors="coerce")
        except Exception:
            pass
    return pd.concat(out,axis=1).sort_index().ffill() if out else pd.DataFrame()

def overseas_snapshot(overseas, dt):
    base={"海外為替判定":"⚪ 海外データなし","海外為替係数":0.60,
          "S&P500_5d":np.nan,"NASDAQ_5d":np.nan,"SOX_5d":np.nan,
          "USDJPY_5d":np.nan,"US10Y_5d":np.nan}
    if overseas.empty: return base
    x=overseas[overseas.index<=pd.Timestamp(dt)]
    if x.empty: return base
    def r5(col):
        if col not in x.columns: return np.nan
        s=x[col].dropna()
        return float((s.iloc[-1]/s.iloc[-6]-1)*100) if len(s)>=6 else np.nan
    sp,nq,sox,fx,rate=[r5(c) for c in ["S&P500","NASDAQ","SOX","USDJPY","米10年金利"]]
    us=(1 if np.isfinite(sp) and sp>0 else 0)+(1 if np.isfinite(nq) and nq>0 else 0)
    sox_s=1 if np.isfinite(sox) and sox>0 else (-1 if np.isfinite(sox) and sox<0 else 0)
    fx_s=1 if np.isfinite(fx) and fx>0 else (-1 if np.isfinite(fx) and fx<0 else 0)
    rate_s=-1 if np.isfinite(rate) and rate>3 else (1 if np.isfinite(rate) and rate<-3 else 0)
    raw=us*.35+sox_s*.20+fx_s*.30+rate_s*.15
    factor=float(np.clip(.75+raw*.25,.45,1.15))
    state="🟢 海外・為替 良好" if factor>=1.03 else "🟡 海外・為替 やや良好" if factor>=.90 else "⚪ 海外・為替 中立" if factor>=.72 else "🔴 海外・為替 注意"
    return {"海外為替判定":state,"海外為替係数":factor,"S&P500_5d":sp,"NASDAQ_5d":nq,
            "SOX_5d":sox,"USDJPY_5d":fx,"US10Y_5d":rate,"sox_score":float(sox_s),"fx_score":float(fx_s)}

def score_band_policy(score, market_state, overseas_factor):
    if 82 <= score < 85:
        band="82-85"
    elif 80 <= score < 82:
        band="80-82"
    elif 85 <= score < 90:
        band="85-90"
    elif score >= 90:
        band="90+"
    else:
        band="<80"
    conf={"82-85":1.02,"85-90":0.98,"90+":0.99}.get(band,1.0)
    if overseas_factor < 0.50:
        conf *= 0.92
    return band,float(conf)

def sector_overseas_bonus(ticker,snap):
    c=code(ticker)
    semiconductor={"8035","6857","6146","6920","4063","6981"}
    exporters={"7203","7267","6501","6503","6758","6594","6367","7741"}
    financials={"8306","8316","8411","8766"}
    bonus=0.0
    if c in semiconductor: bonus += 1.5*snap.get("sox_score",0) if "sox_score" in snap else 0
    elif c in exporters: bonus += 1.5*(1 if snap.get("USDJPY_5d",0)>0 else -1 if snap.get("USDJPY_5d",0)<0 else 0)
    elif c in financials: bonus += 0.75*(1 if snap.get("US10Y_5d",0)<0 else -1 if snap.get("US10Y_5d",0)>0 else 0)
    return float(np.clip(bonus,-6,6))

def tech_components(r, lo, hi):
    return {
        "MA25>MA75": 20 * int(r.MA25 > r.MA75),
        "Close>MA200": 20 * int(r.Close > r.MA200),
        "Close>MA25": 15 * int(r.Close > r.MA25),
        "Volume>VOL20": 15 * int(r.Volume > r.VOL20),
        "RSI": 15 * int(lo <= r.RSI <= hi),
        "MA25_Slope": 10 * int(r.MA25_Slope > 0),
        "MA75_Slope": 5 * int(r.MA75_Slope > 0),
    }

def tech(r, lo, hi):
    return float(sum(tech_components(r, lo, hi).values()))

def market_info(m,d):
    if m.empty:return ("⚪ データなし",60,0.60)
    x=m[m.index<=pd.Timestamp(d)]
    if x.empty:return ("⚪ データなし",60,0.60)
    r=x.iloc[-1]; p=sum([r.Close>r.MA25,r.MA25>r.MA75,r.MA75>r.MA200,r.MA25_Slope>0])
    return [("🔴 弱気",0,0),("🟠 やや弱気",35,.35),("⚪ 中立",60,.60),
            ("🟡 やや強気",84,.84),("🟢 強気",100,1.0)][p]

def next_trade_date(index, dt):
    """Return the next trading date actually available for this ticker."""
    idx = pd.DatetimeIndex(index)
    pos = idx.searchsorted(pd.Timestamp(dt), side="right")
    return idx[pos] if pos < len(idx) else None


def risk_factor_from_losses(losses):
    return 0.30 if losses >= 9 else 0.50 if losses >= 7 else 1.00


def is_blocked(dt, block_until, severe_block_until):
    return ((block_until is not None and dt <= block_until) or
            (severe_block_until is not None and dt <= severe_block_until))


def confidence(s):
    if s["trades"]<8:return 1.0
    wr=s["wins"]/s["trades"]; pf=s["gp"]/s["gl"] if s["gl"] else 9.99
    if wr>=.55 and pf>=1.30:return 1.15
    if wr>=.48 and pf>=1.10:return 1.08
    if wr>=.40 and pf>=.90:return 1.00
    if wr>=.30 and pf>=.70:return .82
    return .65

def conf_points(c): return float(np.clip((c-.65)/.50*100,0,100))
def factor(s): return 1.0 if s>=85 else .85 if s>=75 else .70 if s>=65 else .50 if s>=55 else 0

def recent_loss_penalty(s):
    # Only uses trades already completed up to the current backtest date.
    losses = int(s.get("recent_losses", 0))
    if losses >= 3: return 0.82
    if losses == 2: return 0.90
    if losses == 1: return 0.96
    return 1.00


def stock_quality(s):
    """Reject or down-weight stocks whose completed history is persistently poor.
    Only completed trades available up to the current backtest date are used.
    """
    n = int(s.get("trades", 0))
    wins = int(s.get("wins", 0))
    gp = float(s.get("gp", 0.0)); gl = float(s.get("gl", 0.0))
    recent = int(s.get("recent_losses", 0))
    wr = wins / n if n else 0.0
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    avg = (gp - gl) / n if n else 0.0

    # Not enough completed evidence: do not punish a stock merely for being new.
    if n < 8:
        return 1.00, False, "実績不足（中立）", wr, pf, avg

    # Hard filters: enough evidence exists that repeated poor results should stop BUY.
    if n >= 12 and pf < 0.85 and avg < 0:
        return 0.00, True, "過去PF不良・期待値マイナス", wr, pf, avg
    if n >= 20 and wr < 0.30 and avg < 0:
        return 0.00, True, "過去勝率不良・期待値マイナス", wr, pf, avg

    # Soft penalties: keep good candidates alive but make bad history hard to pass.
    q = 1.00
    reason = "実績許容"
    if pf < 0.95 or avg < 0:
        q *= 0.78; reason = "過去実績を減点"
    elif pf >= 1.20 and avg > 0 and wr >= 0.40:
        q *= 1.08; reason = "過去実績を加点"
    elif pf >= 1.00 and avg >= 0:
        q *= 1.03; reason = "過去実績はプラス"
    if recent >= 3:
        q *= 0.88
        reason += "・直近連敗"
    return float(np.clip(q, 0.0, 1.08)), False, reason, wr, pf, avg


with st.sidebar:
    st.header("⚙️ 詳細設定")
    initial=st.number_input("初期資金（円）",10000,10000000,100000,10000)
    maxpos=st.number_input("最大保有銘柄数",1,50,7)
    maxbuy=st.number_input("1銘柄最大購入額（円）",1000,1000000,10000,1000)
    sl=st.slider("損切り（%）",3.0,12.0,6.0,.5)
    tp=st.slider("利確（%）",8.0,40.0,15.0,1.0)
    rlo=st.slider("RSI下限",25,60,40); rhi=st.slider("RSI上限",60,80,70)
    mintech=st.slider("最低テクニカルスコア",60,90,75)
    minbuy_score=st.slider("BUY最低AIスコア",70,90,80)
    st.caption("RC3ではBUY条件を変えず、採用BUYの成績をスコア帯・銘柄期待値・市場環境別に検証します。")
    cooldown=st.number_input("4連敗後の新規BUY停止日数",5,30,10)
    risk_cooldown=st.number_input("9連敗後の新規BUY停止日数",5,45,15)
    severe_cooldown=st.number_input("10連敗後の新規BUY停止日数",10,60,20)
    max_gap=st.slider("翌営業日寄付ギャップ許容（%）",1.0,10.0,5.0,0.5)
    use_liq=st.checkbox("過去5年平均売買代金TOP50を使用",True)
    universe=st.text_area("分析対象銘柄コード",DEFAULT)
    held=st.text_area("現在保有している銘柄コード","")
    entries=st.text_area("取得単価（例：7203:1500）","")

st.title("📈 日本株 AI投資アシスタント Ver.5.5 RC3.1")
st.caption("RC3: 悪いBUYを削る期待値フィルターを追加。銘柄別の過去PF・勝率・平均損益・直近連敗をBUY判断に反映します。")
st.caption("BUILD: VER5.5-RC3-20260815")
st.caption("🌅 朝イチは「買う・売る・何もしない」だけを確認｜米国市場・為替を裏側で評価｜条件不足なら無理にBUYしません")
st.caption("🛡️ RC2: シグナルは当日終値で確定し、銘柄ごとの次回取引日の寄付で仮想約定。寄付ギャップ急騰・急落は見送ります。")

with st.spinner("🧠 裏側で5年間のAI分析・バックテストを実行中…"):
    data={t:stock_data(t) for t in tickers(universe)}
    data={t:d for t,d in data.items() if not d.empty}
    market=market_data()
    overseas=overseas_data()

liq=pd.DataFrame([{"コード":code(t),"銘柄名":name(t),"平均売買代金":d.Turnover.mean(),"平均出来高":d.Volume.mean()} for t,d in data.items()])
if not liq.empty:
    liq=liq.sort_values("平均売買代金",ascending=False).reset_index(drop=True)
    liq["売買代金順位"]=liq.index+1; liq["売買代金TOP50"]=liq["売買代金順位"]<=50
liq_codes=set(liq.loc[liq["売買代金TOP50"],"コード"]) if not liq.empty else set()

cash=float(initial); pos={}
stats={t:{"trades":0,"wins":0,"gp":0.0,"gl":0.0,"recent_losses":0} for t in data}
trades=[]; analyses=[]; equity=[]; losses=0; maxloss=0
block_until=None; severe_block_until=None
# Orders are keyed by the actual next trading day of each ticker.
# pending_tickers prevents repeated orders for the same stock while an order is pending.
pending_buys={}
pending_tickers=set()
dates=sorted(set(x for d in data.values() for x in d.index))

for dt in dates:
    # 1) Execute BUY signals generated on the previous trading day.
    due = pending_buys.pop(dt, [])
    for order in due:
        t=order["ticker"]
        pending_tickers.discard(t)
        if t not in data or dt not in data[t].index or t in pos:
            continue
        r=data[t].loc[dt]
        p=float(r.Open)
        signal_close=float(order["signal_close"])
        gap_pct=(p/signal_close-1)*100 if signal_close > 0 else 999.0
        if not np.isfinite(p) or p <= 0 or p >= 2000:
            continue
        # Avoid chasing abnormal overnight gaps. The signal remains valid only
        # when the next-open price is within the configured execution tolerance.
        if abs(gap_pct) > order["max_gap_pct"]:
            continue
        blocked=is_blocked(dt, block_until, severe_block_until)
        if blocked or len(pos)>=maxpos or order["market_factor"]<=0:
            continue
        risk_factor=risk_factor_from_losses(losses)
        budget=min(maxbuy,cash)*factor(order["score"])*risk_factor
        shares=int(budget/p)
        if shares<=0:
            continue
        cost=shares*p
        if cost>cash:
            continue
        cash-=cost
        pos[t]={"entry":p,"shares":shares}
        trades.append({
            "日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"BUY",
            "価格":p,"株数":shares,"損益":0,"損益率":0,
            "理由":"Ver.5.5 RC2 AI BUY（翌営業日寄付約定）",
            "シグナル日":order["signal_date"],
            "テクニカルスコア":order["ts"],
            "総合AIスコア":order["score"],
            "銘柄実績信頼度":order["hc"],"市場判定":order["market_state"],
            "銘柄期待値係数":order.get("qfactor",1.0),"海外為替判定":order.get("overseas_state",""),"海外為替係数":order.get("overseas_factor",1.0),"海外補正":order.get("overseas_bonus",0.0),"過去勝率":order.get("wr_hist",0)*100,"過去PF":order.get("pf_hist",0),"過去平均損益":order.get("avg_hist",0),
            "購入資金係数":factor(order["score"]),
            "連敗リスク係数":risk_factor,"シグナル終値":signal_close,
            "寄付ギャップ率":gap_pct,"未来情報使用":False
        })

    # 2) Existing positions are evaluated using today's close.
    for t in list(pos):
        if dt not in data[t].index:
            continue
        r=data[t].loc[dt]
        p=float(r.Close)
        q=pos[t]
        pnl=(p-q["entry"])*q["shares"]
        pct=(p/q["entry"]-1)*100

        ma25_confirm=(p<r.MA25) and ((r.MA25_Slope<0) or (tech(r,rlo,rhi)<60))
        reason=("損切り" if pct<=-sl else
                "利確" if pct>=tp else
                "25日線割れ確認" if ma25_confirm else None)
        if reason:
            cash+=p*q["shares"]
            s=stats[t]
            s["trades"]+=1
            if pnl>0:
                s["wins"]+=1
                s["gp"]+=pnl
                s["recent_losses"]=0
                losses=0
            else:
                s["gl"]+=abs(pnl)
                s["recent_losses"]+=1
                losses+=1
                maxloss=max(maxloss,losses)
                if losses>=10:
                    severe_block_until=dt+pd.tseries.offsets.BDay(severe_cooldown)
                elif losses>=9:
                    block_until=dt+pd.tseries.offsets.BDay(risk_cooldown)
                elif losses>=4:
                    block_until=dt+pd.tseries.offsets.BDay(cooldown)

            trades.append({
                "日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"SELL",
                "価格":p,"株数":q["shares"],"損益":pnl,"損益率":pct,
                "理由":reason,"未来情報使用":False,"連敗数":losses
            })
            del pos[t]

    # 3) Generate today's BUY signals only from information available at today's close.
    cand=[]
    for t,d in data.items():
        if dt not in d.index or t in pos:
            continue
        r=d.loc[dt]
        p=float(r.Close)
        c=code(t)

        # User's permanent rule: stocks >= 2,000 yen are excluded.
        if p>=2000 or (use_liq and c not in liq_codes):
            continue

        ts=tech(r,rlo,rhi)
        if ts<mintech:
            continue

        hc=confidence(stats[t])*recent_loss_penalty(stats[t])
        hp=conf_points(hc)
        ms,mp,mf=market_info(market,dt)
        osnap=overseas_snapshot(overseas,dt)
        obonus=sector_overseas_bonus(t,osnap)
        qfactor,qblock,qreason,wr_hist,pf_hist,avg_hist=stock_quality(stats[t])
        base_score=ts*.55+hp*.30+mp*.15
        score=float(np.clip(base_score*qfactor+obonus,0,100))
        blocked=((block_until is not None and dt<=block_until) or
                 (severe_block_until is not None and dt<=severe_block_until))
        buy_threshold = 85 if ms == "🟡 やや強気" else 80
        buy_reject = qblock or score < buy_threshold

        analyses.append({
            "日付":dt,"コード":c,"銘柄名":name(t),"株価":p,
            "テクニカルスコア":ts,"銘柄実績信頼度":hc,
            "銘柄実績ポイント":hp,"市場判定":ms,"市場ポイント":mp,"海外為替判定":osnap["海外為替判定"],"海外為替係数":osnap["海外為替係数"],"海外補正":obonus,"AIスコア帯":score_band,"スコア信頼補正":score_conf,
            "総合AIスコア":score,"元AIスコア":base_score,
            "銘柄期待値係数":qfactor,"銘柄BUY除外":qblock,
            "銘柄BUY判定理由":qreason,"過去勝率":wr_hist*100,
            "過去PF":pf_hist,"過去平均損益":avg_hist,
            "売買代金TOP50":c in liq_codes,
            "RSI":float(r.RSI),"新規BUY停止":blocked,"海外為替判定":osnap["海外為替判定"],"海外為替係数":osnap["海外為替係数"],"海外補正":obonus,"AIスコア帯":score_band,"スコア信頼補正":score_conf,
            "BUY最低スコア未達":score < minbuy_score,
            "連敗リスク係数":risk_factor_from_losses(losses),
            "未来情報使用":False
        })
        if not blocked and mf>0 and not buy_reject:
            cand.append((score,t,ts,hc,ms,mp))

    cand.sort(reverse=True)

    # Schedule BUY for the actual next trading day of each ticker.
    # This avoids a global-calendar mismatch when a ticker has a missing session.
    for score,t,ts,hc,ms,mp in cand:
        if t in pending_tickers:
            continue
        next_dt=next_trade_date(data[t].index, dt)
        if next_dt is None:
            continue
        pending_buys.setdefault(next_dt,[]).append({
            "ticker":t,"score":score,"ts":ts,"hc":hc,
            "market_state":ms,"market_factor":mp,"overseas_state":osnap["海外為替判定"],"overseas_factor":osnap["海外為替係数"],"overseas_bonus":obonus,"signal_date":dt,
            "signal_close":float(data[t].loc[dt].Close),
            "max_gap_pct":max_gap,
            "qfactor":qfactor,"wr_hist":wr_hist,"pf_hist":pf_hist,"avg_hist":avg_hist
        })
        pending_tickers.add(t)

    hv=sum(float(data[t].loc[dt].Close)*q["shares"]
           for t,q in pos.items() if dt in data[t].index)
    day_blocked=((block_until is not None and dt<=block_until) or
                 (severe_block_until is not None and dt<=severe_block_until))
    equity.append({
        "日付":dt,"現金":cash,"保有株評価額":hv,"総資産":cash+hv,
        "保有銘柄数":len(pos),"連敗数":losses,
        "新規BUY停止中":day_blocked,
        "連敗リスク係数":risk_factor_from_losses(losses)
    })

trades_df=pd.DataFrame(trades); analysis_df=pd.DataFrame(analyses); equity_df=pd.DataFrame(equity)

latest=[]
for t,d in data.items():
    r=d.iloc[-1]; p=float(r.Close); c=code(t)
    if p>=2000 or (use_liq and c not in liq_codes): continue
    ts=tech(r,rlo,rhi)
    if ts<mintech: continue
    hc=confidence(stats[t]) * recent_loss_penalty(stats[t]); hp=conf_points(hc); ms,mp,mf=market_info(market,d.index[-1])
    osnap=overseas_snapshot(overseas,d.index[-1]); obonus=sector_overseas_bonus(t,osnap)
    qfactor,qblock,qreason,wr_hist,pf_hist,avg_hist=stock_quality(stats[t])
    base_score=ts*.55+hp*.30+mp*.15
    raw_score=float(np.clip(base_score*qfactor+obonus,0,100))
    score_band,score_conf=score_band_policy(raw_score,ms,osnap["海外為替係数"])
    score=float(np.clip(raw_score*score_conf,0,100))
    buy_threshold = 86 if ms in ["⚪ 中立","🔴 やや弱気"] else (82 if ms == "🟡 やや強気" else 80)
    if qblock or score < buy_threshold or mf<=0: continue
    latest.append({"コード":c,"銘柄名":name(t),"株価":p,"総合AIスコア":score,"テクニカルスコア":ts,"銘柄実績信頼度":hc,"銘柄期待値係数":qfactor,"過去勝率":wr_hist*100,"過去PF":pf_hist,"過去平均損益":avg_hist,"市場判定":ms,"海外為替判定":osnap["海外為替判定"],"海外為替係数":osnap["海外為替係数"],"海外補正":obonus,"AIスコア帯":score_band,"スコア信頼補正":score_conf,"購入資金係数":factor(score),"RSI":float(r.RSI)})
latest_df=pd.DataFrame(latest).sort_values("総合AIスコア",ascending=False) if latest else pd.DataFrame()

ep=parse_entries(entries); sell=[]
for c in parse_codes(held):
    t=c+".T"
    if t not in data: continue
    r=data[t].iloc[-1]; p=float(r.Close); alerts=[]
    if p<r.MA25 and (r.MA25_Slope<0 or tech(r,rlo,rhi)<60): alerts.append("25日線割れ確認")
    if r.MA25<r.MA75: alerts.append("25日線<75日線")
    if r.MA25_Slope<0: alerts.append("25日線下降")
    if tech(r,rlo,rhi)<60: alerts.append("AIスコア低下")
    osnap=overseas_snapshot(overseas,d.index[-1])
    if osnap["海外為替係数"]<0.50: alerts.append("海外・為替環境悪化")
    if c in ep and (p/ep[c]-1)*100<=-sl: alerts.append("損切りライン")
    sell.append({"コード":c,"銘柄名":name(t),"現在価格":p,"AIスコア":tech(r,rlo,rhi),"判定":"SELL" if len(alerts)>=3 else "SELL注意" if alerts else "保有継続","警戒理由":" / ".join(alerts),"海外為替判定":osnap["海外為替判定"],"売却期限目安":"原則：次の1～3営業日以内" if len(alerts)>=2 else "目安：1～2週間以内"})
sell_df=pd.DataFrame(sell); sell_candidates=sell_df[sell_df["判定"].isin(["SELL","SELL注意"])] if not sell_df.empty else pd.DataFrame()

if not equity_df.empty and "総資産" in equity_df.columns:
    equity_df["総資産"]=pd.to_numeric(equity_df["総資産"],errors="coerce").fillna(initial); final=float(equity_df["総資産"].iloc[-1]); equity_df["最高資産"]=equity_df["総資産"].cummax(); equity_df["DD"]=equity_df["総資産"]-equity_df["最高資産"]; equity_df["DD率"]=np.where(equity_df["最高資産"]!=0,equity_df["DD"]/equity_df["最高資産"]*100,0.0); maxdd=float(equity_df["DD"].min()); maxddrate=float(equity_df["DD率"].min())
else:
    equity_df=pd.DataFrame(columns=["日付","現金","保有株評価額","総資産","保有銘柄数","連敗数","新規BUY停止中"]); final=float(initial); maxdd=0.0; maxddrate=0.0

profit=final-initial; ret=profit/initial*100 if initial else 0.0
selltr=trades_df[trades_df["売買"]=="SELL"] if not trades_df.empty else pd.DataFrame()
winrate=(selltr["損益"]>0).mean()*100 if not selltr.empty else 0
gp=selltr.loc[selltr["損益"]>0,"損益"].sum() if not selltr.empty else 0
gl=abs(selltr.loc[selltr["損益"]<0,"損益"].sum()) if not selltr.empty else 0
pf=gp/gl if gl else 0

# Open positions are not counted as completed trades/PF. Keep their unrealized P&L
# visible so the final asset does not look like realized performance.
open_positions=[]
for t,q in pos.items():
    if t in data and not data[t].empty:
        r=data[t].iloc[-1]; p=float(r.Close)
        upnl=(p-q["entry"])*q["shares"]
        upct=(p/q["entry"]-1)*100 if q["entry"] else 0.0
        open_positions.append({"コード":code(t),"銘柄名":name(t),"取得価格":q["entry"],
                               "現在価格":p,"株数":q["shares"],"含み損益":upnl,"含み損益率":upct})
open_positions_df=pd.DataFrame(open_positions)

st.header("🛡️ Ver.5.5 RC3.1 モデル健全性")
st.info(
    "BUYはシグナル当日終値で判定し、各銘柄の次回取引日の寄付で仮想約定。"
    "株価2,000円以上は除外、明けの明星は不使用。RC3では過去PF・勝率・平均損益・直近連敗で悪いBUYを追加除外します。"
)

st.header("① 🟢 今日の買い候補 TOP3")
if latest_df.empty: st.info("💤 今日は買わない日です。")
else:
    for i,(_,r) in enumerate(latest_df.head(3).iterrows()):
        rank=["🥇","🥈","🥉"][i]; st.success(f"{rank} **{r['銘柄名']}（{r['コード']}）**　AI {r['総合AIスコア']:.0f}点　｜海外環境 **{r.get('海外為替判定','中立')}**")

st.header("② 🔴 もし保有していたら 売却 TOP3")
if sell_candidates.empty: st.success("🟢 現在、明確な売却候補はありません。")
else:
    for _,r in sell_candidates.iterrows(): st.error(f"🔴 **{r['銘柄名']}（{r['コード']}）** → {r['判定']}　｜売却目安 **{r.get('売却期限目安','1～2週間以内')}**")

st.header("③ 📊 ロジック・処理結果分析")
if latest_df.empty or float(latest_df.iloc[0]["総合AIスコア"])<75: st.info("今日は積極的なBUYを見送ります。")
else: st.success("BUY候補があります。無理のない金額で最終判断してください。")

if not open_positions_df.empty:
    st.header("📦 バックテスト終了時の未決済ポジション")
    st.dataframe(open_positions_df, use_container_width=True)

summary=pd.DataFrame({"項目":["Ver","初期資金","最終資産","損益","損益率","決済トレード数","勝率","Profit Factor","最大DD","最大DD率","最大連続損失","明けの明星","株価2,000円以上BUY","25日線SELL","連敗ブレーキ","寄付ギャップ制御","悪いBUY除外","BUY最低AIスコア","RC3 80点固定検証"],"結果":["5.5 RC3",initial,final,profit,ret,len(selltr),winrate,pf,maxdd,maxddrate,maxloss,"不使用","除外","確認型","4/7/9/10段階","あり","銘柄別期待値フィルター","{}".format(minbuy_score),"スコア帯・期待値係数・市場環境別の実績分析"]})
stock_results=selltr.groupby(["コード","銘柄名"]).agg(トレード数=("損益","count"),勝ち=("損益",lambda x:(x>0).sum()),損益=("損益","sum"),平均損益=("損益","mean")).reset_index() if not selltr.empty else pd.DataFrame()
# RC3 diagnostic: pair each completed SELL with its originating BUY and evaluate which BUY characteristics worked.
buy_rows=trades_df[trades_df["売買"]=="BUY"].copy() if not trades_df.empty else pd.DataFrame()
sell_rows=trades_df[trades_df["売買"]=="SELL"].copy() if not trades_df.empty else pd.DataFrame()
paired=[]
if not buy_rows.empty and not sell_rows.empty:
    active={}
    for _,row in trades_df.sort_values("日付").iterrows():
        key=row.get("コード")
        if row.get("売買")=="BUY": active[key]=row
        elif row.get("売買")=="SELL" and key in active:
            b=active.pop(key); paired.append({
                "コード":key,"銘柄名":row.get("銘柄名"),"BUY日":b.get("日付"),"SELL日":row.get("日付"),
                "損益":row.get("損益",0),"損益率":row.get("損益率",0),"AIスコア":b.get("総合AIスコア",np.nan),
                "テクニカルスコア":b.get("テクニカルスコア",np.nan),"銘柄期待値係数":b.get("銘柄期待値係数",1.0),
                "過去勝率":b.get("過去勝率",0),"過去PF":b.get("過去PF",0),"過去平均損益":b.get("過去平均損益",0),
                "市場判定":b.get("市場判定",""),"寄付ギャップ率":b.get("寄付ギャップ率",np.nan)})
paired_df=pd.DataFrame(paired)
if not paired_df.empty:
    bins=[-np.inf,75,78,80,82,85,90,np.inf]; labels=["<75","75-78","78-80","80-82","82-85","85-90","90+"]
    paired_df["AIスコア帯"]=pd.cut(paired_df["AIスコア"],bins=bins,labels=labels,right=False)
    score_band=paired_df.groupby("AIスコア帯",observed=False).agg(
        トレード数=("損益","count"),勝率=("損益",lambda x:(x>0).mean()*100),損益=("損益","sum"),平均損益=("損益","mean"),
        PF=("損益",lambda x:x[x>0].sum()/abs(x[x<0].sum()) if (x<0).any() else 0)).reset_index()
    q_band=paired_df.groupby("銘柄期待値係数",observed=False).agg(トレード数=("損益","count"),勝率=("損益",lambda x:(x>0).mean()*100),損益=("損益","sum"),平均損益=("損益","mean")).reset_index()
    market_band=paired_df.groupby("市場判定",observed=False).agg(トレード数=("損益","count"),勝率=("損益",lambda x:(x>0).mean()*100),損益=("損益","sum"),平均損益=("損益","mean")).reset_index()
else:
    score_band=pd.DataFrame(); q_band=pd.DataFrame(); market_band=pd.DataFrame()
files={"00_summary.csv":summary,"01_today_buy.csv":latest_df,"02_today_sell.csv":sell_candidates,"03_all_ai_analysis.csv":analysis_df,"04_trade_history.csv":trades_df,"05_equity_curve.csv":equity_df,"06_stock_results.csv":stock_results,"07_liquidity_top50.csv":liq,"08_holdings_check.csv":sell_df,"09_open_positions.csv":open_positions_df,"10_paired_buy_sell.csv":paired_df,"11_score_band_analysis.csv":score_band,"12_quality_factor_analysis.csv":q_band,"13_market_analysis.csv":market_band,"14_overseas_fx_analysis.csv":analysis_df}
buf=BytesIO()
with ZipFile(buf,"w") as z:
    for fn,df in files.items(): z.writestr(fn,csv_bytes(df))
buf.seek(0)
st.divider()
st.download_button("📦 Ver.5.5 RC3.1 全処理データをZIPでダウンロード",buf.getvalue(),"ver5_5_RC2_1_all_analysis.zip","application/zip",use_container_width=True)
st.caption("裏側の全分析・バックテスト結果をCSVでまとめたZIPです。BUYは銘柄ごとの次回取引日寄付約定モデルです。")
st.caption("※仮想バックテスト・投資判断補助です。SBI証券への自動発注は行いません。")
