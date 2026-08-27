import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

st.set_page_config(page_title="日本株AI投資アシスタント RC3.11", page_icon="📈", layout="wide")
BUILD = "VER5.5-RC3.11-CLEAN"

STOCK_NAMES = {
"7203":"トヨタ自動車","6758":"ソニーグループ","9984":"ソフトバンクグループ",
"8306":"三菱UFJフィナンシャル・グループ","9432":"NTT","6501":"日立製作所",
"8035":"東京エレクトロン","8058":"三菱商事","7267":"ホンダ","2914":"JT",
"9433":"KDDI","8316":"三井住友FG","8411":"みずほFG","6098":"リクルートHD",
"4063":"信越化学工業","4519":"中外製薬","6367":"ダイキン工業",
"6857":"アドバンテスト","7974":"任天堂","8766":"東京海上HD",
"5401":"日本製鉄","8801":"三井不動産","8802":"三菱地所","4502":"武田薬品工業",
"4503":"アステラス製薬","4523":"エーザイ","4755":"楽天グループ","6594":"ニデック",
"7741":"HOYA","6981":"村田製作所","3382":"セブン＆アイHD",
"4661":"オリエンタルランド","6146":"ディスコ","6920":"レーザーテック",
"7832":"バンダイナムコHD","4568":"第一三共","4452":"花王",
"6503":"三菱電機","6701":"NEC","6702":"富士通",
"3444":"菊池製作所","5885":"ジーデップ・アドバンス",
"6324":"ハーモニック・ドライブ・システムズ","6506":"安川電機",
"6629":"テクノホライゾン","6954":"ファナック","6965":"浜松ホトニクス",
"7012":"川崎重工業","6085":"アーキテクツ・スタジオ・ジャパン"}
DEFAULT=",".join(STOCK_NAMES.keys())

with st.sidebar:
    st.header("⚙️ RC3.11")
    years=st.selectbox("価格データ期間",[2,3,5],index=1)
    rlo=st.slider("RSI下限",25,60,40)
    rhi=st.slider("RSI上限",60,80,70)
    topn=st.number_input("AIお勧めTOP",10,100,50,10)
    exclude2000=st.checkbox("2,000円以上をBUY対象から除外",True)
    universe=st.text_area("分析対象4桁コード",DEFAULT,height=180)

def norm_code(x): return str(x).strip().upper().replace(".T","")
def yf_ticker(x): return norm_code(x)+".T"
def stock_name(x): return STOCK_NAMES.get(norm_code(x),norm_code(x))
def parse_codes(s):
    out=[]
    for x in str(s).replace("\n",",").replace(" ",",").split(","):
        c=norm_code(x)
        if c.isdigit() and len(c) in (4,5): out.append(c)
    return list(dict.fromkeys(out))
def csv_bytes(df): return df.to_csv(index=False,encoding="utf-8-sig").encode("utf-8-sig")

@st.cache_data(ttl=1800)
def stock_data(c,years):
    try:
        end=datetime.now(); start=end-timedelta(days=365*years+300)
        d=yf.download(yf_ticker(c),start=start,end=end+timedelta(days=1),
                      auto_adjust=False,progress=False,threads=False)
        if d.empty: return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
        cols=["Open","High","Low","Close","Volume"]
        if not all(x in d.columns for x in cols): return pd.DataFrame()
        d=d[cols].copy()
        for x in cols: d[x]=pd.to_numeric(d[x],errors="coerce")
        d["MA25"]=d.Close.rolling(25).mean(); d["MA75"]=d.Close.rolling(75).mean()
        d["MA200"]=d.Close.rolling(200).mean()
        d["MA25_Slope"]=d.MA25-d.MA25.shift(5); d["MA75_Slope"]=d.MA75-d.MA75.shift(5)
        d["VOL20"]=d.Volume.rolling(20).mean(); d["Turnover"]=d.Close*d.Volume
        d["Ret5"]=d.Close.pct_change(5)*100; d["Ret10"]=d.Close.pct_change(10)*100
        delta=d.Close.diff(); gain=delta.clip(lower=0).rolling(14).mean()
        loss=(-delta.clip(upper=0)).rolling(14).mean()
        rs=gain/loss.replace(0,np.nan); d["RSI"]=100-(100/(1+rs))
        d["VolRatio"]=d.Volume/d.VOL20.replace(0,np.nan)
        d["MA25Gap"]=(d.Close/d.MA25-1)*100
        return d.replace([np.inf,-np.inf],np.nan).dropna()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=1800)
def market_data():
    try:
        end=datetime.now(); start=end-timedelta(days=365*3+300)
        d=yf.download("^N225",start=start,end=end+timedelta(days=1),
                      auto_adjust=False,progress=False,threads=False)
        if d.empty:return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex):d.columns=d.columns.get_level_values(0)
        c=pd.to_numeric(d["Close"],errors="coerce")
        o=pd.DataFrame({"Close":c}); o["MA25"]=c.rolling(25).mean()
        o["MA75"]=c.rolling(75).mean(); o["MA200"]=c.rolling(200).mean()
        o["MA25_Slope"]=o.MA25-o.MA25.shift(5)
        return o.dropna()
    except Exception:return pd.DataFrame()

@st.cache_data(ttl=1800)
def overseas_data():
    end=datetime.now(); start=end-timedelta(days=365*3+300)
    syms={"SP500":"^GSPC","NASDAQ":"^IXIC","SOX":"^SOX","USDJPY":"USDJPY=X","US10Y":"^TNX"}
    out={}
    for k,s in syms.items():
        try:
            d=yf.download(s,start=start,end=end+timedelta(days=1),auto_adjust=False,
                          progress=False,threads=False)
            if d.empty:continue
            if isinstance(d.columns,pd.MultiIndex):d.columns=d.columns.get_level_values(0)
            out[k]=pd.to_numeric(d["Close"],errors="coerce")
        except Exception:pass
    return pd.concat(out,axis=1).sort_index().ffill() if out else pd.DataFrame()

def env_factor(m,o):
    if m.empty: ms,mf="⚪ データなし",.60
    else:
        r=m.iloc[-1]
        p=int(r.Close>r.MA25)+int(r.MA25>r.MA75)+int(r.MA75>r.MA200)+int(r.MA25_Slope>0)
        ms,mf=[("🔴 弱気",0),("🟠 やや弱気",.35),("⚪ 中立",.60),
               ("🟡 やや強気",.84),("🟢 強気",1.0)][p]
    if o.empty:return ms,mf,"⚪ 海外データなし",.60
    vals={}
    for c in ["SP500","NASDAQ","SOX","USDJPY","US10Y"]:
        s=o[c].dropna() if c in o.columns else pd.Series(dtype=float)
        if len(s)>=6: vals[c]=(s.iloc[-1]/s.iloc[-6]-1)*100
    x=0
    for c,w in [("SP500",.25),("NASDAQ",.25),("SOX",.20),("USDJPY",.20)]:
        x+=w*(1 if vals.get(c,0)>0 else -1 if vals.get(c,0)<0 else 0)
    x+=.10*(-1 if vals.get("US10Y",0)>0 else 1 if vals.get("US10Y",0)<0 else 0)
    of=float(np.clip(.75+x,.45,1.15))
    os="🟢 良好" if of>=1 else "🟡 やや良好" if of>=.85 else "⚪ 中立" if of>=.70 else "🔴 注意"
    return ms,mf,os,of

def tech_score(r,lo,hi):
    return float(18*(r.MA25>r.MA75)+18*(r.Close>r.MA200)+14*(r.Close>r.MA25)+
                 14*(r.Volume>=r.VOL20)+14*(lo<=r.RSI<=hi)+10*(r.MA25_Slope>0)+
                 6*(r.MA75_Slope>0)+3*(r.Ret5>0)+3*(r.Ret10>0))

def surge_score(r):
    s=0
    s+=20 if r.VolRatio>=1.5 else 12 if r.VolRatio>=1.2 else 6 if r.VolRatio>=1 else 0
    s+=18 if 2<=r.Ret5<=12 else 8 if 0<r.Ret5<2 else 5 if r.Ret5>12 else 0
    s+=15 if 3<=r.Ret10<=20 else 7 if 0<r.Ret10<3 else 3 if r.Ret10>20 else 0
    s+=15 if 45<=r.RSI<=70 else 8 if 70<r.RSI<=78 else 0
    s+=15 if 0<r.MA25Gap<=8 else 6 if r.MA25Gap<0 else 3
    s+=7*(r.MA25_Slope>0)+5*(r.MA75_Slope>0)
    return float(np.clip(s,0,100))

def surge_label(s):
    return "🔴 OVERHEAT" if s>=85 else "🔵 PRE-BUY CONFIRMED" if s>=70 else "🔵 PRE-BUY EARLY" if s>=55 else "⚪ WATCH"

def final_label(ai,surge):
    if surge>=85:return "🔴 OVERHEAT"
    if ai>=82 and surge>=70:return "🟢 BUY + 急騰監視"
    if ai>=82:return "🟢 通常BUY候補"
    if surge>=70:return "🔵 PRE-BUY"
    if ai>=72 or surge>=55:return "🟡 WATCH"
    return "⚪ 見送り"

codes=parse_codes(universe)
st.title("📈 日本株 AI投資アシスタント Ver.5.5 RC3.11")
st.caption(f"BUILD: {BUILD}")
if not codes: st.error("銘柄コードを入力してください。"); st.stop()

with st.spinner(f"🧠 {len(codes)}銘柄を分析中…"):
    data={c:stock_data(c,years) for c in codes}; data={c:d for c,d in data.items() if not d.empty}
    market=market_data(); overseas=overseas_data()
ms,mf,os,of=env_factor(market,overseas)

rows=[]
for c,d in data.items():
    r=d.iloc[-1]; tech=tech_score(r,rlo,rhi); surge=surge_score(r)
    ai=tech*.70+surge*.15+mf*8+of*7
    if r.RSI>=82:ai-=8
    if r.MA25Gap>=15:ai-=8
    if r.Ret5>=20:ai-=6
    if r.Turnover>=1e9:ai+=3
    elif r.Turnover>=1e8:ai+=1
    ai=float(np.clip(ai,0,100))
    rows.append({"コード":c,"銘柄名":stock_name(c),"株価":float(r.Close),
                 "AI総合スコア":ai,"テクニカルスコア":tech,
                 "急騰予兆スコア":surge,"急騰判定":surge_label(surge),
                 "最終判定":final_label(ai,surge),"RSI":float(r.RSI),
                 "5日騰落率":float(r.Ret5),"10日騰落率":float(r.Ret10),
                 "出来高倍率":float(r.VolRatio),"25日線乖離率":float(r.MA25Gap),
                 "平均売買代金":float(r.Turnover),"市場判定":ms,"市場係数":mf,
                 "海外判定":os,"海外係数":of})

all_ai=pd.DataFrame(rows)
if all_ai.empty:st.error("取得可能なデータがありません。");st.stop()
all_ai=all_ai.sort_values(["AI総合スコア","急騰予兆スコア"],ascending=False).reset_index(drop=True)
all_ai["AI総合順位"]=all_ai.index+1
ai_top50=all_ai.head(topn).copy()

old=all_ai.sort_values("平均売買代金",ascending=False).reset_index(drop=True)
old["旧方式順位"]=old.index+1; old50=old.head(50).copy()
a,b=set(ai_top50.コード),set(old50.コード); both=a&b; ai_only=a-b; old_only=b-a

st.header("① 🏆 AIお勧めTOP50")
c1,c2,c3,c4=st.columns(4)
c1.metric("分析銘柄数",len(all_ai));c2.metric("AI TOP50",len(ai_top50))
c3.metric("旧TOP50との共通",len(both));c4.metric("AI TOP50のみ",len(ai_only))
st.write(f"市場：**{ms}** ｜ 海外：**{os}**")

# ★ requested display order: 銘柄名の直隣に最終判定
display_cols=["AI総合順位","コード","銘柄名","最終判定","株価","AI総合スコア",
              "テクニカルスコア","急騰予兆スコア","急騰判定","RSI",
              "5日騰落率","出来高倍率","25日線乖離率"]
st.dataframe(ai_top50[display_cols],use_container_width=True,hide_index=True)

st.header("② 🥇 今日の最終候補 TOP3")
pool=ai_top50.copy()
if exclude2000:pool=pool[pool.株価<2000]
pool=pool[~pool.最終判定.isin(["🔴 OVERHEAT","⚪ 見送り"])]
pool=pool.sort_values(["AI総合スコア","急騰予兆スコア"],ascending=False)
if pool.empty:st.info("💤 今日は無理にBUYしません。")
else:
    for i,(_,r) in enumerate(pool.head(3).iterrows()):
        medal=["🥇","🥈","🥉"][i]
        st.success(f"{medal} **{r['銘柄名']}（{r['コード']}）** ｜ **{r['最終判定']}** ｜ AI {r['AI総合スコア']:.0f}点 ｜ 急騰予兆 {r['急騰予兆スコア']:.0f}点")

st.header("③ 🔵 急騰予兆")
surge=all_ai[(all_ai.急騰予兆スコア>=55)&(all_ai.急騰予兆スコア<85)].sort_values("急騰予兆スコア",ascending=False)
st.dataframe(surge[["コード","銘柄名","最終判定","急騰予兆スコア","急騰判定",
                    "AI総合スコア","株価","RSI","5日騰落率","出来高倍率","25日線乖離率"]].head(20),
             use_container_width=True,hide_index=True)

st.header("④ 🔍 AI TOP50 vs 旧・売買代金TOP50")
comparison=pd.DataFrame({"項目":["全分析銘柄数","AI TOP50","旧TOP50","両方","AIのみ","旧のみ"],
                         "値":[len(all_ai),len(a),len(b),len(both),len(ai_only),len(old_only)]})
st.dataframe(comparison,use_container_width=True,hide_index=True)

all_ai["旧TOP50採用"]=all_ai.コード.isin(b);all_ai["AIのみ"]=all_ai.コード.isin(ai_only);all_ai["旧のみ"]=all_ai.コード.isin(old_only)
settings=pd.DataFrame({"項目":["BUILD","未来情報","AI TOP50","旧TOP50","急騰予兆","2,000円以上BUY"],
                       "設定":[BUILD,"不使用",f"上位{topn}","平均売買代金上位50",
                               "当日までの価格・出来高・RSI等","除外" if exclude2000 else "許可"]})
files={"00_summary.csv":comparison,"01_ai_top50.csv":ai_top50,"02_all_ai_screening.csv":all_ai,
       "03_old_turnover_top50.csv":old50,"04_ai_only.csv":all_ai[all_ai["AIのみ"]],
       "05_old_only.csv":all_ai[all_ai["旧のみ"]],"06_overlap.csv":all_ai[all_ai.コード.isin(both)],
       "07_surge_candidates.csv":surge,"08_buy_top3.csv":pool.head(3),"09_settings.csv":settings}
buf=BytesIO()
with ZipFile(buf,"w") as z:
    for fn,df in files.items():z.writestr(fn,csv_bytes(df))
buf.seek(0)
st.download_button("📦 RC3.11 全分析CSVをZIPでダウンロード",data=buf.getvalue(),
                   file_name="ver5_5_RC3_11_AI_TOP50_analysis.zip",
                   mime="application/zip",use_container_width=True)
st.caption("※RC3.11は検証版。日々のCSVを蓄積し、重み・閾値を実績で検証します。")
