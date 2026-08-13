import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import io

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================

st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3.7",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.7")

st.caption(
    "日経225を中心に、日本株の過去5年間を使って仮想売買を検証します。"
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
    60
)


# =========================================================
# 銘柄選定条件
# =========================================================

st.sidebar.header("🎯 銘柄選定条件")

use_nikkei225 = st.sidebar.checkbox(
    "🇯🇵 日経225銘柄を使用",
    value=True
)

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
# 個別銘柄
# =========================================================

st.subheader("📋 バックテスト銘柄")

st.write(
    "日経225を使用しない場合は、日本株コードをカンマ区切りで入力してください。"
)

ticker_input = st.text_input(
    "日本株コード",
    value="7203,6758,9984,8306,9432"
)

st.info(
    "📅 実行時点から過去5年間の株価データを取得します。"
)


# =========================================================
# 銘柄コード整理
# =========================================================

def normalize_tickers(text):

    raw = (
        text
        .replace("、", ",")
        .replace(" ", ",")
        .replace("\n", ",")
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
# 日経225銘柄一覧
# =========================================================

NIKKEI225_FALLBACK = [
    "1332","1605","1801","1802","1803","1808","1925","1928",
    "1963","2002","2267","2413","2432","2501","2502","2503",
    "2531","2768","2801","2802","2871","2914","3086","3092",
    "3099","3101","3103","3105","3110","3382","3401","3402",
    "3405","3407","3436","3659","3861","3863","4004","4005",
    "4021","4042","4043","4061","4062","4063","4151","4183",
    "4188","4202","4203","4204","4205","4307","4324","4385",
    "4452","4502","4503","4506","4507","4519","4523","4543",
    "4568","4578","4661","4689","4704","4751","4755","4901",
    "4911","5019","5020","5101","5108","5201","5214","5232",
    "5233","5301","5332","5333","5401","5406","5411","5541",
    "5631","5706","5707","5711","5713","5714","5801","5802",
    "5803","5831","6098","6103","6113","6301","6302","6305",
    "6326","6361","6367","6471","6472","6473","6479","6501",
    "6503","6504","6506","6526","6594","6645","6674","6701",
    "6702","6703","6723","6724","6752","6758","6762","6770",
    "6841","6857","6861","6902","6952","6954","6963","6971",
    "6976","6981","6988","7003","7004","7011","7012","7013",
    "7186","7201","7202","7203","7205","7211","7261","7267",
    "7269","7270","7272","7731","7733","7735","7741","7751",
    "7752","7832","7911","7912","7951","7974","8001","8002",
    "8015","8020","8031","8035","8053","8058","8233","8252",
    "8253","8267","8303","8304","8306","8308","8309","8316",
    "8331","8354","8411","8601","8604","8630","8697","8725",
    "8750","8766","8795","8801","8802","8804","8830","9001",
    "9005","9007","9008","9009","9020","9021","9022","9064",
    "9101","9104","9107","9147","9201","9202","9301","9412",
    "9432","9433","9434","9501","9502","9503","9531","9532",
    "9602","9613","9681","9735","9766","9983","9984"
]


def get_nikkei225_tickers():

    """
    日経225銘柄を取得する。

    まずWikipediaを試し、
    失敗した場合は内蔵リストを使用する。
    """

    urls = [
        "https://en.wikipedia.org/wiki/Nikkei_225",
        "https://ja.wikipedia.org/wiki/%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87%E6%A0%AA%E4%BE%A1"
    ]

    for url in urls:

        try:

            tables = pd.read_html(url)

            for table in tables:

                cols = [
                    str(c).lower()
                    for c in table.columns
                ]

                code_col = None

                for i, c in enumerate(cols):

                    if (
                        "code" in c
                        or
                        "ticker" in c
                    ):
                        code_col = table.columns[i]
                        break

                if code_col is None:
                    continue

                codes = []

                for value in table[code_col]:

                    s = str(value)

                    digits = ""

                    for ch in s:

                        if ch.isdigit():
                            digits += ch

                    if len(digits) >= 4:

                        code = digits[:4]

                        if code not in codes:
                            codes.append(code)

                if len(codes) >= 200:

                    return [
                        x + ".T"
                        for x in codes[:225]
                    ], "Web"

        except Exception:
            pass

    # Web取得失敗時
    fallback = list(
        dict.fromkeys(
            NIKKEI225_FALLBACK
        )
    )

    return [
        x + ".T"
        for x in fallback
    ], "内蔵リスト"


# =========================================================
# 銘柄選択
# =========================================================

if use_nikkei225:

    with st.spinner(
        "🇯🇵 日経225銘柄一覧を準備しています..."
    ):

        nikkei_tickers, nikkei_source = (
            get_nikkei225_tickers()
        )

    if nikkei_tickers:

        tickers = nikkei_tickers

        if nikkei_source == "Web":

            st.success(
                f"🇯🇵 日経225銘柄をWebから取得しました："
                f"{len(tickers)}銘柄"
            )

        else:

            st.warning(
                "⚠️ 日経225銘柄のWeb自動取得ができなかったため、"
                "内蔵の日経225銘柄リストを使用します。"
            )

    else:

        tickers = manual_tickers

        st.warning(
            "⚠️ 日経225銘柄一覧を取得できませんでした。"
            "入力された個別銘柄でバックテストします。"
        )

else:

    tickers = manual_tickers

    st.info(
        f"📋 個別銘柄モード：{len(tickers)}銘柄"
    )


st.write(
    f"対象銘柄数：**{len(tickers)}銘柄**"
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

        raise ImportError(
            "yfinanceがインストールされていません。"
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
        ["ticker", "date"]
    ).reset_index(drop=True)

    return result, errors


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
# 指標付きデータ作成
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
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

    result = result.sort_values(
        ["date", "ticker"]
    ).reset_index(drop=True)

    return result


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

    required = [
        "ma25",
        "ma75",
        "rsi",
        "vol20"
    ]

    for x in required:

        if pd.isna(r[x]):
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
    data,
    morning=True,
    ma=True,
    volume=True,
    price2000=True
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

    grouped_days = data.groupby(
        "date"
    )

    for current_date, day in grouped_days:

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

            # 日本株は100株単位
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
                    0.0
            })

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

        sells = pd.DataFrame()

    else:

        sells = tr[
            tr["side"] == "SELL"
        ].copy()

    wins = sells[
        sells["pnl"] > 0
    ]

    losses = sells[
        sells["pnl"] < 0
    ]

    win_count = len(
        wins
    )

    loss_count = len(
        losses
    )

    total_closed = (
        win_count
        +
        loss_count
    )

    win_rate = (
        win_count
        /
        total_closed
        if total_closed > 0
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

def diagnostic(data):

    results = []

    if data.empty:
        return pd.DataFrame()

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
                        >=
                        2000
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
# 条件別比較
# =========================================================

def comparison(data):

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
            "25日線＋明けの明星なし",
            False,
            False,
            use_volume,
            use_price_2000
        ),

        (
            "主要選定条件なし",
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
            data,
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
        f"📥 {len(tickers)}銘柄の過去5年データを取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers)
            )
        )

    if errors:

        with st.expander(
            f"⚠️ データ取得できなかった銘柄 "
            f"({len(errors)}件)"
        ):

            for e in errors:

                st.write(e)

    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"✅ {len(stock_df):,}行の株価データを取得しました。"
    )

    st.write(
        f"📅 "
        f"{stock_df['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{stock_df['date'].max():%Y-%m-%d}"
    )

    st.write(
        f"📊 実際に取得できた銘柄数："
        f"**{stock_df['ticker'].nunique()}銘柄**"
    )

    # =====================================================
    # 指標計算
    # =====================================================

    with st.spinner(
        "🔬 テクニカル指標を計算中..."
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
        "📊 メインバックテスト計算中..."
    ):

        eq, tr, positions = run_backtest(
            prepared,
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
    # 条件別比較
    # =====================================================

    if comparison_mode:

        st.divider()

        st.header(
            "🧪 条件別パフォーマンス比較"
        )

        st.caption(
            "同じ初期資金・損切り・利確・RSI条件で、"
            "銘柄選定条件だけを変更して比較します。"
        )

        with st.spinner(
            "🔬 8パターンを比較中..."
        ):

            comp = comparison(
                prepared
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

            # 勝率とDDも表示
            st.info(
                f"📌 勝率：{best['勝率']:.1%}　"
                f"最大DD：{best['最大DD']:.2%}　"
                f"決済数：{int(best['決済数'])}"
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
            file_name="backtest_trades_ver3_7.csv",
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

            hold_df = pd.DataFrame(
                rows
            )

            st.dataframe(
                hold_df,
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
            stock_df.tail(100),
            use_container_width=True,
            hide_index=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.7 / 仮想売買専用。証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
