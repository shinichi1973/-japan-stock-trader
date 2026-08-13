import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta

# yfinanceはrequirements.txtで追加します
try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================
st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3")
st.caption(
    "過去5年分の株価を自動取得してバックテストします。"
    "実際の証券会社への注文は行いません。"
)


# =========================================================
# サイドバー設定
# =========================================================
st.sidebar.header("⚙️ バックテスト設定")

initial_cash = st.sidebar.number_input(
    "初期資金（円）",
    min_value=100000,
    max_value=100000000,
    value=1000000,
    step=100000
)

max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=50,
    value=10,
    step=1
)

max_per_position = st.sidebar.number_input(
    "1銘柄の最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)

stop_loss = st.sidebar.slider(
    "損切り（%）",
    1,
    30,
    7
) / 100

take_profit = st.sidebar.slider(
    "利確（%）",
    1,
    100,
    15
) / 100

rsi_max = st.sidebar.slider(
    "RSI上限",
    50,
    90,
    70
)

st.sidebar.header("🎯 選定条件")

use_morning_star = st.sidebar.checkbox(
    "明けの明星",
    value=True
)

use_ma_trend = st.sidebar.checkbox(
    "25日線 ＞ 75日線",
    value=True
)

use_volume = st.sidebar.checkbox(
    "出来高20日平均超え",
    value=True
)

use_price_2000 = st.sidebar.checkbox(
    "株価2,000円以上",
    value=True
)


# =========================================================
# 銘柄設定
# =========================================================
st.subheader("📋 バックテストする日本株")

st.write(
    "日本株コードをカンマ区切りで入力してください。"
    "例：7203,6758,9984,8306,9432"
)

ticker_input = st.text_input(
    "日本株コード",
    value="7203,6758,9984,8306,9432"
)

st.info(
    "📅 株価データは実行時点から過去5年間を自動取得します。"
)


# =========================================================
# 銘柄コード変換
# =========================================================
def normalize_tickers(text):
    raw = text.replace("、", ",").replace(" ", ",").split(",")

    result = []

    for x in raw:
        x = x.strip()

        if not x:
            continue

        # すでに .T が付いている場合
        if x.upper().endswith(".T"):
            ticker = x.upper()
        else:
            # 日本株4桁コード
            ticker = x + ".T"

        if ticker not in result:
            result.append(ticker)

    return result


tickers = normalize_tickers(ticker_input)

if tickers:
    st.write("対象銘柄：", ", ".join(tickers))


# =========================================================
# 株価データ取得
# =========================================================
def download_stock_data(tickers):
    if yf is None:
        raise ImportError(
            "yfinanceがインストールされていません。"
            "requirements.txtにyfinanceを追加してください。"
        )

    end_date = date.today()
    start_date = end_date - timedelta(days=365 * 5 + 30)

    all_data = []
    errors = []

    progress = st.progress(0)

    for i, ticker in enumerate(tickers):

        try:
            data = yf.download(
                ticker,
                start=start_date,
                end=end_date + timedelta(days=1),
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if data is None or data.empty:
                errors.append(f"{ticker}: データなし")
                progress.progress((i + 1) / len(tickers))
                continue

            # yfinanceのMultiIndex対策
            if isinstance(data.columns, pd.MultiIndex):
                try:
                    data.columns = data.columns.get_level_values(0)
                except Exception:
                    pass

            data = data.reset_index()

            # 列名を統一
            rename_map = {}

            for col in data.columns:
                name = str(col).lower()

                if name == "date":
                    rename_map[col] = "date"
                elif name == "open":
                    rename_map[col] = "open"
                elif name == "high":
                    rename_map[col] = "high"
                elif name == "low":
                    rename_map[col] = "low"
                elif name == "close":
                    rename_map[col] = "close"
                elif name == "volume":
                    rename_map[col] = "volume"

            data = data.rename(columns=rename_map)

            required = [
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]

            if not all(c in data.columns for c in required):
                errors.append(f"{ticker}: 必要列がありません")
                progress.progress((i + 1) / len(tickers))
                continue

            data = data[required].copy()
            data["ticker"] = ticker

            for c in ["open", "high", "low", "close", "volume"]:
                data[c] = pd.to_numeric(
                    data[c],
                    errors="coerce"
                )

            data = data.dropna(
                subset=["date", "open", "high", "low", "close"]
            )

            if not data.empty:
                all_data.append(data)

        except Exception as e:
            errors.append(f"{ticker}: {str(e)}")

        progress.progress((i + 1) / len(tickers))

    progress.empty()

    if not all_data:
        return pd.DataFrame(), errors

    result = pd.concat(
        all_data,
        ignore_index=True
    )

    result["date"] = pd.to_datetime(result["date"])

    result = result.sort_values(
        ["ticker", "date"]
    ).reset_index(drop=True)

    return result, errors


# =========================================================
# テクニカル指標
# =========================================================
def add_indicators(g):

    g = g.sort_values("date").copy()

    # 移動平均
    g["ma25"] = g["close"].rolling(
        25,
        min_periods=25
    ).mean()

    g["ma75"] = g["close"].rolling(
        75,
        min_periods=75
    ).mean()

    # RSI
    delta = g["close"].diff()

    gain = delta.clip(
        lower=0
    ).rolling(
        14,
        min_periods=14
    ).mean()

    loss = (-delta.clip(
        upper=0
    )).rolling(
        14,
        min_periods=14
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    g["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # 出来高
    g["vol20"] = g["volume"].rolling(
        20,
        min_periods=20
    ).mean()

    # =====================================================
    # 明けの明星
    # =====================================================

    body = (
        g["close"] - g["open"]
    ).abs()

    avg_body = body.rolling(
        20,
        min_periods=20
    ).mean()

    first_bear = (
        g["close"].shift(2)
        < g["open"].shift(2)
    )

    first_large = (
        body.shift(2)
        >= avg_body.shift(2) * 1.2
    )

    middle_small = (
        body.shift(1)
        <= avg_body.shift(1) * 0.5
    )

    third_bull = (
        g["close"] > g["open"]
    )

    third_recovery = (
        g["close"]
        >= (
            g["open"].shift(2)
            + g["close"].shift(2)
        ) / 2
    )

    g["morning_star"] = (
        first_bear
        & first_large
        & middle_small
        & third_bull
        & third_recovery
    ).fillna(False)

    return g


# =========================================================
# バックテスト
# =========================================================
def run_backtest(df):

    df = df.copy()

    df["date"] = pd.to_datetime(df["date"])

    processed = []

    for ticker in df["ticker"].unique():

        g = df[
            df["ticker"] == ticker
        ].copy()

        if len(g) < 80:
            continue

        g = add_indicators(g)

        processed.append(g)

    if not processed:
        return (
            pd.DataFrame(),
            pd.DataFrame()
        )

    df = pd.concat(
        processed,
        ignore_index=True
    )

    df = df.sort_values(
        ["date", "ticker"]
    ).reset_index(drop=True)

    cash = float(initial_cash)

    positions = {}

    trades = []

    equity_curve = []

    dates = sorted(
        df["date"].dropna().unique()
    )

    for current_date in dates:

        day = df[
            df["date"] == current_date
        ]

        # =================================================
        # 保有銘柄の決済
        # =================================================
        for ticker in list(positions.keys()):

            row = day[
                day["ticker"] == ticker
            ]

            if row.empty:
                continue

            r = row.iloc[0]

            price = float(r["close"])

            position = positions[ticker]

            entry_price = position["entry_price"]
            shares = position["shares"]

            ret = (
                price / entry_price
            ) - 1

            reason = None

            # 損切り
            if ret <= -stop_loss:
                reason = "損切り"

            # 利確
            elif ret >= take_profit:
                reason = "利確"

            # 25日線割れ
            elif (
                pd.notna(r["ma25"])
                and price < r["ma25"]
            ):
                reason = "25日線割れ"

            if reason:

                proceeds = shares * price

                cash += proceeds

                pnl = (
                    price - entry_price
                ) * shares

                trades.append({
                    "date": current_date,
                    "ticker": ticker,
                    "side": "SELL",
                    "price": price,
                    "shares": shares,
                    "reason": reason,
                    "pnl": pnl
                })

                del positions[ticker]

        # =================================================
        # 新規購入
        # =================================================
        for _, r in day.iterrows():

            ticker = str(r["ticker"])

            if ticker in positions:
                continue

            if len(positions) >= max_positions:
                continue

            close = float(r["close"])

            # 株価条件
            if use_price_2000:
                if close < 2000:
                    continue

            # 明けの明星
            if use_morning_star:
                if not bool(r["morning_star"]):
                    continue

            # 指標不足
            indicators_ok = all(
                pd.notna(r[x])
                for x in [
                    "ma25",
                    "ma75",
                    "rsi",
                    "vol20"
                ]
            )

            if not indicators_ok:
                continue

            # MAトレンド
            if use_ma_trend:
                if r["ma25"] <= r["ma75"]:
                    continue

                if close <= r["ma25"]:
                    continue

            # RSI
            if r["rsi"] >= rsi_max:
                continue

            # 出来高
            if use_volume:
                if r["volume"] <= r["vol20"]:
                    continue

            # 購入金額
            budget = min(
                max_per_position,
                cash
            )

            # 日本株100株単位
            shares = int(
                budget / (close * 100)
            ) * 100

            if shares <= 0:
                continue

            cost = shares * close

            if cost > cash:
                continue

            cash -= cost

            positions[ticker] = {
                "shares": shares,
                "entry_price": close
            }

            trades.append({
                "date": current_date,
                "ticker": ticker,
                "side": "BUY",
                "price": close,
                "shares": shares,
                "reason": "選定条件成立",
                "pnl": 0
            })

        # =================================================
        # 資産評価
        # =================================================
        market_value = 0

        for ticker, position in positions.items():

            row = day[
                day["ticker"] == ticker
            ]

            if row.empty:
                continue

            current_price = float(
                row.iloc[0]["close"]
            )

            market_value += (
                position["shares"]
                * current_price
            )

        equity = cash + market_value

        equity_curve.append({
            "date": current_date,
            "equity": equity,
            "cash": cash,
            "positions": len(positions)
        })

    eq = pd.DataFrame(
        equity_curve
    )

    tr = pd.DataFrame(
        trades
    )

    return eq, tr


# =========================================================
# バックテスト開始ボタン
# =========================================================

st.divider()

st.subheader("🚀 バックテスト")

# ボタンは必ず表示
start_button = st.button(
    "▶ バックテスト開始",
    type="primary",
    use_container_width=True
)


if start_button:

    if not tickers:

        st.error(
            "日本株コードを1つ以上入力してください。"
        )

    elif yf is None:

        st.error(
            "yfinanceがインストールされていません。"
            "requirements.txtを確認してください。"
        )

    else:

        st.info(
            "📥 過去5年分の株価データを取得しています。"
            "銘柄数によって少し時間がかかります。"
        )

        try:

            stock_df, errors = download_stock_data(
                tickers
            )

            if errors:

                with st.expander(
                    "⚠️ 一部の銘柄で取得できなかった場合はこちら"
                ):
                    for error in errors:
                        st.write(error)

            if stock_df.empty:

                st.error(
                    "株価データを取得できませんでした。"
                    "日本株コードを確認してください。"
                )

                st.stop()

            st.success(
                f"✅ {len(stock_df):,} 行の株価データを取得しました。"
            )

            # 取得期間
            min_date = stock_df["date"].min()
            max_date = stock_df["date"].max()

            st.write(
                f"📅 データ期間："
                f"{min_date:%Y-%m-%d}"
                f" ～ "
                f"{max_date:%Y-%m-%d}"
            )

            st.write(
                f"📊 対象銘柄数："
                f"{stock_df['ticker'].nunique()}銘柄"
            )

            # =================================================
            # バックテスト実行
            # =================================================

            with st.spinner(
                "バックテストを計算しています..."
            ):

                eq, tr = run_backtest(
                    stock_df
                )

            if eq.empty:

                st.error(
                    "バックテストに使用できるデータがありません。"
                    "条件を少し緩めて再実行してください。"
                )

                st.stop()

            # =================================================
            # 結果
            # =================================================

            final_asset = float(
                eq.iloc[-1]["equity"]
            )

            pnl = (
                final_asset
                - initial_cash
            )

            return_rate = (
                pnl / initial_cash
            )

            max_asset = eq["equity"].cummax()

            drawdown = (
                eq["equity"] / max_asset - 1
            )

            max_drawdown = drawdown.min()

            # =================================================
            # 結果表示
            # =================================================

            st.divider()

            st.header("📊 バックテスト結果")

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "最終資産",
                f"¥{final_asset:,.0f}"
            )

            c2.metric(
                "損益",
                f"¥{pnl:,.0f}",
                f"{return_rate:.2%}"
            )

            c3.metric(
                "取引回数",
                f"{len(tr):,}"
            )

            c4.metric(
                "最大ドローダウン",
                f"{max_drawdown:.2%}"
            )

            # =================================================
            # 資産推移
            # =================================================

            st.subheader("📈 資産推移")

            chart_df = eq.set_index(
                "date"
            )["equity"]

            st.line_chart(
                chart_df
            )

            # =================================================
            # 売買履歴
            # =================================================

            st.subheader("🧾 売買履歴")

            if tr.empty:

                st.info(
                    "売買条件に一致した銘柄はありませんでした。"
                )

            else:

                display_tr = tr.sort_values(
                    "date",
                    ascending=False
                ).copy()

                display_tr["date"] = pd.to_datetime(
                    display_tr["date"]
                ).dt.strftime(
                    "%Y-%m-%d"
                )

                st.dataframe(
                    display_tr,
                    use_container_width=True
                )

                # CSVダウンロード
                csv = tr.to_csv(
                    index=False
                ).encode("utf-8-sig")

                st.download_button(
                    "⬇️ 売買履歴CSVを保存",
                    data=csv,
                    file_name="backtest_trades_ver3.csv",
                    mime="text/csv"
                )

            # =================================================
            # 使用データ
            # =================================================

            with st.expander(
                "📋 取得した株価データを確認"
            ):

                st.dataframe(
                    stock_df.tail(100),
                    use_container_width=True
                )

        except Exception as e:

            st.error(
                "⚠️ バックテスト中にエラーが発生しました。"
            )

            st.write(
                "エラー内容：",
                str(e)
            )

            st.info(
                "Ver.2のmainブランチには影響ありません。"
            )


# =========================================================
# 注意書き
# =========================================================

st.divider()

st.caption(
    "Ver.3 / 仮想売買専用。"
    "証券会社への実注文は行いません。"
)

st.caption(
    "株価データ取得にはYahoo Finance経由のyfinanceを使用します。"
)

st.caption(
    "バックテスト結果は過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
