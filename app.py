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
    page_title="日本株 自動バックテスト Ver.3.4",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.4")

st.caption(
    "高速化版 / 日経225対応 / 過去5年の日足データによる仮想売買"
)


# =========================================================
# 日経225銘柄コード
# =========================================================

NIKKEI225_CODES = [
    "1332","1605","1721","1801","1802","1803","1808","1812",
    "1925","1928","1963","2002","2267","2413","2432","2501",
    "2502","2503","2531","2768","2801","2802","2871","2914",
    "3086","3092","3099","3382","3401","3402","3405","3407",
    "3436","3659","3861","3863","4004","4005","4021","4042",
    "4043","4061","4062","4063","4151","4183","4188","4202",
    "4203","4204","4205","4206","4208","4272","4324","4385",
    "4452","4502","4503","4506","4507","4519","4523","4543",
    "4568","4578","4661","4689","4704","4751","4755","4901",
    "4902","4911","5019","5020","5101","5108","5201","5214",
    "5232","5233","5301","5332","5333","5401","5406","5411",
    "5631","5706","5711","5713","5714","5801","5802","5803",
    "5831","6098","6103","6113","6301","6302","6305","6326",
    "6361","6367","6471","6472","6473","6479","6501","6503",
    "6504","6506","6526","6594","6645","6674","6701","6702",
    "6703","6723","6724","6752","6758","6762","6770","6841",
    "6857","6861","6902","6920","6952","6954","6971","6976",
    "6981","7003","7004","7011","7012","7013","7186","7201",
    "7202","7203","7205","7211","7261","7267","7269","7270",
    "7272","7731","7733","7735","7741","7751","7752","7762",
    "7832","7911","7912","7951","7974","8001","8002","8015",
    "8031","8035","8053","8058","8233","8252","8253","8267",
    "8303","8304","8306","8308","8309","8316","8331","8354",
    "8411","8601","8604","8630","8697","8725","8750","8766",
    "8801","8802","8804","8830","9001","9005","9007","9008",
    "9009","9020","9021","9022","9064","9101","9104","9107",
    "9147","9201","9202","9301","9412","9432","9433","9434",
    "9501","9502","9503","9531","9532","9602","9613","9681",
    "9735","9766","9843","9983","9984"
]


# 重複除去
NIKKEI225_CODES = list(dict.fromkeys(NIKKEI225_CODES))


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
# 条件
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
# 分析設定
# =========================================================

st.sidebar.header("🚀 高速化設定")

nikkei_mode = st.sidebar.checkbox(
    "日経225全銘柄を使用",
    value=True
)

comparison_mode = st.sidebar.checkbox(
    "🧪 条件別比較を実行",
    value=False
)

ranking_mode = st.sidebar.checkbox(
    "🏆 銘柄別ランキングを表示",
    value=True
)

diagnostic_mode = st.sidebar.checkbox(
    "🔎 条件診断を表示",
    value=False
)


# =========================================================
# 個別銘柄入力
# =========================================================

st.subheader("📋 バックテスト銘柄")

if nikkei_mode:

    st.success(
        f"🇯🇵 日経225モード：{len(NIKKEI225_CODES)}銘柄"
    )

    tickers = [
        code + ".T"
        for code in NIKKEI225_CODES
    ]

    st.caption(
        "日経225銘柄コードを内部リストから使用します。"
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
        "対象銘柄：",
        ", ".join(tickers)
    )


# =========================================================
# データ取得
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def download_stock_data(
    tickers
):

    if yf is None:

        raise ImportError(
            "yfinanceがインストールされていません。"
        )

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(
            days=365 * 5 + 40
        )
    )

    all_data = []

    errors = []

    total = len(tickers)

    progress = st.progress(
        0,
        text="株価データ取得準備中..."
    )

    for i, ticker in enumerate(
        tickers
    ):

        try:

            data = yf.download(
                ticker,
                start=start_date,
                end=end_date + timedelta(days=1),
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if (
                data is None
                or
                data.empty
            ):

                errors.append(
                    f"{ticker}: データなし"
                )

            else:

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

                    name = str(
                        col
                    ).lower()

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

                if all(
                    c in data.columns
                    for c in required
                ):

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

                else:

                    errors.append(
                        f"{ticker}: 必要列なし"
                    )

        except Exception as e:

            errors.append(
                f"{ticker}: {str(e)}"
            )

        progress.progress(
            (i + 1) / total,
            text=f"株価取得中 {i + 1}/{total}"
        )

    progress.empty()

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

    result = (
        result
        .sort_values(
            ["ticker", "date"]
        )
        .reset_index(
            drop=True
        )
    )

    return (
        result,
        errors
    )


# =========================================================
# 指標計算
# =========================================================

def add_indicators(g):

    g = (
        g
        .sort_values("date")
        .copy()
    )

    close = g["close"]

    g["ma25"] = (
        close
        .rolling(
            25,
            min_periods=25
        )
        .mean()
    )

    g["ma75"] = (
        close
        .rolling(
            75,
            min_periods=75
        )
        .mean()
    )

    delta = close.diff()

    gain = (
        delta
        .clip(lower=0)
        .rolling(
            14,
            min_periods=14
        )
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .rolling(
            14,
            min_periods=14
        )
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
        .rolling(
            20,
            min_periods=20
        )
        .mean()
    )

    # -----------------------------------------------------
    # 明けの明星
    # -----------------------------------------------------

    body = (
        g["close"]
        -
        g["open"]
    ).abs()

    avg_body = (
        body
        .rolling(
            20,
            min_periods=20
        )
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
# 全銘柄に指標を一括追加
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def prepare_data(
    stock_df
):

    processed = []

    for ticker, g in stock_df.groupby(
        "ticker",
        sort=False
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

    return (
        result
        .sort_values(
            ["date", "ticker"]
        )
        .reset_index(
            drop=True
        )
    )


# =========================================================
# 条件判定
# =========================================================

def build_condition_mask(
    data,
    morning,
    ma,
    volume,
    price2000
):

    mask = (
        data["ma25"].notna()
        &
        data["ma75"].notna()
        &
        data["rsi"].notna()
        &
        data["vol20"].notna()
    )

    if price2000:

        mask &= (
            data["close"]
            >= 2000
        )

    if morning:

        mask &= (
            data["morning_star"]
        )

    if ma:

        mask &= (
            data["ma25"]
            >
            data["ma75"]
        )

        mask &= (
            data["close"]
            >
            data["ma25"]
        )

    if volume:

        mask &= (
            data["volume"]
            >
            data["vol20"]
        )

    mask &= (
        data["rsi"]
        <
        rsi_max
    )

    return mask


# =========================================================
# 高速バックテスト
# =========================================================

def run_backtest(
    data,
    morning,
    ma,
    volume,
    price2000
):

    if data.empty:

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {}
        )

    condition = build_condition_mask(
        data,
        morning,
        ma,
        volume,
        price2000
    )

    # 条件成立をデータに保持
    work = data.copy()

    work["signal"] = condition

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    curve = []

    dates = (
        work["date"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    for current_date in dates:

        day = work[
            work["date"]
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
        # 購入
        # =================================================

        candidates = day[
            day["signal"]
        ]

        if not candidates.empty:

            for _, r in candidates.iterrows():

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

    if (
        positions
        and
        not eq.empty
    ):

        last_date = eq.iloc[-1]["date"]

        last_day = work[
            work["date"]
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
    ]

    wins = sells[
        sells["pnl"] > 0
    ]

    losses = sells[
        sells["pnl"] < 0
    ]

    win_count = len(wins)

    loss_count = len(losses)

    total = (
        win_count
        +
        loss_count
    )

    win_rate = (
        win_count / total
        if total > 0
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
# 銘柄別ランキング
# =========================================================

def stock_ranking(
    tr
):

    if tr.empty:

        return pd.DataFrame()

    sells = tr[
        tr["side"] == "SELL"
    ].copy()

    if sells.empty:

        return pd.DataFrame()

    result = (
        sells
        .groupby("ticker")
        .agg(
            売買回数=("pnl", "count"),
            総損益=("pnl", "sum"),
            平均損益=("pnl", "mean"),
            勝ち=("pnl", lambda x: (x > 0).sum()),
            負け=("pnl", lambda x: (x < 0).sum())
        )
        .reset_index()
    )

    result["勝率"] = (
        result["勝ち"]
        /
        (
            result["勝ち"]
            +
            result["負け"]
        )
    )

    result = result.sort_values(
        "総損益",
        ascending=False
    )

    return result


# =========================================================
# 条件診断
# =========================================================

def diagnostic(
    data
):

    if data.empty:

        return pd.DataFrame()

    rows = []

    for ticker, g in data.groupby(
        "ticker"
    ):

        valid = (
            g[
                [
                    "ma25",
                    "ma75",
                    "rsi",
                    "vol20"
                ]
            ]
            .notna()
            .all(axis=1)
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

            "株価2000円以上":
                int(
                    (
                        g["close"] >= 2000
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
        rows
    )


# =========================================================
# 条件比較
# =========================================================

def comparison(
    data
):

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

        eq, tr, positions = (
            run_backtest(
                data,
                morning,
                ma,
                volume,
                price2000
            )
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
        "📥 株価データを取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )

    if errors:

        with st.expander(
            f"⚠️ データ取得エラー {len(errors)}件"
        ):

            for e in errors:

                st.write(e)

    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"✅ {stock_df['ticker'].nunique()}銘柄 / "
        f"{len(stock_df):,}行を取得しました。"
    )

    st.write(
        f"📅 "
        f"{stock_df['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{stock_df['date'].max():%Y-%m-%d}"
    )

    # =====================================================
    # 指標計算
    # =====================================================

    with st.spinner(
        "⚙️ 指標を一括計算中..."
    ):

        data = prepare_data(
            stock_df
        )

    if data.empty:

        st.error(
            "指標計算可能なデータがありません。"
        )

        st.stop()

    st.success(
        f"⚙️ 指標計算完了："
        f"{data['ticker'].nunique()}銘柄"
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
            data
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
        "🚀 高速バックテスト計算中..."
    ):

        eq, tr, positions = (
            run_backtest(
                data,
                use_morning_star,
                use_ma_trend,
                use_volume,
                use_price_2000
            )
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
    # 銘柄ランキング
    # =====================================================

    if ranking_mode:

        st.divider()

        st.header(
            "🏆 銘柄別ランキング"
        )

        ranking = stock_ranking(
            tr
        )

        if not ranking.empty:

            st.subheader(
                "利益ランキング"
            )

            st.dataframe(
                ranking,
                use_container_width=True,
                hide_index=True
            )

            best = ranking.iloc[0]

            st.success(
                f"🥇 最大利益銘柄："
                f"{best['ticker']} / "
                f"¥{best['総損益']:,.0f}"
            )

        else:

            st.info(
                "銘柄別の決済データがありません。"
            )

    # =====================================================
    # 条件別比較
    # =====================================================

    if comparison_mode:

        st.divider()

        st.header(
            "🧪 条件別パフォーマンス比較"
        )

        with st.spinner(
            "🔬 条件別比較を計算中..."
        ):

            comp = comparison(
                data
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
                "🏆 最も総損益が高かった条件："
                f"「{best['条件パターン']}」 "
                f"/ ¥{best['総損益']:,.0f}"
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
    # 未決済
    # =====================================================

    if positions:

        st.subheader(
            "📌 最終日の未決済銘柄"
        )

        rows = []

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
    # データ確認
    # =====================================================

    with st.expander(
        "📋 取得データ確認"
    ):

        st.dataframe(
            data.tail(100),
            use_container_width=True,
            hide_index=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.4 / 高速化・日経225対応 / 仮想売買専用"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
