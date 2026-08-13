import streamlit as st
import pandas as pd
import numpy as np

from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from itertools import product
import re
import io


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
    page_title="日本株 自動バックテスト Ver.3.7",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.7")

st.caption(
    "利益だけでなく、勝率・最大DD・損失・安定性を総合評価して"
    "「強い条件」を探します。"
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


stop_loss_default = st.sidebar.slider(
    "損切り（%）",
    3,
    20,
    7
)


take_profit_default = st.sidebar.slider(
    "利確（%）",
    5,
    50,
    15
)


rsi_default = st.sidebar.slider(
    "RSI上限",
    50,
    80,
    60
)


# =========================================================
# 銘柄選択
# =========================================================

st.sidebar.header("📋 銘柄選択")


use_nikkei225 = st.sidebar.checkbox(
    "🇯🇵 日経225を自動取得",
    value=True
)


manual_input = st.sidebar.text_input(
    "個別銘柄コード",
    value="7203,6758,9984,8306,9432"
)


# =========================================================
# 選定条件
# =========================================================

st.sidebar.header("🎯 選定条件")


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
# 最強条件探索
# =========================================================

st.sidebar.header("🔬 最強条件探索")


optimize_mode = st.sidebar.checkbox(
    "最強条件を自動探索",
    value=True
)


training_ratio = st.sidebar.slider(
    "学習期間の割合",
    50,
    80,
    70
)


max_test_patterns = st.sidebar.selectbox(
    "探索する条件数",
    [8, 16, 32, 64],
    index=2
)


# =========================================================
# 表示
# =========================================================

show_diagnostic = st.sidebar.checkbox(
    "🔎 条件診断",
    value=True
)


show_trades = st.sidebar.checkbox(
    "🧾 売買履歴",
    value=True
)


show_data = st.sidebar.checkbox(
    "📋 取得データ",
    value=False
)


# =========================================================
# 日経225公式URL
# =========================================================

NIKKEI_URL = (
    "https://indexes.nikkei.co.jp/en/nkave/index/component"
)


# =========================================================
# ティッカー正規化
# =========================================================

def normalize_tickers(text):

    raw = (
        str(text)
        .replace("、", ",")
        .replace(" ", ",")
        .replace("\n", ",")
        .split(",")
    )

    result = []

    for item in raw:

        item = item.strip()

        if not item:
            continue

        item = item.upper()

        if item.endswith(".T"):
            ticker = item
        else:
            ticker = item + ".T"

        if ticker not in result:
            result.append(ticker)

    return result


# =========================================================
# 日経225取得
# =========================================================

@st.cache_data(ttl=86400, show_spinner=False)
def get_nikkei225_tickers():

    try:

        tables = pd.read_html(
            NIKKEI_URL
        )

        codes = []

        for table in tables:

            if table.empty:
                continue

            columns = [
                str(c).strip()
                for c in table.columns
            ]

            code_col = None

            for c in columns:

                if c.lower() == "code":
                    code_col = c
                    break

            if code_col is None:
                continue

            for value in table[code_col]:

                text = str(value).strip()

                # 4桁数字
                if re.fullmatch(
                    r"\d{4}",
                    text
                ):
                    codes.append(text)

                # 新しい英字付きコード
                elif re.fullmatch(
                    r"\d{4}[A-Z]",
                    text
                ):
                    codes.append(text)

        # 重複除去
        unique_codes = []

        for code in codes:

            if code not in unique_codes:
                unique_codes.append(code)

        # 225銘柄以上取れた場合
        if len(unique_codes) >= 200:

            return (
                [x + ".T" for x in unique_codes],
                "日経公式サイトから取得"
            )

        return [], (
            f"日経225銘柄数が不足しました"
            f"（{len(unique_codes)}銘柄）"
        )

    except Exception as e:

        return [], str(e)


# =========================================================
# 個別銘柄
# =========================================================

manual_tickers = normalize_tickers(
    manual_input
)


# =========================================================
# 銘柄決定
# =========================================================

if use_nikkei225:

    nikkei_tickers, nikkei_message = (
        get_nikkei225_tickers()
    )

    if nikkei_tickers:

        tickers = nikkei_tickers

        st.sidebar.success(
            f"✅ 日経225：{len(tickers)}銘柄"
        )

        st.sidebar.caption(
            nikkei_message
        )

    else:

        tickers = manual_tickers

        st.sidebar.warning(
            "⚠️ 日経225を自動取得できませんでした。"
            "個別銘柄で実行します。"
        )

else:

    tickers = manual_tickers


# =========================================================
# 画面表示
# =========================================================

st.subheader("📋 バックテスト対象")

st.write(
    f"対象銘柄数：**{len(tickers)}銘柄**"
)

if use_nikkei225 and len(tickers) >= 200:

    st.success(
        "🇯🇵 日経225銘柄を対象にします。"
    )

else:

    st.info(
        "個別入力銘柄を対象にします。"
    )


# =========================================================
# 期間
# =========================================================

years = st.sidebar.selectbox(
    "バックテスト期間",
    [3, 5, 7, 10],
    index=1
)


# =========================================================
# データ取得
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def download_stock_data(
    tickers,
    years
):

    if yf is None:

        return (
            pd.DataFrame(),
            ["yfinanceがインストールされていません"]
        )

    tz = ZoneInfo(
        "Asia/Tokyo"
    )

    end_date = datetime.now(
        tz
    ).date()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 120
        )
    )

    errors = []

    all_data = []

    ticker_list = list(
        tickers
    )

    # -----------------------------------------------------
    # 一括取得
    # -----------------------------------------------------

    try:

        data = yf.download(
            ticker_list,
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="column"
        )

        if data is not None and not data.empty:

            # MultiIndex
            if isinstance(
                data.columns,
                pd.MultiIndex
            ):

                level0 = data.columns.get_level_values(
                    0
                )

                level1 = data.columns.get_level_values(
                    1
                )

                # 通常の
                # Open / High / Low / Close / Volume
                if "Close" in level0:

                    for ticker in ticker_list:

                        if ticker not in level1:
                            continue

                        temp = pd.DataFrame({
                            "date":
                                data.index,

                            "open":
                                data[("Open", ticker)]
                                if ("Open", ticker)
                                in data.columns
                                else np.nan,

                            "high":
                                data[("High", ticker)]
                                if ("High", ticker)
                                in data.columns
                                else np.nan,

                            "low":
                                data[("Low", ticker)]
                                if ("Low", ticker)
                                in data.columns
                                else np.nan,

                            "close":
                                data[("Close", ticker)]
                                if ("Close", ticker)
                                in data.columns
                                else np.nan,

                            "volume":
                                data[("Volume", ticker)]
                                if ("Volume", ticker)
                                in data.columns
                                else np.nan
                        })

                        temp["ticker"] = ticker

                        temp = temp.dropna(
                            subset=[
                                "date",
                                "close"
                            ]
                        )

                        if not temp.empty:
                            all_data.append(
                                temp
                            )

                # ticker / price の形式
                else:

                    for ticker in ticker_list:

                        try:

                            temp = pd.DataFrame({
                                "date":
                                    data.index,

                                "open":
                                    data[(ticker, "Open")],

                                "high":
                                    data[(ticker, "High")],

                                "low":
                                    data[(ticker, "Low")],

                                "close":
                                    data[(ticker, "Close")],

                                "volume":
                                    data[(ticker, "Volume")]
                            })

                            temp["ticker"] = ticker

                            temp = temp.dropna(
                                subset=[
                                    "date",
                                    "close"
                                ]
                            )

                            if not temp.empty:
                                all_data.append(
                                    temp
                                )

                        except Exception:
                            continue

            else:

                # 単一銘柄
                ticker = ticker_list[0]

                temp = data.reset_index()

                temp.columns = [
                    str(x).lower()
                    for x in temp.columns
                ]

                if all(
                    x in temp.columns
                    for x in [
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume"
                    ]
                ):

                    temp["ticker"] = ticker

                    all_data.append(
                        temp[
                            [
                                "date",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "ticker"
                            ]
                        ]
                    )

    except Exception as e:

        errors.append(
            f"一括取得エラー：{str(e)}"
        )


    # -----------------------------------------------------
    # 一括取得できなかった場合
    # 重要銘柄だけ個別取得
    # -----------------------------------------------------

    if not all_data:

        st.warning(
            "一括取得に失敗したため、"
            "個別取得方式に切り替えます。"
        )

        for ticker in ticker_list:

            try:

                temp = yf.download(
                    ticker,
                    start=start_date,
                    end=end_date + timedelta(days=1),
                    auto_adjust=False,
                    progress=False,
                    threads=False
                )

                if temp is None or temp.empty:
                    continue

                if isinstance(
                    temp.columns,
                    pd.MultiIndex
                ):

                    temp.columns = (
                        temp.columns
                        .get_level_values(0)
                    )

                temp = temp.reset_index()

                rename = {}

                for col in temp.columns:

                    name = str(
                        col
                    ).lower()

                    if name == "date":
                        rename[col] = "date"

                    elif name == "open":
                        rename[col] = "open"

                    elif name == "high":
                        rename[col] = "high"

                    elif name == "low":
                        rename[col] = "low"

                    elif name == "close":
                        rename[col] = "close"

                    elif name == "volume":
                        rename[col] = "volume"

                temp = temp.rename(
                    columns=rename
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
                    x in temp.columns
                    for x in required
                ):
                    continue

                temp = temp[
                    required
                ].copy()

                temp["ticker"] = ticker

                all_data.append(
                    temp
                )

            except Exception as e:

                errors.append(
                    f"{ticker}: {str(e)}"
                )


    # -----------------------------------------------------
    # 結合
    # -----------------------------------------------------

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
        result["date"],
        errors="coerce"
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:

        result[col] = pd.to_numeric(
            result[col],
            errors="coerce"
        )

    result = result.dropna(
        subset=[
            "date",
            "close"
        ]
    )

    result = result.sort_values(
        [
            "ticker",
            "date"
        ]
    ).reset_index(
        drop=True
    )

    return result, errors


# =========================================================
# 指標計算
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def prepare_indicators(
    df
):

    result = []

    for ticker, g in df.groupby(
        "ticker"
    ):

        g = g.sort_values(
            "date"
        ).copy()

        if len(g) < 100:
            continue

        # -------------------------------------------------
        # 移動平均
        # -------------------------------------------------

        g["ma25"] = (
            g["close"]
            .rolling(
                25
            )
            .mean()
        )

        g["ma75"] = (
            g["close"]
            .rolling(
                75
            )
            .mean()
        )

        # -------------------------------------------------
        # RSI
        # -------------------------------------------------

        delta = g["close"].diff()

        gain = (
            delta
            .clip(
                lower=0
            )
            .rolling(
                14
            )
            .mean()
        )

        loss = (
            -delta
            .clip(
                upper=0
            )
            .rolling(
                14
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

        # -------------------------------------------------
        # 出来高
        # -------------------------------------------------

        g["vol20"] = (
            g["volume"]
            .rolling(
                20
            )
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
            .rolling(
                20
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
            avg_body.shift(2)
            * 1.2
        )

        middle_small = (
            body.shift(1)
            <=
            avg_body.shift(1)
            * 0.5
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

        result.append(
            g
        )

    if not result:

        return pd.DataFrame()

    result = pd.concat(
        result,
        ignore_index=True
    )

    result = result.sort_values(
        [
            "date",
            "ticker"
        ]
    ).reset_index(
        drop=True
    )

    return result


# =========================================================
# 条件判定
# =========================================================

def condition_mask(
    r,
    morning,
    ma,
    volume,
    price2000,
    rsi_max
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
    data,
    morning,
    ma,
    volume,
    price2000,
    rsi_max,
    stop_loss,
    take_profit,
    initial_cash_value,
    max_positions_value,
    max_per_position_value
):

    if data.empty:

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {}
        )

    cash = float(
        initial_cash_value
    )

    positions = {}

    trades = []

    curve = []

    dates = data[
        "date"
    ].drop_duplicates().sort_values()

    grouped = {
        d: x
        for d, x in data.groupby(
            "date"
        )
    }

    for current_date in dates:

        day = grouped.get(
            current_date
        )

        if day is None:
            continue

        # =================================================
        # 売却
        # =================================================

        for ticker in list(
            positions.keys()
        ):

            rows = day[
                day["ticker"]
                ==
                ticker
            ]

            if rows.empty:
                continue

            r = rows.iloc[0]

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

        if (
            len(positions)
            < max_positions_value
        ):

            for _, r in day.iterrows():

                if (
                    len(positions)
                    >= max_positions_value
                ):
                    break

                ticker = str(
                    r["ticker"]
                )

                if ticker in positions:
                    continue

                if not condition_mask(
                    r,
                    morning,
                    ma,
                    volume,
                    price2000,
                    rsi_max
                ):
                    continue

                price = float(
                    r["close"]
                )

                if price <= 0:
                    continue

                budget = min(
                    max_per_position_value,
                    cash
                )

                # 日本株100株単位
                shares = (
                    int(
                        budget
                        /
                        (
                            price
                            * 100
                        )
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

            rows = day[
                day["ticker"]
                ==
                ticker
            ]

            if not rows.empty:

                price = float(
                    rows.iloc[0][
                        "close"
                    ]
                )

                market_value += (
                    p["shares"]
                    *
                    price
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

        last_date = eq.iloc[-1][
            "date"
        ]

        last_day = grouped.get(
            last_date
        )

        if last_day is not None:

            for ticker, p in positions.items():

                rows = last_day[
                    last_day["ticker"]
                    ==
                    ticker
                ]

                if rows.empty:
                    continue

                final_price = float(
                    rows.iloc[0]["close"]
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
    tr,
    initial_cash_value
):

    if (
        eq is None
        or
        eq.empty
    ):

        return {

            "final_asset": 0,
            "pnl": 0,
            "return_rate": 0,
            "cagr": 0,
            "max_drawdown": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "sharpe": 0,
            "score": -999
        }

    final_asset = float(
        eq.iloc[-1]["equity"]
    )

    pnl = (
        final_asset
        -
        initial_cash_value
    )

    return_rate = (
        pnl
        /
        initial_cash_value
    )

    # -----------------------------------------------------
    # CAGR
    # -----------------------------------------------------

    start_date = pd.to_datetime(
        eq.iloc[0]["date"]
    )

    end_date = pd.to_datetime(
        eq.iloc[-1]["date"]
    )

    years = max(
        (
            end_date
            -
            start_date
        ).days / 365.25,
        0.1
    )

    if final_asset > 0:

        cagr = (
            (
                final_asset
                /
                initial_cash_value
            )
            **
            (
                1 / years
            )
            - 1
        )

    else:

        cagr = -1


    # -----------------------------------------------------
    # 最大DD
    # -----------------------------------------------------

    running_max = (
        eq["equity"]
        .cummax()
    )

    drawdown = (
        eq["equity"]
        /
        running_max
        - 1
    )

    max_drawdown = float(
        drawdown.min()
    )


    # -----------------------------------------------------
    # 売買
    # -----------------------------------------------------

    if tr is None:
        tr = pd.DataFrame()

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

    closed = (
        win_count
        +
        loss_count
    )

    if closed > 0:

        win_rate = (
            win_count
            /
            closed
        )

    else:

        win_rate = 0


    avg_win = (
        float(
            wins["pnl"].mean()
        )
        if win_count
        else 0
    )

    avg_loss = (
        float(
            losses["pnl"].mean()
        )
        if loss_count
        else 0
    )


    # -----------------------------------------------------
    # Profit Factor
    # -----------------------------------------------------

    gross_profit = (
        wins["pnl"].sum()
        if win_count
        else 0
    )

    gross_loss = abs(
        losses["pnl"].sum()
    ) if loss_count else 0

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    elif gross_profit > 0:

        profit_factor = 99.0

    else:

        profit_factor = 0


    # -----------------------------------------------------
    # 日次Sharpe
    # -----------------------------------------------------

    daily_returns = (
        eq["equity"]
        .pct_change()
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .dropna()
    )

    if (
        len(daily_returns) > 10
        and
        daily_returns.std() > 0
    ):

        sharpe = (
            daily_returns.mean()
            /
            daily_returns.std()
            *
            np.sqrt(252)
        )

    else:

        sharpe = 0


    # =====================================================
    # 総合リスク調整スコア
    # =====================================================

    # CAGRが高いほどプラス
    cagr_score = (
        cagr * 100
    )

    # DDは小さいほどプラス
    dd_score = (
        abs(max_drawdown)
        * 100
    )

    # 勝率
    win_score = (
        win_rate * 100
    )

    # PF
    pf_score = min(
        profit_factor,
        5
    )

    # Sharpe
    sharpe_score = max(
        min(
            sharpe,
            5
        ),
        -5
    )

    # -----------------------------------------------------
    # スコア
    #
    # CAGR       35%
    # 最大DD     25%
    # 勝率       15%
    # PF         15%
    # Sharpe     10%
    # -----------------------------------------------------

    score = (

        cagr_score * 0.35

        -

        dd_score * 0.25

        +

        win_score * 0.15

        +

        pf_score * 15 * 0.15

        +

        sharpe_score * 10 * 0.10
    )


    return {

        "final_asset":
            final_asset,

        "pnl":
            pnl,

        "return_rate":
            return_rate,

        "cagr":
            cagr,

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
            avg_loss,

        "profit_factor":
            profit_factor,

        "sharpe":
            sharpe,

        "score":
            score
    }


# =========================================================
# 条件診断
# =========================================================

def diagnostic(
    data,
    rsi_max
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

            "株価2000円以上":
                int(
                    (
                        g["close"]
                        >= 2000
                    ).sum()
                ),

            "明けの明星":
                int(
                    g["morning_star"]
                    .sum()
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
# 最適化パターン生成
# =========================================================

def build_patterns(
    current_morning,
    current_ma,
    current_volume,
    current_price,
    current_rsi,
    current_sl,
    current_tp,
    max_patterns
):

    # -----------------------------------------------------
    # 条件パターン
    # -----------------------------------------------------

    technical = list(
        product(
            [False, True],
            [False, True],
            [False, True],
            [False, True]
        )
    )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    rsi_values = [
        55,
        60,
        65,
        70
    ]

    # -----------------------------------------------------
    # 損切り
    # -----------------------------------------------------

    sl_values = [
        5,
        7,
        10
    ]

    # -----------------------------------------------------
    # 利確
    # -----------------------------------------------------

    tp_values = [
        10,
        15,
        20
    ]

    all_patterns = []

    # 現在設定を最初に
    all_patterns.append({

        "morning":
            current_morning,

        "ma":
            current_ma,

        "volume":
            current_volume,

        "price2000":
            current_price,

        "rsi":
            current_rsi,

        "sl":
            current_sl,

        "tp":
            current_tp
    })


    # -----------------------------------------------------
    # 全組み合わせ
    # -----------------------------------------------------

    for (
        morning,
        ma,
        volume,
        price2000
    ) in technical:

        for rsi in rsi_values:

            for sl in sl_values:

                for tp in tp_values:

                    pattern = {

                        "morning":
                            morning,

                        "ma":
                            ma,

                        "volume":
                            volume,

                        "price2000":
                            price2000,

                        "rsi":
                            rsi,

                        "sl":
                            sl,

                        "tp":
                            tp
                    }

                    exists = False

                    for p in all_patterns:

                        if p == pattern:

                            exists = True

                            break

                    if not exists:

                        all_patterns.append(
                            pattern
                        )

                    if len(
                        all_patterns
                    ) >= max_patterns:

                        return all_patterns

    return all_patterns


# =========================================================
# パターン名
# =========================================================

def pattern_name(p):

    parts = []

    parts.append(
        "明星ON"
        if p["morning"]
        else "明星OFF"
    )

    parts.append(
        "MA ON"
        if p["ma"]
        else "MA OFF"
    )

    parts.append(
        "出来高ON"
        if p["volume"]
        else "出来高OFF"
    )

    parts.append(
        "2000円ON"
        if p["price2000"]
        else "2000円OFF"
    )

    return (
        " / ".join(parts)
        +
        f" / RSI≤{p['rsi']}"
        +
        f" / SL-{p['sl']}%"
        +
        f" / TP+{p['tp']}%"
    )


# =========================================================
# 最適化
# =========================================================

def optimize_conditions(
    data,
    patterns,
    split_date
):

    rows = []

    for index, p in enumerate(
        patterns
    ):

        # -------------------------------------------------
        # 学習期間
        # -------------------------------------------------

        train_data = data[
            data["date"]
            <= split_date
        ]

        test_data = data[
            data["date"]
            > split_date
        ]

        # -------------------------------------------------
        # 学習
        # -------------------------------------------------

        train_eq, train_tr, _ = (
            run_backtest(
                train_data,
                p["morning"],
                p["ma"],
                p["volume"],
                p["price2000"],
                p["rsi"],
                p["sl"] / 100,
                p["tp"] / 100,
                initial_cash,
                max_positions,
                max_per_position
            )
        )

        train_stats = calculate_stats(
            train_eq,
            train_tr,
            initial_cash
        )

        # -------------------------------------------------
        # 検証
        # -------------------------------------------------

        test_eq, test_tr, _ = (
            run_backtest(
                test_data,
                p["morning"],
                p["ma"],
                p["volume"],
                p["price2000"],
                p["rsi"],
                p["sl"] / 100,
                p["tp"] / 100,
                initial_cash,
                max_positions,
                max_per_position
            )
        )

        test_stats = calculate_stats(
            test_eq,
            test_tr,
            initial_cash
        )

        rows.append({

            "順位":
                index + 1,

            "条件":
                pattern_name(p),

            "SL":
                f"-{p['sl']}%",

            "TP":
                f"+{p['tp']}%",

            "RSI":
                p["rsi"],

            "学習損益":
                train_stats["pnl"],

            "検証損益":
                test_stats["pnl"],

            "検証収益率":
                test_stats["return_rate"],

            "検証CAGR":
                test_stats["cagr"],

            "検証勝率":
                test_stats["win_rate"],

            "検証最大DD":
                test_stats["max_drawdown"],

            "検証PF":
                test_stats["profit_factor"],

            "検証Sharpe":
                test_stats["sharpe"],

            "総合スコア":
                test_stats["score"]
        })

    result = pd.DataFrame(
        rows
    )

    if result.empty:
        return result

    # -----------------------------------------------------
    # 総合スコア順
    # -----------------------------------------------------

    result = result.sort_values(
        "総合スコア",
        ascending=False
    ).reset_index(
        drop=True
    )

    result["順位"] = (
        np.arange(
            len(result)
        )
        + 1
    )

    return result


# =========================================================
# 実行
# =========================================================

st.divider()

st.subheader(
    "🚀 バックテスト開始"
)


start_button = st.button(
    "▶ Ver.3.7 バックテスト開始",
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
            "requirements.txtを確認してください。"
        )

        st.stop()


    # =====================================================
    # データ取得
    # =====================================================

    with st.spinner(
        f"📥 {len(tickers)}銘柄のデータを取得中..."
    ):

        stock_df, errors = (
            download_stock_data(
                tuple(tickers),
                years
            )
        )


    if errors:

        with st.expander(
            f"⚠️ データ取得メッセージ "
            f"({len(errors)}件)"
        ):

            for e in errors[:50]:

                st.write(
                    e
                )


    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()


    # =====================================================
    # 指標計算
    # =====================================================

    with st.spinner(
        "📊 テクニカル指標を計算中..."
    ):

        data = prepare_indicators(
            stock_df
        )


    if data.empty:

        st.error(
            "指標計算可能なデータがありません。"
        )

        st.stop()


    st.success(
        f"✅ {len(data):,}行のデータを準備しました。"
    )


    st.write(
        f"📅 "
        f"{data['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{data['date'].max():%Y-%m-%d}"
    )


    st.write(
        f"📊 実際にデータが取得できた銘柄："
        f"**{data['ticker'].nunique()}銘柄**"
    )


    # =====================================================
    # 条件診断
    # =====================================================

    if show_diagnostic:

        st.divider()

        st.header(
            "🔎 条件診断"
        )

        diag = diagnostic(
            data,
            rsi_default
        )

        st.dataframe(
            diag,
            use_container_width=True,
            hide_index=True
        )


    # =====================================================
    # メインバックテスト
    # =====================================================

    st.divider()

    st.header(
        "📊 現在設定のバックテスト"
    )


    with st.spinner(
        "📈 現在設定を計算中..."
    ):

        eq, tr, positions = (
            run_backtest(
                data,
                use_morning_star,
                use_ma_trend,
                use_volume,
                use_price_2000,
                rsi_default,
                stop_loss_default / 100,
                take_profit_default / 100,
                initial_cash,
                max_positions,
                max_per_position
            )
        )


    if eq.empty:

        st.error(
            "バックテスト結果がありません。"
        )

        st.stop()


    stats = calculate_stats(
        eq,
        tr,
        initial_cash
    )


    # =====================================================
    # 結果
    # =====================================================

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
        "CAGR",
        f"{stats['cagr']:.2%}"
    )

    c6.metric(
        "Profit Factor",
        f"{stats['profit_factor']:.2f}"
    )

    c7.metric(
        "Sharpe",
        f"{stats['sharpe']:.2f}"
    )

    c8.metric(
        "リスク調整スコア",
        f"{stats['score']:.2f}"
    )


    # =====================================================
    # 最強条件探索
    # =====================================================

    if optimize_mode:

        st.divider()

        st.header(
            "🏆 リスクを考慮した最強条件探索"
        )

        st.info(
            "利益だけではなく、"
            "CAGR・最大DD・勝率・Profit Factor・"
            "Sharpe Ratioを組み合わせて評価します。"
        )


        # -------------------------------------------------
        # 学習/検証分割
        # -------------------------------------------------

        unique_dates = (
            data["date"]
            .drop_duplicates()
            .sort_values()
            .reset_index(
                drop=True
            )
        )

        split_index = int(
            len(unique_dates)
            *
            training_ratio
            /
            100
        )

        split_index = max(
            1,
            min(
                split_index,
                len(unique_dates) - 1
            )
        )

        split_date = unique_dates[
            split_index
        ]


        st.write(
            f"📚 学習期間："
            f"{unique_dates.iloc[0]:%Y-%m-%d}"
            f" ～ "
            f"{split_date:%Y-%m-%d}"
        )

        st.write(
            f"🧪 検証期間："
            f"{split_date:%Y-%m-%d}"
            f" ～ "
            f"{unique_dates.iloc[-1]:%Y-%m-%d}"
        )


        # -------------------------------------------------
        # パターン生成
        # -------------------------------------------------

        patterns = build_patterns(
            use_morning_star,
            use_ma_trend,
            use_volume,
            use_price_2000,
            rsi_default,
            stop_loss_default,
            take_profit_default,
            max_test_patterns
        )


        st.write(
            f"🔬 検証する条件パターン："
            f"**{len(patterns)}通り**"
        )


        # -------------------------------------------------
        # 実行
        # -------------------------------------------------

        with st.spinner(
            "🔬 最強条件を探索中..."
        ):

            optimization = (
                optimize_conditions(
                    data,
                    patterns,
                    split_date
                )
            )


        if optimization.empty:

            st.warning(
                "最適化結果がありません。"
            )

        else:

            # -------------------------------------------------
            # 上位3
            # -------------------------------------------------

            st.subheader(
                "🥇 最強条件 TOP 3"
            )

            top3 = optimization.head(
                3
            ).copy()

            st.dataframe(
                top3,
                use_container_width=True,
                hide_index=True
            )


            # -------------------------------------------------
            # 1位
            # -------------------------------------------------

            best = optimization.iloc[
                0
            ]


            st.success(
                "🏆 リスクを考慮した総合1位\n\n"
                f"**{best['条件']}**\n\n"
                f"検証損益："
                f"¥{best['検証損益']:,.0f}\n\n"
                f"検証収益率："
                f"{best['検証収益率']:.2%}\n\n"
                f"検証CAGR："
                f"{best['検証CAGR']:.2%}\n\n"
                f"検証勝率："
                f"{best['検証勝率']:.1%}\n\n"
                f"検証最大DD："
                f"{best['検証最大DD']:.2%}\n\n"
                f"Profit Factor："
                f"{best['検証PF']:.2f}\n\n"
                f"Sharpe："
                f"{best['検証Sharpe']:.2f}\n\n"
                f"総合スコア："
                f"{best['総合スコア']:.2f}"
            )


            # -------------------------------------------------
            # 全ランキング
            # -------------------------------------------------

            st.subheader(
                "📊 全条件ランキング"
            )

            st.dataframe(
                optimization,
                use_container_width=True,
                hide_index=True
            )


            # -------------------------------------------------
            # CSV
            # -------------------------------------------------

            csv = (
                optimization
                .to_csv(
                    index=False
                )
                .encode(
                    "utf-8-sig"
                )
            )

            st.download_button(
                "⬇️ 条件ランキングCSV",
                data=csv,
                file_name=(
                    "ver3_7_condition_ranking.csv"
                ),
                mime="text/csv"
            )


    # =====================================================
    # 資産推移
    # =====================================================

    st.divider()

    st.subheader(
        "📈 資産推移"
    )

    chart = eq.copy()

    chart["date"] = pd.to_datetime(
        chart["date"]
    )

    st.line_chart(
        chart.set_index(
            "date"
        )["equity"]
    )


    # =====================================================
    # 売買履歴
    # =====================================================

    if show_trades:

        st.divider()

        st.subheader(
            "🧾 売買履歴"
        )

        if tr.empty:

            st.warning(
                "売買履歴はありません。"
            )

        else:

            display_tr = tr.copy()

            display_tr["date"] = (
                pd.to_datetime(
                    display_tr["date"]
                )
                .dt.strftime(
                    "%Y-%m-%d"
                )
            )

            display_tr = (
                display_tr
                .sort_values(
                    "date",
                    ascending=False
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
                file_name=(
                    "backtest_trades_ver3_7.csv"
                ),
                mime="text/csv"
            )


    # =====================================================
    # 未決済
    # =====================================================

    if positions:

        st.divider()

        st.subheader(
            "📌 最終日の未決済銘柄"
        )

        last_date = eq.iloc[-1][
            "date"
        ]

        last_day = data[
            data["date"]
            ==
            last_date
        ]

        rows = []

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

                "株数":
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

    if show_data:

        st.divider()

        st.subheader(
            "📋 取得データ確認"
        )

        st.dataframe(
            data.tail(200),
            use_container_width=True,
            hide_index=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.7 / 仮想売買専用。"
    "証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるバックテストであり、"
    "将来の利益を保証するものではありません。"
)

st.caption(
    "※現在の日経225構成銘柄を使用するため、"
    "過去の構成銘柄変更を完全には再現していません。"
    "このため過去5年間の結果には"
    "サバイバーシップ・バイアスが含まれる可能性があります。"
)
