import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
from itertools import product

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================

st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3.6",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.6")

st.caption(
    "日経225を中心に、過去データから自動的に有力な売買条件を探索します。"
    "実際の注文は行いません。"
)


# =========================================================
# 日経225銘柄
# =========================================================
# 外部サイトから一覧を取得しない方式
# 取得エラー対策としてコードを内蔵

NIKKEI225_CODES = [
    "1332","1605","1721","1801","1802","1803","1808",
    "1812","1925","1928","1963","2002","2267","2413",
    "2432","2501","2502","2503","2531","2768","2801",
    "2802","2871","2914","3086","3092","3099","3101",
    "3103","3105","3110","3289","3382","3401","3402",
    "3405","3407","3436","3659","3861","3863","4004",
    "4005","4021","4042","4043","4061","4062","4063",
    "4151","4183","4188","4202","4203","4204","4205",
    "4208","4272","4324","4385","4452","4502","4503",
    "4506","4507","4513","4519","4523","4543","4568",
    "4578","4661","4689","4704","4751","4755","4901",
    "4902","4911","5019","5020","5101","5108","5201",
    "5214","5232","5233","5301","5332","5333","5401",
    "5406","5411","5631","5706","5707","5711","5713",
    "5714","5801","5802","5803","5831","6098","6103",
    "6113","6301","6302","6305","6326","6361","6367",
    "6471","6472","6473","6479","6501","6503","6504",
    "6506","6526","6594","6645","6674","6701","6702",
    "6723","6724","6752","6758","6762","6841","6857",
    "6861","6869","6902","6952","6954","6971","6976",
    "6981","6988","7003","7004","7011","7012","7013",
    "7186","7201","7202","7203","7205","7211","7261",
    "7267","7269","7270","7272","7731","7733","7735",
    "7741","7751","7832","7911","7912","7951","7974",
    "8001","8002","8015","8031","8035","8053","8058",
    "8233","8252","8253","8267","8279","8303","8304",
    "8306","8308","8309","8316","8331","8354","8355",
    "8411","8601","8604","8630","8697","8725","8750",
    "8766","8801","8802","8804","8830","9001","9005",
    "9007","9008","9009","9020","9021","9022","9064",
    "9101","9104","9107","9201","9202","9301","9432",
    "9433","9434","9501","9502","9503","9531","9532",
    "9602","9613","9681","9735","9766","9843","9983",
    "9984"
]

NIKKEI225_TICKERS = [
    x + ".T"
    for x in NIKKEI225_CODES
]


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


# =========================================================
# 通常設定
# =========================================================

st.sidebar.header("🎯 現在の売買設定")

stop_loss = st.sidebar.slider(
    "損切り（%）",
    1,
    30,
    7
) / 100

take_profit = st.sidebar.slider(
    "利確（%）",
    1,
    50,
    15
) / 100

rsi_max = st.sidebar.slider(
    "RSI上限",
    50,
    90,
    60
)


# =========================================================
# 選定条件
# =========================================================

st.sidebar.header("🔎 銘柄選定条件")

use_morning_star = st.sidebar.checkbox(
    "明けの明星",
    value=False
)

use_ma_trend = st.sidebar.checkbox(
    "25日線条件",
    value=False
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
# 探索設定
# =========================================================

st.sidebar.header("🤖 自動探索")

search_mode = st.sidebar.selectbox(
    "探索モード",
    [
        "高速探索",
        "標準探索"
    ]
)

show_diagnostic = st.sidebar.checkbox(
    "条件診断を表示",
    value=True
)


# =========================================================
# 銘柄選択
# =========================================================

st.subheader("📋 バックテスト対象")

selection_mode = st.radio(
    "対象銘柄",
    [
        "日経225全銘柄",
        "個別銘柄"
    ],
    horizontal=True
)

if selection_mode == "日経225全銘柄":

    tickers = NIKKEI225_TICKERS.copy()

    st.success(
        f"🇯🇵 日経225：{len(tickers)}銘柄を対象にします。"
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
def download_stock_data(tickers):

    if yf is None:

        return (
            pd.DataFrame(),
            ["yfinanceがインストールされていません。"]
        )

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(days=365 * 5 + 40)
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
                    f"{ticker}: 必要列不足"
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

            if len(data) >= 80:

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
        ["ticker", "date"]
    ).reset_index(drop=True)

    return (
        result,
        errors
    )


# =========================================================
# 指標計算
# =========================================================

@st.cache_data(show_spinner=False)
def prepare_data(df):

    result = []

    for ticker, g in df.groupby(
        "ticker"
    ):

        g = g.sort_values(
            "date"
        ).copy()

        if len(g) < 80:
            continue

        # MA
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

        # -------------------------------------------------
        # 明けの明星
        # -------------------------------------------------

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

        result.append(g)

    if not result:

        return pd.DataFrame()

    return pd.concat(
        result,
        ignore_index=True
    )


# =========================================================
# 条件判定
# =========================================================

def make_signal_mask(
    df,
    morning,
    ma,
    volume,
    price2000,
    rsi_limit
):

    valid = (
        df["ma25"].notna()
        &
        df["ma75"].notna()
        &
        df["rsi"].notna()
        &
        df["vol20"].notna()
    )

    mask = valid.copy()

    if morning:

        mask &= (
            df["morning_star"]
        )

    if ma:

        mask &= (
            df["ma25"]
            >
            df["ma75"]
        )

        mask &= (
            df["close"]
            >
            df["ma25"]
        )

    if volume:

        mask &= (
            df["volume"]
            >
            df["vol20"]
        )

    if price2000:

        mask &= (
            df["close"]
            >=
            2000
        )

    mask &= (
        df["rsi"]
        <
        rsi_limit
    )

    return mask


# =========================================================
# バックテスト
# =========================================================

def run_backtest_fast(
    df,
    stop,
    profit,
    rsi_limit,
    morning,
    ma,
    volume,
    price2000,
    start_date=None,
    end_date=None,
    record_trades=False
):

    data = df.copy()

    if start_date is not None:

        data = data[
            data["date"]
            >=
            pd.Timestamp(start_date)
        ]

    if end_date is not None:

        data = data[
            data["date"]
            <=
            pd.Timestamp(end_date)
        ]

    if data.empty:

        return {
            "final_asset": initial_cash,
            "pnl": 0,
            "return_rate": 0,
            "max_drawdown": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0
        }, pd.DataFrame(), {}

    signals = make_signal_mask(
        data,
        morning,
        ma,
        volume,
        price2000,
        rsi_limit
    )

    data = data.copy()

    data["_signal"] = signals

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    equity_curve = []

    dates = sorted(
        data["date"].unique()
    )

    for current_date in dates:

        day = data[
            data["date"]
            ==
            current_date
        ]

        # -------------------------------------------------
        # 売却
        # -------------------------------------------------

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

            if ret <= -stop:

                reason = "損切り"

            elif ret >= profit:

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

                if record_trades:

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
                    )

                del positions[
                    ticker
                ]

        # -------------------------------------------------
        # 買い
        # -------------------------------------------------

        candidates = day[
            day["_signal"]
        ]

        if (
            len(positions)
            <
            max_positions
        ):

            for _, r in candidates.iterrows():

                ticker = str(
                    r["ticker"]
                )

                if ticker in positions:
                    continue

                if (
                    len(positions)
                    >=
                    max_positions
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

                if record_trades:

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

        # -------------------------------------------------
        # 資産評価
        # -------------------------------------------------

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

        equity_curve.append(
            cash + market_value
        )

    # -----------------------------------------------------
    # 最終資産
    # -----------------------------------------------------

    if equity_curve:

        final_asset = float(
            equity_curve[-1]
        )

    else:

        final_asset = initial_cash

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

    # -----------------------------------------------------
    # 最大DD
    # -----------------------------------------------------

    equity_series = pd.Series(
        equity_curve
    )

    if not equity_series.empty:

        running_max = (
            equity_series
            .cummax()
        )

        drawdown = (
            equity_series
            /
            running_max
            - 1
        )

        max_drawdown = float(
            drawdown.min()
        )

    else:

        max_drawdown = 0

    # -----------------------------------------------------
    # 売買統計
    # -----------------------------------------------------

    tr = pd.DataFrame(
        trades
    )

    if tr.empty:

        wins = 0
        losses = 0
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0

    else:

        sells = tr[
            tr["side"]
            ==
            "SELL"
        ]

        win_values = sells[
            sells["pnl"] > 0
        ]["pnl"]

        loss_values = sells[
            sells["pnl"] < 0
        ]["pnl"]

        wins = len(
            win_values
        )

        losses = len(
            loss_values
        )

        total_closed = (
            wins
            +
            losses
        )

        if total_closed:

            win_rate = (
                wins
                /
                total_closed
            )

        else:

            win_rate = 0

        avg_win = (
            float(
                win_values.mean()
            )
            if wins
            else 0
        )

        avg_loss = (
            float(
                loss_values.mean()
            )
            if losses
            else 0
        )

        gross_profit = (
            float(
                win_values.sum()
            )
            if wins
            else 0
        )

        gross_loss = abs(
            float(
                loss_values.sum()
            )
        ) if losses else 0

        if gross_loss > 0:

            profit_factor = (
                gross_profit
                /
                gross_loss
            )

        else:

            profit_factor = 0

    stats = {

        "final_asset":
            final_asset,

        "pnl":
            pnl,

        "return_rate":
            return_rate,

        "max_drawdown":
            max_drawdown,

        "trades":
            wins + losses,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            win_rate,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss,

        "profit_factor":
            profit_factor
    }

    return (
        stats,
        tr,
        positions
    )


# =========================================================
# スコア
# =========================================================

def strategy_score(stats):

    pnl = stats["pnl"]

    dd = abs(
        stats["max_drawdown"]
    )

    pf = stats["profit_factor"]

    trades = stats["trades"]

    # 利益を重視
    score = pnl

    # 大きなDDを少し減点
    score -= (
        initial_cash
        * dd
        * 0.5
    )

    # PFを少し加点
    if pf > 0:

        score += (
            pnl
            *
            min(pf, 3)
            *
            0.05
        )

    # 取引数が極端に少ない設定を軽く減点
    if trades < 5:

        score *= 0.7

    return score


# =========================================================
# 探索
# =========================================================

def generate_parameter_sets():

    if search_mode == "高速探索":

        stop_values = [
            0.05,
            0.07,
            0.10
        ]

        profit_values = [
            0.10,
            0.15,
            0.20
        ]

        rsi_values = [
            55,
            60,
            65
        ]

    else:

        stop_values = [
            0.03,
            0.05,
            0.07,
            0.10
        ]

        profit_values = [
            0.05,
            0.10,
            0.15,
            0.20,
            0.25
        ]

        rsi_values = [
            50,
            55,
            60,
            65,
            70
        ]

    condition_sets = [

        (
            False,
            True,
            True,
            False,
            "25日線ON"
        ),

        (
            False,
            False,
            True,
            False,
            "25日線OFF"
        ),

        (
            False,
            False,
            False,
            False,
            "選定条件最小"
        ),

        (
            True,
            False,
            True,
            False,
            "明けの明星"
        ),

        (
            False,
            True,
            False,
            False,
            "25日線＋出来高なし"
        ),

        (
            False,
            False,
            True,
            True,
            "出来高＋2000円"
        ),

        (
            False,
            False,
            False,
            True,
            "2000円のみ"
        )
    ]

    params = []

    for (
        stop,
        profit,
        rsi,
        condition
    ) in product(
        stop_values,
        profit_values,
        rsi_values,
        condition_sets
    ):

        morning = condition[0]
        ma = condition[1]
        volume = condition[2]
        price2000 = condition[3]
        condition_name = condition[4]

        params.append({

            "stop":
                stop,

            "profit":
                profit,

            "rsi":
                rsi,

            "morning":
                morning,

            "ma":
                ma,

            "volume":
                volume,

            "price2000":
                price2000,

            "condition_name":
                condition_name
        })

    return params


# =========================================================
# 探索実行
# =========================================================

def optimize_strategy(
    data,
    train_start,
    train_end
):

    params = generate_parameter_sets()

    results = []

    total = len(params)

    progress = st.progress(
        0,
        text="🤖 自動探索を開始しています..."
    )

    for i, p in enumerate(params):

        stats, _, _ = run_backtest_fast(
            data,
            p["stop"],
            p["profit"],
            p["rsi"],
            p["morning"],
            p["ma"],
            p["volume"],
            p["price2000"],
            train_start,
            train_end,
            False
        )

        score = strategy_score(
            stats
        )

        results.append({

            "条件":
                p["condition_name"],

            "損切り":
                p["stop"],

            "利確":
                p["profit"],

            "RSI":
                p["rsi"],

            "総損益":
                stats["pnl"],

            "収益率":
                stats["return_rate"],

            "最大DD":
                stats["max_drawdown"],

            "勝率":
                stats["win_rate"],

            "決済数":
                stats["trades"],

            "平均利益":
                stats["avg_win"],

            "平均損失":
                stats["avg_loss"],

            "PF":
                stats["profit_factor"],

            "スコア":
                score,

            "_params":
                p
        })

        progress.progress(
            (i + 1) / total,
            text=f"🤖 自動探索中 {i + 1}/{total}"
        )

    progress.empty()

    result_df = pd.DataFrame(
        results
    )

    result_df = result_df.sort_values(
        "スコア",
        ascending=False
    ).reset_index(drop=True)

    return result_df


# =========================================================
# 実行
# =========================================================

st.divider()

start_button = st.button(
    "🚀 Ver.3.6 バックテスト開始",
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
            "yfinanceがインストールされていません。"
        )

        st.stop()

    # =====================================================
    # データ取得
    # =====================================================

    with st.spinner(
        "📥 株価データを取得しています..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )

    if errors:

        with st.expander(
            f"⚠️ データ取得状況（{len(errors)}件）"
        ):

            for e in errors[:100]:

                st.write(e)

            if len(errors) > 100:

                st.write(
                    f"...その他 {len(errors)-100}件"
                )

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
        f"{stock_df['ticker'].nunique()} / "
        f"{len(tickers)}"
    )

    # =====================================================
    # 指標準備
    # =====================================================

    with st.spinner(
        "📐 テクニカル指標を計算しています..."
    ):

        prepared = prepare_data(
            stock_df
        )

    if prepared.empty:

        st.error(
            "指標計算可能なデータがありません。"
        )

        st.stop()

    # =====================================================
    # 条件診断
    # =====================================================

    if show_diagnostic:

        st.divider()

        st.header(
            "🔎 条件診断"
        )

        diag_rows = []

        for ticker, g in prepared.groupby(
            "ticker"
        ):

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
            ]

            if g.empty:
                continue

            diag_rows.append({

                "銘柄":
                    ticker,

                "判定日数":
                    len(g),

                "明けの明星":
                    int(
                        g[
                            "morning_star"
                        ].sum()
                    ),

                "25日線上昇":
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
                            <
                            60
                        ).sum()
                    ),

                "2000円以上":
                    int(
                        (
                            g["close"]
                            >=
                            2000
                        ).sum()
                    )
            })

        diag = pd.DataFrame(
            diag_rows
        )

        st.dataframe(
            diag,
            use_container_width=True,
            hide_index=True
        )

    # =====================================================
    # 期間設定
    # =====================================================

    min_date = prepared[
        "date"
    ].min()

    max_date = prepared[
        "date"
    ].max()

    total_days = (
        max_date
        -
        min_date
    ).days

    split_date = (
        min_date
        +
        pd.Timedelta(
            days=int(
                total_days * 0.6
            )
        )
    )

    st.divider()

    st.header(
        "🤖 自動パラメータ探索"
    )

    st.info(
        "前半約60%を学習期間、後半約40%を検証期間として使用します。"
    )

    st.write(
        f"📘 学習期間："
        f"{min_date:%Y-%m-%d}"
        f" ～ "
        f"{split_date:%Y-%m-%d}"
    )

    st.write(
        f"📗 検証期間："
        f"{split_date:%Y-%m-%d}"
        f" ～ "
        f"{max_date:%Y-%m-%d}"
    )

    # =====================================================
    # 自動探索
    # =====================================================

    with st.spinner(
        "🤖 最適なパラメータを探索しています..."
    ):

        optimization = optimize_strategy(
            prepared,
            min_date,
            split_date
        )

    if optimization.empty:

        st.error(
            "探索結果がありません。"
        )

        st.stop()

    # =====================================================
    # 上位ランキング
    # =====================================================

    st.subheader(
        "🏆 学習期間ランキング"
    )

    display_opt = optimization.head(
        15
    ).copy()

    display_opt["損切り"] = (
        display_opt["損切り"]
        .map(
            lambda x:
            f"{x:.0%}"
        )
    )

    display_opt["利確"] = (
        display_opt["利確"]
        .map(
            lambda x:
            f"{x:.0%}"
        )
    )

    display_opt["収益率"] = (
        display_opt["収益率"]
        .map(
            lambda x:
            f"{x:.2%}"
        )
    )

    display_opt["最大DD"] = (
        display_opt["最大DD"]
        .map(
            lambda x:
            f"{x:.2%}"
        )
    )

    display_opt["勝率"] = (
        display_opt["勝率"]
        .map(
            lambda x:
            f"{x:.1%}"
        )
    )

    display_opt["総損益"] = (
        display_opt["総損益"]
        .map(
            lambda x:
            f"¥{x:,.0f}"
        )
    )

    display_opt["平均利益"] = (
        display_opt["平均利益"]
        .map(
            lambda x:
            f"¥{x:,.0f}"
        )
    )

    display_opt["平均損失"] = (
        display_opt["平均損失"]
        .map(
            lambda x:
            f"¥{x:,.0f}"
        )
    )

    display_opt["スコア"] = (
        display_opt["スコア"]
        .map(
            lambda x:
            f"¥{x:,.0f}"
        )
    )

    st.dataframe(
        display_opt[
            [
                "条件",
                "損切り",
                "利確",
                "RSI",
                "総損益",
                "収益率",
                "最大DD",
                "勝率",
                "決済数",
                "PF"
            ]
        ],
        use_container_width=True,
        hide_index=True
    )

    # =====================================================
    # 最良パラメータ
    # =====================================================

    best_row = optimization.iloc[0]

    best_params = best_row[
        "_params"
    ]

    st.divider()

    st.header(
        "🥇 学習期間で選ばれた設定"
    )

    b1, b2, b3, b4 = st.columns(4)

    b1.metric(
        "損切り",
        f"{best_params['stop']:.0%}"
    )

    b2.metric(
        "利確",
        f"{best_params['profit']:.0%}"
    )

    b3.metric(
        "RSI",
        f"{best_params['rsi']}"
    )

    b4.metric(
        "条件",
        best_params[
            "condition_name"
        ]
    )

    # =====================================================
    # 検証期間
    # =====================================================

    st.header(
        "📗 未知期間での検証"
    )

    validation_stats, _, _ = (
        run_backtest_fast(
            prepared,
            best_params["stop"],
            best_params["profit"],
            best_params["rsi"],
            best_params["morning"],
            best_params["ma"],
            best_params["volume"],
            best_params["price2000"],
            split_date,
            max_date,
            False
        )
    )

    v1, v2, v3, v4 = st.columns(4)

    v1.metric(
        "検証期間損益",
        f"¥{validation_stats['pnl']:,.0f}"
    )

    v2.metric(
        "収益率",
        f"{validation_stats['return_rate']:.2%}"
    )

    v3.metric(
        "勝率",
        f"{validation_stats['win_rate']:.1%}"
    )

    v4.metric(
        "最大DD",
        f"{validation_stats['max_drawdown']:.2%}"
    )

    if validation_stats["pnl"] > 0:

        st.success(
            "✅ 検証期間でもプラスでした。"
            "学習期間だけに依存した設定ではない可能性があります。"
        )

    else:

        st.warning(
            "⚠️ 検証期間ではマイナスでした。"
            "過去データへの過剰適合の可能性があります。"
        )

    # =====================================================
    # 現在設定との比較
    # =====================================================

    st.divider()

    st.header(
        "🆚 現在設定 vs 自動探索設定"
    )

    current_stats, _, _ = (
        run_backtest_fast(
            prepared,
            stop_loss,
            take_profit,
            rsi_max,
            use_morning_star,
            use_ma_trend,
            use_volume,
            use_price_2000,
            min_date,
            max_date,
            False
        )
    )

    compare_rows = [

        {
            "設定":
                "現在の設定",

            "損切り":
                f"{stop_loss:.0%}",

            "利確":
                f"{take_profit:.0%}",

            "RSI":
                rsi_max,

            "総損益":
                current_stats["pnl"],

            "最大DD":
                current_stats[
                    "max_drawdown"
                ],

            "勝率":
                current_stats[
                    "win_rate"
                ],

            "PF":
                current_stats[
                    "profit_factor"
                ]
        },

        {
            "設定":
                "自動探索",

            "損切り":
                f"{best_params['stop']:.0%}",

            "利確":
                f"{best_params['profit']:.0%}",

            "RSI":
                best_params["rsi"],

            "総損益":
                best_row["総損益"],

            "最大DD":
                best_row["最大DD"],

            "勝率":
                best_row["勝率"],

            "PF":
                best_row["PF"]
        }
    ]

    compare_df = pd.DataFrame(
        compare_rows
    )

    st.dataframe(
        compare_df,
        use_container_width=True,
        hide_index=True
    )

    # =====================================================
    # 最良設定で全期間バックテスト
    # =====================================================

    st.divider()

    st.header(
        "📈 最良設定による全期間バックテスト"
    )

    final_stats, final_trades, positions = (
        run_backtest_fast(
            prepared,
            best_params["stop"],
            best_params["profit"],
            best_params["rsi"],
            best_params["morning"],
            best_params["ma"],
            best_params["volume"],
            best_params["price2000"],
            min_date,
            max_date,
            True
        )
    )

    f1, f2, f3, f4 = st.columns(4)

    f1.metric(
        "最終資産",
        f"¥{final_stats['final_asset']:,.0f}"
    )

    f2.metric(
        "総損益",
        f"¥{final_stats['pnl']:,.0f}",
        f"{final_stats['return_rate']:.2%}"
    )

    f3.metric(
        "勝率",
        f"{final_stats['win_rate']:.1%}"
    )

    f4.metric(
        "最大DD",
        f"{final_stats['max_drawdown']:.2%}"
    )

    f5, f6, f7, f8 = st.columns(4)

    f5.metric(
        "決済数",
        f"{final_stats['trades']:,}"
    )

    f6.metric(
        "平均利益",
        f"¥{final_stats['avg_win']:,.0f}"
    )

    f7.metric(
        "平均損失",
        f"¥{final_stats['avg_loss']:,.0f}"
    )

    f8.metric(
        "PF",
        f"{final_stats['profit_factor']:.2f}"
    )

    # =====================================================
    # 売買履歴
    # =====================================================

    st.subheader(
        "🧾 最良設定の売買履歴"
    )

    if final_trades.empty:

        st.warning(
            "売買記録がありません。"
        )

    else:

        display_trades = (
            final_trades
            .copy()
        )

        display_trades["date"] = (
            pd.to_datetime(
                display_trades["date"]
            )
            .dt.strftime(
                "%Y-%m-%d"
            )
        )

        st.dataframe(
            display_trades,
            use_container_width=True,
            hide_index=True
        )

        csv = (
            final_trades
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
            file_name="backtest_trades_ver3_6.csv",
            mime="text/csv"
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.6 / 仮想売買専用。"
    "証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
