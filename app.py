import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta


# =========================================================
# yfinance
# =========================================================

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================

st.set_page_config(
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.2",
    page_icon="📈",
    layout="wide"
)


# =========================================================
# タイトル
# =========================================================

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.2"
)

st.caption(
    "S株を想定した仮想バックテスト。"
    "明けの明星は使用しません。"
    "良いトレードを残し、悪いトレードを削るスコア方式です。"
)


# =========================================================
# サイドバー
# =========================================================

st.sidebar.header("⚙️ Ver.4.2 バックテスト設定")


initial_cash = st.sidebar.number_input(
    "初期資金（円）",
    min_value=10000,
    max_value=100000000,
    value=100000,
    step=10000
)


max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=50,
    value=10,
    step=1
)


max_per_position = st.sidebar.number_input(
    "1銘柄最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)


stop_loss = st.sidebar.slider(
    "損切り（%）",
    min_value=1.0,
    max_value=20.0,
    value=7.0,
    step=0.5
)


take_profit = st.sidebar.slider(
    "利確開始（%）",
    min_value=5.0,
    max_value=50.0,
    value=15.0,
    step=0.5
)


trailing_stop = st.sidebar.slider(
    "トレーリングストップ（%）",
    min_value=2.0,
    max_value=15.0,
    value=5.0,
    step=0.5
)


min_score = st.sidebar.slider(
    "最低BUYスコア",
    min_value=40,
    max_value=100,
    value=75,
    step=5
)


rsi_low = st.sidebar.slider(
    "RSI下限",
    min_value=20,
    max_value=50,
    value=45,
    step=1
)


rsi_high = st.sidebar.slider(
    "RSI上限",
    min_value=50,
    max_value=90,
    value=65,
    step=1
)


volume_multiplier = st.sidebar.slider(
    "出来高倍率",
    min_value=0.5,
    max_value=3.0,
    value=1.0,
    step=0.1
)


# =========================================================
# Ver.4.2 固定改善条件
# =========================================================

st.sidebar.subheader("🧠 Ver.4.2 改善条件")


chase_limit = st.sidebar.slider(
    "急騰追い買い防止（25日線から）",
    min_value=3.0,
    max_value=20.0,
    value=8.0,
    step=1.0
)


cooldown_days = st.sidebar.slider(
    "損切り後の再購入禁止日数",
    min_value=0,
    max_value=30,
    value=10,
    step=1
)


years = st.sidebar.slider(
    "バックテスト期間（年）",
    min_value=1,
    max_value=10,
    value=5,
    step=1
)


# =========================================================
# 銘柄入力
# =========================================================

st.sidebar.subheader("📋 対象銘柄")


ticker_input = st.sidebar.text_area(
    "銘柄コード",
    value=(
        "7203,6758,9984,8306,9432,"
        "7011,6501,6857,8035,9983"
    )
)


tickers = [
    x.strip()
    for x in ticker_input.replace("\n", ",").split(",")
    if x.strip()
]


tickers = [
    x if "." in x else x + ".T"
    for x in tickers
]


st.sidebar.write(
    f"対象銘柄数：{len(tickers)}"
)


# =========================================================
# データ取得
# =========================================================

@st.cache_data(ttl=3600)
def download_stock_data(ticker, years):

    if yf is None:
        return None

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(days=365 * years + 60)
    )

    try:

        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False
        )

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        df = df.copy()

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col in required:

            if col not in df.columns:
                return None

        df = (
            df[required]
            .dropna()
            .copy()
        )

        return df

    except Exception as e:

        st.warning(
            f"{ticker} データ取得エラー: {e}"
        )

        return None


# =========================================================
# RSI
# =========================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = (
        gain
        .rolling(period)
        .mean()
    )

    avg_loss = (
        loss
        .rolling(period)
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(0, np.nan)
    )

    rsi = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    return rsi


# =========================================================
# テクニカル指標
# =========================================================

def add_indicators(df):

    df = df.copy()

    # -----------------------------------------
    # 移動平均線
    # -----------------------------------------

    df["MA25"] = (
        df["Close"]
        .rolling(25)
        .mean()
    )

    df["MA75"] = (
        df["Close"]
        .rolling(75)
        .mean()
    )

    df["MA200"] = (
        df["Close"]
        .rolling(200)
        .mean()
    )

    # -----------------------------------------
    # RSI
    # -----------------------------------------

    df["RSI"] = calculate_rsi(
        df["Close"],
        14
    )

    # -----------------------------------------
    # 出来高
    # -----------------------------------------

    df["VOL20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    # -----------------------------------------
    # MA傾き
    # -----------------------------------------

    df["MA25_SLOPE"] = (
        df["MA25"].diff(5)
    )

    df["MA75_SLOPE"] = (
        df["MA75"].diff(5)
    )

    # -----------------------------------------
    # ATR
    # -----------------------------------------

    high_low = (
        df["High"]
        - df["Low"]
    )

    high_close = abs(
        df["High"]
        - df["Close"].shift()
    )

    low_close = abs(
        df["Low"]
        - df["Close"].shift()
    )

    tr = pd.concat(
        [
            high_low,
            high_close,
            low_close
        ],
        axis=1
    ).max(axis=1)

    df["ATR14"] = (
        tr
        .rolling(14)
        .mean()
    )

    # -----------------------------------------
    # 25日線からの乖離率
    # -----------------------------------------

    df["MA25_DISTANCE"] = (
        (
            df["Close"]
            / df["MA25"]
        ) - 1
    ) * 100

    return df


# =========================================================
# BUYスコア
# =========================================================

def calculate_buy_score(
    row,
    rsi_low,
    rsi_high,
    volume_multiplier
):

    score = 0

    # -----------------------------------------
    # 25日線 > 75日線
    # 20点
    # -----------------------------------------

    if (
        row["MA25"]
        > row["MA75"]
    ):
        score += 20

    # -----------------------------------------
    # 株価 > 200日線
    # 20点
    # -----------------------------------------

    if (
        row["Close"]
        > row["MA200"]
    ):
        score += 20

    # -----------------------------------------
    # 株価 > 25日線
    # 15点
    # -----------------------------------------

    if (
        row["Close"]
        > row["MA25"]
    ):
        score += 15

    # -----------------------------------------
    # 出来高
    # 15点
    # -----------------------------------------

    if (
        row["Volume"]
        >= row["VOL20"]
        * volume_multiplier
    ):
        score += 15

    # -----------------------------------------
    # RSI
    # 15点
    # -----------------------------------------

    if (
        rsi_low
        <= row["RSI"]
        <= rsi_high
    ):
        score += 15

    # -----------------------------------------
    # 25日線上向き
    # 10点
    # -----------------------------------------

    if (
        row["MA25_SLOPE"]
        > 0
    ):
        score += 10

    # -----------------------------------------
    # 75日線上向き
    # 5点
    # -----------------------------------------

    if (
        row["MA75_SLOPE"]
        > 0
    ):
        score += 5

    return score


# =========================================================
# BUY判定
# =========================================================

def buy_signal(
    row,
    min_score,
    rsi_low,
    rsi_high,
    volume_multiplier,
    chase_limit
):

    score = calculate_buy_score(
        row,
        rsi_low,
        rsi_high,
        volume_multiplier
    )

    # -----------------------------------------
    # 株価2000円以上
    # -----------------------------------------

    price_ok = (
        row["Close"] >= 2000
    )

    # -----------------------------------------
    # RSI
    # -----------------------------------------

    rsi_ok = (
        row["RSI"] >= rsi_low
        and
        row["RSI"] <= rsi_high
    )

    # -----------------------------------------
    # 急騰追い買い防止
    #
    # 25日線から一定%以上離れていたら
    # 高値追いと判断して見送る
    # -----------------------------------------

    chase_ok = (
        row["MA25_DISTANCE"]
        <= chase_limit
    )

    signal = (
        score >= min_score
        and price_ok
        and rsi_ok
        and chase_ok
    )

    return signal, score


# =========================================================
# 売却判定
# =========================================================

def sell_signal(
    row,
    position,
    stop_loss,
    take_profit,
    trailing_stop
):

    entry_price = (
        position["entry_price"]
    )

    highest_price = (
        position["highest_price"]
    )

    current_price = float(
        row["Close"]
    )

    profit_pct = (
        current_price
        / entry_price
        - 1
    ) * 100

    # -----------------------------------------
    # 損切り
    # -----------------------------------------

    if profit_pct <= -stop_loss:

        return True, "損切り"

    # -----------------------------------------
    # 最高値更新
    # -----------------------------------------

    if (
        current_price
        > highest_price
    ):

        position["highest_price"] = (
            current_price
        )

        highest_price = current_price

    # -----------------------------------------
    # トレーリング利確
    # -----------------------------------------

    if profit_pct >= take_profit:

        trailing_price = (
            highest_price
            * (
                1
                - trailing_stop / 100
            )
        )

        if (
            current_price
            <= trailing_price
        ):

            return True, "トレーリング利確"

    # -----------------------------------------
    # 25日線割れ
    # -----------------------------------------

    if (
        current_price
        < row["MA25"]
    ):

        if profit_pct > 5:

            return True, "25日線割れ利益確定"

    # -----------------------------------------
    # 75日線割れ
    # -----------------------------------------

    if (
        current_price
        < row["MA75"]
    ):

        return True, "75日線割れ"

    return False, ""


# =========================================================
# バックテスト
# =========================================================

def run_backtest(
    ticker_data,
    initial_cash,
    max_positions,
    max_per_position,
    stop_loss,
    take_profit,
    trailing_stop,
    min_score,
    rsi_low,
    rsi_high,
    volume_multiplier,
    chase_limit,
    cooldown_days
):

    cash = float(initial_cash)

    positions = {}

    trades = []

    equity_curve = []

    all_dates = set()

    # -----------------------------------------
    # 再エントリー禁止管理
    # ticker -> 再購入可能日
    # -----------------------------------------

    cooldown_until = {}

    # -----------------------------------------
    # 全日付
    # -----------------------------------------

    for ticker, df in ticker_data.items():

        if (
            df is not None
            and not df.empty
        ):

            all_dates.update(
                df.index
            )

    all_dates = sorted(all_dates)

    # =========================================
    # 日次処理
    # =========================================

    for current_date in all_dates:

        # =====================================
        # SELL
        # =====================================

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[ticker]

            if current_date not in df.index:
                continue

            row = df.loc[current_date]

            position = positions[ticker]

            should_sell, reason = sell_signal(
                row,
                position,
                stop_loss,
                take_profit,
                trailing_stop
            )

            if should_sell:

                sell_price = float(
                    row["Close"]
                )

                shares = position["shares"]

                proceeds = (
                    sell_price
                    * shares
                )

                pnl = (
                    sell_price
                    - position["entry_price"]
                ) * shares

                cash += proceeds

                trades.append({
                    "Date": current_date,
                    "Ticker": ticker.replace(".T", ""),
                    "Action": "SELL",
                    "Price": sell_price,
                    "Shares": shares,
                    "Amount": proceeds,
                    "PnL": pnl,
                    "Score": position["score"],
                    "Reason": reason
                })

                # ---------------------------------
                # 損切りの場合は再購入禁止
                # ---------------------------------

                if reason == "損切り":

                    try:

                        future_dates = [
                            d
                            for d in all_dates
                            if d > current_date
                        ]

                        if future_dates:

                            index = min(
                                cooldown_days,
                                len(future_dates) - 1
                            )

                            cooldown_until[ticker] = (
                                future_dates[index]
                            )

                    except Exception:

                        pass

                del positions[ticker]

        # =====================================
        # BUY
        # =====================================

        for ticker, df in ticker_data.items():

            if current_date not in df.index:
                continue

            # ---------------------------------
            # すでに保有中
            # ---------------------------------

            if ticker in positions:
                continue

            # ---------------------------------
            # 最大保有数
            # ---------------------------------

            if (
                len(positions)
                >= max_positions
            ):
                break

            # ---------------------------------
            # クールダウン
            # ---------------------------------

            if ticker in cooldown_until:

                if (
                    current_date
                    < cooldown_until[ticker]
                ):

                    continue

            row = df.loc[current_date]

            # ---------------------------------
            # 必要指標チェック
            # ---------------------------------

            required_values = [
                row["MA25"],
                row["MA75"],
                row["MA200"],
                row["RSI"],
                row["VOL20"],
                row["MA25_SLOPE"],
                row["MA75_SLOPE"],
                row["MA25_DISTANCE"]
            ]

            if any(
                pd.isna(x)
                for x in required_values
            ):

                continue

            # ---------------------------------
            # BUY判定
            # ---------------------------------

            signal, score = buy_signal(
                row,
                min_score,
                rsi_low,
                rsi_high,
                volume_multiplier,
                chase_limit
            )

            if not signal:
                continue

            price = float(
                row["Close"]
            )

            # ---------------------------------
            # S株想定
            #
            # 1株から購入可能
            # ---------------------------------

            shares = int(
                max_per_position
                // price
            )

            if shares <= 0:
                continue

            amount = (
                price
                * shares
            )

            # ---------------------------------
            # 現金不足
            # ---------------------------------

            if amount > cash:
                continue

            cash -= amount

            positions[ticker] = {
                "entry_price": price,
                "shares": shares,
                "highest_price": price,
                "score": score
            }

            trades.append({
                "Date": current_date,
                "Ticker": ticker.replace(".T", ""),
                "Action": "BUY",
                "Price": price,
                "Shares": shares,
                "Amount": amount,
                "PnL": 0,
                "Score": score,
                "Reason": (
                    "強BUY"
                    if score >= 85
                    else "BUY"
                )
            })

        # =====================================
        # 資産評価
        # =====================================

        equity = cash

        for ticker, position in positions.items():

            df = ticker_data[ticker]

            if current_date in df.index:

                price = float(
                    df.loc[
                        current_date,
                        "Close"
                    ]
                )

                equity += (
                    price
                    * position["shares"]
                )

        equity_curve.append({
            "Date": current_date,
            "Equity": equity,
            "Cash": cash,
            "Positions": len(positions)
        })

    # =========================================
    # 最終決済
    # =========================================

    if all_dates:

        final_date = all_dates[-1]

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[ticker]

            if final_date not in df.index:
                continue

            row = df.loc[final_date]

            sell_price = float(
                row["Close"]
            )

            position = positions[ticker]

            shares = position["shares"]

            proceeds = (
                sell_price
                * shares
            )

            pnl = (
                sell_price
                - position["entry_price"]
            ) * shares

            cash += proceeds

            trades.append({
                "Date": final_date,
                "Ticker": ticker.replace(".T", ""),
                "Action": "SELL",
                "Price": sell_price,
                "Shares": shares,
                "Amount": proceeds,
                "PnL": pnl,
                "Score": position["score"],
                "Reason": "最終決済"
            })

            del positions[ticker]

        # -------------------------------------
        # 最終決済後の正確な資産
        # -------------------------------------

        equity_curve.append({
            "Date": final_date,
            "Equity": cash,
            "Cash": cash,
            "Positions": 0
        })

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_curve
    )

    # -----------------------------------------
    # 同日重複削除
    # 最終決済で同じ日付が追加されるため
    # 最後の値を残す
    # -----------------------------------------

    if not equity_df.empty:

        equity_df = (
            equity_df
            .drop_duplicates(
                subset=["Date"],
                keep="last"
            )
            .sort_values("Date")
            .reset_index(drop=True)
        )

    return (
        trades_df,
        equity_df
    )


# =========================================================
# 結果計算
# =========================================================

def calculate_statistics(
    trades_df,
    equity_df,
    initial_cash
):

    result = {}

    if equity_df.empty:

        return result

    final_equity = float(
        equity_df.iloc[-1]["Equity"]
    )

    profit = (
        final_equity
        - initial_cash
    )

    profit_pct = (
        profit
        / initial_cash
    ) * 100

    # -----------------------------------------
    # 最大DD
    # -----------------------------------------

    equity_df = equity_df.copy()

    equity_df["Peak"] = (
        equity_df["Equity"]
        .cummax()
    )

    equity_df["Drawdown"] = (
        equity_df["Equity"]
        - equity_df["Peak"]
    )

    equity_df["DrawdownPct"] = (
        equity_df["Drawdown"]
        / equity_df["Peak"]
        * 100
    )

    max_dd = float(
        equity_df["Drawdown"].min()
    )

    max_dd_pct = float(
        equity_df["DrawdownPct"].min()
    )

    result["final_equity"] = final_equity
    result["profit"] = profit
    result["profit_pct"] = profit_pct
    result["max_dd"] = max_dd
    result["max_dd_pct"] = max_dd_pct

    # -----------------------------------------
    # 売却トレード
    # -----------------------------------------

    if trades_df.empty:

        result["sell_count"] = 0
        result["win_rate"] = 0
        result["profit_factor"] = 0
        result["avg_win"] = 0
        result["avg_loss"] = 0
        result["risk_reward"] = 0

        return result

    sells = trades_df[
        trades_df["Action"] == "SELL"
    ].copy()

    if sells.empty:

        result["sell_count"] = 0
        result["win_rate"] = 0
        result["profit_factor"] = 0
        result["avg_win"] = 0
        result["avg_loss"] = 0
        result["risk_reward"] = 0

        return result

    wins = sells[
        sells["PnL"] > 0
    ]

    losses = sells[
        sells["PnL"] < 0
    ]

    win_rate = (
        len(wins)
        / len(sells)
        * 100
    )

    gross_profit = (
        wins["PnL"].sum()
    )

    gross_loss = abs(
        losses["PnL"].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = np.inf

    avg_win = (
        wins["PnL"].mean()
        if not wins.empty
        else 0
    )

    avg_loss = (
        abs(losses["PnL"].mean())
        if not losses.empty
        else 0
    )

    if avg_loss > 0:

        risk_reward = (
            avg_win
            / avg_loss
        )

    else:

        risk_reward = np.inf

    result["sell_count"] = len(sells)
    result["win_rate"] = win_rate
    result["profit_factor"] = profit_factor
    result["avg_win"] = avg_win
    result["avg_loss"] = avg_loss
    result["risk_reward"] = risk_reward

    return result


# =========================================================
# データ取得
# =========================================================

st.subheader("📥 データ取得")


if st.button(
    "🚀 Ver.4.2 バックテスト開始",
    type="primary"
):

    if yf is None:

        st.error(
            "yfinanceがインストールされていません。"
            "requirements.txtを確認してください。"
        )

        st.stop()

    if len(tickers) == 0:

        st.error(
            "銘柄コードを入力してください。"
        )

        st.stop()

    progress = st.progress(0)

    ticker_data = {}

    for i, ticker in enumerate(tickers):

        df = download_stock_data(
            ticker,
            years
        )

        if (
            df is not None
            and not df.empty
        ):

            df = add_indicators(df)

            ticker_data[ticker] = df

        progress.progress(
            int(
                (i + 1)
                / len(tickers)
                * 100
            )
        )

    progress.empty()

    if not ticker_data:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"{len(ticker_data)}銘柄のデータを取得しました。"
    )

    # =========================================
    # バックテスト
    # =========================================

    trades_df, equity_df = run_backtest(
        ticker_data,
        initial_cash,
        max_positions,
        max_per_position,
        stop_loss,
        take_profit,
        trailing_stop,
        min_score,
        rsi_low,
        rsi_high,
        volume_multiplier,
        chase_limit,
        cooldown_days
    )

    # =========================================
    # 結果
    # =========================================

    st.header(
        "📊 Ver.4.2 バックテスト結果"
    )

    if equity_df.empty:

        st.warning(
            "資産推移データがありません。"
        )

        st.stop()

    stats = calculate_statistics(
        trades_df,
        equity_df,
        initial_cash
    )

    final_equity = stats[
        "final_equity"
    ]

    profit = stats[
        "profit"
    ]

    profit_pct = stats[
        "profit_pct"
    ]

    # =========================================
    # 基本結果
    # =========================================

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "最終資産",
        f"¥{final_equity:,.0f}"
    )

    col2.metric(
        "損益",
        f"¥{profit:,.0f}"
    )

    col3.metric(
        "損益率",
        f"{profit_pct:.2f}%"
    )

    col4.metric(
        "最大DD",
        f"¥{stats['max_dd']:,.0f}"
    )

    # =========================================
    # トレード統計
    # =========================================

    st.subheader(
        "📐 トレード統計"
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric(
        "決済トレード数",
        f"{stats['sell_count']}"
    )

    col2.metric(
        "勝率",
        f"{stats['win_rate']:.1f}%"
    )

    pf = stats["profit_factor"]

    if np.isinf(pf):

        pf_text = "∞"

    else:

        pf_text = f"{pf:.2f}"

    col3.metric(
        "Profit Factor",
        pf_text
    )

    col4.metric(
        "平均利益",
        f"¥{stats['avg_win']:,.0f}"
    )

    rr = stats["risk_reward"]

    if np.isinf(rr):

        rr_text = "∞"

    else:

        rr_text = f"{rr:.2f}倍"

    col5.metric(
        "平均利益/損失",
        rr_text
    )

    # =========================================
    # DD率
    # =========================================

    col1, col2 = st.columns(2)

    col1.metric(
        "最大DD額",
        f"¥{stats['max_dd']:,.0f}"
    )

    col2.metric(
        "最大DD率",
        f"{stats['max_dd_pct']:.2f}%"
    )

    # =========================================
    # 売買結果
    # =========================================

    if not trades_df.empty:

        sells = trades_df[
            trades_df["Action"] == "SELL"
        ].copy()

        if not sells.empty:

            # =====================================
            # 資産曲線
            # =====================================

            st.subheader(
                "📈 資産推移"
            )

            chart_df = (
                equity_df
                .set_index("Date")["Equity"]
            )

            st.line_chart(
                chart_df
            )

            # =====================================
            # DD推移
            # =====================================

            st.subheader(
                "📉 ドローダウン"
            )

            dd_chart = (
                equity_df
                .set_index("Date")
                ["Drawdown"]
            )

            st.area_chart(
                dd_chart
            )

            # =====================================
            # 銘柄別成績
            # =====================================

            st.subheader(
                "🏆 銘柄別成績"
            )

            stock_result = (
                sells
                .groupby("Ticker")
                .agg(
                    売買回数=("PnL", "count"),
                    損益=("PnL", "sum"),
                    平均損益=("PnL", "mean"),
                    勝率=(
                        "PnL",
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
                stock_result.style.format({
                    "損益": "¥{:,.0f}",
                    "平均損益": "¥{:,.0f}",
                    "勝率": "{:.1f}%"
                }),
                use_container_width=True
            )

            # =====================================
            # 良いトレード
            # =====================================

            st.subheader(
                "🟢 良いトレード"
            )

            good_trades = (
                sells[
                    sells["PnL"] > 0
                ]
                .sort_values(
                    "PnL",
                    ascending=False
                )
            )

            st.dataframe(
                good_trades.head(20),
                use_container_width=True
            )

            # =====================================
            # 悪いトレード
            # =====================================

            st.subheader(
                "🔴 改善対象トレード"
            )

            bad_trades = (
                sells[
                    sells["PnL"] < 0
                ]
                .sort_values(
                    "PnL"
                )
            )

            st.dataframe(
                bad_trades.head(20),
                use_container_width=True
            )

            # =====================================
            # 売却理由
            # =====================================

            st.subheader(
                "🚦 売却理由別成績"
            )

            reason_result = (
                sells
                .groupby("Reason")
                .agg(
                    回数=("PnL", "count"),
                    損益=("PnL", "sum"),
                    平均損益=("PnL", "mean"),
                    勝率=(
                        "PnL",
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
                reason_result.style.format({
                    "損益": "¥{:,.0f}",
                    "平均損益": "¥{:,.0f}",
                    "勝率": "{:.1f}%"
                }),
                use_container_width=True
            )

        # =========================================
        # 全売買記録
        # =========================================

        st.subheader(
            "📋 全売買記録"
        )

        st.dataframe(
            trades_df,
            use_container_width=True
        )

        # =========================================
        # CSV
        # =========================================

        csv = (
            trades_df
            .to_csv(index=False)
            .encode("utf-8-sig")
        )

        st.download_button(
            "⬇️ 売買記録CSVをダウンロード",
            data=csv,
            file_name="ver4_2_trades.csv",
            mime="text/csv"
        )

    else:

        st.warning(
            "売買が発生しませんでした。"
            "最低BUYスコアを下げるか、"
            "急騰追い買い防止条件を確認してください。"
        )


# =========================================================
# Ver.4.2 条件説明
# =========================================================

st.divider()

st.subheader(
    "🧠 Ver.4.2 売買思想"
)

st.markdown(
    """
## 🟢 BUY条件

### 基本スコア：100点満点

- 25日線 > 75日線 …… **20点**
- 株価 > 200日線 …… **20点**
- 株価 > 25日線 …… **15点**
- 出来高条件 …… **15点**
- RSI適正 …… **15点**
- 25日線上向き …… **10点**
- 75日線上向き …… **5点**

### BUY判定

**最低BUYスコア：75点**

**85点以上：強BUY**

さらに、

- 株価2,000円以上
- RSI 45～65
- 25日線からの乖離が一定範囲以内

を要求します。

---

## 🛡️ Ver.4.2のリスク管理

### 損切り

**-7%**

大きな損失になる前に撤退します。

### 利益確定

**+15%からトレーリング開始**

利益が伸びた銘柄をできるだけ長く保有します。

### 25日線割れ

利益が5%以上ある場合、

**25日線割れ → 利益確定**

### 75日線割れ

トレンド悪化と判断して、

**75日線割れ → 撤退**

### 再エントリー制限

損切りした銘柄は、

**一定期間再購入しません。**

---

## 🚫 使用しない条件

### 明けの明星

**完全に使用しません。**

Ver.4.2では明けの明星を
銘柄選定条件・BUY条件・スコア条件の
いずれにも使用していません。

---

## 🎯 Ver.4.2の目的

単純に勝率を上げるのではなく、

**「悪い買いを減らす」**

ことを重視します。

特に、

- 最大DD
- Profit Factor
- 平均利益/平均損失
- 損切り回数
- 連続損失
- 銘柄別成績

を確認してVer.4.1と比較します。
"""
)
