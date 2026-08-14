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
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.4",
    page_icon="📈",
    layout="wide"
)


# =========================================================
# タイトル
# =========================================================

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.4"
)

st.caption(
    "S株を想定した仮想バックテスト。"
    "銘柄名表示・AI BUYランキング・市場環境フィルター・"
    "連続損失ブレーキを搭載。"
    "明けの明星は使用しません。"
)


# =========================================================
# 銘柄名辞書
# =========================================================

STOCK_NAMES = {

    "7203": "トヨタ自動車",
    "6758": "ソニーグループ",
    "9984": "ソフトバンクグループ",
    "8306": "三菱UFJフィナンシャル・グループ",
    "9432": "日本電信電話",
    "7011": "三菱重工業",
    "6501": "日立製作所",
    "6857": "アドバンテスト",
    "8035": "東京エレクトロン",
    "9983": "ファーストリテイリング",

    "4063": "信越化学工業",
    "6098": "リクルートホールディングス",
    "6861": "キーエンス",
    "7267": "本田技研工業",
    "8316": "三井住友フィナンシャルグループ",
    "8411": "みずほフィナンシャルグループ",
    "8766": "東京海上ホールディングス",
    "8001": "伊藤忠商事",
    "8058": "三菱商事",
    "9433": "KDDI",

    "4502": "武田薬品工業",
    "4503": "アステラス製薬",
    "4519": "中外製薬",
    "4543": "テルモ",
    "4661": "オリエンタルランド",
    "4689": "LINEヤフー",
    "6098": "リクルートホールディングス",
    "6273": "SMC",
    "6301": "コマツ",
    "6367": "ダイキン工業",

    "6594": "ニデック",
    "6701": "NEC",
    "6702": "富士通",
    "6752": "パナソニック ホールディングス",
    "6762": "TDK",
    "6981": "村田製作所",
    "7733": "オリンパス",
    "7741": "HOYA",
    "7751": "キヤノン",
    "7832": "バンダイナムコホールディングス",

    "7974": "任天堂",
    "8015": "豊田通商",
    "8028": "ファーストリテイリング",
    "8591": "オリックス",
    "8604": "野村ホールディングス",
    "8725": "MS&ADインシュアランスグループ",
    "8801": "三井不動産",
    "8802": "三菱地所",
    "9020": "東日本旅客鉄道",
    "9022": "東海旅客鉄道",

}


# =========================================================
# 銘柄名取得
# =========================================================

def get_stock_name(ticker):

    code = ticker.replace(".T", "")

    return STOCK_NAMES.get(
        code,
        f"銘柄コード {code}"
    )


# =========================================================
# サイドバー
# =========================================================

st.sidebar.header("⚙️ Ver.4.4 バックテスト設定")


initial_cash = st.sidebar.number_input(
    "初期資金（円）",
    min_value=10000,
    max_value=100000000,
    value=100000,
    step=10000
)


max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=50,
    value=5,
    step=1
)


max_per_position = st.sidebar.number_input(
    "1銘柄最大購入額（円）",
    min_value=5000,
    max_value=10000000,
    value=50000,
    step=5000
)


stop_loss = st.sidebar.slider(
    "損切り（%）",
    1.0,
    20.0,
    7.0,
    0.5
)


take_profit = st.sidebar.slider(
    "利確開始（%）",
    5.0,
    50.0,
    15.0,
    0.5
)


trailing_stop = st.sidebar.slider(
    "トレーリングストップ（%）",
    2.0,
    15.0,
    5.0,
    0.5
)


min_score = st.sidebar.slider(
    "最低BUYスコア",
    40,
    100,
    75,
    5
)


strong_buy_score = st.sidebar.slider(
    "強BUYスコア",
    80,
    100,
    85,
    5
)


rsi_low = st.sidebar.slider(
    "RSI下限",
    20,
    60,
    45,
    1
)


rsi_high = st.sidebar.slider(
    "RSI上限",
    55,
    90,
    65,
    1
)


volume_multiplier = st.sidebar.slider(
    "出来高倍率",
    0.5,
    3.0,
    1.0,
    0.1
)


max_deviation = st.sidebar.slider(
    "25日線からの最大乖離（%）",
    5.0,
    30.0,
    15.0,
    1.0
)


years = st.sidebar.slider(
    "バックテスト期間（年）",
    1,
    10,
    5,
    1
)


cooldown_days = st.sidebar.number_input(
    "損切り後の再購入禁止日数",
    min_value=0,
    max_value=60,
    value=10,
    step=1
)


# =========================================================
# 銘柄入力
# =========================================================

st.sidebar.subheader("📋 対象銘柄")


ticker_input = st.sidebar.text_area(
    "銘柄コード",
    value=(
        "7203,6758,9984,8306,9432,"
        "7011,6501,6857,8035,9983"
    )
)


raw_tickers = [
    x.strip()
    for x in ticker_input.replace("\n", ",").split(",")
    if x.strip()
]


tickers = [
    x if "." in x else x + ".T"
    for x in raw_tickers
]


st.sidebar.write(
    f"対象銘柄数：{len(tickers)}"
)


# =========================================================
# 入力銘柄確認
# =========================================================

with st.sidebar.expander("🏷️ 銘柄名確認"):

    for ticker in tickers:

        code = ticker.replace(".T", "")

        st.write(
            f"**{code}**　{get_stock_name(ticker)}"
        )


# =========================================================
# データ取得
# =========================================================

@st.cache_data(ttl=3600)
def download_stock_data(ticker, years):

    if yf is None:
        return None

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(days=365 * years + 300)
    )

    try:

        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False,
            multi_level_index=False
        )

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col in required:

            if col not in df.columns:
                return None

        df = df[
            required
        ].copy()

        df = df.dropna()

        if df.empty:
            return None

        return df

    except Exception:

        return None


# =========================================================
# RSI
# =========================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = (
        gain
        .rolling(period)
        .mean()
    )

    avg_loss = (
        loss
        .rolling(period)
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    return rsi


# =========================================================
# テクニカル指標
# =========================================================

def add_indicators(df):

    df = df.copy()

    df["MA25"] = (
        df["Close"]
        .rolling(25)
        .mean()
    )

    df["MA75"] = (
        df["Close"]
        .rolling(75)
        .mean()
    )

    df["MA200"] = (
        df["Close"]
        .rolling(200)
        .mean()
    )

    df["RSI"] = calculate_rsi(
        df["Close"],
        14
    )

    df["VOL20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    df["MA25_SLOPE"] = (
        df["MA25"]
        .diff(5)
    )

    df["MA75_SLOPE"] = (
        df["MA75"]
        .diff(5)
    )

    # 25日線からの乖離率
    df["MA25_DEVIATION"] = (
        (
            df["Close"]
            / df["MA25"]
        ) - 1
    ) * 100

    # ATR
    high_low = (
        df["High"]
        - df["Low"]
    )

    high_close = abs(
        df["High"]
        - df["Close"].shift()
    )

    low_close = abs(
        df["Low"]
        - df["Close"].shift()
    )

    tr = pd.concat(
        [
            high_low,
            high_close,
            low_close
        ],
        axis=1
    ).max(axis=1)

    df["ATR14"] = (
        tr
        .rolling(14)
        .mean()
    )

    return df


# =========================================================
# BUYスコア
# =========================================================

def calculate_buy_score(
    row,
    volume_multiplier
):

    score = 0

    if (
        pd.notna(row["MA25"])
        and
        pd.notna(row["MA75"])
        and
        row["MA25"] > row["MA75"]
    ):
        score += 20

    if (
        pd.notna(row["MA200"])
        and
        row["Close"] > row["MA200"]
    ):
        score += 20

    if (
        pd.notna(row["MA25"])
        and
        row["Close"] > row["MA25"]
    ):
        score += 15

    if (
        pd.notna(row["VOL20"])
        and
        row["Volume"]
        >= row["VOL20"]
        * volume_multiplier
    ):
        score += 15

    if (
        pd.notna(row["RSI"])
        and
        45 <= row["RSI"] <= 65
    ):
        score += 15

    if (
        pd.notna(row["MA25_SLOPE"])
        and
        row["MA25_SLOPE"] > 0
    ):
        score += 10

    if (
        pd.notna(row["MA75_SLOPE"])
        and
        row["MA75_SLOPE"] > 0
    ):
        score += 5

    return score


# =========================================================
# 市場環境
# =========================================================

@st.cache_data(ttl=3600)
def get_market_data(years):

    if yf is None:
        return None

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(days=365 * years + 300)
    )

    try:

        df = yf.download(
            "^N225",
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False,
            multi_level_index=False
        )

        if df is None or df.empty:
            return None

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        if "Close" not in df.columns:
            return None

        df = df[
            ["Close"]
        ].dropna()

        df["MA25"] = (
            df["Close"]
            .rolling(25)
            .mean()
        )

        df["MA75"] = (
            df["Close"]
            .rolling(75)
            .mean()
        )

        df["MA200"] = (
            df["Close"]
            .rolling(200)
            .mean()
        )

        df["MA25_SLOPE"] = (
            df["MA25"]
            .diff(5)
        )

        return df

    except Exception:

        return None


# =========================================================
# 市場環境判定
# =========================================================

def market_regime(row):

    score = 0

    if row["Close"] > row["MA25"]:
        score += 1

    if row["Close"] > row["MA75"]:
        score += 1

    if row["Close"] > row["MA200"]:
        score += 1

    if row["MA25_SLOPE"] > 0:
        score += 1

    if score == 4:

        return (
            "🟢 強気",
            1.00
        )

    elif score == 3:

        return (
            "🟡 やや強気",
            0.85
        )

    elif score == 2:

        return (
            "⚪ 中立",
            0.60
        )

    elif score == 1:

        return (
            "🟠 やや弱気",
            0.30
        )

    else:

        return (
            "🔴 弱気",
            0.00
        )


# =========================================================
# スコア判定
# =========================================================

def score_label(score):

    if score >= 90:
        return "🔥 強BUY"

    if score >= 85:
        return "🟢 BUY強"

    if score >= 75:
        return "🟢 BUY"

    return "⚪ 見送り"


# =========================================================
# 資金配分
# =========================================================

def allocation_ratio(score):

    if score >= 90:
        return 1.00

    if score >= 85:
        return 0.85

    if score >= 80:
        return 0.70

    if score >= 75:
        return 0.50

    return 0.00


# =========================================================
# BUY判定
# =========================================================

def buy_signal(
    row,
    min_score,
    volume_multiplier,
    max_deviation,
    market_factor,
    loss_streak
):

    score = calculate_buy_score(
        row,
        volume_multiplier
    )

    if score < min_score:
        return False, score, "スコア不足"

    if row["Close"] < 2000:
        return False, score, "株価2,000円未満"

    if not (
        rsi_low <= row["RSI"] <= rsi_high
    ):
        return False, score, "RSI条件外"

    if abs(
        row["MA25_DEVIATION"]
    ) > max_deviation:

        return (
            False,
            score,
            "25日線乖離過大"
        )

    # 市場環境
    if market_factor <= 0:
        return (
            False,
            score,
            "市場環境が弱気"
        )

    # 3連敗ブレーキ
    if loss_streak >= 3:

        if score < 85:

            return (
                False,
                score,
                "連続損失ブレーキ"
            )

    # 5連敗なら90点未満停止
    if loss_streak >= 5:

        if score < 90:

            return (
                False,
                score,
                "5連敗BUY停止"
            )

    return (
        True,
        score,
        "BUY"
    )


# =========================================================
# 売却判定
# =========================================================

def sell_signal(
    row,
    position,
    stop_loss,
    take_profit,
    trailing_stop
):

    entry_price = (
        position["entry_price"]
    )

    highest_price = (
        position["highest_price"]
    )

    current_price = (
        row["Close"]
    )

    profit_pct = (
        current_price
        / entry_price
        - 1
    ) * 100

    if profit_pct <= -stop_loss:

        return True, "損切り"

    if current_price > highest_price:

        position["highest_price"] = (
            current_price
        )

        highest_price = current_price

    if profit_pct >= take_profit:

        trailing_price = (
            highest_price
            * (
                1
                - trailing_stop / 100
            )
        )

        if current_price <= trailing_price:

            return (
                True,
                "トレーリング利確"
            )

    if current_price < row["MA25"]:

        if profit_pct > 5:

            return (
                True,
                "25日線割れ利益確定"
            )

    if current_price < row["MA75"]:

        return True, "75日線割れ"

    return False, ""


# =========================================================
# バックテスト
# =========================================================

def run_backtest(
    ticker_data,
    market_data,
    initial_cash,
    max_positions,
    max_per_position,
    stop_loss,
    take_profit,
    trailing_stop,
    min_score,
    volume_multiplier,
    max_deviation,
    cooldown_days
):

    cash = float(initial_cash)

    positions = {}

    trades = []

    equity_curve = []

    cooldown = {}

    loss_streak = 0

    all_dates = set()

    for ticker, df in ticker_data.items():

        if df is not None and not df.empty:

            all_dates.update(df.index)

    if market_data is not None:

        all_dates.update(
            market_data.index
        )

    all_dates = sorted(all_dates)

    for current_date in all_dates:

        # =================================================
        # 市場環境
        # =================================================

        market_factor = 1.0
        market_label = "不明"

        if (
            market_data is not None
            and
            current_date in market_data.index
        ):

            market_row = (
                market_data.loc[current_date]
            )

            if (
                pd.notna(market_row["MA200"])
            ):

                (
                    market_label,
                    market_factor
                ) = market_regime(
                    market_row
                )

        # =================================================
        # 売却
        # =================================================

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[ticker]

            if current_date not in df.index:
                continue

            row = df.loc[
                current_date
            ]

            position = positions[
                ticker
            ]

            should_sell, reason = (
                sell_signal(
                    row,
                    position,
                    stop_loss,
                    take_profit,
                    trailing_stop
                )
            )

            if should_sell:

                sell_price = float(
                    row["Close"]
                )

                shares = int(
                    position["shares"]
                )

                proceeds = (
                    sell_price
                    * shares
                )

                pnl = (
                    sell_price
                    - position[
                        "entry_price"
                    ]
                ) * shares

                cash += proceeds

                if pnl < 0:

                    loss_streak += 1

                else:

                    loss_streak = 0

                trades.append({

                    "Date": current_date,

                    "Ticker": ticker.replace(
                        ".T",
                        ""
                    ),

                    "Name": get_stock_name(
                        ticker
                    ),

                    "Action": "SELL",

                    "Price": sell_price,

                    "Shares": shares,

                    "Amount": proceeds,

                    "PnL": pnl,

                    "Score": position[
                        "score"
                    ],

                    "Reason": reason,

                    "Market": position[
                        "market"
                    ],

                    "LossStreak": loss_streak

                })

                if reason == "損切り":

                    cooldown[
                        ticker
                    ] = (
                        current_date
                        + pd.Timedelta(
                            days=cooldown_days
                        )
                    )

                del positions[
                    ticker
                ]

        # =================================================
        # BUY候補をランキング
        # =================================================

        candidates = []

        for ticker, df in ticker_data.items():

            if current_date not in df.index:
                continue

            if ticker in positions:
                continue

            if (
                ticker in cooldown
                and
                current_date
                <= cooldown[ticker]
            ):
                continue

            row = df.loc[
                current_date
            ]

            if pd.isna(
                row["MA200"]
            ):
                continue

            if pd.isna(
                row["RSI"]
            ):
                continue

            signal, score, reason = (
                buy_signal(
                    row,
                    min_score,
                    volume_multiplier,
                    max_deviation,
                    market_factor,
                    loss_streak
                )
            )

            if not signal:
                continue

            candidates.append({

                "ticker": ticker,

                "score": score,

                "price": float(
                    row["Close"]
                ),

                "row": row

            })

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        # =================================================
        # BUY
        # =================================================

        for candidate in candidates:

            if len(
                positions
            ) >= max_positions:

                break

            ticker = candidate[
                "ticker"
            ]

            score = candidate[
                "score"
            ]

            price = candidate[
                "price"
            ]

            allocation = (
                allocation_ratio(
                    score
                )
            )

            # 市場環境による調整
            allocation *= (
                market_factor
            )

            # 連続損失ブレーキ
            if loss_streak >= 3:

                allocation *= 0.50

            if loss_streak >= 4:

                allocation *= 0.50

            if loss_streak >= 5:

                allocation = 0

            if allocation <= 0:
                continue

            purchase_limit = min(
                max_per_position
                * allocation,
                cash
            )

            shares = int(
                purchase_limit
                // price
            )

            if shares <= 0:
                continue

            amount = (
                price
                * shares
            )

            if amount > cash:
                continue

            cash -= amount

            positions[
                ticker
            ] = {

                "entry_price": price,

                "shares": shares,

                "highest_price": price,

                "score": score,

                "market": market_label

            }

            trades.append({

                "Date": current_date,

                "Ticker": ticker.replace(
                    ".T",
                    ""
                ),

                "Name": get_stock_name(
                    ticker
                ),

                "Action": "BUY",

                "Price": price,

                "Shares": shares,

                "Amount": amount,

                "PnL": 0,

                "Score": score,

                "Reason": (
                    score_label(score)
                ),

                "Market": market_label,

                "LossStreak": loss_streak

            })

        # =================================================
        # 資産評価
        # =================================================

        equity = cash

        for ticker, position in (
            positions.items()
        ):

            df = ticker_data[
                ticker
            ]

            if current_date in df.index:

                price = float(
                    df.loc[
                        current_date
                    ]["Close"]
                )

                equity += (
                    price
                    * position[
                        "shares"
                    ]
                )

        equity_curve.append({

            "Date": current_date,

            "Equity": equity,

            "Cash": cash,

            "Positions": len(
                positions
            )

        })

    # =====================================================
    # 最終決済
    # =====================================================

    if all_dates:

        final_date = all_dates[-1]

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[
                ticker
            ]

            if final_date not in df.index:
                continue

            row = df.loc[
                final_date
            ]

            sell_price = float(
                row["Close"]
            )

            position = positions[
                ticker
            ]

            shares = int(
                position["shares"]
            )

            proceeds = (
                sell_price
                * shares
            )

            pnl = (
                sell_price
                - position[
                    "entry_price"
                ]
            ) * shares

            cash += proceeds

            trades.append({

                "Date": final_date,

                "Ticker": ticker.replace(
                    ".T",
                    ""
                ),

                "Name": get_stock_name(
                    ticker
                ),

                "Action": "SELL",

                "Price": sell_price,

                "Shares": shares,

                "Amount": proceeds,

                "PnL": pnl,

                "Score": position[
                    "score"
                ],

                "Reason": "最終決済",

                "Market": position[
                    "market"
                ],

                "LossStreak": loss_streak

            })

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_curve
    )

    return (
        trades_df,
        equity_df
    )


# =========================================================
# AI BUYランキング
# =========================================================

def create_buy_ranking(
    ticker_data,
    market_data,
    min_score,
    volume_multiplier,
    max_deviation
):

    results = []

    market_factor = 1.0
    market_label = "不明"

    if (
        market_data is not None
        and
        not market_data.empty
    ):

        row = market_data.iloc[-1]

        if pd.notna(
            row["MA200"]
        ):

            (
                market_label,
                market_factor
            ) = market_regime(
                row
            )

    for ticker, df in ticker_data.items():

        if df.empty:
            continue

        row = df.iloc[-1]

        if pd.isna(
            row["MA200"]
        ):
            continue

        score = calculate_buy_score(
            row,
            volume_multiplier
        )

        rsi_ok = (
            rsi_low
            <= row["RSI"]
            <= rsi_high
        )

        deviation_ok = (
            abs(
                row["MA25_DEVIATION"]
            )
            <= max_deviation
        )

        price_ok = (
            row["Close"] >= 2000
        )

        signal = (
            score >= min_score
            and rsi_ok
            and deviation_ok
            and price_ok
            and market_factor > 0
        )

        results.append({

            "Ticker": ticker.replace(
                ".T",
                ""
            ),

            "銘柄名": get_stock_name(
                ticker
            ),

            "株価": float(
                row["Close"]
            ),

            "BUYスコア": int(
                score
            ),

            "判定": (
                score_label(score)
                if signal
                else "⚪ 見送り"
            ),

            "RSI": float(
                row["RSI"]
            ),

            "25日線乖離率": float(
                row[
                    "MA25_DEVIATION"
                ]
            ),

            "25日線": float(
                row["MA25"]
            ),

            "75日線": float(
                row["MA75"]
            ),

            "200日線": float(
                row["MA200"]
            ),

            "市場環境": market_label,

            "推奨資金配分": (
                allocation_ratio(
                    score
                )
                * market_factor
                * 100
            )

        })

    ranking = pd.DataFrame(
        results
    )

    if not ranking.empty:

        ranking = ranking.sort_values(
            [
                "BUYスコア",
                "RSI"
            ],
            ascending=[
                False,
                True
            ]
        )

        ranking.insert(
            0,
            "順位",
            range(
                1,
                len(ranking) + 1
            )
        )

    return ranking


# =========================================================
# データ取得ボタン
# =========================================================

st.subheader("📥 データ取得")


if st.button(
    "🚀 Ver.4.4 バックテスト開始",
    type="primary"
):

    if yf is None:

        st.error(
            "yfinanceがインストールされていません。"
            "requirements.txtを確認してください。"
        )

        st.stop()

    progress = st.progress(0)

    ticker_data = {}

    for i, ticker in enumerate(
        tickers
    ):

        df = download_stock_data(
            ticker,
            years
        )

        if (
            df is not None
            and
            not df.empty
        ):

            df = add_indicators(
                df
            )

            ticker_data[
                ticker
            ] = df

        progress.progress(
            int(
                (i + 1)
                / len(tickers)
                * 100
            )
        )

    progress.empty()

    if not ticker_data:

        st.error(
            "株価データを取得できませんでした。"
        )

        st.stop()

    st.success(
        f"{len(ticker_data)}銘柄のデータを取得しました。"
    )

    # =====================================================
    # 市場データ
    # =====================================================

    market_data = get_market_data(
        years
    )

    # =====================================================
    # 現在の市場環境
    # =====================================================

    st.header(
        "🌏 現在の市場環境"
    )

    if (
        market_data is not None
        and
        not market_data.empty
    ):

        latest_market = (
            market_data.iloc[-1]
        )

        if pd.notna(
            latest_market["MA200"]
        ):

            (
                market_label,
                market_factor
            ) = market_regime(
                latest_market
            )

            m1, m2, m3, m4 = (
                st.columns(4)
            )

            m1.metric(
                "日経225",
                f"¥{latest_market['Close']:,.0f}"
            )

            m2.metric(
                "25日線",
                f"¥{latest_market['MA25']:,.0f}"
            )

            m3.metric(
                "75日線",
                f"¥{latest_market['MA75']:,.0f}"
            )

            m4.metric(
                "200日線",
                f"¥{latest_market['MA200']:,.0f}"
            )

            st.subheader(
                f"市場判定：{market_label}"
            )

            st.write(
                f"BUY資金係数："
                f"{market_factor * 100:.0f}%"
            )

    # =====================================================
    # AI BUYランキング
    # =====================================================

    st.header(
        "🏆 AI BUYランキング"
    )

    ranking = create_buy_ranking(
        ticker_data,
        market_data,
        min_score,
        volume_multiplier,
        max_deviation
    )

    if not ranking.empty:

        display_ranking = ranking.copy()

        display_ranking[
            "株価"
        ] = display_ranking[
            "株価"
        ].map(
            lambda x:
            f"¥{x:,.0f}"
        )

        display_ranking[
            "RSI"
        ] = display_ranking[
            "RSI"
        ].map(
            lambda x:
            f"{x:.1f}"
        )

        display_ranking[
            "25日線乖離率"
        ] = display_ranking[
            "25日線乖離率"
        ].map(
            lambda x:
            f"{x:+.1f}%"
        )

        display_ranking[
            "推奨資金配分"
        ] = display_ranking[
            "推奨資金配分"
        ].map(
            lambda x:
            f"{x:.0f}%"
        )

        st.dataframe(
            display_ranking[
                [
                    "順位",
                    "Ticker",
                    "銘柄名",
                    "株価",
                    "BUYスコア",
                    "判定",
                    "RSI",
                    "25日線乖離率",
                    "市場環境",
                    "推奨資金配分"
                ]
            ],
            use_container_width=True,
            hide_index=True
        )

    else:

        st.info(
            "現在の条件ではBUY候補がありません。"
        )

    # =====================================================
    # バックテスト
    # =====================================================

    (
        trades_df,
        equity_df
    ) = run_backtest(

        ticker_data,

        market_data,

        initial_cash,

        max_positions,

        max_per_position,

        stop_loss,

        take_profit,

        trailing_stop,

        min_score,

        volume_multiplier,

        max_deviation,

        cooldown_days

    )

    # =====================================================
    # 結果
    # =====================================================

    st.header(
        "📊 Ver.4.4 バックテスト結果"
    )

    if equity_df.empty:

        st.warning(
            "資産推移データがありません。"
        )

        st.stop()

    # 最終資産
    final_equity = float(
        equity_df.iloc[-1][
            "Equity"
        ]
    )

    profit = (
        final_equity
        - initial_cash
    )

    profit_pct = (
        profit
        / initial_cash
    ) * 100

    # DD
    equity_df["Peak"] = (
        equity_df[
            "Equity"
        ].cummax()
    )

    equity_df["Drawdown"] = (
        equity_df[
            "Equity"
        ]
        - equity_df[
            "Peak"
        ]
    )

    equity_df["DrawdownPct"] = (
        equity_df[
            "Drawdown"
        ]
        / equity_df[
            "Peak"
        ]
    ) * 100

    max_dd = float(
        equity_df[
            "Drawdown"
        ].min()
    )

    max_dd_pct = float(
        equity_df[
            "DrawdownPct"
        ].min()
    )

    # =====================================================
    # 基本指標
    # =====================================================

    c1, c2, c3, c4 = (
        st.columns(4)
    )

    c1.metric(
        "最終資産",
        f"¥{final_equity:,.0f}"
    )

    c2.metric(
        "損益",
        f"¥{profit:,.0f}"
    )

    c3.metric(
        "損益率",
        f"{profit_pct:.2f}%"
    )

    c4.metric(
        "最大DD",
        f"¥{max_dd:,.0f}"
    )

    # =====================================================
    # トレード統計
    # =====================================================

    if not trades_df.empty:

        sells = trades_df[
            trades_df[
                "Action"
            ] == "SELL"
        ].copy()

        st.subheader(
            "📐 トレード統計"
        )

        if not sells.empty:

            wins = sells[
                sells["PnL"] > 0
            ]

            losses = sells[
                sells["PnL"] < 0
            ]

            win_rate = (
                len(wins)
                / len(sells)
                * 100
            )

            gross_profit = float(
                wins["PnL"].sum()
            )

            gross_loss = abs(
                float(
                    losses[
                        "PnL"
                    ].sum()
                )
            )

            if gross_loss > 0:

                profit_factor = (
                    gross_profit
                    / gross_loss
                )

            else:

                profit_factor = np.inf

            avg_win = (
                float(
                    wins[
                        "PnL"
                    ].mean()
                )
                if not wins.empty
                else 0
            )

            avg_loss = (
                abs(
                    float(
                        losses[
                            "PnL"
                        ].mean()
                    )
                )
                if not losses.empty
                else 0
            )

            risk_reward = (
                avg_win
                / avg_loss
                if avg_loss > 0
                else np.inf
            )

            t1, t2, t3, t4, t5 = (
                st.columns(5)
            )

            t1.metric(
                "決済トレード数",
                f"{len(sells)}"
            )

            t2.metric(
                "勝率",
                f"{win_rate:.1f}%"
            )

            t3.metric(
                "Profit Factor",
                (
                    f"{profit_factor:.2f}"
                    if np.isfinite(
                        profit_factor
                    )
                    else "∞"
                )
            )

            t4.metric(
                "平均利益",
                f"¥{avg_win:,.0f}"
            )

            t5.metric(
                "平均利益/損失",
                (
                    f"{risk_reward:.2f}倍"
                    if np.isfinite(
                        risk_reward
                    )
                    else "∞"
                )
            )

            d1, d2 = (
                st.columns(2)
            )

            d1.metric(
                "最大DD額",
                f"¥{max_dd:,.0f}"
            )

            d2.metric(
                "最大DD率",
                f"{max_dd_pct:.2f}%"
            )

            # =================================================
            # 資産推移
            # =================================================

            st.subheader(
                "📈 資産推移"
            )

            chart_equity = (
                equity_df
                .set_index(
                    "Date"
                )[
                    "Equity"
                ]
            )

            st.line_chart(
                chart_equity
            )

            # =================================================
            # DD
            # =================================================

            st.subheader(
                "📉 ドローダウン"
            )

            chart_dd = (
                equity_df
                .set_index(
                    "Date"
                )[
                    "Drawdown"
                ]
            )

            st.area_chart(
                chart_dd
            )

            # =================================================
            # 銘柄別成績
            # =================================================

            st.subheader(
                "🏆 銘柄別成績"
            )

            stock_result = (
                sells
                .groupby(
                    [
                        "Ticker",
                        "Name"
                    ]
                )
                .agg(

                    売買回数=(
                        "PnL",
                        "count"
                    ),

                    損益=(
                        "PnL",
                        "sum"
                    ),

                    平均損益=(
                        "PnL",
                        "mean"
                    ),

                    勝率=(
                        "PnL",
                        lambda x:
                        (
                            x > 0
                        ).mean()
                        * 100
                    )

                )
                .sort_values(
                    "損益",
                    ascending=False
                )
            )

            st.dataframe(
                stock_result.style.format({

                    "損益":
                    "¥{:,.0f}",

                    "平均損益":
                    "¥{:,.0f}",

                    "勝率":
                    "{:.1f}%"

                }),
                use_container_width=True
            )

            # =================================================
            # 良いトレード
            # =================================================

            st.subheader(
                "🟢 良いトレード"
            )

            good_trades = (
                sells[
                    sells["PnL"] > 0
                ]
                .sort_values(
                    "PnL",
                    ascending=False
                )
            )

            st.dataframe(
                good_trades.head(20),
                use_container_width=True,
                hide_index=True
            )

            # =================================================
            # 悪いトレード
            # =================================================

            st.subheader(
                "🔴 改善対象トレード"
            )

            bad_trades = (
                sells[
                    sells["PnL"] < 0
                ]
                .sort_values(
                    "PnL"
                )
            )

            st.dataframe(
                bad_trades.head(20),
                use_container_width=True,
                hide_index=True
            )

            # =================================================
            # 売却理由
            # =================================================

            st.subheader(
                "🚦 売却理由別成績"
            )

            reason_result = (
                sells
                .groupby(
                    "Reason"
                )
                .agg(

                    回数=(
                        "PnL",
                        "count"
                    ),

                    損益=(
                        "PnL",
                        "sum"
                    ),

                    勝率=(
                        "PnL",
                        lambda x:
                        (
                            x > 0
                        ).mean()
                        * 100
                    )

                )
                .sort_values(
                    "損益",
                    ascending=False
                )
            )

            st.dataframe(
                reason_result.style.format({

                    "損益":
                    "¥{:,.0f}",

                    "勝率":
                    "{:.1f}%"

                }),
                use_container_width=True
            )

        # =====================================================
        # 全売買記録
        # =====================================================

        st.subheader(
            "📋 全売買記録"
        )

        st.dataframe(
            trades_df,
            use_container_width=True,
            hide_index=True
        )

        # =====================================================
        # CSV
        # =====================================================

        csv = (
            trades_df
            .to_csv(
                index=False
            )
            .encode(
                "utf-8-sig"
            )
        )

        st.download_button(
            "⬇️ 売買記録CSVをダウンロード",
            data=csv,
            file_name="ver4_4_trades.csv",
            mime="text/csv"
        )

    else:

        st.warning(
            "売買が発生しませんでした。"
            "最低BUYスコアやRSI条件を調整して再テストしてください。"
        )


# =========================================================
# Ver.4.4 説明
# =========================================================

st.divider()

st.subheader(
    "🧠 Ver.4.4 売買思想"
)

st.markdown(
    """
## 🟢 BUYスコア

**100点満点**

| 条件 | 点数 |
|---|---:|
| 25日線 > 75日線 | 20点 |
| 株価 > 200日線 | 20点 |
| 株価 > 25日線 | 15点 |
| 出来高条件 | 15点 |
| RSI適正 | 15点 |
| 25日線上向き | 10点 |
| 75日線上向き | 5点 |

---

## 🏆 AI BUYランキング

各銘柄をテクニカルスコアで評価し、

**スコアの高い順にランキング**

します。

### 判定

- 90点以上 → 🔥 強BUY
- 85～89点 → 🟢 BUY強
- 75～84点 → 🟢 BUY
- 75点未満 → ⚪ 見送り

---

## 💰 スコア別資金配分

- 90点以上 → 100%
- 85～89点 → 85%
- 80～84点 → 70%
- 75～79点 → 50%

さらに市場環境によって購入金額を調整します。

---

## 🌏 市場環境フィルター

日経225について、

- 25日線
- 75日線
- 200日線
- 25日線の傾き

を確認します。

### 市場判定

🟢 強気  
🟡 やや強気  
⚪ 中立  
🟠 やや弱気  
🔴 弱気

弱い市場では新規BUYを抑制します。

---

## 🛑 連続損失ブレーキ

3連敗以上になると、

**BUY条件を厳しくします。**

さらに購入金額を縮小します。

5連敗では、

**90点未満の新規BUYを停止**

します。

---

## ⏳ 損切り後の再エントリー

損切りした銘柄は、

**標準10営業日**

再購入を禁止します。

---

## 🛡️ リスク管理

### 損切り

**-7%**

### 利確開始

**+15%**

### トレーリングストップ

利益が伸びた後、

最高値から一定割合下落したところで利確。

### 25日線割れ

利益が5%以上ある場合、

25日線割れ → 利益確定。

### 75日線割れ

75日線割れ → 撤退。

---

## 🚫 明けの明星

**完全に使用しません。**

Ver.4.4では、

明けの明星を

**BUY条件・ランキング・スコアのいずれにも使用していません。**

---

## 🎯 Ver.4.4の目的

単純に勝率だけを上げるのではなく、

> **「悪い買いを減らし、大きな利益を伸ばす」**

ことを重視します。

特に、

- 最大DD
- Profit Factor
- 平均利益/平均損失
- 勝率
- 銘柄別成績
- 売却理由別成績
- 市場環境

を確認します。

---

### ⚠️ 注意

このアプリは過去の株価データを利用した

**仮想バックテスト・銘柄分析ツール**

です。

バックテスト結果やAI BUYランキングは、

**将来の利益を保証するものではありません。**
"""
)
