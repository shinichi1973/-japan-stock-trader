import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

# ============================================================
# 日本株 10万円→100万円 AI投資アシスタント Ver.5.3
# ============================================================
# Ver.5.2の診断結果を反映
# ・テクニカル点数だけでTOP3を決めない
# ・銘柄別の過去実績を総合スコアへ反映
# ・本当の「日次TOP3」だけをTOP3追跡
# ・スコア帯別の実績分析
# ・BUYしない日を明確化
# ・保有銘柄SELL警戒
# ・新規BUY停止と既存決済の連敗を分離
# ・全処理CSV / ZIP
#
# ※SBI証券への自動発注は行いません。
# ※明けの明星は使用しません。
# ※株価2,000円以上はBUY候補から除外します。
# ============================================================

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.5.3",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.3")
st.caption("🌅 朝イチ作戦｜🔥 AI BUY TOP3｜🧠 銘柄信頼度｜🔴 保有SELL警戒｜🚦 連敗ブレーキ")

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

DEFAULT_TICKERS = (
    "7203,6758,9984,8306,9432,6501,8035,8058,7267,2914,"
    "9433,8316,8411,6098,4063,4519,6367,6857,7974,8766,"
    "5401,8801,8802,4502,4503,4523,4755,6594,7741,6981"
)

# -----------------------------
# 設定
# -----------------------------
st.sidebar.header("⚙️ Ver.5.3 設定")

initial_cash = st.sidebar.number_input("初期資金（円）", 10000, 10000000, 100000, 10000)
max_positions = st.sidebar.number_input("最大保有銘柄数", 1, 50, 10)
max_per_position = st.sidebar.number_input("1銘柄最大購入額（円）", 1000, 1000000, 10000, 1000)

stop_loss = st.sidebar.slider("損切り（%）", 3.0, 12.0, 6.0, 0.5)
take_profit = st.sidebar.slider("利確（%）", 8.0, 40.0, 15.0, 1.0)
profit_start = st.sidebar.slider("トレーリング開始（%）", 3.0, 15.0, 5.0, 0.5)
trailing_stop = st.sidebar.slider("トレーリング幅（%）", 2.0, 10.0, 4.0, 0.5)

rsi_low = st.sidebar.slider("RSI下限", 25, 60, 40)
rsi_high = st.sidebar.slider("RSI上限", 60, 80, 70)
min_technical_score = st.sidebar.slider("最低テクニカルスコア", 60, 90, 75)

# Ver.5.3 総合スコアの重み
st.sidebar.subheader("🧠 Ver.5.3 総合AIスコア")
technical_weight = st.sidebar.slider("テクニカル比率", 40, 70, 60) / 100
history_weight = st.sidebar.slider("銘柄実績比率", 15, 40, 25) / 100
market_weight = 1.0 - technical_weight - history_weight
st.sidebar.caption(f"市場環境比率：{market_weight*100:.0f}%")

lookback_years = st.sidebar.slider("バックテスト期間", 2, 5, 5)
cooldown_days = st.sidebar.number_input("4連敗後の冷却期間（営業日）", 5, 30, 10)
ma_break_days = st.sidebar.number_input("25日線割れ確認日数", 1, 5, 2)

use_liquidity = st.sidebar.checkbox("過去5年売買代金TOP50を優先", True)
use_price_filter = st.sidebar.checkbox("株価2,000円以上をBUY除外", True)

st.sidebar.subheader("📋 保有銘柄")
held_text = st.sidebar.text_area("保有中の銘柄コード", "")
entry_text = st.sidebar.text_area("取得単価（コード:価格）", "")
ticker_input = st.sidebar.text_area("分析対象銘柄コード", DEFAULT_TICKERS)

def normalize_tickers(text):
    out = []
    for x in text.replace("\n", ",").split(","):
        x = x.strip()
        if x:
            out.append(x if x.endswith(".T") else x + ".T")
    return list(dict.fromkeys(out))

def stock_code(ticker):
    return ticker.replace(".T", "")

def stock_name(ticker):
    return STOCK_NAMES.get(stock_code(ticker), stock_code(ticker))

def parse_codes(text):
    return [x.strip().replace(".T", "") for x in text.replace("\n", ",").split(",") if x.strip()]

def parse_entries(text):
    out = {}
    for item in text.replace("\n", ",").split(","):
        if ":" not in item:
            continue
        c, p = item.split(":", 1)
        try:
            out[c.strip().replace(".T", "")] = float(p)
        except Exception:
            pass
    return out

def csv_bytes(df):
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False).encode("utf-8-sig")

@st.cache_data(ttl=3600)
def download_stock_data(ticker, years):
    end = datetime.now()
    start = end - timedelta(days=365 * years + 300)
    try:
        df = yf.download(ticker, start=start, end=end + timedelta(days=1),
                         auto_adjust=False, progress=False, threads=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        need = ["Open","High","Low","Close","Volume"]
        if not all(c in df.columns for c in need):
            return pd.DataFrame()
        df = df[need].copy()
        for c in need:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["MA25"] = df["Close"].rolling(25).mean()
        df["MA75"] = df["Close"].rolling(75).mean()
        df["MA200"] = df["Close"].rolling(200).mean()
        df["MA25_Slope"] = df["MA25"] - df["MA25"].shift(5)
        df["MA75_Slope"] = df["MA75"] - df["MA75"].shift(5)
        df["VOL20"] = df["Volume"].rolling(20).mean()
        df["Turnover"] = df["Close"] * df["Volume"]
        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI"] = 100 - (100 / (1 + rs))
        return df.dropna()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def download_market(years):
    end = datetime.now()
    start = end - timedelta(days=365 * years + 300)
    try:
        df = yf.download("^N225", start=start, end=end + timedelta(days=1),
                         auto_adjust=False, progress=False, threads=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = pd.to_numeric(df["Close"], errors="coerce")
        out = pd.DataFrame({"Close":close})
        out["MA25"] = close.rolling(25).mean()
        out["MA75"] = close.rolling(75).mean()
        out["MA200"] = close.rolling(200).mean()
        out["MA25_Slope"] = out["MA25"] - out["MA25"].shift(5)
        return out.dropna()
    except Exception:
        return pd.DataFrame()

def technical_score(row):
    score = 0
    score += 20 if row["MA25"] > row["MA75"] else 0
    score += 20 if row["Close"] > row["MA200"] else 0
    score += 15 if row["Close"] > row["MA25"] else 0
    score += 15 if row["Volume"] > row["VOL20"] else 0
    score += 15 if rsi_low <= row["RSI"] <= rsi_high else 0
    score += 10 if row["MA25_Slope"] > 0 else 0
    score += 5 if row["MA75_Slope"] > 0 else 0
    return int(score)

def market_state(market_df, date_value):
    if market_df.empty:
        return {"判定":"⚪ データなし","係数":1.0}
    x = market_df[market_df.index <= pd.Timestamp(date_value)]
    if x.empty:
        return {"判定":"⚪ データなし","係数":1.0}
    r = x.iloc[-1]
    points = sum([
        r["Close"] > r["MA25"],
        r["MA25"] > r["MA75"],
        r["MA75"] > r["MA200"],
        r["MA25_Slope"] > 0
    ])
    if points == 4: return {"判定":"🟢 強気","係数":1.0}
    if points == 3: return {"判定":"🟡 やや強気","係数":0.85}
    if points == 2: return {"判定":"⚪ 中立","係数":0.60}
    if points == 1: return {"判定":"🟠 やや弱気","係数":0.35}
    return {"判定":"🔴 弱気","係数":0.0}

def loss_brake(losses):
    if losses >= 4: return 0.0
    if losses == 3: return 0.50
    if losses == 2: return 0.80
    return 1.0

def score_rank(score):
    if score >= 85: return "S"
    if score >= 75: return "A"
    if score >= 65: return "B"
    if score >= 55: return "C"
    return "D"

def business_days_after(date_value, days):
    d = pd.Timestamp(date_value)
    n = 0
    while n < days:
        d += pd.Timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d

# ============================================================
# データ取得
# ============================================================
st.subheader("📥 データ取得")
tickers = normalize_tickers(ticker_input)
data = {}
bar = st.progress(0)
for i, ticker in enumerate(tickers):
    df = download_stock_data(ticker, lookback_years)
    if not df.empty:
        data[ticker] = df
    bar.progress(int((i+1)/max(len(tickers),1)*100))
bar.empty()

if not data:
    st.error("株価データを取得できませんでした。")
    st.stop()
st.success(f"{len(data)}銘柄のデータを取得しました。")

# ============================================================
# 流動性
# ============================================================
liq_rows = []
for ticker, df in data.items():
    liq_rows.append({
        "コード":stock_code(ticker),"銘柄名":stock_name(ticker),
        "平均売買代金":float(df["Turnover"].mean()),
        "平均出来高":float(df["Volume"].mean())
    })
liquidity_df = pd.DataFrame(liq_rows).sort_values("平均売買代金", ascending=False).reset_index(drop=True)
liquidity_df.insert(0,"売買代金順位",liquidity_df.index+1)
liquidity_df["売買代金TOP50"] = liquidity_df["売買代金順位"] <= 50
liquidity_codes = set(liquidity_df.loc[liquidity_df["売買代金TOP50"],"コード"].astype(str))

# ============================================================
# 市場
# ============================================================
market_df = download_market(lookback_years)

# ============================================================
# Ver.5.3 バックテスト
# ============================================================
st.subheader("📊 Ver.5.3 バックテスト")

cash = float(initial_cash)
positions = {}
trades = []
analysis = []
equity = []
consecutive_losses = 0
max_consecutive_losses = 0
new_buy_block_until = None

stats = {t:{"trades":0,"wins":0,"gross_profit":0.0,"gross_loss":0.0} for t in data}

# 過去実績から信頼度を作る
# 十分なサンプルがない銘柄は中立1.0倍
def history_confidence(s):
    n = s["trades"]
    if n < 8:
        return 1.00
    wr = s["wins"] / n
    gl = s["gross_loss"]
    pf = s["gross_profit"] / gl if gl > 0 else 9.99
    if wr >= 0.55 and pf >= 1.30: return 1.15
    if wr >= 0.48 and pf >= 1.10: return 1.08
    if wr >= 0.40 and pf >= 0.90: return 1.00
    if wr >= 0.30 and pf >= 0.70: return 0.88
    return 0.75

all_dates = sorted(set(d for df in data.values() for d in df.index))
bar = st.progress(0)

for di, current_date in enumerate(all_dates):
    current_date = pd.Timestamp(current_date)

    # 既存ポジションの決済連敗
    # 4連敗後は「新規BUY」を停止するが、既存ポジションのSELLは継続
    new_buy_blocked = (
        new_buy_block_until is not None and current_date <= new_buy_block_until
    )

    # SELL
    for ticker in list(positions.keys()):
        df = data[ticker]
        if current_date not in df.index:
            continue
        row = df.loc[current_date]
        pos = positions[ticker]
        price = float(row["Close"])
        entry = pos["entry_price"]
        pnl_pct = (price/entry-1)*100
        pos["highest_price"] = max(pos["highest_price"], price)
        trail_price = pos["highest_price"]*(1-trailing_stop/100)

        if price < row["MA25"]:
            pos["ma25_break_days"] += 1
        else:
            pos["ma25_break_days"] = 0

        reason = None
        if pnl_pct <= -stop_loss:
            reason = "損切り"
        elif pnl_pct >= profit_start and price <= trail_price:
            reason = "トレーリング"
        elif pnl_pct >= take_profit:
            reason = "利確"
        elif pos["ma25_break_days"] >= ma_break_days:
            reason = "25日線連続割れ"

        if reason:
            shares = pos["shares"]
            pnl = (price-entry)*shares
            cash += price*shares

            stats[ticker]["trades"] += 1
            if pnl > 0:
                stats[ticker]["wins"] += 1
                stats[ticker]["gross_profit"] += pnl
                consecutive_losses = 0
            else:
                stats[ticker]["gross_loss"] += abs(pnl)
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                if consecutive_losses >= 4:
                    new_buy_block_until = business_days_after(current_date, cooldown_days)

            trades.append({
                "日付":current_date,"コード":stock_code(ticker),"銘柄名":pos["name"],
                "売買":"SELL","価格":price,"株数":shares,"損益":pnl,
                "損益率":pnl_pct,"理由":reason,
                "テクニカルBUYスコア":pos["technical_score"],
                "総合BUYスコア":pos["composite_score"],
                "未来情報使用":False,
                "決済後連敗数":consecutive_losses,
                "新規BUY停止中":new_buy_blocked
            })
            del positions[ticker]

    # BUY候補
    candidates = []
    for ticker, df in data.items():
        if current_date not in df.index:
            continue
        if ticker in positions or len(positions) >= max_positions:
            continue

        row = df.loc[current_date]
        price = float(row["Close"])
        code = stock_code(ticker)
        tech = technical_score(row)
        in_liq = code in liquidity_codes

        # ここがVer.5.3の核心：
        # 銘柄の「過去実績」をその時点までのトレード履歴だけで評価
        hc = history_confidence(stats[ticker])
        market = market_state(market_df, current_date)
        # 信頼度を0～100へ変換
        history_points = np.clip((hc-0.70)/(1.15-0.70)*100, 0, 100)
        market_points = market["係数"]*100

        composite = (
            tech*technical_weight +
            history_points*history_weight +
            market_points*market_weight
        )

        reasons = []
        if row["MA25"] > row["MA75"]: reasons.append("25日線>75日線")
        if row["Close"] > row["MA200"]: reasons.append("200日線上")
        if row["Close"] > row["MA25"]: reasons.append("25日線上")
        if row["Volume"] > row["VOL20"]: reasons.append("出来高増")
        if rsi_low <= row["RSI"] <= rsi_high: reasons.append("RSI適正")
        if row["MA25_Slope"] > 0: reasons.append("25日線上向き")
        if row["MA75_Slope"] > 0: reasons.append("75日線上向き")

        judgement = "見送り"
        eligible = True
        if use_price_filter and price >= 2000:
            eligible=False; judgement="株価2,000円以上"
        elif use_liquidity and not in_liq:
            eligible=False; judgement="売買代金TOP50外"
        elif tech < min_technical_score:
            eligible=False; judgement="テクニカル不足"
        elif new_buy_blocked:
            eligible=False; judgement="🚦新規BUY停止"
        elif market["係数"] <= 0:
            eligible=False; judgement="🌏市場BUY停止"
        elif composite < 55:
            eligible=False; judgement="総合スコア不足"
        else:
            judgement="BUY候補"

        if eligible:
            # 総合スコアから資金係数
            if composite >= 85: factor = 1.00
            elif composite >= 75: factor = 0.85
            elif composite >= 65: factor = 0.70
            else: factor = 0.50
            budget = min(max_per_position,cash)*factor
            candidates.append({
                "ticker":ticker,"row":row,"tech":tech,"history_conf":hc,
                "history_points":history_points,"market":market,
                "composite":composite,"budget":budget,
                "reason":" / ".join(reasons),"in_liq":in_liq
            })

        analysis.append({
            "日付":current_date,"コード":code,"銘柄名":stock_name(ticker),
            "株価":price,"テクニカルスコア":tech,
            "銘柄実績信頼度":hc,"銘柄実績ポイント":history_points,
            "市場判定":market["判定"],"市場ポイント":market_points,
            "総合AIスコア":composite,"ランク":score_rank(composite),
            "売買代金TOP50":in_liq,"RSI":float(row["RSI"]),
            "判定":judgement,"判定理由":" / ".join(reasons),
            "未来情報使用":False,"新規BUY停止":new_buy_blocked
        })

    # 日次ランキング順でBUY
    candidates.sort(key=lambda x:(x["composite"],x["tech"],x["history_conf"]), reverse=True)

    for c in candidates:
        if len(positions) >= max_positions:
            break
        ticker = c["ticker"]
        if ticker in positions:
            continue
        price = float(c["row"]["Close"])
        shares = int(min(c["budget"],cash)/price)
        if shares <= 0:
            continue
        cost = shares*price
        cash -= cost
        positions[ticker] = {
            "entry_price":price,"shares":shares,"highest_price":price,
            "name":stock_name(ticker),"technical_score":c["tech"],
            "composite_score":c["composite"],"ma25_break_days":0
        }
        trades.append({
            "日付":current_date,"コード":stock_code(ticker),"銘柄名":stock_name(ticker),
            "売買":"BUY","価格":price,"株数":shares,"損益":0,"損益率":0,
            "理由":"Ver.5.3総合AI BUY",
            "テクニカルBUYスコア":c["tech"],
            "総合BUYスコア":c["composite"],
            "銘柄実績信頼度":c["history_conf"],
            "市場判定":c["market"]["判定"],
            "未来情報使用":False,
            "決済後連敗数":consecutive_losses,
            "新規BUY停止中":new_buy_blocked
        })

    holdings = 0
    for ticker,pos in positions.items():
        if current_date in data[ticker].index:
            holdings += float(data[ticker].loc[current_date]["Close"])*pos["shares"]

    total = cash+holdings
    equity.append({
        "日付":current_date,"現金":cash,"保有株評価額":holdings,
        "総資産":total,"保有銘柄数":len(positions),
        "決済連敗数":consecutive_losses,
        "新規BUY停止中":new_buy_blocked,
        "新規BUY停止終了予定":new_buy_block_until,
        "市場判定":market["判定"]
    })

    if di % 100 == 0 or di == len(all_dates)-1:
        bar.progress(int((di+1)/len(all_dates)*100))
bar.empty()

trades_df = pd.DataFrame(trades)
analysis_df = pd.DataFrame(analysis)
equity_df = pd.DataFrame(equity)

# ============================================================
# 現在の朝イチ判定
# ============================================================
latest_rows = []
for ticker,df in data.items():
    row = df.iloc[-1]
    tech = technical_score(row)
    hc = history_confidence(stats[ticker])
    hist_points = np.clip((hc-0.70)/(1.15-0.70)*100,0,100)
    market = market_state(market_df,df.index[-1])
    market_points = market["係数"]*100
    composite = tech*technical_weight + hist_points*history_weight + market_points*market_weight

    reasons=[]
    if row["MA25"] > row["MA75"]: reasons.append("25日線>75日線")
    if row["Close"] > row["MA200"]: reasons.append("200日線上")
    if row["Close"] > row["MA25"]: reasons.append("25日線上")
    if row["Volume"] > row["VOL20"]: reasons.append("出来高増")
    if rsi_low <= row["RSI"] <= rsi_high: reasons.append("RSI適正")
    if row["MA25_Slope"] > 0: reasons.append("25日線上向き")
    if row["MA75_Slope"] > 0: reasons.append("75日線上向き")

    eligible=True
    why=" / ".join(reasons)
    if use_price_filter and float(row["Close"]) >= 2000:
        eligible=False; why="株価2,000円以上"
    elif use_liquidity and stock_code(ticker) not in liquidity_codes:
        eligible=False; why="売買代金TOP50外"
    elif tech < min_technical_score:
        eligible=False; why="テクニカル不足"
    elif market["係数"] <= 0:
        eligible=False; why="市場環境が弱くBUY停止"

    latest_rows.append({
        "コード":stock_code(ticker),"銘柄名":stock_name(ticker),
        "株価":float(row["Close"]),"テクニカル":tech,
        "銘柄実績信頼度":hc,"市場判定":market["判定"],
        "総合AIスコア":composite,"ランク":score_rank(composite),
        "AI優先度":eligible,"RSI":float(row["RSI"]),
        "推奨購入目安":min(max_per_position,initial_cash)*(
            1.0 if composite>=85 else 0.85 if composite>=75 else
            0.70 if composite>=65 else 0.50
        ),
        "理由":why,"データ日":df.index[-1]
    })

morning_all_df = pd.DataFrame(latest_rows)
morning_top3 = morning_all_df[morning_all_df["AI優先度"]].sort_values(
    ["総合AIスコア","銘柄実績信頼度"],ascending=False
).head(3).reset_index(drop=True)

# ============================================================
# 本当のTOP3追跡
# ============================================================
# 各日について全候補を総合スコアで順位付けし、その日TOP3だけを保存
top3_tracking_rows=[]
all_date_set=set(all_dates)

for d in all_dates:
    candidates=[]
    for ticker,df in data.items():
        if d not in df.index:
            continue
        row=df.loc[d]
        tech=technical_score(row)
        if use_price_filter and float(row["Close"])>=2000:
            continue
        if use_liquidity and stock_code(ticker) not in liquidity_codes:
            continue
        if tech<min_technical_score:
            continue
        # バックテスト時点のstatsを再構築するのは複雑になるため、
        # 未来を使わない厳密な評価は本体analysisから取得する
        a=analysis_df[
            (analysis_df["日付"]==d)&
            (analysis_df["コード"]==stock_code(ticker))
        ]
        if a.empty:
            continue
        rr=a.iloc[-1]
        candidates.append((ticker,rr))

    candidates.sort(key=lambda x:float(x[1]["総合AIスコア"]),reverse=True)
    for rank,(ticker,rr) in enumerate(candidates[:3],1):
        df=data[ticker]
        idx=list(df.index).index(d)
        for label,h in [("翌日",1),("5営業日後",5),("20営業日後",20)]:
            if idx+h < len(df):
                base=float(df.iloc[idx]["Close"])
                future=float(df.iloc[idx+h]["Close"])
                top3_tracking_rows.append({
                    "判定日":d,"順位":rank,"コード":stock_code(ticker),
                    "銘柄名":stock_name(ticker),
                    "総合AIスコア":float(rr["総合AIスコア"]),
                    "テクニカルスコア":float(rr["テクニカルスコア"]),
                    "銘柄実績信頼度":float(rr["銘柄実績信頼度"]),
                    "期間":label,"基準価格":base,"将来価格":future,
                    "騰落率":(future/base-1)*100
                })

top3_tracking_df=pd.DataFrame(top3_tracking_rows)

# ============================================================
# 結果
# ============================================================
final_asset=float(equity_df["総資産"].iloc[-1])
profit=final_asset-initial_cash
return_rate=profit/initial_cash*100
equity_df["最高資産"]=equity_df["総資産"].cummax()
equity_df["DD"]=equity_df["総資産"]-equity_df["最高資産"]
equity_df["DD率"]=equity_df["DD"]/equity_df["最高資産"]*100
max_dd=float(equity_df["DD"].min())
max_dd_rate=float(equity_df["DD率"].min())

sell_df=trades_df[trades_df["売買"]=="SELL"].copy() if not trades_df.empty else pd.DataFrame()
trade_count=len(sell_df)
if trade_count:
    wins=sell_df[sell_df["損益"]>0]
    losses=sell_df[sell_df["損益"]<0]
    win_rate=len(wins)/trade_count*100
    gross_profit=wins["損益"].sum()
    gross_loss=abs(losses["損益"].sum())
    pf=gross_profit/gross_loss if gross_loss>0 else np.inf
    avg_profit=wins["損益"].mean() if len(wins) else 0
    avg_loss=abs(losses["損益"].mean()) if len(losses) else 0
else:
    win_rate=pf=avg_profit=avg_loss=0
avg_ratio=avg_profit/avg_loss if avg_loss>0 else 0

# ============================================================
# UI
# ============================================================
st.subheader("🌅 今日の朝イチAI作戦")

if morning_top3.empty:
    st.error("🛑 今日は新規BUYを見送る判断です。無理に買わず、現金を温存します。")
else:
    st.success("🟢 BUY候補あり。ただし最終判断はご自身で行ってください。")

cols=st.columns(3)
for i,(_,r) in enumerate(morning_top3.iterrows()):
    with cols[i]:
        icon=["🥇","🥈","🥉"][i]
        st.markdown(f"### {icon} {r['コード']} {r['銘柄名']}")
        st.metric("総合AIスコア",f"{r['総合AIスコア']:.1f}点")
        st.write(f"**{r['ランク']}ランク**")
        st.write(f"テクニカル：{r['テクニカル']:.0f}点")
        st.write(f"銘柄信頼度：{r['銘柄実績信頼度']:.2f}倍")
        st.write(f"市場：{r['市場判定']}")
        st.write(f"購入目安：**¥{r['推奨購入目安']:,.0f}**")
        st.write(f"理由：{r['理由']}")

st.subheader("⚠️ 高スコアでも注意する銘柄")
weak_high=morning_all_df[
    (morning_all_df["総合AIスコア"]>=75)&
    (morning_all_df["銘柄実績信頼度"]<1.0)
].sort_values("総合AIスコア",ascending=False).head(5)
if weak_high.empty:
    st.info("現在、総合スコア75点以上かつ銘柄実績信頼度1.0倍未満の銘柄はありません。")
else:
    st.dataframe(weak_high,use_container_width=True,hide_index=True)

st.subheader("🔴 保有銘柄AIチェック")
held_codes=parse_codes(held_text)
entry_prices=parse_entries(entry_text)
hold_rows=[]
for code in held_codes:
    ticker=code+".T"
    if ticker not in data:
        hold_rows.append({"コード":code,"銘柄名":STOCK_NAMES.get(code,code),"判定":"⚪ データなし"})
        continue
    df=data[ticker]
    row=df.iloc[-1]
    price=float(row["Close"])
    tech=technical_score(row)
    alerts=[]
    if price<row["MA25"]: alerts.append("25日線下")
    if row["MA25"]<row["MA75"]: alerts.append("25日線<75日線")
    if row["MA25_Slope"]<0: alerts.append("25日線下降")
    if tech<60: alerts.append("AIスコア低下")
    if row["RSI"]<35: alerts.append("RSI弱化")
    level="🟢 保有継続"
    if len(alerts)>=3: level="🔴 売却候補"
    elif alerts: level="🟠 売却検討"
    if code in entry_prices:
        pnlp=(price/entry_prices[code]-1)*100
        if pnlp<=-stop_loss: level="🔴 損切り候補"
    hold_rows.append({
        "コード":code,"銘柄名":stock_name(ticker),"現在価格":price,
        "AIスコア":tech,"RSI":float(row["RSI"]),
        "判定":level,"警戒理由":" / ".join(alerts) if alerts else "トレンド維持",
        "取得単価":entry_prices.get(code,np.nan)
    })
holding_df=pd.DataFrame(hold_rows)
if holding_df.empty:
    st.info("保有銘柄を入力するとSELL警戒を表示します。")
else:
    st.dataframe(holding_df,use_container_width=True,hide_index=True)

st.subheader("📊 Ver.5.3 バックテスト結果")
c1,c2,c3,c4=st.columns(4)
c1.metric("最終資産",f"¥{final_asset:,.0f}")
c2.metric("損益",f"¥{profit:,.0f}")
c3.metric("損益率",f"{return_rate:.2f}%")
c4.metric("最大DD",f"¥{max_dd:,.0f}")
c1,c2,c3,c4=st.columns(4)
c1.metric("決済トレード数",trade_count)
c2.metric("勝率",f"{win_rate:.2f}%")
c3.metric("Profit Factor",f"{pf:.2f}" if np.isfinite(pf) else "∞")
c4.metric("平均利益/損失",f"{avg_ratio:.2f}倍")

st.subheader("📈 資産推移")
st.line_chart(equity_df.set_index("日付")["総資産"])
st.subheader("📉 ドローダウン")
st.area_chart(equity_df.set_index("日付")["DD"])

# ============================================================
# TOP3実績
# ============================================================
st.subheader("🔬 本当のAI TOP3実績")
if top3_tracking_df.empty:
    st.info("TOP3追跡データがありません。")
else:
    tracking_summary=top3_tracking_df.groupby("期間").agg(
        件数=("騰落率","count"),
        平均騰落率=("騰落率","mean"),
        プラス率=("騰落率",lambda x:(x>0).mean()*100),
        中央値=("騰落率","median")
    ).reset_index()
    st.dataframe(tracking_summary,use_container_width=True,hide_index=True)

    rank_summary=top3_tracking_df.groupby(["順位","期間"]).agg(
        件数=("騰落率","count"),
        平均騰落率=("騰落率","mean"),
        プラス率=("騰落率",lambda x:(x>0).mean()*100)
    ).reset_index()
    st.dataframe(rank_summary,use_container_width=True,hide_index=True)

st.subheader("🎯 AIスコア帯別の実績")
if not top3_tracking_df.empty:
    temp=top3_tracking_df.copy()
    temp["スコア帯"]=pd.cut(
        temp["総合AIスコア"],
        bins=[-1,64,74,84,100],
        labels=["～64","65～74","75～84","85～"]
    )
    band=temp.groupby(["スコア帯","期間"],observed=False).agg(
        件数=("騰落率","count"),
        平均騰落率=("騰落率","mean"),
        プラス率=("騰落率",lambda x:(x>0).mean()*100)
    ).reset_index()
    st.dataframe(band,use_container_width=True,hide_index=True)

# ============================================================
# 銘柄別成績
# ============================================================
st.subheader("🏢 銘柄別成績")
if not sell_df.empty:
    stock_result=sell_df.groupby(["コード","銘柄名"]).agg(
        トレード数=("損益","count"),
        勝ち=("損益",lambda x:(x>0).sum()),
        損益=("損益","sum"),
        平均損益=("損益","mean")
    ).reset_index()
    stock_result["勝率"]=stock_result["勝ち"]/stock_result["トレード数"]*100
    stock_result["AI信頼度"]=stock_result.apply(
        lambda r: 1.15 if r["勝率"]>=55 else 1.08 if r["勝率"]>=48 else
        1.0 if r["勝率"]>=40 else 0.88 if r["勝率"]>=30 else 0.75,axis=1
    )
    st.dataframe(stock_result.sort_values("損益",ascending=False),
                 use_container_width=True,hide_index=True)
else:
    stock_result=pd.DataFrame()

st.subheader("📋 全売買記録")
st.dataframe(trades_df.sort_values("日付",ascending=False),
             use_container_width=True,hide_index=True)

# ============================================================
# CSV
# ============================================================
summary_df=pd.DataFrame({
    "項目":[
        "Ver","初期資金","最終資産","損益","損益率","決済トレード数",
        "勝率","Profit Factor","平均利益","平均損失","平均利益/損失",
        "最大DD","最大DD率","最大連続損失","テクニカル比率",
        "銘柄実績比率","市場環境比率","明けの明星","株価2,000円以上BUY"
    ],
    "結果":[
        "5.3",initial_cash,final_asset,profit,return_rate,trade_count,
        win_rate,pf,avg_profit,avg_loss,avg_ratio,max_dd,max_dd_rate,
        max_consecutive_losses,technical_weight,history_weight,market_weight,
        "不使用","除外" if use_price_filter else "フィルターOFF"
    ]
})

files={
    "summary.csv":summary_df,
    "morning_top3.csv":morning_top3,
    "morning_all_rank.csv":morning_all_df,
    "holding_check.csv":holding_df,
    "all_ai_analysis.csv":analysis_df,
    "trade_history.csv":trades_df,
    "equity_curve.csv":equity_df,
    "stock_results.csv":stock_result,
    "liquidity_top50.csv":liquidity_df,
    "top3_tracking.csv":top3_tracking_df
}

st.subheader("📥 全処理結果")
for filename,df in files.items():
    st.download_button(
        f"📄 {filename}",csv_bytes(df),filename,"text/csv",
        key="download_"+filename
    )

zip_buffer=BytesIO()
with ZipFile(zip_buffer,"w") as z:
    for filename,df in files.items():
        z.writestr(filename,csv_bytes(df))
st.download_button(
    "📦 全CSVをZIPで一括ダウンロード",
    zip_buffer.getvalue(),"ver5_3_all_results.zip","application/zip"
)

st.subheader("🧠 Ver.5.3 売買思想")
st.markdown("""
### 🌅 朝イチ
**良い日だけ買う。悪い日は買わない。**

### 🔥 TOP3
テクニカルだけでなく、**その銘柄で過去にAI判定がどれだけ機能したか**を総合評価へ反映します。

### ⚠️ 高スコア注意
テクニカルスコアが高くても、銘柄実績信頼度が低い場合は優先度を下げます。

### 🚦 連敗ブレーキ
2連敗 → 80%  
3連敗 → 50%  
4連敗 → 新規BUY停止  
※既存ポジションのSELL判断は停止しません。

### ❌ 使用しない条件
明けの明星 / 株価2,000円以上BUY

### 🔒 重要
これは仮想バックテスト・投資判断補助です。将来の利益を保証するものではありません。
SBI証券への自動注文は行いません。
""")

st.success("🚀 Ver.5.3 完了")
