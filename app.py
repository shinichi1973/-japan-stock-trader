import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import io
import requests


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
    page_title="日本株 自動バックテスト Ver.3.5",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.5")

st.caption(
    "日経225を中心に、過去5年の日足データで仮想売買を検証します。"
    "実際の注文は行いません。"
)


# =========================================================
# サイドバー
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
    value=10
)

max_per_position = st.sidebar.number_input(
    "1銘柄の最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)


# =========================================================
# 売買設定
# =========================================================

st.sidebar.header("💰 売買設定")

stop_loss = st.sidebar.slider(
    "損切り（%）",
    1,
    30,
    7
)

take_profit = st.sidebar.slider(
    "利確（%）",
    1,
    50,
    15
)

rsi_max = st.sidebar.slider(
    "RSI上限",
    50,
    90,
    60
)


# =========================================================
# 選定条件
# =========================================================

st.sidebar.header("🎯 銘柄選定条件")

use_morning_star = st.sidebar.checkbox(
    "明けの明星",
    value=False
)

use_ma_trend = st.sidebar.checkbox(
    "25日線 ＞ 75日線 ＆ 株価 ＞ 25日線",
    value=True
)

use_volume = st.sidebar.checkbox(
    "出来高20日平均超え",
    value=True
)

use_price_2000 = st.sidebar.checkbox(
    "株価2,000円以上",
    value=False
)


# =========================================================
# 表示設定
# =========================================================

st.sidebar.header("🔎 表示設定")

diagnostic_mode = st.sidebar.checkbox(
    "条件診断",
    value=True
)

optimization_mode = st.sidebar.checkbox(
    "自動パラメータ最適化",
    value=True
)

top10_mode = st.sidebar.checkbox(
    "最適条件TOP10",
    value=True
)


# =========================================================
# 日経225取得
# =========================================================

@st.cache_data(ttl=86400)
def get_nikkei225_tickers():

    """
    WikipediaのNikkei 225ページから
    4桁コードを取得する。

    取得できない場合は空リストを返す。
    """

    url = "https://en.wikipedia.org/wiki/Nikkei_225"

    try:

        headers = {
            "User-Agent":
                "Mozilla/5.0"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        tables = pd.read_html(
            io.StringIO(
                response.text
            )
        )

        codes = []

        for table in tables:

            for col in table.columns:

                series = table[col].astype(
                    str
                )

                for value in series:

                    value = value.strip()

                    # 日本株コードは4桁数字
                    if (
                        value.isdigit()
                        and len(value) == 4
                    ):

                        if value not in codes:

                            codes.append(
                                value
                            )

        # 225銘柄前後に絞る
        codes = list(
            dict.fromkeys(
                codes
            )
        )

        if len(codes) >= 200:

            return [
                code + ".T"
                for code in codes
            ]

        return []

    except Exception:

        return []


# =========================================================
# 銘柄選択
# =========================================================

st.subheader("📋 バックテスト銘柄")

use_nikkei225 = st.checkbox(
    "🇯🇵 日経225全銘柄を使用",
    value=True
)

if use_nikkei225:

    with st.spinner(
        "🇯🇵 日経225銘柄一覧を取得中..."
    ):

        nikkei_tickers = (
            get_nikkei225_tickers()
        )

    if nikkei_tickers:

        tickers = nikkei_tickers

        st.success(
            f"✅ 日経225銘柄を取得しました："
            f"{len(tickers)}銘柄"
        )

    else:

        st.error(
            "❌ 日経225銘柄一覧を取得できませんでした。"
        )

        st.info(
            "下の手入力欄から銘柄を入力して実行できます。"
        )

        ticker_input = st.text_input(
            "日本株コード（カンマ区切り）",
            value="7203,6758,9984,8306,9432"
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

else:

    ticker_input = st.text_input(
        "日本株コード（カンマ区切り）",
        value="7203,6758,9984,8306,9432"
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
    f"📊 対象銘柄数：{len(tickers)}"
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
        -
        timedelta(
            days=365 * 5 + 40
        )
    )

    try:

        data = yf.download(
            list(tickers),
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker"
        )

    except Exception as e:

        return (
            pd.DataFrame(),
            [str(e)]
        )

    if data is None or data.empty:

        return (
            pd.DataFrame(),
            ["株価データなし"]
        )

    all_data = []

    errors = []

    # =====================================================
    # MultiIndex
    # =====================================================

    if isinstance(
        data.columns,
        pd.MultiIndex
    ):

        level0 = list(
            data.columns
            .get_level_values(0)
            .unique()
        )

        # ---------------------------------------------
        # ticker -> OHLCV
        # ---------------------------------------------

        for ticker in tickers:

            if ticker not in level0:

                errors.append(
                    f"{ticker}: データなし"
                )

                continue

            try:

                g = data[
                    ticker
                ].copy()

                g = g.reset_index()

                g.columns = [
                    str(c).lower()
                    for c in g.columns
                ]

                required = [
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume"
                ]

                if not all(
                    c in g.columns
                    for c in required
                ):

                    errors.append(
                        f"{ticker}: 必要列なし"
                    )

                    continue

                g = g[
                    required
                ].copy()

                g["ticker"] = ticker

                for c in [
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume"
                ]:

                    g[c] = pd.to_numeric(
                        g[c],
                        errors="coerce"
                    )

                g = g.dropna(
                    subset=[
                        "date",
                        "open",
                        "high",
                        "low",
                        "close"
                    ]
                )

                if not g.empty:

                    all_data.append(
                        g
                    )

            except Exception as e:

                errors.append(
                    f"{ticker}: {str(e)}"
                )

    else:

        # 単一銘柄の場合
        g = data.copy()

        g = g.reset_index()

        g.columns = [
            str(c).lower()
            for c in g.columns
        ]

        required = [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        if all(
            c in g.columns
            for c in required
        ):

            g = g[
                required
            ].copy()

            g["ticker"] = tickers[0]

            all_data.append(
                g
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
        [
            "ticker",
            "date"
        ]
    ).reset_index(
        drop=True
    )

    return (
        result,
        errors
    )


# =========================================================
# 指標
# =========================================================

def add_indicators(g):

    g = g.sort_values(
        "date"
    ).copy()

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
        )
        / 2
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
# 全銘柄の指標を一度だけ作成
# =========================================================

def prepare_data(df):

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

        return pd.DataFrame()

    result = pd.concat(
        processed,
        ignore_index=True
    )

    return result.sort_values(
        [
            "date",
            "ticker"
        ]
    ).reset_index(
        drop=True
    )


# =========================================================
# 条件判定
# =========================================================

def condition_mask(
    r,
    morning,
    ma,
    volume,
    price2000,
    rsi_limit
):

    if pd.isna(r["ma25"]):
        return False

    if pd.isna(r["ma75"]):
        return False

    if pd.isna(r["rsi"]):
        return False

    if pd.isna(r["vol20"]):
        return False

    if price2000:

        if r["close"] < 2000:

            return False

    if morning:

        if not bool(
            r["morning_star"]
        ):

            return False

    if ma:

        if not (
            r["ma25"]
            >
            r["ma75"]
            and
            r["close"]
            >
            r["ma25"]
        ):

            return False

    if volume:

        if not (
            r["volume"]
            >
            r["vol20"]
        ):

            return False

    if r["rsi"] >= rsi_limit:

        return False

    return True


# =========================================================
# バックテスト
# =========================================================

def run_backtest(
    data,
    morning,
    ma,
    volume,
    price2000,
    rsi_limit,
    stop_loss_rate,
    take_profit_rate,
    save_trades=False
):

    if data.empty:

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {}
        )

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    curve = []

    dates = data[
        "date"
    ].drop_duplicates().sort_values()

    # =====================================================
    # 日次処理
    # =====================================================

    for current_date in dates:

        day = data[
            data["date"]
            ==
            current_date
        ]

        # =================================================
        # 売却
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

            p = positions[
                ticker
            ]

            ret = (
                price
                /
                p["entry_price"]
                - 1
            )

            reason = None

            if ret <= -stop_loss_rate:

                reason = "損切り"

            elif ret >= take_profit_rate:

                reason = "利確"

            elif (
                pd.notna(r["ma25"])
                and
                price < r["ma25"]
            ):

                reason = "25日線割れ"

            if reason:

                proceeds = (
                    p["shares"]
                    *
                    price
                )

                cash += proceeds

                pnl = (
                    price
                    -
                    p["entry_price"]
                ) * p["shares"]

                if save_trades:

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
                            pnl
                    })

                del positions[
                    ticker
                ]

        # =================================================
        # 買い
        # =================================================

        if len(positions) < max_positions:

            for _, r in day.iterrows():

                ticker = str(
                    r["ticker"]
                )

                if ticker in positions:

                    continue

                if (
                    len(positions)
                    >= max_positions
                ):

                    break

                if not condition_mask(
                    r,
                    morning,
                    ma,
                    volume,
                    price2000,
                    rsi_limit
                ):

                    continue

                price = float(
                    r["close"]
                )

                budget = min(
                    max_per_position,
                    cash
                )

                shares = (
                    int(
                        budget
                        /
                        (
                            price
                            *
                            100
                        )
                    )
                    *
                    100
                )

                if shares <= 0:

                    continue

                cost = (
                    shares
                    *
                    price
                )

                if cost > cash:

                    continue

                cash -= cost

                positions[
                    ticker
                ] = {

                    "shares":
                        shares,

                    "entry_price":
                        price
                }

                if save_trades:

                    trades.append({

                        "date":
                            current_date,

                        "ticker":
                            ticker,

                        "side":
                            "BUY",

                        "price":
                            price,

                        "shares":
                            shares,

                        "reason":
                            "選定条件成立",

                        "pnl":
                            0
                    })

        # =================================================
        # 資産評価
        # =================================================

        market_value = 0

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

        curve.append({

            "date":
                current_date,

            "equity":
                cash + market_value,

            "cash":
                cash,

            "positions":
                len(positions)
        })

    eq = pd.DataFrame(
        curve
    )

    tr = pd.DataFrame(
        trades
    )

    return (
        eq,
        tr,
        positions
    )


# =========================================================
# 成績
# =========================================================

def calculate_stats(
    eq,
    tr
):

    if eq.empty:

        return {

            "final_asset": 0,
            "pnl": 0,
            "return_rate": 0,
            "max_drawdown": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0
        }

    final_asset = float(
        eq.iloc[-1]["equity"]
    )

    pnl = (
        final_asset
        -
        initial_cash
    )

    return_rate = (
        pnl
        /
        initial_cash
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

    if tr.empty:

        return {

            "final_asset":
                final_asset,

            "pnl":
                pnl,

            "return_rate":
                return_rate,

            "max_drawdown":
                max_drawdown,

            "trades":
                0,

            "wins":
                0,

            "losses":
                0,

            "win_rate":
                0,

            "avg_win":
                0,

            "avg_loss":
                0
        }

    sells = tr[
        tr["side"] == "SELL"
    ].copy()

    wins = sells[
        sells["pnl"] > 0
    ]

    losses = sells[
        sells["pnl"] < 0
    ]

    win_count = len(wins)

    loss_count = len(losses)

    closed = (
        win_count
        +
        loss_count
    )

    win_rate = (
        win_count / closed
        if closed > 0
        else 0
    )

    avg_win = (
        float(
            wins["pnl"].mean()
        )
        if win_count > 0
        else 0
    )

    avg_loss = (
        float(
            losses["pnl"].mean()
        )
        if loss_count > 0
        else 0
    )

    return {

        "final_asset":
            final_asset,

        "pnl":
            pnl,

        "return_rate":
            return_rate,

        "max_drawdown":
            max_drawdown,

        "trades":
            len(sells),

        "wins":
            win_count,

        "losses":
            loss_count,

        "win_rate":
            win_rate,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss
    }


# =========================================================
# 条件診断
# =========================================================

def diagnostic(
    data
):

    rows = []

    for ticker, g in data.groupby(
        "ticker"
    ):

        valid = g[
            [
                "ma25",
                "ma75",
                "rsi",
                "vol20"
            ]
        ].notna().all(
            axis=1
        )

        g = g[
            valid
        ]

        if g.empty:

            continue

        rows.append({

            "銘柄":
                ticker,

            "判定日数":
                len(g),

            "2000円以上":
                int(
                    (
                        g["close"]
                        >= 2000
                    ).sum()
                ),

            "明けの明星":
                int(
                    g["morning_star"].sum()
                ),

            "25日線>75日線":
                int(
                    (
                        g["ma25"]
                        >
                        g["ma75"]
                    ).sum()
                ),

            "株価>25日線":
                int(
                    (
                        g["close"]
                        >
                        g["ma25"]
                    ).sum()
                ),

            "出来高":
                int(
                    (
                        g["volume"]
                        >
                        g["vol20"]
                    ).sum()
                ),

            "RSI60未満":
                int(
                    (
                        g["rsi"]
                        < 60
                    ).sum()
                )
        })

    return pd.DataFrame(
        rows
    )


# =========================================================
# 自動最適化
# =========================================================

def optimize_parameters(
    data
):

    results = []

    # -----------------------------------------------------
    # 探索範囲
    # -----------------------------------------------------

    stop_values = [
        5,
        6,
        7,
        8,
        9,
        10
    ]

    rsi_values = [
        55,
        60,
        65,
        70
    ]

    profit_values = [
        10,
        12,
        15,
        18,
        20,
        25
    ]

    total = (
        len(stop_values)
        *
        len(rsi_values)
        *
        len(profit_values)
    )

    progress = st.progress(
        0
    )

    counter = 0

    for sl in stop_values:

        for rsi in rsi_values:

            for tp in profit_values:

                eq, tr, positions = run_backtest(
                    data,
                    use_morning_star,
                    use_ma_trend,
                    use_volume,
                    use_price_2000,
                    rsi,
                    sl / 100,
                    tp / 100,
                    False
                )

                stats = calculate_stats(
                    eq,
                    tr
                )

                results.append({

                    "損切り":
                        f"{sl}%",

                    "RSI":
                        rsi,

                    "利確":
                        f"{tp}%",

                    "総損益":
                        stats["pnl"],

                    "収益率":
                        stats["return_rate"],

                    "勝率":
                        stats["win_rate"],

                    "決済数":
                        stats["trades"],

                    "平均利益":
                        stats["avg_win"],

                    "平均損失":
                        stats["avg_loss"],

                    "最大DD":
                        stats["max_drawdown"]
                })

                counter += 1

                progress.progress(
                    counter / total
                )

    progress.empty()

    result = pd.DataFrame(
        results
    )

    return result.sort_values(
        "総損益",
        ascending=False
    ).reset_index(
        drop=True
    )


# =========================================================
# 実行
# =========================================================

st.divider()

st.subheader(
    "🚀 バックテスト開始"
)

start_button = st.button(
    "▶ バックテスト開始",
    type="primary",
    use_container_width=True
)


if start_button:

    if not tickers:

        st.error(
            "銘柄がありません。"
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
        "📥 株価データを一括取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )

    if errors:

        with st.expander(
            f"⚠️ データ取得状況 "
            f"（{len(errors)}件）"
        ):

            for e in errors[:100]:

                st.write(e)

    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"✅ {len(stock_df):,}行取得"
    )

    st.write(
        f"📅 "
        f"{stock_df['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{stock_df['date'].max():%Y-%m-%d}"
    )

    st.write(
        f"📊 実際に取得できた銘柄："
        f"{stock_df['ticker'].nunique()}銘柄"
    )

    # =====================================================
    # 指標作成
    # =====================================================

    with st.spinner(
        "🧮 テクニカル指標を計算中..."
    ):

        prepared = prepare_data(
            stock_df
        )

    if prepared.empty:

        st.error(
            "指標を計算できるデータがありません。"
        )

        st.stop()

    st.success(
        "✅ テクニカル指標の計算完了"
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
            prepared
        )

        st.dataframe(
            diag,
            use_container_width=True,
            hide_index=True
        )

    # =====================================================
    # メインバックテスト
    # =====================================================

    with st.spinner(
        "📊 メインバックテスト中..."
    ):

        eq, tr, positions = (
            run_backtest(
                prepared,
                use_morning_star,
                use_ma_trend,
                use_volume,
                use_price_2000,
                rsi_max,
                stop_loss / 100,
                take_profit / 100,
                True
            )
        )

    if eq.empty:

        st.error(
            "バックテスト結果がありません。"
        )

        st.stop()

    stats = calculate_stats(
        eq,
        tr
    )

    # =====================================================
    # メイン結果
    # =====================================================

    st.divider()

    st.header(
        "📊 バックテスト結果"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "最終資産",
        f"¥{stats['final_asset']:,.0f}"
    )

    c2.metric(
        "総損益",
        f"¥{stats['pnl']:,.0f}",
        f"{stats['return_rate']:.2%}"
    )

    c3.metric(
        "勝率",
        f"{stats['win_rate']:.1%}"
    )

    c4.metric(
        "最大DD",
        f"{stats['max_drawdown']:.2%}"
    )

    c5, c6, c7, c8 = st.columns(4)

    c5.metric(
        "決済数",
        f"{stats['trades']:,}"
    )

    c6.metric(
        "勝ち",
        f"{stats['wins']:,}"
    )

    c7.metric(
        "平均利益",
        f"¥{stats['avg_win']:,.0f}"
    )

    c8.metric(
        "平均損失",
        f"¥{stats['avg_loss']:,.0f}"
    )

    # =====================================================
    # 自動最適化
    # =====================================================

    if optimization_mode:

        st.divider()

        st.header(
            "🤖 パラメータ自動最適化"
        )

        st.info(
            "損切り・RSI・利確の組み合わせを自動検証します。"
            "現在の設定だけでなく、他の組み合わせも比較します。"
        )

        with st.spinner(
            "🔬 最適なパラメータを探索中..."
        ):

            optimization = (
                optimize_parameters(
                    prepared
                )
            )

        if not optimization.empty:

            st.subheader(
                "🏆 最適パラメータ"
            )

            best = optimization.iloc[0]

            b1, b2, b3, b4 = st.columns(4)

            b1.metric(
                "損切り",
                best["損切り"]
            )

            b2.metric(
                "RSI",
                str(int(best["RSI"]))
            )

            b3.metric(
                "利確",
                best["利確"]
            )

            b4.metric(
                "総損益",
                f"¥{best['総損益']:,.0f}"
            )

            st.success(
                "🎯 この5年間のデータでは、"
                f"損切り{best['損切り']}・"
                f"RSI{int(best['RSI'])}・"
                f"利確{best['利確']} "
                f"が最も総損益の高い組み合わせでした。"
            )

            if top10_mode:

                st.subheader(
                    "🥇 最適条件 TOP10"
                )

                top10 = optimization.head(
                    10
                ).copy()

                top10["総損益"] = (
                    top10["総損益"]
                    .map(
                        lambda x:
                        f"¥{x:,.0f}"
                    )
                )

                top10["収益率"] = (
                    top10["収益率"]
                    .map(
                        lambda x:
                        f"{x:.2%}"
                    )
                )

                top10["勝率"] = (
                    top10["勝率"]
                    .map(
                        lambda x:
                        f"{x:.1%}"
                    )
                )

                top10["最大DD"] = (
                    top10["最大DD"]
                    .map(
                        lambda x:
                        f"{x:.2%}"
                    )
                )

                st.dataframe(
                    top10,
                    use_container_width=True,
                    hide_index=True
                )

            # CSV
            optimization_csv = (
                optimization
                .to_csv(
                    index=False
                )
                .encode(
                    "utf-8-sig"
                )
            )

            st.download_button(
                "⬇️ 最適化結果CSV",
                data=optimization_csv,
                file_name="ver3_5_optimization.csv",
                mime="text/csv"
            )

    # =====================================================
    # 資産推移
    # =====================================================

    st.divider()

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

    if tr.empty:

        st.warning(
            "売買履歴はありません。"
        )

    else:

        display_tr = tr.copy()

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

        st.dataframe(
            display_tr,
            use_container_width=True,
            hide_index=True
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
            "⬇️ 売買履歴CSV",
            data=csv,
            file_name="backtest_trades_ver3_5.csv",
            mime="text/csv"
        )

    # =====================================================
    # 未決済
    # =====================================================

    if positions:

        st.subheader(
            "📌 最終日の未決済銘柄"
        )

        rows = []

        last_date = eq.iloc[-1]["date"]

        last_day = prepared[
            prepared["date"]
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

            unrealized = (
                final_price
                -
                p["entry_price"]
            ) * p["shares"]

            rows.append({

                "銘柄":
                    ticker,

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
                use_container_width=True,
                hide_index=True
            )

    # =====================================================
    # 選定銘柄ランキング
    # =====================================================

    st.subheader(
        "⭐ 選定された銘柄ランキング"
    )

    if not tr.empty:

        buys = tr[
            tr["side"] == "BUY"
        ]

        if not buys.empty:

            ranking = (
                buys
                .groupby("ticker")
                .size()
                .reset_index(
                    name="選定回数"
                )
                .sort_values(
                    "選定回数",
                    ascending=False
                )
            )

            st.dataframe(
                ranking,
                use_container_width=True,
                hide_index=True
            )

    # =====================================================
    # データ確認
    # =====================================================

    with st.expander(
        "📋 取得データ確認"
    ):

        st.dataframe(
            prepared.tail(200),
            use_container_width=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.5 / 仮想売買専用。"
    "証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
