import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

st.set_page_config(page_title="日本株 AI投資アシスタント Ver.5.5", page_icon="📈", layout="wide")

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

def tech(r,lo,hi):
    return sum([20*(r.MA25>r.MA75),20*(r.Close>r.MA200),15*(r.Close>r.MA25),
                15*(r.Volume>r.VOL20),15*(lo<=r.RSI<=hi),10*(r.MA25_Slope>0),5*(r.MA75_Slope>0)])

def market_info(m,d):
    if m.empty:return ("⚪ データなし",60,0.60)
    x=m[m.index<=pd.Timestamp(d)]
    if x.empty:return ("⚪ データなし",60,0.60)
    r=x.iloc[-1]; p=sum([r.Close>r.MA25,r.MA25>r.MA75,r.MA75>r.MA200,r.MA25_Slope>0])
    return [("🔴 弱気",0,0),("🟠 やや弱気",35,.35),("⚪ 中立",60,.60),
            ("🟡 やや強気",85,.85),("🟢 強気",100,1.0)][p]

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
    # 3+ consecutive losses on the same stock lowers its confidence modestly.
    losses = int(s.get("recent_losses", 0))
    if losses >= 3: return 0.82
    if losses == 2: return 0.90
    if losses == 1: return 0.96
    return 1.00


with st.sidebar:
    st.header("⚙️ 詳細設定")
    initial=st.number_input("初期資金（円）",10000,10000000,100000,10000)
    maxpos=st.number_input("最大保有銘柄数",1,50,10)
    maxbuy=st.number_input("1銘柄最大購入額（円）",1000,1000000,10000,1000)
    sl=st.slider("損切り（%）",3.0,12.0,6.0,.5)
    tp=st.slider("利確（%）",8.0,40.0,15.0,1.0)
    rlo=st.slider("RSI下限",25,60,40); rhi=st.slider("RSI上限",60,80,70)
    mintech=st.slider("最低テクニカルスコア",60,90,75)
    cooldown=st.number_input("4連敗後の新規BUY停止日数",5,30,10)
    severe_cooldown=st.number_input("10連敗後の新規BUY停止日数",10,60,20)
    use_liq=st.checkbox("過去5年平均売買代金TOP50を使用",True)
    universe=st.text_area("分析対象銘柄コード",DEFAULT)
    held=st.text_area("現在保有している銘柄コード","")
    entries=st.text_area("取得単価（例：7203:1500）","")

st.title("📈 日本株 AI投資アシスタント Ver.5.5")
st.caption("BUILD: VER5.5-TEST1-20260815")
st.caption("🌅 朝イチは「買う・売る・何もしない」だけを確認")

with st.spinner("🧠 裏側で5年間のAI分析・バックテストを実行中…"):
    data={t:stock_data(t) for t in tickers(universe)}
    data={t:d for t,d in data.items() if not d.empty}
    market=market_data()

liq=pd.DataFrame([{"コード":code(t),"銘柄名":name(t),"平均売買代金":d.Turnover.mean(),"平均出来高":d.Volume.mean()} for t,d in data.items()])
if not liq.empty:
    liq=liq.sort_values("平均売買代金",ascending=False).reset_index(drop=True)
    liq["売買代金順位"]=liq.index+1; liq["売買代金TOP50"]=liq["売買代金順位"]<=50
liq_codes=set(liq.loc[liq["売買代金TOP50"],"コード"]) if not liq.empty else set()

cash=float(initial); pos={}; stats={t:{"trades":0,"wins":0,"gp":0.0,"gl":0.0,"recent_losses":0} for t in data}
trades=[]; analyses=[]; equity=[]; losses=0; maxloss=0; block_until=None; severe_block_until=None
dates=sorted(set(x for d in data.values() for x in d.index))

for dt in dates:
    for t in list(pos):
        if dt not in data[t].index: continue
        r=data[t].loc[dt]; p=float(r.Close); q=pos[t]; pnl=(p-q["entry"])*q["shares"]; pct=(p/q["entry"]-1)*100
        # MA25 exit is now a confirmed exit: price below MA25 plus either
        # a falling MA25 or a clearly weakened technical score.
        ma25_confirm = (p < r.MA25) and ((r.MA25_Slope < 0) or (tech(r,rlo,rhi) < 60))
        reason="損切り" if pct<=-sl else "利確" if pct>=tp else "25日線割れ確認" if ma25_confirm else None
        if reason:
            cash+=p*q["shares"]; s=stats[t]; s["trades"]+=1
            if pnl>0:
                s["wins"]+=1; s["gp"]+=pnl; s["recent_losses"]=0; losses=0
            else:
                s["gl"]+=abs(pnl); s["recent_losses"]+=1; losses+=1; maxloss=max(maxloss,losses)
                if losses>=4:block_until=dt+pd.tseries.offsets.BDay(cooldown)
                if losses>=10:severe_block_until=dt+pd.tseries.offsets.BDay(severe_cooldown)
            trades.append({"日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"SELL","価格":p,"株数":q["shares"],"損益":pnl,"損益率":pct,"理由":reason,"未来情報使用":False,"連敗数":losses})
            del pos[t]

    cand=[]
    for t,d in data.items():
        if dt not in d.index or t in pos: continue
        r=d.loc[dt]; p=float(r.Close); c=code(t)
        if p>=2000 or (use_liq and c not in liq_codes): continue
        ts=tech(r,rlo,rhi)
        if ts<mintech: continue
        hc=confidence(stats[t]) * recent_loss_penalty(stats[t]); hp=conf_points(hc); ms,mp,mf=market_info(market,dt); score=ts*.55+hp*.30+mp*.15
        blocked=(block_until is not None and dt<=block_until) or (severe_block_until is not None and dt<=severe_block_until)
        analyses.append({"日付":dt,"コード":c,"銘柄名":name(t),"株価":p,"テクニカルスコア":ts,"銘柄実績信頼度":hc,"銘柄実績ポイント":hp,"市場判定":ms,"市場ポイント":mp,"総合AIスコア":score,"売買代金TOP50":c in liq_codes,"RSI":float(r.RSI),"新規BUY停止":blocked,"連敗リスク係数":0.50 if losses>=7 else 1.00,"未来情報使用":False})
        if not blocked and mf>0: cand.append((score,t,ts,hc,ms))
    cand.sort(reverse=True)
    for score,t,ts,hc,ms in cand:
        if len(pos)>=maxpos: break
        p=float(data[t].loc[dt].Close); risk_factor=0.50 if losses>=7 else 1.00; budget=min(maxbuy,cash)*factor(score)*risk_factor; shares=int(budget/p)
        if shares<=0: continue
        cost=shares*p
        if cost>cash: continue
        cash-=cost; pos[t]={"entry":p,"shares":shares}
        trades.append({"日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"BUY","価格":p,"株数":shares,"損益":0,"損益率":0,"理由":"Ver.5.4 AI BUY","テクニカルスコア":ts,"総合AIスコア":score,"銘柄実績信頼度":hc,"市場判定":ms,"購入資金係数":factor(score),"連敗リスク係数":0.50 if losses>=7 else 1.00,"未来情報使用":False})
    hv=sum(float(data[t].loc[dt].Close)*q["shares"] for t,q in pos.items() if dt in data[t].index)
    equity.append({"日付":dt,"現金":cash,"保有株評価額":hv,"総資産":cash+hv,"保有銘柄数":len(pos),"連敗数":losses,"新規BUY停止中":blocked if "blocked" in locals() else False,"連敗リスク係数":0.50 if losses>=7 else 1.00})

trades_df=pd.DataFrame(trades); analysis_df=pd.DataFrame(analyses); equity_df=pd.DataFrame(equity)

latest=[]
for t,d in data.items():
    r=d.iloc[-1]; p=float(r.Close); c=code(t)
    if p>=2000 or (use_liq and c not in liq_codes): continue
    ts=tech(r,rlo,rhi)
    if ts<mintech: continue
    hc=confidence(stats[t]) * recent_loss_penalty(stats[t]); hp=conf_points(hc); ms,mp,mf=market_info(market,d.index[-1]); score=ts*.55+hp*.30+mp*.15
    latest.append({"コード":c,"銘柄名":name(t),"株価":p,"総合AIスコア":score,"テクニカルスコア":ts,"銘柄実績信頼度":hc,"市場判定":ms,"購入資金係数":factor(score),"RSI":float(r.RSI)})
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
    if c in ep and (p/ep[c]-1)*100<=-sl: alerts.append("損切りライン")
    sell.append({"コード":c,"銘柄名":name(t),"現在価格":p,"AIスコア":tech(r,rlo,rhi),"判定":"SELL" if len(alerts)>=3 else "SELL注意" if alerts else "保有継続","警戒理由":" / ".join(alerts)})
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

st.header("🟢 BUY")
if latest_df.empty: st.info("💤 今日は買わない日です。")
else:
    for i,(_,r) in enumerate(latest_df.head(3).iterrows()):
        rank=["🥇","🥈","🥉"][i]; st.success(f"{rank} **{r['銘柄名']}（{r['コード']}）**　AI {r['総合AIスコア']:.0f}点　購入目安 ¥{maxbuy*r['購入資金係数']:,.0f}")

st.header("🔴 SELL")
if sell_candidates.empty: st.success("🟢 現在、明確な売却候補はありません。")
else:
    for _,r in sell_candidates.iterrows(): st.error(f"🔴 **{r['銘柄名']}（{r['コード']}）** → {r['判定']}　{r['警戒理由']}")

st.header("💤 今日の判断")
if latest_df.empty or float(latest_df.iloc[0]["総合AIスコア"])<75: st.info("今日は積極的なBUYを見送ります。")
else: st.success("BUY候補があります。無理のない金額で最終判断してください。")

summary=pd.DataFrame({"項目":["Ver","初期資金","最終資産","損益","損益率","決済トレード数","勝率","Profit Factor","最大DD","最大DD率","最大連続損失","明けの明星","株価2,000円以上BUY","25日線SELL","連敗ブレーキ"],"結果":["5.5",initial,final,profit,ret,len(selltr),winrate,pf,maxdd,maxddrate,maxloss,"不使用","除外","確認型","4/7/10段階"]})
stock_results=selltr.groupby(["コード","銘柄名"]).agg(トレード数=("損益","count"),勝ち=("損益",lambda x:(x>0).sum()),損益=("損益","sum"),平均損益=("損益","mean")).reset_index() if not selltr.empty else pd.DataFrame()
files={"00_summary.csv":summary,"01_today_buy.csv":latest_df,"02_today_sell.csv":sell_candidates,"03_all_ai_analysis.csv":analysis_df,"04_trade_history.csv":trades_df,"05_equity_curve.csv":equity_df,"06_stock_results.csv":stock_results,"07_liquidity_top50.csv":liq,"08_holdings_check.csv":sell_df}
buf=BytesIO()
with ZipFile(buf,"w") as z:
    for fn,df in files.items(): z.writestr(fn,csv_bytes(df))
buf.seek(0)
st.divider()
st.download_button("📦 全処理データをZIPでダウンロード",buf.getvalue(),"ver5_5_TEST1_all_analysis.zip","application/zip",use_container_width=True)
st.caption("裏側の全分析・バックテスト結果をCSVでまとめたZIPです。")
st.caption("※仮想バックテスト・投資判断補助です。SBI証券への自動発注は行いません。")
