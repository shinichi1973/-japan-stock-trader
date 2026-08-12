import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="日本株 仮想売買 Ver.2", page_icon="📈", layout="wide")
st.title("📈 日本株 仮想売買システム Ver.2")
st.caption("仮想売買専用。証券会社への実注文は行いません。")

st.sidebar.header("⚙️ 基本設定")
initial_cash = st.sidebar.number_input("初期資金", 1000000, 100000000, 1000000, 100000)
max_positions = st.sidebar.number_input("最大保有銘柄数", 1, 50, 10)
max_per_position = st.sidebar.number_input("1銘柄の最大購入額", 10000, 10000000, 100000, 10000)
stop_loss = st.sidebar.slider("損切り (%)", 1, 30, 7) / 100
take_profit = st.sidebar.slider("利確 (%)", 1, 100, 15) / 100
rsi_max = st.sidebar.slider("RSI上限", 50, 90, 70)

st.sidebar.header("🎯 銘柄選定条件")
use_tick_top50 = st.sidebar.checkbox("SBI ティック回数上位50", value=True)
use_morning_star = st.sidebar.checkbox("明けの明星成立", value=True)
use_price_2000 = st.sidebar.checkbox("株価2,000円以上", value=True)

def indicators(g):
    g = g.sort_values("date").copy()
    g["ma25"] = g["close"].rolling(25).mean()
    g["ma75"] = g["close"].rolling(75).mean()
    delta = g["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    g["rsi"] = 100 - (100 / (1 + rs))
    g["vol20"] = g["volume"].rolling(20).mean()

    # 3本のローソク足による簡易的な「明けの明星」判定
    body = (g["close"] - g["open"]).abs()
    avg = body.rolling(20).mean()
    first_bear = g["close"].shift(2) < g["open"].shift(2)
    first_large = body.shift(2) >= avg.shift(2) * 1.2
    middle_small = body.shift(1) <= avg.shift(1) * 0.5
    third_bull = g["close"] > g["open"]
    third_recovery = g["close"] >= (g["open"].shift(2) + g["close"].shift(2)) / 2
    g["morning_star"] = (first_bear & first_large & middle_small &
                         third_bull & third_recovery).fillna(False)
    return g

def backtest(df):
    df=df.copy()
    df["date"]=pd.to_datetime(df["date"])
    df["ticker"]=df["ticker"].astype(str)
    if "tick_rank" not in df.columns:
        df["tick_rank"]=np.nan
    df=df.sort_values(["ticker","date"])
    df=df.groupby("ticker", group_keys=False).apply(indicators).reset_index(drop=True)

    cash=float(initial_cash); positions={}; trades=[]; curve=[]

    for date in sorted(df["date"].unique()):
        day=df[df["date"]==date]

        for ticker in list(positions):
            row=day[day.ticker==ticker]
            if row.empty: continue
            r=row.iloc[0]; price=float(r.close)
            p=positions[ticker]; ret=price/p["entry_price"]-1
            reason=None
            if ret <= -stop_loss: reason="損切り"
            elif ret >= take_profit: reason="利確"
            elif pd.notna(r.ma25) and price < r.ma25: reason="25日線割れ"
            if reason:
                proceeds=p["shares"]*price; cash+=proceeds
                trades.append([date,ticker,"SELL",price,p["shares"],reason,
                               proceeds-p["shares"]*p["entry_price"]])
                del positions[ticker]

        for _,r in day.iterrows():
            ticker=str(r.ticker)
            if ticker in positions or len(positions)>=max_positions: continue
            if use_tick_top50 and (pd.isna(r.tick_rank) or float(r.tick_rank)>50): continue
            if use_price_2000 and float(r.close)<2000: continue
            if use_morning_star and not bool(r.morning_star): continue
            if not all(pd.notna(r[x]) for x in ["ma25","ma75","rsi","vol20"]): continue
            if r.ma25<=r.ma75 or r.close<=r.ma25 or r.rsi>=rsi_max or r.volume<=r.vol20: continue

            price=float(r.close); budget=min(max_per_position,cash)
            shares=int(budget//(price*100))*100
            if shares<=0 or shares*price>cash: continue
            cost=shares*price; cash-=cost
            positions[ticker]={"shares":shares,"entry_price":price}
            trades.append([date,ticker,"BUY",price,shares,"選定条件成立",0])

        mv=sum(p["shares"]*float(day[day.ticker==t].iloc[0].close)
               for t,p in positions.items() if not day[day.ticker==t].empty)
        curve.append([date,cash+mv,cash,len(positions)])

    eq=pd.DataFrame(curve,columns=["date","equity","cash","positions"])
    tr=pd.DataFrame(trades,columns=["date","ticker","side","price","shares","reason","pnl"])
    return eq,tr,positions

uploaded=st.file_uploader("📁 株価CSVをアップロード",type=["csv"])
st.markdown("CSV必須: `date,ticker,open,close,volume`。ティック条件ONなら `tick_rank`（1～50）も必要です。")

if uploaded:
    df=pd.read_csv(uploaded)
    required={"date","ticker","open","close","volume"}
    missing=required-set(df.columns)
    if missing:
        st.error("不足列: "+", ".join(sorted(missing))); st.stop()

    active=[]
    if use_tick_top50: active.append("ティック上位50")
    if use_morning_star: active.append("明けの明星")
    if use_price_2000: active.append("株価2,000円以上")
    st.success(f"{len(df):,} 行を読み込みました。")
    st.write("**ONの追加条件:** "+(" / ".join(active) if active else "なし"))
    st.dataframe(df.tail(20),use_container_width=True)

    if st.button("▶ バックテスト開始",type="primary"):
        eq,tr,positions=backtest(df)
        final=float(eq.iloc[-1].equity); pnl=final-initial_cash
        a,b,c=st.columns(3)
        a.metric("最終資産",f"¥{final:,.0f}")
        b.metric("損益",f"¥{pnl:,.0f}",f"{pnl/initial_cash:.2%}")
        c.metric("取引回数",len(tr))
        st.subheader("📊 資産推移")
        st.line_chart(eq.set_index("date")["equity"])
        st.subheader("🧾 売買履歴")
        st.dataframe(tr.sort_values("date",ascending=False),use_container_width=True)
else:
    st.info("CSVをアップロードするとバックテストできます。")
st.caption("Ver.2 / 仮想売買のみ。SBI証券への自動注文・SBI画面の自動取得は実装していません。")
