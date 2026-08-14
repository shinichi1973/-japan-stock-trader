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
    page_title="日本株 10万円→100万円 AI投資アシスタント Ver.4.3",
    page_icon="📈",
    layout="wide"
)


# =========================================================
# タイトル
# =========================================================

st.title(
    "📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.3"
)

st.caption(
    "S株を想定した仮想バックテスト。"
    "銘柄名表示・AI BUYランキング・市場環境フィルターを搭載。"
    "明けの明星は使用しません。"
)


# =========================================================
# 銘柄名
# =========================================================

DEFAULT_NAMES = {

    "7203.T": "トヨタ自動車",

    "6758.T": "ソニーグループ",

    "9984.T": "ソフトバンクグループ",

    "8306.T": "三菱UFJフィナンシャル・グループ",

    "9432.T": "日本電信電話",

    "7011.T": "三菱重工業",

    "6501.T": "日立製作所",

    "6857.T": "アドバンテスト",

    "8035.T": "東京エレクトロン",

    "9983.T": "ファーストリテイリング",

    "6752.T": "パナソニック ホールディングス",

    "6861.T": "キーエンス",

    "6098.T": "リクルートホールディングス",

    "4063.T": "信越化学工業",

    "7974.T": "任天堂",

    "6367.T": "ダイキン工業",

    "6146.T": "ディスコ",

    "4519.T": "中外製薬",

    "4568.T": "第一三共",

    "8001.T": "伊藤忠商事",

    "8031.T": "三井物産",

    "8058.T": "三菱商事",

    "8316.T": "三井住友フィナンシャルグループ",

    "8411.T": "みずほフィナンシャルグループ",

    "9433.T": "KDDI",

    "9434.T": "ソフトバンク",

    "2914.T": "日本たばこ産業",

    "3382.T": "セブン＆アイ・ホールディングス",

    "7267.T": "本田技研工業",

    "7269.T": "スズキ",

    "6902.T": "デンソー",

    "6762.T": "TDK",

    "6981.T": "村田製作所",

    "6723.T": "ルネサスエレクトロニクス",

    "7735.T": "SCREENホールディングス",

    "7832.T": "バンダイナムコホールディングス",

    "4502.T": "武田薬品工業",

    "4661.T": "オリエンタルランド",

    "9020.T": "東日本旅客鉄道",

    "9021.T": "西日本旅客鉄道",

    "9022.T": "東海旅客鉄道",

    "9101.T": "日本郵船",

    "9104.T": "商船三井",

    "9107.T": "川崎汽船",

}


# =========================================================
# サイドバー
# =========================================================

st.sidebar.header(
    "⚙️ Ver.4.3 バックテスト設定"
)


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
    value=10,
    step=1
)


max_per_position = st.sidebar.number_input(
    "1銘柄最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)


stop_loss = st.sidebar.slider(
    "損切り（%）",
    min_value=1.0,
    max_value=20.0,
    value=7.0,
    step=0.5
)


take_profit = st.sidebar.slider(
    "利確開始（%）",
    min_value=5.0,
    max_value=50.0,
    value=15.0,
    step=0.5
)


trailing_stop = st.sidebar.slider(
    "トレーリングストップ（%）",
    min_value=2.0,
    max_value=15.0,
    value=5.0,
    step=0.5
)


min_score = st.sidebar.slider(
    "最低BUYスコア",
    min_value=40,
    max_value=100,
    value=75,
    step=5
)


rsi_low = st.sidebar.slider(
    "RSI下限",
    min_value=20,
    max_value=50,
    value=45,
    step=1
)


rsi_high = st.sidebar.slider(
    "RSI上限",
    min_value=50,
    max_value=90,
    value=65,
    step=1
)


volume_multiplier = st.sidebar.slider(
    "出来高倍率",
    min_value=0.5,
    max_value=3.0,
    value=1.0,
    step=0.1
)


chase_limit = st.sidebar.slider(
    "急騰追い買い防止（25日線から）",
    min_value=3.0,
    max_value=20.0,
    value=8.0,
    step=1.0
)


cooldown_days = st.sidebar.slider(
    "損切り後の再購入禁止日数",
    min_value=0,
    max_value=30,
    value=10,
    step=1
)


years = st.sidebar.slider(
    "バックテスト期間（年）",
    min_value=1,
    max_value=10,
    value=5,
    step=1
)


# =========================================================
# Ver.4.3 リスク管理
# =========================================================

st.sidebar.subheader(
    "🛡️ Ver.4.3 リスク管理"
)


market_filter = st.sidebar.checkbox(
    "📊 日経225市場フィルター",
    value=True
)


loss_brake = st.sidebar.checkbox(
    "🛑 連続損失ブレーキ",
    value=True
)


ranking_only = st.sidebar.checkbox(
    "🏆 上位ランキング優先",
    value=True
)


# =========================================================
# 銘柄入力
# =========================================================

st.sidebar.subheader(
    "📋 対象銘柄"
)


ticker_input = st.sidebar.text_area(
    "銘柄コード",
    value=(
        "7203,6758,9984,8306,9432,"
        "7011,6501,6857,8035,9983"
    )
)


tickers = [
    x.strip()
    for x in ticker_input
    .replace("\n", ",")
    .split(",")
    if x.strip()
]


tickers = [
    x if "." in x else x + ".T"
    for x in tickers
]


# 重複削除
tickers = list(
    dict.fromkeys(tickers)
)


st.sidebar.write(
    f"対象銘柄数：{len(tickers)}"
)


# =========================================================
# 銘柄名取得
# =========================================================

@st.cache_data(ttl=86400)
def get_company_name(ticker):

    if ticker in DEFAULT_NAMES:
        return DEFAULT_NAMES[ticker]

    if yf is None:
        return ticker.replace(".T", "")

    try:

        info = yf.Ticker(ticker).info

        name = (
            info.get("longName")
            or info.get("shortName")
        )

        if name:
            return str(name)

    except Exception:
        pass

    return ticker.replace(".T", "")


# =========================================================
# データ取得
# =========================================================

@st.cache_data(ttl=3600)
def download_stock_data(
    ticker,
    years
):

    if yf is None:
        return None

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 250
        )
    )

    try:

        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False
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

        df = (
            df[required]
            .dropna()
            .copy()
        )

        return df

    except Exception as e:

        return None


# =========================================================
# 日経225データ
# =========================================================

@st.cache_data(ttl=3600)
def download_market_data(
    years
):

    if yf is None:
        return None

    end_date = date.today()

    start_date = (
        end_date
        - timedelta(
            days=365 * years + 250
        )
    )

    try:

        df = yf.download(
            "^N225",
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False
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
        ].dropna().copy()

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
            df["MA25"].diff(5)
        )

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

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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
        df["MA25"].diff(5)
    )

    df["MA75_SLOPE"] = (
        df["MA75"].diff(5)
    )

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

    df["MA25_DISTANCE"] = (
        (
            df["Close"]
            / df["MA25"]
        ) - 1
    ) * 100

    return df


# =========================================================
# BUYスコア
# =========================================================

def calculate_buy_score(
    row,
    rsi_low,
    rsi_high,
    volume_multiplier
):

    score = 0

    # 25日線 > 75日線
    if row["MA25"] > row["MA75"]:
        score += 20

    # 株価 > 200日線
    if row["Close"] > row["MA200"]:
        score += 20

    # 株価 > 25日線
    if row["Close"] > row["MA25"]:
        score += 15

    # 出来高
    if (
        row["Volume"]
        >= row["VOL20"]
        * volume_multiplier
    ):
        score += 15

    # RSI
    if (
        rsi_low
        <= row["RSI"]
        <= rsi_high
    ):
        score += 15

    # 25日線上向き
    if row["MA25_SLOPE"] > 0:
        score += 10

    # 75日線上向き
    if row["MA75_SLOPE"] > 0:
        score += 5

    return score


# =========================================================
# スコア説明
# =========================================================

def get_score_reasons(
    row,
    rsi_low,
    rsi_high,
    volume_multiplier
):

    reasons = []

    if row["MA25"] > row["MA75"]:
        reasons.append(
            "25日線 > 75日線"
        )

    if row["Close"] > row["MA200"]:
        reasons.append(
            "株価 > 200日線"
        )

    if row["Close"] > row["MA25"]:
        reasons.append(
            "株価 > 25日線"
        )

    if (
        row["Volume"]
        >= row["VOL20"]
        * volume_multiplier
    ):
        reasons.append(
            "出来高増加"
        )

    if (
        rsi_low
        <= row["RSI"]
        <= rsi_high
    ):
        reasons.append(
            "RSI適正"
        )

    if row["MA25_SLOPE"] > 0:
        reasons.append(
            "25日線上向き"
        )

    if row["MA75_SLOPE"] > 0:
        reasons.append(
            "75日線上向き"
        )

    return reasons


# =========================================================
# BUY判定
# =========================================================

def evaluate_buy(
    row,
    min_score,
    rsi_low,
    rsi_high,
    volume_multiplier,
    chase_limit
):

    score = calculate_buy_score(
        row,
        rsi_low,
        rsi_high,
        volume_multiplier
    )

    price_ok = (
        row["Close"] >= 2000
    )

    rsi_ok = (
        row["RSI"] >= rsi_low
        and
        row["RSI"] <= rsi_high
    )

    chase_ok = (
        row["MA25_DISTANCE"]
        <= chase_limit
    )

    signal = (
        score >= min_score
        and price_ok
        and rsi_ok
        and chase_ok
    )

    if signal:

        if score >= 90:
            judgment = "🔥 強BUY"

        elif score >= 85:
            judgment = "🟢 BUY強"

        else:
            judgment = "🟢 BUY"

    else:

        judgment = "⚪ 見送り"

    return (
        signal,
        score,
        judgment
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

    current_price = float(
        row["Close"]
    )

    profit_pct = (
        current_price
        / entry_price
        - 1
    ) * 100

    # 損切り
    if profit_pct <= -stop_loss:

        return True, "損切り"

    # 最高値更新
    if current_price > highest_price:

        position["highest_price"] = (
            current_price
        )

        highest_price = current_price

    # トレーリング
    if profit_pct >= take_profit:

        trailing_price = (
            highest_price
            * (
                1
                - trailing_stop / 100
            )
        )

        if current_price <= trailing_price:

            return True, "トレーリング利確"

    # 25日線割れ
    if current_price < row["MA25"]:

        if profit_pct > 5:

            return True, "25日線割れ利益確定"

    # 75日線割れ
    if current_price < row["MA75"]:

        return True, "75日線割れ"

    return False, ""


# =========================================================
# 市場環境
# =========================================================

def get_market_state(
    market_row
):

    if market_row is None:
        return "不明"

    try:

        close = float(
            market_row["Close"]
        )

        ma25 = float(
            market_row["MA25"]
        )

        ma75 = float(
            market_row["MA75"]
        )

        ma200 = float(
            market_row["MA200"]
        )

        slope = float(
            market_row["MA25_SLOPE"]
        )

        if (
            close > ma25
            and
            ma25 > ma75
            and
            ma75 > ma200
            and
            slope > 0
        ):

            return "🟢 強気"

        elif (
            close > ma25
            and
            ma25 > ma75
        ):

            return "🟡 やや強気"

        elif (
            close < ma25
            and
            ma25 < ma75
        ):

            return "🔴 弱気"

        else:

            return "⚪ 中立"

    except Exception:

        return "不明"


# =========================================================
# 購入金額倍率
# =========================================================

def score_allocation(
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
# バックテスト
# =========================================================

def run_backtest(
    ticker_data,
    market_data,
    names,
    initial_cash,
    max_positions,
    max_per_position,
    stop_loss,
    take_profit,
    trailing_stop,
    min_score,
    rsi_low,
    rsi_high,
    volume_multiplier,
    chase_limit,
    cooldown_days,
    market_filter,
    loss_brake,
    ranking_only
):

    cash = float(
        initial_cash
    )

    positions = {}

    trades = []

    equity_curve = []

    all_dates = set()

    cooldown_until = {}

    consecutive_losses = 0

    # 全日付
    for ticker, df in ticker_data.items():

        if (
            df is not None
            and not df.empty
        ):

            all_dates.update(
                df.index
            )

    all_dates = sorted(
        all_dates
    )

    # =====================================================
    # 日次処理
    # =====================================================

    for current_date in all_dates:

        # =================================================
        # SELL
        # =================================================

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[ticker]

            if current_date not in df.index:
                continue

            row = df.loc[current_date]

            position = positions[ticker]

            should_sell, reason = sell_signal(
                row,
                position,
                stop_loss,
                take_profit,
                trailing_stop
            )

            if should_sell:

                sell_price = float(
                    row["Close"]
                )

                shares = position["shares"]

                proceeds = (
                    sell_price
                    * shares
                )

                pnl = (
                    sell_price
                    - position["entry_price"]
                ) * shares

                cash += proceeds

                if pnl < 0:

                    consecutive_losses += 1

                else:

                    consecutive_losses = 0

                trades.append({

                    "Date": current_date,

                    "Ticker": ticker.replace(
                        ".T",
                        ""
                    ),

                    "Name": names.get(
                        ticker,
                        ticker
                    ),

                    "Action": "SELL",

                    "Price": sell_price,

                    "Shares": shares,

                    "Amount": proceeds,

                    "PnL": pnl,

                    "Score": position["score"],

                    "Reason": reason

                })

                # 損切り後クールダウン
                if reason == "損切り":

                    future_dates = [
                        d
                        for d in all_dates
                        if d > current_date
                    ]

                    if future_dates:

                        if cooldown_days > 0:

                            idx = min(
                                cooldown_days - 1,
                                len(future_dates) - 1
                            )

                            cooldown_until[ticker] = (
                                future_dates[idx]
                            )

                del positions[ticker]

        # =================================================
        # 市場状態
        # =================================================

        market_row = None

        if (
            market_data is not None
            and current_date in market_data.index
        ):

            market_row = (
                market_data
                .loc[current_date]
            )

        market_state = get_market_state(
            market_row
        )

        market_is_bad = (
            market_state == "🔴 弱気"
        )

        # =================================================
        # BUY候補作成
        # =================================================

        candidates = []

        for ticker, df in ticker_data.items():

            if current_date not in df.index:
                continue

            if ticker in positions:
                continue

            if ticker in cooldown_until:

                if (
                    current_date
                    <= cooldown_until[ticker]
                ):

                    continue

            row = df.loc[current_date]

            required = [

                row["MA25"],
                row["MA75"],
                row["MA200"],
                row["RSI"],
                row["VOL20"],
                row["MA25_SLOPE"],
                row["MA75_SLOPE"],
                row["MA25_DISTANCE"]

            ]

            if any(
                pd.isna(x)
                for x in required
            ):

                continue

            signal, score, judgment = evaluate_buy(

                row,

                min_score,

                rsi_low,

                rsi_high,

                volume_multiplier,

                chase_limit

            )

            if not signal:
                continue

            # 市場フィルター
            if market_filter:

                if market_is_bad:

                    continue

            # 連続損失ブレーキ
            if loss_brake:

                if consecutive_losses >= 3:

                    # スコア85以上だけ許可
                    if score < 85:
                        continue

            candidates.append({

                "ticker": ticker,

                "score": score,

                "row": row,

                "judgment": judgment

            })

        # =================================================
        # スコア順
        # =================================================

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        # =================================================
        # BUY
        # =================================================

        for candidate in candidates:

            if (
                len(positions)
                >= max_positions
            ):
                break

            ticker = candidate["ticker"]

            score = candidate["score"]

            row = candidate["row"]

            # ランキング優先
            if ranking_only:

                current_rank = (
                    candidates.index(
                        candidate
                    )
                    + 1
                )

                # 上位銘柄を優先
                if (
                    current_rank
                    > max_positions
                ):
                    break

            allocation = score_allocation(
                score
            )

            if allocation <= 0:
                continue

            # 連続損失時は半分
            if (
                loss_brake
                and
                consecutive_losses >= 3
            ):

                allocation *= 0.5

            target_amount = (
                max_per_position
                * allocation
            )

            price = float(
                row["Close"]
            )

            shares = int(
                target_amount
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

            positions[ticker] = {

                "entry_price": price,

                "shares": shares,

                "highest_price": price,

                "score": score

            }

            trades.append({

                "Date": current_date,

                "Ticker": ticker.replace(
                    ".T",
                    ""
                ),

                "Name": names.get(
                    ticker,
                    ticker
                ),

                "Action": "BUY",

                "Price": price,

                "Shares": shares,

                "Amount": amount,

                "PnL": 0,

                "Score": score,

                "Reason": (
                    "強BUY"
                    if score >= 85
                    else "BUY"
                )

            })

        # =================================================
        # 資産評価
        # =================================================

        equity = cash

        for ticker, position in positions.items():

            df = ticker_data[ticker]

            if current_date in df.index:

                price = float(
                    df.loc[
                        current_date,
                        "Close"
                    ]
                )

                equity += (
                    price
                    * position["shares"]
                )

        equity_curve.append({

            "Date": current_date,

            "Equity": equity,

            "Cash": cash,

            "Positions": len(
                positions
            ),

            "Market": market_state

        })

    # =====================================================
    # 最終決済
    # =====================================================

    if all_dates:

        final_date = all_dates[-1]

        for ticker in list(
            positions.keys()
        ):

            df = ticker_data[ticker]

            if final_date not in df.index:
                continue

            row = df.loc[final_date]

            sell_price = float(
                row["Close"]
            )

            position = positions[ticker]

            shares = position["shares"]

            proceeds = (
                sell_price
                * shares
            )

            pnl = (
                sell_price
                - position["entry_price"]
            ) * shares

            cash += proceeds

            trades.append({

                "Date": final_date,

                "Ticker": ticker.replace(
                    ".T",
                    ""
                ),

                "Name": names.get(
                    ticker,
                    ticker
                ),

                "Action": "SELL",

                "Price": sell_price,

                "Shares": shares,

                "Amount": proceeds,

                "PnL": pnl,

                "Score": position["score"],

                "Reason": "最終決済"

            })

            del positions[ticker]

        # 最終資産
        equity_curve.append({

            "Date": final_date,

            "Equity": cash,

            "Cash": cash,

            "Positions": 0,

            "Market": get_market_state(
                market_data.loc[final_date]
                if (
                    market_data is not None
                    and final_date in market_data.index
                )
                else None
            )

        })

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_curve
    )

    # 重複日付削除
    if not equity_df.empty:

        equity_df = (
            equity_df
            .drop_duplicates(
                subset=["Date"],
                keep="last"
            )
            .sort_values("Date")
            .reset_index(drop=True)
        )

        # DD
        equity_df["Peak"] = (
            equity_df["Equity"]
            .cummax()
        )

        equity_df["Drawdown"] = (
            equity_df["Equity"]
            - equity_df["Peak"]
        )

        equity_df["DrawdownPct"] = (
            equity_df["Drawdown"]
            / equity_df["Peak"]
            * 100
        )

    return (
        trades_df,
        equity_df
    )


# =========================================================
# 統計
# =========================================================

def calculate_statistics(
    trades_df,
    equity_df,
    initial_cash
):

    result = {}

    if equity_df.empty:
        return result

    final_equity = float(
        equity_df.iloc[-1]["Equity"]
    )

    profit = (
        final_equity
        - initial_cash
    )

    profit_pct = (
        profit
        / initial_cash
    ) * 100

    max_dd = float(
        equity_df["Drawdown"].min()
    )

    max_dd_pct = float(
        equity_df["DrawdownPct"].min()
    )

    result[
        "final_equity"
    ] = final_equity

    result[
        "profit"
    ] = profit

    result[
        "profit_pct"
    ] = profit_pct

    result[
        "max_dd"
    ] = max_dd

    result[
        "max_dd_pct"
    ] = max_dd_pct

    if trades_df.empty:

        result["sell_count"] = 0
        result["win_rate"] = 0
        result["profit_factor"] = 0
        result["avg_win"] = 0
        result["avg_loss"] = 0
        result["risk_reward"] = 0

        return result

    sells = trades_df[
        trades_df["Action"] == "SELL"
    ].copy()

    if sells.empty:

        result["sell_count"] = 0
        result["win_rate"] = 0
        result["profit_factor"] = 0
        result["avg_win"] = 0
        result["avg_loss"] = 0
        result["risk_reward"] = 0

        return result

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

    gross_profit = wins["PnL"].sum()

    gross_loss = abs(
        losses["PnL"].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = np.inf

    avg_win = (
        wins["PnL"].mean()
        if not wins.empty
        else 0
    )

    avg_loss = (
        abs(losses["PnL"].mean())
        if not losses.empty
        else 0
    )

    risk_reward = (
        avg_win / avg_loss
        if avg_loss > 0
        else np.inf
    )

    result[
        "sell_count"
    ] = len(sells)

    result[
        "win_rate"
    ] = win_rate

    result[
        "profit_factor"
    ] = profit_factor

    result[
        "avg_win"
    ] = avg_win

    result[
        "avg_loss"
    ] = avg_loss

    result[
        "risk_reward"
    ] = risk_reward

    return result


# =========================================================
# データ取得
# =========================================================

st.subheader(
    "📥 データ取得"
)


if st.button(
    "🚀 Ver.4.3 バックテスト開始",
    type="primary"
):

    if yf is None:

        st.error(
            "yfinanceがインストールされていません。"
            "requirements.txtを確認してください。"
        )

        st.stop()

    if not tickers:

        st.error(
            "銘柄コードを入力してください。"
        )

        st.stop()

    progress = st.progress(0)

    ticker_data = {}

    names = {}

    # =====================================================
    # 銘柄データ
    # =====================================================

    for i, ticker in enumerate(tickers):

        df = download_stock_data(
            ticker,
            years
        )

        if (
            df is not None
            and not df.empty
        ):

            df = add_indicators(
                df
            )

            ticker_data[
                ticker
            ] = df

            names[
                ticker
            ] = get_company_name(
                ticker
            )

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

    # =====================================================
    # 市場データ
    # =====================================================

    market_data = None

    if market_filter:

        with st.spinner(
            "📊 日経225市場データを取得中..."
        ):

            market_data = (
                download_market_data(
                    years
                )
            )

    st.success(
        f"{len(ticker_data)}銘柄のデータを取得しました。"
    )

    # =====================================================
    # バックテスト
    # =====================================================

    trades_df, equity_df = run_backtest(

        ticker_data,

        market_data,

        names,

        initial_cash,

        max_positions,

        max_per_position,

        stop_loss,

        take_profit,

        trailing_stop,

        min_score,

        rsi_low,

        rsi_high,

        volume_multiplier,

        chase_limit,

        cooldown_days,

        market_filter,

        loss_brake,

        ranking_only

    )

    # =====================================================
    # 結果
    # =====================================================

    st.header(
        "📊 Ver.4.3 バックテスト結果"
    )

    if equity_df.empty:

        st.warning(
            "資産推移データがありません。"
        )

        st.stop()

    stats = calculate_statistics(

        trades_df,

        equity_df,

        initial_cash

    )

    # =====================================================
    # 基本結果
    # =====================================================

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "最終資産",
        f"¥{stats['final_equity']:,.0f}"
    )

    col2.metric(
        "損益",
        f"¥{stats['profit']:,.0f}"
    )

    col3.metric(
        "損益率",
        f"{stats['profit_pct']:.2f}%"
    )

    col4.metric(
        "最大DD",
        f"¥{stats['max_dd']:,.0f}"
    )

    # =====================================================
    # 統計
    # =====================================================

    st.subheader(
        "📐 トレード統計"
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric(
        "決済トレード数",
        f"{stats['sell_count']}"
    )

    col2.metric(
        "勝率",
        f"{stats['win_rate']:.1f}%"
    )

    pf = stats["profit_factor"]

    pf_text = (
        "∞"
        if np.isinf(pf)
        else f"{pf:.2f}"
    )

    col3.metric(
        "Profit Factor",
        pf_text
    )

    col4.metric(
        "平均利益",
        f"¥{stats['avg_win']:,.0f}"
    )

    rr = stats["risk_reward"]

    rr_text = (
        "∞"
        if np.isinf(rr)
        else f"{rr:.2f}倍"
    )

    col5.metric(
        "平均利益/損失",
        rr_text
    )

    col1, col2 = st.columns(2)

    col1.metric(
        "最大DD額",
        f"¥{stats['max_dd']:,.0f}"
    )

    col2.metric(
        "最大DD率",
        f"{stats['max_dd_pct']:.2f}%"
    )

    # =====================================================
    # 現在の市場環境
    # =====================================================

    st.subheader(
        "🌏 現在の市場環境"
    )

    if (
        market_data is not None
        and not market_data.empty
    ):

        latest_market = (
            market_data.iloc[-1]
        )

        market_state = get_market_state(
            latest_market
        )

        st.metric(
            "日経225市場判定",
            market_state
        )

    else:

        st.info(
            "市場フィルターが無効、または日経225データを取得できませんでした。"
        )

    # =====================================================
    # AI BUYランキング
    # =====================================================

    st.header(
        "🏆 AI BUYランキング"
    )

    latest_candidates = []

    for ticker, df in ticker_data.items():

        if df.empty:
            continue

        row = df.iloc[-1]

        required = [

            row["MA25"],
            row["MA75"],
            row["MA200"],
            row["RSI"],
            row["VOL20"],
            row["MA25_SLOPE"],
            row["MA75_SLOPE"],
            row["MA25_DISTANCE"]

        ]

        if any(
            pd.isna(x)
            for x in required
        ):

            continue

        signal, score, judgment = evaluate_buy(

            row,

            min_score,

            rsi_low,

            rsi_high,

            volume_multiplier,

            chase_limit

        )

        reasons = get_score_reasons(

            row,

            rsi_low,

            rsi_high,

            volume_multiplier

        )

        latest_candidates.append({

            "Ticker": ticker.replace(
                ".T",
                ""
            ),

            "Name": names.get(
                ticker,
                ticker
            ),

            "Price": float(
                row["Close"]
            ),

            "Score": score,

            "Judgment": judgment,

            "RSI": float(
                row["RSI"]
            ),

            "MA25乖離率": float(
                row["MA25_DISTANCE"]
            ),

            "理由": " / ".join(
                reasons
            )

        })

    ranking_df = pd.DataFrame(
        latest_candidates
    )

    if not ranking_df.empty:

        ranking_df = (
            ranking_df
            .sort_values(
                "Score",
                ascending=False
            )
            .reset_index(drop=True)
        )

        ranking_df.index += 1

        st.dataframe(

            ranking_df.style.format({

                "Price": "¥{:,.0f}",

                "Score": "{:.0f}",

                "RSI": "{:.1f}",

                "MA25乖離率": "{:.1f}%"

            }),

            use_container_width=True

        )

        st.caption(
            "※ランキングはテクニカル条件を点数化したもので、将来の株価上昇を保証するものではありません。"
        )

    else:

        st.warning(
            "ランキング対象となる銘柄がありません。"
        )

    # =====================================================
    # 資産推移
    # =====================================================

    st.subheader(
        "📈 資産推移"
    )

    chart_df = (
        equity_df
        .set_index("Date")
        ["Equity"]
    )

    st.line_chart(
        chart_df
    )

    # =====================================================
    # ドローダウン
    # =====================================================

    st.subheader(
        "📉 ドローダウン"
    )

    dd_chart = (
        equity_df
        .set_index("Date")
        ["Drawdown"]
    )

    st.area_chart(
        dd_chart
    )

    # =====================================================
    # 銘柄別成績
    # =====================================================

    if not trades_df.empty:

        sells = trades_df[
            trades_df["Action"] == "SELL"
        ].copy()

        if not sells.empty:

            st.subheader(
                "🏆 銘柄別成績"
            )

            stock_result = (
                sells
                .groupby(
                    ["Ticker", "Name"]
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
                use_container_width=True
            )

            # =================================================
            # 改善対象
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
                use_container_width=True
            )

            # =================================================
            # 売却理由
            # =================================================

            st.subheader(
                "🚦 売却理由別成績"
            )

            reason_result = (
                sells
                .groupby("Reason")
                .agg(

                    回数=(
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

                reason_result.style.format({

                    "損益":
                    "¥{:,.0f}",

                    "平均損益":
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
            use_container_width=True
        )

        # =====================================================
        # CSV
        # =====================================================

        csv = (
            trades_df
            .to_csv(
                index=False
            )
            .encode("utf-8-sig")
        )

        st.download_button(

            "⬇️ 売買記録CSVをダウンロード",

            data=csv,

            file_name=
            "ver4_3_trades.csv",

            mime="text/csv"

        )

    else:

        st.warning(
            "売買が発生しませんでした。"
            "最低BUYスコアなどの設定を確認してください。"
        )


# =========================================================
# Ver.4.3 説明
# =========================================================

st.divider()

st.subheader(
    "🧠 Ver.4.3 売買思想"
)

st.markdown(
    """
## 🟢 BUYスコア

100点満点

- 25日線 > 75日線 …… 20点
- 株価 > 200日線 …… 20点
- 株価 > 25日線 …… 15点
- 出来高条件 …… 15点
- RSI適正 …… 15点
- 25日線上向き …… 10点
- 75日線上向き …… 5点

---

## 🏆 AI BUYランキング

各銘柄をスコアリングし、

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

つまり、

**強い銘柄ほど資金を厚くする**

方式です。

---

## 📊 市場環境フィルター

日経225の、

- 25日線
- 75日線
- 200日線
- 25日線の傾き

を確認します。

弱気相場ではBUYを抑制します。

---

## 🛑 連続損失ブレーキ

3回連続して損失が出た場合、

通常より厳しいBUY条件にします。

さらに購入金額を抑えます。

---

## 🛡️ リスク管理

### 損切り

-7%

### 利確開始

+15%

### トレーリングストップ

利益を伸ばした後、

最高値から一定割合下落したところで利確。

### 25日線割れ

利益が5%以上ある場合、

25日線割れで利益確定。

### 75日線割れ

75日線割れで撤退。

### 損切り後

標準10営業日再購入禁止。

---

## 🚫 明けの明星

**完全に使用しません。**

Ver.4.3でも、

明けの明星は

**BUY条件・ランキング・スコアのいずれにも使用していません。**

---

## ⚠️ 注意

このアプリは過去の株価データを使った
仮想バックテストおよび銘柄分析ツールです。

バックテスト結果やBUYランキングは、
将来の利益を保証するものではありません。
"""
)
