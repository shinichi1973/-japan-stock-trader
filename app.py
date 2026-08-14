import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# =========================================================
# ページ設定
# =========================================================
st.set_page_config(
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.5",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.5")
st.caption(
    "S株を想定した仮想バックテスト｜AI BUYランキング｜市場環境フィルター｜連続損失ブレーキ"
)

st.info(
    "Ver.4.5では「明けの明星」と「株価2,000円以上」を完全に選定条件から除外しています。"
)

# =========================================================
# 銘柄名
# =========================================================
STOCK_NAMES = {
    "7203.T": "トヨタ自動車",
    "6758.T": "ソニーグループ",
    "9984.T": "ソフトバンクグループ",
    "8306.T": "三菱UFJフィナンシャル・グループ",
    "9432.T": "NTT",
    "8058.T": "三菱商事",
    "6501.T": "日立製作所",
    "7011.T": "三菱重工業",
    "8035.T": "東京エレクトロン",
    "6857.T": "アドバンテスト",
    "4063.T": "信越化学工業",
    "6146.T": "ディスコ",
    "6367.T": "ダイキン工業",
    "4568.T": "第一三共",
    "4519.T": "中外製薬",
    "7267.T": "ホンダ",
    "8316.T": "三井住友フィナンシャルグループ",
    "8411.T": "みずほフィナンシャルグループ",
    "8766.T": "東京海上ホールディングス",
    "2914.T": "JT",
}

# =========================================================
# サイドバー
# =========================================================
st.sidebar.header("⚙️ バックテスト設定")

initial_cash = st.sidebar.number_input(
    "初期資金",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)

max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=20,
    value=10,
    step=1
)

max_per_position = st.sidebar.number_input(
    "1銘柄あたり最大購入額",
    min_value=1000,
    max_value=1000000,
    value=20000,
    step=1000
)

stop_loss = st.sidebar.slider(
    "損切り",
    min_value=2,
    max_value=20,
    value=7
)

take_profit = st.sidebar.slider(
    "利確",
    min_value=5,
    max_value=50,
    value=15
)

rsi_low = st.sidebar.slider(
    "RSI下限",
    min_value=20,
    max_value=60,
    value=40
)

rsi_high = st.sidebar.slider(
    "RSI上限",
    min_value=55,
    max_value=90,
    value=70
)

min_buy_score = st.sidebar.slider(
    "最低BUYスコア",
    min_value=50,
    max_value=95,
    value=75
)

st.sidebar.markdown("---")

st.sidebar.subheader("🚦 連続損失ブレーキ")

loss2_factor = st.sidebar.slider(
    "2連敗時の資金係数",
    20,
    100,
    80
)

loss3_factor = st.sidebar.slider(
    "3連敗時の資金係数",
    10,
    100,
    50
)

loss4_stop = st.sidebar.checkbox(
    "4連敗で新規BUY停止",
    value=True
)

st.sidebar.markdown("---")

ticker_input = st.sidebar.text_area(
    "対象銘柄（最大10銘柄推奨）",
    value="7203,6758,9984,8306,9432,8058,6501,7011,8035,6857"
)

run_button = st.sidebar.button(
    "▶ バックテスト開始",
    use_container_width=True
)

# =========================================================
# ティッカー整形
# =========================================================
def normalize_tickers(text):
    raw = text.replace("\n", ",").replace(" ", "").split(",")

    tickers = []

    for x in raw:
        if not x:
            continue

        if not x.endswith(".T"):
            x = x + ".T"

        if x not in tickers:
            tickers.append(x)

    return tickers[:10]


tickers = normalize_tickers(ticker_input)

# =========================================================
# RSI
# =========================================================
def calc_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi


# =========================================================
# データ取得
# =========================================================
@st.cache_data(ttl=3600)
def download_stock_data(ticker):

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 5 + 60)

    try:
        df = yf.download(
            ticker,
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=True,
            progress=False
        )

        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.copy()

        required = ["Open", "High", "Low", "Close", "Volume"]

        for col in required:
            if col not in df.columns:
                return pd.DataFrame()

        df = df[required].dropna()

        return df

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def download_market_data():

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 5 + 60)

    try:
        df = yf.download(
            "^N225",
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=True,
            progress=False
        )

        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna()

    except Exception:
        return pd.DataFrame()


# =========================================================
# テクニカル指標
# =========================================================
def add_indicators(df):

    df = df.copy()

    df["MA25"] = df["Close"].rolling(25).mean()
    df["MA75"] = df["Close"].rolling(75).mean()
    df["MA200"] = df["Close"].rolling(200).mean()

    df["VOL20"] = df["Volume"].rolling(20).mean()

    df["RSI"] = calc_rsi(df["Close"], 14)

    df["MA25_SLOPE"] = df["MA25"].diff(5)
    df["MA75_SLOPE"] = df["MA75"].diff(5)

    # =====================================================
    # BUYスコア
    # =====================================================

    score = pd.Series(0.0, index=df.index)

    # 25日線 > 75日線
    score += np.where(
        df["MA25"] > df["MA75"],
        20,
        0
    )

    # 株価 > 200日線
    score += np.where(
        df["Close"] > df["MA200"],
        20,
        0
    )

    # 株価 > 25日線
    score += np.where(
        df["Close"] > df["MA25"],
        15,
        0
    )

    # 出来高
    score += np.where(
        df["Volume"] > df["VOL20"],
        15,
        0
    )

    # RSI適正
    score += np.where(
        (df["RSI"] >= rsi_low) &
        (df["RSI"] <= rsi_high),
        15,
        0
    )

    # 25日線上向き
    score += np.where(
        df["MA25_SLOPE"] > 0,
        10,
        0
    )

    # 75日線上向き
    score += np.where(
        df["MA75_SLOPE"] > 0,
        5,
        0
    )

    df["BUY_SCORE"] = score

    return df


# =========================================================
# BUY判定
# =========================================================
def get_buy_status(score):

    if score >= 90:
        return "🔥 強BUY"

    elif score >= 85:
        return "🟢 BUY強"

    elif score >= 75:
        return "🟢 BUY"

    else:
        return "⚪ 見送り"


# =========================================================
# スコア資金係数
# =========================================================
def get_score_factor(score):

    if score >= 90:
        return 1.00

    elif score >= 85:
        return 0.85

    elif score >= 80:
        return 0.70

    elif score >= 75:
        return 0.50

    else:
        return 0.00


# =========================================================
# 市場環境
# =========================================================
def market_environment(market):

    market = market.copy()

    market["MA25"] = market["Close"].rolling(25).mean()
    market["MA75"] = market["Close"].rolling(75).mean()
    market["MA200"] = market["Close"].rolling(200).mean()

    market["MA25_SLOPE"] = market["MA25"].diff(5)

    latest = market.dropna().iloc[-1]

    price = float(latest["Close"])
    ma25 = float(latest["MA25"])
    ma75 = float(latest["MA75"])
    ma200 = float(latest["MA200"])
    slope = float(latest["MA25_SLOPE"])

    bullish_points = 0

    if price > ma25:
        bullish_points += 1

    if ma25 > ma75:
        bullish_points += 1

    if ma75 > ma200:
        bullish_points += 1

    if slope > 0:
        bullish_points += 1

    if bullish_points == 4:

        status = "🟢 強気"
        factor = 1.00

    elif bullish_points == 3:

        status = "🟡 やや強気"
        factor = 0.85

    elif bullish_points == 2:

        status = "⚪ 中立"
        factor = 0.70

    elif bullish_points == 1:

        status = "🟠 やや弱気"
        factor = 0.50

    else:

        status = "🔴 弱気"
        factor = 0.30

    return {
        "price": price,
        "ma25": ma25,
        "ma75": ma75,
        "ma200": ma200,
        "slope": slope,
        "status": status,
        "factor": factor
    }


# =========================================================
# 連続損失ブレーキ
# =========================================================
def get_loss_brake(loss_streak):

    if loss_streak >= 4 and loss4_stop:
        return 0.0, "🛑 新規BUY停止"

    if loss_streak >= 3:
        return loss3_factor / 100, "🔴 3連敗ブレーキ"

    if loss_streak >= 2:
        return loss2_factor / 100, "🟠 2連敗ブレーキ"

    return 1.0, "🟢 通常"


# =========================================================
# AI BUYランキング
# =========================================================
def create_ranking(data_dict, market_factor):

    rows = []

    for ticker, df in data_dict.items():

        if df.empty:
            continue

        latest = df.dropna().iloc[-1]

        score = float(latest["BUY_SCORE"])

        factor = get_score_factor(score)

        final_factor = factor * market_factor

        rows.append({
            "銘柄コード": ticker.replace(".T", ""),
            "銘柄名": STOCK_NAMES.get(ticker, ticker),
            "株価": float(latest["Close"]),
            "BUYスコア": round(score, 1),
            "判定": get_buy_status(score),
            "RSI": round(float(latest["RSI"]), 1),
            "25日線": round(float(latest["MA25"]), 1),
            "75日線": round(float(latest["MA75"]), 1),
            "200日線": round(float(latest["MA200"]), 1),
            "資金係数": f"{final_factor * 100:.0f}%"
        })

    if not rows:
        return pd.DataFrame()

    ranking = pd.DataFrame(rows)

    ranking = ranking.sort_values(
        "BUYスコア",
        ascending=False
    ).reset_index(drop=True)

    ranking.insert(
        0,
        "順位",
        range(1, len(ranking) + 1)
    )

    return ranking


# =========================================================
# バックテスト
# =========================================================
def run_backtest(
    data_dict,
    market_data,
    initial_cash,
    max_positions,
    max_per_position,
    stop_loss_pct,
    take_profit_pct,
    min_buy_score,
    market_factor
):

    # 日付を統合
    all_dates = set()

    for df in data_dict.values():

        if not df.empty:
            all_dates.update(df.index)

    dates = sorted(all_dates)

    cash = float(initial_cash)

    positions = {}

    trades = []

    equity_history = []

    loss_streak = 0

    for date in dates:

        # =================================================
        # 市場環境を当日判定
        # =================================================
        try:

            market_hist = market_data.loc[
                market_data.index <= date
            ]

            if len(market_hist) >= 200:

                market_state = market_environment(
                    market_hist
                )

                daily_market_factor = market_state["factor"]

            else:

                daily_market_factor = market_factor

        except Exception:

            daily_market_factor = market_factor

        # =================================================
        # 売却判定
        # =================================================
        for ticker in list(positions.keys()):

            if ticker not in data_dict:
                continue

            df = data_dict[ticker]

            if date not in df.index:
                continue

            row = df.loc[date]

            price = float(row["Close"])

            position = positions[ticker]

            buy_price = position["buy_price"]
            shares = position["shares"]

            pnl_pct = (price / buy_price - 1) * 100

            sell_reason = None

            # 損切り
            if pnl_pct <= -stop_loss_pct:

                sell_reason = "🛑 損切り"

            # 利確
            elif pnl_pct >= take_profit_pct:

                sell_reason = "💰 利確"

            # トレンド崩れ
            elif (
                pd.notna(row["MA25"])
                and price < float(row["MA25"])
            ):

                sell_reason = "📉 25日線割れ"

            if sell_reason:

                sell_value = price * shares

                pnl = sell_value - position["cost"]

                cash += sell_value

                trades.append({
                    "日付": date,
                    "銘柄コード": ticker.replace(".T", ""),
                    "銘柄名": STOCK_NAMES.get(
                        ticker,
                        ticker
                    ),
                    "売買": "SELL",
                    "株価": price,
                    "株数": shares,
                    "売買金額": sell_value,
                    "損益": pnl,
                    "損益率": pnl_pct,
                    "BUYスコア": position["score"],
                    "売却理由": sell_reason
                })

                if pnl < 0:
                    loss_streak += 1
                else:
                    loss_streak = 0

                del positions[ticker]

        # =================================================
        # 新規BUY
        # =================================================

        brake_factor, brake_status = get_loss_brake(
            loss_streak
        )

        if brake_factor > 0:

            candidates = []

            for ticker, df in data_dict.items():

                if ticker in positions:
                    continue

                if len(positions) >= max_positions:
                    break

                if date not in df.index:
                    continue

                row = df.loc[date]

                score = float(row["BUY_SCORE"])

                if score < min_buy_score:
                    continue

                if pd.isna(row["MA25"]):
                    continue

                if pd.isna(row["MA75"]):
                    continue

                if pd.isna(row["MA200"]):
                    continue

                # 基本トレンド条件
                if not (
                    float(row["MA25"]) >
                    float(row["MA75"])
                ):
                    continue

                if not (
                    float(row["Close"]) >
                    float(row["MA200"])
                ):
                    continue

                score_factor = get_score_factor(score)

                if score_factor <= 0:
                    continue

                candidates.append(
                    (
                        score,
                        ticker,
                        row,
                        score_factor
                    )
                )

            # スコア順
            candidates.sort(
                key=lambda x: x[0],
                reverse=True
            )

            for score, ticker, row, score_factor in candidates:

                if len(positions) >= max_positions:
                    break

                price = float(row["Close"])

                total_factor = (
                    score_factor *
                    daily_market_factor *
                    brake_factor
                )

                budget = min(
                    max_per_position,
                    cash
                )

                budget *= total_factor

                if budget < price:
                    continue

                # S株想定
                shares = int(
                    budget / price
                )

                if shares < 1:
                    continue

                cost = shares * price

                if cost > cash:
                    continue

                cash -= cost

                positions[ticker] = {
                    "buy_price": price,
                    "shares": shares,
                    "cost": cost,
                    "score": round(score, 1),
                    "buy_date": date
                }

                trades.append({
                    "日付": date,
                    "銘柄コード": ticker.replace(".T", ""),
                    "銘柄名": STOCK_NAMES.get(
                        ticker,
                        ticker
                    ),
                    "売買": "BUY",
                    "株価": price,
                    "株数": shares,
                    "売買金額": cost,
                    "損益": 0,
                    "損益率": 0,
                    "BUYスコア": round(score, 1),
                    "売却理由": ""
                })

        # =================================================
        # 総資産
        # =================================================

        equity = cash

        for ticker, position in positions.items():

            df = data_dict[ticker]

            available = df.loc[
                df.index <= date
            ]

            if not available.empty:

                current_price = float(
                    available.iloc[-1]["Close"]
                )

                equity += (
                    current_price *
                    position["shares"]
                )

        equity_history.append({
            "日付": date,
            "資産": equity,
            "現金": cash,
            "保有銘柄数": len(positions),
            "連続損失": loss_streak
        })

    # =====================================================
    # 最終ポジションを清算
    # =====================================================
    if dates:

        last_date = dates[-1]

        for ticker in list(positions.keys()):

            df = data_dict[ticker]

            available = df.loc[
                df.index <= last_date
            ]

            if available.empty:
                continue

            price = float(
                available.iloc[-1]["Close"]
            )

            position = positions[ticker]

            shares = position["shares"]

            sell_value = price * shares

            pnl = sell_value - position["cost"]

            pnl_pct = (
                price /
                position["buy_price"] -
                1
            ) * 100

            cash += sell_value

            trades.append({
                "日付": last_date,
                "銘柄コード": ticker.replace(".T", ""),
                "銘柄名": STOCK_NAMES.get(
                    ticker,
                    ticker
                ),
                "売買": "SELL",
                "株価": price,
                "株数": shares,
                "売買金額": sell_value,
                "損益": pnl,
                "損益率": pnl_pct,
                "BUYスコア": position["score"],
                "売却理由": "📅 期間終了"
            })

    equity_df = pd.DataFrame(
        equity_history
    )

    trades_df = pd.DataFrame(
        trades
    )

    return equity_df, trades_df


# =========================================================
# 最大DD
# =========================================================
def calculate_drawdown(equity_df):

    if equity_df.empty:
        return 0, 0

    equity = equity_df["資産"]

    peak = equity.cummax()

    drawdown = equity - peak

    drawdown_pct = (
        equity / peak - 1
    ) * 100

    return (
        float(drawdown.min()),
        float(drawdown_pct.min())
    )


# =========================================================
# 実行
# =========================================================
if run_button:

    if not tickers:

        st.error("銘柄コードを入力してください。")

        st.stop()

    # =====================================================
    # データ取得
    # =====================================================
    with st.spinner("📥 株価データを取得しています..."):

        data_dict = {}

        progress = st.progress(0)

        for i, ticker in enumerate(tickers):

            df = download_stock_data(ticker)

            if not df.empty:

                df = add_indicators(df)

                data_dict[ticker] = df

            progress.progress(
                (i + 1) / len(tickers)
            )

        progress.empty()

    st.success(
        f"📥 {len(data_dict)}銘柄のデータを取得しました。"
    )

    if not data_dict:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    # =====================================================
    # 市場データ
    # =====================================================
    market_data = download_market_data()

    if market_data.empty:

        st.warning(
            "日経225データを取得できないため、市場係数を85%として計算します。"
        )

        market_info = {
            "price": 0,
            "ma25": 0,
            "ma75": 0,
            "ma200": 0,
            "slope": 0,
            "status": "🟡 やや強気",
            "factor": 0.85
        }

    else:

        market_info = market_environment(
            market_data
        )

    market_factor = market_info["factor"]

    # =====================================================
    # 市場環境表示
    # =====================================================
    st.subheader("🌏 現在の市場環境")

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric(
        "日経225",
        f"¥{market_info['price']:,.0f}"
    )

    col2.metric(
        "25日線",
        f"¥{market_info['ma25']:,.0f}"
    )

    col3.metric(
        "75日線",
        f"¥{market_info['ma75']:,.0f}"
    )

    col4.metric(
        "200日線",
        f"¥{market_info['ma200']:,.0f}"
    )

    col5.metric(
        "BUY資金係数",
        f"{market_factor * 100:.0f}%"
    )

    st.markdown(
        f"### 市場判定：{market_info['status']}"
    )

    # =====================================================
    # AI BUYランキング
    # =====================================================
    st.subheader("🏆 AI BUYランキング")

    ranking_df = create_ranking(
        data_dict,
        market_factor
    )

    if not ranking_df.empty:

        st.dataframe(
            ranking_df,
            use_container_width=True,
            hide_index=True
        )

    # =====================================================
    # バックテスト
    # =====================================================
    with st.spinner(
        "📊 Ver.4.5 バックテストを実行しています..."
    ):

        equity_df, trades_df = run_backtest(
            data_dict=data_dict,
            market_data=market_data,
            initial_cash=initial_cash,
            max_positions=max_positions,
            max_per_position=max_per_position,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            min_buy_score=min_buy_score,
            market_factor=market_factor
        )

    # =====================================================
    # 結果
    # =====================================================
    st.subheader("📊 Ver.4.5 バックテスト結果")

    if equity_df.empty:

        st.warning(
            "バックテスト結果がありません。"
        )

        st.stop()

    final_asset = float(
        equity_df["資産"].iloc[-1]
    )

    pnl = final_asset - initial_cash

    return_rate = (
        pnl / initial_cash
    ) * 100

    max_dd, max_dd_pct = calculate_drawdown(
        equity_df
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "最終資産",
        f"¥{final_asset:,.0f}"
    )

    col2.metric(
        "損益",
        f"¥{pnl:,.0f}"
    )

    col3.metric(
        "損益率",
        f"{return_rate:.2f}%"
    )

    col4.metric(
        "最大DD",
        f"¥{max_dd:,.0f}"
    )

    # =====================================================
    # トレード統計
    # =====================================================
    st.subheader("📐 トレード統計")

    if not trades_df.empty:

        sell_df = trades_df[
            trades_df["売買"] == "SELL"
        ].copy()

        if not sell_df.empty:

            total_trades = len(sell_df)

            wins = sell_df[
                sell_df["損益"] > 0
            ]

            losses = sell_df[
                sell_df["損益"] < 0
            ]

            win_rate = (
                len(wins) /
                total_trades *
                100
            )

            gross_profit = wins["損益"].sum()

            gross_loss = abs(
                losses["損益"].sum()
            )

            if gross_loss > 0:

                profit_factor = (
                    gross_profit /
                    gross_loss
                )

            else:

                profit_factor = np.inf

            avg_profit = (
                wins["損益"].mean()
                if not wins.empty
                else 0
            )

            avg_loss = abs(
                losses["損益"].mean()
            ) if not losses.empty else 0

            if avg_loss > 0:

                profit_loss_ratio = (
                    avg_profit /
                    avg_loss
                )

            else:

                profit_loss_ratio = np.inf

            col1, col2, col3, col4, col5 = st.columns(5)

            col1.metric(
                "決済トレード数",
                f"{total_trades}"
            )

            col2.metric(
                "勝率",
                f"{win_rate:.1f}%"
            )

            col3.metric(
                "Profit Factor",
                f"{profit_factor:.2f}"
            )

            col4.metric(
                "平均利益",
                f"¥{avg_profit:,.0f}"
            )

            col5.metric(
                "平均利益/損失",
                f"{profit_loss_ratio:.2f}倍"
            )

            col6, col7 = st.columns(2)

            col6.metric(
                "最大DD額",
                f"¥{max_dd:,.0f}"
            )

            col7.metric(
                "最大DD率",
                f"{max_dd_pct:.2f}%"
            )

    # =====================================================
    # 資産推移
    # =====================================================
    st.subheader("📈 資産推移")

    chart_df = equity_df.set_index("日付")

    st.line_chart(
        chart_df["資産"],
        use_container_width=True
    )

    # =====================================================
    # ドローダウン
    # =====================================================
    st.subheader("📉 ドローダウン")

    dd_df = equity_df.copy()

    dd_df["ピーク"] = (
        dd_df["資産"].cummax()
    )

    dd_df["DD"] = (
        dd_df["資産"] -
        dd_df["ピーク"]
    )

    dd_df = dd_df.set_index("日付")

    st.area_chart(
        dd_df["DD"],
        use_container_width=True
    )

    # =====================================================
    # 銘柄別成績
    # =====================================================
    st.subheader("🏆 銘柄別成績")

    if not trades_df.empty:

        sell_df = trades_df[
            trades_df["売買"] == "SELL"
        ]

        if not sell_df.empty:

            stock_result = (
                sell_df
                .groupby(
                    ["銘柄コード", "銘柄名"],
                    as_index=False
                )
                .agg(
                    トレード数=("損益", "count"),
                    損益=("損益", "sum"),
                    平均損益=("損益", "mean"),
                    勝率=(
                        "損益",
                        lambda x:
                        (x > 0).mean() * 100
                    )
                )
                .sort_values(
                    "損益",
                    ascending=False
                )
            )

            st.dataframe(
                stock_result,
                use_container_width=True,
                hide_index=True
            )

            # =================================================
            # 良いトレード
            # =================================================
            st.markdown("### 🟢 良いトレード")

            good = sell_df[
                sell_df["損益"] > 0
            ].sort_values(
                "損益",
                ascending=False
            )

            st.dataframe(
                good[
                    [
                        "日付",
                        "銘柄コード",
                        "銘柄名",
                        "損益",
                        "損益率",
                        "BUYスコア",
                        "売却理由"
                    ]
                ].head(20),
                use_container_width=True,
                hide_index=True
            )

            # =================================================
            # 改善対象
            # =================================================
            st.markdown("### 🔴 改善対象トレード")

            bad = sell_df[
                sell_df["損益"] < 0
            ].sort_values(
                "損益"
            )

            st.dataframe(
                bad[
                    [
                        "日付",
                        "銘柄コード",
                        "銘柄名",
                        "損益",
                        "損益率",
                        "BUYスコア",
                        "売却理由"
                    ]
                ].head(20),
                use_container_width=True,
                hide_index=True
            )

    # =====================================================
    # 売却理由別
    # =====================================================
    st.subheader("🚦 売却理由別成績")

    if not trades_df.empty:

        sell_df = trades_df[
            trades_df["売買"] == "SELL"
        ]

        if not sell_df.empty:

            reason_result = (
                sell_df
                .groupby(
                    "売却理由",
                    as_index=False
                )
                .agg(
                    件数=("損益", "count"),
                    損益=("損益", "sum"),
                    平均損益=("損益", "mean"),
                    勝率=(
                        "損益",
                        lambda x:
                        (x > 0).mean() * 100
                    )
                )
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

    # =====================================================
    # 連続損失状況
    # =====================================================
    st.subheader("🚦 連続損失ブレーキ")

    if not equity_df.empty:

        max_loss_streak = int(
            equity_df["連続損失"].max()
        )

        if max_loss_streak >= 4:

            st.error(
                f"最大連続損失：{max_loss_streak}回"
            )

        elif max_loss_streak >= 3:

            st.warning(
                f"最大連続損失：{max_loss_streak}回"
            )

        else:

            st.success(
                f"最大連続損失：{max_loss_streak}回"
            )

    # =====================================================
    # 全売買記録
    # =====================================================
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
            file_name="ver4_5_trades.csv",
            mime="text/csv",
            use_container_width=True
        )

    # =====================================================
    # 売買思想
    # =====================================================
    st.subheader("🧠 Ver.4.5 売買思想")

    st.markdown(
        """
### 🎯 Ver.4.5の目的

**「良い銘柄を選び、悪いBUYを減らす」**

という考え方をさらに強化しています。

### 🟢 BUYスコア

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

### 🏆 AI BUYランキング

- 90点以上 → 🔥 強BUY
- 85～89点 → 🟢 BUY強
- 75～84点 → 🟢 BUY
- 75点未満 → ⚪ 見送り

### 💰 スコア別資金配分

- 90点以上 → 100%
- 85～89点 → 85%
- 80～84点 → 70%
- 75～79点 → 50%

さらに、

**スコア × 市場環境 × 連続損失ブレーキ**

で最終購入金額を決定します。

### 🌏 市場環境フィルター

日経225について、

- 25日線
- 75日線
- 200日線
- 25日線の傾き

を確認します。

### 🚦 連続損失ブレーキ

- 2連敗 → 購入額80%
- 3連敗 → 購入額50%
- 4連敗 → 新規BUY停止

という形で、資金を守ります。

### ❌ Ver.4.5で使用しない条件

- 明けの明星
- 株価2,000円以上

**価格そのものではなく、トレンド・勢い・出来高・RSIを重視します。**
"""
    )

else:

    st.markdown(
        """
### 👋 Ver.4.5へようこそ

左側の設定を確認して、

**「▶ バックテスト開始」**

を押してください。

#### Ver.4.5の主な変更

✅ AI BUYランキング  
✅ 市場環境フィルター  
✅ 連続損失ブレーキ  
✅ 銘柄別成績  
✅ 売却理由別成績  
✅ S株想定  
✅ 10万円スタート  
✅ 5年間バックテスト  

そして、

❌ 明けの明星  
❌ 株価2,000円以上  

は使用しません。
"""
    )
