import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

# ============================================================
# 日本株 10万円→100万円 AI投資アシスタント Ver.5.2
# ============================================================
# 朝イチ作戦 / AI BUY TOP3 / 保有銘柄SELL警戒 /
# 市場環境 / 銘柄別AI信頼度 / 連敗ブレーキ /
# 全判定CSV / TOP3追跡
#
# 注意:
# ・これは投資判断の補助・仮想バックテスト用です。
# ・SBI証券への自動発注は行いません。
# ・「今日」は最新取得可能な市場データ日を意味します。
# ・明けの明星は使用しません。
# ・株価2,000円以上はBUY候補から除外します。
# ============================================================

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.5.2",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.2")
st.caption("🌅 朝イチ作戦｜🔥 AI BUY TOP3｜🔴 保有銘柄SELL警戒｜🚦 連敗ブレーキ｜📊 全判定CSV")

# -----------------------------
# 銘柄名
# -----------------------------
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
# サイドバー
# -----------------------------
st.sidebar.header("⚙️ 設定")

initial_cash = st.sidebar.number_input("初期資金（円）", 10000, 10000000, 100000, 10000)
max_positions = st.sidebar.number_input("最大保有銘柄数", 1, 50, 10)
max_per_position = st.sidebar.number_input("1銘柄最大購入額（円）", 1000, 1000000, 10000, 1000)

stop_loss = st.sidebar.slider("損切り（%）", 3.0, 12.0, 6.0, 0.5)
take_profit = st.sidebar.slider("利確（%）", 8.0, 40.0, 15.0, 1.0)
profit_start = st.sidebar.slider("トレーリング開始（%）", 3.0, 15.0, 5.0, 0.5)
trailing_stop = st.sidebar.slider("トレーリング幅（%）", 2.0, 10.0, 4.0, 0.5)

rsi_low = st.sidebar.slider("RSI下限", 25, 60, 40)
rsi_high = st.sidebar.slider("RSI上限", 60, 80, 70)
min_score = st.sidebar.slider("最低BUYスコア", 60, 90, 75)

lookback_years = st.sidebar.slider("バックテスト期間", 2, 5, 5)
cooldown_days = st.sidebar.number_input("4連敗後の冷却期間（営業日）", 5, 30, 10)
ma_break_days = st.sidebar.number_input("25日線割れ確認日数", 1, 5, 2)

use_liquidity = st.sidebar.checkbox("過去5年売買代金TOP50を優先", True)
use_price_filter = st.sidebar.checkbox("株価2,000円以上をBUY除外", True)

st.sidebar.subheader("📋 保有銘柄チェック")
held_text = st.sidebar.text_area(
    "保有中の銘柄コード（カンマ区切り）",
    value="",
    help="例：8306,9984,8411"
)

entry_text = st.sidebar.text_area(
    "任意：取得単価（コード:価格）",
    value="",
    help="例：8306:1200,9984:5000"
)

ticker_input = st.sidebar.text_area("分析対象銘柄コード", DEFAULT_TICKERS)

# -----------------------------
# ユーティリティ
# -----------------------------
def normalize_tickers(text):
    result = []
    for x in text.replace("\n", ",").split(","):
        x = x.strip()
        if not x:
            continue
        if not x.endswith(".T"):
            x += ".T"
        result.append(x)
    return list(dict.fromkeys(result))

def stock_code(ticker):
    return ticker.replace(".T", "")

def stock_name(ticker):
    code = stock_code(ticker)
    return STOCK_NAMES.get(code, code)

def parse_codes(text):
    return [x.strip().replace(".T", "") for x in text.replace("\n", ",").split(",") if x.strip()]

def parse_entries(text):
    out = {}
    for item in text.replace("\n", ",").split(","):
        if ":" not in item:
            continue
        code, price = item.split(":", 1)
        try:
            out[code.strip().replace(".T", "")] = float(price)
        except Exception:
            pass
    return out

def csv_bytes(df):
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False).encode("utf-8-sig")

# -----------------------------
# 株価データ
# -----------------------------
@st.cache_data(ttl=3600)
def download_stock_data(ticker, years):
    end = datetime.now()
    start = end - timedelta(days=365 * years + 300)
    try:
        df = yf.download(
            ticker, start=start, end=end + timedelta(days=1),
            auto_adjust=False, progress=False, threads=False
        )
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        need = ["Open", "High", "Low", "Close", "Volume"]
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
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["RSI"] = 100 - (100 / (1 + rs))
        return df.dropna()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def download_market(years):
    end = datetime.now()
    start = end - timedelta(days=365 * years + 300)
    try:
        df = yf.download(
            "^N225", start=start, end=end + timedelta(days=1),
            auto_adjust=False, progress=False, threads=False
        )
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = pd.to_numeric(df["Close"], errors="coerce")
        out = pd.DataFrame(index=close.index)
        out["Close"] = close
        out["MA25"] = close.rolling(25).mean()
        out["MA75"] = close.rolling(75).mean()
        out["MA200"] = close.rolling(200).mean()
        out["MA25_Slope"] = out["MA25"] - out["MA25"].shift(5)
        return out.dropna()
    except Exception:
        return pd.DataFrame()

# -----------------------------
# AIスコア
# -----------------------------
def ai_score(row):
    score = 0
    score += 20 if row["MA25"] > row["MA75"] else 0
    score += 20 if row["Close"] > row["MA200"] else 0
    score += 15 if row["Close"] > row["MA25"] else 0
    score += 15 if row["Volume"] > row["VOL20"] else 0
    score += 15 if rsi_low <= row["RSI"] <= rsi_high else 0
    score += 10 if row["MA25_Slope"] > 0 else 0
    score += 5 if row["MA75_Slope"] > 0 else 0
    return int(score)

def score_factor(score):
    if score >= 90: return 1.00
    if score >= 85: return 0.85
    if score >= 80: return 0.70
    if score >= 75: return 0.50
    return 0.0

def score_rank(score):
    if score >= 90: return "S"
    if score >= 85: return "A+"
    if score >= 80: return "A"
    if score >= 75: return "B"
    return "C"

def loss_brake(losses):
    if losses >= 4: return 0.0
    if losses == 3: return 0.50
    if losses == 2: return 0.80
    return 1.0

def market_state(market_df, date_value):
    if market_df.empty:
        return {"判定":"⚪ データなし", "係数":1.0, "points":0}
    a = market_df[market_df.index <= pd.Timestamp(date_value)]
    if a.empty:
        return {"判定":"⚪ データなし", "係数":1.0, "points":0}
    r = a.iloc[-1]
    points = sum([
        bool(r["Close"] > r["MA25"]),
        bool(r["MA25"] > r["MA75"]),
        bool(r["MA75"] > r["MA200"]),
        bool(r["MA25_Slope"] > 0)
    ])
    if points == 4: return {"判定":"🟢 強気", "係数":1.00, "points":points}
    if points == 3: return {"判定":"🟡 やや強気", "係数":0.85, "points":points}
    if points == 2: return {"判定":"⚪ 中立", "係数":0.60, "points":points}
    if points == 1: return {"判定":"🟠 やや弱気", "係数":0.35, "points":points}
    return {"判定":"🔴 弱気", "係数":0.0, "points":points}

def confidence_factor(stat):
    n = stat["trades"]
    if n < 10:
        return 1.00
    wr = stat["wins"] / n
    gl = stat["gross_loss"]
    pf = stat["gross_profit"] / gl if gl > 0 else 9.99
    if wr >= 0.55 and pf >= 1.30: return 1.10
    if wr >= 0.45 and pf >= 1.10: return 1.05
    if wr >= 0.40 and pf >= 0.90: return 1.00
    if wr >= 0.30 and pf >= 0.70: return 0.85
    return 0.70

def business_days_after(date_value, days):
    d = pd.Timestamp(date_value)
    count = 0
    while count < days:
        d += pd.Timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d

# -----------------------------
# データ取得
# -----------------------------
tickers = normalize_tickers(ticker_input)
st.subheader("📥 データ取得")

data = {}
p = st.progress(0)
for i, ticker in enumerate(tickers):
    df = download_stock_data(ticker, lookback_years)
    if not df.empty:
        data[ticker] = df
    p.progress(int((i + 1) / max(len(tickers), 1) * 100))
p.empty()

if not data:
    st.error("株価データを取得できませんでした。")
    st.stop()

st.success(f"{len(data)}銘柄のデータを取得しました。")

# -----------------------------
# 流動性TOP50
# -----------------------------
liq_rows = []
for ticker, df in data.items():
    liq_rows.append({
        "コード": stock_code(ticker),
        "銘柄名": stock_name(ticker),
        "平均売買代金": float(df["Turnover"].mean()),
        "平均出来高": float(df["Volume"].mean())
    })
liquidity_df = pd.DataFrame(liq_rows).sort_values("平均売買代金", ascending=False).reset_index(drop=True)
liquidity_df.insert(0, "売買代金順位", liquidity_df.index + 1)
liquidity_df["売買代金TOP50"] = liquidity_df["売買代金順位"] <= 50
liquidity_codes = set(liquidity_df.loc[liquidity_df["売買代金TOP50"], "コード"].astype(str))

st.subheader("💰 過去5年 平均売買代金TOP50")
st.dataframe(liquidity_df, use_container_width=True, hide_index=True)

# -----------------------------
# 市場
# -----------------------------
market_df = download_market(lookback_years)

# -----------------------------
# バックテスト
# -----------------------------
st.subheader("📊 Ver.5.2 バックテスト")
st.write("🔒 BUY判定は、その日までに利用可能だった情報だけを使用します。")

cash = float(initial_cash)
positions = {}
trades = []
analysis = []
equity = []
brake_history = []
stock_stats = {
    t: {"trades":0, "wins":0, "losses":0, "gross_profit":0.0, "gross_loss":0.0}
    for t in data
}
consecutive_losses = 0
max_consecutive_losses = 0
cooldown_until = None

all_dates = sorted(set(d for df in data.values() for d in df.index))
progress = st.progress(0)

for date_i, current_date in enumerate(all_dates):
    current_date = pd.Timestamp(current_date)

    cooling = cooldown_until is not None and current_date <= cooldown_until
    if cooldown_until is not None and current_date > cooldown_until:
        cooldown_until = None
        consecutive_losses = 0
        cooling = False

    brake = loss_brake(consecutive_losses)
    market = market_state(market_df, current_date)

    # SELL
    for ticker in list(positions.keys()):
        df = data[ticker]
        if current_date not in df.index:
            continue
        row = df.loc[current_date]
        pos = positions[ticker]
        price = float(row["Close"])
        entry = pos["entry_price"]
        shares = pos["shares"]
        pnl_pct = (price / entry - 1) * 100

        pos["highest_price"] = max(pos["highest_price"], price)
        trail_price = pos["highest_price"] * (1 - trailing_stop / 100)

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
            sell_value = price * shares
            cash += sell_value
            pnl = (price - entry) * shares

            stock_stats[ticker]["trades"] += 1
            if pnl > 0:
                stock_stats[ticker]["wins"] += 1
                stock_stats[ticker]["gross_profit"] += pnl
                consecutive_losses = 0
            else:
                stock_stats[ticker]["losses"] += 1
                stock_stats[ticker]["gross_loss"] += abs(pnl)
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                if consecutive_losses >= 4:
                    cooldown_until = business_days_after(current_date, cooldown_days)

            trades.append({
                "日付": current_date, "コード": stock_code(ticker), "銘柄名": pos["name"],
                "売買":"SELL", "価格":price, "株数":shares, "損益":pnl, "損益率":pnl_pct,
                "理由":reason, "BUYスコア":pos["score"], "BUY時信頼度":pos["confidence"],
                "売買代金TOP50":pos["liquidity_top50"], "未来情報使用":False,
                "連敗数":consecutive_losses
            })
            del positions[ticker]

    # BUY候補
    candidates = []
    for ticker, df in data.items():
        if current_date not in df.index or ticker in positions or len(positions) >= max_positions:
            continue

        row = df.loc[current_date]
        price = float(row["Close"])
        code = stock_code(ticker)
        in_liq = code in liquidity_codes
        score = ai_score(row)
        sf = score_factor(score)
        conf = confidence_factor(stock_stats[ticker])
        mf = market["係数"]
        brake_now = loss_brake(consecutive_losses)
        final_factor = min(sf * mf * brake_now * conf, 1.0)
        budget = min(max_per_position, cash) * final_factor

        reasons = []
        if row["MA25"] > row["MA75"]: reasons.append("25日線>75日線")
        if row["Close"] > row["MA200"]: reasons.append("200日線上")
        if row["Close"] > row["MA25"]: reasons.append("25日線上")
        if row["Volume"] > row["VOL20"]: reasons.append("出来高増")
        if rsi_low <= row["RSI"] <= rsi_high: reasons.append("RSI適正")
        if row["MA25_Slope"] > 0: reasons.append("25日線上向き")
        if row["MA75_Slope"] > 0: reasons.append("75日線上向き")

        if use_price_filter and price >= 2000:
            judgement = "❌ 株価2,000円以上"
        elif use_liquidity and not in_liq:
            judgement = "⚪ 売買代金TOP50外"
        elif score < min_score:
            judgement = "⚪ スコア不足"
        elif cooling or brake_now <= 0:
            judgement = "🚦 連敗ブレーキ"
        elif mf <= 0:
            judgement = "🌏 市場BUY停止"
        else:
            judgement = "🟢 BUY候補"
            candidates.append({
                "ticker":ticker, "row":row, "score":score, "budget":budget,
                "confidence":conf, "name":stock_name(ticker),
                "liquidity_top50":in_liq, "reasons":" / ".join(reasons)
            })

        analysis.append({
            "日付":current_date, "コード":code, "銘柄名":stock_name(ticker), "株価":price,
            "AIスコア":score, "ランク":score_rank(score), "RSI":float(row["RSI"]),
            "出来高":float(row["Volume"]), "20日平均出来高":float(row["VOL20"]),
            "売買代金TOP50":in_liq, "市場判定":market["判定"], "市場係数":mf,
            "過去トレード数":stock_stats[ticker]["trades"],
            "過去勝率":(stock_stats[ticker]["wins"]/stock_stats[ticker]["trades"]*100
                       if stock_stats[ticker]["trades"] else 0),
            "銘柄AI信頼度":conf, "連敗数":consecutive_losses,
            "ブレーキ係数":brake_now, "最終資金係数":final_factor,
            "購入可能額":budget, "判定":judgement, "判定理由":" / ".join(reasons),
            "未来情報使用":False
        })

    candidates.sort(key=lambda x:(x["score"], x["confidence"], x["liquidity_top50"]), reverse=True)

    # BUY
    for c in candidates:
        if len(positions) >= max_positions:
            break
        ticker = c["ticker"]
        if ticker in positions:
            continue
        price = float(c["row"]["Close"])
        budget = min(c["budget"], cash)
        shares = int(budget / price)  # S株想定
        if shares <= 0:
            continue
        cost = shares * price
        if cost > cash:
            continue
        cash -= cost
        positions[ticker] = {
            "entry_price":price, "shares":shares, "highest_price":price,
            "score":c["score"], "confidence":c["confidence"], "name":c["name"],
            "liquidity_top50":c["liquidity_top50"], "ma25_break_days":0
        }
        trades.append({
            "日付":current_date, "コード":stock_code(ticker), "銘柄名":c["name"],
            "売買":"BUY", "価格":price, "株数":shares, "損益":0, "損益率":0,
            "理由":"AI BUY", "BUYスコア":c["score"], "BUY時信頼度":c["confidence"],
            "売買代金TOP50":c["liquidity_top50"], "未来情報使用":False,
            "連敗数":consecutive_losses
        })

    holdings = 0
    for ticker, pos in positions.items():
        df = data[ticker]
        if current_date in df.index:
            holdings += float(df.loc[current_date]["Close"]) * pos["shares"]

    total_asset = cash + holdings
    equity.append({
        "日付":current_date, "現金":cash, "保有株評価額":holdings,
        "総資産":total_asset, "保有銘柄数":len(positions),
        "連敗数":consecutive_losses, "ブレーキ係数":brake,
        "冷却中":cooling, "市場判定":market["判定"]
    })
    brake_history.append({
        "日付":current_date, "連敗数":consecutive_losses,
        "ブレーキ係数":brake, "冷却中":cooling,
        "冷却終了予定":cooldown_until, "市場判定":market["判定"]
    })

    if date_i % 100 == 0 or date_i == len(all_dates)-1:
        progress.progress(int((date_i+1)/len(all_dates)*100))

progress.empty()

trades_df = pd.DataFrame(trades)
analysis_df = pd.DataFrame(analysis)
equity_df = pd.DataFrame(equity)
brake_df = pd.DataFrame(brake_history)

# -----------------------------
# 統計
# -----------------------------
final_asset = float(equity_df["総資産"].iloc[-1])
profit = final_asset - initial_cash
return_rate = profit / initial_cash * 100

equity_df["最高資産"] = equity_df["総資産"].cummax()
equity_df["DD"] = equity_df["総資産"] - equity_df["最高資産"]
equity_df["DD率"] = equity_df["DD"] / equity_df["最高資産"] * 100
max_dd = float(equity_df["DD"].min())
max_dd_rate = float(equity_df["DD率"].min())

sell_df = trades_df[trades_df["売買"]=="SELL"].copy() if not trades_df.empty else pd.DataFrame()
trade_count = len(sell_df)
if trade_count:
    wins = sell_df[sell_df["損益"] > 0]
    losses = sell_df[sell_df["損益"] < 0]
    win_rate = len(wins)/trade_count*100
    gross_profit = wins["損益"].sum()
    gross_loss = abs(losses["損益"].sum())
    profit_factor = gross_profit/gross_loss if gross_loss > 0 else np.inf
    avg_profit = wins["損益"].mean() if len(wins) else 0
    avg_loss = abs(losses["損益"].mean()) if len(losses) else 0
else:
    win_rate = profit_factor = avg_profit = avg_loss = 0
avg_ratio = avg_profit/avg_loss if avg_loss > 0 else 0

# -----------------------------
# 現在の朝イチランキング
# -----------------------------
latest_candidates = []
latest_date_map = {}
for ticker, df in data.items():
    latest = df.iloc[-1]
    latest_date_map[ticker] = df.index[-1]
    code = stock_code(ticker)
    price = float(latest["Close"])
    in_liq = code in liquidity_codes
    score = ai_score(latest)
    conf = confidence_factor(stock_stats[ticker])
    market_now = market_state(market_df, df.index[-1])
    sf = score_factor(score)
    mf = market_now["係数"]
    rank_factor = min(sf * mf * conf, 1.0)
    reasons = []
    if latest["MA25"] > latest["MA75"]: reasons.append("25日線>75日線")
    if latest["Close"] > latest["MA200"]: reasons.append("200日線上")
    if latest["Close"] > latest["MA25"]: reasons.append("25日線上")
    if latest["Volume"] > latest["VOL20"]: reasons.append("出来高増")
    if rsi_low <= latest["RSI"] <= rsi_high: reasons.append("RSI適正")
    if latest["MA25_Slope"] > 0: reasons.append("25日線上向き")
    if latest["MA75_Slope"] > 0: reasons.append("75日線上向き")
    eligible = True
    reason = " / ".join(reasons)
    if use_price_filter and price >= 2000:
        eligible = False
        reason = "株価2,000円以上"
    if use_liquidity and not in_liq:
        eligible = False
        reason = "売買代金TOP50外"
    if score < min_score:
        eligible = False
    if mf <= 0:
        eligible = False
    latest_candidates.append({
        "コード":code, "銘柄名":stock_name(ticker), "株価":price,
        "AIスコア":score, "ランク":score_rank(score),
        "AI信頼度":conf, "市場判定":market_now["判定"], "市場係数":mf,
        "最終資金係数":rank_factor, "推奨購入目安":min(max_per_position, initial_cash)*rank_factor,
        "RSI":float(latest["RSI"]), "理由":reason,
        "BUY候補":eligible, "データ日":df.index[-1]
    })

morning_df = pd.DataFrame(latest_candidates)
top3 = morning_df[morning_df["BUY候補"]].sort_values(
    ["AIスコア","AI信頼度"], ascending=False
).head(3).reset_index(drop=True)

# -----------------------------
# 朝イチ作戦
# -----------------------------
st.subheader("🌅 今日の朝イチAI作戦")

if top3.empty:
    st.error("🛑 今日は新規BUYを見送る候補日です。無理に買わず、現金を温存する判断を優先します。")
    st.caption("※データ上の条件に基づく自動判定であり、将来の利益を保証するものではありません。")
else:
    st.success(f"🟢 BUY候補あり｜最新データ日：{morning_df['データ日'].max().date()}")

cols = st.columns(3)
for i, (_, r) in enumerate(top3.iterrows()):
    with cols[i]:
        st.markdown(f"### {'🥇' if i==0 else '🥈' if i==1 else '🥉'} {r['コード']} {r['銘柄名']}")
        st.metric("AIスコア", f"{int(r['AIスコア'])}点")
        st.write(f"**{r['ランク']}ランク**｜AI信頼度 {r['AI信頼度']:.2f}倍")
        st.write(f"市場：{r['市場判定']}")
        st.write(f"購入目安：**¥{r['推奨購入目安']:,.0f}**")
        st.write(f"理由：{r['理由']}")

# -----------------------------
# 保有銘柄SELL警戒
# -----------------------------
st.subheader("🔴 保有銘柄AIチェック")

held_codes = parse_codes(held_text)
entry_prices = parse_entries(entry_text)
holding_rows = []

for code in held_codes:
    ticker = code + ".T"
    if ticker not in data:
        holding_rows.append({
            "コード":code, "銘柄名":STOCK_NAMES.get(code, code),
            "判定":"⚪ データなし", "理由":"対象データを取得できませんでした"
        })
        continue

    df = data[ticker]
    row = df.iloc[-1]
    price = float(row["Close"])
    score = ai_score(row)
    reasons = []
    alerts = []
    sell_level = "🟢 保有継続"

    if price < row["MA25"]:
        alerts.append("25日線下")
    if row["MA25"] < row["MA75"]:
        alerts.append("25日線<75日線")
    if row["MA25_Slope"] < 0:
        alerts.append("25日線下降")
    if score < 60:
        alerts.append("AIスコア低下")
    if row["RSI"] < 35:
        alerts.append("RSI弱化")

    if entry_prices.get(code):
        ep = entry_prices[code]
        pnlp = (price/ep - 1)*100
        if pnlp <= -stop_loss:
            alerts.append("損切りライン")
            sell_level = "🔴 売却候補"
        elif pnlp > 0 and price < row["MA25"]:
            sell_level = "🟠 売却検討"
    if len(alerts) >= 3:
        sell_level = "🔴 売却候補"
    elif len(alerts) >= 1:
        sell_level = "🟠 売却検討"

    holding_rows.append({
        "コード":code, "銘柄名":stock_name(ticker), "現在価格":price,
        "AIスコア":score, "RSI":float(row["RSI"]),
        "判定":sell_level, "警戒理由":" / ".join(alerts) if alerts else "トレンド維持",
        "取得単価":entry_prices.get(code, np.nan)
    })

holding_df = pd.DataFrame(holding_rows)
if holding_df.empty:
    st.info("保有銘柄を入力すると、毎朝のSELL警戒チェックを表示します。")
else:
    st.dataframe(holding_df, use_container_width=True, hide_index=True)

# -----------------------------
# バックテスト結果
# -----------------------------
st.subheader("📊 Ver.5.2 バックテスト結果")
c1,c2,c3,c4 = st.columns(4)
c1.metric("最終資産", f"¥{final_asset:,.0f}")
c2.metric("損益", f"¥{profit:,.0f}")
c3.metric("損益率", f"{return_rate:.2f}%")
c4.metric("最大DD", f"¥{max_dd:,.0f}")

c1,c2,c3,c4 = st.columns(4)
c1.metric("決済トレード数", trade_count)
c2.metric("勝率", f"{win_rate:.2f}%")
c3.metric("Profit Factor", f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "∞")
c4.metric("平均利益/損失", f"{avg_ratio:.2f}倍")

c1,c2,c3 = st.columns(3)
c1.metric("最大DD率", f"{max_dd_rate:.2f}%")
c2.metric("最大連続損失", f"{max_consecutive_losses}回")
c3.metric("明けの明星", "不使用")

# -----------------------------
# チャート
# -----------------------------
st.subheader("📈 資産推移")
chart = equity_df.copy().set_index("日付")
st.line_chart(chart["総資産"])

st.subheader("📉 ドローダウン")
st.area_chart(chart["DD"])

# -----------------------------
# TOP3検証
# -----------------------------
st.subheader("🔬 AI TOP3 追跡検証")
st.caption("各データ日で選ばれたTOP3について、翌日・5営業日後・20営業日後の価格変化を記録します。未来の結果はBUY判定には使用しません。")

tracking_rows = []
for ticker, df in data.items():
    dates = list(df.index)
    for i, d in enumerate(dates):
        row = df.iloc[i]
        score = ai_score(row)
        if score < min_score:
            continue
        code = stock_code(ticker)
        if use_price_filter and float(row["Close"]) >= 2000:
            continue
        if use_liquidity and code not in liquidity_codes:
            continue
        for horizon, h in [("翌日",1),("5営業日後",5),("20営業日後",20)]:
            if i+h < len(df):
                future_price = float(df.iloc[i+h]["Close"])
                tracking_rows.append({
                    "判定日":d, "コード":code, "銘柄名":stock_name(ticker),
                    "AIスコア":score, "ランク":score_rank(score),
                    "基準価格":float(row["Close"]),
                    "期間":horizon, "将来価格":future_price,
                    "騰落率":(future_price/float(row["Close"])-1)*100
                })

tracking_df = pd.DataFrame(tracking_rows)

# 最新日TOP3だけを「今日の注目」とし、追跡は全履歴を保存
st.dataframe(top3, use_container_width=True, hide_index=True)

# -----------------------------
# 銘柄別成績
# -----------------------------
st.subheader("🏢 銘柄別成績")
if not sell_df.empty:
    stock_result = sell_df.groupby(["コード","銘柄名"]).agg(
        トレード数=("損益","count"),
        勝ち=("損益", lambda x:(x>0).sum()),
        損益=("損益","sum"),
        平均損益=("損益","mean")
    ).reset_index()
    stock_result["勝率"] = stock_result["勝ち"]/stock_result["トレード数"]*100
    st.dataframe(stock_result.sort_values("損益", ascending=False), use_container_width=True, hide_index=True)
else:
    stock_result = pd.DataFrame()

# -----------------------------
# 全売買記録
# -----------------------------
st.subheader("📋 全売買記録")
st.dataframe(trades_df.sort_values("日付", ascending=False), use_container_width=True, hide_index=True)

# -----------------------------
# サマリー
# -----------------------------
summary_df = pd.DataFrame({
    "項目":[
        "Ver","初期資金","最終資産","損益","損益率","決済トレード数",
        "勝率","Profit Factor","平均利益","平均損失","平均利益/損失",
        "最大DD","最大DD率","最大連続損失","明けの明星","株価2,000円以上BUY"
    ],
    "結果":[
        "5.2",initial_cash,final_asset,profit,return_rate,trade_count,
        win_rate,profit_factor,avg_profit,avg_loss,avg_ratio,
        max_dd,max_dd_rate,max_consecutive_losses,"不使用",
        "除外" if use_price_filter else "フィルターOFF"
    ]
})

# -----------------------------
# CSV / ZIP
# -----------------------------
st.subheader("📥 全処理結果CSV")

files = {
    "summary.csv":summary_df,
    "morning_top3.csv":top3,
    "morning_all_rank.csv":morning_df,
    "holding_check.csv":holding_df,
    "all_ai_analysis.csv":analysis_df,
    "trade_history.csv":trades_df,
    "equity_curve.csv":equity_df,
    "loss_brake.csv":brake_df,
    "stock_results.csv":stock_result,
    "liquidity_top50.csv":liquidity_df,
    "top3_tracking.csv":tracking_df
}

for filename, df in files.items():
    st.download_button(
        f"📄 {filename}",
        csv_bytes(df),
        filename,
        "text/csv",
        key=f"dl_{filename}"
    )

zip_buffer = BytesIO()
with ZipFile(zip_buffer, "w") as z:
    for filename, df in files.items():
        z.writestr(filename, csv_bytes(df))

st.download_button(
    "📦 全CSVをZIPで一括ダウンロード",
    zip_buffer.getvalue(),
    "ver5_2_all_results.zip",
    "application/zip"
)

# -----------------------------
# 売買思想
# -----------------------------
st.subheader("🧠 Ver.5.2 売買思想")
st.markdown("""
### 🌅 朝イチの基本ルール
**「良い日だけ買う。悪い日は買わない。」**

### 🔥 AI BUY TOP3
AIスコア、銘柄別AI信頼度、市場環境、流動性を総合してランキングします。

### 🔴 保有銘柄
25日線、75日線、RSI、AIスコア、損切りラインを確認し、
**保有継続 / 売却検討 / 売却候補**を表示します。

### 🚦 連敗ブレーキ
2連敗 → 80%  
3連敗 → 50%  
4連敗 → 新規BUY停止

### ❌ BUY選定に使用しないもの
- 明けの明星
- 株価2,000円以上

### 🔒 重要
AIランキングは将来の利益を保証しません。
SBI証券への注文は自動化せず、最終的な注文判断は利用者が行う設計です。
""")

st.success("🚀 Ver.5.2 完了")
