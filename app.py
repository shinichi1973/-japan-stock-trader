import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# =========================================================
# ページ設定
# =========================================================
st.set_page_config(
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.6",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.6")
st.caption(
    "S株を想定した仮想バックテスト｜AI BUYランキング｜市場環境フィルター｜"
    "連続損失ブレーキ｜トレーリングストップ"
)

st.info(
    "Ver.4.6では「明けの明星」と「株価2,000円以上」を完全に使用しません。"
    "トレンド・勢い・出来高・RSI・出口戦略を重視します。"
)

# =========================================================
# サイドバー
# =========================================================
st.sidebar.header("⚙️ バックテスト設定")

initial_cash = st.sidebar.number_input(
    "初期資金（円）",
    min_value=10000,
    value=100000,
    step=10000
)

max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=30,
    value=10,
    step=1
)

max_per_position = st.sidebar.number_input(
    "1銘柄最大購入額（円）",
    min_value=5000,
    value=100000,
    step=5000
)

stop_loss = st.sidebar.slider(
    "基本損切り（%）",
    3.0,
    10.0,
    6.0,
    0.5
)

profit_start = st.sidebar.slider(
    "利益確保開始（%）",
    3.0,
    10.0,
    5.0,
    0.5
)

trailing_stop = st.sidebar.slider(
    "トレーリング幅（%）",
    2.0,
    8.0,
    4.0,
    0.5
)

take_profit = st.sidebar.slider(
    "通常利確（%）",
    8.0,
    30.0,
    15.0,
    1.0
)

rsi_low = st.sidebar.slider(
    "RSI下限",
    30,
    60,
    40
)

rsi_high = st.sidebar.slider(
    "RSI上限",
    60,
    80,
    70
)

min_score = st.sidebar.slider(
    "最低BUYスコア",
    70,
    90,
    75
)

lookback_years = st.sidebar.slider(
    "バックテスト期間（年）",
    2,
    5,
    5
)

st.sidebar.markdown("---")

ticker_input = st.sidebar.text_area(
    "対象銘柄コード",
    value="7203,6758,9984,8306,9432,6501,8035,8058,7267,2914"
)

diagnostic_mode = st.sidebar.checkbox(
    "🔎 診断モード",
    value=False
)

# =========================================================
# 銘柄コード整理
# =========================================================
def normalize_tickers(text):
    tickers = []

    for x in text.replace("\n", ",").split(","):
        x = x.strip()

        if not x:
            continue

        if x.endswith(".T"):
            tickers.append(x)
        else:
            tickers.append(x + ".T")

    return list(dict.fromkeys(tickers))


tickers = normalize_tickers(ticker_input)

# =========================================================
# 指標計算
# =========================================================
def calculate_indicators(df):

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]

    for col in required:
        if col not in df.columns:
            return pd.DataFrame()

    df = df[required].copy()

    df["MA25"] = df["Close"].rolling(25).mean()
    df["MA75"] = df["Close"].rolling(75).mean()
    df["MA200"] = df["Close"].rolling(200).mean()

    df["MA25_Slope"] = df["MA25"].diff(5)
    df["MA75_Slope"] = df["MA75"].diff(5)

    df["VOL20"] = df["Volume"].rolling(20).mean()

    # RSI
    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["RSI"] = 100 - (100 / (1 + rs))

    # 20日高値
    df["HIGH20"] = df["High"].rolling(20).max()

    # ATR
    high_low = df["High"] - df["Low"]
    high_close = abs(df["High"] - df["Close"].shift())
    low_close = abs(df["Low"] - df["Close"].shift())

    tr = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)

    df["ATR14"] = tr.rolling(14).mean()

    return df.dropna()


# =========================================================
# データ取得
# =========================================================
@st.cache_data(ttl=3600)
def download_data(ticker, years):

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * years + 300)

    try:
        df = yf.download(
            ticker,
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=False,
            progress=False
        )

        if df.empty:
            return pd.DataFrame()

        return calculate_indicators(df)

    except Exception:
        return pd.DataFrame()


# =========================================================
# 市場環境
# =========================================================
@st.cache_data(ttl=3600)
def get_market_environment():

    try:
        nikkei = yf.download(
            "^N225",
            period="2y",
            auto_adjust=False,
            progress=False
        )

        if nikkei.empty:
            return None

        if isinstance(nikkei.columns, pd.MultiIndex):
            nikkei.columns = nikkei.columns.get_level_values(0)

        close = nikkei["Close"]

        ma25 = close.rolling(25).mean()
        ma75 = close.rolling(75).mean()
        ma200 = close.rolling(200).mean()

        latest = float(close.iloc[-1])
        latest_ma25 = float(ma25.iloc[-1])
        latest_ma75 = float(ma75.iloc[-1])
        latest_ma200 = float(ma200.iloc[-1])

        slope25 = latest_ma25 - float(ma25.iloc[-6])

        score = 0

        if latest > latest_ma25:
            score += 1

        if latest_ma25 > latest_ma75:
            score += 1

        if latest_ma75 > latest_ma200:
            score += 1

        if slope25 > 0:
            score += 1

        if score >= 4:
            market = "🟢 強気"
            factor = 1.00

        elif score == 3:
            market = "🟡 やや強気"
            factor = 0.85

        elif score == 2:
            market = "⚪ 中立"
            factor = 0.60

        elif score == 1:
            market = "🟠 やや弱気"
            factor = 0.35

        else:
            market = "🔴 弱気"
            factor = 0.00

        return {
            "price": latest,
            "ma25": latest_ma25,
            "ma75": latest_ma75,
            "ma200": latest_ma200,
            "slope25": slope25,
            "market": market,
            "factor": factor
        }

    except Exception:
        return None


# =========================================================
# AI BUYスコア
# =========================================================
def calculate_score(row):

    score = 0
    reasons = []

    # -----------------------------------------------------
    # 25日線 > 75日線
    # -----------------------------------------------------
    if row["MA25"] > row["MA75"]:
        score += 20
        reasons.append("25MA>75MA")

    # -----------------------------------------------------
    # 株価 > 200日線
    # -----------------------------------------------------
    if row["Close"] > row["MA200"]:
        score += 20
        reasons.append("200MA上")

    # -----------------------------------------------------
    # 株価 > 25日線
    # -----------------------------------------------------
    if row["Close"] > row["MA25"]:
        score += 15
        reasons.append("25MA上")

    # -----------------------------------------------------
    # 出来高
    # -----------------------------------------------------
    if row["Volume"] > row["VOL20"]:
        score += 15
        reasons.append("出来高増")

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------
    if rsi_low <= row["RSI"] <= rsi_high:
        score += 15
        reasons.append("RSI適正")

    # -----------------------------------------------------
    # 25日線上向き
    # -----------------------------------------------------
    if row["MA25_Slope"] > 0:
        score += 10
        reasons.append("25MA上向き")

    # -----------------------------------------------------
    # 75日線上向き
    # -----------------------------------------------------
    if row["MA75_Slope"] > 0:
        score += 5
        reasons.append("75MA上向き")

    return score, reasons


# =========================================================
# スコア → 資金係数
# =========================================================
def score_money_factor(score):

    if score >= 90:
        return 1.00

    elif score >= 85:
        return 0.85

    elif score >= 80:
        return 0.70

    elif score >= 75:
        return 0.50

    return 0.00


# =========================================================
# 過去トレード成績による補正
# =========================================================
def historical_factor(ticker_stats):

    if not ticker_stats:
        return 1.00

    trades = ticker_stats.get("trades", 0)
    wins = ticker_stats.get("wins", 0)

    if trades < 3:
        return 1.00

    win_rate = wins / trades

    if win_rate >= 0.65:
        return 1.10

    if win_rate >= 0.55:
        return 1.05

    if win_rate < 0.35:
        return 0.70

    if win_rate < 0.45:
        return 0.85

    return 1.00


# =========================================================
# 売買シミュレーション
# =========================================================
def run_backtest(data_dict, market_info):

    cash = float(initial_cash)

    positions = {}

    trades = []

    equity_curve = []

    ticker_stats = {}

    consecutive_losses = 0
    max_consecutive_losses = 0

    for ticker in data_dict:
        ticker_stats[ticker] = {
            "trades": 0,
            "wins": 0
        }

    all_dates = sorted(
        set(
            date
            for df in data_dict.values()
            for date in df.index
        )
    )

    for current_date in all_dates:

        # =================================================
        # SELL
        # =================================================
        for ticker in list(positions.keys()):

            pos = positions[ticker]

            df = data_dict[ticker]

            if current_date not in df.index:
                continue

            row = df.loc[current_date]

            price = float(row["Close"])

            entry_price = pos["entry_price"]

            profit_pct = (
                price / entry_price - 1
            ) * 100

            # 最高値更新
            if price > pos["highest_price"]:
                pos["highest_price"] = price

            highest_price = pos["highest_price"]

            trailing_price = (
                highest_price *
                (1 - trailing_stop / 100)
            )

            reason = None

            # -------------------------------------------------
            # 損切り
            # -------------------------------------------------
            if profit_pct <= -stop_loss:
                reason = "損切り"

            # -------------------------------------------------
            # トレーリングストップ
            # -------------------------------------------------
            elif (
                profit_pct >= profit_start
                and price <= trailing_price
            ):
                reason = "トレーリング"

            # -------------------------------------------------
            # 通常利確
            # -------------------------------------------------
            elif profit_pct >= take_profit:
                reason = "利確"

            # -------------------------------------------------
            # 25日線割れ
            # -------------------------------------------------
            elif price < row["MA25"]:
                reason = "25日線割れ"

            # -------------------------------------------------
            # 売却
            # -------------------------------------------------
            if reason:

                shares = pos["shares"]

                sell_value = shares * price

                cash += sell_value

                pnl = (
                    price - entry_price
                ) * shares

                pnl_pct = (
                    price / entry_price - 1
                ) * 100

                ticker_stats[ticker]["trades"] += 1

                if pnl > 0:
                    ticker_stats[ticker]["wins"] += 1
                    consecutive_losses = 0

                else:
                    consecutive_losses += 1

                    max_consecutive_losses = max(
                        max_consecutive_losses,
                        consecutive_losses
                    )

                trades.append({
                    "日付": current_date,
                    "銘柄": ticker.replace(".T", ""),
                    "売買": "SELL",
                    "価格": price,
                    "株数": shares,
                    "損益": pnl,
                    "損益率": pnl_pct,
                    "理由": reason,
                    "BUYスコア": pos["score"]
                })

                del positions[ticker]

        # =================================================
        # BUY
        # =================================================

        # 連続損失ブレーキ
        if consecutive_losses >= 5:
            loss_factor = 0.00

        elif consecutive_losses >= 4:
            loss_factor = 0.30

        elif consecutive_losses >= 3:
            loss_factor = 0.50

        elif consecutive_losses >= 2:
            loss_factor = 0.80

        else:
            loss_factor = 1.00

        # 市場環境
        market_factor = (
            market_info["factor"]
            if market_info
            else 1.00
        )

        if market_factor > 0:

            candidates = []

            for ticker, df in data_dict.items():

                if current_date not in df.index:
                    continue

                if ticker in positions:
                    continue

                if len(positions) >= max_positions:
                    break

                row = df.loc[current_date]

                price = float(row["Close"])

                # =================================================
                # 株価2,000円以上を完全除外
                # =================================================
                if price >= 2000:
                    continue

                # =================================================
                # 指標欠損
                # =================================================
                if pd.isna(row["MA25"]) or pd.isna(row["MA75"]):
                    continue

                # =================================================
                # AIスコア
                # =================================================
                score, reasons = calculate_score(row)

                if score < min_score:
                    continue

                # =================================================
                # スコア資金係数
                # =================================================
                score_factor = score_money_factor(score)

                if score_factor <= 0:
                    continue

                # =================================================
                # 過去成績補正
                # =================================================
                hist_factor = historical_factor(
                    ticker_stats.get(ticker)
                )

                final_factor = (
                    score_factor
                    * market_factor
                    * loss_factor
                    * hist_factor
                )

                final_factor = min(
                    final_factor,
                    1.00
                )

                if final_factor <= 0:
                    continue

                candidates.append({
                    "ticker": ticker,
                    "row": row,
                    "score": score,
                    "reasons": reasons,
                    "factor": final_factor
                })

            # =================================================
            # BUYランキング順
            # =================================================
            candidates.sort(
                key=lambda x: x["score"],
                reverse=True
            )

            for candidate in candidates:

                if len(positions) >= max_positions:
                    break

                ticker = candidate["ticker"]

                row = candidate["row"]

                price = float(row["Close"])

                factor = candidate["factor"]

                budget = min(
                    max_per_position,
                    cash
                )

                budget *= factor

                if budget <= 0:
                    continue

                # S株想定：1株単位
                shares = int(
                    budget / price
                )

                if shares <= 0:
                    continue

                cost = shares * price

                if cost > cash:
                    continue

                cash -= cost

                positions[ticker] = {
                    "entry_price": price,
                    "shares": shares,
                    "highest_price": price,
                    "score": candidate["score"],
                    "entry_date": current_date
                }

                trades.append({
                    "日付": current_date,
                    "銘柄": ticker.replace(".T", ""),
                    "売買": "BUY",
                    "価格": price,
                    "株数": shares,
                    "損益": 0,
                    "損益率": 0,
                    "理由": "AI BUY",
                    "BUYスコア": candidate["score"]
                })

        # =================================================
        # 資産評価
        # =================================================
        total_asset = cash

        for ticker, pos in positions.items():

            df = data_dict[ticker]

            if current_date in df.index:

                price = float(
                    df.loc[current_date]["Close"]
                )

                total_asset += (
                    price * pos["shares"]
                )

        equity_curve.append({
            "日付": current_date,
            "資産": total_asset
        })

    return (
        pd.DataFrame(trades),
        pd.DataFrame(equity_curve),
        max_consecutive_losses
    )


# =========================================================
# データ取得
# =========================================================
st.subheader("📥 データ取得")

data_dict = {}

progress = st.progress(0)

for i, ticker in enumerate(tickers):

    df = download_data(
        ticker,
        lookback_years
    )

    if not df.empty:
        data_dict[ticker] = df

    progress.progress(
        int((i + 1) / len(tickers) * 100)
    )

progress.empty()

st.write(
    f"**{len(data_dict)}銘柄のデータを取得しました。**"
)

if len(data_dict) == 0:

    st.error(
        "データを取得できませんでした。銘柄コードを確認してください。"
    )

    st.stop()


# =========================================================
# 市場環境
# =========================================================
market_info = get_market_environment()

st.subheader("🌏 現在の市場環境")

if market_info:

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        "日経225",
        f"¥{market_info['price']:,.0f}"
    )

    c2.metric(
        "25日線",
        f"¥{market_info['ma25']:,.0f}"
    )

    c3.metric(
        "75日線",
        f"¥{market_info['ma75']:,.0f}"
    )

    c4.metric(
        "200日線",
        f"¥{market_info['ma200']:,.0f}"
    )

    c5.metric(
        "BUY資金係数",
        f"{market_info['factor']:.0%}"
    )

    st.success(
        f"市場判定：{market_info['market']}"
    )

else:

    st.warning(
        "日経225のデータを取得できませんでした。"
    )

    market_info = {
        "factor": 1.0
    }


# =========================================================
# AI BUYランキング
# =========================================================
st.subheader("🏆 AI BUYランキング")

ranking = []

for ticker, df in data_dict.items():

    row = df.iloc[-1]

    price = float(row["Close"])

    # 2,000円以上は完全除外
    if price >= 2000:
        continue

    score, reasons = calculate_score(row)

    if score >= 90:
        judgement = "🔥 強BUY"

    elif score >= 85:
        judgement = "🟢 BUY強"

    elif score >= 75:
        judgement = "🟢 BUY"

    else:
        judgement = "⚪ 見送り"

    ranking.append({
        "銘柄": ticker.replace(".T", ""),
        "株価": round(price, 1),
        "AIスコア": score,
        "判定": judgement,
        "RSI": round(float(row["RSI"]), 1),
        "25MA": round(float(row["MA25"]), 1),
        "75MA": round(float(row["MA75"]), 1),
        "200MA": round(float(row["MA200"]), 1),
        "出来高倍率": round(
            float(row["Volume"] / row["VOL20"]),
            2
        ),
        "条件": ", ".join(reasons)
    })

ranking_df = pd.DataFrame(ranking)

if not ranking_df.empty:

    ranking_df = ranking_df.sort_values(
        "AIスコア",
        ascending=False
    )

    st.dataframe(
        ranking_df,
        use_container_width=True,
        hide_index=True
    )

else:

    st.warning(
        "現在、AI BUY条件を満たす銘柄はありません。"
    )


# =========================================================
# バックテスト
# =========================================================
st.subheader("📊 Ver.4.6 バックテスト")

with st.spinner("バックテストを実行しています..."):

    trades_df, equity_df, max_consecutive_losses = run_backtest(
        data_dict,
        market_info
    )


# =========================================================
# 最終資産
# =========================================================
if equity_df.empty:

    st.warning(
        "バックテスト結果がありません。"
    )

    st.stop()

final_asset = float(
    equity_df["資産"].iloc[-1]
)

profit = final_asset - initial_cash

return_rate = (
    final_asset / initial_cash - 1
) * 100

# 最大DD
equity_series = equity_df["資産"]

running_max = equity_series.cummax()

drawdown = (
    equity_series - running_max
)

max_dd = float(drawdown.min())

max_dd_rate = (
    max_dd / running_max.max()
) * 100


# =========================================================
# トレード統計
# =========================================================
sell_df = trades_df[
    trades_df["売買"] == "SELL"
].copy()

trade_count = len(sell_df)

if trade_count > 0:

    wins = sell_df[
        sell_df["損益"] > 0
    ]

    losses = sell_df[
        sell_df["損益"] < 0
    ]

    win_rate = (
        len(wins) / trade_count
    ) * 100

    gross_profit = wins["損益"].sum()

    gross_loss = abs(
        losses["損益"].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit / gross_loss
        )
    else:
        profit_factor = np.inf

    avg_profit = (
        wins["損益"].mean()
        if not wins.empty
        else 0
    )

    avg_loss = (
        abs(losses["損益"].mean())
        if not losses.empty
        else 0
    )

    if avg_loss > 0:
        avg_ratio = (
            avg_profit / avg_loss
        )
    else:
        avg_ratio = np.inf

else:

    win_rate = 0
    profit_factor = 0
    avg_profit = 0
    avg_loss = 0
    avg_ratio = 0


# =========================================================
# 結果表示
# =========================================================
c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "最終資産",
    f"¥{final_asset:,.0f}"
)

c2.metric(
    "損益",
    f"¥{profit:,.0f}"
)

c3.metric(
    "損益率",
    f"{return_rate:.2f}%"
)

c4.metric(
    "最大DD",
    f"¥{max_dd:,.0f}"
)


# =========================================================
# 統計
# =========================================================
st.subheader("📐 トレード統計")

c1, c2, c3 = st.columns(3)

c1.metric(
    "決済トレード数",
    f"{trade_count}"
)

c2.metric(
    "勝率",
    f"{win_rate:.1f}%"
)

c3.metric(
    "Profit Factor",
    f"{profit_factor:.2f}"
)

c1, c2, c3 = st.columns(3)

c1.metric(
    "平均利益",
    f"¥{avg_profit:,.0f}"
)

c2.metric(
    "平均損失",
    f"¥{avg_loss:,.0f}"
)

c3.metric(
    "平均利益/損失",
    f"{avg_ratio:.2f}倍"
)

st.metric(
    "最大DD率",
    f"{max_dd_rate:.2f}%"
)


# =========================================================
# 資産推移
# =========================================================
st.subheader("📈 資産推移")

chart_df = equity_df.copy()

chart_df["日付"] = pd.to_datetime(
    chart_df["日付"]
)

chart_df = chart_df.set_index("日付")

st.line_chart(
    chart_df["資産"]
)


# =========================================================
# DD
# =========================================================
st.subheader("📉 ドローダウン")

dd_df = pd.DataFrame({
    "ドローダウン": drawdown.values
})

dd_df.index = equity_df["日付"]

st.area_chart(
    dd_df
)


# =========================================================
# 銘柄別成績
# =========================================================
st.subheader("🏆 銘柄別成績")

if not sell_df.empty:

    ticker_result = (
        sell_df
        .groupby("銘柄")
        .agg(
            トレード数=("損益", "count"),
            勝ち=("損益", lambda x: (x > 0).sum()),
            損益=("損益", "sum"),
            平均損益=("損益", "mean")
        )
        .reset_index()
    )

    ticker_result["勝率"] = (
        ticker_result["勝ち"]
        / ticker_result["トレード数"]
        * 100
    )

    ticker_result = ticker_result.sort_values(
        "損益",
        ascending=False
    )

    st.dataframe(
        ticker_result,
        use_container_width=True,
        hide_index=True
    )

    good_trades = ticker_result[
        ticker_result["損益"] > 0
    ]

    bad_trades = ticker_result[
        ticker_result["損益"] <= 0
    ]

    col1, col2 = st.columns(2)

    with col1:
        st.success("🟢 良いトレード")
        st.dataframe(
            good_trades,
            use_container_width=True,
            hide_index=True
        )

    with col2:
        st.error("🔴 改善対象トレード")
        st.dataframe(
            bad_trades,
            use_container_width=True,
            hide_index=True
        )


# =========================================================
# 売却理由
# =========================================================
st.subheader("🚦 売却理由別成績")

if not sell_df.empty:

    reason_result = (
        sell_df
        .groupby("理由")
        .agg(
            回数=("損益", "count"),
            損益=("損益", "sum"),
            平均損益=("損益", "mean")
        )
        .reset_index()
        .sort_values(
            "損益",
            ascending=False
        )
    )

    st.dataframe(
        reason_result,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# 連続損失ブレーキ
# =========================================================
st.subheader("🚦 連続損失ブレーキ")

st.metric(
    "最大連続損失",
    f"{max_consecutive_losses}回"
)

st.markdown(
    """
**Ver.4.6 ブレーキ**

- 2連敗 → 購入額 **80%**
- 3連敗 → 購入額 **50%**
- 4連敗 → 購入額 **30%**
- 5連敗 → **新規BUY停止**
"""
)


# =========================================================
# 全売買記録
# =========================================================
st.subheader("📋 全売買記録")

if not trades_df.empty:

    st.dataframe(
        trades_df.sort_values(
            "日付",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True
    )

    csv = trades_df.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "📥 売買記録CSVをダウンロード",
        data=csv,
        file_name="ver4_6_trade_history.csv",
        mime="text/csv"
    )

else:

    st.info(
        "売買記録はありません。"
    )


# =========================================================
# 診断
# =========================================================
if diagnostic_mode:

    st.subheader("🔎 Ver.4.6 診断")

    diagnostic_rows = []

    for ticker, df in data_dict.items():

        latest = df.iloc[-1]

        score, reasons = calculate_score(
            latest
        )

        diagnostic_rows.append({
            "銘柄": ticker.replace(".T", ""),
            "株価": round(float(latest["Close"]), 1),
            "2,000円未満": float(latest["Close"]) < 2000,
            "25MA>75MA": latest["MA25"] > latest["MA75"],
            "株価>200MA": latest["Close"] > latest["MA200"],
            "株価>25MA": latest["Close"] > latest["MA25"],
            "出来高": latest["Volume"] > latest["VOL20"],
            "RSI": round(float(latest["RSI"]), 1),
            "25MA上向き": latest["MA25_Slope"] > 0,
            "75MA上向き": latest["MA75_Slope"] > 0,
            "AIスコア": score,
            "BUY判定": score >= min_score
        })

    diagnostic_df = pd.DataFrame(
        diagnostic_rows
    )

    st.dataframe(
        diagnostic_df,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# 売買思想
# =========================================================
st.subheader("🧠 Ver.4.6 売買思想")

st.markdown(
    """
### 🎯 Ver.4.6の目的

**「良い銘柄を選び、悪いBUYを減らし、利益を伸ばす」**

---

### 🟢 AI BUYスコア

| 条件 | 点数 |
|---|---:|
| 25日線 > 75日線 | 20点 |
| 株価 > 200日線 | 20点 |
| 株価 > 25日線 | 15点 |
| 出来高条件 | 15点 |
| RSI適正 | 15点 |
| 25日線上向き | 10点 |
| 75日線上向き | 5点 |
| **合計** | **100点** |

---

### 🏆 BUY判定

- 90点以上 → 🔥 強BUY
- 85～89点 → 🟢 BUY強
- 75～84点 → 🟢 BUY
- 75点未満 → ⚪ 見送り

---

### 💰 基本資金配分

- 90点以上 → 100%
- 85～89点 → 85%
- 80～84点 → 70%
- 75～79点 → 50%

さらに、

**AIスコア × 市場環境 × 連続損失ブレーキ × 過去成績**

で購入金額を決定します。

---

### 🚦 出口戦略

Ver.4.6では利益を伸ばすため、

**利益確保開始 → トレーリングストップ**

を導入しています。

勝率だけではなく、

**Profit Factor**

を重視します。

---

### ❌ 使用しない条件

- 明けの明星
- 株価2,000円以上

価格そのものではなく、

**トレンド・勢い・出来高・RSI・出口戦略**

を重視します。
"""
)

st.success(
    "🚀 Ver.4.6 バックテスト完了"
)
