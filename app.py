import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

st.set_page_config(page_title="日本株 自動バックテスト Ver.3", page_icon="📈", layout="wide")
st.title("📈 日本株 自動バックテスト Ver.3")
st.caption("過去5年の日足データを自動取得して仮想売買を検証します。実注文は行いません。")

st.sidebar.header("⚙️ 基本設定")
initial_cash = st.sidebar.number_input("初期資金（円）",100000,100000000,1000000,100000)
max_positions = st.sidebar.number_input("最大保有銘柄数",1,50,10)
max_per_position = st.sidebar.number_input("1銘柄の最大購入額（円）",10000,10000000,100000,10000)
stop_loss = st.sidebar.slider("損切り（%）",1,30,7)/100
take_profit = st.sidebar.slider("利確（%）",1,100,15)/100
rsi_max = st.sidebar.slider("RSI上限",50,90,70)

st.sidebar.header("🎯 条件")
use_morning_star = st.sidebar.checkbox("明けの明星",False)
use_price_2000 = st.sidebar.checkbox("株価2,000円以上",True)
use_ma = st.sidebar.checkbox("25日線 > 75日線 ＆ 株価 > 25日線",True)
use_volume = st.sidebar.checkbox("出来高 > 20日平均",True)
use_rsi = st.sidebar.checkbox("RSI < 上限",True)
years = st.sidebar.selectbox("取得期間（年）",[1,3,5],index=2)

PRESET20=["7203","6758","8306","9984","6861","6501","8035","6098","9432","9433",
"4502","4503","7267","7269","7974","2914","8058","8001","8411","8316"]
PRESET50=PRESET20+["4063","6857","6146","6367","6762","6981","6594","6902","7751","7735",
"6503","6301","6305","7011","7012","5401","5411","5406","8801","8802",
"9020","9021","9022","9101","9104","9107","3382","3387","8267","9843"]

preset=st.sidebar.selectbox("対象銘柄",["主要20銘柄","主要50銘柄","自分で指定"])
if preset=="主要20銘柄": codes=PRESET20
elif preset=="主要50銘柄": codes=PRESET50
else:
    s=st.sidebar.text_area("銘柄コード（カンマ区切り）","7203,6758,8306,9984,6861")
    codes=[x.strip().upper().replace(".T","") for x in s.replace("、",",").split(",") if x.strip()]
codes=list(dict.fromkeys(codes))

@st.cache_data(ttl=3600,show_spinner=False)
def get_data(codes,years):
    end=pd.Timestamp.now().normalize()
    start=end-pd.DateOffset(years=years)
    out=[]
    for code in codes:
        try:
            d=yf.download(code+".T",start=start,end=end+pd.Timedelta(days=1),
                           interval="1d",auto_adjust=True,progress=False,threads=False)
            if d is None or d.empty: continue
            if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
            d=d.reset_index()
            need=["Date","Open","Close","Volume"]
            if not all(c in d.columns for c in need): continue
            d=d[need].rename(columns={"Date":"date","Open":"open","Close":"close","Volume":"volume"})
            d["ticker"]=code
            d["date"]=pd.to_datetime(d["date"]).dt.tz_localize(None)
            for c in ["open","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
            out.append(d.dropna())
        except Exception:
            continue
    return pd.concat(out,ignore_index=True) if out else pd.DataFrame()

def indicators(g):
    g=g.sort_values("date").copy()
    g["ma25"]=g.close.rolling(25,min_periods=25).mean()
    g["ma75"]=g.close.rolling(75,min_periods=75).mean()
    delta=g.close.diff()
    gain=delta.clip(lower=0).rolling(14,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).rolling(14,min_periods=14).mean()
    rs=gain/loss.replace(0,np.nan)
    g["rsi"]=100-(100/(1+rs))
    g["vol20"]=g.volume.rolling(20,min_periods=20).mean()
    body=(g.close-g.open).abs()
    avg=body.rolling(20,min_periods=20).mean()
    g["morning_star"]=(
        (g.close.shift(2)<g.open.shift(2))&
        (body.shift(2)>=avg.shift(2)*1.2)&
        (body.shift(1)<=avg.shift(1)*0.5)&
        (g.close>g.open)&
        (g.close>=(g.open.shift(2)+g.close.shift(2))/2)
    ).fillna(False)
    return g

def run_bt(df):
    parts=[indicators(g) for _,g in df.groupby("ticker",sort=False)]
    df=pd.concat(parts,ignore_index=True).sort_values(["date","ticker"])
    cash=float(initial_cash); pos={}; trades=[]; curve=[]
    for date,day in df.groupby("date",sort=True):
        for t in list(pos):
            r=day[day.ticker==t]
            if r.empty: continue
            r=r.iloc[0]; price=float(r.close); p=pos[t]; ret=price/p["entry"]-1
            reason=None
            if ret<=-stop_loss: reason="損切り"
            elif ret>=take_profit: reason="利確"
            elif pd.notna(r.ma25) and price<r.ma25: reason="25日線割れ"
            if reason:
                proceeds=p["shares"]*price
                cash+=proceeds
                trades.append([date,t,"SELL",price,p["shares"],reason,proceeds-p["shares"]*p["entry"]])
                del pos[t]
        for _,r in day.iterrows():
            t=str(r.ticker); price=float(r.close)
            if t in pos or len(pos)>=max_positions: continue
            if use_price_2000 and price<2000: continue
            if use_morning_star and not bool(r.morning_star): continue
            if use_ma and (pd.isna(r.ma25) or pd.isna(r.ma75) or r.ma25<=r.ma75 or price<=r.ma25): continue
            if use_volume and (pd.isna(r.vol20) or r.volume<=r.vol20): continue
            if use_rsi and (pd.isna(r.rsi) or r.rsi>=rsi_max): continue
            shares=int(min(max_per_position,cash)//(price*100))*100
            if shares<=0: continue
            cost=shares*price
            if cost>cash: continue
            cash-=cost; pos[t]={"shares":shares,"entry":price}
            trades.append([date,t,"BUY",price,shares,"選定条件成立",0])
        mv=0
        for t,p in pos.items():
            r=day[day.ticker==t]
            if not r.empty: mv+=p["shares"]*float(r.iloc[0].close)
        curve.append([date,cash+mv,cash,len(pos)])
    eq=pd.DataFrame(curve,columns=["date","equity","cash","positions"])
    tr=pd.DataFrame(trades,columns=["date","ticker","side","price","shares","reason","pnl"])
    return eq,tr,pos

st.subheader("① 過去データを自動取得")
st.write(f"対象：**{len(codes)}銘柄** ／ **過去{years}年** ／ 日足")
if st.button("📥 株価データを取得",type="secondary"):
    with st.spinner("過去データを取得中です…"):
        data=get_data(tuple(codes),years)
    if data.empty:
        st.error("データを取得できませんでした。銘柄コードを確認してください。")
    else:
        st.session_state["data_v3"]=data
        st.success(f"{len(data):,}行・{data.ticker.nunique()}銘柄を取得しました。")

data=st.session_state.get("data_v3",pd.DataFrame())

if not data.empty:
    st.subheader("② 取得データ")
    st.dataframe(data.sort_values(["date","ticker"]).tail(30),use_container_width=True)
    st.subheader("③ バックテスト")
    if st.button("▶ バックテスト開始",type="primary",key="bt3"):
        with st.spinner("バックテスト中です…"):
            try:
                eq,tr,pos=run_bt(data)
                if eq.empty:
                    st.error("バックテスト可能なデータがありません。75営業日以上必要です。")
                else:
                    final=float(eq.iloc[-1].equity); pnl=final-initial_cash
                    dd=(eq.equity/eq.equity.cummax()-1).min()
                    sells=tr[tr.side=="SELL"] if not tr.empty else pd.DataFrame()
                    win=(sells.pnl>0).mean() if not sells.empty else 0
                    a,b,c=st.columns(3); a.metric("最終資産",f"¥{final:,.0f}"); b.metric("損益",f"¥{pnl:,.0f}",f"{pnl/initial_cash:.2%}"); c.metric("取引回数",len(tr))
                    a,b,c=st.columns(3); a.metric("最大ドローダウン",f"{dd:.2%}"); b.metric("決済勝率",f"{win:.2%}"); c.metric("最終保有",len(pos))
                    st.subheader("📊 資産推移"); st.line_chart(eq.set_index("date")["equity"])
                    st.subheader("🧾 売買履歴")
                    st.dataframe(tr.sort_values("date",ascending=False),use_container_width=True)
            except Exception as e:
                st.error("バックテスト中にエラーが発生しました。")
                st.exception(e)
else:
    st.info("まず「📥 株価データを取得」を押してください。取得後にバックテスト開始ボタンが表示されます。")

st.divider()
st.caption("Ver.3 / 仮想売買専用。証券会社への自動注文は行いません。")
