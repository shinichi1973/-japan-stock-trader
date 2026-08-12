import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="日本株 仮想売買 Ver.2",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 仮想売買システム Ver.2")
st.caption("仮想売買専用。証券会社への実注文は行いません。")

# =========================
# 基本設定
# =========================
st.sidebar.header("⚙️ 基本設定")

initial_cash = st.sidebar.number_input(
    "初期資金",
    min_value=100000,
    max_value=100000000,
    value=1000000,
    step=100000
)

max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=50,
    value=10
)

max_per_position = st.sidebar.number_input(
    "1銘柄の最大購入額",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)

stop_loss = st.sidebar.slider(
    "損切り (%)",
    1, 30, 7
) / 100

take_profit = st.sidebar.slider(
    "利確 (%)",
    1, 100, 15
) / 100

rsi_max = st.sidebar.slider(
    "RSI上限",
    50, 90, 70
)

# =========================
# 銘柄選定条件
# =========================
st.sidebar.header("🎯 銘柄選定条件")

use_tick_top50 = st.sidebar.checkbox(
    "SBI ティック回数上位50",
    value=True
)

use_morning_star = st.sidebar.checkbox(
    "明けの明星成立",
    value=True
)

use_price_2000 = st.sidebar.checkbox(
    "株価2,000円以上",
    value=True
)


# =========================
# テクニカル指標
# =========================
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

    # =========================
    # 明けの明星
    # =========================
    body = (g["close"] - g["open"]).abs()
    avg_body = body.rolling(20).mean()

    first_bear = (
        g["close"].shift(2) <
        g["open"].shift(2)
    )

    first_large = (
        body.shift(2) >=
        avg_body.shift(2) * 1.2
    )

    middle_small = (
        body.shift(1) <=
        avg_body.shift(1) * 0.5
    )

    third_bull = (
        g["close"] >
        g["open"]
    )

    third_recovery = (
        g["close"] >=
        (
            g["open"].shift(2) +
            g["close"].shift(2)
        ) / 2
    )

    g["morning_star"] = (
        first_bear &
        first_large &
        middle_small &
        third_bull &
        third_recovery
    ).fillna(False)

    return g


# =========================
# バックテスト
# =========================
def backtest(df):

    df = df.copy()

    # 型を統一
    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df["ticker"] = df["ticker"].astype(str)

    df["open"] = pd.to_numeric(
        df["open"],
        errors="coerce"
    )

    df["close"] = pd.to_numeric(
        df["close"],
        errors="coerce"
    )

    df["volume"] = pd.to_numeric(
        df["volume"],
        errors="coerce"
    )

    if "tick_rank" not in df.columns:
        df["tick_rank"] = np.nan
    else:
        df["tick_rank"] = pd.to_numeric(
            df["tick_rank"],
            errors="coerce"
        )

    # 不正データを除外
    df = df.dropna(
        subset=["date", "ticker", "open", "close", "volume"]
    )

    df = df.sort_values(
        ["ticker", "date"]
    ).reset_index(drop=True)

    # 銘柄ごとに指標計算
    groups = []

    for ticker, group in df.groupby("ticker", sort=False):
        groups.append(
            indicators(group)
        )

    if not groups:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {}
        )

    df = pd.concat(
        groups,
        ignore_index=True
    )

    # =========================
    # 仮想資産
    # =========================
    cash = float(initial_cash)

    positions = {}

    trades = []

    curve = []

    dates = sorted(
        df["date"].dropna().unique()
    )

    # =========================
    # 日付ごとの処理
    # =========================
    for date in dates:

        day = df[
            df["date"] == date
        ].copy()

        # =========================
        # 売却判定
        # =========================
        for ticker in list(positions.keys()):

            row = day[
                day["ticker"] == ticker
            ]

            if row.empty:
                continue

            r = row.iloc[0]

            price = float(
                r["close"]
            )

            position = positions[ticker]

            entry_price = float(
                position["entry_price"]
            )

            shares = int(
                position["shares"]
            )

            ret = (
                price /
                entry_price
            ) - 1

            reason = None

            if ret <= -stop_loss:
                reason = "損切り"

            elif ret >= take_profit:
                reason = "利確"

            elif (
                pd.notna(r["ma25"])
                and price < float(r["ma25"])
            ):
                reason = "25日線割れ"

            if reason is not None:

                proceeds = shares * price

                cash += proceeds

                pnl = (
                    proceeds -
                    shares * entry_price
                )

                trades.append([
                    date,
                    ticker,
                    "SELL",
                    price,
                    shares,
                    reason,
                    pnl
                ])

                del positions[ticker]

        # =========================
        # 買い判定
        # =========================
        for _, r in day.iterrows():

            # ★重要
            # r.ticker ではなく r["ticker"]
            ticker = str(
                r["ticker"]
            )

            if ticker in positions:
                continue

            if len(positions) >= int(max_positions):
                continue

            # ティック上位50
            if use_tick_top50:

                tick_rank = r["tick_rank"]

                if (
                    pd.isna(tick_rank)
                    or float(tick_rank) > 50
                ):
                    continue

            # 株価2,000円以上
            if use_price_2000:

                if float(r["close"]) < 2000:
                    continue

            # 明けの明星
            if use_morning_star:

                if not bool(
                    r["morning_star"]
                ):
                    continue

            # 必要な指標
            indicator_cols = [
                "ma25",
                "ma75",
                "rsi",
                "vol20"
            ]

            if not all(
                pd.notna(r[col])
                for col in indicator_cols
            ):
                continue

            ma25 = float(r["ma25"])
            ma75 = float(r["ma75"])
            rsi = float(r["rsi"])
            close = float(r["close"])
            volume = float(r["volume"])
            vol20 = float(r["vol20"])

            # =========================
            # 買い条件
            # =========================
            if ma25 <= ma75:
                continue

            if close <= ma25:
                continue

            if rsi >= rsi_max:
                continue

            if volume <= vol20:
                continue

            # =========================
            # 購入株数
            # =========================
            price = close

            budget = min(
                float(max_per_position),
                cash
            )

            # 日本株100株単位
            shares = int(
                budget // (price * 100)
            ) * 100

            if shares <= 0:
                continue

            cost = shares * price

            if cost > cash:
                continue

            # =========================
            # 購入
            # =========================
            cash -= cost

            positions[ticker] = {
                "shares": shares,
                "entry_price": price
            }

            trades.append([
                date,
                ticker,
                "BUY",
                price,
                shares,
                "選定条件成立",
                0
            ])

        # =========================
        # 時価評価
        # =========================
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
                position["shares"] *
                current_price
            )

        equity = cash + market_value

        curve.append([
            date,
            equity,
            cash,
            len(positions)
        ])

    # =========================
    # 結果
    # =========================
    eq = pd.DataFrame(
        curve,
        columns=[
            "date",
            "equity",
            "cash",
            "positions"
        ]
    )

    tr = pd.DataFrame(
        trades,
        columns=[
            "date",
            "ticker",
            "side",
            "price",
            "shares",
            "reason",
            "pnl"
        ]
    )

    return eq, tr, positions


# =========================
# CSVアップロード
# =========================
uploaded = st.file_uploader(
    "📁 株価CSVをアップロード",
    type=["csv"]
)

st.markdown(
    "CSV必須: `date,ticker,open,close,volume`。"
    "ティック条件ONなら `tick_rank`（1～50）も必要です。"
)


if uploaded:

    try:

        df = pd.read_csv(
            uploaded
        )

    except Exception as e:

        st.error(
            f"CSVを読み込めませんでした: {e}"
        )

        st.stop()

    required = {
        "date",
        "ticker",
        "open",
        "close",
        "volume"
    }

    missing = (
        required -
        set(df.columns)
    )

    if missing:

        st.error(
            "不足している列: " +
            ", ".join(
                sorted(missing)
            )
        )

        st.stop()

    # ティック条件ONの場合
    if (
        use_tick_top50
        and "tick_rank" not in df.columns
    ):

        st.warning(
            "「SBI ティック回数上位50」がONですが、"
            "CSVに tick_rank 列がありません。"
        )

    active = []

    if use_tick_top50:
        active.append(
            "ティック上位50"
        )

    if use_morning_star:
        active.append(
            "明けの明星"
        )

    if use_price_2000:
        active.append(
            "株価2,000円以上"
        )

    st.success(
        f"{len(df):,} 行を読み込みました。"
    )

    st.write(
        "**ONの追加条件:** " +
        (
            " / ".join(active)
            if active
            else "なし"
        )
    )

    st.dataframe(
        df.tail(20),
        use_container_width=True
    )

    # =========================
    # バックテスト
    # =========================
    if st.button(
        "▶ バックテスト開始",
        type="primary"
    ):

        with st.spinner(
            "バックテストを実行中..."
        ):

            try:

                eq, tr, positions = backtest(
                    df
                )

            except Exception as e:

                st.error(
                    "バックテスト中にエラーが発生しました。"
                )

                st.exception(e)

                st.stop()

        if eq.empty:

            st.warning(
                "バックテストできるデータがありません。"
            )

            st.stop()

        # =========================
        # 最終結果
        # =========================
        final = float(
            eq.iloc[-1]["equity"]
        )

        pnl = (
            final -
            float(initial_cash)
        )

        pnl_rate = (
            pnl /
            float(initial_cash)
        )

        a, b, c = st.columns(3)

        a.metric(
            "最終資産",
            f"¥{final:,.0f}"
        )

        b.metric(
            "損益",
            f"¥{pnl:,.0f}",
            f"{pnl_rate:.2%}"
        )

        c.metric(
            "取引回数",
            len(tr)
        )

        # =========================
        # 資産推移
        # =========================
        st.subheader(
            "📊 資産推移"
        )

        st.line_chart(
            eq.set_index("date")["equity"]
        )

        # =========================
        # 売買履歴
        # =========================
        st.subheader(
            "🧾 売買履歴"
        )

        if not tr.empty:

            st.dataframe(
                tr.sort_values(
                    "date",
                    ascending=False
                ),
                use_container_width=True
            )

        else:

            st.info(
                "売買条件に一致する銘柄がありませんでした。"
            )

        # =========================
        # 現在の保有銘柄
        # =========================
        st.subheader(
            "📦 現在の保有銘柄"
        )

        if positions:

            position_rows = []

            for ticker, position in positions.items():

                position_rows.append({
                    "ticker": ticker,
                    "shares": position["shares"],
                    "entry_price": position["entry_price"]
                })

            st.dataframe(
                pd.DataFrame(position_rows),
                use_container_width=True
            )

        else:

            st.info(
                "現在の保有銘柄はありません。"
            )

else:

    st.info(
        "CSVをアップロードするとバックテストできます。"
    )


st.caption(
    "Ver.2 / 仮想売買のみ。"
    "SBI証券への自動注文・SBI画面の自動取得は実装していません。"
)
