import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import io
import time

try:
    import yfinance as yf
except ImportError:
    yf = None


# =========================================================
# 日本株 10万円→100万円 AI投資アシスタント Ver.4.0
# =========================================================
# Ver.4.0の目的
# - 10万円スタートのS株向け仮想バックテスト
# - 明けの明星は使用しない
# - 株価・出来高・MA・RSIを中心にスコアリング
# - Streamlit cacheで再取得・再計算を抑制
# - S株の実際の約定ルールを完全再現するものではなく、
#   「当日終値でシグナル→翌営業日始値で約定」の近似を採用
#
# Ver.4.1以降で追加予定:
# - 業績
# - 市況・セクター
# - ニュース・材料
# - より厳密なS株注文時刻シミュレーション
# - 日本株全銘柄自動スキャン
# =========================================================


st.set_page_config(
    page_title="日本株 10万円→100万円 Ver.4.0",
    page_icon="📈",
    layout="wide",
)


# =========================================================
# 共通関数
# =========================================================
def normalize_tickers(text: str):
    """7203,6758,9984 -> ['7203.T', ...]"""
    raw = (
        text.replace("、", ",")
        .replace(" ", ",")
        .replace("\n", ",")
        .split(",")
    )

    result = []
    for x in raw:
        x = x.strip().upper()
        if not x:
            continue

        if x.endswith(".T"):
            ticker = x
        elif x.isdigit():
            ticker = f"{x}.T"
        else:
            ticker = x

        if ticker not in result:
            result.append(ticker)

    return result


def ticker_display(ticker: str):
    return ticker.replace(".T", "")


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


# =========================================================
# テクニカル指標
# =========================================================
def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def add_indicators(df):
    """OHLCV DataFrameに指標を追加"""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    # MultiIndex等を避けて標準化
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]
    for col in required:
        if col not in out.columns:
            out[col] = np.nan

    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["Close"]).copy()

    out["MA25"] = out["Close"].rolling(25).mean()
    out["MA75"] = out["Close"].rolling(75).mean()
    out["MA200"] = out["Close"].rolling(200).mean()

    out["RSI14"] = calculate_rsi(out["Close"], 14)

    out["VOL20"] = out["Volume"].rolling(20).mean()
    out["VOL_RATIO"] = out["Volume"] / out["VOL20"].replace(0, np.nan)

    out["RET_5D"] = out["Close"].pct_change(5)
    out["RET_20D"] = out["Close"].pct_change(20)

    # 20日高値・安値
    out["HIGH20"] = out["High"].rolling(20).max()
    out["LOW20"] = out["Low"].rolling(20).min()

    # ボラティリティ
    out["VOLATILITY20"] = out["Close"].pct_change().rolling(20).std()

    # 直近高値からの下落率
    out["FROM_HIGH20"] = out["Close"] / out["HIGH20"] - 1

    # 前日終値比
    out["DAY_CHANGE"] = out["Close"].pct_change()

    return out


# =========================================================
# yfinanceデータ取得
# =========================================================
@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def download_data_cached(tickers_tuple, start_date, end_date):
    """
    yfinanceからまとめて取得。
    同じ条件ならStreamlitがキャッシュを利用する。
    """
    if yf is None:
        raise RuntimeError(
            "yfinanceがインストールされていません。requirements.txtを確認してください。"
        )

    tickers = list(tickers_tuple)

    if not tickers:
        return {}

    data = yf.download(
        tickers=tickers,
        start=start_date,
        end=end_date,
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )

    result = {}

    if data is None or data.empty:
        return result

    # 複数銘柄
    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(data.columns.get_level_values(0))

        for ticker in tickers:
            try:
                # 通常のyfinance形式: Column -> Ticker
                if ticker in data.columns.get_level_values(1):
                    df = data.xs(ticker, axis=1, level=1, drop_level=True)
                else:
                    df = pd.DataFrame()
            except Exception:
                df = pd.DataFrame()

            if not df.empty:
                result[ticker] = df.copy()

    else:
        # 1銘柄
        result[tickers[0]] = data.copy()

    return result


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def prepare_all_data(raw_items):
    """
    指標計算もキャッシュ。
    raw_itemsは(tuple(ticker, dataframe))の形で渡す。
    """
    result = {}

    for ticker, df in raw_items:
        try:
            result[ticker] = add_indicators(df)
        except Exception:
            result[ticker] = pd.DataFrame()

    return result


# =========================================================
# スコアリング
# =========================================================
def score_row(row, settings):
    """
    100点満点。
    Ver.4.0ではテクニカル中心。
    明けの明星は使用しない。
    """
    score = 0
    reasons = []
    warnings = []

    close = safe_float(row.get("Close"))
    ma25 = safe_float(row.get("MA25"))
    ma75 = safe_float(row.get("MA75"))
    ma200 = safe_float(row.get("MA200"))
    rsi = safe_float(row.get("RSI14"), 50)
    vol_ratio = safe_float(row.get("VOL_RATIO"), 1)
    ret5 = safe_float(row.get("RET_5D"))
    ret20 = safe_float(row.get("RET_20D"))
    from_high = safe_float(row.get("FROM_HIGH20"))
    volatility = safe_float(row.get("VOLATILITY20"))

    # --- トレンド 30点 ---
    if ma25 > 0 and ma75 > 0 and ma25 > ma75:
        score += 12
        reasons.append("25日線 > 75日線")

    if ma200 > 0 and close > ma200:
        score += 8
        reasons.append("株価 > 200日線")

    if ma25 > 0 and close > ma25:
        score += 5
        reasons.append("株価 > 25日線")

    if ret20 > 0:
        score += 5
        reasons.append("20日騰落率プラス")
    else:
        warnings.append("20日騰落率マイナス")

    # --- 出来高 15点 ---
    if vol_ratio >= 1.5:
        score += 10
        reasons.append(f"出来高{vol_ratio:.1f}倍")
    elif vol_ratio >= 1.2:
        score += 7
        reasons.append(f"出来高{vol_ratio:.1f}倍")
    elif vol_ratio >= 1.0:
        score += 4
        reasons.append("出来高20日平均以上")

    # --- RSI 20点 ---
    if settings["use_rsi"]:
        if 45 <= rsi <= 65:
            score += 15
            reasons.append(f"RSI適正({rsi:.1f})")
        elif 35 <= rsi < 45:
            score += 10
            reasons.append(f"RSI押し目({rsi:.1f})")
        elif 65 < rsi <= settings["rsi_max"]:
            score += 8
            reasons.append(f"RSIやや高め({rsi:.1f})")
        elif rsi > settings["rsi_max"]:
            warnings.append(f"RSI過熱({rsi:.1f})")
        else:
            warnings.append(f"RSI低迷({rsi:.1f})")

    # --- モメンタム 15点 ---
    if ret5 >= 0.03:
        score += 8
        reasons.append("5日上昇モメンタム")
    elif ret5 >= 0:
        score += 4

    if 0.02 <= ret20 <= 0.20:
        score += 7
        reasons.append("20日上昇率が適正")
    elif ret20 > 0.20:
        warnings.append("短期間で上昇し過ぎの可能性")

    # --- 押し目 10点 ---
    if -0.10 <= from_high <= -0.02 and close > ma75:
        score += 10
        reasons.append("高値からの健全な押し目")
    elif -0.02 < from_high <= 0:
        score += 5

    # 過度なボラティリティ
    if volatility > 0.06:
        score -= 5
        warnings.append("値動きが大きい")

    score = max(0, min(100, int(score)))

    if score >= settings["buy_score"]:
        decision = "🟢 買い候補"
    elif score >= settings["watch_score"]:
        decision = "🟡 監視・押し目待ち"
    else:
        decision = "⚪ 見送り"

    return score, decision, reasons, warnings


# =========================================================
# バックテスト
# =========================================================
def run_backtest(
    data_dict,
    initial_cash,
    max_positions,
    max_per_position,
    stop_loss,
    take_profit,
    buy_score,
    min_price,
    max_price,
):
    """
    簡易S株バックテスト。

    重要:
    当日終値でシグナルを確定し、
    次の営業日の始値で約定する近似モデル。
    実際のS株の1日3回の約定タイミングを完全再現するものではない。
    """
    if not data_dict:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    settings = {
        "use_rsi": True,
        "rsi_max": 70,
        "buy_score": buy_score,
        "watch_score": max(0, buy_score - 15),
    }

    # 全日付を取得
    all_dates = sorted(
        set(
            d
            for df in data_dict.values()
            if df is not None and not df.empty
            for d in df.index
        )
    )

    if len(all_dates) < 2:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    cash = float(initial_cash)
    positions = {}

    trades = []
    equity_rows = []

    for i in range(len(all_dates) - 1):
        current_date = all_dates[i]
        next_date = all_dates[i + 1]

        # ---------------------------------------------
        # まず保有株を翌日始値で売却判定
        # ---------------------------------------------
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            df = data_dict[ticker]

            if next_date not in df.index:
                continue

            next_row = df.loc[next_date]

            open_price = safe_float(next_row.get("Open"))
            low_price = safe_float(next_row.get("Low"))
            high_price = safe_float(next_row.get("High"))

            if open_price <= 0:
                continue

            sell_price = None
            reason = None

            # 損切り/利確は翌日始値と当日高安を簡易利用
            stop_price = pos["entry_price"] * (1 - stop_loss / 100)
            take_price = pos["entry_price"] * (1 + take_profit / 100)

            if open_price <= stop_price:
                sell_price = open_price
                reason = "損切り"
            elif open_price >= take_price:
                sell_price = open_price
                reason = "利確"
            elif low_price <= stop_price:
                sell_price = stop_price
                reason = "損切り"
            elif high_price >= take_price:
                sell_price = take_price
                reason = "利確"
            else:
                # 現在のトレンド悪化
                ma25 = safe_float(next_row.get("MA25"))
                ma75 = safe_float(next_row.get("MA75"))
                rsi = safe_float(next_row.get("RSI14"), 50)

                if ma25 > 0 and ma75 > 0 and ma25 < ma75:
                    sell_price = open_price
                    reason = "トレンド悪化"
                elif rsi > 80:
                    sell_price = open_price
                    reason = "RSI過熱"

            if sell_price is not None and sell_price > 0:
                shares = pos["shares"]
                proceeds = shares * sell_price
                cash += proceeds

                pnl = (sell_price - pos["entry_price"]) * shares

                trades.append(
                    {
                        "Date": pd.Timestamp(next_date),
                        "Ticker": ticker_display(ticker),
                        "Action": "SELL",
                        "Price": round(sell_price, 2),
                        "Shares": int(shares),
                        "Amount": round(proceeds, 2),
                        "PnL": round(pnl, 2),
                        "Reason": reason,
                    }
                )

                del positions[ticker]

        # ---------------------------------------------
        # 次に買い候補を判定
        # ---------------------------------------------
        candidates = []

        for ticker, df in data_dict.items():
            if df is None or df.empty:
                continue

            if current_date not in df.index or next_date not in df.index:
                continue

            if ticker in positions:
                continue

            row = df.loc[current_date]
            next_row = df.loc[next_date]

            current_close = safe_float(row.get("Close"))
            next_open = safe_float(next_row.get("Open"))

            if current_close <= 0 or next_open <= 0:
                continue

            # 株価範囲
            if current_close < min_price:
                continue

            if max_price > 0 and current_close > max_price:
                continue

            score, decision, reasons, warnings = score_row(row, settings)

            if score >= buy_score:
                candidates.append(
                    (
                        score,
                        ticker,
                        next_open,
                        reasons,
                        warnings,
                    )
                )

        candidates.sort(reverse=True, key=lambda x: x[0])

        available_slots = max(0, max_positions - len(positions))

        for score, ticker, next_open, reasons, warnings in candidates[:available_slots]:
            if cash <= 0:
                break

            budget = min(max_per_position, cash)

            shares = int(budget // next_open)

            # S株なので1株からOK
            if shares < 1:
                continue

            cost = shares * next_open

            if cost > cash:
                continue

            cash -= cost

            positions[ticker] = {
                "shares": shares,
                "entry_price": next_open,
                "entry_date": next_date,
                "score": score,
            }

            trades.append(
                {
                    "Date": pd.Timestamp(next_date),
                    "Ticker": ticker_display(ticker),
                    "Action": "BUY",
                    "Price": round(next_open, 2),
                    "Shares": int(shares),
                    "Amount": round(cost, 2),
                    "PnL": 0.0,
                    "Reason": f"Score {score} / " + ", ".join(reasons[:3]),
                }
            )

        # ---------------------------------------------
        # 当日終値で資産評価
        # ---------------------------------------------
        equity = cash

        for ticker, pos in positions.items():
            df = data_dict[ticker]

            if current_date in df.index:
                close = safe_float(df.loc[current_date].get("Close"))
                equity += pos["shares"] * close

        equity_rows.append(
            {
                "Date": pd.Timestamp(current_date),
                "Cash": cash,
                "Equity": equity,
                "Positions": len(positions),
            }
        )

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_rows)

    # 保有中ポジション
    positions_rows = []
    for ticker, pos in positions.items():
        df = data_dict[ticker]
        if df.empty:
            continue

        last_row = df.iloc[-1]
        last_price = safe_float(last_row.get("Close"))

        market_value = pos["shares"] * last_price
        pnl = (last_price - pos["entry_price"]) * pos["shares"]

        positions_rows.append(
            {
                "Ticker": ticker_display(ticker),
                "Shares": pos["shares"],
                "EntryPrice": round(pos["entry_price"], 2),
                "CurrentPrice": round(last_price, 2),
                "MarketValue": round(market_value, 2),
                "PnL": round(pnl, 2),
                "PnL%": round(
                    (last_price / pos["entry_price"] - 1) * 100, 2
                )
                if pos["entry_price"] > 0
                else 0,
                "EntryDate": pos["entry_date"],
                "Score": pos["score"],
            }
        )

    positions_df = pd.DataFrame(positions_rows)

    return trades_df, equity_df, positions_df


# =========================================================
# 統計
# =========================================================
def calculate_statistics(trades_df, equity_df, initial_cash):
    if equity_df.empty:
        return {
            "final": initial_cash,
            "profit": 0,
            "return_pct": 0,
            "max_dd": 0,
            "trades": 0,
            "win_rate": 0,
        }

    final_equity = safe_float(equity_df.iloc[-1]["Equity"], initial_cash)
    profit = final_equity - initial_cash
    return_pct = (final_equity / initial_cash - 1) * 100

    equity = equity_df["Equity"].astype(float)
    peak = equity.cummax()
    drawdown = equity / peak - 1
    max_dd = drawdown.min() * 100

    if trades_df.empty:
        win_rate = 0
        sell_count = 0
    else:
        sells = trades_df[trades_df["Action"] == "SELL"].copy()
        sell_count = len(sells)
        if sell_count > 0:
            win_rate = (sells["PnL"] > 0).mean() * 100
        else:
            win_rate = 0

    return {
        "final": final_equity,
        "profit": profit,
        "return_pct": return_pct,
        "max_dd": max_dd,
        "trades": sell_count,
        "win_rate": win_rate,
    }


# =========================================================
# 最新スコア
# =========================================================
def latest_candidates(data_dict, buy_score, min_price, max_price, top_n=10):
    settings = {
        "use_rsi": True,
        "rsi_max": 70,
        "buy_score": buy_score,
        "watch_score": max(0, buy_score - 15),
    }

    rows = []

    for ticker, df in data_dict.items():
        if df is None or df.empty:
            continue

        row = df.iloc[-1]

        close = safe_float(row.get("Close"))
        if close < min_price:
            continue

        if max_price > 0 and close > max_price:
            continue

        score, decision, reasons, warnings = score_row(row, settings)

        rows.append(
            {
                "Ticker": ticker_display(ticker),
                "Price": round(close, 2),
                "Score": score,
                "Decision": decision,
                "RSI": round(safe_float(row.get("RSI14"), 50), 1),
                "MA25": round(safe_float(row.get("MA25")), 2),
                "MA75": round(safe_float(row.get("MA75")), 2),
                "VolumeRatio": round(safe_float(row.get("VOL_RATIO"), 1), 2),
                "5D": round(safe_float(row.get("RET_5D")) * 100, 2),
                "20D": round(safe_float(row.get("RET_20D")) * 100, 2),
                "Reasons": " / ".join(reasons[:5]),
                "Warnings": " / ".join(warnings[:3]),
            }
        )

    result = pd.DataFrame(rows)

    if result.empty:
        return result

    return result.sort_values(
        ["Score", "VolumeRatio"],
        ascending=[False, False],
    ).head(top_n)


# =========================================================
# UI
# =========================================================
st.title("📈 日本株 10万円→100万円 AI投資アシスタント Ver.4.0")

st.caption(
    "S株を想定した仮想バックテスト。明けの明星は使用しません。"
)

with st.sidebar:
    st.header("⚙️ 基本設定")

    initial_cash = st.number_input(
        "初期資金（円）",
        min_value=10_000,
        max_value=100_000_000,
        value=100_000,
        step=10_000,
    )

    target_cash = st.number_input(
        "目標資産（円）",
        min_value=100_000,
        max_value=100_000_000,
        value=1_000_000,
        step=100_000,
    )

    max_positions = st.number_input(
        "最大保有銘柄数",
        min_value=1,
        max_value=50,
        value=5,
        step=1,
    )

    max_per_position = st.number_input(
        "1銘柄の最大購入額（円）",
        min_value=1_000,
        max_value=100_000_000,
        value=20_000,
        step=1_000,
    )

    st.divider()

    st.subheader("売買ルール")

    stop_loss = st.slider(
        "損切り（%）",
        min_value=1.0,
        max_value=30.0,
        value=7.0,
        step=0.5,
    )

    take_profit = st.slider(
        "利確（%）",
        min_value=2.0,
        max_value=100.0,
        value=15.0,
        step=1.0,
    )

    buy_score = st.slider(
        "買い判定スコア",
        min_value=50,
        max_value=100,
        value=75,
        step=1,
    )

    min_price = st.number_input(
        "最低株価（円）",
        min_value=0,
        max_value=1_000_000,
        value=500,
        step=100,
    )

    max_price = st.number_input(
        "最高株価（円）※0=制限なし",
        min_value=0,
        max_value=1_000_000,
        value=0,
        step=100,
    )

    st.divider()

    st.subheader("バックテスト期間")

    years = st.slider(
        "過去何年分？",
        min_value=1,
        max_value=5,
        value=5,
        step=1,
    )

    st.divider()

    st.subheader("対象銘柄")

    ticker_text = st.text_area(
        "銘柄コードを入力",
        value="7203,6758,9984,8306,9432,8035,6861,6857,7011,6501",
        height=120,
        help="例：7203,6758,9984。東証銘柄は自動的に.Tを付けます。",
    )

    uploaded = st.file_uploader(
        "銘柄コードCSV（任意）",
        type=["csv"],
        help="1列目に銘柄コードを入れたCSVを指定できます。",
    )

    st.divider()

    run_button = st.button(
        "🚀 バックテスト開始",
        type="primary",
        use_container_width=True,
    )

    clear_button = st.button(
        "🧹 キャッシュをクリア",
        use_container_width=True,
    )

if clear_button:
    st.cache_data.clear()
    st.success("キャッシュをクリアしました。")
    st.stop()


# =========================================================
# 銘柄読み込み
# =========================================================
tickers = normalize_tickers(ticker_text)

if uploaded is not None:
    try:
        csv_df = pd.read_csv(uploaded)

        if not csv_df.empty:
            first_col = csv_df.columns[0]
            csv_tickers = normalize_tickers(
                ",".join(csv_df[first_col].astype(str).tolist())
            )

            for ticker in csv_tickers:
                if ticker not in tickers:
                    tickers.append(ticker)

    except Exception as e:
        st.warning(f"CSVを読み込めませんでした: {e}")

if not tickers:
    st.warning("銘柄コードを1つ以上入力してください。")
    st.stop()

st.info(
    f"対象銘柄：{len(tickers)}銘柄　|　"
    f"初期資金：{initial_cash:,.0f}円　|　"
    f"目標：{target_cash:,.0f}円"
)


# =========================================================
# データ取得
# =========================================================
end_date = date.today() + timedelta(days=1)
start_date = date.today() - timedelta(days=365 * years + 250)

if run_button:
    if yf is None:
        st.error(
            "yfinanceが見つかりません。requirements.txtに"
            "yfinance>=0.2.54 を入れてください。"
        )
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    status.write("📥 株価データを取得しています…")

    try:
        raw = download_data_cached(
            tuple(tickers),
            start_date.isoformat(),
            end_date.isoformat(),
        )
    except Exception as e:
        st.error(f"データ取得エラー：{e}")
        st.stop()

    progress.progress(45)

    if not raw:
        st.error(
            "株価データを取得できませんでした。"
            "銘柄コード、ネット接続、yfinanceの状態を確認してください。"
        )
        st.stop()

    status.write("🧮 テクニカル指標を計算しています…")

    raw_items = tuple(
        (ticker, df)
        for ticker, df in raw.items()
        if df is not None and not df.empty
    )

    data_dict = prepare_all_data(raw_items)

    progress.progress(70)

    usable = {
        ticker: df
        for ticker, df in data_dict.items()
        if df is not None and not df.empty
    }

    st.session_state["data_dict"] = usable
    st.session_state["loaded_tickers"] = list(usable.keys())

    status.write("📊 バックテストを実行しています…")

    trades_df, equity_df, positions_df = run_backtest(
        usable,
        initial_cash=initial_cash,
        max_positions=int(max_positions),
        max_per_position=float(max_per_position),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        buy_score=int(buy_score),
        min_price=float(min_price),
        max_price=float(max_price),
    )

    progress.progress(100)
    status.write("✅ 完了しました。")

    stats = calculate_statistics(
        trades_df,
        equity_df,
        initial_cash,
    )

    st.session_state["trades_df"] = trades_df
    st.session_state["equity_df"] = equity_df
    st.session_state["positions_df"] = positions_df
    st.session_state["stats"] = stats


# =========================================================
# 結果表示
# =========================================================
if "data_dict" not in st.session_state:
    st.markdown(
        """
        ### 👋 Ver.4.0へようこそ

        左側で銘柄と条件を設定して、**「🚀 バックテスト開始」**を押してください。

        **最初のテストでは10～20銘柄程度がおすすめです。**

        動作確認後に対象銘柄数を増やします。
        """
    )

    st.info(
        "このVer.4.0はまず「高速で安定した土台」を作る版です。"
        "業績・ニュース・市況を使った総合AIスコアはVer.4.1以降で追加します。"
    )

    st.warning(
        "注意：バックテストの売買価格は「シグナル確定日の翌営業日始値」を"
        "使う近似モデルです。実際のS株の1日3回の約定タイミングを完全再現するものではありません。"
    )

    st.stop()


data_dict = st.session_state["data_dict"]
trades_df = st.session_state.get("trades_df", pd.DataFrame())
equity_df = st.session_state.get("equity_df", pd.DataFrame())
positions_df = st.session_state.get("positions_df", pd.DataFrame())
stats = st.session_state.get("stats", calculate_statistics(
    trades_df, equity_df, initial_cash
))


# =========================================================
# KPI
# =========================================================
st.subheader("📊 バックテスト結果")

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric(
    "最終資産",
    f"{stats['final']:,.0f}円",
    f"{stats['profit']:+,.0f}円",
)

c2.metric(
    "収益率",
    f"{stats['return_pct']:+.2f}%",
)

c3.metric(
    "最大DD",
    f"{stats['max_dd']:.2f}%",
)

c4.metric(
    "決済回数",
    f"{stats['trades']}回",
)

c5.metric(
    "勝率",
    f"{stats['win_rate']:.1f}%",
)


# 目標達成率
progress_value = min(
    100,
    max(
        0,
        stats["final"] / target_cash * 100,
    ),
)

st.progress(progress_value / 100)

st.caption(
    f"目標1,000,000円に対する進捗：{progress_value:.2f}%"
)


# =========================================================
# 最新の買い候補
# =========================================================
st.subheader("🔥 現在の買い候補")

latest_df = latest_candidates(
    data_dict,
    buy_score=int(buy_score),
    min_price=float(min_price),
    max_price=float(max_price),
    top_n=10,
)

if latest_df.empty:
    st.warning("現在の条件では候補銘柄がありません。買いスコアや株価条件を調整してください。")
else:
    st.dataframe(
        latest_df,
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# 資産曲線
# =========================================================
st.subheader("📈 資産推移")

if not equity_df.empty:
    chart_df = equity_df.set_index("Date")[["Equity"]]
    st.line_chart(chart_df)

    st.caption(
        "資産曲線は各営業日の終値ベース。売買は翌営業日始値の近似モデルです。"
    )
else:
    st.info("資産推移データがありません。")


# =========================================================
# 保有銘柄
# =========================================================
st.subheader("📦 バックテスト終了時の保有銘柄")

if positions_df.empty:
    st.info("バックテスト終了時に保有銘柄はありません。")
else:
    st.dataframe(
        positions_df,
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# 売買記録
# =========================================================
st.subheader("📝 売買記録")

if trades_df.empty:
    st.info(
        "売買がありません。買いスコアを下げる、対象銘柄を増やす、"
        "株価条件を調整するなどして再実行してください。"
    )
else:
    st.dataframe(
        trades_df.sort_values("Date", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

    csv = trades_df.to_csv(
        index=False,
        encoding="utf-8-sig",
    )

    st.download_button(
        "⬇️ 売買記録CSVをダウンロード",
        data=csv,
        file_name="ver4_0_trades.csv",
        mime="text/csv",
    )


# =========================================================
# データ状況
# =========================================================
with st.expander("🔎 データ取得状況"):
    status_rows = []

    for ticker, df in data_dict.items():
        if df.empty:
            continue

        status_rows.append(
            {
                "Ticker": ticker_display(ticker),
                "Start": str(df.index.min().date()),
                "End": str(df.index.max().date()),
                "Rows": len(df),
                "LastPrice": round(
                    safe_float(df.iloc[-1]["Close"]),
                    2,
                ),
            }
        )

    if status_rows:
        st.dataframe(
            pd.DataFrame(status_rows),
            use_container_width=True,
            hide_index=True,
        )

    st.write(
        "取得成功：",
        len(data_dict),
        "/",
        len(tickers),
        "銘柄",
    )


# =========================================================
# 重要な注意事項
# =========================================================
with st.expander("⚠️ Ver.4.0の重要な注意"):
    st.markdown(
        """
        **このシステムは投資判断の研究・仮想売買用です。**

        - 実際のSBI証券への注文は行いません。
        - 「必ず上がる銘柄」を予測するものではありません。
        - 過去のバックテスト結果は将来の利益を保証しません。
        - Ver.4.0ではニュース・企業業績・市況をまだ総合判定していません。
        - S株の実際の約定は1日3回で、Ver.4.0は「翌営業日始値」の近似モデルです。
        - 株式分割・併合、上場廃止、データ欠損等については、今後のバージョンでさらに精度を上げます。
        """
    )

st.caption(
    "日本株 10万円→100万円 AI投資アシスタント Ver.4.0 / 仮想売買専用"
)
