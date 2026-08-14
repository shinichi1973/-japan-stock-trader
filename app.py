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
# 日本株 自動バックテスト Ver.3.8
# 日経225 / 明けの明星削除 / 高速条件探索 / リスク評価
# =========================================================

st.set_page_config(
    page_title="日本株 自動バックテスト Ver.3.8",
    page_icon="📈",
    layout="wide"
)

st.title("📈 日本株 自動バックテスト Ver.3.8")
st.caption(
    "日経225を中心に、利益だけでなくリスク・安定性も含めて"
    "強い売買条件を自動探索します。実注文は行いません。"
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
    value=10
)

max_per_position = st.sidebar.number_input(
    "1銘柄の最大購入額（円）",
    min_value=10000,
    max_value=10000000,
    value=100000,
    step=10000
)

years = st.sidebar.selectbox(
    "バックテスト期間",
    [1, 3, 5],
    index=2
)

st.sidebar.header("🔬 自動探索")

search_mode = st.sidebar.selectbox(
    "探索モード",
    ["高速探索", "標準探索"],
    index=0
)

# 現時点の固定条件
default_rsi = st.sidebar.slider(
    "通常バックテスト RSI上限",
    50, 70, 60
)

default_sl = st.sidebar.slider(
    "通常バックテスト 損切り（%）",
    3, 15, 7
) / 100

default_tp = st.sidebar.slider(
    "通常バックテスト 利確（%）",
    5, 30, 15
) / 100

st.sidebar.header("🎯 条件")

use_price_2000 = st.sidebar.checkbox(
    "株価2,000円以上",
    value=False
)

use_ma = st.sidebar.checkbox(
    "25日線 ＞ 75日線 ＆ 株価 ＞ 25日線",
    value=False
)

use_volume = st.sidebar.checkbox(
    "出来高 ＞ 20日平均",
    value=False
)

use_rsi = st.sidebar.checkbox(
    "RSI ＜ 上限",
    value=True
)

st.sidebar.info(
    "明けの明星はVer.3.8から完全削除しています。"
)

# =========================================================
# 日経225取得
# =========================================================

NIKKEI_URL = (
    "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
)

@st.cache_data(ttl=86400, show_spinner=False)
def get_nikkei225():
    """
    日経225公式構成銘柄ページから4桁/英数字コードを取得。
    pandas.read_html()/lxmlには依存しません。
    """
    import urllib.request
    from html.parser import HTMLParser

    class NikkeiCodeParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.codes = []
            self.in_td = False

        def handle_starttag(self, tag, attrs):
            if tag.lower() == "td":
                self.in_td = True

        def handle_endtag(self, tag):
            if tag.lower() == "td":
                self.in_td = False

        def handle_data(self, data):
            if not self.in_td:
                return

            text = data.strip().upper().replace("\xa0", "")
            text = re.sub(r"\s+", "", text)

            # 現在の日経225には4桁コードだけでなく
            # 285Aのような英字入りコードも存在するため対応。
            if re.fullmatch(r"\d{4}[A-Z]?", text):
                if text not in self.codes:
                    self.codes.append(text)

    urls = [
        "https://indexes.nikkei.co.jp/en/nkave/index/component",
        "https://indexes.nikkei.co.jp/nkave/index/component"
    ]

    last_error = None

    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
                    )
                }
            )

            with urllib.request.urlopen(
                req,
                timeout=20
            ) as response:
                html = response.read().decode(
                    "utf-8",
                    errors="ignore"
                )

            parser = NikkeiCodeParser()
            parser.feed(html)

            codes = list(dict.fromkeys(parser.codes))

            # 公式ページのNikkei 225は225銘柄。
            # ページ構造変更等で余分なコードが拾われる可能性があるため、
            # 225前後の取得を確認する。
            if len(codes) >= 220:
                return codes[:225]

            last_error = (
                f"公式ページから取得できたコードが"
                f"{len(codes)}銘柄でした。"
            )

        except Exception as e:
            last_error = str(e)

    raise RuntimeError(
        "日経225公式ページから銘柄コードを取得できませんでした。"
        f" {last_error or ''}"
    )



st.subheader("🇯🇵 バックテスト対象")

try:
    nikkei_codes = get_nikkei225()
    nikkei_ok = True
except Exception as e:
    nikkei_codes = []
    nikkei_ok = False
    nikkei_error = str(e)

if nikkei_ok:
    st.success(
        f"✅ 日経225銘柄一覧を取得しました：{len(nikkei_codes)}銘柄"
    )
else:
    st.warning(
        "⚠️ 日経225銘柄一覧を自動取得できませんでした。"
        "下の入力銘柄でバックテストします。"
    )
    st.caption(f"取得エラー: {nikkei_error}")

fallback_text = st.text_area(
    "日経225取得失敗時・個別銘柄入力（カンマ区切り）",
    "7203,6758,9984,8306,9432,6752,6861,8035,4063"
)

fallback_codes = []
for x in fallback_text.replace("、", ",").replace(" ", ",").split(","):
    x = x.strip().upper().replace(".T", "")
    if x:
        fallback_codes.append(x)
fallback_codes = list(dict.fromkeys(fallback_codes))

target_codes = nikkei_codes if nikkei_ok else fallback_codes

st.write(
    f"対象銘柄数：**{len(target_codes)}銘柄**"
)

# =========================================================
# データ取得
# =========================================================

@st.cache_data(ttl=3600, show_spinner=False)
def download_data(codes, years):
    if yf is None:
        raise ImportError("yfinanceがインストールされていません。")

    tickers = [f"{x}.T" for x in codes]
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=years) - pd.Timedelta(days=100)

    raw = yf.download(
        tickers=tickers,
        start=start,
        end=end + pd.Timedelta(days=1),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker"
    )

    if raw is None or raw.empty:
        return pd.DataFrame()

    frames = []

    # 複数銘柄取得
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        level1 = set(raw.columns.get_level_values(1))

        # yfinanceのMultiIndex向きに両パターンへ対応
        for code in codes:
            tk = f"{code}.T"

            try:
                if tk in level0:
                    d = raw[tk].copy()
                elif tk in level1:
                    d = raw.xs(tk, axis=1, level=1).copy()
                else:
                    continue

                d = d.reset_index()

                rename = {}
                for c in d.columns:
                    s = str(c).lower()
                    if s == "date":
                        rename[c] = "date"
                    elif s == "open":
                        rename[c] = "open"
                    elif s == "high":
                        rename[c] = "high"
                    elif s == "low":
                        rename[c] = "low"
                    elif s == "close":
                        rename[c] = "close"
                    elif s == "volume":
                        rename[c] = "volume"

                d = d.rename(columns=rename)

                required = [
                    "date", "open", "high",
                    "low", "close", "volume"
                ]

                if not all(c in d.columns for c in required):
                    continue

                d = d[required].copy()
                d["ticker"] = code
                frames.append(d)

            except Exception:
                continue

    else:
        # 1銘柄時など
        d = raw.reset_index()
        rename = {}
        for c in d.columns:
            s = str(c).lower()
            if s == "date":
                rename[c] = "date"
            elif s == "open":
                rename[c] = "open"
            elif s == "high":
                rename[c] = "high"
            elif s == "low":
                rename[c] = "low"
            elif s == "close":
                rename[c] = "close"
            elif s == "volume":
                rename[c] = "volume"

        d = d.rename(columns=rename)
        required = [
            "date", "open", "high",
            "low", "close", "volume"
        ]

        if all(c in d.columns for c in required):
            d = d[required].copy()
            d["ticker"] = codes[0]
            frames.append(d)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    df["date"] = pd.to_datetime(
        df["date"], errors="coerce"
    ).dt.tz_localize(None)

    for c in [
        "open", "high", "low",
        "close", "volume"
    ]:
        df[c] = pd.to_numeric(
            df[c], errors="coerce"
        )

    df = df.dropna(
        subset=[
            "date", "open", "high",
            "low", "close"
        ]
    )

    df = df.sort_values(
        ["ticker", "date"]
    ).reset_index(drop=True)

    return df

# =========================================================
# 指標作成
# =========================================================

@st.cache_data(show_spinner=False)
def prepare_indicators(df):
    parts = []

    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("date").copy()

        g["ma25"] = g["close"].rolling(
            25, min_periods=25
        ).mean()

        g["ma75"] = g["close"].rolling(
            75, min_periods=75
        ).mean()

        delta = g["close"].diff()

        gain = delta.clip(lower=0).rolling(
            14, min_periods=14
        ).mean()

        loss = (-delta.clip(upper=0)).rolling(
            14, min_periods=14
        ).mean()

        rs = gain / loss.replace(0, np.nan)

        g["rsi"] = (
            100 - 100 / (1 + rs)
        )

        g["vol20"] = g["volume"].rolling(
            20, min_periods=20
        ).mean()

        g["ma_ok"] = (
            (g["ma25"] > g["ma75"])
            & (g["close"] > g["ma25"])
        )

        g["volume_ok"] = (
            g["volume"] > g["vol20"]
        )

        g["price2000_ok"] = (
            g["close"] >= 2000
        )

        parts.append(g)

    return pd.concat(
        parts, ignore_index=True
    ).sort_values(
        ["date", "ticker"]
    ).reset_index(drop=True)

# =========================================================
# 条件シグナル
# =========================================================

def make_signal(
    df,
    use_ma,
    use_volume,
    use_price2000,
    use_rsi,
    rsi_max
):
    s = pd.Series(
        True, index=df.index
    )

    valid = (
        df["ma25"].notna()
        & df["ma75"].notna()
        & df["rsi"].notna()
        & df["vol20"].notna()
    )

    s &= valid

    if use_ma:
        s &= df["ma_ok"]

    if use_volume:
        s &= df["volume_ok"]

    if use_price2000:
        s &= df["price2000_ok"]

    if use_rsi:
        s &= df["rsi"] < rsi_max

    return s.fillna(False)

# =========================================================
# バックテスト
# =========================================================

def run_backtest(
    df,
    use_ma,
    use_volume,
    use_price2000,
    use_rsi,
    rsi_max,
    stop_loss,
    take_profit,
    keep_trades=False
):
    cash = float(initial_cash)
    positions = {}
    trades = []
    curve = []

    df = df.copy()

    df["signal"] = make_signal(
        df,
        use_ma,
        use_volume,
        use_price2000,
        use_rsi,
        rsi_max
    )

    dates = df["date"].drop_duplicates().sort_values()

    for current_date in dates:
        day = df[df["date"] == current_date]

        # -----------------------------
        # 売却
        # -----------------------------
        for ticker in list(positions.keys()):
            row = day[day["ticker"] == ticker]

            if row.empty:
                continue

            r = row.iloc[0]
            price = float(r["close"])
            p = positions[ticker]

            ret = (
                price / p["entry_price"] - 1
            )

            reason = None

            if ret <= -stop_loss:
                reason = "損切り"
            elif ret >= take_profit:
                reason = "利確"
            elif (
                pd.notna(r["ma25"])
                and price < r["ma25"]
            ):
                reason = "25日線割れ"

            if reason:
                proceeds = (
                    p["shares"] * price
                )
                cash += proceeds

                pnl = (
                    price - p["entry_price"]
                ) * p["shares"]

                if keep_trades:
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

        # -----------------------------
        # 買い
        # -----------------------------
        if len(positions) < max_positions:
            candidates = day[day["signal"]].copy()

            # RSIが低い順 → 条件に余裕のある銘柄を優先
            if not candidates.empty:
                candidates = candidates.sort_values(
                    ["rsi", "volume"],
                    ascending=[True, False]
                )

            for _, r in candidates.iterrows():
                ticker = str(r["ticker"])

                if ticker in positions:
                    continue

                if len(positions) >= max_positions:
                    break

                price = float(r["close"])

                budget = min(
                    float(max_per_position),
                    cash
                )

                shares = (
                    int(
                        budget / (price * 100)
                    ) * 100
                )

                if shares <= 0:
                    continue

                cost = shares * price

                if cost > cash:
                    continue

                cash -= cost

                positions[ticker] = {
                    "shares": shares,
                    "entry_price": price
                }

                if keep_trades:
                    trades.append({
                        "date": current_date,
                        "ticker": ticker,
                        "side": "BUY",
                        "price": price,
                        "shares": shares,
                        "reason": "選定条件成立",
                        "pnl": 0.0
                    })

        # -----------------------------
        # 資産評価
        # -----------------------------
        market_value = 0.0

        if positions:
            held = day[
                day["ticker"].isin(
                    list(positions.keys())
                )
            ]

            prices = dict(
                zip(
                    held["ticker"],
                    held["close"]
                )
            )

            for ticker, p in positions.items():
                if ticker in prices:
                    market_value += (
                        p["shares"]
                        * float(prices[ticker])
                    )

        curve.append({
            "date": current_date,
            "equity": cash + market_value,
            "cash": cash,
            "positions": len(positions)
        })

    eq = pd.DataFrame(curve)

    tr = pd.DataFrame(trades)

    return eq, tr, positions

# =========================================================
# 成績
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
            "profit_factor": 0,
            "sharpe": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "score": -999
        }

    final_asset = float(eq.iloc[-1]["equity"])
    pnl = final_asset - float(initial_cash)
    return_rate = pnl / float(initial_cash)

    days = max(
        1,
        (
            pd.to_datetime(eq.iloc[-1]["date"])
            - pd.to_datetime(eq.iloc[0]["date"])
        ).days
    )

    years = days / 365.25

    if years > 0 and final_asset > 0:
        cagr = (
            final_asset / float(initial_cash)
        ) ** (1 / years) - 1
    else:
        cagr = 0.0

    rolling_max = eq["equity"].cummax()
    dd = eq["equity"] / rolling_max - 1
    max_dd = float(dd.min())

    daily_ret = eq["equity"].pct_change().replace(
        [np.inf, -np.inf], np.nan
    ).dropna()

    if (
        len(daily_ret) >= 20
        and daily_ret.std() > 0
    ):
        sharpe = (
            daily_ret.mean()
            / daily_ret.std()
            * np.sqrt(252)
        )
    else:
        sharpe = 0.0

    if tr.empty or "side" not in tr.columns:
        sells = pd.DataFrame()
    else:
        sells = tr[
            tr["side"] == "SELL"
        ].copy()

    if sells.empty:
        wins = pd.DataFrame()
        losses = pd.DataFrame()
    else:
        wins = sells[sells["pnl"] > 0]
        losses = sells[sells["pnl"] < 0]

    win_count = len(wins)
    loss_count = len(losses)
    closed = win_count + loss_count

    win_rate = (
        win_count / closed
        if closed > 0 else 0
    )

    gross_profit = (
        float(wins["pnl"].sum())
        if win_count else 0
    )

    gross_loss = abs(
        float(losses["pnl"].sum())
    ) if loss_count else 0

    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = 0.0 if gross_profit <= 0 else 5.0

    avg_win = (
        float(wins["pnl"].mean())
        if win_count else 0
    )

    avg_loss = (
        float(losses["pnl"].mean())
        if loss_count else 0
    )

    # -----------------------------------------------------
    # 総合スコア
    # 利益だけでなく、CAGR・DD・PF・Sharpeを重視
    # -----------------------------------------------------
    score = (
        cagr * 100
        + sharpe * 12
        + max(-max_dd, 0) * -80
        + min(pf, 3) * 5
        + win_rate * 5
    )

    # 極端に取引が少ない結果を少し減点
    if closed < 10:
        score -= 5

    return {
        "final_asset": final_asset,
        "pnl": pnl,
        "return_rate": return_rate,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "trades": closed,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "profit_factor": pf,
        "sharpe": sharpe,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "score": score
    }

# =========================================================
# 自動探索
# =========================================================

def make_search_grid(mode):
    if mode == "高速探索":
        rsi_values = [55, 60, 65, 70]
        sl_values = [0.05, 0.07, 0.10]
        tp_values = [0.10, 0.15, 0.20]
    else:
        rsi_values = [50, 55, 60, 65, 70]
        sl_values = [0.03, 0.05, 0.07, 0.10]
        tp_values = [0.10, 0.15, 0.20, 0.25]

    rows = []

    for ma, volume, price2000, rsi, sl, tp in product(
        [False, True],
        [False, True],
        [False, True],
        rsi_values,
        sl_values,
        tp_values
    ):
        rows.append({
            "MA": ma,
            "出来高": volume,
            "2000円": price2000,
            "RSI": rsi,
            "SL": sl,
            "TP": tp
        })

    return rows

# =========================================================
# 買い候補・売り候補
# =========================================================

def current_candidates(
    data,
    use_ma,
    use_volume,
    use_price2000,
    use_rsi,
    rsi_max
):
    if data.empty:
        return pd.DataFrame()

    last_date = data["date"].max()
    day = data[
        data["date"] == last_date
    ].copy()

    signal = make_signal(
        day,
        use_ma,
        use_volume,
        use_price2000,
        use_rsi,
        rsi_max
    )

    day["買いシグナル"] = signal

    buy = day[
        day["買いシグナル"]
    ].copy()

    if buy.empty:
        return pd.DataFrame()

    buy["スコア"] = (
        (70 - buy["rsi"].clip(upper=70))
        + buy["volume"].div(
            buy["vol20"].replace(0, np.nan)
        ).clip(upper=3) * 10
    )

    cols = [
        "ticker",
        "close",
        "rsi",
        "ma25",
        "ma75",
        "volume",
        "vol20",
        "スコア"
    ]

    return buy[
        [c for c in cols if c in buy.columns]
    ].sort_values(
        "スコア",
        ascending=False
    ).reset_index(drop=True)

# =========================================================
# 実行
# =========================================================

st.divider()

if st.button(
    "📥 日経225の株価データを取得",
    type="secondary",
    use_container_width=True
):
    if yf is None:
        st.error(
            "yfinanceがありません。requirements.txtに yfinance を追加してください。"
        )
        st.stop()

    with st.spinner(
        f"📥 {len(target_codes)}銘柄・過去{years}年の日足を取得中..."
    ):
        try:
            raw_df = download_data(
                tuple(target_codes),
                years
            )

            if raw_df.empty:
                st.error(
                    "株価データを取得できませんでした。"
                )
            else:
                data = prepare_indicators(raw_df)
                st.session_state["v38_data"] = data

                st.success(
                    f"✅ {len(data):,}行 / "
                    f"{data['ticker'].nunique()}銘柄を取得しました。"
                )
        except Exception as e:
            st.error(
                f"データ取得エラー: {e}"
            )

data = st.session_state.get(
    "v38_data",
    pd.DataFrame()
)

if not data.empty:

    st.write(
        f"📅 {data['date'].min():%Y-%m-%d}"
        f" ～ "
        f"{data['date'].max():%Y-%m-%d}"
    )

    st.write(
        f"📊 実際に取得できた銘柄："
        f"**{data['ticker'].nunique()}銘柄**"
    )

    with st.expander("📋 取得データ確認"):
        st.dataframe(
            data.tail(100),
            use_container_width=True,
            hide_index=True
        )

    # -----------------------------------------------------
    # 通常バックテスト
    # -----------------------------------------------------

    st.divider()
    st.header("📊 通常バックテスト")

    if st.button(
        "▶ 現在の設定でバックテスト",
        type="primary",
        use_container_width=True
    ):
        with st.spinner(
            "バックテストを計算中..."
        ):
            eq, tr, positions = run_backtest(
                data,
                use_ma,
                use_volume,
                use_price_2000,
                use_rsi,
                default_rsi,
                default_sl,
                default_tp,
                keep_trades=True
            )

        stats = calculate_stats(eq, tr)

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
            "最大DD",
            f"{stats['max_drawdown']:.2%}"
        )

        c5, c6, c7, c8 = st.columns(4)

        c5.metric(
            "勝率",
            f"{stats['win_rate']:.1%}"
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
            "総合スコア",
            f"{stats['score']:.2f}"
        )

        st.subheader("📈 資産推移")

        if not eq.empty:
            st.line_chart(
                eq.set_index("date")["equity"]
            )

        st.subheader("🧾 売買履歴")

        if tr.empty:
            st.info(
                "売買履歴はありません。"
            )
        else:
            display = tr.copy()
            display["date"] = pd.to_datetime(
                display["date"]
            ).dt.strftime("%Y-%m-%d")

            st.dataframe(
                display.sort_values(
                    "date",
                    ascending=False
                ),
                use_container_width=True,
                hide_index=True
            )

            csv = tr.to_csv(
                index=False
            ).encode("utf-8-sig")

            st.download_button(
                "⬇️ 売買履歴CSVを保存",
                data=csv,
                file_name="backtest_trades_ver3_8.csv",
                mime="text/csv"
            )

    # -----------------------------------------------------
    # 自動条件探索
    # -----------------------------------------------------

    st.divider()
    st.header("🔬 強い条件の自動探索")

    st.caption(
        "利益だけではなく、CAGR・最大DD・Profit Factor・"
        "Sharpe・勝率を組み合わせて総合評価します。"
    )

    grid = make_search_grid(
        search_mode
    )

    st.write(
        f"探索パターン：**{len(grid):,}通り**"
    )

    if st.button(
        "🔬 強い条件を自動探索",
        type="primary",
        use_container_width=True
    ):
        results = []

        progress = st.progress(0.0)
        status = st.empty()

        for i, p in enumerate(grid):

            status.write(
                f"探索中：{i + 1} / {len(grid)}"
            )

            eq, tr, _ = run_backtest(
                data,
                p["MA"],
                p["出来高"],
                p["2000円"],
                True,
                p["RSI"],
                p["SL"],
                p["TP"],
                keep_trades=True
            )

            s = calculate_stats(
                eq, tr
            )

            results.append({
                "MA": "ON" if p["MA"] else "OFF",
                "出来高": "ON" if p["出来高"] else "OFF",
                "2000円": "ON" if p["2000円"] else "OFF",
                "RSI": p["RSI"],
                "SL": f"-{p['SL']:.0%}",
                "TP": f"+{p['TP']:.0%}",
                "総損益": s["pnl"],
                "収益率": s["return_rate"],
                "CAGR": s["cagr"],
                "最大DD": s["max_drawdown"],
                "PF": s["profit_factor"],
                "Sharpe": s["sharpe"],
                "勝率": s["win_rate"],
                "決済数": s["trades"],
                "総合スコア": s["score"]
            })

            progress.progress(
                (i + 1) / len(grid)
            )

        status.empty()
        progress.empty()

        result_df = pd.DataFrame(
            results
        ).sort_values(
            "総合スコア",
            ascending=False
        ).reset_index(drop=True)

        st.session_state[
            "v38_search_results"
        ] = result_df

        st.success(
            "✅ 自動探索が完了しました。"
        )

    search_results = st.session_state.get(
        "v38_search_results",
        pd.DataFrame()
    )

    if not search_results.empty:

        st.subheader(
            "🏆 リスクを考慮した総合ランキング"
        )

        st.dataframe(
            search_results.head(20),
            use_container_width=True,
            hide_index=True
        )

        best = search_results.iloc[0]

        st.success(
            f"🏆 総合1位："
            f"MA {best['MA']} / "
            f"出来高 {best['出来高']} / "
            f"2000円 {best['2000円']} / "
            f"RSI≤{best['RSI']} / "
            f"SL {best['SL']} / "
            f"TP {best['TP']}\n\n"
            f"総損益：¥{best['総損益']:,.0f} / "
            f"CAGR：{best['CAGR']:.2%} / "
            f"最大DD：{best['最大DD']:.2%} / "
            f"PF：{best['PF']:.2f} / "
            f"Sharpe：{best['Sharpe']:.2f} / "
            f"総合スコア：{best['総合スコア']:.2f}"
        )

        csv = search_results.to_csv(
            index=False
        ).encode("utf-8-sig")

        st.download_button(
            "⬇️ 条件探索ランキングCSV",
            data=csv,
            file_name="ver3_8_condition_ranking.csv",
            mime="text/csv"
        )

    # -----------------------------------------------------
    # 最新日の買い候補
    # -----------------------------------------------------

    st.divider()
    st.header("🟢 最新日の買い候補")

    if st.button(
        "🔎 買い候補を抽出",
        use_container_width=True
    ):
        candidates = current_candidates(
            data,
            use_ma,
            use_volume,
            use_price_2000,
            use_rsi,
            default_rsi
        )

        if candidates.empty:
            st.warning(
                "現在の条件では買い候補がありません。"
            )
        else:
            candidates = candidates.rename(
                columns={
                    "ticker": "銘柄",
                    "close": "終値",
                    "rsi": "RSI",
                    "ma25": "25日線",
                    "ma75": "75日線",
                    "volume": "出来高",
                    "vol20": "出来高20日平均"
                }
            )

            st.dataframe(
                candidates,
                use_container_width=True,
                hide_index=True
            )

            st.info(
                "これは前日の日足データを基準にした候補抽出です。"
                "実際の売買判断・発注は行いません。"
            )

    # -----------------------------------------------------
    # 将来の売買監視用
    # -----------------------------------------------------

    st.divider()
    st.header("🔴 売却監視について")

    st.info(
        "Ver.3.8では実注文を行いません。"
        "今後、保有銘柄を登録して、損切り・利確・25日線割れを"
        "毎朝自動判定する機能へ発展させます。"
    )

else:
    st.info(
        "まず「📥 日経225の株価データを取得」を押してください。"
    )

st.divider()

st.caption(
    "Ver.3.8 / 仮想売買専用。証券会社への実注文は行いません。"
)

st.caption(
    "過去データによるシミュレーションであり、"
    "将来の利益を保証するものではありません。"
)
