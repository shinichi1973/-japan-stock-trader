import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import re


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
    page_title="日本株 自動バックテスト Ver.3.4",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.4")

st.caption(
    "日経225全銘柄にも対応した日本株自動バックテスト。"
    "過去5年の日足データで仮想売買を検証します。"
)


# =========================================================
# 基本設定
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


# =========================================================
# 銘柄選定条件
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

diagnostic_mode = st.sidebar.checkbox(
    "🔎 条件診断を表示",
    value=True
)

comparison_mode = st.sidebar.checkbox(
    "🧪 条件別比較を表示",
    value=True
)


# =========================================================
# 銘柄選択方法
# =========================================================

st.sidebar.header("🇯🇵 銘柄範囲")

stock_mode = st.sidebar.radio(
    "バックテストする銘柄",
    [
        "入力した銘柄",
        "日経225全銘柄"
    ],
    index=1
)


# =========================================================
# 日経225取得
# =========================================================

@st.cache_data(ttl=86400)
def get_nikkei225_tickers():

    try:

        url = "https://en.wikipedia.org/wiki/Nikkei_225"

        tables = pd.read_html(url)

        result = []

        for table in tables:

            for col in table.columns:

                column_text = str(col).lower()

                if (
                    "code" in column_text
                    or
                    "ticker" in column_text
                ):

                    for value in table[col]:

                        text = str(value)

                        match = re.search(
                            r"\b(\d{4})\b",
                            text
                        )

                        if match:

                            code = match.group(1)

                            ticker = (
                                code
                                +
                                ".T"
                            )

                            if ticker not in result:

                                result.append(
                                    ticker
                                )

        # 重複除去

        result = list(
            dict.fromkeys(result)
        )

        # 日経225は通常225銘柄

        if len(result) < 200:

            return []

        return result[:225]

    except Exception:

        return []


# =========================================================
# 手入力銘柄
# =========================================================

st.subheader("📋 バックテスト銘柄")


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


manual_tickers = normalize_tickers(
    ticker_input
)


# =========================================================
# 対象銘柄決定
# =========================================================

if stock_mode == "日経225全銘柄":

    with st.spinner(
        "🇯🇵 日経225銘柄一覧を取得しています..."
    ):

        nikkei225_tickers = (
            get_nikkei225_tickers()
        )

    if nikkei225_tickers:

        tickers = nikkei225_tickers

        st.success(
            f"🇯🇵 日経225 {len(tickers)}銘柄を対象にします。"
        )

    else:

        st.error(
            "日経225銘柄一覧を取得できませんでした。"
        )

        st.info(
            "一時的に「入力した銘柄」に変更してください。"
        )

        tickers = manual_tickers

else:

    tickers = manual_tickers


st.write(
    f"対象銘柄数：**{len(tickers)}銘柄**"
)


if stock_mode == "入力した銘柄":

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
        -
        timedelta(
            days=365 * 5 + 30
        )
    )

    all_data = []

    errors = []

    # =====================================================
    # 一括取得
    # =====================================================

    try:

        raw = yf.download(
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


    # =====================================================
    # データ展開
    # =====================================================

    for ticker in tickers:

        try:

            if isinstance(
                raw.columns,
                pd.MultiIndex
            ):

                # ticker が第一階層の場合

                if ticker in raw.columns.get_level_values(0):

                    data = raw[ticker].copy()

                # ticker が第二階層の場合

                elif ticker in raw.columns.get_level_values(1):

                    data = raw.xs(
                        ticker,
                        axis=1,
                        level=1
                    ).copy()

                else:

                    errors.append(
                        f"{ticker}: データなし"
                    )

                    continue

            else:

                # 1銘柄のみの場合

                data = raw.copy()


            if data.empty:

                errors.append(
                    f"{ticker}: データなし"
                )

                continue


            data = data.reset_index()


            # -------------------------------------------------
            # 列名統一
            # -------------------------------------------------

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
                    f"{ticker}: 必要列なし"
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

                all_data.append(
                    data
                )


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


    rs = (
        gain
        /
        loss.replace(
            0,
            np.nan
        )
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
# 条件判定
# =========================================================

def condition_mask(
    r,
    morning=True,
    ma=True,
    volume=True,
    price2000=True
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


    if r["rsi"] >= rsi_max:

        return False


    return True


# =========================================================
# バックテスト
# =========================================================

def run_backtest(
    df,
    morning=True,
    ma=True,
    volume=True,
    price2000=True
):

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
            {}
        )


    data = pd.concat(
        processed,
        ignore_index=True
    )


    data = data.sort_values(
        [
            "date",
            "ticker"
        ]
    )


    cash = float(
        initial_cash
    )


    positions = {}


    trades = []


    curve = []


    dates = sorted(
        data["date"].unique()
    )


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
                -
                1
            )


            reason = None


            if ret <= -stop_loss:

                reason = "損切り"


            elif ret >= take_profit:

                reason = "利確"


            elif (
                pd.notna(
                    r["ma25"]
                )
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

                continue


            if not condition_mask(
                r,
                morning,
                ma,
                volume,
                price2000
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
                    (price * 100)
                )
                * 100
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


    # =====================================================
    # 最終日の含み損益
    # =====================================================

    if positions and not eq.empty:

        last_date = eq.iloc[-1][
            "date"
        ]


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


            unrealized = (
                final_price
                -
                p["entry_price"]
            ) * p["shares"]


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
                            "最終日評価",

                        "pnl":
                            unrealized

                    }])
                ],
                ignore_index=True
            )


    return (
        eq,
        tr,
        positions
    )


# =========================================================
# 成績計算
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
        -
        1
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


    total_closed = (
        win_count
        +
        loss_count
    )


    if total_closed > 0:

        win_rate = (
            win_count
            /
            total_closed
        )

    else:

        win_rate = 0


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


        g = g[
            valid
        ].copy()


        if g.empty:

            continue


        results.append({

            "銘柄":
                ticker,

            "判定日数":
                len(g),

            "株価2000円以上":
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

            "RSI条件":
                int(
                    (
                        g["rsi"]
                        <
                        rsi_max
                    ).sum()
                )
        })


    return pd.DataFrame(
        results
    )


# =========================================================
# 銘柄別成績ランキング
# =========================================================

def ticker_ranking(
    tr
):

    if tr.empty:

        return pd.DataFrame()


    sells = tr[
        tr["side"] == "SELL"
    ].copy()


    if sells.empty:

        return pd.DataFrame()


    ranking = (
        sells
        .groupby("ticker")
        .agg(
            決済数=("pnl", "count"),
            総損益=("pnl", "sum"),
            平均損益=("pnl", "mean"),
            勝ち=("pnl", lambda x: (x > 0).sum()),
            負け=("pnl", lambda x: (x < 0).sum())
        )
        .reset_index()
    )


    ranking["勝率"] = np.where(
        (
            ranking["勝ち"]
            +
            ranking["負け"]
        ) > 0,

        ranking["勝ち"]
        /
        (
            ranking["勝ち"]
            +
            ranking["負け"]
        ),

        0
    )


    ranking = ranking.sort_values(
        "総損益",
        ascending=False
    )


    return ranking


# =========================================================
# 条件別比較
# =========================================================

def comparison(df):

    tests = [

        (
            "現在の設定",
            use_morning_star,
            use_ma_trend,
            use_volume,
            use_price_2000
        ),

        (
            "明けの明星なし",
            False,
            use_ma_trend,
            use_volume,
            use_price_2000
        ),

        (
            "株価2,000円条件なし",
            use_morning_star,
            use_ma_trend,
            use_volume,
            False
        ),

        (
            "出来高条件なし",
            use_morning_star,
            use_ma_trend,
            False,
            use_price_2000
        ),

        (
            "25日線条件なし",
            use_morning_star,
            False,
            use_volume,
            use_price_2000
        ),

        (
            "明けの明星＋2,000円なし",
            False,
            use_ma_trend,
            use_volume,
            False
        ),

        (
            "全選定条件なし",
            False,
            False,
            False,
            False
        )
    ]


    rows = []


    for (
        name,
        morning,
        ma,
        volume,
        price2000
    ) in tests:


        eq, tr, positions = run_backtest(
            df,
            morning,
            ma,
            volume,
            price2000
        )


        stats = calculate_stats(
            eq,
            tr
        )


        rows.append({

            "条件パターン":
                name,

            "最終資産":
                stats["final_asset"],

            "総損益":
                stats["pnl"],

            "収益率":
                stats["return_rate"],

            "決済数":
                stats["trades"],

            "勝ち":
                stats["wins"],

            "負け":
                stats["losses"],

            "勝率":
                stats["win_rate"],

            "平均利益":
                stats["avg_win"],

            "平均損失":
                stats["avg_loss"],

            "最大DD":
                stats["max_drawdown"]
        })


    return pd.DataFrame(
        rows
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
        f"📥 {len(tickers)}銘柄の過去5年データを取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )


    if errors:

        with st.expander(
            f"⚠️ データ取得エラー ({len(errors)}件)"
        ):

            for e in errors[:100]:

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
        f"📅 "
        f"{stock_df['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{stock_df['date'].max():%Y-%m-%d}"
    )


    st.write(
        f"📊 実際に取得できた銘柄："
        f"**{stock_df['ticker'].nunique()}銘柄**"
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
            use_container_width=True,
            hide_index=True
        )


    # =====================================================
    # メインバックテスト
    # =====================================================

    with st.spinner(
        "📊 バックテスト計算中..."
    ):

        eq, tr, positions = run_backtest(
            stock_df,
            use_morning_star,
            use_ma_trend,
            use_volume,
            use_price_2000
        )


    if eq.empty:

        st.error(
            "バックテスト可能なデータがありません。"
        )

        st.stop()


    stats = calculate_stats(
        eq,
        tr
    )


    # =====================================================
    # 結果
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
    # 日経225銘柄ランキング
    # =====================================================

    if stock_mode == "日経225全銘柄":

        st.divider()

        st.header(
            "🏆 日経225 銘柄別ランキング"
        )


        ranking = ticker_ranking(
            tr
        )


        if not ranking.empty:

            st.dataframe(
                ranking,
                use_container_width=True,
                hide_index=True
            )


            best = ranking.iloc[0]


            st.success(
                f"🥇 最も総損益が高かった銘柄："
                f"{best['ticker']} "
                f"/ "
                f"¥{best['総損益']:,.0f}"
            )

        else:

            st.info(
                "決済された銘柄がありません。"
            )


    # =====================================================
    # 条件別比較
    # =====================================================

    if comparison_mode:

        st.divider()

        st.header(
            "🧪 条件別パフォーマンス比較"
        )


        st.caption(
            "同じ初期資金・損切り・利確条件で、"
            "選定条件だけを変更して比較しています。"
        )


        with st.spinner(
            "🔬 条件別バックテストを計算中..."
        ):

            comp = comparison(
                stock_df
            )


        st.dataframe(
            comp,
            use_container_width=True,
            hide_index=True
        )


        if not comp.empty:

            best = comp.loc[
                comp["総損益"].idxmax()
            ]


            st.success(
                "🏆 過去5年間で最も総損益が高かった条件："
                f"「{best['条件パターン']}」"
                f" / "
                f"¥{best['総損益']:,.0f}"
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


    if tr.empty:

        st.warning(
            "売買履歴はありませんでした。"
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
            "⬇️ 売買履歴CSVを保存",
            data=csv,
            file_name="backtest_trades_ver3_4.csv",
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
    "Ver.3.4 / 仮想売買専用。証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
