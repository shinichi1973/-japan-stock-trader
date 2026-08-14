import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile


# =========================================================
# ページ設定
# =========================================================
st.set_page_config(
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.7",
    page_icon="📈",
    layout="wide"
)

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.7"
)

st.caption(
    "S株を想定した仮想バックテスト｜"
    "AI BUYランキング｜市場環境フィルター｜"
    "連続損失ブレーキ｜全AI判定CSV"
)

st.info(
    "Ver.4.7では「明けの明星」と「株価2,000円以上」を "
    "BUY選定条件から完全に除外しています。"
)


# =========================================================
# サイドバー
# =========================================================
st.sidebar.header("⚙️ バックテスト設定")

initial_cash = st.sidebar.number_input(
    "初期資金（円）",
    min_value=10000,
    value=100000,
    step=10000
)

max_positions = st.sidebar.number_input(
    "最大保有銘柄数",
    min_value=1,
    max_value=30,
    value=10,
    step=1
)

max_per_position = st.sidebar.number_input(
    "1銘柄最大購入額（円）",
    min_value=1000,
    value=10000,
    step=1000
)

stop_loss = st.sidebar.slider(
    "損切り（%）",
    3.0,
    10.0,
    6.0,
    0.5
)

profit_start = st.sidebar.slider(
    "トレーリング開始利益（%）",
    3.0,
    10.0,
    5.0,
    0.5
)

trailing_stop = st.sidebar.slider(
    "トレーリング幅（%）",
    2.0,
    8.0,
    4.0,
    0.5
)

take_profit = st.sidebar.slider(
    "通常利確（%）",
    8.0,
    30.0,
    15.0,
    1.0
)

rsi_low = st.sidebar.slider(
    "RSI下限",
    30,
    60,
    40
)

rsi_high = st.sidebar.slider(
    "RSI上限",
    60,
    80,
    70
)

min_score = st.sidebar.slider(
    "最低BUYスコア",
    70,
    90,
    75
)

lookback_years = st.sidebar.slider(
    "バックテスト期間（年）",
    2,
    5,
    5
)

diagnostic_mode = st.sidebar.checkbox(
    "🔎 詳細診断モード",
    value=False
)

ticker_input = st.sidebar.text_area(
    "対象銘柄コード",
    value=(
        "7203,6758,9984,8306,9432,"
        "6501,8035,8058,7267,2914"
    )
)


# =========================================================
# 銘柄コード整理
# =========================================================
def normalize_tickers(text):

    result = []

    for x in text.replace("\n", ",").split(","):

        x = x.strip()

        if not x:
            continue

        if not x.endswith(".T"):
            x += ".T"

        result.append(x)

    return list(dict.fromkeys(result))


tickers = normalize_tickers(
    ticker_input
)


# =========================================================
# 銘柄名マスター
# =========================================================
STOCK_NAMES = {

    "7203": "トヨタ自動車",

    "6758": "ソニーグループ",

    "9984": "ソフトバンクグループ",

    "8306": "三菱UFJフィナンシャル・グループ",

    "9432": "NTT",

    "6501": "日立製作所",

    "8035": "東京エレクトロン",

    "8058": "三菱商事",

    "7267": "ホンダ",

    "2914": "JT",

    "9433": "KDDI",

    "8316": "三井住友フィナンシャルグループ",

    "8411": "みずほフィナンシャルグループ",

    "6098": "リクルートホールディングス",

    "4063": "信越化学工業",

    "4519": "中外製薬",

    "6367": "ダイキン工業",

    "6857": "アドバンテスト",

    "7974": "任天堂",

    "8766": "東京海上ホールディングス",

    "5401": "日本製鉄",

    "8801": "三井不動産",

    "8802": "三菱地所",

    "4502": "武田薬品工業",

    "4503": "アステラス製薬",

    "4523": "エーザイ",

    "4755": "楽天グループ",

    "6594": "ニデック",

    "7741": "HOYA",

    "6981": "村田製作所"
}


# =========================================================
# 銘柄名取得
# =========================================================
@st.cache_data(ttl=86400)
def get_stock_name(ticker):

    code = ticker.replace(
        ".T",
        ""
    )

    if code in STOCK_NAMES:
        return STOCK_NAMES[code]

    try:

        info = yf.Ticker(
            ticker
        ).info

        name = (
            info.get("shortName")
            or info.get("longName")
            or info.get("displayName")
        )

        if name:
            return str(name)

    except Exception:
        pass

    return "銘柄名未登録"


# =========================================================
# 指標計算
# =========================================================
def calculate_indicators(df):

    if df.empty:
        return pd.DataFrame()

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

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

    if not all(
        x in df.columns
        for x in required
    ):
        return pd.DataFrame()

    df = df[
        required
    ].copy()

    # ---------------------------------------------
    # 移動平均
    # ---------------------------------------------
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

    # ---------------------------------------------
    # MA傾き
    # ---------------------------------------------
    df["MA25_Slope"] = (
        df["MA25"]
        - df["MA25"].shift(5)
    )

    df["MA75_Slope"] = (
        df["MA75"]
        - df["MA75"].shift(5)
    )

    # ---------------------------------------------
    # 出来高
    # ---------------------------------------------
    df["VOL20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    # ---------------------------------------------
    # RSI
    # ---------------------------------------------
    delta = df[
        "Close"
    ].diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .rolling(14)
        .mean()
    )

    avg_loss = (
        loss
        .rolling(14)
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    # ---------------------------------------------
    # ATR
    # ---------------------------------------------
    tr1 = (
        df["High"]
        - df["Low"]
    )

    tr2 = (
        df["High"]
        - df["Close"].shift()
    ).abs()

    tr3 = (
        df["Low"]
        - df["Close"].shift()
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    df["ATR14"] = (
        tr
        .rolling(14)
        .mean()
    )

    return df.dropna()


# =========================================================
# 株価取得
# =========================================================
@st.cache_data(ttl=3600)
def download_stock_data(
    ticker,
    years
):

    end_date = datetime.now()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 300
        )
    )

    try:

        df = yf.download(
            ticker,
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=False,
            progress=False
        )

        return calculate_indicators(
            df
        )

    except Exception:

        return pd.DataFrame()


# =========================================================
# 日経225取得
# =========================================================
@st.cache_data(ttl=3600)
def download_market_data(
    years
):

    end_date = datetime.now()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 300
        )
    )

    try:

        df = yf.download(
            "^N225",
            start=start_date,
            end=end_date + timedelta(days=1),
            auto_adjust=False,
            progress=False
        )

        if df.empty:
            return pd.DataFrame()

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        close = df[
            "Close"
        ].copy()

        market = pd.DataFrame(
            index=close.index
        )

        market["Close"] = close

        market["MA25"] = (
            close
            .rolling(25)
            .mean()
        )

        market["MA75"] = (
            close
            .rolling(75)
            .mean()
        )

        market["MA200"] = (
            close
            .rolling(200)
            .mean()
        )

        market["MA25_Slope"] = (
            market["MA25"]
            - market["MA25"].shift(5)
        )

        return market.dropna()

    except Exception:

        return pd.DataFrame()


# =========================================================
# 市場環境
# =========================================================
def market_condition(
    market_df,
    current_date
):

    if market_df.empty:

        return {
            "判定": "⚪ データなし",
            "係数": 1.0,
            "価格": np.nan,
            "MA25": np.nan,
            "MA75": np.nan,
            "MA200": np.nan,
            "MA25傾き": np.nan
        }

    available = market_df[
        market_df.index
        <= current_date
    ]

    if available.empty:

        return {
            "判定": "⚪ データなし",
            "係数": 1.0,
            "価格": np.nan,
            "MA25": np.nan,
            "MA75": np.nan,
            "MA200": np.nan,
            "MA25傾き": np.nan
        }

    row = available.iloc[-1]

    price = float(
        row["Close"]
    )

    ma25 = float(
        row["MA25"]
    )

    ma75 = float(
        row["MA75"]
    )

    ma200 = float(
        row["MA200"]
    )

    slope = float(
        row["MA25_Slope"]
    )

    points = 0

    if price > ma25:
        points += 1

    if ma25 > ma75:
        points += 1

    if ma75 > ma200:
        points += 1

    if slope > 0:
        points += 1

    if points == 4:

        judgement = "🟢 強気"
        factor = 1.00

    elif points == 3:

        judgement = "🟡 やや強気"
        factor = 0.85

    elif points == 2:

        judgement = "⚪ 中立"
        factor = 0.60

    elif points == 1:

        judgement = "🟠 やや弱気"
        factor = 0.35

    else:

        judgement = "🔴 弱気"
        factor = 0.00

    return {

        "判定": judgement,

        "係数": factor,

        "価格": price,

        "MA25": ma25,

        "MA75": ma75,

        "MA200": ma200,

        "MA25傾き": slope
    }


# =========================================================
# AIスコア
# =========================================================
def calculate_score(
    row
):

    score = 0

    details = {}


    # 1
    c1 = (
        row["MA25"]
        > row["MA75"]
    )

    details[
        "25日線>75日線"
    ] = 20 if c1 else 0

    score += details[
        "25日線>75日線"
    ]


    # 2
    c2 = (
        row["Close"]
        > row["MA200"]
    )

    details[
        "株価>200日線"
    ] = 20 if c2 else 0

    score += details[
        "株価>200日線"
    ]


    # 3
    c3 = (
        row["Close"]
        > row["MA25"]
    )

    details[
        "株価>25日線"
    ] = 15 if c3 else 0

    score += details[
        "株価>25日線"
    ]


    # 4
    c4 = (
        row["Volume"]
        > row["VOL20"]
    )

    details[
        "出来高"
    ] = 15 if c4 else 0

    score += details[
        "出来高"
    ]


    # 5
    c5 = (
        rsi_low
        <= row["RSI"]
        <= rsi_high
    )

    details[
        "RSI適正"
    ] = 15 if c5 else 0

    score += details[
        "RSI適正"
    ]


    # 6
    c6 = (
        row["MA25_Slope"]
        > 0
    )

    details[
        "25日線上向き"
    ] = 10 if c6 else 0

    score += details[
        "25日線上向き"
    ]


    # 7
    c7 = (
        row["MA75_Slope"]
        > 0
    )

    details[
        "75日線上向き"
    ] = 5 if c7 else 0

    score += details[
        "75日線上向き"
    ]


    return score, details


# =========================================================
# スコア係数
# =========================================================
def score_factor(
    score
):

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
# スコア判定
# =========================================================
def score_judgement(
    score
):

    if score >= 90:
        return "🔥 強BUY"

    if score >= 85:
        return "🟢 BUY強"

    if score >= 75:
        return "🟢 BUY"

    return "⚪ 見送り"


# =========================================================
# 過去成績係数
# =========================================================
def historical_factor(
    stats
):

    if stats is None:
        return 1.00

    trades = stats[
        "trades"
    ]

    wins = stats[
        "wins"
    ]

    if trades < 3:
        return 1.00

    win_rate = (
        wins / trades
    )

    if win_rate >= 0.65:
        return 1.10

    if win_rate >= 0.55:
        return 1.05

    if win_rate < 0.35:
        return 0.70

    if win_rate < 0.45:
        return 0.85

    return 1.00


# =========================================================
# 連敗係数
# =========================================================
def loss_brake_factor(
    consecutive_losses
):

    if consecutive_losses >= 5:
        return 0.00

    if consecutive_losses >= 4:
        return 0.30

    if consecutive_losses >= 3:
        return 0.50

    if consecutive_losses >= 2:
        return 0.80

    return 1.00


# =========================================================
# バックテスト
# =========================================================
def run_backtest(
    data_dict,
    market_df
):

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    equity_records = []

    analysis_records = []

    market_records = []

    stats = {}

    consecutive_losses = 0

    max_consecutive_losses = 0


    for ticker in data_dict:

        stats[ticker] = {

            "trades": 0,

            "wins": 0
        }


    # 全取引日
    all_dates = sorted(
        set(
            d
            for df in data_dict.values()
            for d in df.index
        )
    )


    # =====================================================
    # 日付ループ
    # =====================================================
    for current_date in all_dates:


        # =================================================
        # SELL
        # =================================================
        for ticker in list(
            positions.keys()
        ):

            df = data_dict[
                ticker
            ]

            if current_date not in df.index:
                continue

            row = df.loc[
                current_date
            ]

            pos = positions[
                ticker
            ]

            price = float(
                row["Close"]
            )

            entry_price = pos[
                "entry_price"
            ]

            shares = pos[
                "shares"
            ]

            profit_pct = (
                price
                / entry_price
                - 1
            ) * 100


            if price > pos[
                "highest_price"
            ]:

                pos[
                    "highest_price"
                ] = price


            highest_price = pos[
                "highest_price"
            ]


            trailing_price = (
                highest_price
                * (
                    1
                    - trailing_stop
                    / 100
                )
            )


            reason = ""


            if profit_pct <= -stop_loss:

                reason = "損切り"

            elif (
                profit_pct
                >= profit_start
                and price
                <= trailing_price
            ):

                reason = "トレーリング"

            elif profit_pct >= take_profit:

                reason = "利確"

            elif price < row["MA25"]:

                reason = "25日線割れ"


            if reason:

                sell_value = (
                    price * shares
                )

                cash += sell_value

                pnl = (
                    price
                    - entry_price
                ) * shares


                if pnl > 0:

                    stats[
                        ticker
                    ]["wins"] += 1

                    consecutive_losses = 0

                else:

                    consecutive_losses += 1

                    max_consecutive_losses = max(
                        max_consecutive_losses,
                        consecutive_losses
                    )


                stats[
                    ticker
                ]["trades"] += 1


                holding_days = (
                    pd.Timestamp(
                        current_date
                    )
                    - pd.Timestamp(
                        pos[
                            "entry_date"
                        ]
                    )
                ).days


                trades.append({

                    "日付":
                        current_date,

                    "コード":
                        ticker.replace(
                            ".T",
                            ""
                        ),

                    "銘柄名":
                        pos["name"],

                    "売買":
                        "SELL",

                    "価格":
                        price,

                    "株数":
                        shares,

                    "損益":
                        pnl,

                    "損益率":
                        profit_pct,

                    "理由":
                        reason,

                    "BUYスコア":
                        pos["score"],

                    "BUY日":
                        pos["entry_date"],

                    "保有日数":
                        holding_days
                })


                del positions[
                    ticker
                ]


        # =================================================
        # 市場環境
        # =================================================
        market = market_condition(
            market_df,
            current_date
        )


        market_records.append({

            "日付":
                current_date,

            "日経225":
                market["価格"],

            "日経225_25日線":
                market["MA25"],

            "日経225_75日線":
                market["MA75"],

            "日経225_200日線":
                market["MA200"],

            "日経225_25日線傾き":
                market["MA25傾き"],

            "市場判定":
                market["判定"],

            "市場BUY資金係数":
                market["係数"]
        })


        # =================================================
        # BUY判定前のブレーキ
        # =================================================
        loss_factor = loss_brake_factor(
            consecutive_losses
        )


        # =================================================
        # 各銘柄分析
        # =================================================
        candidates = []


        for ticker, df in data_dict.items():

            if current_date not in df.index:
                continue

            row = df.loc[
                current_date
            ]

            code = ticker.replace(
                ".T",
                ""
            )

            name = get_stock_name(
                ticker
            )

            price = float(
                row["Close"]
            )


            score, details = (
                calculate_score(
                    row
                )
            )


            judgement = score_judgement(
                score
            )


            sf = score_factor(
                score
            )


            hf = historical_factor(
                stats.get(
                    ticker
                )
            )


            # ---------------------------------------------
            # 2,000円以上は完全除外
            # ---------------------------------------------
            price_under_2000 = (
                price < 2000
            )


            if not price_under_2000:

                exclusion_reason = (
                    "株価2,000円以上のため除外"
                )

            elif score < min_score:

                exclusion_reason = (
                    f"AIスコア{score}点 "
                    f"< 最低{min_score}点"
                )

            elif market["係数"] <= 0:

                exclusion_reason = (
                    "市場環境によりBUY停止"
                )

            elif loss_factor <= 0:

                exclusion_reason = (
                    "連続損失ブレーキによりBUY停止"
                )

            elif sf <= 0:

                exclusion_reason = (
                    "BUYスコア不足"
                )

            else:

                exclusion_reason = ""


            final_factor = (
                sf
                * market["係数"]
                * loss_factor
                * hf
            )

            final_factor = min(
                final_factor,
                1.0
            )


            # ---------------------------------------------
            # 購入可能額
            # ---------------------------------------------
            theoretical_budget = (
                min(
                    max_per_position,
                    cash
                )
                * final_factor
            )


            actual_budget = 0


            # 候補登録
            if (
                price_under_2000
                and score >= min_score
                and market["係数"] > 0
                and loss_factor > 0
                and sf > 0
                and ticker not in positions
            ):

                candidates.append({

                    "ticker":
                        ticker,

                    "row":
                        row,

                    "score":
                        score,

                    "details":
                        details,

                    "factor":
                        final_factor,

                    "budget":
                        theoretical_budget,

                    "name":
                        name
                })


            # ---------------------------------------------
            # AI全判定履歴
            # ---------------------------------------------
            analysis_records.append({

                "日付":
                    current_date,

                "コード":
                    code,

                "銘柄名":
                    name,

                "株価":
                    price,

                "25日線":
                    float(
                        row["MA25"]
                    ),

                "75日線":
                    float(
                        row["MA75"]
                    ),

                "200日線":
                    float(
                        row["MA200"]
                    ),

                "RSI":
                    float(
                        row["RSI"]
                    ),

                "出来高":
                    float(
                        row["Volume"]
                    ),

                "出来高20日平均":
                    float(
                        row["VOL20"]
                    ),

                "出来高倍率":
                    (
                        float(
                            row["Volume"]
                        )
                        /
                        float(
                            row["VOL20"]
                        )
                    ),

                "25日線傾き":
                    float(
                        row["MA25_Slope"]
                    ),

                "75日線傾き":
                    float(
                        row["MA75_Slope"]
                    ),

                "25日線>75日線":
                    details[
                        "25日線>75日線"
                    ],

                "株価>200日線":
                    details[
                        "株価>200日線"
                    ],

                "株価>25日線":
                    details[
                        "株価>25日線"
                    ],

                "出来高条件":
                    details[
                        "出来高"
                    ],

                "RSI条件":
                    details[
                        "RSI適正"
                    ],

                "25日線上向き":
                    details[
                        "25日線上向き"
                    ],

                "75日線上向き":
                    details[
                        "75日線上向き"
                    ],

                "AIスコア":
                    score,

                "AI判定":
                    judgement,

                "2,000円未満":
                    price_under_2000,

                "市場環境":
                    market["判定"],

                "市場BUY資金係数":
                    market["係数"],

                "連続損失":
                    consecutive_losses,

                "損失ブレーキ係数":
                    loss_factor,

                "過去成績係数":
                    hf,

                "スコア資金係数":
                    sf,

                "最終資金係数":
                    final_factor,

                "理論購入可能額":
                    theoretical_budget,

                "実際購入額":
                    actual_budget,

                "判定理由":
                    exclusion_reason
            })


        # =================================================
        # スコア順BUY
        # =================================================
        candidates.sort(
            key=lambda x:
                x["score"],
            reverse=True
        )


        for candidate in candidates:

            if len(positions) >= max_positions:
                break


            ticker = candidate[
                "ticker"
            ]

            if ticker in positions:
                continue


            price = float(
                candidate[
                    "row"
                ]["Close"]
            )


            budget = min(
                candidate["budget"],
                cash
            )


            shares = int(
                budget / price
            )


            if shares <= 0:
                continue


            cost = (
                shares * price
            )


            if cost > cash:
                continue


            cash -= cost


            name = candidate[
                "name"
            ]


            positions[
                ticker
            ] = {

                "entry_price":
                    price,

                "shares":
                    shares,

                "highest_price":
                    price,

                "score":
                    candidate[
                        "score"
                    ],

                "entry_date":
                    current_date,

                "name":
                    name
            }


            # 実購入額を分析履歴に反映
            for rec in reversed(
                analysis_records
            ):

                if (
                    rec["日付"]
                    == current_date
                    and rec["コード"]
                    == ticker.replace(
                        ".T",
                        ""
                    )
                ):

                    rec[
                        "実際購入額"
                    ] = cost

                    rec[
                        "判定理由"
                    ] = "AI BUY実行"

                    break


            trades.append({

                "日付":
                    current_date,

                "コード":
                    ticker.replace(
                        ".T",
                        ""
                    ),

                "銘柄名":
                    name,

                "売買":
                    "BUY",

                "価格":
                    price,

                "株数":
                    shares,

                "損益":
                    0,

                "損益率":
                    0,

                "理由":
                    "AI BUY",

                "BUYスコア":
                    candidate[
                        "score"
                    ],

                "BUY日":
                    current_date,

                "保有日数":
                    0
            })


        # =================================================
        # 資産評価
        # =================================================
        holdings_value = 0


        for ticker, pos in positions.items():

            df = data_dict[
                ticker
            ]

            if current_date in df.index:

                price = float(
                    df.loc[
                        current_date
                    ]["Close"]
                )

                holdings_value += (
                    price
                    * pos["shares"]
                )


        total_asset = (
            cash
            + holdings_value
        )


        equity_records.append({

            "日付":
                current_date,

            "現金":
                cash,

            "保有株評価額":
                holdings_value,

            "総資産":
                total_asset,

            "保有銘柄数":
                len(positions)
        })


    # =====================================================
    # DataFrame化
    # =====================================================
    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_records
    )

    analysis_df = pd.DataFrame(
        analysis_records
    )

    market_history_df = pd.DataFrame(
        market_records
    )


    return (
        trades_df,
        equity_df,
        analysis_df,
        market_history_df,
        max_consecutive_losses
    )


# =========================================================
# データ取得
# =========================================================
st.subheader(
    "📥 データ取得"
)


data_dict = {}

stock_names = {}


progress = st.progress(0)


for i, ticker in enumerate(
    tickers
):

    df = download_stock_data(
        ticker,
        lookback_years
    )

    if not df.empty:

        data_dict[
            ticker
        ] = df

        stock_names[
            ticker
        ] = get_stock_name(
            ticker
        )

    progress.progress(
        int(
            (
                i + 1
            )
            /
            len(tickers)
            * 100
        )
    )


progress.empty()


st.write(
    f"**{len(data_dict)}銘柄のデータを取得しました。**"
)


if not data_dict:

    st.error(
        "データを取得できませんでした。"
    )

    st.stop()


# =========================================================
# 対象銘柄
# =========================================================
st.subheader(
    "🏢 対象銘柄"
)


stock_list = []


for ticker in data_dict:

    stock_list.append({

        "コード":
            ticker.replace(
                ".T",
                ""
            ),

        "銘柄名":
            stock_names.get(
                ticker,
                "銘柄名未登録"
            )
    })


stock_list_df = pd.DataFrame(
    stock_list
)


st.dataframe(
    stock_list_df,
    use_container_width=True,
    hide_index=True
)


# =========================================================
# 日経225
# =========================================================
st.subheader(
    "🌏 現在の市場環境"
)


market_df = download_market_data(
    lookback_years
)


if not market_df.empty:

    latest = market_df.iloc[-1]

    current = market_condition(
        market_df,
        market_df.index[-1]
    )


    c1, c2, c3, c4, c5 = st.columns(5)


    c1.metric(
        "日経225",
        f"¥{latest['Close']:,.0f}"
    )

    c2.metric(
        "25日線",
        f"¥{latest['MA25']:,.0f}"
    )

    c3.metric(
        "75日線",
        f"¥{latest['MA75']:,.0f}"
    )

    c4.metric(
        "200日線",
        f"¥{latest['MA200']:,.0f}"
    )

    c5.metric(
        "BUY資金係数",
        f"{current['係数']:.0%}"
    )


    st.success(
        f"市場判定：{current['判定']}"
    )


# =========================================================
# 現在のAI BUYランキング
# =========================================================
st.subheader(
    "🏆 AI BUYランキング"
)


ranking = []


for ticker, df in data_dict.items():

    row = df.iloc[-1]

    price = float(
        row["Close"]
    )

    if price >= 2000:
        continue

    score, details = (
        calculate_score(
            row
        )
    )

    ranking.append({

        "コード":
            ticker.replace(
                ".T",
                ""
            ),

        "銘柄名":
            stock_names.get(
                ticker,
                "銘柄名未登録"
            ),

        "株価":
            round(
                price,
                1
            ),

        "AIスコア":
            score,

        "判定":
            score_judgement(
                score
            ),

        "RSI":
            round(
                float(
                    row["RSI"]
                ),
                1
            ),

        "25日線":
            round(
                float(
                    row["MA25"]
                ),
                1
            ),

        "75日線":
            round(
                float(
                    row["MA75"]
                ),
                1
            ),

        "200日線":
            round(
                float(
                    row["MA200"]
                ),
                1
            ),

        "出来高倍率":
            round(
                float(
                    row["Volume"]
                    /
                    row["VOL20"]
                ),
                2
            )
    })


ranking_df = pd.DataFrame(
    ranking
)


if not ranking_df.empty:

    ranking_df = (
        ranking_df
        .sort_values(
            "AIスコア",
            ascending=False
        )
        .reset_index(
            drop=True
        )
    )

    ranking_df.insert(
        0,
        "順位",
        ranking_df.index + 1
    )

    st.dataframe(
        ranking_df,
        use_container_width=True,
        hide_index=True
    )

else:

    st.warning(
        "現在BUY条件を満たす銘柄はありません。"
    )


# =========================================================
# バックテスト
# =========================================================
st.subheader(
    "📊 Ver.4.7 バックテスト結果"
)


with st.spinner(
    "AIバックテストを実行中..."
):

    (
        trades_df,
        equity_df,
        analysis_df,
        market_history_df,
        max_consecutive_losses
    ) = run_backtest(
        data_dict,
        market_df
    )


if equity_df.empty:

    st.error(
        "バックテスト結果がありません。"
    )

    st.stop()


# =========================================================
# 最終資産
# =========================================================
final_asset = float(
    equity_df[
        "総資産"
    ].iloc[-1]
)

profit = (
    final_asset
    - initial_cash
)

return_rate = (
    profit
    / initial_cash
) * 100


# =========================================================
# 最大DD
# =========================================================
equity_series = (
    equity_df[
        "総資産"
    ]
)

running_max = (
    equity_series
    .cummax()
)

drawdown = (
    equity_series
    - running_max
)

drawdown_rate = (
    drawdown
    / running_max
    * 100
)

max_dd = float(
    drawdown.min()
)

max_dd_rate = float(
    drawdown_rate.min()
)


# =========================================================
# トレード統計
# =========================================================
if not trades_df.empty:

    sell_df = trades_df[
        trades_df["売買"]
        == "SELL"
    ].copy()

else:

    sell_df = pd.DataFrame()


trade_count = len(
    sell_df
)


if trade_count > 0:

    wins = sell_df[
        sell_df["損益"] > 0
    ]

    losses = sell_df[
        sell_df["損益"] < 0
    ]

    win_rate = (
        len(wins)
        / trade_count
        * 100
    )

    gross_profit = (
        wins["損益"].sum()
    )

    gross_loss = abs(
        losses["損益"].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = np.inf

    avg_profit = (
        wins["損益"].mean()
        if not wins.empty
        else 0
    )

    avg_loss = (
        abs(
            losses["損益"].mean()
        )
        if not losses.empty
        else 0
    )

else:

    win_rate = 0

    profit_factor = 0

    avg_profit = 0

    avg_loss = 0


# =========================================================
# 結果表示
# =========================================================
c1, c2, c3, c4 = st.columns(4)


c1.metric(
    "最終資産",
    f"¥{final_asset:,.0f}"
)

c2.metric(
    "損益",
    f"¥{profit:,.0f}"
)

c3.metric(
    "損益率",
    f"{return_rate:.2f}%"
)

c4.metric(
    "最大DD",
    f"¥{max_dd:,.0f}"
)


# =========================================================
# 統計
# =========================================================
st.subheader(
    "📐 トレード統計"
)


c1, c2, c3 = st.columns(3)


c1.metric(
    "決済トレード数",
    trade_count
)

c2.metric(
    "勝率",
    f"{win_rate:.1f}%"
)

c3.metric(
    "Profit Factor",
    (
        f"{profit_factor:.2f}"
        if np.isfinite(
            profit_factor
        )
        else "∞"
    )
)


c1, c2, c3 = st.columns(3)


c1.metric(
    "平均利益",
    f"¥{avg_profit:,.0f}"
)

c2.metric(
    "平均損失",
    f"¥{avg_loss:,.0f}"
)

if avg_loss > 0:

    avg_ratio = (
        avg_profit
        / avg_loss
    )

else:

    avg_ratio = 0


c3.metric(
    "平均利益/損失",
    f"{avg_ratio:.2f}倍"
)


st.metric(
    "最大DD率",
    f"{max_dd_rate:.2f}%"
)


# =========================================================
# 資産推移
# =========================================================
st.subheader(
    "📈 資産推移"
)


asset_chart = equity_df[
    [
        "日付",
        "総資産"
    ]
].copy()


asset_chart["日付"] = pd.to_datetime(
    asset_chart["日付"]
)

asset_chart = asset_chart.set_index(
    "日付"
)


st.line_chart(
    asset_chart[
        "総資産"
    ]
)


# =========================================================
# DD
# =========================================================
st.subheader(
    "📉 ドローダウン"
)


dd_chart = pd.DataFrame({

    "ドローダウン":
        drawdown.values

})

dd_chart.index = pd.to_datetime(
    equity_df["日付"]
)


st.area_chart(
    dd_chart
)


# =========================================================
# 銘柄別成績
# =========================================================
st.subheader(
    "🏆 銘柄別成績"
)


if not sell_df.empty:

    ticker_result = (
        sell_df
        .groupby(
            [
                "コード",
                "銘柄名"
            ]
        )
        .agg(

            トレード数=(
                "損益",
                "count"
            ),

            勝ち=(
                "損益",
                lambda x:
                    (
                        x > 0
                    ).sum()
            ),

            損益=(
                "損益",
                "sum"
            ),

            平均損益=(
                "損益",
                "mean"
            )
        )
        .reset_index()
    )


    ticker_result[
        "勝率"
    ] = (
        ticker_result["勝ち"]
        /
        ticker_result["トレード数"]
        * 100
    )


    ticker_result = (
        ticker_result
        .sort_values(
            "損益",
            ascending=False
        )
    )


    st.dataframe(
        ticker_result,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# 売却理由
# =========================================================
st.subheader(
    "🚦 売却理由別成績"
)


if not sell_df.empty:

    reason_result = (
        sell_df
        .groupby("理由")
        .agg(

            回数=(
                "損益",
                "count"
            ),

            損益=(
                "損益",
                "sum"
            ),

            平均損益=(
                "損益",
                "mean"
            )
        )
        .reset_index()
        .sort_values(
            "損益",
            ascending=False
        )
    )


    st.dataframe(
        reason_result,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# 連続損失
# =========================================================
st.subheader(
    "🚦 連続損失ブレーキ"
)


st.metric(
    "最大連続損失",
    f"{max_consecutive_losses}回"
)


st.markdown(
    """
- 2連敗 → 購入額 **80%**
- 3連敗 → 購入額 **50%**
- 4連敗 → 購入額 **30%**
- 5連敗 → **新規BUY停止**
"""
)


# =========================================================
# 全売買記録
# =========================================================
st.subheader(
    "📋 全売買記録"
)


if not trades_df.empty:

    trade_display = (
        trades_df
        .sort_values(
            "日付",
            ascending=False
        )
    )

    st.dataframe(
        trade_display,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# CSV作成
# =========================================================
st.subheader(
    "📥 分析結果CSV"
)


# ---------------------------------------------------------
# バックテスト結果
# ---------------------------------------------------------
summary_df = pd.DataFrame({

    "項目": [

        "初期資金",

        "最終資産",

        "損益",

        "損益率",

        "決済トレード数",

        "勝率",

        "Profit Factor",

        "平均利益",

        "平均損失",

        "平均利益/損失",

        "最大DD",

        "最大DD率",

        "最大連続損失"
    ],

    "結果": [

        initial_cash,

        final_asset,

        profit,

        return_rate,

        trade_count,

        win_rate,

        profit_factor,

        avg_profit,

        avg_loss,

        avg_ratio,

        max_dd,

        max_dd_rate,

        max_consecutive_losses
    ]
})


# ---------------------------------------------------------
# 資産推移CSV
# ---------------------------------------------------------
equity_export = equity_df.copy()


equity_export[
    "最高資産"
] = running_max.values


equity_export[
    "ドローダウン"
] = drawdown.values


equity_export[
    "ドローダウン率"
] = drawdown_rate.values


# ---------------------------------------------------------
# AI分析CSV
# ---------------------------------------------------------
analysis_export = (
    analysis_df.copy()
)


# ---------------------------------------------------------
# CSV関数
# ---------------------------------------------------------
def csv_bytes(df):

    return df.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )


# =========================================================
# 個別CSV
# =========================================================
col1, col2 = st.columns(2)


with col1:

    st.download_button(
        "📊 全AI判定履歴CSV",
        data=csv_bytes(
            analysis_export
        ),
        file_name=(
            "ver4_7_AI_all_analysis.csv"
        ),
        mime="text/csv"
    )


with col2:

    st.download_button(
        "📋 全売買記録CSV",
        data=csv_bytes(
            trades_df
        ),
        file_name=(
            "ver4_7_trade_history.csv"
        ),
        mime="text/csv"
    )


col1, col2 = st.columns(2)


with col1:

    st.download_button(
        "📈 資産推移CSV",
        data=csv_bytes(
            equity_export
        ),
        file_name=(
            "ver4_7_equity_curve.csv"
        ),
        mime="text/csv"
    )


with col2:

    st.download_button(
        "🌏 市場環境履歴CSV",
        data=csv_bytes(
            market_history_df
        ),
        file_name=(
            "ver4_7_market_history.csv"
        ),
        mime="text/csv"
    )


st.download_button(
    "📊 バックテスト結果CSV",
    data=csv_bytes(
        summary_df
    ),
    file_name=(
        "ver4_7_summary.csv"
    ),
    mime="text/csv"
)


# =========================================================
# ZIP
# =========================================================
zip_buffer = BytesIO()


with ZipFile(
    zip_buffer,
    "w"
) as zip_file:

    zip_file.writestr(
        "ver4_7_AI_all_analysis.csv",
        csv_bytes(
            analysis_export
        )
    )

    zip_file.writestr(
        "ver4_7_trade_history.csv",
        csv_bytes(
            trades_df
        )
    )

    zip_file.writestr(
        "ver4_7_equity_curve.csv",
        csv_bytes(
            equity_export
        )
    )

    zip_file.writestr(
        "ver4_7_market_history.csv",
        csv_bytes(
            market_history_df
        )
    )

    zip_file.writestr(
        "ver4_7_summary.csv",
        csv_bytes(
            summary_df
        )
    )


st.download_button(
    "📦 全分析結果をZIPで一括ダウンロード",
    data=zip_buffer.getvalue(),
    file_name=(
        "ver4_7_all_results.zip"
    ),
    mime="application/zip"
)


# =========================================================
# 詳細診断
# =========================================================
if diagnostic_mode:

    st.subheader(
        "🔎 詳細診断"
    )

    latest_analysis = (
        analysis_df
        .sort_values(
            "日付"
        )
        .groupby(
            "コード"
        )
        .tail(1)
    )


    st.dataframe(
        latest_analysis,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# 売買思想
# =========================================================
st.subheader(
    "🧠 Ver.4.7 売買思想"
)


st.markdown(
    """
### 🎯 目的

**「良い銘柄を選び、悪いBUYを減らし、利益を伸ばす」**

### 🟢 AI BUYスコア

| 条件 | 点数 |
|---|---:|
| 25日線 > 75日線 | 20 |
| 株価 > 200日線 | 20 |
| 株価 > 25日線 | 15 |
| 出来高条件 | 15 |
| RSI適正 | 15 |
| 25日線上向き | 10 |
| 75日線上向き | 5 |
| **合計** | **100** |

### 🏆 判定

- 90点以上 → 🔥 強BUY
- 85～89点 → 🟢 BUY強
- 75～84点 → 🟢 BUY
- 75点未満 → ⚪ 見送り

### ❌ 完全に使用しない条件

- 明けの明星
- 株価2,000円以上

### 🚦 連続損失ブレーキ

- 2連敗 → 80%
- 3連敗 → 50%
- 4連敗 → 30%
- 5連敗 → BUY停止

### 📊 CSV

Ver.4.7では、

**AI判定 → BUY → SELL → 損益 → 資産推移 → 市場環境**

を後から検証できるようにしています。
"""
)


st.success(
    "🚀 Ver.4.7 バックテスト完了"
)
