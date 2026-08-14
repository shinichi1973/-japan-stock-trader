# ============================================================
# 📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.1
# ============================================================
# Ver.5.1 改良点
#
# ・未来情報（ルックアヘッド・バイアス）をBUY判定から排除
# ・過去のトレード成績だけで銘柄別AI信頼度を計算
# ・連続損失ブレーキを厳密に管理
#   2連敗 → 80%
#   3連敗 → 50%
#   4連敗 → 新規BUY停止
# ・売買代金TOP50は流動性の参考指標
# ・現在取得できる約定TOP50は「現在の参考情報」として表示
# ・BUY時点の情報をCSVへ完全記録
# ・銘柄名表示
# ・市場環境フィルター
# ・全処理結果CSV
# ・ZIP一括出力
#
# ❌ 明けの明星
# ❌ 株価2,000円以上
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
    page_title="日本株 AI投資アシスタント Ver.5.1",
    page_icon="📈",
    layout="wide"
)

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.5.1"
)

st.caption(
    "未来情報排除｜AI BUYランキング｜市場環境｜"
    "連続損失ブレーキ｜銘柄別AI信頼度"
)

st.info(
    "Ver.5.1では「明けの明星」と「株価2,000円以上」を "
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

take_profit = st.sidebar.slider(
    "利確（%）",
    8.0,
    30.0,
    15.0,
    1.0
)

profit_start = st.sidebar.slider(
    "トレーリング開始（%）",
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
    "バックテスト期間",
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

use_liquidity = st.sidebar.checkbox(
    "売買代金TOP50を優先指標にする",
    value=True
)

diagnostic = st.sidebar.checkbox(
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

    for x in text.replace(
        "\n", ","
    ).split(","):

        x = x.strip()

        if not x:
            continue

        if not x.endswith(".T"):
            x += ".T"

        result.append(x)

    return list(
        dict.fromkeys(result)
    )


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


def get_stock_name(ticker):

    code = ticker.replace(
        ".T", ""
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

    end = datetime.now()

    start = (
        end
        -
        timedelta(
            days=365 * years + 300
        )
    )

    try:

        df = yf.download(
            ticker,
            start=start,
            end=end + timedelta(days=1),
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

        cols = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        if not all(
            c in df.columns
            for c in cols
        ):
            return pd.DataFrame()

        df = df[cols].copy()

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

        df["VOL20"] = (
            df["Volume"]
            .rolling(20)
            .mean()
        )

        df["Turnover"] = (
            df["Close"]
            *
            df["Volume"]
        )

        # RSI
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
# データ取得
# ============================================================

st.subheader(
    "📥 データ取得"
)

data = {}

progress = st.progress(0)

status = st.empty()

for i, ticker in enumerate(
    tickers
):

    status.write(
        f"🔄 {i+1}/{len(tickers)} "
        f"{ticker} を取得中..."
    )

    df = download_stock_data(
        ticker,
        lookback_years
    )

    if not df.empty:
        data[ticker] = df

    progress.progress(
        int(
            (i + 1)
            /
            len(tickers)
            * 100
        )
    )

progress.empty()
status.empty()

st.success(
    f"{len(data)}銘柄のデータを取得しました。"
)

if not data:
    st.error(
        "株価データを取得できませんでした。"
    )
    st.stop()


# ============================================================
# 過去5年売買代金ランキング
# ============================================================

liquidity_rows = []

for ticker, df in data.items():

    liquidity_rows.append({

        "コード":
            ticker.replace(
                ".T", ""
            ),

        "銘柄名":
            get_stock_name(
                ticker
            ),

        "平均売買代金":
            df["Turnover"].mean(),

        "平均出来高":
            df["Volume"].mean()
    })


liquidity_df = pd.DataFrame(
    liquidity_rows
)

liquidity_df = (
    liquidity_df
    .sort_values(
        "平均売買代金",
        ascending=False
    )
    .reset_index(drop=True)
)

liquidity_df.insert(
    0,
    "売買代金順位",
    liquidity_df.index + 1
)

liquidity_df[
    "売買代金TOP50"
] = (
    liquidity_df[
        "売買代金順位"
    ]
    <= 50
)


st.subheader(
    "💰 過去5年 平均売買代金TOP50"
)

st.dataframe(
    liquidity_df,
    use_container_width=True,
    hide_index=True
)


# ============================================================
# 現在の約定TOP50
# ============================================================
#
# 重要：
# これは2026年現在のランキング。
# 過去のBUY判定には使用しない。
# ============================================================

tick_df = pd.DataFrame()

try:

    import requests
    from bs4 import BeautifulSoup
    import re

    url = (
        "https://finance.matsui.co.jp/"
        "ranking-tick/index"
    )

    response = requests.get(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        },
        timeout=10
    )

    if response.status_code == 200:

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        rows = []

        for table in soup.find_all(
            "table"
        ):

            for tr in table.find_all(
                "tr"
            ):

                text = tr.get_text(
                    " ",
                    strip=True
                )

                match = re.search(
                    r"\b([0-9]{4})\b",
                    text
                )

                if match:

                    rows.append({

                        "コード":
                            match.group(1),

                        "情報":
                            text
                    })

        if rows:

            tick_df = (
                pd.DataFrame(rows)
                .drop_duplicates(
                    "コード"
                )
                .head(50)
                .reset_index(
                    drop=True
                )
            )

            tick_df.insert(
                0,
                "約定順位",
                tick_df.index + 1
            )

            tick_df[
                "約定TOP50"
            ] = True

            tick_df[
                "取得日"
            ] = datetime.now().strftime(
                "%Y-%m-%d"
            )

except Exception:

    tick_df = pd.DataFrame()


st.subheader(
    "🔄 現在のネット約定TOP50"
)

if tick_df.empty:

    st.warning(
        "現在の約定ランキングを取得できませんでした。"
        "バックテストには影響しません。"
    )

else:

    st.caption(
        "⚠️ 現在の約定ランキングは"
        "過去のBUY判定には使用しません。"
    )

    st.dataframe(
        tick_df,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 銘柄ユニバース
# ============================================================

liquidity_codes = set(
    liquidity_df[
        liquidity_df[
            "売買代金TOP50"
        ]
    ]["コード"]
    .astype(str)
)

current_tick_codes = set()

if not tick_df.empty:

    current_tick_codes = set(
        tick_df["コード"]
        .astype(str)
    )


universe = []

for ticker in data:

    code = ticker.replace(
        ".T", ""
    )

    in_liquidity = (
        code in liquidity_codes
    )

    in_current_tick = (
        code in current_tick_codes
    )

    universe.append({

        "コード":
            code,

        "銘柄名":
            get_stock_name(
                ticker
            ),

        "売買代金TOP50":
            in_liquidity,

        "現在約定TOP50":
            in_current_tick,

        "流動性優先":
            (
                in_liquidity
                if use_liquidity
                else True
            )
    })


universe_df = pd.DataFrame(
    universe
)


st.subheader(
    "🏆 Ver.5.1 銘柄ユニバース"
)

st.dataframe(
    universe_df,
    use_container_width=True,
    hide_index=True
)


# ============================================================
# 日経225
# ============================================================

@st.cache_data(ttl=3600)
def download_market(
    years
):

    end = datetime.now()

    start = (
        end
        -
        timedelta(
            days=365 * years + 300
        )
    )

    try:

        df = yf.download(
            "^N225",
            start=start,
            end=end + timedelta(days=1),
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


market_df = download_market(
    lookback_years
)


# ============================================================
# 市場判定
# ============================================================

def market_state(
    current_date
):

    if market_df.empty:

        return {
            "判定": "⚪ データなし",
            "係数": 1.0
        }

    available = market_df[
        market_df.index <= current_date
    ]

    if available.empty:

        return {
            "判定": "⚪ データなし",
            "係数": 1.0
        }

    r = available.iloc[-1]

    points = 0

    points += int(
        r["Close"] > r["MA25"]
    )

    points += int(
        r["MA25"] > r["MA75"]
    )

    points += int(
        r["MA75"] > r["MA200"]
    )

    points += int(
        r["MA25_Slope"] > 0
    )

    if points == 4:

        return {
            "判定": "🟢 強気",
            "係数": 1.00
        }

    if points == 3:

        return {
            "判定": "🟡 やや強気",
            "係数": 0.85
        }

    if points == 2:

        return {
            "判定": "⚪ 中立",
            "係数": 0.60
        }

    if points == 1:

        return {
            "判定": "🟠 やや弱気",
            "係数": 0.35
        }

    return {
        "判定": "🔴 弱気",
        "係数": 0.00
    }


# ============================================================
# AIスコア
# ============================================================

def ai_score(row):

    score = 0

    score += int(
        row["MA25"]
        >
        row["MA75"]
    ) * 20

    score += int(
        row["Close"]
        >
        row["MA200"]
    ) * 20

    score += int(
        row["Close"]
        >
        row["MA25"]
    ) * 15

    score += int(
        row["Volume"]
        >
        row["VOL20"]
    ) * 15

    score += int(
        rsi_low
        <=
        row["RSI"]
        <=
        rsi_high
    ) * 15

    score += int(
        row["MA25_Slope"]
        > 0
    ) * 10

    score += int(
        row["MA75_Slope"]
        > 0
    ) * 5

    return score


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
#
# ★重要
# 現在までに完了したトレードだけを使用。
# 未来の結果は絶対に使用しない。
# ============================================================

def confidence_factor(
    stat
):

    trades = stat[
        "trades"
    ]

    if trades < 10:
        return 1.00

    wins = stat[
        "wins"
    ]

    losses = stat[
        "losses"
    ]

    gross_profit = stat[
        "gross_profit"
    ]

    gross_loss = stat[
        "gross_loss"
    ]

    win_rate = (
        wins
        /
        trades
    )

    if gross_loss > 0:

        pf = (
            gross_profit
            /
            gross_loss
        )

    else:

        pf = 9.99

    # --------------------------------------------
    # 強い銘柄
    # --------------------------------------------

    if (
        win_rate >= 0.55
        and
        pf >= 1.30
    ):

        return 1.10

    if (
        win_rate >= 0.45
        and
        pf >= 1.10
    ):

        return 1.05

    # --------------------------------------------
    # 普通
    # --------------------------------------------

    if (
        win_rate >= 0.40
        and
        pf >= 0.90
    ):

        return 1.00

    # --------------------------------------------
    # 弱い
    # --------------------------------------------

    if (
        win_rate >= 0.30
        and
        pf >= 0.70
    ):

        return 0.85

    return 0.70


# ============================================================
# 連敗ブレーキ
# ============================================================

def loss_brake(
    consecutive_losses
):

    if consecutive_losses >= 4:

        return 0.00

    if consecutive_losses == 3:

        return 0.50

    if consecutive_losses == 2:

        return 0.80

    return 1.00


# ============================================================
# 営業日加算
# ============================================================

def business_days_after(
    date_value,
    days
):

    d = pd.Timestamp(
        date_value
    )

    count = 0

    while count < days:

        d += pd.Timedelta(
            days=1
        )

        if d.weekday() < 5:
            count += 1

    return d


# ============================================================
# バックテスト開始
# ============================================================

st.subheader(
    "📊 Ver.5.1 バックテスト"
)

st.write(
    "🔒 未来情報を使用せず、"
    "各BUY時点までの過去成績だけで"
    "銘柄別AI信頼度を計算します。"
)


cash = float(
    initial_cash
)

positions = {}

trades = []

analysis = []

equity = []

brake_history = []

stock_stats = {}

for ticker in data:

    stock_stats[ticker] = {

        "trades": 0,

        "wins": 0,

        "losses": 0,

        "gross_profit": 0.0,

        "gross_loss": 0.0
    }


consecutive_losses = 0

max_consecutive_losses = 0

cooldown_until = None


all_dates = sorted(
    set(
        d
        for df in data.values()
        for d in df.index
    )
)


progress = st.progress(0)

status = st.empty()

# ============================================================
# 日付ループ
# ============================================================

for date_i, current_date in enumerate(
    all_dates
):

    current_date = pd.Timestamp(
        current_date
    )

    # --------------------------------------------------------
    # 冷却期間
    # --------------------------------------------------------

    cooling = False

    if cooldown_until is not None:

        if current_date <= cooldown_until:

            cooling = True

        else:

            cooldown_until = None

            # 冷却終了後にリセット
            consecutive_losses = 0


    brake = loss_brake(
        consecutive_losses
    )

    market = market_state(
        current_date
    )


    # ========================================================
    # SELL
    # ========================================================

    for ticker in list(
        positions.keys()
    ):

        df = data[ticker]

        if current_date not in df.index:
            continue

        row = df.loc[
            current_date
        ]

        pos = positions[ticker]

        price = float(
            row["Close"]
        )

        entry = pos[
            "entry_price"
        ]

        shares = pos[
            "shares"
        ]

        pnl_pct = (
            price / entry - 1
        ) * 100

        pos[
            "highest_price"
        ] = max(
            pos["highest_price"],
            price
        )

        trail_price = (
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


        reason = None


        if pnl_pct <= -stop_loss:

            reason = "損切り"

        elif (
            pnl_pct >= profit_start
            and
            price <= trail_price
        ):

            reason = "トレーリング"

        elif pnl_pct >= take_profit:

            reason = "利確"

        elif (
            pos[
                "ma25_break_days"
            ]
            >=
            ma_break_days
        ):

            reason = (
                "25日線連続割れ"
            )


        if reason is not None:

            sell_value = (
                price * shares
            )

            cash += sell_value

            pnl = (
                price - entry
            ) * shares

            # ------------------------------------------------
            # 成績更新
            # ------------------------------------------------

            stock_stats[
                ticker
            ]["trades"] += 1

            if pnl > 0:

                stock_stats[
                    ticker
                ]["wins"] += 1

                stock_stats[
                    ticker
                ]["gross_profit"] += pnl

                # 勝ちで連敗リセット
                consecutive_losses = 0

            else:

                stock_stats[
                    ticker
                ]["losses"] += 1

                stock_stats[
                    ticker
                ]["gross_loss"] += abs(pnl)

                consecutive_losses += 1

                max_consecutive_losses = max(
                    max_consecutive_losses,
                    consecutive_losses
                )


                # ------------------------------------------------
                # 4連敗
                # ------------------------------------------------

                if consecutive_losses >= 4:

                    cooldown_until = (
                        business_days_after(
                            current_date,
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

                "BUY時信頼度":
                    pos["confidence"],

                "売買代金TOP50":
                    pos["liquidity_top50"],

                "現在約定TOP50":
                    pos["current_tick_top50"],

                "売却時連敗数":
                    consecutive_losses
            })

            del positions[ticker]


    # ========================================================
    # BUY候補
    # ========================================================

    candidates = []

    for ticker, df in data.items():

        if current_date not in df.index:
            continue

        if ticker in positions:
            continue

        if len(positions) >= max_positions:
            continue

        row = df.loc[
            current_date
        ]

        price = float(
            row["Close"]
        )

        code = ticker.replace(
            ".T",
            ""
        )

        # ----------------------------------------------------
        # 売買代金TOP50
        # ----------------------------------------------------

        in_liquidity = (
            code in liquidity_codes
        )

        # ----------------------------------------------------
        # 現在の約定TOP50
        #
        # ★BUY判定には使用しない
        # ----------------------------------------------------

        current_tick = (
            code in current_tick_codes
        )

        # ----------------------------------------------------
        # 株価2,000円以上
        # ----------------------------------------------------

        if price >= 2000:

            analysis.append({

                "日付": current_date,
                "コード": code,
                "銘柄名": get_stock_name(ticker),
                "株価": price,
                "AIスコア": ai_score(row),
                "判定": "❌ 株価2000円以上",
                "連敗数": consecutive_losses,
                "ブレーキ係数": brake,
                "未来情報使用": False
            })

            continue


        # ----------------------------------------------------
        # 流動性
        # ----------------------------------------------------

        if use_liquidity and not in_liquidity:

            analysis.append({

                "日付": current_date,
                "コード": code,
                "銘柄名": get_stock_name(ticker),
                "株価": price,
                "AIスコア": ai_score(row),
                "判定": "⚪ 売買代金TOP50外",
                "連敗数": consecutive_losses,
                "ブレーキ係数": brake,
                "未来情報使用": False
            })

            continue


        # ----------------------------------------------------
        # AIスコア
        # ----------------------------------------------------

        score = ai_score(
            row
        )

        sf = score_factor(
            score
        )

        # ----------------------------------------------------
        # 過去成績から信頼度
        # ----------------------------------------------------

        confidence = (
            confidence_factor(
                stock_stats[ticker]
            )
        )


        # ----------------------------------------------------
        # 市場係数
        # ----------------------------------------------------

        market_factor = (
            market["係数"]
        )


        # ----------------------------------------------------
        # 最終資金係数
        # ----------------------------------------------------

        final_factor = (
            sf
            *
            market_factor
            *
            brake
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


        # ----------------------------------------------------
        # 判定
        # ----------------------------------------------------

        if score < min_score:

            judgement = "⚪ スコア不足"

        elif cooling:

            judgement = "🚦 連敗冷却中"

        elif brake <= 0:

            judgement = "🛑 4連敗BUY停止"

        elif market_factor <= 0:

            judgement = "🌏 市場BUY停止"

        else:

            if score >= 90:

                judgement = "🔥 強BUY"

            elif score >= 85:

                judgement = "🟢 BUY強"

            else:

                judgement = "🟢 BUY"


        analysis.append({

            "日付":
                current_date,

            "コード":
                code,

            "銘柄名":
                get_stock_name(ticker),

            "株価":
                price,

            "売買代金TOP50":
                in_liquidity,

            "現在約定TOP50":
                current_tick,

            "AIスコア":
                score,

            "スコア資金係数":
                sf,

            "市場判定":
                market["判定"],

            "市場係数":
                market_factor,

            "過去トレード数":
                stock_stats[
                    ticker
                ]["trades"],

            "過去勝率":
                (
                    stock_stats[
                        ticker
                    ]["wins"]
                    /
                    stock_stats[
                        ticker
                    ]["trades"]
                    * 100
                    if stock_stats[
                        ticker
                    ]["trades"] > 0
                    else 0
                ),

            "銘柄AI信頼度":
                confidence,

            "連敗数":
                consecutive_losses,

            "ブレーキ係数":
                brake,

            "最終資金係数":
                final_factor,

            "購入可能額":
                budget,

            "判定":
                judgement,

            "未来情報使用":
                False
        })


        # ----------------------------------------------------
        # BUY候補
        # ----------------------------------------------------

        if (

            score >= min_score

            and

            not cooling

            and

            brake > 0

            and

            market_factor > 0

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
                    get_stock_name(
                        ticker
                    ),

                "liquidity_top50":
                    in_liquidity,

                "current_tick_top50":
                    current_tick
            })


    # ========================================================
    # BUYランキング
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["confidence"],
            x["liquidity_top50"]
        ),
        reverse=True
    )


    # ========================================================
    # BUY実行
    # ========================================================

    for c in candidates:

        if len(positions) >= max_positions:
            break

        ticker = c[
            "ticker"
        ]

        if ticker in positions:
            continue

        price = float(
            c["row"]["Close"]
        )

        budget = min(
            c["budget"],
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

        positions[ticker] = {

            "entry_price":
                price,

            "shares":
                shares,

            "highest_price":
                price,

            "score":
                c["score"],

            "confidence":
                c["confidence"],

            "name":
                c["name"],

            "liquidity_top50":
                c["liquidity_top50"],

            "current_tick_top50":
                c["current_tick_top50"],

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
                c["name"],

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
                c["score"],

            "BUY時信頼度":
                c["confidence"],

            "売買代金TOP50":
                c["liquidity_top50"],

            "現在約定TOP50":
                c["current_tick_top50"],

            "売却時連敗数":
                consecutive_losses
        })


    # ========================================================
    # 資産
    # ========================================================

    holdings = 0

    for ticker, pos in positions.items():

        df = data[ticker]

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

    equity.append({

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

        "連敗数":
            consecutive_losses,

        "ブレーキ係数":
            brake,

        "冷却中":
            cooling,

        "市場判定":
            market["判定"]
    })


    brake_history.append({

        "日付":
            current_date,

        "連敗数":
            consecutive_losses,

        "ブレーキ係数":
            brake,

        "冷却中":
            cooling,

        "冷却終了予定":
            cooldown_until,

        "市場判定":
            market["判定"]
    })


    # --------------------------------------------------------
    # 進捗
    # --------------------------------------------------------

    if (
        date_i % 100 == 0
        or
        date_i == len(all_dates) - 1
    ):

        progress.progress(
            int(
                (date_i + 1)
                /
                len(all_dates)
                * 100
            )
        )

        status.write(
            f"🔄 バックテスト中 "
            f"{date_i+1}/{len(all_dates)} "
            f"| 保有 {len(positions)}銘柄 "
            f"| 連敗 {consecutive_losses}"
        )


progress.empty()
status.empty()


# ============================================================
# DataFrame
# ============================================================

trades_df = pd.DataFrame(
    trades
)

analysis_df = pd.DataFrame(
    analysis
)

equity_df = pd.DataFrame(
    equity
)

brake_df = pd.DataFrame(
    brake_history
)


if equity_df.empty:

    st.error(
        "バックテスト結果がありません。"
    )

    st.stop()


# ============================================================
# 最終結果
# ============================================================

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

equity_df[
    "最高資産"
] = (
    equity_df[
        "総資産"
    ].cummax()
)

equity_df[
    "DD"
] = (
    equity_df[
        "総資産"
    ]
    -
    equity_df[
        "最高資産"
    ]
)

equity_df[
    "DD率"
] = (
    equity_df["DD"]
    /
    equity_df["最高資産"]
    *
    100
)

max_dd = float(
    equity_df["DD"].min()
)

max_dd_rate = float(
    equity_df["DD率"].min()
)


# ============================================================
# トレード統計
# ============================================================

sell_df = trades_df[
    trades_df["売買"]
    ==
    "SELL"
].copy()


trade_count = len(
    sell_df
)

if trade_count:

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

    profit_factor = (
        gross_profit
        /
        gross_loss
        if gross_loss > 0
        else np.inf
    )

    avg_profit = (
        wins["損益"].mean()
        if len(wins)
        else 0
    )

    avg_loss = (
        abs(
            losses["損益"].mean()
        )
        if len(losses)
        else 0
    )

else:

    win_rate = 0
    profit_factor = 0
    avg_profit = 0
    avg_loss = 0


avg_ratio = (
    avg_profit
    /
    avg_loss
    if avg_loss > 0
    else 0
)


# ============================================================
# 結果表示
# ============================================================

st.subheader(
    "📊 Ver.5.1 バックテスト結果"
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

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "決済トレード数",
    trade_count
)

c2.metric(
    "勝率",
    f"{win_rate:.2f}%"
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

c4.metric(
    "平均利益/損失",
    f"{avg_ratio:.2f}倍"
)

c1, c2 = st.columns(2)

c1.metric(
    "最大DD額",
    f"¥{max_dd:,.0f}"
)

c2.metric(
    "最大DD率",
    f"{max_dd_rate:.2f}%"
)

st.metric(
    "最大連続損失",
    f"{max_consecutive_losses}回"
)


# ============================================================
# 市場環境
# ============================================================

st.subheader(
    "🌏 現在の市場環境"
)

if not market_df.empty:

    latest = market_df.iloc[-1]

    market_now = market_state(
        market_df.index[-1]
    )

    c1, c2, c3, c4 = st.columns(4)

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

    st.write(
        f"市場判定："
        f"**{market_now['判定']}**"
    )


# ============================================================
# 資産推移
# ============================================================

st.subheader(
    "📈 資産推移"
)

chart = equity_df.copy()

chart["日付"] = pd.to_datetime(
    chart["日付"]
)

chart = chart.set_index(
    "日付"
)

st.line_chart(
    chart["総資産"]
)


# ============================================================
# DD
# ============================================================

st.subheader(
    "📉 ドローダウン"
)

dd_chart = equity_df.copy()

dd_chart["日付"] = pd.to_datetime(
    dd_chart["日付"]
)

dd_chart = dd_chart.set_index(
    "日付"
)

st.area_chart(
    dd_chart["DD"]
)


# ============================================================
# 連敗ブレーキ
# ============================================================

st.subheader(
    "🚦 連続損失ブレーキ"
)

st.write(
    f"最大連続損失："
    f"**{max_consecutive_losses}回**"
)

st.dataframe(
    brake_df.tail(100),
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

    stock_result[
        "勝率"
    ] = (
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
# BUYランキング
# ============================================================

st.subheader(
    "🏆 AI BUYランキング"
)

buy_analysis = analysis_df[
    analysis_df["AIスコア"]
    >= min_score
].copy()

if not buy_analysis.empty:

    latest_date = (
        buy_analysis["日付"].max()
    )

    latest_buy = (
        buy_analysis[
            buy_analysis["日付"]
            ==
            latest_date
        ]
        .sort_values(
            "AIスコア",
            ascending=False
        )
        .head(20)
    )

    st.dataframe(
        latest_buy,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 全売買記録
# ============================================================

st.subheader(
    "📋 全売買記録"
)

st.dataframe(
    trades_df.sort_values(
        "日付",
        ascending=False
    ),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# サマリー
# ============================================================

summary_df = pd.DataFrame({

    "項目": [

        "Ver",

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

        "最大連続損失",

        "未来情報使用"
    ],

    "結果": [

        "5.1",

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

        max_consecutive_losses,

        False
    ]
})


# ============================================================
# CSV
# ============================================================

def csv_bytes(df):

    if df is None:
        df = pd.DataFrame()

    return df.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )


st.subheader(
    "📥 全処理結果CSV"
)

st.download_button(
    "📊 サマリーCSV",
    csv_bytes(summary_df),
    "ver5_1_summary.csv",
    "text/csv"
)

st.download_button(
    "🏆 銘柄ユニバースCSV",
    csv_bytes(universe_df),
    "ver5_1_stock_universe.csv",
    "text/csv"
)

st.download_button(
    "💰 売買代金TOP50 CSV",
    csv_bytes(liquidity_df),
    "ver5_1_liquidity_top50.csv",
    "text/csv"
)

st.download_button(
    "🔄 現在約定TOP50 CSV",
    csv_bytes(tick_df),
    "ver5_1_current_tick_top50.csv",
    "text/csv"
)

st.download_button(
    "🧠 全AI判定CSV",
    csv_bytes(analysis_df),
    "ver5_1_all_ai_analysis.csv",
    "text/csv"
)

st.download_button(
    "📋 全売買記録CSV",
    csv_bytes(trades_df),
    "ver5_1_trade_history.csv",
    "text/csv"
)

st.download_button(
    "📈 資産推移CSV",
    csv_bytes(equity_df),
    "ver5_1_equity_curve.csv",
    "text/csv"
)

st.download_button(
    "🚦 連敗ブレーキCSV",
    csv_bytes(brake_df),
    "ver5_1_loss_brake.csv",
    "text/csv"
)

st.download_button(
    "🏢 銘柄別成績CSV",
    csv_bytes(stock_result),
    "ver5_1_stock_results.csv",
    "text/csv"
)


# ============================================================
# ZIP
# ============================================================

zip_buffer = BytesIO()

with ZipFile(
    zip_buffer,
    "w"
) as z:

    csv_files = {

        "summary.csv":
            summary_df,

        "stock_universe.csv":
            universe_df,

        "liquidity_top50.csv":
            liquidity_df,

        "current_tick_top50.csv":
            tick_df,

        "all_ai_analysis.csv":
            analysis_df,

        "trade_history.csv":
            trades_df,

        "equity_curve.csv":
            equity_df,

        "loss_brake.csv":
            brake_df,

        "stock_results.csv":
            stock_result
    }

    for filename, df in csv_files.items():

        z.writestr(
            filename,
            csv_bytes(df)
        )


st.download_button(
    "📦 全CSVをZIPで一括ダウンロード",
    zip_buffer.getvalue(),
    "ver5_1_all_results.zip",
    "application/zip"
)


# ============================================================
# 詳細診断
# ============================================================

if diagnostic:

    st.subheader(
        "🔎 詳細診断"
    )

    st.write(
        "BUY判定に使用したデータは、"
        "その日までに利用可能だった情報のみです。"
    )

    st.dataframe(
        analysis_df.tail(200),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# 売買思想
# ============================================================

st.subheader(
    "🧠 Ver.5.1 売買思想"
)

st.markdown(
    """
### 🎯 目的

**「未来を見ずに、良いBUYだけを残す」**

### 🟢 AIスコア

- 25日線 > 75日線 → 20点
- 株価 > 200日線 → 20点
- 株価 > 25日線 → 15点
- 出来高条件 → 15点
- RSI適正 → 15点
- 25日線上向き → 10点
- 75日線上向き → 5点

**合計100点**

### 🚦 連敗ブレーキ

- 2連敗 → 80%
- 3連敗 → 50%
- 4連敗 → 新規BUY停止
- 冷却期間 → 設定営業日

### 🧠 銘柄別AI信頼度

BUYする時点より**後のトレード結果は使用しません。**

その時点までに終了しているトレードだけから、

- 勝率
- Profit Factor
- トレード数

を計算します。

### ❌ 使用しない条件

- 明けの明星
- 株価2,000円以上

### 🔒 未来情報

現在の約定TOP50は表示しますが、

**過去のバックテストBUY判定には使用しません。**

これにより、Ver.5.0よりバックテストの公平性を高めています。
"""
)

st.success(
    "🚀 Ver.5.1 バックテスト完了"
)
