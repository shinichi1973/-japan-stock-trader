import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import time

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# ページ設定
# =========================================================

st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3.8",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.8")

st.caption(
    "日経225を基本対象とした高速バックテスト。"
    "過去データによる仮想売買シミュレーションです。"
)


# =========================================================
# 日経225構成銘柄
# 2026年8月時点
# =========================================================

NIKKEI225_CODES = [
    "1332",
    "1605",

    "1721", "1801", "1802", "1803", "1808",
    "1812", "1925", "1928", "1963",

    "2002", "2269", "2282",
    "2413", "2432",

    "2501", "2502", "2503",
    "2768", "2801", "2802", "285A",
    "2871", "2914",

    "3086", "3092", "3099", "3289",
    "3382", "3401", "3402", "3405",
    "3407", "3436",

    "3659", "3697", "3861",
    "4004", "4005", "4021", "4042",
    "4043", "4061", "4062", "4063",
    "4151", "4183", "4188", "4208",
    "4307", "4324", "4385", "4452",
    "4502", "4503", "4506", "4507",
    "4519", "4523", "4543", "4568",
    "4578",

    "4661", "4689", "4704", "4751",
    "4755",

    "4901", "4902", "4911",
    "5019", "5020",
    "5101", "5108",
    "5201", "5214", "5233", "5301",
    "5332", "5333",
    "5401", "5406", "5411",
    "543A",
    "5631", "5706", "5711", "5713",
    "5714", "5801", "5802", "5803",
    "5831",

    "6098", "6103", "6113", "6146",
    "6178", "6273", "6301", "6302",
    "6305", "6326", "6361", "6367",
    "6471", "6472", "6473", "6479",
    "6501", "6503", "6504", "6506",
    "6526", "6532", "6645", "6701",
    "6702", "6723", "6724", "6752",
    "6753", "6758", "6762", "6770",
    "6841", "6857", "6861", "6902",
    "6920", "6954", "6963", "6971",
    "6976", "6981", "6988",
    "7004", "7011", "7012", "7013",
    "7013",
    "7186", "7201", "7202", "7203",
    "7211", "7261", "7267", "7269",
    "7270", "7272",
    "7453", "7532",
    "7731", "7733", "7735", "7741",
    "7751", "7752",
    "7832", "7911", "7912", "7951",
    "7974",
    "8001", "8002", "8015", "8031",
    "8035", "8053", "8058",
    "8233", "8252", "8253", "8267",
    "8304", "8306", "8308", "8309",
    "8316", "8331", "8354", "8411",
    "8591", "8601", "8604", "8630",
    "8697", "8725", "8750", "8766",
    "8795",
    "8801", "8802", "8804", "8830",
    "9001", "9005", "9007", "9008",
    "9009", "9020", "9021", "9022",
    "9064", "9101", "9104", "9107",
    "9147", "9201", "9202",
    "9432", "9433", "9434",
    "9501", "9502", "9503",
    "9531", "9532",
    "9602", "9735", "9766",
    "9843", "9983", "9984"
]

# 重複除去
NIKKEI225_CODES = list(dict.fromkeys(NIKKEI225_CODES))


def to_yahoo_ticker(code):
    return str(code).strip().upper() + ".T"


NIKKEI225_TICKERS = [
    to_yahoo_ticker(x)
    for x in NIKKEI225_CODES
]


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
    max_value=225,
    value=10
)

max_per_position = st.sidebar.number_input(
    "1銘柄の最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)

stop_loss_pct = st.sidebar.slider(
    "損切り（%）",
    1,
    30,
    5
)

take_profit_pct = st.sidebar.slider(
    "利確（%）",
    1,
    100,
    10
)

rsi_max = st.sidebar.slider(
    "RSI上限",
    40,
    90,
    60
)

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

st.sidebar.header("⚡ 高速化設定")

fast_mode = st.sidebar.checkbox(
    "高速モード",
    value=True
)

use_nikkei225 = st.sidebar.checkbox(
    "🇯🇵 日経225を使用",
    value=True
)

diagnostic_mode = st.sidebar.checkbox(
    "🔎 条件診断を表示",
    value=False
)

comparison_mode = st.sidebar.checkbox(
    "🧪 条件別比較を表示",
    value=True
)


# =========================================================
# 個別銘柄追加
# =========================================================

st.subheader("📋 バックテスト対象")

custom_input = st.text_input(
    "追加する日本株コード（任意・カンマ区切り）",
    value=""
)

custom_codes = []

if custom_input.strip():

    raw = (
        custom_input
        .replace("、", ",")
        .replace(" ", ",")
        .split(",")
    )

    for x in raw:

        x = x.strip()

        if not x:
            continue

        if x.upper().endswith(".T"):
            x = x[:-2]

        if x not in custom_codes:
            custom_codes.append(x)


if use_nikkei225:

    target_codes = list(
        dict.fromkeys(
            NIKKEI225_CODES + custom_codes
        )
    )

else:

    target_codes = custom_codes


if not target_codes:

    st.warning(
        "日経225をOFFにした場合は、追加銘柄を入力してください。"
    )

else:

    st.write(
        f"対象銘柄数：**{len(target_codes)}銘柄**"
    )


# =========================================================
# データ取得
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def download_stock_data_fast(
    tickers,
    years=5
):

    if yf is None:

        raise ImportError(
            "yfinanceがインストールされていません。"
        )

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 100
        )
    )

    tickers = list(tickers)

    if not tickers:

        return pd.DataFrame(), []

    errors = []

    try:

        data = yf.download(
            tickers=tickers,
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
            ["データが取得できませんでした。"]
        )

    all_frames = []

    # -----------------------------------------------------
    # Multi ticker
    # -----------------------------------------------------

    if isinstance(
        data.columns,
        pd.MultiIndex
    ):

        level0 = list(
            data.columns.get_level_values(0)
        )

        level1 = list(
            data.columns.get_level_values(1)
        )

        known_ohlcv = {
            "Open",
            "High",
            "Low",
            "Close",
            "Adj Close",
            "Volume"
        }

        # ticker first
        if any(
            x in known_ohlcv
            for x in level0
        ):

            data = data.swaplevel(
                0,
                1,
                axis=1
            )

        for ticker in data.columns.levels[0]:

            try:

                if ticker not in data.columns.get_level_values(0):

                    continue

                g = data[ticker].copy()

                required = [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume"
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

                g = g.reset_index()

                g.columns = [
                    str(c).lower()
                    for c in g.columns
                ]

                g["ticker"] = str(
                    ticker
                )

                all_frames.append(g)

            except Exception as e:

                errors.append(
                    f"{ticker}: {e}"
                )

    else:

        # 1銘柄
        g = data.copy()

        g = g.reset_index()

        g.columns = [
            str(c).lower()
            for c in g.columns
        ]

        if "close" in g.columns:

            g["ticker"] = tickers[0]

            all_frames.append(g)

    if not all_frames:

        return (
            pd.DataFrame(),
            errors
        )

    result = pd.concat(
        all_frames,
        ignore_index=True
    )

    needed = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ticker"
    ]

    result = result[
        [
            c for c in needed
            if c in result.columns
        ]
    ].copy()

    for c in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:

        result[c] = pd.to_numeric(
            result[c],
            errors="coerce"
        )

    result["date"] = pd.to_datetime(
        result["date"],
        errors="coerce"
    )

    result = result.dropna(
        subset=[
            "date",
            "open",
            "high",
            "low",
            "close"
        ]
    )

    result = result.sort_values(
        ["ticker", "date"]
    ).reset_index(drop=True)

    return result, errors


# =========================================================
# 指標計算
# =========================================================

def add_indicators_fast(g):

    g = g.sort_values(
        "date"
    ).copy()

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

    # RSI
    delta = close.diff()

    gain = (
        delta.clip(lower=0)
        .rolling(
            14,
            min_periods=14
        )
        .mean()
    )

    loss = (
        (-delta.clip(upper=0))
        .rolling(
            14,
            min_periods=14
        )
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
# 全銘柄指標一括計算
# =========================================================

@st.cache_data(
    ttl=3600,
    show_spinner=False
)
def prepare_data(df):

    if df.empty:

        return df

    frames = []

    for ticker, g in df.groupby(
        "ticker",
        sort=False
    ):

        if len(g) < 80:
            continue

        frames.append(
            add_indicators_fast(g)
        )

    if not frames:

        return pd.DataFrame()

    result = pd.concat(
        frames,
        ignore_index=True
    )

    result = result.sort_values(
        ["date", "ticker"]
    ).reset_index(drop=True)

    # -----------------------------------------------------
    # 条件用の基本フラグを事前計算
    # -----------------------------------------------------

    result["valid"] = (
        result["ma25"].notna()
        &
        result["ma75"].notna()
        &
        result["rsi"].notna()
        &
        result["vol20"].notna()
    )

    result["price2000_ok"] = (
        result["close"] >= 2000
    )

    result["ma_ok"] = (
        (result["ma25"] > result["ma75"])
        &
        (result["close"] > result["ma25"])
    )

    result["volume_ok"] = (
        result["volume"]
        >
        result["vol20"]
    )

    return result


# =========================================================
# 条件マスク
# =========================================================

def build_condition_mask(
    df,
    morning,
    ma,
    volume,
    price2000,
    rsi_limit
):

    mask = df["valid"].copy()

    if morning:
        mask &= df["morning_star"]

    if ma:
        mask &= df["ma_ok"]

    if volume:
        mask &= df["volume_ok"]

    if price2000:
        mask &= df["price2000_ok"]

    mask &= (
        df["rsi"] < rsi_limit
    )

    return mask


# =========================================================
# バックテスト
# =========================================================

def run_backtest_fast(
    data,
    morning,
    ma,
    volume,
    price2000,
    rsi_limit,
    sl_pct,
    tp_pct
):

    if data.empty:

        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {}
        )

    data = data.copy()

    condition = build_condition_mask(
        data,
        morning,
        ma,
        volume,
        price2000,
        rsi_limit
    )

    data["_buy_signal"] = condition

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    equity_rows = []

    # -----------------------------------------------------
    # 日付ごと
    # -----------------------------------------------------

    for current_date, day in data.groupby(
        "date",
        sort=True
    ):

        # ================================================
        # 売却
        # ================================================

        for ticker in list(
            positions.keys()
        ):

            rows = day[
                day["ticker"] == ticker
            ]

            if rows.empty:
                continue

            r = rows.iloc[0]

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

            if ret <= -sl_pct:

                reason = "損切り"

            elif ret >= tp_pct:

                reason = "利確"

            elif (
                pd.notna(r["ma25"])
                and
                price < float(r["ma25"])
            ):

                reason = "25日線割れ"

            if reason is not None:

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
                    "date": current_date,
                    "ticker": ticker,
                    "side": "SELL",
                    "price": price,
                    "shares": p["shares"],
                    "reason": reason,
                    "pnl": pnl
                })

                del positions[ticker]

        # ================================================
        # 買い
        # ================================================

        if len(positions) < max_positions:

            signals = day[
                day["_buy_signal"]
            ]

            # 価格の安い順ではなく、
            # RSIが低い順を優先
            if not signals.empty:

                signals = signals.sort_values(
                    "rsi",
                    ascending=True
                )

                for _, r in signals.iterrows():

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

                    if price <= 0:
                        continue

                    budget = min(
                        max_per_position,
                        cash
                    )

                    # 日本株100株単位
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

                    positions[ticker] = {
                        "shares": shares,
                        "entry_price": price
                    }

                    trades.append({
                        "date": current_date,
                        "ticker": ticker,
                        "side": "BUY",
                        "price": price,
                        "shares": shares,
                        "reason": "選定条件成立",
                        "pnl": 0.0
                    })

        # ================================================
        # 資産評価
        # ================================================

        market_value = 0.0

        for ticker, p in positions.items():

            rows = day[
                day["ticker"] == ticker
            ]

            if not rows.empty:

                market_value += (
                    p["shares"]
                    *
                    float(
                        rows.iloc[0]["close"]
                    )
                )

        equity_rows.append({
            "date": current_date,
            "equity": cash + market_value,
            "cash": cash,
            "positions": len(positions)
        })

    eq = pd.DataFrame(
        equity_rows
    )

    tr = pd.DataFrame(
        trades
    )

    # -----------------------------------------------------
    # 最終日評価
    # -----------------------------------------------------

    if (
        positions
        and
        not eq.empty
    ):

        last_date = eq.iloc[-1]["date"]

        last_day = data[
            data["date"] == last_date
        ]

        for ticker, p in positions.items():

            rows = last_day[
                last_day["ticker"] == ticker
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
                        "date": last_date,
                        "ticker": ticker,
                        "side": "HOLD",
                        "price": final_price,
                        "shares": p["shares"],
                        "reason": "最終日評価",
                        "pnl": unrealized
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
# 統計
# =========================================================

def calculate_stats(eq, tr):

    if eq.empty:

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
            "sharpe": 0
        }

    equity = eq["equity"].astype(float)

    final_asset = float(
        equity.iloc[-1]
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

    # -----------------------------------------------------
    # CAGR
    # -----------------------------------------------------

    days = (
        pd.to_datetime(eq["date"].iloc[-1])
        -
        pd.to_datetime(eq["date"].iloc[0])
    ).days

    years = max(
        days / 365.25,
        0.01
    )

    if final_asset > 0:

        cagr = (
            final_asset
            /
            initial_cash
        ) ** (
            1 / years
        ) - 1

    else:

        cagr = -1

    # -----------------------------------------------------
    # DD
    # -----------------------------------------------------

    peak = equity.cummax()

    drawdown = (
        equity / peak - 1
    )

    max_drawdown = float(
        drawdown.min()
    )

    # -----------------------------------------------------
    # 売買統計
    # -----------------------------------------------------

    if tr.empty:

        return {
            "final_asset": final_asset,
            "pnl": pnl,
            "return_rate": return_rate,
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "sharpe": 0
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

    wins_count = len(wins)

    losses_count = len(losses)

    closed = (
        wins_count
        +
        losses_count
    )

    win_rate = (
        wins_count / closed
        if closed > 0
        else 0
    )

    avg_win = (
        float(wins["pnl"].mean())
        if wins_count > 0
        else 0
    )

    avg_loss = (
        float(losses["pnl"].mean())
        if losses_count > 0
        else 0
    )

    gross_profit = (
        float(wins["pnl"].sum())
        if wins_count > 0
        else 0
    )

    gross_loss = abs(
        float(losses["pnl"].sum())
    ) if losses_count > 0 else 0

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = (
            999.0
            if gross_profit > 0
            else 0
        )

    # -----------------------------------------------------
    # Sharpe
    # -----------------------------------------------------

    daily_returns = (
        equity
        .pct_change()
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .dropna()
    )

    if (
        len(daily_returns) > 1
        and
        daily_returns.std() > 0
    ):

        sharpe = (
            daily_returns.mean()
            /
            daily_returns.std()
        ) * np.sqrt(252)

    else:

        sharpe = 0

    return {
        "final_asset": final_asset,
        "pnl": pnl,
        "return_rate": return_rate,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "trades": len(sells),
        "wins": wins_count,
        "losses": losses_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "sharpe": sharpe
    }


# =========================================================
# 総合スコア
# =========================================================

def calculate_risk_score(stats):

    # -----------------------------------------------------
    # 利益
    # -----------------------------------------------------

    return_score = np.clip(
        stats["return_rate"] * 100,
        -20,
        30
    )

    # -----------------------------------------------------
    # CAGR
    # -----------------------------------------------------

    cagr_score = np.clip(
        stats["cagr"] * 100,
        -10,
        20
    )

    # -----------------------------------------------------
    # DD
    # 小さいほど高評価
    # -----------------------------------------------------

    dd_score = np.clip(
        abs(stats["max_drawdown"]) * 100,
        0,
        30
    )

    dd_component = max(
        0,
        20 - dd_score
    )

    # -----------------------------------------------------
    # PF
    # -----------------------------------------------------

    pf_component = np.clip(
        stats["profit_factor"] * 8,
        0,
        20
    )

    # -----------------------------------------------------
    # Sharpe
    # -----------------------------------------------------

    sharpe_component = np.clip(
        stats["sharpe"] * 5,
        0,
        15
    )

    # -----------------------------------------------------
    # 勝率
    # -----------------------------------------------------

    win_component = (
        stats["win_rate"] * 10
    )

    score = (
        return_score
        +
        cagr_score
        +
        dd_component
        +
        pf_component
        +
        sharpe_component
        +
        win_component
    )

    return round(
        float(score),
        2
    )


# =========================================================
# 条件比較
# =========================================================

def comparison_fast(data):

    tests = [

        (
            "現在の設定",
            use_morning_star,
            use_ma_trend,
            use_volume,
            use_price_2000,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "明けの明星OFF",
            False,
            use_ma_trend,
            use_volume,
            use_price_2000,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "MA OFF",
            use_morning_star,
            False,
            use_volume,
            use_price_2000,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "出来高OFF",
            use_morning_star,
            use_ma_trend,
            False,
            use_price_2000,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "2000円OFF",
            use_morning_star,
            use_ma_trend,
            use_volume,
            False,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "明けの明星OFF / MA OFF",
            False,
            False,
            use_volume,
            use_price_2000,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        ),

        (
            "全選定条件OFF",
            False,
            False,
            False,
            False,
            rsi_max,
            stop_loss_pct / 100,
            take_profit_pct / 100
        )
    ]

    rows = []

    for (
        name,
        morning,
        ma,
        volume,
        price2000,
        rsi,
        sl,
        tp
    ) in tests:

        eq, tr, positions = run_backtest_fast(
            data,
            morning,
            ma,
            volume,
            price2000,
            rsi,
            sl,
            tp
        )

        stats = calculate_stats(
            eq,
            tr
        )

        score = calculate_risk_score(
            stats
        )

        rows.append({

            "条件パターン": name,

            "最終資産":
                stats["final_asset"],

            "総損益":
                stats["pnl"],

            "収益率":
                stats["return_rate"],

            "CAGR":
                stats["cagr"],

            "勝率":
                stats["win_rate"],

            "最大DD":
                stats["max_drawdown"],

            "Profit Factor":
                stats["profit_factor"],

            "Sharpe":
                stats["sharpe"],

            "決済数":
                stats["trades"],

            "勝ち":
                stats["wins"],

            "負け":
                stats["losses"],

            "総合スコア":
                score
        })

    result = pd.DataFrame(
        rows
    )

    if not result.empty:

        result = result.sort_values(
            "総合スコア",
            ascending=False
        ).reset_index(
            drop=True
        )

    return result


# =========================================================
# 条件診断
# =========================================================

def diagnostic_fast(data):

    rows = []

    for ticker, g in data.groupby(
        "ticker",
        sort=False
    ):

        valid = g[
            "valid"
        ]

        if valid.sum() == 0:
            continue

        rows.append({

            "銘柄":
                ticker,

            "有効日数":
                int(valid.sum()),

            "明けの明星":
                int(
                    g.loc[
                        valid,
                        "morning_star"
                    ].sum()
                ),

            "MA条件":
                int(
                    g.loc[
                        valid,
                        "ma_ok"
                    ].sum()
                ),

            "出来高条件":
                int(
                    g.loc[
                        valid,
                        "volume_ok"
                    ].sum()
                ),

            "2000円以上":
                int(
                    g.loc[
                        valid,
                        "price2000_ok"
                    ].sum()
                ),

            "RSI条件":
                int(
                    (
                        g.loc[
                            valid,
                            "rsi"
                        ]
                        <
                        rsi_max
                    ).sum()
                )
        })

    return pd.DataFrame(
        rows
    )


# =========================================================
# 実行
# =========================================================

st.divider()

st.subheader(
    "🚀 バックテスト開始"
)

start_button = st.button(
    "▶ 日経225バックテスト開始",
    type="primary",
    use_container_width=True
)


if start_button:

    if not target_codes:

        st.error(
            "対象銘柄がありません。"
        )

        st.stop()

    if yf is None:

        st.error(
            "yfinanceがインストールされていません。"
        )

        st.stop()

    target_tickers = [
        to_yahoo_ticker(x)
        for x in target_codes
    ]

    st.info(
        f"🇯🇵 {len(target_tickers)}銘柄を一括取得します。"
        "初回は時間がかかりますが、2回目以降はキャッシュを利用します。"
    )

    progress = st.progress(
        0,
        text="📥 株価データ取得中..."
    )

    start_time = time.time()

    # =====================================================
    # データ取得
    # =====================================================

    try:

        stock_df, errors = (
            download_stock_data_fast(
                tuple(target_tickers),
                5
            )
        )

    except Exception as e:

        st.error(
            f"データ取得エラー：{e}"
        )

        st.stop()

    progress.progress(
        45,
        text="📊 指標計算中..."
    )

    if stock_df.empty:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    # =====================================================
    # 指標
    # =====================================================

    data = prepare_data(
        stock_df
    )

    progress.progress(
        70,
        text="🧮 バックテスト計算中..."
    )

    if data.empty:

        st.error(
            "バックテスト可能なデータがありません。"
        )

        st.stop()

    # =====================================================
    # 取得状況
    # =====================================================

    success_count = (
        data["ticker"]
        .nunique()
    )

    requested_count = len(
        target_tickers
    )

    st.success(
        f"✅ データ取得成功："
        f"{success_count} / {requested_count} 銘柄"
    )

    if requested_count - success_count > 0:

        st.warning(
            f"⚠️ {requested_count - success_count}銘柄は"
            "データ取得できなかったため自動スキップしました。"
        )

    if errors:

        with st.expander(
            "⚠️ データ取得詳細"
        ):

            for e in errors[:50]:

                st.write(e)

    # =====================================================
    # 期間
    # =====================================================

    st.write(
        f"📅 "
        f"{data['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{data['date'].max():%Y-%m-%d}"
    )

    st.write(
        f"📊 実際に検証した銘柄："
        f"**{success_count}銘柄**"
    )

    st.write(
        f"📚 データ行数："
        f"**{len(data):,}行**"
    )

    # =====================================================
    # 条件診断
    # =====================================================

    if diagnostic_mode:

        st.divider()

        st.header(
            "🔎 条件診断"
        )

        diag = diagnostic_fast(
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

    eq, tr, positions = run_backtest_fast(
        data,
        use_morning_star,
        use_ma_trend,
        use_volume,
        use_price_2000,
        rsi_max,
        stop_loss_pct / 100,
        take_profit_pct / 100
    )

    progress.progress(
        100,
        text="✅ 完了"
    )

    elapsed = (
        time.time()
        -
        start_time
    )

    st.caption(
        f"⏱️ 処理時間：{elapsed:.1f}秒"
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

    score = calculate_risk_score(
        stats
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
        "CAGR",
        f"{stats['cagr']:.2%}"
    )

    c4.metric(
        "総合スコア",
        f"{score:.2f}"
    )

    c5, c6, c7, c8 = st.columns(4)

    c5.metric(
        "勝率",
        f"{stats['win_rate']:.1%}"
    )

    c6.metric(
        "最大DD",
        f"{stats['max_drawdown']:.2%}"
    )

    c7.metric(
        "Profit Factor",
        f"{stats['profit_factor']:.2f}"
    )

    c8.metric(
        "Sharpe",
        f"{stats['sharpe']:.2f}"
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
            "利益だけでなく、最大DD・Profit Factor・"
            "Sharpe・CAGR・勝率を含めて評価します。"
        )

        with st.spinner(
            "🔬 条件別バックテスト中..."
        ):

            comp = comparison_fast(
                data
            )

        if not comp.empty:

            # 表示用
            display_comp = comp.copy()

            for col in [
                "収益率",
                "CAGR",
                "勝率",
                "最大DD"
            ]:

                display_comp[col] = (
                    display_comp[col]
                    .map(
                        lambda x:
                        f"{x:.2%}"
                    )
                )

            for col in [
                "最終資産",
                "総損益"
            ]:

                display_comp[col] = (
                    display_comp[col]
                    .map(
                        lambda x:
                        f"¥{x:,.0f}"
                    )
                )

            display_comp["Profit Factor"] = (
                display_comp["Profit Factor"]
                .map(
                    lambda x:
                    f"{x:.2f}"
                )
            )

            display_comp["Sharpe"] = (
                display_comp["Sharpe"]
                .map(
                    lambda x:
                    f"{x:.2f}"
                )
            )

            display_comp["総合スコア"] = (
                display_comp["総合スコア"]
                .map(
                    lambda x:
                    f"{x:.2f}"
                )
            )

            st.dataframe(
                display_comp,
                use_container_width=True,
                hide_index=True
            )

            # ============================================
            # 総合1位
            # ============================================

            best = comp.iloc[0]

            st.success(
                "🏆 リスクを考慮した総合1位："
                f"「{best['条件パターン']}」\n\n"
                f"検証損益：¥{best['総損益']:,.0f}　"
                f"収益率：{best['収益率']:.2%}　"
                f"CAGR：{best['CAGR']:.2%}　"
                f"最大DD：{best['最大DD']:.2%}　"
                f"PF：{best['Profit Factor']:.2f}　"
                f"Sharpe：{best['Sharpe']:.2f}　"
                f"総合スコア：{best['総合スコア']:.2f}"
            )

            # ============================================
            # 利益1位
            # ============================================

            profit_best = comp.loc[
                comp["総損益"].idxmax()
            ]

            # ============================================
            # DD1位
            # ============================================

            dd_best = comp.loc[
                comp["最大DD"].idxmax()
            ]

            # ============================================
            # 参考ランキング
            # ============================================

            st.subheader(
                "🏅 参考ランキング"
            )

            r1, r2, r3 = st.columns(3)

            r1.metric(
                "💰 利益1位",
                f"¥{profit_best['総損益']:,.0f}",
                profit_best["条件パターン"]
            )

            r2.metric(
                "🛡️ DDが小さい条件",
                f"{dd_best['最大DD']:.2%}",
                dd_best["条件パターン"]
            )

            r3.metric(
                "🏆 総合1位",
                f"{best['総合スコア']:.2f}",
                best["条件パターン"]
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
        chart_df["date"]
    )

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
            file_name="backtest_trades_ver3_8.csv",
            mime="text/csv"
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
            data["date"] == last_date
        ]

        for ticker, p in positions.items():

            r = last_day[
                last_day["ticker"] == ticker
            ]

            if r.empty:
                continue

            final_price = float(
                r.iloc[0]["close"]
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
            use_container_width=True
        )


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    "Ver.3.8 / 仮想売買専用。証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
