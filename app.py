# ============================================================
# 📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.0
# ============================================================
# ・過去5年 平均売買代金TOP50
# ・ネット上で取得可能な約定回数ランキング
# ・AI BUYランキング
# ・市場環境フィルター
# ・連続損失ブレーキ
# ・25日線2営業日割れ売却
# ・銘柄別AI信頼度
# ・全処理CSV
# ・ZIP一括出力
#
# ❌ 明けの明星は使用しない
# ❌ 株価2,000円以上は選定対象外
# ============================================================

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile


# ============================================================
# ページ設定
# ============================================================

st.set_page_config(
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.5.0",
    page_icon="📈",
    layout="wide"
)

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.0"
)

st.caption(
    "流動性TOP50｜約定TOP50｜AI BUYランキング｜"
    "市場環境｜連続損失ブレーキ｜銘柄別AI信頼度"
)

st.info(
    "Ver.5.0では「明けの明星」と「株価2,000円以上」を "
    "BUY選定条件から完全に除外しています。"
)


# ============================================================
# サイドバー
# ============================================================

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
    value=10
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

cooldown_days = st.sidebar.number_input(
    "4連敗後の冷却期間",
    min_value=5,
    max_value=30,
    value=10
)

ma_break_days = st.sidebar.number_input(
    "25日線割れ確認日数",
    min_value=2,
    max_value=5,
    value=2
)

slope_grace = st.sidebar.checkbox(
    "25日線上向きなら1日猶予",
    value=True
)

use_liquidity_top50 = st.sidebar.checkbox(
    "平均売買代金TOP50を使用",
    value=True
)

use_tick_top50 = st.sidebar.checkbox(
    "ネット約定TOP50を使用",
    value=True
)

diagnostic_mode = st.sidebar.checkbox(
    "🔎 詳細診断",
    value=False
)

ticker_input = st.sidebar.text_area(
    "分析対象銘柄コード",
    value=(
        "7203,6758,9984,8306,9432,"
        "6501,8035,8058,7267,2914,"
        "9433,8316,8411,6098,4063,"
        "4519,6367,6857,7974,8766,"
        "5401,8801,8802,4502,4503,"
        "4523,4755,6594,7741,6981"
    )
)


# ============================================================
# 銘柄コード
# ============================================================

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


# ============================================================
# 銘柄名
# ============================================================

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


@st.cache_data(ttl=86400)
def get_stock_name(ticker):

    code = ticker.replace(".T", "")

    if code in STOCK_NAMES:
        return STOCK_NAMES[code]

    try:

        info = yf.Ticker(ticker).info

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


# ============================================================
# 株価データ
# ============================================================

@st.cache_data(ttl=3600)
def download_stock_data(
    ticker,
    years
):

    end_date = datetime.now()

    start_date = (
        end_date
        -
        timedelta(
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

        df = df[required].copy()

        # ----------------------------------------------------
        # MA
        # ----------------------------------------------------

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

        df["MA25_Slope"] = (
            df["MA25"]
            -
            df["MA25"].shift(5)
        )

        df["MA75_Slope"] = (
            df["MA75"]
            -
            df["MA75"].shift(5)
        )

        # ----------------------------------------------------
        # 出来高
        # ----------------------------------------------------

        df["VOL20"] = (
            df["Volume"]
            .rolling(20)
            .mean()
        )

        # ----------------------------------------------------
        # 売買代金
        # ----------------------------------------------------

        df["Turnover"] = (
            df["Close"]
            *
            df["Volume"]
        )

        df["Turnover_MA20"] = (
            df["Turnover"]
            .rolling(20)
            .mean()
        )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        delta = df["Close"].diff()

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
            /
            avg_loss.replace(
                0,
                np.nan
            )
        )

        df["RSI"] = (
            100
            -
            (
                100
                /
                (1 + rs)
            )
        )

        return df.dropna()

    except Exception:

        return pd.DataFrame()


# ============================================================
# 過去5年 平均売買代金ランキング
# ============================================================

def calculate_liquidity_top50(
    data_dict
):

    records = []

    for ticker, df in data_dict.items():

        if df.empty:
            continue

        # 全期間平均
        avg_turnover = (
            df["Turnover"]
            .mean()
        )

        median_turnover = (
            df["Turnover"]
            .median()
        )

        avg_volume = (
            df["Volume"]
            .mean()
        )

        records.append({

            "コード":
                ticker.replace(".T", ""),

            "銘柄名":
                get_stock_name(ticker),

            "平均売買代金":
                avg_turnover,

            "中央値売買代金":
                median_turnover,

            "平均出来高":
                avg_volume
        })

    result = pd.DataFrame(
        records
    )

    if result.empty:
        return result

    result = (
        result
        .sort_values(
            "平均売買代金",
            ascending=False
        )
        .reset_index(drop=True)
    )

    result.insert(
        0,
        "売買代金順位",
        result.index + 1
    )

    result["売買代金TOP50"] = (
        result["売買代金順位"] <= 50
    )

    return result


# ============================================================
# ネット約定TOP50
# ============================================================

def get_web_tick_top50():

    """
    ネット上で確認可能な約定回数ランキング。

    外部サイトのHTML構造変更などで取得できない場合でも
    アプリ全体を停止させない。
    """

    try:

        import requests
        from bs4 import BeautifulSoup

        url = (
            "https://finance.matsui.co.jp/"
            "ranking-tick/index"
        )

        headers = {
            "User-Agent":
                "Mozilla/5.0"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return pd.DataFrame()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        rows = []

        # ----------------------------------------------------
        # ページ内のテーブルを探索
        # ----------------------------------------------------

        tables = soup.find_all(
            "table"
        )

        for table in tables:

            trs = table.find_all(
                "tr"
            )

            for tr in trs:

                cells = [
                    c.get_text(
                        " ",
                        strip=True
                    )
                    for c in tr.find_all(
                        ["td", "th"]
                    )
                ]

                if not cells:
                    continue

                text = " ".join(
                    cells
                )

                # 4桁銘柄コード探索
                import re

                match = re.search(
                    r"\b([0-9]{4})\b",
                    text
                )

                if not match:
                    continue

                code = match.group(1)

                rows.append({

                    "コード":
                        code,

                    "ネット約定情報":
                        text
                })

        if not rows:
            return pd.DataFrame()

        result = pd.DataFrame(
            rows
        )

        result = (
            result
            .drop_duplicates(
                "コード"
            )
            .head(50)
            .reset_index(drop=True)
        )

        result.insert(
            0,
            "約定順位",
            result.index + 1
        )

        result["約定TOP50"] = True

        result[
            "約定ランキング取得日"
        ] = datetime.now().strftime(
            "%Y-%m-%d"
        )

        result[
            "約定ランキング取得元"
        ] = "松井証券 Tick回数ランキング"

        return result

    except Exception:

        return pd.DataFrame()


# ============================================================
# 市場データ
# ============================================================

@st.cache_data(ttl=3600)
def download_market_data(
    years
):

    end_date = datetime.now()

    start_date = (
        end_date
        -
        timedelta(
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

        close = df["Close"]

        result = pd.DataFrame(
            index=close.index
        )

        result["Close"] = close

        result["MA25"] = (
            close
            .rolling(25)
            .mean()
        )

        result["MA75"] = (
            close
            .rolling(75)
            .mean()
        )

        result["MA200"] = (
            close
            .rolling(200)
            .mean()
        )

        result["MA25_Slope"] = (
            result["MA25"]
            -
            result["MA25"].shift(5)
        )

        return result.dropna()

    except Exception:

        return pd.DataFrame()


# ============================================================
# 市場判定
# ============================================================

def get_market_condition(
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
            "傾き": np.nan
        }

    available = market_df[
        market_df.index <= current_date
    ]

    if available.empty:

        return {
            "判定": "⚪ データなし",
            "係数": 1.0,
            "価格": np.nan,
            "MA25": np.nan,
            "MA75": np.nan,
            "MA200": np.nan,
            "傾き": np.nan
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

    points += (
        price > ma25
    )

    points += (
        ma25 > ma75
    )

    points += (
        ma75 > ma200
    )

    points += (
        slope > 0
    )

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

        "判定":
            judgement,

        "係数":
            factor,

        "価格":
            price,

        "MA25":
            ma25,

        "MA75":
            ma75,

        "MA200":
            ma200,

        "傾き":
            slope
    }


# ============================================================
# AIスコア
# ============================================================

def calculate_score(
    row
):

    score = 0
    details = {}

    details[
        "25日線>75日線"
    ] = (
        20
        if row["MA25"] > row["MA75"]
        else 0
    )

    details[
        "株価>200日線"
    ] = (
        20
        if row["Close"] > row["MA200"]
        else 0
    )

    details[
        "株価>25日線"
    ] = (
        15
        if row["Close"] > row["MA25"]
        else 0
    )

    details[
        "出来高"
    ] = (
        15
        if row["Volume"] > row["VOL20"]
        else 0
    )

    details[
        "RSI適正"
    ] = (
        15
        if rsi_low <= row["RSI"] <= rsi_high
        else 0
    )

    details[
        "25日線上向き"
    ] = (
        10
        if row["MA25_Slope"] > 0
        else 0
    )

    details[
        "75日線上向き"
    ] = (
        5
        if row["MA75_Slope"] > 0
        else 0
    )

    score = sum(
        details.values()
    )

    return score, details


# ============================================================
# AI判定
# ============================================================

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


# ============================================================
# 銘柄別AI信頼度
# ============================================================

def stock_confidence(
    stats
):

    trades = stats.get(
        "trades",
        0
    )

    wins = stats.get(
        "wins",
        0
    )

    if trades < 20:
        return 1.00

    win_rate = (
        wins
        /
        trades
    )

    if win_rate >= 0.65:
        return 1.10

    if win_rate >= 0.55:
        return 1.05

    if win_rate >= 0.45:
        return 1.00

    if win_rate >= 0.35:
        return 0.85

    return 0.70


# ============================================================
# 連敗係数
# ============================================================

def loss_factor(
    losses
):

    if losses >= 4:
        return 0.00

    if losses == 3:
        return 0.50

    if losses == 2:
        return 0.80

    return 1.00


# ============================================================
# 営業日加算
# ============================================================

def add_business_days(
    date_value,
    days
):

    current = pd.Timestamp(
        date_value
    )

    count = 0

    while count < days:

        current += pd.Timedelta(
            days=1
        )

        if current.weekday() < 5:
            count += 1

    return current


# ============================================================
# データ取得
# ============================================================

st.subheader(
    "📥 データ取得"
)

data_dict = {}

progress = st.progress(0)

for i, ticker in enumerate(tickers):

    df = download_stock_data(
        ticker,
        lookback_years
    )

    if not df.empty:

        data_dict[ticker] = df

    progress.progress(
        int(
            (i + 1)
            /
            len(tickers)
            *
            100
        )
    )

progress.empty()

st.success(
    f"{len(data_dict)}銘柄のデータを取得しました。"
)

if not data_dict:

    st.error(
        "株価データを取得できませんでした。"
    )

    st.stop()


# ============================================================
# 流動性TOP50
# ============================================================

liquidity_df = (
    calculate_liquidity_top50(
        data_dict
    )
)

if use_liquidity_top50:

    st.subheader(
        "💰 過去5年 平均売買代金TOP50"
    )

    st.dataframe(
        liquidity_df.head(50),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 約定TOP50
# ============================================================

if use_tick_top50:

    st.subheader(
        "🔄 ネット約定回数TOP50"
    )

    tick_df = (
        get_web_tick_top50()
    )

    if tick_df.empty:

        st.warning(
            "ネット約定ランキングを取得できませんでした。"
            "この場合は約定ランキングを銘柄選定から除外して続行します。"
        )

    else:

        st.dataframe(
            tick_df,
            use_container_width=True,
            hide_index=True
        )

else:

    tick_df = pd.DataFrame()


# ============================================================
# 銘柄ユニバース
# ============================================================

liquidity_codes = set()

if not liquidity_df.empty:

    liquidity_codes = set(
        liquidity_df[
            liquidity_df[
                "売買代金TOP50"
            ]
        ]["コード"]
        .astype(str)
    )


tick_codes = set()

if not tick_df.empty:

    tick_codes = set(
        tick_df["コード"]
        .astype(str)
    )


universe_records = []

for ticker in data_dict:

    code = ticker.replace(
        ".T",
        ""
    )

    name = get_stock_name(
        ticker
    )

    in_liquidity = (
        code in liquidity_codes
    )

    in_tick = (
        code in tick_codes
    )

    both = (
        in_liquidity
        and
        in_tick
    )

    # --------------------------------------------------------
    # TOP50選定
    # --------------------------------------------------------

    if (
        use_liquidity_top50
        or
        use_tick_top50
    ):

        selected = (
            in_liquidity
            or
            in_tick
        )

    else:

        selected = True

    if both:

        priority = 3

    elif in_liquidity or in_tick:

        priority = 2

    else:

        priority = 0

    universe_records.append({

        "コード":
            code,

        "銘柄名":
            name,

        "売買代金TOP50":
            in_liquidity,

        "約定TOP50":
            in_tick,

        "両方TOP50":
            both,

        "優先度":
            priority,

        "選定対象":
            selected
    })


universe_df = pd.DataFrame(
    universe_records
)

st.subheader(
    "🏆 Ver.5.0 銘柄ユニバース"
)

st.dataframe(
    universe_df
    .sort_values(
        [
            "選定対象",
            "優先度"
        ],
        ascending=False
    ),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# 市場データ
# ============================================================

market_df = download_market_data(
    lookback_years
)


# ============================================================
# バックテスト
# ============================================================

st.subheader(
    "📊 Ver.5.0 バックテスト"
)

cash = float(
    initial_cash
)

positions = {}

trades = []

analysis_records = []

equity_records = []

market_records = []

stats = {}

for ticker in data_dict:

    stats[ticker] = {

        "trades": 0,

        "wins": 0,

        "profit": 0.0
    }


consecutive_losses = 0

max_consecutive_losses = 0

cooldown_until = None


all_dates = sorted(
    set(
        d
        for df in data_dict.values()
        for d in df.index
    )
)


# ============================================================
# 日付ループ
# ============================================================

for current_date in all_dates:

    current_ts = pd.Timestamp(
        current_date
    )

    # --------------------------------------------------------
    # 市場
    # --------------------------------------------------------

    market = get_market_condition(
        market_df,
        current_date
    )

    cooling = False

    if cooldown_until is not None:

        if (
            current_ts
            <= cooldown_until
        ):

            cooling = True

        else:

            cooldown_until = None

            consecutive_losses = 0


    current_loss_factor = (
        loss_factor(
            consecutive_losses
        )
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

        "市場判定":
            market["判定"],

        "市場係数":
            market["係数"],

        "連続損失":
            consecutive_losses,

        "冷却中":
            cooling
    })


    # ========================================================
    # SELL
    # ========================================================

    for ticker in list(
        positions.keys()
    ):

        df = data_dict[ticker]

        if current_date not in df.index:
            continue

        row = df.loc[
            current_date
        ]

        pos = positions[ticker]

        price = float(
            row["Close"]
        )

        entry_price = pos[
            "entry_price"
        ]

        shares = pos[
            "shares"
        ]

        pnl_pct = (
            price
            /
            entry_price
            -
            1
        ) * 100

        pos[
            "highest_price"
        ] = max(
            pos["highest_price"],
            price
        )

        trailing_price = (
            pos["highest_price"]
            *
            (
                1
                -
                trailing_stop / 100
            )
        )

        if price < row["MA25"]:

            pos[
                "ma25_break_days"
            ] += 1

        else:

            pos[
                "ma25_break_days"
            ] = 0


        ma_break = (
            pos[
                "ma25_break_days"
            ]
        )

        reason = ""


        # ----------------------------------------------------
        # 損切り
        # ----------------------------------------------------

        if pnl_pct <= -stop_loss:

            reason = "損切り"


        # ----------------------------------------------------
        # トレーリング
        # ----------------------------------------------------

        elif (
            pnl_pct >= profit_start
            and
            price <= trailing_price
        ):

            reason = "トレーリング"


        # ----------------------------------------------------
        # 利確
        # ----------------------------------------------------

        elif pnl_pct >= take_profit:

            reason = "利確"


        # ----------------------------------------------------
        # 25日線
        # ----------------------------------------------------

        elif ma_break >= ma_break_days:

            if (
                slope_grace
                and
                row["MA25_Slope"] > 0
                and
                ma_break < (
                    ma_break_days + 1
                )
            ):

                reason = ""

            else:

                reason = (
                    f"25日線{ma_break}日連続割れ"
                )


        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        if reason:

            sell_value = (
                price
                *
                shares
            )

            cash += sell_value

            pnl = (
                price
                -
                entry_price
            ) * shares

            stats[ticker][
                "trades"
            ] += 1

            stats[ticker][
                "profit"
            ] += pnl

            if pnl > 0:

                stats[ticker][
                    "wins"
                ] += 1

                consecutive_losses = 0

            else:

                consecutive_losses += 1

                max_consecutive_losses = max(
                    max_consecutive_losses,
                    consecutive_losses
                )

                if (
                    consecutive_losses >= 4
                ):

                    cooldown_until = (
                        add_business_days(
                            current_ts,
                            cooldown_days
                        )
                    )

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
                    pnl_pct,

                "理由":
                    reason,

                "BUYスコア":
                    pos["score"],

                "売買代金TOP50":
                    pos[
                        "liquidity_top50"
                    ],

                "約定TOP50":
                    pos[
                        "tick_top50"
                    ],

                "両方TOP50":
                    pos[
                        "both_top50"
                    ],

                "銘柄信頼度":
                    pos[
                        "confidence"
                    ],

                "25日線割れ日数":
                    ma_break
            })

            del positions[ticker]


    # ========================================================
    # BUY候補
    # ========================================================

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

        universe_row = (
            universe_df[
                universe_df["コード"]
                == code
            ]
        )

        if universe_row.empty:
            continue

        u = universe_row.iloc[0]

        selected = bool(
            u["選定対象"]
        )

        in_liquidity = bool(
            u["売買代金TOP50"]
        )

        in_tick = bool(
            u["約定TOP50"]
        )

        both = bool(
            u["両方TOP50"]
        )

        price = float(
            row["Close"]
        )

        score, details = (
            calculate_score(
                row
            )
        )

        confidence = (
            stock_confidence(
                stats[ticker]
            )
        )

        sf = score_factor(
            score
        )

        final_factor = (
            sf
            *
            market["係数"]
            *
            current_loss_factor
            *
            confidence
        )

        final_factor = min(
            final_factor,
            1.0
        )

        budget = (
            min(
                max_per_position,
                cash
            )
            *
            final_factor
        )


        if not selected:

            judgement = (
                "⚪ TOP50対象外"
            )

        elif price >= 2000:

            judgement = (
                "❌ 2,000円以上"
            )

        elif score < min_score:

            judgement = (
                "⚪ AIスコア不足"
            )

        elif ticker in positions:

            judgement = (
                "📌 保有中"
            )

        elif cooling:

            judgement = (
                "🚦 冷却中"
            )

        elif market["係数"] <= 0:

            judgement = (
                "🌏 市場BUY停止"
            )

        else:

            judgement = score_judgement(
                score
            )


        analysis_records.append({

            "日付":
                current_date,

            "コード":
                code,

            "銘柄名":
                name,

            "株価":
                price,

            "売買代金TOP50":
                in_liquidity,

            "約定TOP50":
                in_tick,

            "両方TOP50":
                both,

            "AIスコア":
                score,

            "AI判定":
                judgement,

            "25日線":
                row["MA25"],

            "75日線":
                row["MA75"],

            "200日線":
                row["MA200"],

            "RSI":
                row["RSI"],

            "出来高":
                row["Volume"],

            "20日平均出来高":
                row["VOL20"],

            "売買代金":
                row["Turnover"],

            "25日線傾き":
                row["MA25_Slope"],

            "75日線傾き":
                row["MA75_Slope"],

            "市場判定":
                market["判定"],

            "市場係数":
                market["係数"],

            "連続損失":
                consecutive_losses,

            "損失ブレーキ係数":
                current_loss_factor,

            "銘柄別AI信頼度":
                confidence,

            "スコア資金係数":
                sf,

            "最終資金係数":
                final_factor,

            "購入可能額":
                budget,

            "実際購入額":
                0
        })


        # -----------------------------------------------
        # BUY候補
        # -----------------------------------------------

        if (

            selected

            and

            price < 2000

            and

            score >= min_score

            and

            ticker not in positions

            and

            len(positions)
            <
            max_positions

            and

            not cooling

            and

            market["係数"] > 0

            and

            budget >= price

        ):

            candidates.append({

                "ticker":
                    ticker,

                "row":
                    row,

                "score":
                    score,

                "budget":
                    budget,

                "confidence":
                    confidence,

                "name":
                    name,

                "liquidity_top50":
                    in_liquidity,

                "tick_top50":
                    in_tick,

                "both_top50":
                    both
            })


    # ========================================================
    # BUYランキング
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["both_top50"],
            x["score"],
            x["confidence"]
        ),
        reverse=True
    )


    # ========================================================
    # BUY実行
    # ========================================================

    for candidate in candidates:

        if (
            len(positions)
            >= max_positions
        ):
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
            budget
            /
            price
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

            "name":
                candidate[
                    "name"
                ],

            "confidence":
                candidate[
                    "confidence"
                ],

            "liquidity_top50":
                candidate[
                    "liquidity_top50"
                ],

            "tick_top50":
                candidate[
                    "tick_top50"
                ],

            "both_top50":
                candidate[
                    "both_top50"
                ],

            "ma25_break_days":
                0
        }

        trades.append({

            "日付":
                current_date,

            "コード":
                ticker.replace(
                    ".T",
                    ""
                ),

            "銘柄名":
                candidate[
                    "name"
                ],

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

            "売買代金TOP50":
                candidate[
                    "liquidity_top50"
                ],

            "約定TOP50":
                candidate[
                    "tick_top50"
                ],

            "両方TOP50":
                candidate[
                    "both_top50"
                ],

            "銘柄信頼度":
                candidate[
                    "confidence"
                ],

            "25日線割れ日数":
                0
        })


    # ========================================================
    # 資産
    # ========================================================

    holdings = 0

    for ticker, pos in positions.items():

        df = data_dict[ticker]

        if current_date in df.index:

            price = float(
                df.loc[
                    current_date
                ]["Close"]
            )

            holdings += (
                price
                *
                pos["shares"]
            )

    total_asset = (
        cash
        +
        holdings
    )

    equity_records.append({

        "日付":
            current_date,

        "現金":
            cash,

        "保有株評価額":
            holdings,

        "総資産":
            total_asset,

        "保有銘柄数":
            len(positions),

        "連続損失":
            consecutive_losses,

        "冷却中":
            cooling
    })


# ============================================================
# DataFrame
# ============================================================

trades_df = pd.DataFrame(
    trades
)

analysis_df = pd.DataFrame(
    analysis_records
)

equity_df = pd.DataFrame(
    equity_records
)

market_history_df = pd.DataFrame(
    market_records
)


# ============================================================
# 最終結果
# ============================================================

if equity_df.empty:

    st.error(
        "バックテスト結果がありません。"
    )

    st.stop()


final_asset = float(
    equity_df[
        "総資産"
    ].iloc[-1]
)

profit = (
    final_asset
    -
    initial_cash
)

return_rate = (
    profit
    /
    initial_cash
    *
    100
)


# ============================================================
# DD
# ============================================================

running_max = (
    equity_df[
        "総資産"
    ]
    .cummax()
)

drawdown = (
    equity_df[
        "総資産"
    ]
    -
    running_max
)

drawdown_rate = (
    drawdown
    /
    running_max
    *
    100
)

max_dd = float(
    drawdown.min()
)

max_dd_rate = float(
    drawdown_rate.min()
)


# ============================================================
# 売買統計
# ============================================================

sell_df = trades_df[
    trades_df["売買"]
    == "SELL"
].copy()


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
        /
        trade_count
        *
        100
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
            /
            gross_loss
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


if avg_loss > 0:

    avg_ratio = (
        avg_profit
        /
        avg_loss
    )

else:

    avg_ratio = 0


# ============================================================
# 表示
# ============================================================

st.subheader(
    "📊 Ver.5.0 バックテスト結果"
)

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


# ============================================================
# 統計
# ============================================================

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

c3.metric(
    "平均利益/損失",
    (
        f"{avg_ratio:.2f}倍"
    )
)

st.metric(
    "最大DD率",
    f"{max_dd_rate:.2f}%"
)


# ============================================================
# 資産推移
# ============================================================

st.subheader(
    "📈 資産推移"
)

chart_df = equity_df.copy()

chart_df["日付"] = pd.to_datetime(
    chart_df["日付"]
)

chart_df = chart_df.set_index(
    "日付"
)

st.line_chart(
    chart_df["総資産"]
)


# ============================================================
# DD
# ============================================================

st.subheader(
    "📉 ドローダウン"
)

dd_df = pd.DataFrame(
    {
        "ドローダウン":
            drawdown.values
    },
    index=pd.to_datetime(
        equity_df["日付"]
    )
)

st.area_chart(
    dd_df
)


# ============================================================
# TOP50別成績
# ============================================================

st.subheader(
    "🏆 TOP50別成績"
)

if not sell_df.empty:

    top50_result = (
        sell_df
        .groupby(
            [
                "売買代金TOP50",
                "約定TOP50",
                "両方TOP50"
            ]
        )
        .agg(

            決済数=(
                "損益",
                "count"
            ),

            勝ち=(
                "損益",
                lambda x:
                    (x > 0).sum()
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

    top50_result["勝率"] = (
        top50_result["勝ち"]
        /
        top50_result["決済数"]
        *
        100
    )

    st.dataframe(
        top50_result,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 銘柄別成績
# ============================================================

st.subheader(
    "🏢 銘柄別成績"
)

if not sell_df.empty:

    stock_result = (
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
                    (x > 0).sum()
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

    stock_result["勝率"] = (
        stock_result["勝ち"]
        /
        stock_result["トレード数"]
        *
        100
    )

    st.dataframe(
        stock_result.sort_values(
            "損益",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True
    )

else:

    stock_result = pd.DataFrame()


# ============================================================
# 全売買記録
# ============================================================

st.subheader(
    "📋 全売買記録"
)

if not trades_df.empty:

    st.dataframe(
        trades_df.sort_values(
            "日付",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# CSV
# ============================================================

summary_df = pd.DataFrame({

    "項目": [

        "Ver",

        "初期資金",

        "最終資産",

        "損益",

        "損益率",

        "決済数",

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

        "5.0",

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


# ============================================================
# CSV関数
# ============================================================

def csv_bytes(df):

    if df is None:
        df = pd.DataFrame()

    return df.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )


# ============================================================
# CSVダウンロード
# ============================================================

st.subheader(
    "📥 全処理結果CSV"
)

st.download_button(
    "🏆 銘柄ユニバースCSV",
    data=csv_bytes(
        universe_df
    ),
    file_name=(
        "ver5_0_stock_universe.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "💰 売買代金TOP50 CSV",
    data=csv_bytes(
        liquidity_df
    ),
    file_name=(
        "ver5_0_liquidity_top50.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "🔄 約定TOP50 CSV",
    data=csv_bytes(
        tick_df
    ),
    file_name=(
        "ver5_0_tick_top50.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "🧠 全AI判定CSV",
    data=csv_bytes(
        analysis_df
    ),
    file_name=(
        "ver5_0_all_ai_analysis.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "📋 全売買記録CSV",
    data=csv_bytes(
        trades_df
    ),
    file_name=(
        "ver5_0_trade_history.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "📈 資産推移CSV",
    data=csv_bytes(
        equity_export
    ),
    file_name=(
        "ver5_0_equity_curve.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "🌏 市場環境CSV",
    data=csv_bytes(
        market_history_df
    ),
    file_name=(
        "ver5_0_market_history.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "🏢 銘柄別成績CSV",
    data=csv_bytes(
        stock_result
    ),
    file_name=(
        "ver5_0_stock_results.csv"
    ),
    mime="text/csv"
)

st.download_button(
    "📊 バックテスト概要CSV",
    data=csv_bytes(
        summary_df
    ),
    file_name=(
        "ver5_0_summary.csv"
    ),
    mime="text/csv"
)


# ============================================================
# ZIP
# ============================================================

zip_buffer = BytesIO()

with ZipFile(
    zip_buffer,
    "w"
) as z:

    files = {

        "stock_universe.csv":
            universe_df,

        "liquidity_top50.csv":
            liquidity_df,

        "tick_top50.csv":
            tick_df,

        "all_ai_analysis.csv":
            analysis_df,

        "trade_history.csv":
            trades_df,

        "equity_curve.csv":
            equity_export,

        "market_history.csv":
            market_history_df,

        "stock_results.csv":
            stock_result,

        "summary.csv":
            summary_df
    }

    for filename, df in files.items():

        z.writestr(
            filename,
            csv_bytes(df)
        )


st.download_button(
    "📦 全分析結果をZIPで一括ダウンロード",
    data=zip_buffer.getvalue(),
    file_name=(
        "ver5_0_all_results.zip"
    ),
    mime="application/zip"
)


# ============================================================
# 詳細診断
# ============================================================

if diagnostic_mode:

    st.subheader(
        "🔎 詳細診断"
    )

    st.dataframe(
        analysis_df.tail(100),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 売買思想
# ============================================================

st.subheader(
    "🧠 Ver.5.0 売買思想"
)

st.markdown(
    """
### 🎯 基本思想

**「流動性の高い銘柄から、AIが良いBUYを選ぶ」**

### 🏆 銘柄選定

1. 過去5年間の平均売買代金TOP50
2. ネット上で取得可能な約定回数TOP50
3. 両方に入る銘柄を優先

### 🟢 AIスコア

- 25日線 > 75日線 → 20点
- 株価 > 200日線 → 20点
- 株価 > 25日線 → 15点
- 出来高 → 15点
- RSI適正 → 15点
- 25日線上向き → 10点
- 75日線上向き → 5点

### ❌ 使用しない条件

- 明けの明星
- 株価2,000円以上

### 🚦 連敗ブレーキ

- 2連敗 → 80%
- 3連敗 → 50%
- 4連敗 → BUY停止
- 冷却期間 → 10営業日

### 📉 売却

- 損切り
- 利確
- トレーリング
- 25日線連続割れ

### 🧠 銘柄別AI信頼度

20トレード未満：
**補正なし**

20トレード以上：

- 勝率65%以上 → 110%
- 勝率55%以上 → 105%
- 勝率45%以上 → 100%
- 勝率35%以上 → 85%
- 35%未満 → 70%

としてBUY資金を調整します。
"""
)

st.success(
    "🚀 Ver.5.0 バックテスト完了"
)
