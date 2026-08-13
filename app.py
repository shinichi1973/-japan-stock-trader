import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================

st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3.2",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.2")

st.caption(
    "過去5年間の日足データを使用した仮想売買シミュレーションです。"
    "実際の注文は行いません。"
)


# =========================================================
# サイドバー設定
# =========================================================

st.sidebar.header("⚙️ 基本設定")

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

commission_rate = st.sidebar.number_input(
    "売買手数料率（片道・%）",
    min_value=0.0,
    max_value=1.0,
    value=0.0,
    step=0.01
) / 100


# =========================================================
# 銘柄選定条件
# =========================================================

st.sidebar.header("🎯 銘柄選定条件")

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

diagnostic_mode = st.sidebar.checkbox(
    "🔎 条件診断を表示",
    value=True
)


# =========================================================
# 銘柄
# =========================================================

st.subheader("📋 バックテスト銘柄")

st.write(
    "日本株コードをカンマ区切りで入力してください。"
)

ticker_input = st.text_input(
    "日本株コード",
    value="7203,6758,9984,8306,9432"
)

st.info(
    "📅 実行時点から過去5年間の株価データを取得します。"
)


def normalize_tickers(text):

    raw = (
        text
        .replace("、", ",")
        .replace(" ", ",")
        .split(",")
    )

    result = []

    for x in raw:

        x = x.strip()

        if not x:
            continue

        if x.upper().endswith(".T"):
            ticker = x.upper()
        else:
            ticker = x + ".T"

        if ticker not in result:
            result.append(ticker)

    return result


tickers = normalize_tickers(
    ticker_input
)

st.write(
    "対象銘柄：",
    ", ".join(tickers)
)


# =========================================================
# データ取得
# =========================================================

@st.cache_data(ttl=3600)
def download_stock_data(tickers):

    if yf is None:
        raise ImportError(
            "yfinanceがインストールされていません。"
        )

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(days=365 * 5 + 30)
    )

    all_data = []
    errors = []

    for ticker in tickers:

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

                errors.append(
                    f"{ticker}: データなし"
                )

                continue

            if isinstance(
                data.columns,
                pd.MultiIndex
            ):

                data.columns = (
                    data.columns
                    .get_level_values(0)
                )

            data = data.reset_index()

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

            data = data.rename(
                columns=rename_map
            )

            required = [
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]

            if not all(
                c in data.columns
                for c in required
            ):

                errors.append(
                    f"{ticker}: 必要列がありません"
                )

                continue

            data = data[
                required
            ].copy()

            data["ticker"] = ticker

            for c in [
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]:

                data[c] = pd.to_numeric(
                    data[c],
                    errors="coerce"
                )

            data = data.dropna(
                subset=[
                    "date",
                    "open",
                    "high",
                    "low",
                    "close"
                ]
            )

            if not data.empty:
                all_data.append(data)

        except Exception as e:

            errors.append(
                f"{ticker}: {str(e)}"
            )

    if not all_data:

        return (
            pd.DataFrame(),
            errors
        )

    result = pd.concat(
        all_data,
        ignore_index=True
    )

    result["date"] = pd.to_datetime(
        result["date"]
    )

    result = result.sort_values(
        ["ticker", "date"]
    ).reset_index(drop=True)

    return result, errors


# =========================================================
# 指標計算
# =========================================================

def add_indicators(g):

    g = g.sort_values(
        "date"
    ).copy()

    # 移動平均
    g["ma25"] = (
        g["close"]
        .rolling(25)
        .mean()
    )

    g["ma75"] = (
        g["close"]
        .rolling(75)
        .mean()
    )

    # RSI
    delta = g["close"].diff()

    gain = (
        delta
        .clip(lower=0)
        .rolling(14)
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .rolling(14)
        .mean()
    )

    rs = gain / loss.replace(
        0,
        np.nan
    )

    g["rsi"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    # 出来高
    g["vol20"] = (
        g["volume"]
        .rolling(20)
        .mean()
    )

    # =====================================================
    # 明けの明星
    # =====================================================

    body = (
        g["close"]
        -
        g["open"]
    ).abs()

    avg_body = (
        body
        .rolling(20)
        .mean()
    )

    first_bear = (
        g["close"].shift(2)
        <
        g["open"].shift(2)
    )

    first_large = (
        body.shift(2)
        >=
        avg_body.shift(2) * 1.2
    )

    middle_small = (
        body.shift(1)
        <=
        avg_body.shift(1) * 0.5
    )

    third_bull = (
        g["close"]
        >
        g["open"]
    )

    third_recovery = (
        g["close"]
        >=
        (
            g["open"].shift(2)
            +
            g["close"].shift(2)
        ) / 2
    )

    g["morning_star"] = (
        first_bear
        &
        first_large
        &
        middle_small
        &
        third_bull
        &
        third_recovery
    ).fillna(False)

    return g


# =========================================================
# 買い条件
# =========================================================

def buy_condition(row):

    if pd.isna(row["ma25"]):
        return False

    if pd.isna(row["ma75"]):
        return False

    if pd.isna(row["rsi"]):
        return False

    if pd.isna(row["vol20"]):
        return False

    if (
        use_price_2000
        and
        row["close"] < 2000
    ):
        return False

    if (
        use_morning_star
        and
        not bool(row["morning_star"])
    ):
        return False

    if use_ma_trend:

        if not (
            row["ma25"]
            >
            row["ma75"]
            and
            row["close"]
            >
            row["ma25"]
        ):
            return False

    if use_volume:

        if not (
            row["volume"]
            >
            row["vol20"]
        ):
            return False

    if row["rsi"] >= rsi_max:
        return False

    return True


# =========================================================
# 条件診断
# =========================================================

def diagnostic(df):

    results = []

    for ticker, g in df.groupby(
        "ticker"
    ):

        g = add_indicators(g)

        valid = g[
            [
                "ma25",
                "ma75",
                "rsi",
                "vol20"
            ]
        ].notna().all(axis=1)

        g = g[valid].copy()

        if g.empty:

            results.append({
                "銘柄": ticker,
                "判定対象": 0,
                "株価2000円以上": 0,
                "明けの明星": 0,
                "25日線>75日線": 0,
                "株価>25日線": 0,
                "出来高": 0,
                "RSI": 0,
                "全条件一致": 0
            })

            continue

        price_ok = (
            g["close"] >= 2000
        )

        morning_ok = (
            g["morning_star"]
        )

        ma_ok = (
            g["ma25"]
            >
            g["ma75"]
        )

        price_ma_ok = (
            g["close"]
            >
            g["ma25"]
        )

        volume_ok = (
            g["volume"]
            >
            g["vol20"]
        )

        rsi_ok = (
            g["rsi"]
            <
            rsi_max
        )

        all_ok = pd.Series(
            True,
            index=g.index
        )

        if use_price_2000:
            all_ok &= price_ok

        if use_morning_star:
            all_ok &= morning_ok

        if use_ma_trend:

            all_ok &= (
                ma_ok
                &
                price_ma_ok
            )

        if use_volume:
            all_ok &= volume_ok

        all_ok &= rsi_ok

        results.append({

            "銘柄": ticker,

            "判定対象":
                len(g),

            "株価2000円以上":
                int(price_ok.sum()),

            "明けの明星":
                int(morning_ok.sum()),

            "25日線>75日線":
                int(ma_ok.sum()),

            "株価>25日線":
                int(price_ma_ok.sum()),

            "出来高":
                int(volume_ok.sum()),

            "RSI":
                int(rsi_ok.sum()),

            "全条件一致":
                int(all_ok.sum())
        })

    return pd.DataFrame(
        results
    )


# =========================================================
# バックテスト
# =========================================================

def run_backtest(df):

    processed = []

    for ticker, g in df.groupby(
        "ticker"
    ):

        if len(g) < 80:
            continue

        processed.append(
            add_indicators(g)
        )

    if not processed:

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {},
            {}
        )

    data = pd.concat(
        processed,
        ignore_index=True
    )

    data = data.sort_values(
        ["date", "ticker"]
    ).reset_index(drop=True)

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    curve = []

    signal_count = 0

    buy_count = 0

    sell_count = 0

    realized_pnl = 0.0

    # =====================================================
    # 日付ループ
    # =====================================================

    dates = sorted(
        data["date"].unique()
    )

    for i, current_date in enumerate(dates):

        day = data[
            data["date"]
            ==
            current_date
        ]

        # =================================================
        # 決済
        # =================================================

        for ticker in list(
            positions.keys()
        ):

            row = day[
                day["ticker"]
                ==
                ticker
            ]

            if row.empty:
                continue

            r = row.iloc[0]

            price = float(
                r["close"]
            )

            p = positions[ticker]

            ret = (
                price
                /
                p["entry_price"]
                - 1
            )

            reason = None

            if ret <= -stop_loss:

                reason = "損切り"

            elif ret >= take_profit:

                reason = "利確"

            elif (
                pd.notna(r["ma25"])
                and
                price < r["ma25"]
            ):

                reason = "25日線割れ"

            if reason is not None:

                gross = (
                    price
                    -
                    p["entry_price"]
                ) * p["shares"]

                sell_cost = (
                    price
                    *
                    p["shares"]
                    *
                    commission_rate
                )

                net_pnl = (
                    gross
                    -
                    p["buy_cost"]
                    -
                    sell_cost
                )

                proceeds = (
                    price
                    *
                    p["shares"]
                )

                cash += (
                    proceeds
                    -
                    sell_cost
                )

                realized_pnl += net_pnl

                trades.append({

                    "date":
                        current_date,

                    "ticker":
                        ticker,

                    "side":
                        "SELL",

                    "price":
                        price,

                    "shares":
                        p["shares"],

                    "reason":
                        reason,

                    "pnl":
                        net_pnl
                })

                sell_count += 1

                del positions[ticker]

        # =================================================
        # 購入
        #
        # 条件成立日の終値を確認し、
        # 翌営業日の始値で購入する
        # =================================================

        if i < len(dates) - 1:

            next_date = dates[i + 1]

            next_day = data[
                data["date"]
                ==
                next_date
            ]

            for _, r in day.iterrows():

                ticker = str(
                    r["ticker"]
                )

                if ticker in positions:
                    continue

                if len(positions) >= max_positions:
                    continue

                if not buy_condition(r):
                    continue

                signal_count += 1

                next_row = next_day[
                    next_day["ticker"]
                    ==
                    ticker
                ]

                if next_row.empty:
                    continue

                nr = next_row.iloc[0]

                entry_price = float(
                    nr["open"]
                )

                if entry_price <= 0:
                    continue

                budget = min(
                    float(max_per_position),
                    float(cash)
                )

                shares = (
                    int(
                        budget
                        /
                        (
                            entry_price
                            * 100
                        )
                    )
                    * 100
                )

                if shares <= 0:
                    continue

                gross_cost = (
                    shares
                    *
                    entry_price
                )

                buy_cost = (
                    gross_cost
                    *
                    commission_rate
                )

                total_cost = (
                    gross_cost
                    +
                    buy_cost
                )

                if total_cost > cash:
                    continue

                cash -= total_cost

                positions[ticker] = {

                    "shares":
                        shares,

                    "entry_price":
                        entry_price,

                    "buy_cost":
                        buy_cost,

                    "entry_date":
                        next_date
                }

                trades.append({

                    "date":
                        next_date,

                    "ticker":
                        ticker,

                    "side":
                        "BUY",

                    "price":
                        entry_price,

                    "shares":
                        shares,

                    "reason":
                        "条件成立→翌日始値購入",

                    "pnl":
                        0.0
                })

                buy_count += 1

        # =================================================
        # 資産評価
        # =================================================

        market_value = 0.0

        for ticker, p in positions.items():

            row = day[
                day["ticker"]
                ==
                ticker
            ]

            if not row.empty:

                market_value += (
                    p["shares"]
                    *
                    float(
                        row.iloc[0]["close"]
                    )
                )

        equity = (
            cash
            +
            market_value
        )

        curve.append({

            "date":
                current_date,

            "equity":
                equity,

            "cash":
                cash,

            "market_value":
                market_value,

            "positions":
                len(positions)
        })

    # =====================================================
    # DataFrame化
    # =====================================================

    eq = pd.DataFrame(
        curve
    )

    tr = pd.DataFrame(
        trades
    )

    # =====================================================
    # 最終日の未決済評価
    # =====================================================

    unrealized_total = 0.0

    if (
        positions
        and
        not eq.empty
    ):

        last_date = eq.iloc[-1]["date"]

        last_day = data[
            data["date"]
            ==
            last_date
        ]

        for ticker, p in positions.items():

            row = last_day[
                last_day["ticker"]
                ==
                ticker
            ]

            if row.empty:
                continue

            final_price = float(
                row.iloc[0]["close"]
            )

            gross_unrealized = (
                final_price
                -
                p["entry_price"]
            ) * p["shares"]

            sell_cost = (
                final_price
                *
                p["shares"]
                *
                commission_rate
            )

            net_unrealized = (
                gross_unrealized
                -
                p["buy_cost"]
                -
                sell_cost
            )

            unrealized_total += (
                net_unrealized
            )

            tr = pd.concat(
                [
                    tr,
                    pd.DataFrame([{

                        "date":
                            last_date,

                        "ticker":
                            ticker,

                        "side":
                            "HOLD",

                        "price":
                            final_price,

                        "shares":
                            p["shares"],

                        "reason":
                            "最終日評価（含み損益）",

                        "pnl":
                            net_unrealized
                    }])
                ],
                ignore_index=True
            )

    stats = {

        "signal_count":
            signal_count,

        "buy_count":
            buy_count,

        "sell_count":
            sell_count,

        "realized_pnl":
            realized_pnl,

        "unrealized_pnl":
            unrealized_total
    }

    return (
        eq,
        tr,
        positions,
        stats
    )


# =========================================================
# 実行
# =========================================================

st.divider()

st.subheader(
    "🚀 バックテスト"
)

start_button = st.button(
    "▶ バックテスト開始",
    type="primary",
    use_container_width=True
)


if start_button:

    if not tickers:

        st.error(
            "日本株コードを入力してください。"
        )

        st.stop()

    if yf is None:

        st.error(
            "yfinanceがありません。"
        )

        st.stop()

    # =====================================================
    # データ取得
    # =====================================================

    with st.spinner(
        "📥 過去5年分の株価データを取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )

    if errors:

        with st.expander(
            "⚠️ データ取得エラー"
        ):

            for e in errors:
                st.write(e)

    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"✅ {len(stock_df):,}行のデータを取得しました。"
    )

    st.write(
        f"📅 {stock_df['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{stock_df['date'].max():%Y-%m-%d}"
    )

    st.write(
        f"📊 対象銘柄："
        f"{stock_df['ticker'].nunique()}銘柄"
    )

    # =====================================================
    # 条件診断
    # =====================================================

    if diagnostic_mode:

        st.divider()

        st.header(
            "🔎 条件診断"
        )

        diag = diagnostic(
            stock_df
        )

        st.dataframe(
            diag,
            use_container_width=True
        )

        total = int(
            diag[
                "全条件一致"
            ].sum()
        )

        if total == 0:

            st.warning(
                "⚠️ 過去5年間で全条件を同時に満たした日がありません。"
            )

        else:

            st.success(
                f"🎯 全条件一致：{total:,}件"
            )

    # =====================================================
    # バックテスト
    # =====================================================

    with st.spinner(
        "📊 バックテスト計算中..."
    ):

        (
            eq,
            tr,
            positions,
            stats
        ) = run_backtest(
            stock_df
        )

    if eq.empty:

        st.error(
            "バックテスト可能なデータがありません。"
        )

        st.stop()

    # =====================================================
    # 結果
    # =====================================================

    final_asset = float(
        eq.iloc[-1]["equity"]
    )

    pnl = (
        final_asset
        -
        float(initial_cash)
    )

    return_rate = (
        pnl
        /
        float(initial_cash)
    )

    max_asset = (
        eq["equity"]
        .cummax()
    )

    drawdown = (
        eq["equity"]
        /
        max_asset
        - 1
    )

    max_drawdown = float(
        drawdown.min()
    )

    # =====================================================
    # 結果表示
    # =====================================================

    st.divider()

    st.header(
        "📊 バックテスト結果"
    )

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
        "買付件数",
        f"{stats['buy_count']:,}"
    )

    c4.metric(
        "売却件数",
        f"{stats['sell_count']:,}"
    )

    c5, c6, c7, c8 = st.columns(4)

    c5.metric(
        "条件成立数",
        f"{stats['signal_count']:,}"
    )

    c6.metric(
        "確定損益",
        f"¥{stats['realized_pnl']:,.0f}"
    )

    c7.metric(
        "含み損益",
        f"¥{stats['unrealized_pnl']:,.0f}"
    )

    c8.metric(
        "最大DD",
        f"{max_drawdown:.2%}"
    )

    # =====================================================
    # 資産推移
    # =====================================================

    st.subheader(
        "📈 資産推移"
    )

    chart_df = eq.copy()

    chart_df["date"] = pd.to_datetime(
        chart_df["date"],
        errors="coerce"
    )

    chart_df = chart_df.dropna(
        subset=["date"]
    )

    if not chart_df.empty:

        st.line_chart(
            chart_df.set_index(
                "date"
            )["equity"]
        )

    # =====================================================
    # 売買履歴
    # =====================================================

    st.subheader(
        "🧾 売買履歴"
    )

    if tr is None:

        tr = pd.DataFrame()

    if not isinstance(
        tr,
        pd.DataFrame
    ):

        tr = pd.DataFrame(tr)

    if tr.empty:

        st.warning(
            "売買履歴はありません。"
        )

    else:

        display_tr = tr.copy()

        if "date" in display_tr.columns:

            display_tr["date"] = pd.to_datetime(
                display_tr["date"],
                errors="coerce"
            )

            display_tr = (
                display_tr
                .sort_values(
                    "date",
                    ascending=False
                )
                .copy()
            )

            display_tr["date"] = (
                display_tr["date"]
                .dt.strftime(
                    "%Y-%m-%d"
                )
            )

        if "pnl" in display_tr.columns:

            display_tr["pnl"] = (
                pd.to_numeric(
                    display_tr["pnl"],
                    errors="coerce"
                )
                .round(0)
            )

        st.dataframe(
            display_tr,
            use_container_width=True
        )

        csv = (
            tr
            .to_csv(
                index=False
            )
            .encode(
                "utf-8-sig"
            )
        )

        st.download_button(
            "⬇️ 売買履歴CSVを保存",
            data=csv,
            file_name="backtest_trades_ver3_2.csv",
            mime="text/csv"
        )

    # =====================================================
    # 未決済銘柄
    # =====================================================

    if positions:

        st.subheader(
            "📌 最終日の未決済銘柄"
        )

        rows = []

        last_date = eq.iloc[-1]["date"]

        last_day = stock_df[
            stock_df["date"]
            ==
            last_date
        ]

        for ticker, p in positions.items():

            row = last_day[
                last_day["ticker"]
                ==
                ticker
            ]

            if row.empty:
                continue

            final_price = float(
                row.iloc[0]["close"]
            )

            gross = (
                final_price
                -
                p["entry_price"]
            ) * p["shares"]

            sell_cost = (
                final_price
                *
                p["shares"]
                *
                commission_rate
            )

            unrealized = (
                gross
                -
                p["buy_cost"]
                -
                sell_cost
            )

            rows.append({

                "銘柄":
                    ticker,

                "購入日":
                    p["entry_date"],

                "保有株数":
                    p["shares"],

                "購入価格":
                    p["entry_price"],

                "最終価格":
                    final_price,

                "含み損益":
                    unrealized
            })

        if rows:

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True
            )

        else:

            st.info(
                "未決済銘柄の価格データがありません。"
            )

    else:

        st.info(
            "📭 最終日時点で未決済の銘柄はありません。"
        )

    # =====================================================
    # 取得データ
    # =====================================================

    with st.expander(
        "📋 取得データ確認"
    ):

        st.dataframe(
            stock_df.tail(100),
            use_container_width=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.2 / 仮想売買専用。証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
