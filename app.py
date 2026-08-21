import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.5.5 RC3.8",
    page_icon="📈",
    layout="wide"
)

# =========================================================
# 銘柄名
# =========================================================
STOCK_NAMES = {
    "7203":"トヨタ自動車","6758":"ソニーグループ","9984":"ソフトバンクグループ",
    "8306":"三菱UFJフィナンシャル・グループ","9432":"NTT","6501":"日立製作所",
    "8035":"東京エレクトロン","8058":"三菱商事","7267":"ホンダ","2914":"JT",
    "9433":"KDDI","8316":"三井住友フィナンシャルグループ","8411":"みずほフィナンシャルグループ",
    "6098":"リクルートホールディングス","4063":"信越化学工業","4519":"中外製薬",
    "6367":"ダイキン工業","6857":"アドバンテスト","7974":"任天堂","8766":"東京海上ホールディングス",
    "5401":"日本製鉄","8801":"三井不動産","8802":"三菱地所","4502":"武田薬品工業",
    "4503":"アステラス製薬","4523":"エーザイ","4755":"楽天グループ","6594":"ニデック",
    "7741":"HOYA","6981":"村田製作所","3382":"セブン＆アイ・ホールディングス",
    "4661":"オリエンタルランド","6146":"ディスコ","6920":"レーザーテック",
    "7832":"バンダイナムコホールディングス","4568":"第一三共","4452":"花王",
    "6503":"三菱電機","6701":"NEC","6702":"富士通",

    # 今回追加：実保有銘柄
    "3444":"菊池製作所","5885":"ジーデップ・アドバンス",
    "6324":"ハーモニック・ドライブ・システムズ","6506":"安川電機",
    "6629":"テクノホライゾン","6954":"ファナック","6965":"浜松ホトニクス",
    "7012":"川崎重工業",

    # 今回追加：重点監視
    "6085":"アーキテクツ・スタジオ・ジャパン"
}

# =========================================================
# 初期ユニバース
# =========================================================
DEFAULT = (
    "7203,6758,9984,8306,9432,6501,8035,8058,7267,2914,"
    "9433,8316,8411,6098,4063,4519,6367,6857,7974,8766,"
    "5401,8801,8802,4502,4503,4523,4755,6594,7741,6981,"
    "3444,5885,6324,6506,6629,6702,6954,6965,7012,9432,"
    "9984,6085"
)

# 実際の保有銘柄
HELD_DEFAULT = (
    "3444,5885,6324,6501,6506,6629,"
    "6702,6954,6965,7012,9432,9984"
)

# SBI画面から読み取った取得単価
ENTRY_DEFAULT = (
    "3444:992,5885:2749,6324:6870,6501:4854,"
    "6506:5335,6629:1023,6702:3244,6954:6938,"
    "6965:2548,7012:2781,9432:151,9984:5918"
)

# 実保有＋重点監視は流動性TOP50から外れても分析対象に残す
WATCH_CODES = {
    "3444","5885","6324","6501","6506","6629",
    "6702","6954","6965","7012","9432","9984",
    "6085"
}

HELD_CODES = {
    "3444","5885","6324","6501","6506","6629",
    "6702","6954","6965","7012","9432","9984"
}

# RC3.8検証パラメータ
# 90+は過去データでPFが弱かったため、無条件に最高評価として扱わない。
# 85-90を基準帯、90+はスコアを0.90倍して過信を抑制する。
RC33_OVER90_SCORE_FACTOR = 0.90
RC33_OVER90_BUDGET_FACTOR = 0.85
RC33_STALE_DAYS = 10


def code(t):
    return t.replace(".T", "")


def name(t):
    return STOCK_NAMES.get(code(t), code(t))


def tickers(s):
    return list(dict.fromkeys([
        x.strip() if x.strip().endswith(".T") else x.strip()+".T"
        for x in s.replace("\n", ",").split(",")
        if x.strip()
    ]))


def parse_codes(s):
    return [
        x.strip().replace(".T", "")
        for x in s.replace("\n", ",").split(",")
        if x.strip()
    ]


def parse_entries(s):
    d = {}
    for x in s.replace("\n", ",").split(","):
        if ":" in x:
            a, b = x.split(":", 1)
            try:
                d[a.strip().replace(".T", "")] = float(b)
            except Exception:
                pass
    return d


def csv_bytes(df):
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


# =========================================================
# 株価データ
# =========================================================
@st.cache_data(ttl=3600)
def stock_data(t, years=5):
    """5年日足取得。6085などでYahoo側の取得終端が古い場合は再取得を試す。"""
    end = datetime.now()
    start = end - timedelta(days=365*years+300)

    def normalize(df):
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cols = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in cols):
            return pd.DataFrame()
        df = df[cols].copy()
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=cols)
        if df.empty:
            return pd.DataFrame()
        df["MA25"] = df.Close.rolling(25).mean()
        df["MA75"] = df.Close.rolling(75).mean()
        df["MA200"] = df.Close.rolling(200).mean()
        df["MA25_Slope"] = df.MA25 - df.MA25.shift(5)
        df["MA75_Slope"] = df.MA75 - df.MA75.shift(5)
        df["VOL20"] = df.Volume.rolling(20).mean()
        df["Turnover"] = df.Close * df.Volume
        delta = df.Close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI"] = 100 - (100/(1+rs))
        df["Return_5d"] = df.Close.pct_change(5) * 100
        df["Return_25d"] = df.Close.pct_change(25) * 100
        df["Volume_Ratio"] = df.Volume / df.VOL20.replace(0, np.nan)
        return df.dropna()

    try:
        df = yf.download(
            t, start=start, end=end+timedelta(days=1),
            auto_adjust=False, progress=False, threads=False
        )
        out = normalize(df)

        # RC3.8: 6085でYahoo downloadの終端が古い場合、Ticker.historyで再取得。
        if code(t) == "6085" and not out.empty:
            latest = pd.Timestamp(out.index.max()).tz_localize(None) if getattr(out.index.max(), 'tzinfo', None) else pd.Timestamp(out.index.max())
            if (pd.Timestamp(end.date()) - latest).days > RC33_STALE_DAYS:
                try:
                    alt = yf.Ticker(t).history(
                        period="max", auto_adjust=False, actions=False
                    )
                    alt = normalize(alt)
                    if not alt.empty:
                        alt_latest = pd.Timestamp(alt.index.max()).tz_localize(None) if getattr(alt.index.max(), 'tzinfo', None) else pd.Timestamp(alt.index.max())
                        if alt_latest > latest:
                            out = alt
                except Exception:
                    pass

        return out
    except Exception:
        try:
            alt = yf.Ticker(t).history(
                start=start, end=end+timedelta(days=1),
                auto_adjust=False, actions=False
            )
            return normalize(alt)
        except Exception:
            return pd.DataFrame()


@st.cache_data(ttl=3600)
def market_data():
    end = datetime.now()
    start = end - timedelta(days=365*5+300)

    try:
        df = yf.download(
            "^N225",
            start=start,
            end=end+timedelta(days=1),
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        c = pd.to_numeric(df["Close"], errors="coerce")

        o = pd.DataFrame({"Close": c})
        o["MA25"] = c.rolling(25).mean()
        o["MA75"] = c.rolling(75).mean()
        o["MA200"] = c.rolling(200).mean()
        o["MA25_Slope"] = o.MA25 - o.MA25.shift(5)

        return o.dropna()

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def overseas_data():
    end = datetime.now()
    start = end - timedelta(days=365*5+300)

    symbols = {
        "S&P500":"^GSPC",
        "NASDAQ":"^IXIC",
        "NYダウ":"^DJI",
        "SOX":"^SOX",
        "USDJPY":"USDJPY=X",
        "米10年金利":"^TNX"
    }

    out = {}

    for label, symbol in symbols.items():
        try:
            df = yf.download(
                symbol,
                start=start,
                end=end+timedelta(days=1),
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                out[label] = pd.to_numeric(
                    df["Close"],
                    errors="coerce"
                )

        except Exception:
            pass

    return (
        pd.concat(out, axis=1).sort_index().ffill()
        if out else pd.DataFrame()
    )


def overseas_snapshot(overseas, dt):
    base = {
        "海外為替判定":"⚪ 海外データなし",
        "海外為替係数":0.60,
        "S&P500_5d":np.nan,
        "NASDAQ_5d":np.nan,
        "SOX_5d":np.nan,
        "USDJPY_5d":np.nan,
        "US10Y_5d":np.nan,
        "sox_score":0.0,
        "fx_score":0.0
    }

    if overseas.empty:
        return base

    x = overseas[overseas.index <= pd.Timestamp(dt)]

    if x.empty:
        return base

    def r5(col):
        if col not in x.columns:
            return np.nan

        s = x[col].dropna()

        if len(s) < 6:
            return np.nan

        return float((s.iloc[-1]/s.iloc[-6]-1)*100)

    sp, nq, sox, fx, rate = [
        r5(c)
        for c in ["S&P500","NASDAQ","SOX","USDJPY","米10年金利"]
    ]

    us = (
        (1 if np.isfinite(sp) and sp > 0 else 0)
        +
        (1 if np.isfinite(nq) and nq > 0 else 0)
    )

    sox_s = (
        1 if np.isfinite(sox) and sox > 0
        else -1 if np.isfinite(sox) and sox < 0
        else 0
    )

    fx_s = (
        1 if np.isfinite(fx) and fx > 0
        else -1 if np.isfinite(fx) and fx < 0
        else 0
    )

    rate_s = (
        -1 if np.isfinite(rate) and rate > 3
        else 1 if np.isfinite(rate) and rate < -3
        else 0
    )

    raw = us*.35 + sox_s*.20 + fx_s*.30 + rate_s*.15
    factor = float(np.clip(.75 + raw*.25, .45, 1.15))

    state = (
        "🟢 海外・為替 良好" if factor >= 1.03
        else "🟡 海外・為替 やや良好" if factor >= .90
        else "⚪ 海外・為替 中立" if factor >= .72
        else "🔴 海外・為替 注意"
    )

    return {
        "海外為替判定":state,
        "海外為替係数":factor,
        "S&P500_5d":sp,
        "NASDAQ_5d":nq,
        "SOX_5d":sox,
        "USDJPY_5d":fx,
        "US10Y_5d":rate,
        "sox_score":float(sox_s),
        "fx_score":float(fx_s)
    }


# =========================================================
# AIスコア
# =========================================================
def score_band_policy(score, market_state, overseas_factor):
    """RC3.8 experimental score-band policy.

    85-90 is treated as the reference band. 90+ receives a 10%
    confidence reduction because RC3.8 historical analysis showed
    materially weaker PF in that band. This is a testable correction,
    not a permanent rule.
    """
    if 82 <= score < 85:
        band = "82-85"
    elif 80 <= score < 82:
        band = "80-82"
    elif 85 <= score < 90:
        band = "85-90"
    elif score >= 90:
        band = "90+"
    else:
        band = "<80"

    conf = {
        "80-82": 1.00,
        "82-85": 1.00,
        "85-90": 1.00,
        "90+": RC33_OVER90_SCORE_FACTOR
    }.get(band, 1.0)

    if overseas_factor < 0.50:
        conf *= 0.92

    return band, float(conf)


def score_band_budget_factor(band):
    """RC3.8: 90+ is also position-size cautious; other bands unchanged."""
    return RC33_OVER90_BUDGET_FACTOR if band == "90+" else 1.0


def sector_overseas_bonus(ticker, snap):
    c = code(ticker)

    semiconductor = {
        "8035","6857","6146","6920","4063","6981"
    }

    exporters = {
        "7203","7267","6501","6503","6758",
        "6594","6367","7741"
    }

    financials = {
        "8306","8316","8411","8766"
    }

    bonus = 0.0

    if c in semiconductor:
        bonus += (
            1.5 * snap.get("sox_score", 0)
            if "sox_score" in snap else 0
        )

    elif c in exporters:
        bonus += 1.5 * (
            1 if snap.get("USDJPY_5d",0) > 0
            else -1 if snap.get("USDJPY_5d",0) < 0
            else 0
        )

    elif c in financials:
        bonus += .75 * (
            1 if snap.get("US10Y_5d",0) < 0
            else -1 if snap.get("US10Y_5d",0) > 0
            else 0
        )

    return float(np.clip(bonus, -6, 6))


def tech_components(r, lo, hi):
    return {
        "MA25>MA75":20 * int(r.MA25 > r.MA75),
        "Close>MA200":20 * int(r.Close > r.MA200),
        "Close>MA25":15 * int(r.Close > r.MA25),
        "Volume>VOL20":15 * int(r.Volume > r.VOL20),
        "RSI":15 * int(lo <= r.RSI <= hi),
        "MA25_Slope":10 * int(r.MA25_Slope > 0),
        "MA75_Slope":5 * int(r.MA75_Slope > 0)
    }


def tech(r, lo, hi):
    return float(sum(tech_components(r, lo, hi).values()))


def market_info(m, d):
    if m.empty:
        return ("⚪ データなし",60,.60)

    x = m[m.index <= pd.Timestamp(d)]

    if x.empty:
        return ("⚪ データなし",60,.60)

    r = x.iloc[-1]

    p = sum([
        r.Close > r.MA25,
        r.MA25 > r.MA75,
        r.MA75 > r.MA200,
        r.MA25_Slope > 0
    ])

    return [
        ("🔴 弱気",0,0),
        ("🟠 やや弱気",35,.35),
        ("⚪ 中立",60,.60),
        ("🟡 やや強気",84,.84),
        ("🟢 強気",100,1.0)
    ][p]


def next_trade_date(index, dt):
    idx = pd.DatetimeIndex(index)
    pos = idx.searchsorted(pd.Timestamp(dt), side="right")

    return idx[pos] if pos < len(idx) else None


def risk_factor_from_losses(losses):
    return (
        .30 if losses >= 9
        else .50 if losses >= 7
        else 1.00
    )


def is_blocked(dt, block_until, severe_block_until):
    return (
        (block_until is not None and dt <= block_until)
        or
        (severe_block_until is not None and dt <= severe_block_until)
    )


def confidence(s):
    if s["trades"] < 8:
        return 1.0

    wr = s["wins"]/s["trades"]
    pf = s["gp"]/s["gl"] if s["gl"] else 9.99

    if wr >= .55 and pf >= 1.30:
        return 1.15
    if wr >= .48 and pf >= 1.10:
        return 1.08
    if wr >= .40 and pf >= .90:
        return 1.00
    if wr >= .30 and pf >= .70:
        return .82

    return .65


def conf_points(c):
    return float(np.clip((c-.65)/.50*100, 0, 100))


def factor(s):
    return (
        1.0 if s >= 85
        else .85 if s >= 75
        else .70 if s >= 65
        else .50 if s >= 55
        else 0
    )


def recent_loss_penalty(s):
    losses = int(s.get("recent_losses", 0))

    if losses >= 3:
        return .82
    if losses == 2:
        return .90
    if losses == 1:
        return .96

    return 1.00


def stock_quality(s):
    """
    完了済みトレードだけを使用して銘柄別期待値を評価。
    実績不足の銘柄は不当に減点しない。
    """

    n = int(s.get("trades", 0))
    wins = int(s.get("wins", 0))
    gp = float(s.get("gp", 0.0))
    gl = float(s.get("gl", 0.0))
    recent = int(s.get("recent_losses", 0))

    wr = wins/n if n else 0.0
    pf = gp/gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    avg = (gp-gl)/n if n else 0.0

    if n < 8:
        return 1.00, False, "実績不足（中立）", wr, pf, avg

    if n >= 12 and pf < .85 and avg < 0:
        return 0.00, True, "過去PF不良・期待値マイナス", wr, pf, avg

    if n >= 20 and wr < .30 and avg < 0:
        return 0.00, True, "過去勝率不良・期待値マイナス", wr, pf, avg

    q = 1.00
    reason = "実績許容"

    if pf < .95 or avg < 0:
        q *= .78
        reason = "過去実績を減点"

    elif pf >= 1.20 and avg > 0 and wr >= .40:
        q *= 1.08
        reason = "過去実績を加点"

    elif pf >= 1.00 and avg >= 0:
        q *= 1.03
        reason = "過去実績はプラス"

    if recent >= 3:
        q *= .88
        reason += "・直近連敗"

    return float(np.clip(q, 0.0, 1.08)), False, reason, wr, pf, avg



# =========================================================
# 急騰予兆AI / ニュースレーダー
# =========================================================
def news_signal(ticker):
    """現在取得できるYahoo Financeニュースだけを補助情報として評価。
    過去バックテストには使用せず、未来情報混入を防ぐ。
    """
    positive = [
        "上方修正", "増額", "黒字", "最高益", "増益", "受注", "提携",
        "業務提携", "資本提携", "買収", "TOB", "自社株買い", "復配",
        "増配", "新製品", "大型受注", "採用", "契約", "材料", "上場"
    ]
    negative = [
        "下方修正", "減額", "赤字", "減益", "債務超過", "希薄化",
        "増資", "第三者割当", "売出し", "特損", "不適切", "監査",
        "継続企業", "業績悪化", "配当減", "無配"
    ]
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []

    score = 0
    headlines = []
    for item in items[:8]:
        title = str(item.get("title", ""))
        if not title:
            continue
        ps = sum(title.count(k) for k in positive)
        ns = sum(title.count(k) for k in negative)
        score += min(ps, 2) * 8
        score -= min(ns, 2) * 10
        headlines.append(title)

    score = int(np.clip(score, -20, 30))
    state = "🟢 材料良好" if score >= 16 else "🟡 材料あり" if score >= 8 else "⚪ 材料中立" if score > -8 else "🔴 材料注意"
    return score, state, headlines[:3]


def surge_radar_row(t, d, use_news=True):
    """急騰予兆は通常BUYと別系統。WATCH用途でありBUY判定には直接使用しない。"""
    r = d.iloc[-1]
    p = float(r.Close)
    if p <= 0 or p >= 2000:
        return None

    ret5 = float(r.Return_5d)
    ret25 = float(r.Return_25d)
    vr = float(r.Volume_Ratio)
    ma25_gap = (p / float(r.MA25) - 1) * 100 if float(r.MA25) else np.nan
    slope = float(r.MA25_Slope)

    # 先回り寄り：出来高・価格・トレンド転換の組み合わせを評価。
    vol_pts = 30 if vr >= 3 else 22 if vr >= 2 else 14 if vr >= 1.5 else 6 if vr >= 1.2 else 0
    ret_pts = 24 if 8 <= ret5 <= 25 else 18 if 5 <= ret5 < 8 else 10 if 0 <= ret5 < 5 else 6 if ret5 > 25 else 0
    trend_pts = 18 if slope > 0 and p > r.MA25 else 10 if slope > 0 else 0

    prior20 = d.Close.shift(1).rolling(20).max().iloc[-1] if len(d) >= 21 else np.nan
    breakout_pts = 18 if np.isfinite(prior20) and p > prior20 else 0

    # 急騰し過ぎは予兆としては検出するが、過熱リスクを別途減点。
    overheat_penalty = 18 if ret5 >= 30 or r.RSI >= 80 else 10 if ret5 >= 20 or r.RSI >= 75 else 0

    news_score, news_state, headlines = (news_signal(t) if use_news else (0, "⚪ ニュース未取得", []))
    raw = vol_pts + ret_pts + trend_pts + breakout_pts + max(news_score, 0) - overheat_penalty
    score = int(np.clip(raw, 0, 100))

    if score >= 70:
        state = "🚨 強い急騰予兆"
    elif score >= 55:
        state = "🟠 急騰予兆"
    elif score >= 40:
        state = "🟡 変化検知"
    else:
        state = "⚪ 通常"

    return {
        "コード": code(t), "銘柄名": name(t), "株価": p,
        "急騰予兆スコア": score, "急騰予兆判定": state,
        "5日騰落率": ret5, "25日騰落率": ret25,
        "出来高倍率": vr, "MA25乖離率": ma25_gap,
        "MA25傾き": slope, "RSI": float(r.RSI),
        "出来高ポイント": vol_pts, "騰落率ポイント": ret_pts,
        "トレンドポイント": trend_pts, "ブレイクポイント": breakout_pts,
        "ニュースポイント": news_score, "過熱減点": overheat_penalty,
        "ニュース判定": news_state,
        "ニュース見出し": " | ".join(headlines),
        "重点監視": code(t) in WATCH_CODES,
        "6085特別監視": code(t) == "6085",
        "実保有": code(t) in HELD_CODES,
        "先回り用途": True,
        "通常BUYとは別判定": True,
    }

# =========================================================
# サイドバー
# =========================================================
with st.sidebar:
    st.header("⚙️ 詳細設定")

    initial = st.number_input(
        "初期資金（円）",
        10000,10000000,100000,10000
    )

    maxpos = st.number_input(
        "最大保有銘柄数",
        1,50,7
    )

    maxbuy = st.number_input(
        "1銘柄最大購入額（円）",
        1000,1000000,10000,1000
    )

    sl = st.slider(
        "損切り（%）",
        3.0,12.0,6.0,.5
    )

    tp = st.slider(
        "利確（%）",
        8.0,40.0,15.0,1.0
    )

    rlo = st.slider("RSI下限",25,60,40)
    rhi = st.slider("RSI上限",60,80,70)

    mintech = st.slider(
        "最低テクニカルスコア",
        60,90,75
    )

    minbuy_score = st.slider(
        "BUY最低AIスコア",
        70,90,80
    )

    st.caption(
        "RC3.8ではBUY条件と実績フィルターの整合性を修正。"
        "実保有12銘柄＋6085を重点監視します。"
    )

    cooldown = st.number_input(
        "4連敗後の新規BUY停止日数",
        5,30,10
    )

    risk_cooldown = st.number_input(
        "9連敗後の新規BUY停止日数",
        5,45,15
    )

    severe_cooldown = st.number_input(
        "10連敗後の新規BUY停止日数",
        10,60,20
    )

    max_gap = st.slider(
        "翌営業日寄付ギャップ許容（%）",
        1.0,10.0,5.0,.5
    )

    use_liq = st.checkbox(
        "過去5年平均売買代金TOP50を使用",
        True
    )

    use_news_radar = st.checkbox(
        "急騰予兆AIで現在ニュースも取得",
        True
    )

    universe = st.text_area(
        "分析対象銘柄コード",
        DEFAULT
    )

    held = st.text_area(
        "現在保有している銘柄コード",
        HELD_DEFAULT
    )

    entries = st.text_area(
        "取得単価（例：7203:1500）",
        ENTRY_DEFAULT
    )


# =========================================================
# メイン画面
# =========================================================
st.title(
    "📈 日本株 AI投資アシスタント Ver.5.5 RC3.8"
)

st.caption(
    "RC3.8検証版：通常AIは根拠重視、別系統で「急騰予兆AI」を搭載。出来高・値動き・トレンド転換・現在ニュースを検知します。"
    "BUY最低AIスコア条件を実際の判定にも反映。"
)

st.caption(
    "🌅 朝イチは「買う・売る・何もしない」だけを確認｜"
    "米国市場・為替は裏側で評価｜条件不足なら無理にBUYしません"
)

st.caption(
    "🛡️ BUYシグナルは当日終値で確定し、"
    "各銘柄の次回取引日の寄付で仮想約定。"
    "寄付ギャップ急騰・急落は見送ります。"
)

st.caption(
    "🔎 6085は『買い推奨』ではなく、"
    "ネット上の急騰・大化け説をデータで検証する重点監視銘柄です。"
)


with st.spinner(
    "🧠 裏側で5年間のAI分析・バックテストを実行中…"
):
    data = {
        t:stock_data(t)
        for t in tickers(universe)
    }

    data = {
        t:d for t,d in data.items()
        if not d.empty
    }

    market = market_data()
    overseas = overseas_data()


# =========================================================
# 流動性
# =========================================================
liq = pd.DataFrame([
    {
        "コード":code(t),
        "銘柄名":name(t),
        "平均売買代金":d.Turnover.mean(),
        "平均出来高":d.Volume.mean()
    }
    for t,d in data.items()
])

if not liq.empty:
    liq = (
        liq.sort_values(
            "平均売買代金",
            ascending=False
        ).reset_index(drop=True)
    )

    liq["売買代金順位"] = liq.index + 1
    liq["売買代金TOP50"] = liq["売買代金順位"] <= 50

liq_codes = (
    set(
        liq.loc[
            liq["売買代金TOP50"],
            "コード"
        ]
    )
    if not liq.empty
    else set()
)


# =========================================================
# バックテスト
# =========================================================
cash = float(initial)
pos = {}

stats = {
    t:{
        "trades":0,
        "wins":0,
        "gp":0.0,
        "gl":0.0,
        "recent_losses":0
    }
    for t in data
}

trades = []
analyses = []
equity = []

losses = 0
maxloss = 0

block_until = None
severe_block_until = None

pending_buys = {}
pending_tickers = set()

dates = sorted(
    set(
        x
        for d in data.values()
        for x in d.index
    )
)


for dt in dates:

    # -----------------------------------------------------
    # 1. 前日のBUYシグナルを翌営業日寄付で約定
    # -----------------------------------------------------
    due = pending_buys.pop(dt, [])

    for order in due:
        t = order["ticker"]

        pending_tickers.discard(t)

        if (
            t not in data
            or dt not in data[t].index
            or t in pos
        ):
            continue

        r = data[t].loc[dt]
        p = float(r.Open)

        signal_close = float(
            order["signal_close"]
        )

        gap_pct = (
            (p/signal_close-1)*100
            if signal_close > 0
            else 999.0
        )

        if (
            not np.isfinite(p)
            or p <= 0
            or p >= 2000
        ):
            continue

        if abs(gap_pct) > order["max_gap_pct"]:
            continue

        blocked = is_blocked(
            dt,
            block_until,
            severe_block_until
        )

        if (
            blocked
            or len(pos) >= maxpos
            or order["market_factor"] <= 0
        ):
            continue

        risk_factor = risk_factor_from_losses(
            losses
        )

        budget = (
            min(maxbuy, cash)
            * factor(order["score"])
            * order.get("score_band_budget_factor", 1.0)
            * risk_factor
        )

        shares = int(budget/p)

        if shares <= 0:
            continue

        cost = shares*p

        if cost > cash:
            continue

        cash -= cost

        pos[t] = {
            "entry":p,
            "shares":shares
        }

        trades.append({
            "日付":dt,
            "コード":code(t),
            "銘柄名":name(t),
            "売買":"BUY",
            "価格":p,
            "株数":shares,
            "損益":0,
            "損益率":0,
            "理由":"Ver.5.5 RC3.8 AI BUY（翌営業日寄付約定）",
            "シグナル日":order["signal_date"],
            "テクニカルスコア":order["ts"],
            "総合AIスコア":order["score"],
            "元スコア帯":order.get("score_band", ""),
            "90+過信補正係数":order.get("score_band_conf", 1.0),
            "90+資金係数":order.get("score_band_budget_factor", 1.0),
            "銘柄実績信頼度":order["hc"],
            "市場判定":order["market_state"],
            "銘柄期待値係数":order.get("qfactor",1.0),
            "海外為替判定":order.get("overseas_state",""),
            "海外為替係数":order.get("overseas_factor",1.0),
            "海外補正":order.get("overseas_bonus",0.0),
            "過去勝率":order.get("wr_hist",0)*100,
            "過去PF":order.get("pf_hist",0),
            "過去平均損益":order.get("avg_hist",0),
            "購入資金係数":factor(order["score"]),
            "連敗リスク係数":risk_factor,
            "シグナル終値":signal_close,
            "寄付ギャップ率":gap_pct,
            "重点監視銘柄":code(t) in WATCH_CODES,
            "6085特別監視":code(t) == "6085",
            "実保有銘柄":code(t) in HELD_CODES,
            "未来情報使用":False
        })


    # -----------------------------------------------------
    # 2. 保有ポジションを今日の終値で評価
    # -----------------------------------------------------
    for t in list(pos):

        if dt not in data[t].index:
            continue

        r = data[t].loc[dt]
        p = float(r.Close)

        q = pos[t]

        pnl = (
            p-q["entry"]
        ) * q["shares"]

        pct = (
            p/q["entry"]-1
        ) * 100

        ma25_confirm = (
            p < r.MA25
            and (
                r.MA25_Slope < 0
                or tech(r,rlo,rhi) < 60
            )
        )

        reason = (
            "損切り"
            if pct <= -sl
            else
            "利確"
            if pct >= tp
            else
            "25日線割れ確認"
            if ma25_confirm
            else None
        )

        if reason:

            cash += p*q["shares"]

            s = stats[t]

            s["trades"] += 1

            if pnl > 0:
                s["wins"] += 1
                s["gp"] += pnl
                s["recent_losses"] = 0
                losses = 0

            else:
                s["gl"] += abs(pnl)
                s["recent_losses"] += 1
                losses += 1
                maxloss = max(maxloss, losses)

                if losses >= 10:
                    severe_block_until = (
                        dt
                        + pd.tseries.offsets.BDay(
                            severe_cooldown
                        )
                    )

                elif losses >= 9:
                    block_until = (
                        dt
                        + pd.tseries.offsets.BDay(
                            risk_cooldown
                        )
                    )

                elif losses >= 4:
                    block_until = (
                        dt
                        + pd.tseries.offsets.BDay(
                            cooldown
                        )
                    )

            trades.append({
                "日付":dt,
                "コード":code(t),
                "銘柄名":name(t),
                "売買":"SELL",
                "価格":p,
                "株数":q["shares"],
                "損益":pnl,
                "損益率":pct,
                "理由":reason,
                "重点監視銘柄":code(t) in WATCH_CODES,
                "6085特別監視":code(t) == "6085",
                "実保有銘柄":code(t) in HELD_CODES,
                "未来情報使用":False,
                "連敗数":losses
            })

            del pos[t]


    # -----------------------------------------------------
    # 3. 今日の終値からBUYシグナル生成
    # -----------------------------------------------------
    cand = []

    for t,d in data.items():

        if dt not in d.index or t in pos:
            continue

        r = d.loc[dt]

        p = float(r.Close)
        c = code(t)

        # 永続ルール：株価2,000円以上は新規BUY対象外
        if p >= 2000:
            continue

        # 流動性TOP50。
        # ただし実保有・重点監視銘柄は例外として検証対象に残す。
        liq_ok = (
            (not use_liq)
            or (c in liq_codes)
            or (c in WATCH_CODES)
        )

        if not liq_ok:
            continue

        ts = tech(r,rlo,rhi)

        if ts < mintech:
            continue

        hc = (
            confidence(stats[t])
            * recent_loss_penalty(stats[t])
        )

        hp = conf_points(hc)

        ms,mp,mf = market_info(
            market,
            dt
        )

        osnap = overseas_snapshot(
            overseas,
            dt
        )

        obonus = sector_overseas_bonus(
            t,
            osnap
        )

        qfactor,qblock,qreason,wr_hist,pf_hist,avg_hist = stock_quality(
            stats[t]
        )

        base_score = (
            ts*.55
            + hp*.30
            + mp*.15
        )

        raw_score = float(
            np.clip(
                base_score*qfactor+obonus,
                0,
                100
            )
        )

        score_band,score_conf = score_band_policy(
            raw_score,
            ms,
            osnap["海外為替係数"]
        )

        score = float(
            np.clip(
                raw_score*score_conf,
                0,
                100
            )
        )

        # 市場が弱いほどBUY条件を引き上げる
        buy_threshold = (
            86
            if ms in [
                "⚪ 中立",
                "🟠 やや弱気",
                "🔴 弱気"
            ]
            else
            82
            if ms == "🟡 やや強気"
            else
            80
        )

        overseas_block = (
            osnap["海外為替係数"] < .50
            and score < 86
        )

        # RC3.8修正：
        # 画面設定の「BUY最低AIスコア」を実際のBUY判定にも適用
        buy_reject = (
            qblock
            or score < minbuy_score
            or score < buy_threshold
            or overseas_block
        )

        blocked = is_blocked(
            dt,
            block_until,
            severe_block_until
        )

        analyses.append({
            "日付":dt,
            "コード":c,
            "銘柄名":name(t),
            "株価":p,
            "テクニカルスコア":ts,
            "銘柄実績信頼度":hc,
            "銘柄実績ポイント":hp,
            "市場判定":ms,
            "市場ポイント":mp,
            "海外為替判定":osnap["海外為替判定"],
            "海外為替係数":osnap["海外為替係数"],
            "海外補正":obonus,
            "AIスコア帯":score_band,
            "スコア信頼補正":score_conf,
            "90+過信補正":score_band == "90+",
            "90+資金係数":score_band_budget_factor(score_band),
            "総合AIスコア":score,
            "元AIスコア":base_score,
            "銘柄期待値係数":qfactor,
            "銘柄BUY除外":qblock,
            "銘柄BUY判定理由":qreason,
            "過去勝率":wr_hist*100,
            "過去PF":pf_hist,
            "過去平均損益":avg_hist,
            "売買代金TOP50":c in liq_codes,
            "流動性例外":c in WATCH_CODES,
            "重点監視銘柄":c in WATCH_CODES,
            "6085特別監視":c == "6085",
            "実保有銘柄":c in HELD_CODES,
            "RSI":float(r.RSI),
            "5日騰落率":float(r.Return_5d),
            "25日騰落率":float(r.Return_25d),
            "出来高倍率":float(r.Volume_Ratio),
            "5日急騰警戒":bool(r.Return_5d >= 15),
            "90+過信警戒":bool(score_band == "90+"),
            "新規BUY停止":blocked,
            "BUY最低スコア未達":score < minbuy_score,
            "市場条件未達":score < buy_threshold,
            "海外為替ブロック":overseas_block,
            "連敗リスク係数":risk_factor_from_losses(losses),
            "未来情報使用":False
        })

        if (
            not blocked
            and mf > 0
            and not buy_reject
        ):
            cand.append((
                score,
                t,
                ts,
                hc,
                ms,
                mp,
                osnap["海外為替判定"],
                osnap["海外為替係数"],
                obonus,
                qfactor,
                wr_hist,
                pf_hist,
                avg_hist
            ))


    cand.sort(reverse=True)


    # -----------------------------------------------------
    # 4. 次回取引日の寄付にBUY予約
    # -----------------------------------------------------
    for (
        score,t,ts,hc,ms,mp,
        os_state,os_factor,os_bonus,
        qfactor,wr_hist,pf_hist,avg_hist
    ) in cand:

        if t in pending_tickers:
            continue

        next_dt = next_trade_date(
            data[t].index,
            dt
        )

        if next_dt is None:
            continue

        pending_buys.setdefault(
            next_dt,
            []
        ).append({
            "ticker":t,
            "score":score,
            "ts":ts,
            "hc":hc,
            "market_state":ms,
            "market_factor":mp,
            "overseas_state":os_state,
            "overseas_factor":os_factor,
            "overseas_bonus":os_bonus,
            "signal_date":dt,
            "signal_close":float(
                data[t].loc[dt].Close
            ),
            "max_gap_pct":max_gap,
            "qfactor":qfactor,
            "wr_hist":wr_hist,
            "pf_hist":pf_hist,
            "avg_hist":avg_hist,
            "score_band":score_band,
            "score_band_conf":score_conf,
            "score_band_budget_factor":score_band_budget_factor(score_band)
        })

        pending_tickers.add(t)


    # -----------------------------------------------------
    # 5. 資産評価
    # -----------------------------------------------------
    hv = sum(
        float(
            data[t].loc[dt].Close
        ) * q["shares"]
        for t,q in pos.items()
        if dt in data[t].index
    )

    day_blocked = is_blocked(
        dt,
        block_until,
        severe_block_until
    )

    equity.append({
        "日付":dt,
        "現金":cash,
        "保有株評価額":hv,
        "総資産":cash+hv,
        "保有銘柄数":len(pos),
        "連敗数":losses,
        "新規BUY停止中":day_blocked,
        "連敗リスク係数":risk_factor_from_losses(losses)
    })


# =========================================================
# DataFrame
# =========================================================
trades_df = pd.DataFrame(trades)
analysis_df = pd.DataFrame(analyses)
equity_df = pd.DataFrame(equity)


# =========================================================
# 最新BUY候補
# =========================================================
latest = []

latest_dt = (
    max(
        d.index[-1]
        for d in data.values()
        if not d.empty
    )
    if data
    else None
)

if latest_dt is not None:

    for t,d in data.items():

        r = d.iloc[-1]
        p = float(r.Close)
        c = code(t)

        if p >= 2000:
            continue

        liq_ok = (
            (not use_liq)
            or (c in liq_codes)
            or (c in WATCH_CODES)
        )

        if not liq_ok:
            continue

        ts = tech(r,rlo,rhi)

        if ts < mintech:
            continue

        hc = (
            confidence(stats[t])
            * recent_loss_penalty(stats[t])
        )

        hp = conf_points(hc)

        ms,mp,mf = market_info(
            market,
            d.index[-1]
        )

        osnap = overseas_snapshot(
            overseas,
            d.index[-1]
        )

        obonus = sector_overseas_bonus(
            t,
            osnap
        )

        qfactor,qblock,qreason,wr_hist,pf_hist,avg_hist = stock_quality(
            stats[t]
        )

        base_score = (
            ts*.55
            + hp*.30
            + mp*.15
        )

        raw_score = float(
            np.clip(
                base_score*qfactor+obonus,
                0,
                100
            )
        )

        score_band,score_conf = score_band_policy(
            raw_score,
            ms,
            osnap["海外為替係数"]
        )

        score = float(
            np.clip(
                raw_score*score_conf,
                0,
                100
            )
        )

        buy_threshold = (
            86
            if ms in [
                "⚪ 中立",
                "🟠 やや弱気",
                "🔴 弱気"
            ]
            else
            82
            if ms == "🟡 やや強気"
            else
            80
        )

        overseas_block = (
            osnap["海外為替係数"] < .50
            and score < 86
        )

        # 最新BUY候補にも同じ最低スコア条件を適用
        if (
            qblock
            or score < minbuy_score
            or score < buy_threshold
            or mf <= 0
            or overseas_block
        ):
            continue

        latest.append({
            "コード":c,
            "銘柄名":name(t),
            "株価":p,
            "総合AIスコア":score,
            "テクニカルスコア":ts,
            "銘柄実績信頼度":hc,
            "銘柄期待値係数":qfactor,
            "過去勝率":wr_hist*100,
            "過去PF":pf_hist,
            "過去平均損益":avg_hist,
            "市場判定":ms,
            "海外為替判定":osnap["海外為替判定"],
            "海外為替係数":osnap["海外為替係数"],
            "海外補正":obonus,
            "AIスコア帯":score_band,
            "スコア信頼補正":score_conf,
            "購入資金係数":factor(score),
            "RSI":float(r.RSI),
            "5日騰落率":float(r.Return_5d),
            "25日騰落率":float(r.Return_25d),
            "出来高倍率":float(r.Volume_Ratio),
            "重点監視銘柄":c in WATCH_CODES,
            "6085特別監視":c == "6085",
            "実保有銘柄":c in HELD_CODES
        })

latest_df = (
    pd.DataFrame(latest)
    .sort_values(
        "総合AIスコア",
        ascending=False
    )
    if latest
    else pd.DataFrame()
)



# =========================================================
# 急騰予兆AI
# =========================================================
surge_rows = []
for t, d in data.items():
    c = code(t)
    if use_liq and c not in liq_codes and c not in WATCH_CODES:
        continue
    row = surge_radar_row(t, d, use_news=use_news_radar and (c in WATCH_CODES or c in liq_codes))
    if row is not None:
        surge_rows.append(row)

surge_df = pd.DataFrame(surge_rows)
if not surge_df.empty:
    surge_df = surge_df.sort_values(
        ["急騰予兆スコア", "出来高倍率"],
        ascending=[False, False]
    ).reset_index(drop=True)

# =========================================================
# 保有銘柄SELL判定
# =========================================================
ep = parse_entries(entries)
sell = []

for c in parse_codes(held):

    t = c + ".T"

    if t not in data:
        continue

    r = data[t].iloc[-1]
    p = float(r.Close)

    alerts = []

    if (
        p < r.MA25
        and (
            r.MA25_Slope < 0
            or tech(r,rlo,rhi) < 60
        )
    ):
        alerts.append("25日線割れ確認")

    if r.MA25 < r.MA75:
        alerts.append("25日線<75日線")

    if r.MA25_Slope < 0:
        alerts.append("25日線下降")

    if tech(r,rlo,rhi) < 60:
        alerts.append("AIスコア低下")

    osnap = overseas_snapshot(
        overseas,
        r.name
    )

    if osnap["海外為替係数"] < .50:
        alerts.append("海外・為替環境悪化")

    if (
        c in ep
        and (p/ep[c]-1)*100 <= -sl
    ):
        alerts.append("損切りライン")

    sell.append({
        "コード":c,
        "銘柄名":name(t),
        "現在価格":p,
        "取得単価":ep.get(c,np.nan),
        "含み損益率":(
            (p/ep[c]-1)*100
            if c in ep and ep[c]
            else np.nan
        ),
        "AIスコア":tech(r,rlo,rhi),
        "判定":(
            "SELL"
            if len(alerts) >= 3
            else "SELL注意"
            if alerts
            else "保有継続"
        ),
        "警戒理由":" / ".join(alerts),
        "海外為替判定":osnap["海外為替判定"],
        "海外為替係数":osnap["海外為替係数"],
        "売却期限目安":(
            "原則：次の1～3営業日以内"
            if len(alerts) >= 2
            else "目安：1～2週間以内"
        ),
        "重点監視銘柄":c in WATCH_CODES,
        "6085特別監視":c == "6085"
    })

sell_df = pd.DataFrame(sell)

sell_candidates = (
    sell_df[
        sell_df["判定"].isin(
            ["SELL","SELL注意"]
        )
    ]
    if not sell_df.empty
    else pd.DataFrame()
)


# =========================================================
# バックテスト成績
# =========================================================
if (
    not equity_df.empty
    and "総資産" in equity_df.columns
):

    equity_df["総資産"] = (
        pd.to_numeric(
            equity_df["総資産"],
            errors="coerce"
        ).fillna(initial)
    )

    final = float(
        equity_df["総資産"].iloc[-1]
    )

    equity_df["最高資産"] = (
        equity_df["総資産"].cummax()
    )

    equity_df["DD"] = (
        equity_df["総資産"]
        - equity_df["最高資産"]
    )

    equity_df["DD率"] = np.where(
        equity_df["最高資産"] != 0,
        equity_df["DD"]
        / equity_df["最高資産"]*100,
        0.0
    )

    maxdd = float(
        equity_df["DD"].min()
    )

    maxddrate = float(
        equity_df["DD率"].min()
    )

else:

    equity_df = pd.DataFrame(
        columns=[
            "日付","現金","保有株評価額",
            "総資産","保有銘柄数",
            "連敗数","新規BUY停止中"
        ]
    )

    final = float(initial)
    maxdd = 0.0
    maxddrate = 0.0


profit = final-initial
ret = (
    profit/initial*100
    if initial else 0.0
)

selltr = (
    trades_df[
        trades_df["売買"]=="SELL"
    ]
    if not trades_df.empty
    else pd.DataFrame()
)

winrate = (
    (selltr["損益"]>0).mean()*100
    if not selltr.empty
    else 0
)

gp = (
    selltr.loc[
        selltr["損益"]>0,
        "損益"
    ].sum()
    if not selltr.empty
    else 0
)

gl = (
    abs(
        selltr.loc[
            selltr["損益"]<0,
            "損益"
        ].sum()
    )
    if not selltr.empty
    else 0
)

pf = gp/gl if gl else 0


# =========================================================
# 未決済ポジション
# =========================================================
open_positions = []

for t,q in pos.items():

    if t in data and not data[t].empty:

        r = data[t].iloc[-1]
        p = float(r.Close)

        upnl = (
            p-q["entry"]
        ) * q["shares"]

        upct = (
            (p/q["entry"]-1)*100
            if q["entry"]
            else 0.0
        )

        open_positions.append({
            "コード":code(t),
            "銘柄名":name(t),
            "取得価格":q["entry"],
            "現在価格":p,
            "株数":q["shares"],
            "含み損益":upnl,
            "含み損益率":upct
        })

open_positions_df = pd.DataFrame(
    open_positions
)


# =========================================================
# RC3.8 健全性
# =========================================================
st.header(
    "🛡️ Ver.5.5 RC3.8 モデル健全性"
)

st.info(
    "BUYはシグナル当日終値で判定し、"
    "各銘柄の次回取引日の寄付で仮想約定。"
    "株価2,000円以上は新規BUY対象外、"
    "明けの明星は不使用。"
    "RC3.8ではBUY最低AIスコア条件に加え、90+過信補正を実判定にも適用し、"
    "実保有12銘柄＋6085を重点監視します。"
)


# =========================================================
# 今日のBUY
# =========================================================
st.header(
    "① 🟢 今日の買い候補 TOP3"
)

if latest_df.empty:

    st.info(
        "💤 今日は買わない日です。"
    )

else:

    for i,(_,r) in enumerate(
        latest_df.head(3).iterrows()
    ):

        rank = [
            "🥇","🥈","🥉"
        ][i]

        special = (
            " 🚨6085重点監視"
            if r.get("6085特別監視",False)
            else
            " 🔎保有銘柄"
            if r.get("実保有銘柄",False)
            else ""
        )

        st.success(
            f"{rank} **{r['銘柄名']}（{r['コード']}）**"
            f"{special}"
            f"　AI {r['総合AIスコア']:.0f}点"
            f"　｜海外環境 **{r.get('海外為替判定','中立')}**"
        )



# =========================================================
# 急騰予兆WATCH
# =========================================================
st.header("🟠 急騰予兆AI WATCH TOP3")
st.caption("急騰予兆AIはBUY推奨ではありません。通常AIとは独立して『異変の早期検知』を行い、先回り候補をWATCHします。")
if surge_df.empty:
    st.info("急騰予兆データがありません。")
else:
    watch_df = surge_df[surge_df["急騰予兆スコア"] >= 40].copy()
    if watch_df.empty:
        st.info("現時点で強い急騰予兆は検出されていません。")
    else:
        for _, rr in watch_df.head(3).iterrows():
            badge = "🚨" if rr["急騰予兆スコア"] >= 70 else "🟠"
            special = " 6085重点監視" if rr["6085特別監視"] else ""
            st.warning(
                f"{badge} **{rr['銘柄名']}（{rr['コード']}）**{special} "
                f"｜予兆 {rr['急騰予兆スコア']:.0f}点 "
                f"｜5日 {rr['5日騰落率']:+.1f}% "
                f"｜出来高 {rr['出来高倍率']:.1f}倍 "
                f"｜{rr['ニュース判定']}"
            )

# =========================================================
# SELL
# =========================================================
st.header(
    "② 🔴 もし保有していたら 売却 TOP3"
)

if sell_candidates.empty:

    st.success(
        "🟢 現在、明確な売却候補はありません。"
    )

else:

    for _,r in sell_candidates.head(3).iterrows():

        st.error(
            f"🔴 **{r['銘柄名']}（{r['コード']}）**"
            f" → {r['判定']}"
            f"　｜売却目安 **{r.get('売却期限目安','1～2週間以内')}**"
        )


# =========================================================
# 6085 特別監視
# =========================================================
st.header(
    "🚨 6085 アーキテクツ・スタジオ・ジャパン 特別監視"
)

if "6085.T" in data:

    d6085 = data["6085.T"]
    r6085 = d6085.iloc[-1]

    p6085 = float(r6085.Close)
    ts6085 = tech(r6085,rlo,rhi)
    data6085_latest = pd.Timestamp(d6085.index.max())
    data6085_age = (pd.Timestamp(datetime.now().date()) - data6085_latest.normalize()).days

    if data6085_age > RC33_STALE_DAYS:
        st.error(
            f"⚠️ 6085のデータが古い状態です。最終取得日：{data6085_latest.date()} "
            f"（現在から約{data6085_age}日）。自動再取得でも更新できない場合はYahoo側の取得制限を確認してください。"
        )
    else:
        st.success(
            f"🟢 6085データ最新日：{data6085_latest.date()}（約{data6085_age}日遅れ）"
        )

    os6085 = overseas_snapshot(
        overseas,
        r6085.name
    )

    ms6085,mp6085,mf6085 = market_info(
        market,
        r6085.name
    )

    q6085,qblock6085,qreason6085,wr6085,pf6085,avg6085 = stock_quality(
        stats["6085.T"]
    )

    hp6085 = conf_points(
        confidence(stats["6085.T"])
        * recent_loss_penalty(stats["6085.T"])
    )

    base6085 = (
        ts6085*.55
        + hp6085*.30
        + mp6085*.15
    )

    raw6085 = float(
        np.clip(
            base6085*q6085,
            0,
            100
        )
    )

    band6085,conf6085 = score_band_policy(
        raw6085,
        ms6085,
        os6085["海外為替係数"]
    )

    score6085 = float(
        np.clip(
            raw6085*conf6085,
            0,
            100
        )
    )

    st.metric(
        "6085 現在AIスコア",
        f"{score6085:.0f}点"
    )

    st.write(
        f"**RC3.8判定：** AIスコア帯 {band6085} "
        f"｜90+過信補正 {conf6085:.2f} "
        f"｜90+資金係数 {score_band_budget_factor(band6085):.2f}"
    )

    c1,c2,c3,c4 = st.columns(4)

    c1.metric(
        "株価",
        f"{p6085:,.1f}円"
    )

    c2.metric(
        "テクニカル",
        f"{ts6085:.0f}点"
    )

    c3.metric(
        "5日騰落率",
        f"{float(r6085.Return_5d):+.2f}%"
    )

    c4.metric(
        "出来高/VOL20",
        f"{float(r6085.Volume_Ratio):.2f}倍"
    )

    st.write(
        f"**市場環境：** {ms6085}　"
        f"｜**海外・為替：** {os6085['海外為替判定']}　"
        f"｜**AIスコア帯：** {band6085}"
    )

    st.write(
        f"**過去トレード数：** {stats['6085.T']['trades']}　"
        f"｜**過去勝率：** {wr6085*100:.1f}%　"
        f"｜**過去PF：** {pf6085:.2f}　"
        f"｜**期待値係数：** {q6085:.2f}"
    )

    st.caption(
        "※6085は『何倍になる』という噂を根拠にBUYするものではありません。"
        "出来高・テクニカル・市場環境・過去の同条件トレードを継続記録し、"
        "大化け説をデータで検証するための重点監視です。"
    )

else:

    st.warning(
        "6085が分析対象にありません。"
        "分析対象銘柄コードに 6085 を追加してください。"
    )


# =========================================================
# 処理結果
# =========================================================
st.header(
    "③ 📊 ロジック・処理結果分析"
)

if (
    latest_df.empty
    or float(
        latest_df.iloc[0]["総合AIスコア"]
    ) < 75
):

    st.info(
        "今日は積極的なBUYを見送ります。"
    )

else:

    st.success(
        "BUY候補があります。"
        "無理のない金額で最終判断してください。"
    )


if not open_positions_df.empty:

    st.header(
        "📦 バックテスト終了時の未決済ポジション"
    )

    st.dataframe(
        open_positions_df,
        use_container_width=True
    )


# =========================================================
# サマリー
# =========================================================
summary = pd.DataFrame({
    "項目":[
        "Ver",
        "初期資金",
        "最終資産",
        "損益",
        "損益率",
        "決済トレード数",
        "勝率",
        "Profit Factor",
        "最大DD",
        "最大DD率",
        "最大連続損失",
        "明けの明星",
        "株価2,000円以上BUY",
        "25日線SELL",
        "連敗ブレーキ",
        "寄付ギャップ制御",
        "悪いBUY除外",
        "BUY最低AIスコア",
        "実保有銘柄",
        "6085重点監視",
        "90+過信補正",
        "85-90基準帯",
        "6085データ鮮度監視",
        "RC3.8検証"
    ],
    "結果":[
        "5.5 RC3.8",
        initial,
        final,
        profit,
        ret,
        len(selltr),
        winrate,
        pf,
        maxdd,
        maxddrate,
        maxloss,
        "不使用",
        "除外",
        "確認型",
        "4/7/9/10段階",
        "あり",
        "銘柄別期待値フィルター",
        minbuy_score,
        "12銘柄",
        "あり",
        "90+は0.90倍・資金0.85倍",
        "85-90を基準帯",
        f"{RC33_STALE_DAYS}日超で警告・再取得",
        "90+過信抑制＋6085取得改善＋連敗ブレーキ再検証"
    ]
})


# =========================================================
# 銘柄別結果
# =========================================================
stock_results = (
    selltr.groupby(
        ["コード","銘柄名"]
    ).agg(
        トレード数=("損益","count"),
        勝ち=("損益",lambda x:(x>0).sum()),
        損益=("損益","sum"),
        平均損益=("損益","mean")
    ).reset_index()
    if not selltr.empty
    else pd.DataFrame()
)


# =========================================================
# BUY/SELLペア分析
# =========================================================
buy_rows = (
    trades_df[
        trades_df["売買"]=="BUY"
    ].copy()
    if not trades_df.empty
    else pd.DataFrame()
)

sell_rows = (
    trades_df[
        trades_df["売買"]=="SELL"
    ].copy()
    if not trades_df.empty
    else pd.DataFrame()
)

paired = []

if not buy_rows.empty and not sell_rows.empty:

    active = {}

    for _,row in trades_df.sort_values("日付").iterrows():

        key = row.get("コード")

        if row.get("売買") == "BUY":

            active[key] = row

        elif (
            row.get("売買") == "SELL"
            and key in active
        ):

            b = active.pop(key)

            paired.append({
                "コード":key,
                "銘柄名":row.get("銘柄名"),
                "BUY日":b.get("日付"),
                "SELL日":row.get("日付"),
                "損益":row.get("損益",0),
                "損益率":row.get("損益率",0),
                "AIスコア":b.get("総合AIスコア",np.nan),
                "テクニカルスコア":b.get("テクニカルスコア",np.nan),
                "銘柄期待値係数":b.get("銘柄期待値係数",1.0),
                "過去勝率":b.get("過去勝率",0),
                "過去PF":b.get("過去PF",0),
                "過去平均損益":b.get("過去平均損益",0),
                "市場判定":b.get("市場判定",""),
                "海外為替係数":b.get("海外為替係数",np.nan),
                "寄付ギャップ率":b.get("寄付ギャップ率",np.nan),
                "重点監視銘柄":b.get("重点監視銘柄",False),
                "6085特別監視":b.get("6085特別監視",False),
                "実保有銘柄":b.get("実保有銘柄",False)
            })


paired_df = pd.DataFrame(paired)


# =========================================================
# スコア帯分析
# =========================================================
if not paired_df.empty:

    bins = [
        -np.inf,
        75,
        78,
        80,
        82,
        85,
        90,
        np.inf
    ]

    labels = [
        "<75",
        "75-78",
        "78-80",
        "80-82",
        "82-85",
        "85-90",
        "90+"
    ]

    paired_df["AIスコア帯"] = pd.cut(
        paired_df["AIスコア"],
        bins=bins,
        labels=labels,
        right=False
    )

    score_band = (
        paired_df.groupby(
            "AIスコア帯",
            observed=False
        ).agg(
            トレード数=("損益","count"),
            勝率=("損益",lambda x:(x>0).mean()*100),
            損益=("損益","sum"),
            平均損益=("損益","mean"),
            PF=("損益",lambda x:
                x[x>0].sum()
                / abs(x[x<0].sum())
                if (x<0).any()
                else 0
            )
        ).reset_index()
    )

    q_band = (
        paired_df.groupby(
            "銘柄期待値係数",
            observed=False
        ).agg(
            トレード数=("損益","count"),
            勝率=("損益",lambda x:(x>0).mean()*100),
            損益=("損益","sum"),
            平均損益=("損益","mean")
        ).reset_index()
    )

    market_band = (
        paired_df.groupby(
            "市場判定",
            observed=False
        ).agg(
            トレード数=("損益","count"),
            勝率=("損益",lambda x:(x>0).mean()*100),
            損益=("損益","sum"),
            平均損益=("損益","mean")
        ).reset_index()
    )

else:

    score_band = pd.DataFrame()
    q_band = pd.DataFrame()
    market_band = pd.DataFrame()


# =========================================================
# 6085専用分析
# =========================================================
if not analysis_df.empty:

    analysis_6085 = analysis_df[
        analysis_df["コード"]=="6085"
    ].copy()

else:

    analysis_6085 = pd.DataFrame()


# =========================================================
# RC3.8 診断
# =========================================================
rc33_diagnostics = pd.DataFrame([
    {
        "検証項目":"90+過信補正",
        "RC3.8実績":"90+ PF 0.526",
        "RC3.8変更":"90+スコア×0.90・資金係数0.85",
        "目的":"90点以上の過信を抑制"
    },
    {
        "検証項目":"85-90基準帯",
        "RC3.8実績":"85-90 PF 2.241",
        "RC3.8変更":"基準帯として補正なし",
        "目的":"好成績帯を歪めず検証"
    },
    {
        "検証項目":"6085データ鮮度",
        "RC3.8実績":"取得終端が古い場合あり",
        "RC3.8変更":"6085のみTicker.history再取得＋鮮度警告",
        "目的":"2026年8月までの連続データを確保"
    },
    {
        "検証項目":"未来情報",
        "RC3.8実績":"当日終値→次回寄付",
        "RC3.8変更":"同一ルールを維持",
        "目的":"検証の公平性を維持"
    }
])

st.subheader("🧪 RC3.8 検証ポイント")
st.dataframe(rc33_diagnostics, width="stretch", hide_index=True)

# =========================================================
# ZIP
# =========================================================

files = {
    "00_summary.csv": summary,
    "01_today_buy.csv": latest_df,
    "02_today_sell.csv": sell_candidates,
    "03_all_ai_analysis.csv": analysis_df,
    "04_trade_history.csv": trades_df,
    "05_equity_curve.csv": equity_df,
    "06_stock_results.csv": stock_results,
    "07_liquidity_top50.csv": liq,
    "08_holdings_check.csv": sell_df,
    "09_open_positions.csv": open_positions_df,
    "10_paired_buy_sell.csv": paired_df,
    "11_score_band_analysis.csv": score_band,
    "12_quality_factor_analysis.csv": q_band,
    "13_market_analysis.csv": market_band,
    "14_overseas_fx_analysis.csv": analysis_df,
}

# RC3.8 diagnostic: 85-90 vs 90+.
diagnostic_rows = []
if not paired_df.empty:
    for _, row in paired_df.iterrows():
        ai = float(row.get("AIスコア", np.nan))
        if not np.isfinite(ai):
            continue
        if 85 <= ai < 90:
            zone, adjusted, capital_factor = "85-90", ai, 1.00
        elif ai >= 90:
            zone, adjusted, capital_factor = "90+", ai * 0.90, 0.85
        else:
            zone, adjusted, capital_factor = "<85", ai, 1.00

        diagnostic_rows.append({
            "コード": row.get("コード", ""),
            "銘柄名": row.get("銘柄名", ""),
            "BUY日": row.get("BUY日", ""),
            "SELL日": row.get("SELL日", ""),
            "元AIスコア": ai,
            "RC3_3補正後スコア": adjusted,
            "資金係数補正": capital_factor,
            "検証帯": zone,
            "損益": row.get("損益", 0),
            "損益率": row.get("損益率", 0),
            "過去PF": row.get("過去PF", 0),
            "銘柄期待値係数": row.get("銘柄期待値係数", 1.0),
        })

files["15_rc3_3_diagnostics.csv"] = pd.DataFrame(diagnostic_rows)

# 6085 dedicated diagnostic.
special_rows = []
if "6085.T" in data and not data["6085.T"].empty:
    d6085 = data["6085.T"].copy()
    r = d6085.iloc[-1]
    special_rows.append({
        "コード": "6085",
        "銘柄名": "アーキテクツ・スタジオ・ジャパン",
        "取得最終日": str(d6085.index[-1].date()),
        "現在価格": float(r["Close"]),
        "RSI": float(r["RSI"]),
        "MA25": float(r["MA25"]),
        "MA75": float(r["MA75"]),
        "MA200": float(r["MA200"]),
        "5日騰落率": float(
            (d6085["Close"].iloc[-1] / d6085["Close"].iloc[-6] - 1) * 100
        ) if len(d6085) >= 6 else np.nan,
        "20日平均出来高": float(r["VOL20"]),
        "現在出来高": float(r["Volume"]),
        "出来高倍率": float(r["Volume"] / r["VOL20"]) if r["VOL20"] else np.nan,
        "テクニカルスコア": tech(r, rlo, rhi),
    })

files["16_6085_special_analysis.csv"] = pd.DataFrame(special_rows)
files["17_surge_radar.csv"] = surge_df


# ===== RC3.8 SURGE VALIDATION =====
def surge_signal_row(d, dt):
    """Generate a surge-alert using information available at dt only."""
    if dt not in d.index:
        return None
    i = d.index.get_loc(dt)
    if isinstance(i, slice) or i < 25:
        return None
    r = d.iloc[i]
    close = float(r.Close)
    vol20 = float(r.VOL20) if np.isfinite(r.VOL20) else np.nan
    if close <= 0 or not np.isfinite(vol20) or vol20 <= 0:
        return None

    p5 = float((close / float(d.iloc[i-5].Close) - 1) * 100)
    p10 = float((close / float(d.iloc[i-10].Close) - 1) * 100)
    p25 = float((close / float(d.iloc[i-25].Close) - 1) * 100)
    vm = float(r.Volume / vol20) if vol20 else np.nan
    ma25_gap = float((close / float(r.MA25) - 1) * 100) if np.isfinite(r.MA25) else np.nan
    high20 = float(d.iloc[max(0, i-20):i+1].High.max())
    breakout = int(close >= high20)

    points = 0
    if p5 >= 10: points += 20
    if p5 >= 20: points += 10
    if p10 >= 15: points += 15
    if p25 >= 20: points += 10
    if vm >= 1.5: points += 15
    if vm >= 3.0: points += 10
    if breakout: points += 10
    if np.isfinite(r.MA25_Slope) and r.MA25_Slope > 0: points += 10

    # Overheating penalty: alert is still recorded, but the score is reduced.
    if np.isfinite(r.RSI) and r.RSI >= 80:
        points -= 15
    elif np.isfinite(r.RSI) and r.RSI >= 75:
        points -= 8

    score = float(np.clip(points, 0, 100))
    if score >= 70:
        state = "🚨 強い急騰予兆"
    elif score >= 55:
        state = "🟠 急騰予兆"
    elif score >= 40:
        state = "🟡 変化検知"
    else:
        state = "⚪ 通常"

    return {
        "予兆日": dt, "急騰予兆スコア": score, "急騰予兆判定": state,
        "5日騰落率": p5, "10日騰落率": p10, "25日騰落率": p25,
        "出来高倍率": vm, "MA25乖離率": ma25_gap, "20日高値更新": breakout,
        "RSI": float(r.RSI) if np.isfinite(r.RSI) else np.nan,
        "予兆時株価": close,
    }

def build_surge_validation(data, threshold=55):
    rows = []
    for t, d in data.items():
        if d.empty or len(d) < 40:
            continue
        idx = pd.DatetimeIndex(d.index)
        for dt in idx:
            s = surge_signal_row(d, dt)
            if not s or s["急騰予兆スコア"] < threshold:
                continue
            i = d.index.get_loc(dt)
            if isinstance(i, slice):
                continue
            row = {"コード": code(t), "銘柄名": name(t), **s}
            for days in (1, 3, 5, 10):
                j = i + days
                if j < len(d):
                    future_close = float(d.iloc[j].Close)
                    future_high = float(d.iloc[i+1:j+1].High.max())
                    future_low = float(d.iloc[i+1:j+1].Low.min())
                    base = s["予兆時株価"]
                    row[f"{days}営業日後騰落率"] = (future_close/base-1)*100
                    row[f"{days}営業日後最大上昇率"] = (future_high/base-1)*100
                    row[f"{days}営業日後最大下落率"] = (future_low/base-1)*100
                else:
                    row[f"{days}営業日後騰落率"] = np.nan
                    row[f"{days}営業日後最大上昇率"] = np.nan
                    row[f"{days}営業日後最大下落率"] = np.nan
            rows.append(row)
    return pd.DataFrame(rows)

try:
    surge_validation_df = build_surge_validation(data, threshold=55)
except Exception as e:
    surge_validation_df = pd.DataFrame()
    st.warning(f"急騰予兆検証の生成をスキップしました: {e}")

if not surge_validation_df.empty:
    st.header("🚨 急騰予兆 → その後の実績")
    st.caption("予兆判定には予兆日までの情報だけを使用。将来の価格は検証結果の集計にのみ使用します。")
    display_cols = [
        "コード","銘柄名","予兆日","急騰予兆スコア","急騰予兆判定",
        "5日騰落率","出来高倍率","RSI",
        "1営業日後騰落率","3営業日後騰落率",
        "5営業日後騰落率","10営業日後騰落率",
        "5営業日後最大上昇率","5営業日後最大下落率"
    ]
    display_cols = [c for c in display_cols if c in surge_validation_df.columns]
    st.dataframe(surge_validation_df[display_cols].sort_values(
        ["急騰予兆スコア","予兆日"], ascending=[False, False]
    ).head(100), use_container_width=True)

    # Score-band validation
    bins = [-np.inf, 55, 70, 85, np.inf]
    tmp = surge_validation_df.copy()
    tmp["予兆スコア帯"] = pd.cut(
        tmp["急騰予兆スコア"],
        bins=bins,
        labels=["<55", "55-69", "70-84", "85+"],
        right=False
    )
    completed = tmp.dropna(subset=["5営業日後騰落率"])
    if not completed.empty:
        surge_band_df = (
            completed
            .groupby("予兆スコア帯", observed=False)
            .agg(
                件数=("5営業日後騰落率", "count"),
                **{
                    "5日後平均騰落率": ("5営業日後騰落率", "mean"),
                    "5日後プラス率": ("5営業日後騰落率", lambda x: (x > 0).mean() * 100),
                    "5日後最大上昇平均": ("5営業日後最大上昇率", "mean"),
                    "5日後最大下落平均": ("5営業日後最大下落率", "mean"),
                }
            )
            .reset_index()
        )
        st.subheader("📊 急騰予兆スコア帯別の5営業日後実績")
        st.dataframe(surge_band_df, use_container_width=True)
    else:
        surge_band_df = pd.DataFrame()
else:
    surge_band_df = pd.DataFrame()
    st.info("急騰予兆検証データがありません。")

# Export the new diagnostic datasets if the original app has a files dict.
try:
    files["15_surge_validation.csv"] = surge_validation_df
    files["16_surge_score_band.csv"] = surge_band_df
except Exception:
    pass
# ===== END RC3.8 SURGE VALIDATION =====




st.caption("※仮想バックテスト・投資判断補助です。SBI証券への自動発注は行いません。")


# =========================================================
# RC3.8 THREE-LAYER DECISION DASHBOARD
# 通常BUY / 急騰WATCH / 過熱警戒
# =========================================================

st.title("📈 日本株AI意思決定システム Ver.5.5 RC3.8")
st.caption("朝は結論だけ。通常BUY・急騰WATCH・過熱警戒を分離し、裏側では従来通り詳細検証。")
st.caption("BUILD: VER5.5-RC3.8-20260821")

# ---------------------------------------------------------
# 最新の通常BUY
# ---------------------------------------------------------
top_buy = latest_df.head(3).copy() if not latest_df.empty else pd.DataFrame()

# ---------------------------------------------------------
# 保有銘柄SELL
# ---------------------------------------------------------
top_sell = sell_candidates.head(3).copy() if not sell_candidates.empty else pd.DataFrame()

# ---------------------------------------------------------
# 急騰レーダー
# 70-84: WATCH
# 85+: OVERHEAT
# ---------------------------------------------------------
surge_latest = pd.DataFrame()
surge_watch = pd.DataFrame()
surge_hot = pd.DataFrame()

try:
    if isinstance(surge_validation_df, pd.DataFrame) and not surge_validation_df.empty:
        tmp = surge_validation_df.copy()
        if "予兆日" in tmp.columns:
            tmp = tmp.sort_values(["コード", "予兆日"])
        surge_latest = tmp.groupby("コード", as_index=False).tail(1)

        if "急騰予兆スコア" in surge_latest.columns:
            surge_watch = (
                surge_latest[
                    surge_latest["急騰予兆スコア"].between(
                        70, 84.999999, inclusive="both"
                    )
                ]
                .sort_values("急騰予兆スコア", ascending=False)
                .head(3)
            )
            surge_hot = (
                surge_latest[surge_latest["急騰予兆スコア"] >= 85]
                .sort_values("急騰予兆スコア", ascending=False)
                .head(3)
            )
except Exception:
    surge_latest = pd.DataFrame()
    surge_watch = pd.DataFrame()
    surge_hot = pd.DataFrame()

# ---------------------------------------------------------
# 市場環境
# ---------------------------------------------------------
try:
    ref_date = next(iter(data.values())).index[-1] if data else datetime.now()
    market_state, market_points, market_factor = market_info(
        market, ref_date
    )
except Exception:
    market_state, market_points, market_factor = "⚪ 中立", 60, 0.60

# ---------------------------------------------------------
# 今日の総合結論
# 優先順位：
# SELL警戒 > BUY > WATCH > 何もしない
# ---------------------------------------------------------
if not top_sell.empty:
    today_decision = "🔴 保有銘柄を確認"
    decision_help = "売却警戒を最優先で確認してください。"
elif not top_buy.empty:
    today_decision = "🟢 BUY候補あり"
    decision_help = "通常AIの根拠が揃った銘柄です。"
elif not surge_watch.empty:
    today_decision = "🟡 WATCH中心"
    decision_help = "急騰予兆がありますが、まだBUYにはしません。"
elif not surge_hot.empty:
    today_decision = "🔴 過熱警戒"
    decision_help = "急騰後の過熱可能性があるため追いかけません。"
else:
    today_decision = "⚪ 今日は無理に買わない"
    decision_help = "条件不足です。現金を守ります。"

st.header("📌 今日の結論")
st.success(f"## {today_decision}")
st.caption(decision_help)
st.caption(
    f"市場環境：{market_state} ｜ 市場係数：{market_factor:.2f}"
)

# ---------------------------------------------------------
# 3層を一目で表示
# ---------------------------------------------------------
c1, c2, c3 = st.columns(3)

with c1:
    st.metric("🟢 BUY", len(top_buy))

with c2:
    st.metric("🟡 WATCH", len(surge_watch))

with c3:
    st.metric("🔴 売却/過熱警戒", len(top_sell) + len(surge_hot))

# ---------------------------------------------------------
# ① 通常BUY
# ---------------------------------------------------------
st.header("① 🟢 BUY候補 TOP3")

if top_buy.empty:
    st.info("通常BUY条件を満たす銘柄はありません。")
else:
    for i, (_, r) in enumerate(top_buy.iterrows()):
        rank = ["🥇", "🥈", "🥉"][i]

        st.success(
            f"{rank} **{r['銘柄名']}（{r['コード']}）** "
            f"AI **{r['総合AIスコア']:.0f}点** ｜ "
            f"株価 ¥{r['株価']:.0f}"
        )

        st.caption(
            f"テクニカル {r.get('テクニカルスコア', 0):.0f} ｜ "
            f"過去PF {r.get('過去PF', 0):.2f} ｜ "
            f"海外 {r.get('海外為替判定', '中立')}"
        )

# ---------------------------------------------------------
# ② 急騰WATCH
# ---------------------------------------------------------
st.header("② 🟡 急騰予兆 WATCH TOP3")

if surge_watch.empty:
    st.info("現在、70～84点の先行監視銘柄はありません。")
else:
    st.warning(
        "⚠️ WATCHはBUYではありません。"
        "出来高・値動きなどから変化を早期検知した銘柄です。"
    )

    for _, r in surge_watch.iterrows():
        parts = [
            f"予兆 **{r.get('急騰予兆スコア', 0):.0f}点**"
        ]

        if "5日騰落率" in r and pd.notna(r["5日騰落率"]):
            parts.append(f"5日 {r['5日騰落率']:+.1f}%")

        if "出来高倍率" in r and pd.notna(r["出来高倍率"]):
            parts.append(f"出来高 {r['出来高倍率']:.1f}倍")

        if "RSI" in r and pd.notna(r["RSI"]):
            parts.append(f"RSI {r['RSI']:.1f}")

        st.warning(
            f"🟡 **{r['銘柄名']}（{r['コード']}）** ｜ "
            + " ｜ ".join(parts)
        )

# ---------------------------------------------------------
# ③ 過熱警戒
# ---------------------------------------------------------
st.header("③ 🔴 過熱警戒")

if surge_hot.empty:
    st.success("🔵 現在、急騰予兆85点以上の銘柄はありません。")
else:
    st.error(
        "急騰予兆が非常に強い銘柄です。"
        "高値追いを避け、通常BUY条件が整うまで待ちます。"
    )

    for _, r in surge_hot.iterrows():
        st.error(
            f"🔴 **{r['銘柄名']}（{r['コード']}）** "
            f"予兆 **{r.get('急騰予兆スコア', 0):.0f}点**"
        )

# ---------------------------------------------------------
# ④ 保有銘柄
# ---------------------------------------------------------
st.header("④ 🔴 保有銘柄の判断")

if top_sell.empty:
    st.success("🟢 明確な売却警戒はありません。")
else:
    for _, r in top_sell.iterrows():
        st.error(
            f"🔴 **{r['銘柄名']}（{r['コード']}）** "
            f"→ **{r['判定']}** ｜ "
            f"{r.get('売却期限目安', '1～3営業日以内')}"
        )

        if r.get("警戒理由"):
            st.caption(r["警戒理由"])

# ---------------------------------------------------------
# 詳細分析
# ---------------------------------------------------------
with st.expander("🔍 詳細分析・検証データ", expanded=False):

    st.subheader("📊 通常AI BUY")
    if not top_buy.empty:
        st.dataframe(top_buy, use_container_width=True)
    else:
        st.info("BUY候補なし")

    st.subheader("🚨 急騰予兆検証")
    if not surge_validation_df.empty:
        surge_cols = [
            c for c in [
                "コード", "銘柄名", "予兆日", "急騰予兆スコア",
                "急騰予兆判定", "5日騰落率", "10日騰落率",
                "出来高倍率", "RSI", "5営業日後騰落率",
                "5営業日後最大上昇率", "5営業日後最大下落率"
            ]
            if c in surge_validation_df.columns
        ]

        st.dataframe(
            surge_validation_df[surge_cols]
            .sort_values("急騰予兆スコア", ascending=False)
            .head(200),
            use_container_width=True
        )

        if not surge_band_df.empty:
            st.subheader("急騰予兆スコア帯別実績")
            st.dataframe(surge_band_df, use_container_width=True)
    else:
        st.info("急騰予兆データなし")

    st.subheader("📈 バックテスト概要")
    st.dataframe(summary, use_container_width=True)

    if not stock_results.empty:
        st.subheader("銘柄別実績")
        st.dataframe(
            stock_results.sort_values("損益", ascending=False),
            use_container_width=True
        )

    if not open_positions_df.empty:
        st.subheader("未決済ポジション")
        st.dataframe(open_positions_df, use_container_width=True)

# ---------------------------------------------------------
# CSV / ZIP 出力
# ---------------------------------------------------------
st.header("📥 検証データ出力")

def rc38_df(x):
    return x.copy() if isinstance(x, pd.DataFrame) else pd.DataFrame()

rc38_files = {
    "00_summary.csv": rc38_df(summary),
    "01_today_buy.csv": rc38_df(top_buy),
    "02_today_sell.csv": rc38_df(top_sell),
    "03_all_ai_analysis.csv": rc38_df(analysis_df),
    "04_trade_history.csv": rc38_df(trades_df),
    "05_equity_curve.csv": rc38_df(equity_df),
    "06_stock_results.csv": rc38_df(stock_results),
    "07_liquidity_top50.csv": rc38_df(liq),
    "08_holdings_check.csv": rc38_df(sell_df),
    "09_open_positions.csv": rc38_df(open_positions_df),
    "10_paired_buy_sell.csv": rc38_df(paired_df),
    "11_score_band_analysis.csv": rc38_df(score_band),
    "12_quality_factor_analysis.csv": rc38_df(q_band),
    "13_market_analysis.csv": rc38_df(market_band),
    "14_overseas_fx_analysis.csv": rc38_df(analysis_df),
    "15_surge_validation.csv": rc38_df(surge_validation_df),
    "16_surge_score_band.csv": rc38_df(surge_band_df),
}

ec1, ec2 = st.columns(2)

with ec1:
    st.download_button(
        "📄 今日のBUY CSV",
        data=rc38_df(top_buy).to_csv(
            index=False, encoding="utf-8-sig"
        ).encode("utf-8-sig"),
        file_name="RC3_8_today_buy.csv",
        mime="text/csv",
        width="stretch",
    )

with ec2:
    st.download_button(
        "📄 今日のSELL CSV",
        data=rc38_df(top_sell).to_csv(
            index=False, encoding="utf-8-sig"
        ).encode("utf-8-sig"),
        file_name="RC3_8_today_sell.csv",
        mime="text/csv",
        width="stretch",
    )

rc38_zip = BytesIO()

with ZipFile(rc38_zip, "w") as z:
    for filename, frame in rc38_files.items():
        z.writestr(
            filename,
            frame.to_csv(
                index=False, encoding="utf-8-sig"
            ).encode("utf-8-sig")
        )

rc38_zip.seek(0)

st.download_button(
    "📦 全処理CSVをZIPで保存",
    data=rc38_zip.getvalue(),
    file_name="ver5_5_RC3_8_all_analysis.zip",
    mime="application/zip",
    width="stretch",
)

st.caption(
    "通常BUY・急騰WATCH・過熱警戒・売却判断・バックテストを"
    "すべてCSVとして保存し、次回RCの検証材料にします。"
)

st.caption(
    "※仮想バックテスト・投資判断補助です。"
    "SBI証券への自動発注は行いません。"
)

# =========================================================
# END RC3.8
# =========================================================
