# ============================================================
# 日本株 AI投資アシスタント Ver.17.24.47
# BUILD: VER17-24-47-FLOW-RESEARCH-20261026
#
# 目的:
#   企業価値AI + テンバガーAI + テクニカルAI
#   + 保有銘柄AI + 損切り/リスク管理 + 資金管理
#
# 重要:
#   ・Ver.5.5系の思想を維持
#   ・未来情報を使わないバックテスト
#   ・現行のファンダメンタル評価は「現在情報」に限定
#     （過去バックテストへ混ぜない）
#   ・SBI証券への自動発注は行わない
#   ・保有銘柄はSBI証券「約定履歴CSV」から自動復元（スクショ/OCRは完全除外）
# ============================================================

import io
import zipfile
import hashlib
import json
import math
import os
import re
import threading
import tempfile
import base64
import zlib
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import escape as html_escape, unescape as html_unescape
from html.parser import HTMLParser
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.17.24.48",
    page_icon="📈",
    layout="wide",
)

VERSION = "17.24.48 底打ち研究表示修正"
BUILD = "VER17-24-48-BOTTOM-EMPTY-FIX-20261028"

JST = ZoneInfo("Asia/Tokyo")
TRADINGVIEW_QUOTES_CACHE = {}
JQUANTS_CALL_TIMES = []
JQUANTS_CALL_LOCK = threading.Lock()



def valuation_display_rank(value):
    """装飾付き判定にも対応し、同一判定内の元の優先順位を維持する。"""
    text = str(value)
    for rank, label in enumerate(['やや割安', '割安', '適正', 'やや割高', '割高']):
        if label in text:
            return {'やや割安': 1, '割安': 0, '適正': 2, 'やや割高': 3, '割高': 4}[label]
    return 5


def prepare_stock_table(data):
    """表示専用コピー。銘柄表のみ共通評価を補い、集計表はそのまま表示する。"""
    if not isinstance(data, pd.DataFrame):
        return data
    out = data.copy()
    source = next((c for c in ['銘柄判定', '割安判定', '割安'] if c in out), None)
    code_col = next((c for c in ['コード', '銘柄コード', 'Ticker', 'ticker'] if c in out), None)
    if source is None and code_col is None:
        return out
    if source is not None and source != '銘柄判定':
        out = out.rename(columns={source: '銘柄判定'})
    if '銘柄判定' not in out:
        out.insert(0, '銘柄判定', '未算定')
    master = globals().get('valuation_master_df')
    if code_col is not None and isinstance(master, pd.DataFrame) and {'コード', '割安判定'}.issubset(master.columns):
        normalize = lambda x: str(x).removesuffix('.T').removesuffix('.0')
        labels = dict(zip(master['コード'].map(normalize), master['割安判定']))
        mapped = out[code_col].map(normalize).map(labels)
        out['銘柄判定'] = mapped.where(mapped.notna() & mapped.astype(str).str.strip().ne(''), out['銘柄判定'])
    out['銘柄判定'] = out['銘柄判定'].fillna('未算定').replace('', '未算定')
    out = out[['銘柄判定'] + [c for c in out.columns if c != '銘柄判定']]
    order = out['銘柄判定'].map(valuation_display_rank).to_numpy().argsort(kind='stable')
    return out.iloc[order].reset_index(drop=True)


def annotate_reference_choice(data, context=None, candidates=None):
    """表示用の相対比較。注文・売買条件には使用しない。"""
    out = prepare_stock_table(data)
    if not isinstance(out, pd.DataFrame) or out.empty or 'コード' not in out:
        return out
    out['AI参考選択'] = ''
    out['AI比較理由'] = ''
    pool = out if candidates is None else out[out['コード'].astype(str).isin(set(map(str, candidates)))]
    if pool.empty:
        return out
    metrics = pool.copy()
    if isinstance(context, pd.DataFrame) and 'コード' in context:
        lookup = context.assign(コード=context['コード'].astype(str)).drop_duplicates('コード').set_index('コード')
        for col in lookup:
            if col not in metrics:
                metrics[col] = metrics['コード'].astype(str).map(lookup[col])
    def numeric(col, default=0):
        return pd.to_numeric(metrics.get(col, pd.Series(default, index=metrics.index)), errors='coerce').fillna(default)
    # 財務評価、既存研究点、条件達成数、トレンドの順。同点はコードで固定。
    metrics['_value'] = metrics['銘柄判定'].map(valuation_display_rank)
    metrics['_score'] = numeric('研究点')
    conditions = [c for c in metrics if c.startswith('条件_')]
    metrics['_passed'] = metrics[conditions].eq('○').sum(axis=1) if conditions else 0
    metrics['_trend'] = metrics.get('上昇トレンド', pd.Series(False, index=metrics.index)).eq(True).astype(int)
    metrics['_code'] = metrics['コード'].astype(str)
    selected = metrics.sort_values(['_value', '_score', '_passed', '_trend', '_code'],
                                  ascending=[True, False, False, False, True], kind='stable').iloc[0]
    reason = '銘柄判定を優先'
    if '研究点' in metrics:
        reason += f"／研究点{selected['_score']:g}／条件達成{selected['_passed']}個"
    elif selected['_trend']:
        reason += '／上昇トレンド確認'
    reason += '（同点はコード順）'
    idx = selected.name
    out.loc[idx, 'AI参考選択'] = '★ AI参考候補'
    out.loc[idx, 'AI比較理由'] = reason
    return out


def show_reference_choice_table(data, context=None, candidates=None, **kwargs):
    table = annotate_reference_choice(data, context, candidates)
    if isinstance(table, pd.DataFrame) and 'AI参考選択' in table:
        chosen = table[table['AI参考選択'].ne('')]
        if not chosen.empty:
            row = chosen.iloc[0]
            st.info(f"★ AI参考候補：{row['コード']} {row.get('銘柄名', '')} ｜ {row['AI比較理由']}")
        else:
            st.caption('AI参考候補：比較対象なし')
        st.caption('黄色の1行は表内の相対比較です。取得済み指標による規則式で、買い条件の通過や上昇を保証しません。見送り理由・判定保留を優先して確認してください。')
        styled = table.style.apply(lambda row: ['background-color: #fff2b3; color: #17202a; font-weight: 700'
                                    if row['AI参考選択'] else '' for _ in row], axis=1)
        return st.dataframe(styled, **kwargs)
    return st.dataframe(table, **kwargs)


def show_stock_table(data, *args, **kwargs):
    """全タブで銘柄判定を先頭に、割安順を標準表示する。"""
    return st.dataframe(prepare_stock_table(data), *args, **kwargs)


def tokyo_now():
    """Streamlit CloudのUTC設定に依存せず、日本時間を返す。"""
    return datetime.now(timezone.utc).astimezone(JST)


def yahoo_history_window(years=5):
    """Yahooのend日が排他的であることを考慮した日本時間基準の取得範囲。"""
    now_jst = tokyo_now()
    end_jst = datetime.combine(
        now_jst.date() + timedelta(days=1), datetime.min.time(), tzinfo=JST
    )
    start_jst = end_jst - timedelta(days=365 * years + 300)
    return start_jst, end_jst

# ------------------------------------------------------------
# 銘柄名（既存システムの主要銘柄＋実保有/監視銘柄）
# ------------------------------------------------------------
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
    "3444":"菊池製作所","5885":"ジーデップ・アドバンス",
    "6324":"ハーモニック・ドライブ・システムズ","6506":"安川電機",
    "6629":"テクノホライゾン","6954":"ファナック","6965":"浜松ホトニクス",
    "7012":"川崎重工業","6085":"アーキテクツ・スタジオ・ジャパン",
}

# 検証ZIPで5年間の日足が揃った旧固定リストの45銘柄。
# 欠損した3444、5885、6085、6629は新規買い対象にしない。
VERIFIED_45_CODES = tuple(
    "7203,6758,9984,8306,9432,6501,8035,8058,7267,2914,9433,8316,8411,6098,"
    "4063,4519,6367,6857,7974,8766,5401,8801,8802,4502,4503,4523,4755,6594,"
    "7741,6981,3382,4661,6146,6920,7832,4568,4452,6503,6701,6702,6324,6506,"
    "6954,6965,7012"
    .split(",")
)
assert len(VERIFIED_45_CODES) == len(set(VERIFIED_45_CODES)) == 45
DEFAULT_UNIVERSE = ",".join(VERIFIED_45_CODES)

# ------------------------------------------------------------
# 共通関数
# ------------------------------------------------------------
def code(t):
    return str(t).replace(".T", "").strip()

def name(t):
    return STOCK_NAMES.get(code(t), code(t))

def tickers(s):
    vals = []
    for x in str(s).replace("\n", ",").split(","):
        x = x.strip()
        if not x:
            continue
        vals.append(x if x.endswith(".T") else x + ".T")
    return list(dict.fromkeys(vals))

def parse_codes(s):
    return list(dict.fromkeys([
        x.strip().replace(".T", "")
        for x in str(s).replace("\n", ",").split(",")
        if x.strip()
    ]))

def parse_entries(s):
    out = {}
    for x in str(s).replace("\n", ",").split(","):
        if ":" not in x:
            continue
        a, b = x.split(":", 1)
        try:
            out[a.strip().replace(".T", "")] = float(b)
        except Exception:
            pass
    return out

def parse_shares(s):
    out = {}
    for x in str(s).replace("\n", ",").split(","):
        if ":" not in x:
            continue
        a, b = x.split(":", 1)
        try:
            out[a.strip().replace(".T", "")] = int(float(b))
        except Exception:
            pass
    return out

def csv_bytes(df):
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

def safe_float(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default

def clamp(x, lo=0, hi=100):
    return float(np.clip(safe_float(x, lo), lo, hi))

# ------------------------------------------------------------
# 株価データ
# ------------------------------------------------------------
@st.cache_data(ttl=3600)
def _stock_data_rc61_fallback(t, years=5):
    start, end = yahoo_history_window(years)

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
        if len(df) < 220:
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
        df["RSI"] = 100 - (100 / (1 + rs))

        tr = pd.concat([
            df.High - df.Low,
            (df.High - df.Close.shift()).abs(),
            (df.Low - df.Close.shift()).abs(),
        ], axis=1).max(axis=1)
        df["ATR14"] = tr.rolling(14).mean()

        df["Return_5d"] = df.Close.pct_change(5) * 100
        df["Return_25d"] = df.Close.pct_change(25) * 100
        df["Volume_Ratio"] = df.Volume / df.VOL20.replace(0, np.nan)
        return df.dropna()

    try:
        raw = yf.download(
            t, start=start, end=end,
            auto_adjust=False, progress=False, threads=False
        )
        return normalize(raw)
    except Exception:
        try:
            return normalize(yf.Ticker(t).history(
                start=start, end=end,
                auto_adjust=False, actions=False
            ))
        except Exception:
            return pd.DataFrame()

@st.cache_data(ttl=3600)
def _market_data_rc61_fallback():
    start, end = yahoo_history_window(5)
    try:
        df = yf.download(
            "^N225", start=start, end=end,
            auto_adjust=False, progress=False, threads=False
        )
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        c = pd.to_numeric(df["Close"], errors="coerce")
        out = pd.DataFrame({"Close": c})
        out["MA25"] = c.rolling(25).mean()
        out["MA75"] = c.rolling(75).mean()
        out["MA200"] = c.rolling(200).mean()
        out["MA25_Slope"] = out.MA25 - out.MA25.shift(5)
        return out.dropna()
    except Exception:
        return pd.DataFrame()

def _append_regular_market_row(frame, meta, regular_date):
    """日足配列だけ更新が遅い場合、同じレスポンスの最新四本値で末尾を補完する。"""
    if frame is None or frame.empty or pd.isna(regular_date):
        return frame, False
    latest = pd.Timestamp(frame.index.max()).normalize()
    if regular_date <= latest:
        return frame, False

    close = safe_float(meta.get("regularMarketPrice"), np.nan)
    if not np.isfinite(close) or close <= 0:
        return frame, False
    open_price = safe_float(meta.get("regularMarketOpen"), close)
    high = safe_float(meta.get("regularMarketDayHigh"), close)
    low = safe_float(meta.get("regularMarketDayLow"), close)
    volume = safe_float(meta.get("regularMarketVolume"), 0.0)
    frame = frame.copy()
    frame.loc[regular_date, ["Open", "High", "Low", "Close", "Volume"]] = [
        open_price, high, low, close, max(volume, 0.0)
    ]
    return frame.sort_index(), True


def _aggregate_intraday_day(stamps, quote, tz_name, target_date):
    """Yahooの分足を指定日の日足OHLCVへ集約する。"""
    if not stamps:
        return None
    size = len(stamps)

    def values(key):
        vals = list(quote.get(key) or [])
        return (vals + [np.nan] * size)[:size]

    idx = pd.to_datetime(stamps, unit="s", utc=True)
    try:
        idx = idx.tz_convert(tz_name)
    except Exception:
        pass
    idx = idx.tz_localize(None)
    intraday = pd.DataFrame({
        "Open": values("open"), "High": values("high"),
        "Low": values("low"), "Close": values("close"),
        "Volume": values("volume"),
    }, index=idx)
    target_date = pd.Timestamp(target_date).normalize()
    intraday = intraday[intraday.index.normalize() == target_date].copy()
    intraday["Close"] = pd.to_numeric(intraday["Close"], errors="coerce")
    intraday = intraday.dropna(subset=["Close"]).sort_index()
    if intraday.empty:
        return None
    for c in ["Open", "High", "Low", "Volume"]:
        intraday[c] = pd.to_numeric(intraday[c], errors="coerce")
    open_values = intraday["Open"].dropna()
    high_values = intraday["High"].dropna()
    low_values = intraday["Low"].dropna()
    volume = intraday["Volume"].sum(min_count=1)
    return {
        "Open": float(open_values.iloc[0]) if not open_values.empty else float(intraday["Close"].iloc[0]),
        "High": float(high_values.max()) if not high_values.empty else float(intraday["Close"].max()),
        "Low": float(low_values.min()) if not low_values.empty else float(intraday["Close"].min()),
        "Close": float(intraday["Close"].iloc[-1]),
        "Volume": float(volume) if np.isfinite(volume) else 0.0,
    }


def _yahoo_intraday_day(symbol, target_date):
    """直近5日分の分足から、指定日の確定OHLCVを取得する。"""
    encoded = requests.utils.quote(str(symbol), safe="")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/6.0)"}
    last_error = None
    for interval in ("1m", "5m"):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                url = f"https://{host}/v8/finance/chart/{encoded}"
                params = {
                    "range": "5d", "interval": interval,
                    "includePrePost": "false", "events": "history",
                }
                response = requests.get(url, params=params, headers=headers, timeout=12)
                response.raise_for_status()
                payload = response.json()
                result = payload.get("chart", {}).get("result") or []
                if not result:
                    raise ValueError(payload.get("chart", {}).get("error") or "分足データなし")
                item = result[0]
                quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
                tz_name = (item.get("meta") or {}).get("exchangeTimezoneName") or "Asia/Tokyo"
                row = _aggregate_intraday_day(
                    item.get("timestamp") or [], quote, tz_name, target_date
                )
                if row is not None:
                    return row, f"Yahoo Finance {interval}分足集約 ({host})"
                raise ValueError(f"{pd.Timestamp(target_date).date()}の分足なし")
            except Exception as e:
                last_error = e
    raise RuntimeError(f"日経平均の分足取得失敗: {last_error}")


def _json_number_from_html(text, key):
    patterns = [
        rf'"{re.escape(key)}"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)',
        rf'"{re.escape(key)}"\s*:\s*\{{[^{{}}]*?"raw"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            return safe_float(m.group(1), np.nan)
    return np.nan


def _yahoo_japan_nikkei_snapshot(target_date):
    """Yahoo Japanの998407.O指数ページから最新の四本値を取得する。"""
    url = "https://finance.yahoo.co.jp/quote/998407.O"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/6.0)"}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    html = response.text
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.I | re.S)
    visible = html_unescape(re.sub(r"<[^>]+>", " ", visible))
    visible = re.sub(r"\s+", " ", visible)

    target_date = pd.Timestamp(target_date).normalize()
    timestamp = _json_number_from_html(html, "regularMarketTime")
    page_date = pd.NaT
    if np.isfinite(timestamp) and timestamp > 1_000_000_000:
        page_date = pd.Timestamp(int(timestamp), unit="s", tz="UTC").tz_convert("Asia/Tokyo").tz_localize(None).normalize()
    if pd.isna(page_date):
        date_tokens = {
            f"{target_date.year}/{target_date.month}/{target_date.day}",
            f"{target_date.month}月{target_date.day}日",
            target_date.strftime("%Y/%m/%d"),
        }
        if any(token in visible for token in date_tokens):
            page_date = target_date
    if pd.isna(page_date) or page_date != target_date:
        raise ValueError(f"Yahoo Japan指数ページの日付不一致: {page_date}")

    values = {
        "Open": _json_number_from_html(html, "regularMarketOpen"),
        "High": _json_number_from_html(html, "regularMarketDayHigh"),
        "Low": _json_number_from_html(html, "regularMarketDayLow"),
        "Close": _json_number_from_html(html, "regularMarketPrice"),
        "Volume": _json_number_from_html(html, "regularMarketVolume"),
    }
    label_patterns = {
        "Open": r"始値[^0-9]{0,80}([0-9][0-9,]*(?:\.[0-9]+)?)",
        "High": r"高値[^0-9]{0,80}([0-9][0-9,]*(?:\.[0-9]+)?)",
        "Low": r"安値[^0-9]{0,80}([0-9][0-9,]*(?:\.[0-9]+)?)",
        "Close": r"(?:取引値|現在値)[^0-9]{0,80}([0-9][0-9,]*(?:\.[0-9]+)?)",
    }
    for key, pattern in label_patterns.items():
        if not np.isfinite(values[key]):
            m = re.search(pattern, visible)
            if m:
                values[key] = safe_float(m.group(1).replace(",", ""), np.nan)
    if not all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open", "High", "Low", "Close"]):
        raise ValueError("Yahoo Japan指数ページから四本値を抽出できません")
    if not np.isfinite(values["Volume"]):
        values["Volume"] = 0.0
    return values, "Yahoo Japan 998407.O指数ページ"


def _repair_nikkei_day(raw, required_date):
    """日経平均の欠落日を独立した複数経路で補完する。"""
    required_date = pd.Timestamp(required_date).normalize()
    if raw is not None and not raw.empty and pd.Timestamp(raw.index.max()).normalize() >= required_date:
        return raw, "", False

    # 1) Yahoo Japanと同じ指数コードをチャートAPIで試す。
    try:
        alt, _meta = _yahoo_chart("998407.O", 5)
        if required_date in alt.index:
            row = alt.loc[required_date]
            raw = raw.copy()
            raw.loc[required_date, ["Open", "High", "Low", "Close", "Volume"]] = [
                row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
            ]
            return raw.sort_index(), "Yahoo Finance 998407.Oチャート", True
    except Exception:
        pass

    # 2) Yahoo Japanの指数詳細ページから確定四本値を取得する。
    try:
        row, source = _yahoo_japan_nikkei_snapshot(required_date)
        raw = raw.copy()
        raw.loc[required_date, ["Open", "High", "Low", "Close", "Volume"]] = [
            row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
        ]
        return raw.sort_index(), source, True
    except Exception:
        pass

    # 3) 最後に分足集約を試す。
    row, source = _yahoo_intraday_day("^N225", required_date)
    raw = raw.copy()
    raw.loc[required_date, ["Open", "High", "Low", "Close", "Volume"]] = [
        row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
    ]
    return raw.sort_index(), source, True


def _yahoo_chart(symbol, years=5):
    """Yahoo Financeの最新日足を直接取得し、取引時刻メタ情報も返す。"""
    start_jst, end_jst = yahoo_history_window(years)
    end_ts = int(end_jst.timestamp())
    start_ts = int(start_jst.timestamp())
    encoded = requests.utils.quote(str(symbol), safe="")
    params = {
        "period1": start_ts, "period2": end_ts, "interval": "1d",
        "events": "history", "includeAdjustedClose": "true",
    }
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/6.0)"}
    last_error = None

    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{encoded}"
            response = requests.get(url, params=params, headers=headers, timeout=12)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("chart", {}).get("result") or []
            if not result:
                raise ValueError(payload.get("chart", {}).get("error") or "チャートデータなし")

            item = result[0]
            stamps = item.get("timestamp") or []
            quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
            if not stamps:
                raise ValueError("日足タイムスタンプなし")
            size = len(stamps)
            frame = pd.DataFrame({
                "Open": quote.get("open", [np.nan] * size),
                "High": quote.get("high", [np.nan] * size),
                "Low": quote.get("low", [np.nan] * size),
                "Close": quote.get("close", [np.nan] * size),
                "Volume": quote.get("volume", [np.nan] * size),
            })
            meta = item.get("meta") or {}
            tz_name = meta.get("exchangeTimezoneName") or "UTC"
            idx = pd.to_datetime(stamps, unit="s", utc=True)
            try:
                idx = idx.tz_convert(tz_name)
            except Exception:
                pass
            frame.index = idx.tz_localize(None).normalize()
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()

            regular_date = pd.NaT
            if meta.get("regularMarketTime"):
                ts = pd.Timestamp(meta["regularMarketTime"], unit="s", tz="UTC")
                try:
                    ts = ts.tz_convert(tz_name)
                except Exception:
                    pass
                regular_date = ts.tz_localize(None).normalize()
            frame, repaired = _append_regular_market_row(frame, meta, regular_date)
            repair_method = "regularMarket最新四本値補完" if repaired else ""
            latest = pd.Timestamp(frame.index.max()).normalize() if not frame.empty else pd.NaT
            if pd.notna(regular_date) and (pd.isna(latest) or latest < regular_date):
                for provider in (
                    _minkabu_stock_day,
                    _stooq_stock_day,
                    _yahoo_japan_stock_snapshot,
                    _yahoo_recent_day,
                    _yahoo_intraday_day,
                ):
                    try:
                        row, repair_method = provider(symbol, regular_date)
                        frame = frame.copy()
                        frame.loc[regular_date, ["Open", "High", "Low", "Close", "Volume"]] = [
                            row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
                        ]
                        frame = frame.sort_index()
                        repaired = True
                        break
                    except Exception:
                        continue
            source = f"Yahoo Finance Chart API ({host})"
            if repaired:
                source += f" + {repair_method}"
            return frame, {
                "source": source,
                "regular_market_date": regular_date,
                "latest_row_repaired": repaired,
            }
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Yahoo Finance Chart API取得失敗: {last_error}")


def _yahoo_recent_day(symbol, target_date):
    """短期チャートから指定日の確定OHLCVを取得する。"""
    encoded = requests.utils.quote(str(symbol), safe="")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/6.0)"}
    target_date = pd.Timestamp(target_date).normalize()
    last_error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{encoded}"
            response = requests.get(
                url,
                params={"range":"1mo", "interval":"1d", "events":"history", "includeAdjustedClose":"true"},
                headers=headers,
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("chart", {}).get("result") or []
            if not result:
                raise ValueError(payload.get("chart", {}).get("error") or "短期日足なし")
            item = result[0]
            stamps = item.get("timestamp") or []
            quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
            tz_name = (item.get("meta") or {}).get("exchangeTimezoneName") or "Asia/Tokyo"
            if not stamps:
                raise ValueError("短期日足タイムスタンプなし")
            size = len(stamps)
            idx = pd.to_datetime(stamps, unit="s", utc=True)
            try:
                idx = idx.tz_convert(tz_name)
            except Exception:
                pass
            idx = idx.tz_localize(None).normalize()
            recent = pd.DataFrame({
                "Open": (list(quote.get("open") or []) + [np.nan] * size)[:size],
                "High": (list(quote.get("high") or []) + [np.nan] * size)[:size],
                "Low": (list(quote.get("low") or []) + [np.nan] * size)[:size],
                "Close": (list(quote.get("close") or []) + [np.nan] * size)[:size],
                "Volume": (list(quote.get("volume") or []) + [np.nan] * size)[:size],
            }, index=idx)
            recent = recent[~recent.index.duplicated(keep="last")]
            if target_date not in recent.index:
                raise ValueError(f"{target_date.date()}の短期日足なし")
            row = recent.loc[target_date]
            values = {k: safe_float(row[k], np.nan) for k in ["Open", "High", "Low", "Close", "Volume"]}
            if not all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open", "High", "Low", "Close"]):
                raise ValueError("短期日足OHLC不正")
            if not np.isfinite(values["Volume"]):
                values["Volume"] = 0.0
            return values, f"短期日足再取得 ({host})"
        except Exception as e:
            last_error = e
    raise RuntimeError(f"短期日足再取得失敗: {last_error}")


class _SimpleTableParser(HTMLParser):
    """外部ライブラリを追加せずHTML表を読み取る最小パーサー。"""
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("th", "td") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("th", "td") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _page_number(value):
    text = str(value).replace(",", "").replace("円", "").replace("株", "").strip()
    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", text)
    return safe_float(match.group(0), np.nan) if match else np.nan


def _minkabu_stock_day(symbol, target_date):
    """みんかぶ国内株の公開時系列表から指定日の東証OHLCVを取得する。"""
    symbol = str(symbol).strip()
    if not symbol.endswith(".T"):
        raise ValueError("日本株以外はみんかぶ補完の対象外です")
    stock_code = symbol[:-2]
    if not re.fullmatch(r"[0-9A-Za-z]{4,5}", stock_code):
        raise ValueError("銘柄コード形式が不正です")
    url = f"https://minkabu.jp/stock/{stock_code}/daily_bar"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    parser = _SimpleTableParser()
    parser.feed(response.text)
    target_date = pd.Timestamp(target_date).normalize()

    aliases = {
        "Date": ("日時", "日付"), "Open": ("始値",), "High": ("高値",),
        "Low": ("安値",), "Close": ("終値",), "Volume": ("出来高", "出来高(株)"),
    }
    for table in parser.tables:
        for header_pos, header in enumerate(table):
            positions = {}
            for key, names in aliases.items():
                for i, cell in enumerate(header):
                    if any(name in cell for name in names):
                        positions[key] = i
                        break
            if set(positions) != set(aliases):
                continue
            for cells in table[header_pos + 1:]:
                if max(positions.values()) >= len(cells):
                    continue
                row_date = pd.to_datetime(cells[positions["Date"]], errors="coerce")
                if pd.isna(row_date) or pd.Timestamp(row_date).normalize() != target_date:
                    continue
                values = {k: _page_number(cells[positions[k]]) for k in ["Open", "High", "Low", "Close", "Volume"]}
                if not all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open", "High", "Low", "Close"]):
                    raise ValueError("みんかぶ時系列のOHLCが不正です")
                if not np.isfinite(values["Volume"]):
                    values["Volume"] = 0.0
                return values, "みんかぶ国内株・東証時系列（無料）"
    raise ValueError(f"みんかぶに{target_date.date()}の時系列データがありません")


def _stooq_stock_day(symbol, target_date):
    """Stooqの無料日足CSVから指定日のOHLCVを取得する。"""
    symbol = str(symbol).strip()
    if not symbol.endswith(".T"):
        raise ValueError("日本株以外はStooq補完の対象外です")
    stock_code = symbol[:-2].lower()
    target_date = pd.Timestamp(target_date).normalize()
    d1 = (target_date - pd.Timedelta(days=10)).strftime("%Y%m%d")
    d2 = target_date.strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={stock_code}.jp&i=d&d1={d1}&d2={d2}"
    response = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    if not response.text.strip() or "No data" in response.text:
        raise ValueError("Stooq日足なし")
    frame = pd.read_csv(io.StringIO(response.text))
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        raise ValueError("Stooq CSV列不足")
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
    selected = frame.loc[frame["Date"] == target_date]
    if selected.empty:
        raise ValueError(f"Stooqに{target_date.date()}の日足がありません")
    row = selected.iloc[-1]
    values = {k: safe_float(row[k], np.nan) for k in ["Open", "High", "Low", "Close", "Volume"]}
    if not all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open", "High", "Low", "Close"]):
        raise ValueError("Stooq OHLC不正")
    if not np.isfinite(values["Volume"]):
        values["Volume"] = 0.0
    return values, "Stooq日本株日足CSV（無料）"


@st.cache_data(ttl=300, show_spinner=False)
def latest_tse_session_status(reference_symbol="7203.T"):
    """東証の直近取引日をYahooの取引時刻から確認する。

    カレンダーを推測せず、実際の regularMarketTime を使うことで、土日祝日や
    臨時休場日に当日行を誤追加しない。15:30の大引け後もデータ配信側の集計を
    待ち、16:00以降だけ当日OHLCVを確定扱いする。
    """
    encoded = requests.utils.quote(str(reference_symbol), safe="")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/17.15)"}
    last_error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            response = requests.get(
                f"https://{host}/v8/finance/chart/{encoded}",
                params={"range":"5d", "interval":"1d", "includePrePost":"false"},
                headers=headers, timeout=12,
            )
            response.raise_for_status()
            result = (response.json().get("chart", {}).get("result") or [])[0]
            meta = result.get("meta") or {}
            market_ts = meta.get("regularMarketTime")
            if not market_ts:
                raise ValueError("regularMarketTimeなし")
            market_time = pd.Timestamp(int(market_ts), unit="s", tz="UTC").tz_convert(JST)
            session_date = market_time.tz_localize(None).normalize()
            now_jst = tokyo_now()
            today = pd.Timestamp(now_jst.date())
            # 前営業日以前なら確定済み。当日分は16時以降だけ採用する。
            confirmed = bool(session_date < today or (session_date == today and now_jst.hour >= 16))
            return {
                "session_date": session_date,
                "confirmed": confirmed,
                "market_time": market_time.strftime("%Y-%m-%d %H:%M:%S JST"),
                "market_state": str(meta.get("marketState") or ""),
                "source": f"Yahoo取引時刻 ({host})",
                "error": "",
            }
        except Exception as exc:
            last_error = exc
    return {
        "session_date": pd.NaT, "confirmed": False, "market_time": "",
        "market_state": "", "source": "取得失敗", "error": str(last_error or "不明"),
    }


def _merge_same_day_quote(history, quote, session_date):
    """一括取得した確定OHLCVを、指標計算前の日足履歴へ安全に結合する。"""
    base = _normalize_ohlcv(history)
    if base.empty or not quote or pd.isna(session_date):
        return history, False
    if "Adj Close" in history.columns:
        adjusted = pd.to_numeric(history["Adj Close"], errors="coerce")
        adjusted.index = pd.DatetimeIndex(pd.to_datetime(adjusted.index)).tz_localize(None).normalize()
        base["Adj Close"] = adjusted.reindex(base.index)
    session_date = pd.Timestamp(session_date).normalize()
    latest = pd.Timestamp(base.index.max()).normalize()
    if latest >= session_date:
        return base, False
    values = {k: safe_float(quote.get(k), np.nan) for k in ["Open","High","Low","Close","Volume"]}
    valid = (
        all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open","High","Low","Close"])
        and values["Low"] <= min(values["Open"], values["Close"])
        and values["High"] >= max(values["Open"], values["Close"])
    )
    if not valid:
        return base, False
    volume = values["Volume"] if np.isfinite(values["Volume"]) and values["Volume"] >= 0 else 0.0
    base.loc[session_date, ["Open","High","Low","Close","Volume"]] = [
        values["Open"], values["High"], values["Low"], values["Close"], volume
    ]
    if "Adj Close" in base:
        previous = base["Adj Close"].div(base["Close"]).dropna()
        ratio = float(previous.iloc[-1]) if not previous.empty else 1.0
        base.loc[session_date, "Adj Close"] = values["Close"] * ratio
    return base.sort_index(), True


@st.cache_data(ttl=300)
def tradingview_batch_quotes(tickers_tuple):
    """TradingView公開スキャナーから東証銘柄を1回でまとめて取得する。"""
    requested = [str(t) for t in tickers_tuple if str(t).endswith(".T")]
    tv_symbols = [f"TSE:{t[:-2]}" for t in requested]
    columns = ["open", "high", "low", "close", "volume"]
    payload = {
        "symbols": {"tickers": tv_symbols, "query": {"types": []}},
        "columns": columns,
        "range": [0, max(len(tv_symbols), 1)],
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
        "User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/6.0)",
    }
    diagnostics = []
    quotes = {}
    for endpoint in (
        "https://scanner.tradingview.com/japan/scan",
        "https://scanner.tradingview.com/global/scan",
    ):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
            diagnostics.append({
                "段階":"TradingView一括取得", "対象":"全銘柄", "取得先":endpoint,
                "HTTP状態":response.status_code, "結果":"応答受信" if response.ok else "HTTPエラー",
                "詳細":f"応答サイズ {len(response.content):,} bytes",
            })
            response.raise_for_status()
            body = response.json()
            for item in body.get("data") or []:
                symbol = str(item.get("s", ""))
                values = list(item.get("d") or [])
                if len(values) < len(columns) or ":" not in symbol:
                    continue
                stock_code = symbol.split(":", 1)[1]
                parsed = dict(zip(columns, values))
                row = {
                    "Open": safe_float(parsed.get("open"), np.nan),
                    "High": safe_float(parsed.get("high"), np.nan),
                    "Low": safe_float(parsed.get("low"), np.nan),
                    "Close": safe_float(parsed.get("close"), np.nan),
                    "Volume": safe_float(parsed.get("volume"), 0.0),
                }
                valid = (
                    all(np.isfinite(row[k]) and row[k] > 0 for k in ["Open", "High", "Low", "Close"])
                    and row["Low"] <= min(row["Open"], row["Close"])
                    and row["High"] >= max(row["Open"], row["Close"])
                )
                if valid:
                    quotes[f"{stock_code}.T"] = row
            if quotes:
                diagnostics.append({
                    "段階":"TradingView一括取得", "対象":"全銘柄", "取得先":endpoint,
                    "HTTP状態":response.status_code, "結果":"成功", "詳細":f"有効OHLCV {len(quotes)}銘柄",
                })
                break
            diagnostics.append({
                "段階":"TradingView一括取得", "対象":"全銘柄", "取得先":endpoint,
                "HTTP状態":response.status_code, "結果":"データなし", "詳細":"有効な東証OHLCVを確認できません",
            })
        except Exception as exc:
            diagnostics.append({
                "段階":"TradingView一括取得", "対象":"全銘柄", "取得先":endpoint,
                "HTTP状態":"取得不可", "結果":"失敗", "詳細":f"{type(exc).__name__}: {str(exc)[:240]}",
            })
    return quotes, diagnostics


def _merge_tradingview_raw(raw, ticker, regular_market_date=pd.NaT):
    """指標計算前の生OHLCVへTradingViewの確定日を追加する。"""
    quote = TRADINGVIEW_QUOTES_CACHE.get(str(ticker))
    if raw is None or raw.empty or not quote:
        return raw, False, "一括価格なし"
    required_date = regular_market_date
    required_date = pd.Timestamp(required_date).normalize() if pd.notna(required_date) else pd.NaT
    normalized = _normalize_ohlcv(raw)
    if normalized.empty:
        return raw, False, "元OHLCVを正規化できない"
    latest = pd.Timestamp(normalized.index.max()).normalize()
    if pd.isna(required_date):
        return raw, False, "必要日を確認できないため不採用"
    if latest >= required_date:
        return raw, False, "既存日足が最新"
    hour = tokyo_now().hour
    if 9 <= hour < 16:
        return raw, False, "取引時間中の未確定日足は不採用"
    attrs = dict(raw.attrs)
    normalized.loc[required_date, ["Open", "High", "Low", "Close", "Volume"]] = [
        quote["Open"], quote["High"], quote["Low"], quote["Close"], max(quote["Volume"], 0.0)
    ]
    normalized = normalized.sort_index()
    normalized.attrs.update(attrs)
    return normalized, True, "TradingView一括OHLCVを生データへ追加"


def _yahoo_japan_stock_snapshot(symbol, target_date):
    """Yahoo!ファイナンス日本版から東証の最新確定OHLCVを取得する。"""
    symbol = str(symbol).strip()
    if not symbol.endswith(".T"):
        raise ValueError("日本株以外は日本版補完の対象外です")
    url = f"https://finance.yahoo.co.jp/quote/{symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    html = response.text
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.I | re.S)
    visible = html_unescape(re.sub(r"<[^>]+>", " ", visible))
    visible = re.sub(r"\s+", " ", visible)

    target_date = pd.Timestamp(target_date).normalize()
    timestamp = _json_number_from_html(html, "regularMarketTime")
    page_date = pd.NaT
    if np.isfinite(timestamp) and timestamp > 1_000_000_000:
        page_date = (
            pd.Timestamp(int(timestamp), unit="s", tz="UTC")
            .tz_convert("Asia/Tokyo").tz_localize(None).normalize()
        )
    if pd.isna(page_date):
        tokens = {
            f"{target_date.month}/{target_date.day}",
            f"{target_date.month:02d}/{target_date.day:02d}",
            target_date.strftime("%Y/%m/%d"),
        }
        if any(token in visible for token in tokens):
            page_date = target_date
    if pd.isna(page_date) or page_date != target_date:
        raise ValueError(f"日本版株価ページの日付不一致: {page_date}")

    values = {
        "Open": _json_number_from_html(html, "regularMarketOpen"),
        "High": _json_number_from_html(html, "regularMarketDayHigh"),
        "Low": _json_number_from_html(html, "regularMarketDayLow"),
        "Close": _json_number_from_html(html, "regularMarketPrice"),
        "Volume": _json_number_from_html(html, "regularMarketVolume"),
    }
    label_patterns = {
        "Open": r"始値\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*\(",
        "High": r"高値\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*\(",
        "Low": r"安値\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*\(",
        "Volume": r"出来高\s*([0-9][0-9,]*)株\s*\(",
    }
    for key, pattern in label_patterns.items():
        if not np.isfinite(values[key]):
            match = re.search(pattern, visible)
            if match:
                values[key] = safe_float(match.group(1).replace(",", ""), np.nan)
    if not all(np.isfinite(values[k]) and values[k] > 0 for k in ["Open", "High", "Low", "Close"]):
        raise ValueError("日本版株価ページから確定OHLCを取得できません")
    if not np.isfinite(values["Volume"]):
        values["Volume"] = 0.0
    return values, "Yahoo!ファイナンス日本版（東証）"


def _normalize_ohlcv(df):
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in cols):
        return pd.DataFrame()
    df = df[cols].copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=cols)


def _add_indicators(df):
    df = _normalize_ohlcv(df)
    if len(df) < 220:
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
    df["RSI"] = 100 - (100 / (1 + rs))
    tr = pd.concat([
        df.High - df.Low,
        (df.High - df.Close.shift()).abs(),
        (df.Low - df.Close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()
    df["Return_5d"] = df.Close.pct_change(5) * 100
    df["Return_25d"] = df.Close.pct_change(25) * 100
    df["Volume_Ratio"] = df.Volume / df.VOL20.replace(0, np.nan)
    return df.dropna()


def _market_indicators(raw):
    raw = _normalize_ohlcv(raw)
    if raw.empty:
        return pd.DataFrame()
    c = pd.to_numeric(raw["Close"], errors="coerce")
    out = pd.DataFrame({"Close": c})
    out["MA25"] = c.rolling(25).mean()
    out["MA75"] = c.rolling(75).mean()
    out["MA200"] = c.rolling(200).mean()
    out["MA25_Slope"] = out.MA25 - out.MA25.shift(5)
    return out.dropna()


def _market_from_close(close):
    c = pd.to_numeric(close, errors="coerce").dropna().sort_index()
    out = pd.DataFrame({"Close": c})
    out["MA25"] = c.rolling(25).mean()
    out["MA75"] = c.rolling(75).mean()
    out["MA200"] = c.rolling(200).mean()
    out["MA25_Slope"] = out.MA25 - out.MA25.shift(5)
    return out.dropna()


# RC6.8以降: TradingView行を生OHLCVへ追加した後、指標を一度だけ計算する。
@st.cache_data(ttl=1800)
def stock_data(t, years=5):
    start, end = yahoo_history_window(years)
    try:
        raw, meta = _yahoo_chart(t, years)
        raw, tv_repaired, _ = _merge_tradingview_raw(raw, t, meta.get("regular_market_date", pd.NaT))
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs.update(meta)
            if tv_repaired:
                out.attrs["source"] = "TradingView東証スキャナー一括取得（無料） + 既存履歴"
                out.attrs["latest_row_repaired"] = True
            return out
    except Exception:
        pass
    try:
        raw = yf.Ticker(t).history(start=start, end=end,
                                   auto_adjust=False, actions=False)
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance Ticker.history"
            return out
    except Exception:
        pass
    try:
        raw = yf.download(t, start=start, end=end,
                          auto_adjust=False, progress=False, threads=False)
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance download"
            return out
    except Exception:
        pass
    return pd.DataFrame()


def _append_1321_proxy_market_row(raw_market, required_date):
    """1321.Tの当日騰落率を、直近日経平均終値へ連結して市場判定を更新する。"""
    required_date = pd.Timestamp(required_date).normalize()
    if raw_market is None or raw_market.empty:
        raise ValueError("連結元の日経平均履歴がありません")
    proxy = stock_data("1321.T", 5)
    if proxy.empty or required_date not in proxy.index:
        raise ValueError("1321.Tが必要日まで更新されていません")

    # 指標計算済みデータではなく生の終値履歴へ代理値を追加する。
    # 計算済みデータを再投入すると、MA200分が二重に欠落してしまう。
    raw_market = _normalize_ohlcv(raw_market)
    market_close = pd.to_numeric(raw_market["Close"], errors="coerce").dropna()
    proxy_close = pd.to_numeric(proxy["Close"], errors="coerce").dropna()
    common = market_close.index.intersection(proxy_close.index)
    common = common[common < required_date]
    if common.empty:
        raise ValueError("日経平均と1321.Tの共通基準日がありません")
    base_date = pd.Timestamp(common.max()).normalize()
    base_index_close = float(market_close.loc[base_date])
    base_proxy_close = float(proxy_close.loc[base_date])
    required_proxy_close = float(proxy_close.loc[required_date])
    if base_index_close <= 0 or base_proxy_close <= 0 or required_proxy_close <= 0:
        raise ValueError("1321.T代理計算に使用する価格が不正です")

    proxy_return = required_proxy_close / base_proxy_close
    converted_close = base_index_close * proxy_return
    combined = market_close.copy()
    combined.loc[required_date] = converted_close
    out = _market_from_close(combined)
    out.attrs.update({
        "source": "日経平均履歴 + 1321.T（日経225連動ETF）騰落率代理",
        "regular_market_date": required_date,
        "market_mode": "1321_ETF_PROXY",
        "market_symbol": "1321.T",
        "market_name": "日経225連動ETFによる市場判定",
        "proxy_base_date": base_date,
        "proxy_base_index_close": base_index_close,
        "proxy_base_close": base_proxy_close,
        "proxy_required_close": required_proxy_close,
        "proxy_return_pct": (proxy_return - 1.0) * 100.0,
        "converted_market_close": converted_close,
    })
    return out


def _nikkei_japan_daily():
    """日経平均そのものの日足は日本経済新聞社の公式CSVを優先。"""
    response = requests.get(
        "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv",
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja-JP,ja;q=0.9"}, timeout=20,
    )
    response.raise_for_status()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = response.content.decode(encoding)
        except UnicodeError:
            continue
        for start, line in enumerate(text.splitlines()[:30]):
            if "日付" not in line:
                continue
            try:
                frame = pd.read_csv(io.StringIO("\n".join(text.splitlines()[start:])), dtype=str)
                frame.columns = [str(c).strip() for c in frame.columns]
                dates = pd.to_datetime(frame["日付"], errors="coerce")
                raw = pd.DataFrame(index=pd.DatetimeIndex(dates))
                for dest, src in (("Open", "始値"), ("High", "高値"), ("Low", "安値"), ("Close", "終値")):
                    raw[dest] = pd.to_numeric(frame[src].str.replace(",", "", regex=False), errors="coerce").to_numpy()
                raw["Volume"] = 0.0  # 指数の出来高は非提供。市場判定は終値のみ使用。
                raw = _normalize_ohlcv(raw)
                if len(raw) < 220:
                    continue
                return raw
            except (KeyError, ValueError, pd.errors.ParserError):
                continue
    raise ValueError("日経日本公式日足CSVの形式または履歴本数を確認できません")


@st.cache_data(ttl=1800)
def market_data(required_date=None):
    start, end = yahoo_history_window(5)
    required_date = pd.Timestamp(required_date).normalize() if pd.notna(required_date) else pd.NaT
    try:
        raw = _nikkei_japan_daily()
        out = _market_indicators(raw)
        latest = pd.Timestamp(raw.index.max()).normalize()
        if not out.empty and (pd.isna(required_date) or latest >= required_date):
            out.attrs.update({"source": "日経日本公式日次CSV", "market_mode": "NIKKEI225_DIRECT",
                              "market_symbol": "^N225", "market_name": "日経平均",
                              "regular_market_date": latest})
            return out
    except (requests.RequestException, ValueError):
        pass
    try:
        raw, meta = _yahoo_chart("^N225", 5)
        if pd.notna(required_date) and not raw.empty and pd.Timestamp(raw.index.max()).normalize() < required_date:
            try:
                raw, repair_source, repaired = _repair_nikkei_day(raw, required_date)
                if repaired:
                    meta["source"] = f"{meta.get('source', 'Yahoo Finance')} + {repair_source}"
                    meta["regular_market_date"] = required_date
                    meta["latest_row_repaired"] = True
            except Exception:
                pass
        out = _market_indicators(raw)
        if not out.empty:
            out.attrs.update(meta)
            out.attrs.setdefault("market_mode", "NIKKEI225_DIRECT")
            out.attrs.setdefault("market_symbol", "^N225")
            out.attrs.setdefault("market_name", "日経平均")
            if pd.notna(required_date) and pd.Timestamp(out.index.max()).normalize() < required_date:
                try:
                    return _append_1321_proxy_market_row(raw, required_date)
                except Exception:
                    pass
            return out
    except Exception:
        pass
    try:
        raw = yf.Ticker("^N225").history(start=start, end=end,
                                          auto_adjust=False, actions=False)
        out = _market_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance Ticker.history"
            out.attrs.update({"market_mode":"NIKKEI225_DIRECT","market_symbol":"^N225","market_name":"日経平均"})
            if pd.notna(required_date) and pd.Timestamp(out.index.max()).normalize() < required_date:
                try:
                    return _append_1321_proxy_market_row(raw, required_date)
                except Exception:
                    pass
            return out
    except Exception:
        pass
    try:
        raw = yf.download("^N225", start=start, end=end,
                          auto_adjust=False, progress=False, threads=False)
        out = _market_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance download"
            out.attrs.update({"market_mode":"NIKKEI225_DIRECT","market_symbol":"^N225","market_name":"日経平均"})
            if pd.notna(required_date) and pd.Timestamp(out.index.max()).normalize() < required_date:
                try:
                    return _append_1321_proxy_market_row(raw, required_date)
                except Exception:
                    pass
            return out
    except Exception:
        pass
    return pd.DataFrame()


def build_freshness_report(data, market):
    """日経平均の確定日を基準に、朝の売買判断に使える鮮度か判定する。"""
    rows = []
    market_latest = pd.NaT if market.empty else pd.Timestamp(market.index.max()).normalize()
    regular_date = market.attrs.get("regular_market_date", pd.NaT) if not market.empty else pd.NaT
    regular_date = pd.Timestamp(regular_date).normalize() if pd.notna(regular_date) else pd.NaT
    # 指数メタ情報が取れない場合でも、各銘柄の取引時刻メタ情報を照合に利用する。
    reference_dates = [regular_date] if pd.notna(regular_date) else []
    for d in data.values():
        ref = d.attrs.get("regular_market_date", pd.NaT)
        if pd.notna(ref):
            reference_dates.append(pd.Timestamp(ref).normalize())
    required_date = max(reference_dates) if reference_dates else market_latest
    market_stale = bool(market.empty or (pd.notna(required_date) and market_latest < required_date))
    market_symbol = market.attrs.get("market_symbol", "^N225") if not market.empty else "^N225"
    market_name = market.attrs.get("market_name", "日経平均") if not market.empty else "日経平均"
    rows.append({
        "種別": "市場", "コード": market_symbol, "銘柄名": market_name,
        "データ最終日": market_latest, "基準日": required_date,
        "データ元": market.attrs.get("source", "取得失敗") if not market.empty else "取得失敗",
        "鮮度": "🔴 DATA STALE" if market_stale else "🟢 OK",
    })
    stale_tickers = set()
    for t, d in data.items():
        latest = pd.Timestamp(d.index.max()).normalize()
        stale = bool(pd.notna(required_date) and latest < required_date)
        if stale:
            stale_tickers.add(t)
        rows.append({
            "種別": "個別株", "コード": code(t), "銘柄名": name(t),
            "データ最終日": latest, "基準日": required_date,
            "データ元": d.attrs.get("source", "不明"),
            "鮮度": "🔴 DATA STALE" if stale else "🟢 OK",
        })
    return pd.DataFrame(rows), market_stale, stale_tickers


@st.cache_data(ttl=3600)
def overseas_data():
    start, end = yahoo_history_window(5)
    symbols = {
        "S&P500":"^GSPC","NASDAQ":"^IXIC","NYダウ":"^DJI",
        "SOX":"^SOX","USDJPY":"USDJPY=X","米10年金利":"^TNX"
    }
    out = {}
    for label, symbol in symbols.items():
        try:
            df = yf.download(
                symbol, start=start, end=end,
                auto_adjust=False, progress=False, threads=False
            )
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                out[label] = pd.to_numeric(df["Close"], errors="coerce")
        except Exception:
            pass
    return pd.concat(out, axis=1).sort_index().ffill() if out else pd.DataFrame()

def overseas_snapshot(overseas, dt):
    base = {
        "海外為替判定":"⚪ 海外データなし","海外為替係数":0.60,
        "S&P500_5d":np.nan,"NASDAQ_5d":np.nan,"SOX_5d":np.nan,
        "USDJPY_5d":np.nan,"US10Y_5d":np.nan,"sox_score":0.0,"fx_score":0.0
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
        return float((s.iloc[-1] / s.iloc[-6] - 1) * 100)

    sp, nq, sox, fx, rate = [r5(c) for c in
        ["S&P500","NASDAQ","SOX","USDJPY","米10年金利"]]

    us = int(np.isfinite(sp) and sp > 0) + int(np.isfinite(nq) and nq > 0)
    sox_s = 1 if np.isfinite(sox) and sox > 0 else -1 if np.isfinite(sox) and sox < 0 else 0
    fx_s = 1 if np.isfinite(fx) and fx > 0 else -1 if np.isfinite(fx) and fx < 0 else 0
    rate_s = -1 if np.isfinite(rate) and rate > 3 else 1 if np.isfinite(rate) and rate < -3 else 0
    raw = us * .35 + sox_s * .20 + fx_s * .30 + rate_s * .15
    factor = float(np.clip(.75 + raw * .25, .45, 1.15))
    state = (
        "🟢 海外・為替 良好" if factor >= 1.03 else
        "🟡 海外・為替 やや良好" if factor >= .90 else
        "⚪ 海外・為替 中立" if factor >= .72 else
        "🔴 海外・為替 注意"
    )
    return {
        "海外為替判定":state,"海外為替係数":factor,
        "S&P500_5d":sp,"NASDAQ_5d":nq,"SOX_5d":sox,
        "USDJPY_5d":fx,"US10Y_5d":rate,"sox_score":float(sox_s),"fx_score":float(fx_s)
    }

# ------------------------------------------------------------
# テクニカル / 市場
# ------------------------------------------------------------
def tech_components(r, lo, hi):
    return {
        "MA25>MA75":20 * int(r.MA25 > r.MA75),
        "Close>MA200":20 * int(r.Close > r.MA200),
        "Close>MA25":15 * int(r.Close > r.MA25),
        "Volume>VOL20":15 * int(r.Volume > r.VOL20),
        "RSI":15 * int(lo <= r.RSI <= hi),
        "MA25_Slope":10 * int(r.MA25_Slope > 0),
        "MA75_Slope":5 * int(r.MA75_Slope > 0),
    }

def tech(r, lo, hi):
    return float(sum(tech_components(r, lo, hi).values()))

def market_info(m, d):
    if m.empty:
        return ("⚪ データなし", 60, .60)
    x = m[m.index <= pd.Timestamp(d)]
    if x.empty:
        return ("⚪ データなし", 60, .60)
    r = x.iloc[-1]
    p = sum([r.Close > r.MA25, r.MA25 > r.MA75, r.MA75 > r.MA200, r.MA25_Slope > 0])
    return [
        ("🔴 弱気",0,0),("🟠 やや弱気",35,.35),("⚪ 中立",60,.60),
        ("🟡 やや強気",84,.84),("🟢 強気",100,1.0)
    ][p]

def next_trade_date(index, dt):
    idx = pd.DatetimeIndex(index)
    pos = idx.searchsorted(pd.Timestamp(dt), side="right")
    return idx[pos] if pos < len(idx) else None

# ------------------------------------------------------------
# 過去実績フィルター（Ver.5.5系を継承）
# ------------------------------------------------------------
def confidence(s):
    if s["trades"] < 8:
        return 1.0
    wr = s["wins"] / s["trades"]
    pf = s["gp"] / s["gl"] if s["gl"] else 9.99
    if wr >= .55 and pf >= 1.30: return 1.15
    if wr >= .48 and pf >= 1.10: return 1.08
    if wr >= .40 and pf >= .90: return 1.00
    if wr >= .30 and pf >= .70: return .82
    return .65

def conf_points(c):
    return float(np.clip((c - .65) / .50 * 100, 0, 100))

def recent_loss_penalty(s):
    n = int(s.get("recent_losses", 0))
    return .82 if n >= 3 else .90 if n == 2 else .96 if n == 1 else 1.0

def stock_quality(s):
    n = int(s.get("trades",0)); wins = int(s.get("wins",0))
    gp = float(s.get("gp",0)); gl = float(s.get("gl",0)); recent = int(s.get("recent_losses",0))
    wr = wins/n if n else 0.0
    pf = gp/gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    avg = (gp-gl)/n if n else 0.0
    if n < 8: return 1.00, False, "実績不足（中立）", wr, pf, avg
    if n >= 12 and pf < .85 and avg < 0: return 0.00, True, "過去PF不良・期待値マイナス", wr, pf, avg
    if n >= 20 and wr < .30 and avg < 0: return 0.00, True, "過去勝率不良・期待値マイナス", wr, pf, avg
    q = 1.0; reason = "実績許容"
    if pf < .95 or avg < 0: q *= .78; reason = "過去実績を減点"
    elif pf >= 1.20 and avg > 0 and wr >= .40: q *= 1.08; reason = "過去実績を加点"
    elif pf >= 1.00 and avg >= 0: q *= 1.03; reason = "過去実績はプラス"
    if recent >= 3: q *= .88; reason += "・直近連敗"
    return float(np.clip(q,0,1.08)), False, reason, wr, pf, avg

def risk_factor_from_losses(losses):
    return .30 if losses >= 9 else .50 if losses >= 7 else 1.0

def is_blocked(dt, block_until, severe_block_until):
    return ((block_until is not None and dt <= block_until) or
            (severe_block_until is not None and dt <= severe_block_until))

# ------------------------------------------------------------
# 現在ファンダメンタルAI
# ------------------------------------------------------------
def info_num(info, *keys):
    for k in keys:
        v = info.get(k)
        if v is not None:
            x = safe_float(v)
            if np.isfinite(x):
                return x
    return np.nan

def jquants_api_key():
    """キーをコードやZIPに書き出さず、Streamlit secrets または環境変数から取得する。"""
    try:
        key = str(st.secrets.get("JQUANTS_API_KEY", "")).strip()
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("JQUANTS_API_KEY", "").strip()


def jquants_get(api_key, params):
    # 財務サマリーの独立した60回/分枠に余裕を持たせる。
    with JQUANTS_CALL_LOCK:
        now = time.monotonic()
        JQUANTS_CALL_TIMES[:] = [x for x in JQUANTS_CALL_TIMES if now - x < 61]
        if len(JQUANTS_CALL_TIMES) >= 50:
            time.sleep(max(0, 61 - (now - JQUANTS_CALL_TIMES[0])))
            now = time.monotonic()
            JQUANTS_CALL_TIMES[:] = [x for x in JQUANTS_CALL_TIMES if now - x < 61]
        JQUANTS_CALL_TIMES.append(now)
    return requests.get(
        "https://api.jquants.com/v2/fins/summary",
        headers={"x-api-key": api_key}, params=params, timeout=12,
    )


def parse_split_factor(v):
    """Yahooの lastSplitFactor 等を 5:1 -> 5.0 のように正規化する。"""
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        x = safe_float(v)
        return x if np.isfinite(x) and x > 0 else np.nan
    txt = str(v).strip().replace(" ", "")
    m = re.match(r"^([0-9.]+)[:/]([0-9.]+)$", txt)
    if m:
        a, b = safe_float(m.group(1)), safe_float(m.group(2))
        if np.isfinite(a) and np.isfinite(b) and a > 0 and b > 0:
            return a / b
    x = safe_float(txt)
    return x if np.isfinite(x) and x > 0 else np.nan


def split_date_from_info(v):
    """lastSplitDate をJSTの日付へ変換。取得不能ならNaT。"""
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return pd.NaT
        if isinstance(v, (int, float, np.integer, np.floating)):
            return pd.to_datetime(int(v), unit="s", utc=True).tz_convert(JST).tz_localize(None)
        return pd.to_datetime(v, errors="coerce")
    except Exception:
        return pd.NaT


def _split_recent(split_date, max_days=1095):
    if pd.isna(split_date):
        return False
    try:
        d = pd.Timestamp(split_date).date()
        return 0 <= (tokyo_now().date() - d).days <= int(max_days)
    except Exception:
        return False


def _adjust_by_split_to_implied(raw_value, implied_value, split_factor, recent=True):
    """
    EPSなどについて、株価/PEから逆算した同一株式基準の値と照合し、
    直近分割比率で説明できるズレだけを補正する。
    戻り値: (補正後, 補正有無, 判定文)
    """
    raw = safe_float(raw_value)
    implied = safe_float(implied_value)
    f = safe_float(split_factor)
    if not (np.isfinite(raw) and raw > 0):
        return np.nan, False, "値なし"
    if not (recent and np.isfinite(f) and f > 1.01 and np.isfinite(implied) and implied > 0):
        return raw, False, "照合材料不足" if recent else "分割補正対象外"

    def dist(x):
        return abs(math.log(max(x, 1e-12) / implied))

    candidates = [(raw, "raw"), (raw / f, "divide"), (raw * f, "multiply")]
    best_val, best_kind = min(candidates, key=lambda z: dist(z[0]))
    raw_dist, best_dist = dist(raw), dist(best_val)
    # 分割補正によって誤差が十分に縮み、補正後が暗黙値の±35%以内なら採用。
    if best_kind != "raw" and best_dist < raw_dist * 0.45 and 0.65 <= best_val / implied <= 1.35:
        op = f"÷{f:g}" if best_kind == "divide" else f"×{f:g}"
        return float(best_val), True, f"分割補正{op}"
    return raw, False, "補正根拠不足"


def _adjust_target_for_split(target, current_price, split_factor, recent=True):
    """
    アナリスト目標株価は基準日が取得できないため、極端な乖離時だけ分割比率で補正。
    補正が一意に確認できなければ使用停止して安全側へ倒す。
    """
    t = safe_float(target)
    p = safe_float(current_price)
    f = safe_float(split_factor)
    if not (np.isfinite(t) and t > 0):
        return np.nan, False, False, "目標株価なし"
    if not (np.isfinite(p) and p > 0):
        return t, False, False, "現在株価なし"
    if not (recent and np.isfinite(f) and f > 1.01):
        return t, False, False, "分割補正対象外"

    raw_ratio = t / p
    # 0.40〜2.50倍なら、通常の目標株価レンジとしてそのまま採用。
    if 0.40 <= raw_ratio <= 2.50:
        return t, False, False, "補正不要"

    candidates = [(t / f, f"÷{f:g}"), (t * f, f"×{f:g}")]
    plausible = [(v, label) for v, label in candidates if 0.40 <= v / p <= 2.50]
    if len(plausible) == 1:
        v, label = plausible[0]
        # 補正後が現在株価に明確に近づく場合だけ採用。
        if abs(math.log(v / p)) < abs(math.log(t / p)) * 0.65:
            return float(v), True, False, f"分割補正{label}"
    # 分割後基準か分割前基準かを断定できないため、適正株価算定から除外。
    return np.nan, False, True, "⚠️分割基準不明のため目標株価を除外"


DOMESTIC_FINANCE_FRESHNESS = "国内参考指標の基準日を確認（決算鮮度未保証）"


def finance_freshness_usable(freshness):
    return str(freshness) in ("Yahoo期末日を確認", DOMESTIC_FINANCE_FRESHNESS)


def _parse_domestic_finance(html, today):
    """参考指標のdlだけを読み、広告・AI要約の数値は取り込まない。"""
    def plain(value):
        return re.sub(r"\s+", "", html_unescape(re.sub(r"<[^>]+>", " ", value)))

    start = re.search(r'<(?:section|div)\b[^>]*id=[\"\']referenc[\"\']', html)
    if not start:
        raise ValueError("参考指標セクションなし（ページ変更またはアクセス制限）")
    section = html[start.start():]
    end = section.find("</section>")
    if end >= 0:
        section = section[:end]
    values = {}
    labels = {"PER（会社予想）":"forwardPE", "PBR（実績）":"priceToBook",
              "EPS（会社予想）":"forwardEps", "BPS（実績）":"bookValue",
              "ROE（実績）":"returnOnEquity", "時価総額":"marketCap",
              "発行済株式数":"sharesOutstanding"}
    reference_date = None
    forecast_period = ""
    forecast_eps_unavailable = False
    for block in re.findall(r'<dl\b[^>]*>(.*?)</dl>', section, re.S | re.I):
        dt = re.search(r'<dt\b[^>]*>(.*?)</dt>', block, re.S | re.I)
        dd = re.search(r'<dd\b[^>]*>(.*?)</dd>', block, re.S | re.I)
        if not dt or not dd:
            continue
        label = plain(dt.group(1)).replace("用語", "")
        key = labels.get(label)
        if not key:
            continue
        value_text = plain(dd.group(1)).replace("(連)", "").replace("(単)", "")
        if key == "forwardEps":
            forecast_eps_unavailable = bool(re.match(r"[-－―—]{2,}", value_text))
            period = re.search(r'\((20\d{2})/(\d{2})\)', value_text)
            if period: forecast_period = "-".join(period.groups())
        number = re.match(r'([-+−]?[0-9][0-9,]*(?:\.[0-9]+)?)', value_text)
        if not number:
            continue  # --や未提供をゼロに変換しない
        value = safe_float(number.group(1).replace(",", "").replace("−", "-"))
        if not np.isfinite(value):
            continue
        if key == "returnOnEquity":
            value /= 100.0
        elif key == "marketCap":
            if "百万円" not in value_text:
                continue
            value *= 1_000_000
        values[key] = value
        if key in ("forwardPE", "sharesOutstanding"):
            date_match = re.search(r'\((\d{1,2})/(\d{1,2})\)', value_text)
            if date_match:
                month, day = map(int, date_match.groups())
                try:
                    reference_date = today.replace(month=month, day=day)
                    if reference_date > today:
                        reference_date = reference_date.replace(year=today.year - 1)
                except ValueError:
                    reference_date = None
        if key == "forwardEps":
            period = re.search(r'\((20\d{2})/(\d{2})\)', value_text)
            if period:
                forecast_period = "-".join(period.groups())
    if not any(k in values for k in ("forwardEps", "priceToBook", "returnOnEquity")):
        raise ValueError("参考指標に有効な財務値なし")
    valid_date = reference_date is not None and 0 <= (today - reference_date).days <= 10
    valid_period = False
    if forecast_period:
        period_end = pd.Timestamp(forecast_period + "-01") + pd.offsets.MonthEnd(0)
        valid_period = -100 <= (period_end.date() - today).days <= 550
    usable = valid_date and valid_period and values.get("forwardPE", 0) > 0 and values.get("forwardEps", 0) > 0
    values["_reference_date"] = str(reference_date or "")
    values["_forecast_eps_unavailable"] = forecast_eps_unavailable
    values["_forecast_period"] = forecast_period
    values["_freshness"] = DOMESTIC_FINANCE_FRESHNESS if usable else "国内参考指標の基準日・予想期を確認できず"
    values["_source"] = "Yahoo!ファイナンス日本版・無料公開参考指標"
    values["_url"] = "https://finance.yahoo.co.jp/"
    return values


def _finance_plain(value):
    return re.sub(r"\s+", "", html_unescape(re.sub(r"<[^>]+>", " ", value)))


def _finance_number(value):
    text = _finance_plain(value).replace(",", "").replace("−", "-").replace("▲", "-")
    match = re.match(r"[-+]?[0-9]+(?:\.[0-9]+)?", text)
    return safe_float(match.group(0)) if match else np.nan


def _parse_kabutan_finance(html, today):
    """無料の基本情報ページだけを解析。広告・有料会員欄・目標株価を利用しない。"""
    values = {"_source": "株探・無料公開基本情報／業績推移", "_url": "https://kabutan.jp/"}
    header = re.search(r'<div\b[^>]*id=["\']stockinfo_i1["\'](.*?)<!--stockinfo_i1-->', html, re.S)
    if not header:
        raise ValueError("株探の銘柄情報セクションなし")
    dt = re.search(r'<time\b[^>]*datetime=["\'](\d{4}-\d{2}-\d{2})T', header.group(1))
    reference = pd.Timestamp(dt.group(1)).date() if dt else None
    quote = re.search(r'<span\b[^>]*class=["\']kabuka["\'][^>]*>(.*?)</span>', header.group(1), re.S)
    price = _finance_number(quote.group(1)) if quote else np.nan
    if np.isfinite(price) and price > 0: values["currentPrice"] = price
    ratios = re.search(r'<div\b[^>]*id=["\']stockinfo_i3["\'](.*?)</table>', html, re.S)
    if ratios:
        body = re.search(r'<tbody[^>]*>(.*?)</tbody>', ratios.group(1), re.S)
        first = re.search(r'<tr[^>]*>(.*?)</tr>', body.group(1), re.S) if body else None
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', first.group(1), re.S) if first else []
        for key, cell in zip(("forwardPE", "priceToBook"), cells):
            value = _finance_number(cell)
            if np.isfinite(value) and value > 0: values[key] = value
    block = re.search(r'<div\b[^>]*class=["\']gyouseki_block["\'](.*?)</table>', html, re.S)
    if not block:
        raise ValueError("無料業績推移表なし")
    table = block.group(1)
    heads = [_finance_plain(x) for x in re.findall(r'<th\b[^>]*>(.*?)</th>', table.split('</thead>')[0], re.S)]
    if not all(key in heads for key in ("決算期", "売上高", "最終益", "１株益", "発表日")):
        raise ValueError("業績推移表の列を確認できず")
    records = []
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.S):
        cells = [_finance_plain(x) for x in re.findall(r'<t[hd]\b[^>]*>(.*?)</t[hd]>', row, re.S)]
        if len(cells) != len(heads): continue
        record = dict(zip(heads, cells))
        period = re.search(r'(20\d{2})\.(\d{2})', record["決算期"])
        if not period: continue
        record["period"] = "-".join(period.groups())
        try: record["published"] = datetime.strptime(record["発表日"], "%y/%m/%d").date()
        except ValueError: continue
        if record["published"] > today: continue
        records.append(record)
    forecasts = [r for r in records if "予" in r["決算期"]]
    actuals = sorted([r for r in records if "予" not in r["決算期"]], key=lambda r:r["period"])
    forecast = max(forecasts, key=lambda r:r["published"]) if forecasts else None
    if forecast:
        values["forwardEps"] = _finance_number(forecast["１株益"])
        values["_forecast_period"] = forecast["period"]
        values["_disclosure_date"] = str(forecast["published"])
    if actuals:
        values["trailingEps"] = _finance_number(actuals[-1]["１株益"])
        values["_actual_period"] = actuals[-1]["period"]
    if len(actuals) >= 2:
        # 同一企業・連続する年度の実績同士だけを比較。予想を実績へ混ぜない。
        latest, previous = actuals[-1], actuals[-2]
        if (pd.Period(latest["period"], freq="M") - pd.Period(previous["period"], freq="M")).n == 12:
            values["_growth_period"] = latest["period"]
            values["_growth_previous_period"] = previous["period"]
            for col, key in (("売上高", "revenueGrowth"), ("最終益", "earningsGrowth")):
                now, old = _finance_number(latest[col]), _finance_number(previous[col])
                if np.isfinite(now) and np.isfinite(old) and old > 0: values[key] = now / old - 1
    values["_reference_date"] = str(reference or "")
    values.setdefault("_forecast_period", "")
    period_ok = False
    if forecast:
        period_end = (pd.Timestamp(forecast["period"] + "-01") + pd.offsets.MonthEnd(0)).date()
        period_ok = -100 <= (period_end-today).days <= 550 and (today-forecast["published"]).days <= 200
    usable = reference is not None and 0 <= (today-reference).days <= 10 and period_ok and values.get("forwardEps", 0) > 0
    values["_freshness"] = DOMESTIC_FINANCE_FRESHNESS if usable else "株探の基準日・予想期・発表日を確認できず"
    if not any(np.isfinite(safe_float(values.get(k))) for k in ("forwardEps", "priceToBook", "trailingEps")):
        raise ValueError("無料公開財務に有効な数値なし")
    return values


def _parse_traders_finance(html, today):
    parser = DomesticHistoryParser(); parser.feed(re.sub(r"<br\s*/?>", "\n", html, flags=re.I))
    rows = parser.rows
    plain = _finance_plain(html).replace("：", ":")
    quoted = re.search(r"株価[：:]([0-9]{4}/[0-9]{2}/[0-9]{2})", plain)
    reference = pd.Timestamp(quoted.group(1)).date() if quoted else None
    values = {"_source": "トレーダーズ・ウェブ・無料公開業績表", "_reference_date": str(reference or ""),
              "_forecast_period": "", "_actual_period": "", "_freshness": "国内業績表の予想指標・基準日未確認"}
    start = next((i for i, r in enumerate(rows) if len(r) > 2 and re.fullmatch(r"20[0-9]{2}年[0-9]{2}月", r[1].strip())), None)
    if start is None or start + 1 >= len(rows): raise ValueError("業績表の決算期ヘッダーなし")
    dates, kinds = rows[start], rows[start+1]
    epsrow = next((r for r in rows[start+2:start+10] if r and _finance_plain(r[0]) == "1株利益(円)"), None)
    if not epsrow: raise ValueError("業績表に1株利益行なし")
    forecasts = [i for i in range(1, min(len(dates), len(kinds), len(epsrow))) if "本決算" in kinds[i] and "会社予想" in kinds[i]]
    actuals = [i for i in range(1, min(len(dates), len(kinds), len(epsrow))) if "本決算" in kinds[i] and "予想" not in kinds[i]]
    for cols, key, period_key in ((forecasts, "forwardEps", "_forecast_period"), (actuals, "trailingEps", "_actual_period")):
        if cols:
            i = cols[0]
            values[period_key] = dates[i].strip().replace("年", "-").replace("月", "")
            value = _finance_number(epsrow[i].splitlines()[0])
            if np.isfinite(value): values[key] = value
    # 同一ページの現在株価を読む。予想EPSは計算で捏造せず、表の通期会社予想だけを使う。
    quote_block = re.search(r'<span[^>]*class=["\'][^"\']*\bclose_price\b[^"\']*["\'][^>]*>(.*?)</span>', html, re.S)
    # 株価は日足側からも渡されるため、見つからなくても取得失敗にはしない。
    if quote_block:
        price = _finance_number(_finance_plain(quote_block.group(1)).lstrip("円"))
        if np.isfinite(price) and price > 0: values["currentPrice"] = price
    # 先頭の類似銘柄表の自銘柄行だけを利用。PERの予想/実績区分が不明なので予想PERへ転用しない。
    # 財務が部分取得でも実績EPSと対象期を明示して返す。
    if values.get("forwardEps", 0) > 0 and values.get("currentPrice", 0) > 0:
        values["forwardPE"] = values["currentPrice"] / values["forwardEps"]
        values["_per_basis"] = "同一ページ株価÷通期会社予想EPS"
    period_ok = False
    if values["_forecast_period"]:
        end = (pd.Timestamp(values["_forecast_period"]+"-01") + pd.offsets.MonthEnd(0)).date()
        period_ok = -100 <= (end-today).days <= 550
    if reference and 0 <= (today-reference).days <= 10 and period_ok and values.get("forwardEps", 0) > 0 and values.get("forwardPE", 0) > 0:
        values["_freshness"] = DOMESTIC_FINANCE_FRESHNESS
    if not any(np.isfinite(safe_float(values.get(k))) for k in ("forwardEps", "trailingEps")):
        raise ValueError("無料業績表のEPSが数値未提供")
    return values


@st.cache_resource(show_spinner=False)
def _finance_transport_state():
    # 1プロセスの全スレッドでホストごとの間隔と停止時刻を共有する。
    return {"lock": threading.Lock(), "hosts": {}, "local": threading.local()}


def _finance_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc.lower()


def _finance_paced_get(url):
    state = _finance_transport_state()
    host = _finance_host(url)
    with state["lock"]:
        meta = state["hosts"].setdefault(host, {"next": 0.0, "blocked_until": 0.0, "reason": "", "paths": {}})
        now = time.monotonic()
        if now < meta.setdefault("paths", {}).get(url, 0):
            raise RuntimeError(f"{host}: このURLはHTTP 403/405のため一時休止中")
        if now < meta["blocked_until"]:
            raise RuntimeError(f"{host}: 取得先の{meta['reason']}により一時休止中。連続再アクセスを抑制")
        slot = max(now, meta["next"])
        meta["next"] = slot + (2.5 if host.endswith("traders.co.jp") else 1.2)
    delay = slot - time.monotonic()
    if delay > 0: time.sleep(delay)
    if not hasattr(state["local"], "session"):
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html", "Accept-Language": "ja,en;q=0.5"})
        state["local"].session = session
    response = state["local"].session.get(url, timeout=(15, 20))
    with state["lock"]:
        meta["server_failures"] = meta.get("server_failures", 0) + 1 if response.status_code in (500, 502, 503, 504) else 0
        if meta["server_failures"] >= 3:
            meta["blocked_until"] = time.monotonic() + 60
            meta["reason"] = "連続したHTTP 5xx"
    if response.status_code in (403, 405):
        with state["lock"]:
            meta["paths"][url] = time.monotonic() + 900
            meta["access_failures"] = meta.get("access_failures", 0) + 1
            if meta["access_failures"] >= 3:
                meta["blocked_until"] = time.monotonic() + 900
                meta["reason"] = f"連続したHTTP {response.status_code}"
    elif response.status_code == 429:
        with state["lock"]:
            meta["blocked_until"] = time.monotonic() + 60
            meta["reason"] = "HTTP 429"
    elif response.status_code < 400:
        with state["lock"]: meta["access_failures"] = 0
    return response


@st.cache_data(ttl=300, max_entries=2000, show_spinner=False)
def _fetch_free_finance_html(url):
    # タイムアウト・接続切断も再試行。403/405/429は連打しない。
    for attempt in range(3):
        try:
            response = _finance_paced_get(url)
            response.raise_for_status()
            return response.content.decode("utf-8", errors="replace")
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if attempt < 2 and (status is None or status in (500, 502, 503, 504)):
                time.sleep(0.8 * (2 ** attempt))
                continue
            raise
    raise ValueError("無料公開ページ取得失敗")


def _finance_cache_file(t):
    if not re.fullmatch(r"[0-9A-Za-z]{4,5}\.T", str(t)):
        raise ValueError("国内株コード形式が不正")
    root = os.environ.get("FINANCE_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "jp_stock_finance_cache_v20")
    return os.path.join(root, str(t).upper() + ".json")


def _finance_cache_read(t):
    try:
        with open(_finance_cache_file(t), encoding="utf-8") as f: payload = json.load(f)
        if payload.get("schema") != 1 or payload.get("ticker") != t or not isinstance(payload.get("values"), dict):
            return None
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if fetched.tzinfo is None or fetched > tokyo_now(): return None
        if (tokyo_now() - fetched).total_seconds() > 7 * 86400: return None
        return payload
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _finance_json_safe(value):
    if isinstance(value, dict): return {k: _finance_json_safe(v) for k, v in value.items()}
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, np.bool_): return bool(value)
    if isinstance(value, list): return [_finance_json_safe(v) for v in value]
    return value


def _finance_cache_write(t, values):
    path = _finance_cache_file(t)
    temp_path = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"schema": 1, "ticker": t, "fetched_at": values["_fetched_at"], "values": _finance_json_safe(values)}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path), suffix=".tmp", delete=False) as f:
            temp_path = f.name
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
        os.replace(temp_path, path)
        return "保存済"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return "保存失敗（取得値は利用可）: " + type(exc).__name__
    finally:
        if temp_path and os.path.exists(temp_path):
            try: os.unlink(temp_path)
            except OSError: pass


def _finance_cache_fresh(payload, today_now):
    if not payload: return False
    try:
        fetched = datetime.fromisoformat(payload["fetched_at"])
        values = payload["values"]
        age = (today_now - fetched).total_seconds()
        complete = finance_freshness_usable(values.get("_freshness"))
        if not complete and values.get("_parser_version") != 33: return False
        if today_now.hour >= 16 and fetched.hour < 16: return False
        # 正常取得は当日6時間、数値不足は15分だけ再利用し無駄な再取得を防ぐ。
        ttl = 6 * 3600 if complete else 15 * 60
        reference = pd.Timestamp(values.get("_reference_date", "")).date()
        return fetched.date() == today_now.date() and 0 <= age <= ttl and 0 <= (today_now.date()-reference).days <= 10
    except (ValueError, TypeError, KeyError):
        return False


@st.cache_data(ttl=60, show_spinner=False)
def _domestic_finance_refresh(t):
    if not re.fullmatch(r"[0-9A-Za-z]{4,5}\.T", str(t)):
        raise ValueError("国内株コード形式が不正")
    now = tokyo_now()
    cached = _finance_cache_read(t)
    if _finance_cache_fresh(cached, now):
        values = dict(cached["values"])
        values["_cache_mode"] = "当日保存値（6時間以内）" if finance_freshness_usable(values.get("_freshness")) else "一部保存値（15分以内）"
        values["_attempts"] = "再アクセス抑制: " + values["_cache_mode"] + " / 前回: " + str(values.get("_attempts", ""))
        return values
    sources = [
        ("Yahoo!日本版", f"https://finance.yahoo.co.jp/quote/{t}", _parse_domestic_finance),
        ("トレーダーズ", f"https://www.traders.co.jp/stocks/61_{code(t)}/achievement", _parse_traders_finance),
        ("株探", f"https://kabutan.jp/stock/?code={code(t)}", _parse_kabutan_finance),
    ]
    attempts, partials = [], []
    for label, url, parser in sources:
        try:
            html = _fetch_free_finance_html(url)
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            if not title or not re.search(r"(?<![0-9A-Z])" + re.escape(code(t)) + r"(?:\.T)?(?![0-9A-Z])", _finance_plain(title.group(1)), re.I):
                raise ValueError("取得ページの銘柄コードを確認できず（別ページ・アクセス制限の可能性）")
            values = parser(html, now.date())
            values.update({"_url": url, "_fetched_at": tokyo_now().isoformat(), "_cache_mode": "今回取得", "_parser_version": 33})
            attempts.append(label + ": " + values.get("_freshness", "不明"))
            partials.append(values)
            if finance_freshness_usable(values.get("_freshness")):
                values["_attempts"] = " / ".join(attempts)
                values["_cache_storage"] = _finance_cache_write(t, values)
                return values
        except Exception as exc:
            attempts.append(label + ": " + type(exc).__name__ + " " + str(exc)[:160])
    if partials:
        values = max(partials, key=lambda v: sum(not k.startswith("_") for k in v))
        values["_attempts"] = " / ".join(attempts)
        # 以前の正常取得を数値不足の応答で上書きしない。
        if not cached or not finance_freshness_usable(cached["values"].get("_freshness")):
            values["_cache_storage"] = _finance_cache_write(t, values)
        return values
    if cached:
        values = dict(cached["values"])
        values.update({"_cache_mode": "過去保存値・表示のみ", "_freshness": "保存値・再取得失敗のため新規買い停止",
                       "_attempts": " / ".join(attempts) + " / 前回保存値を参考表示。最新性は未確認"})
        return values
    raise ValueError(" / ".join(attempts))


def _domestic_finance_snapshot_uncached(t):
    # Streamlitの短期メモリキャッシュより先に保存日時・使用区分を確認する。
    cached = _finance_cache_read(t)
    if _finance_cache_fresh(cached, tokyo_now()):
        values = dict(cached["values"])
        values["_cache_mode"] = "当日保存値（6時間以内）" if finance_freshness_usable(values.get("_freshness")) else "一部保存値（15分以内）"
        values["_attempts"] = "再アクセス抑制: " + values["_cache_mode"] + " / 前回: " + str(values.get("_attempts", ""))
        return values
    return _domestic_finance_refresh(t)


@st.cache_data(ttl=900, max_entries=1500, show_spinner=False)
def _domestic_finance_outcome(t, session_key):
    # 失敗も結果として短期保存し、監査→参考評価→共通判定での再通信を防ぐ。
    # 日付・16時の境界はキーで分ける。取得日時は書き換えない。
    try:
        return _domestic_finance_snapshot_uncached(t), ""
    except Exception as exc:
        return None, type(exc).__name__ + ": " + str(exc)[:1000]


def _domestic_finance_snapshot(t):
    now = tokyo_now()
    values, error = _domestic_finance_outcome(t, (str(now.date()), now.hour >= 16, 33))
    if error: raise ValueError(error)
    return values


def finance_evaluation_reason(values):
    """データ不具合と会社予想未提供・赤字を区別。買い許可には転用しない。"""
    if values.get("_cache_mode") == "過去保存値・表示のみ":
        return "過去保存値・最新未確認"
    eps = safe_float(values.get("forwardEps"))
    if np.isfinite(eps) and eps <= 0:
        return "赤字・ゼロ利益予想（PER評価対象外）"
    if values.get("_forecast_eps_unavailable") and not np.isfinite(eps):
        return "会社予想EPS未提供（予想PER算定不可）"
    if not np.isfinite(eps):
        return "予想EPS未取得・確認待ち"
    if not np.isfinite(safe_float(values.get("forwardPE"))):
        return "予想PER未取得・確認待ち"
    if not finance_freshness_usable(values.get("_freshness")):
        return "基準日・予想対象期を確認できず"
    return ""


def eps_price_consistent(price, eps, pe):
    """独立取得したEPSとPERの株価基準が10%以内で整合するか。"""
    vals = [safe_float(v) for v in (price, eps, pe)]
    return bool(all(np.isfinite(v) and v > 0 for v in vals)
                and abs(vals[1] * vals[2] / vals[0] - 1) <= .10)


def financial_unavailable_label(status, freshness):
    if str(status).startswith("評価保留:"):
        return "⚠️ " + str(status).split(":", 1)[1]
    if str(status) != "OK":
        return "⚠️ 財務取得不可・算定不能"
    if not finance_freshness_usable(freshness):
        return "⚠️ 最新性未確認"
    return ""


@st.cache_data(ttl=300)
def fundamental_snapshot(t, market_price_hint=np.nan):
    """現在情報のみ。過去バックテストには使用しない。株式分割ズレを最優先で防止する。"""
    try:
        yahoo_status = "OK"
        finance_source = "Yahoo!ファイナンス日本版・公開参考指標"
        domestic = None
        try:
            domestic = _domestic_finance_snapshot(t)
            finance_source = domestic.get("_source", finance_source)
            info = {k: v for k, v in domestic.items() if not k.startswith("_")}
            if not info:
                raise ValueError("日本版の有効な参考指標なし")
        except Exception as exc:
            domestic = None
            yahoo_status = "日本版取得失敗: " + type(exc).__name__ + ": " + str(exc)[:120]
            finance_source = "日本版取得不可（海外財務へ代替しない）"
            info = {}
        if not info:
            yahoo_status += " / 有効な財務情報なし"
        yahoo_quarter_raw = safe_float(info.get("mostRecentQuarter"))
        yahoo_quarter = (datetime.fromtimestamp(yahoo_quarter_raw, timezone.utc).date()
                          if np.isfinite(yahoo_quarter_raw) and yahoo_quarter_raw > 0 else None)
        today = tokyo_now().date()
        yahoo_quarter_age = (today - yahoo_quarter).days if yahoo_quarter else None
        info_price = info_num(info, "currentPrice", "regularMarketPrice")
        hint = safe_float(market_price_hint)
        price = hint if np.isfinite(hint) and hint > 0 else info_price
        market_cap = info_num(info, "marketCap")
        trailing_pe = info_num(info, "trailingPE")
        forward_pe = info_num(info, "forwardPE")
        price_to_book = info_num(info, "priceToBook")
        roe = info_num(info, "returnOnEquity")
        profit_margin = info_num(info, "profitMargins")
        op_margin = info_num(info, "operatingMargins")
        revenue_growth = info_num(info, "revenueGrowth")
        earnings_growth = info_num(info, "earningsGrowth")
        debt_to_equity = info_num(info, "debtToEquity")
        current_ratio = info_num(info, "currentRatio")
        free_cf = info_num(info, "freeCashflow")
        target_mean = info_num(info, "targetMeanPrice")
        shares = info_num(info, "sharesOutstanding")
        fifty_two_high = info_num(info, "fiftyTwoWeekHigh")
        fifty_two_low = info_num(info, "fiftyTwoWeekLow")
        trailing_eps = info_num(info, "trailingEps")
        forward_eps = info_num(info, "forwardEps")
        book_value = info_num(info, "bookValue")
        sector = str(info.get("sector") or "")
        industry = str(info.get("industry") or "")
        # 株価ヒントだけでは企業価値の取得成功とみなさない。
        # Yahoo側が空のinfoを返す場合、従来は中立点50と「OK」が全銘柄に付いていた。
        fundamental_fields = (trailing_pe, forward_pe, price_to_book, roe,
                              revenue_growth, earnings_growth, trailing_eps,
                              forward_eps, target_mean)
        has_fundamentals = any(np.isfinite(x) for x in fundamental_fields)
        if not has_fundamentals and yahoo_status == "OK":
            yahoo_status = "応答に財務指標なし（取得元で欠損）"

        # Yahooが返す直近株式分割情報。基準の違うEPS/目標株価を混ぜないため最優先で確認。
        split_factor_raw = info.get("lastSplitFactor")
        split_factor = parse_split_factor(split_factor_raw)
        split_date = split_date_from_info(info.get("lastSplitDate"))
        split_recent = _split_recent(split_date, max_days=1095)
        split_notes = []
        split_adjusted = False
        split_warning = False
        if split_recent and np.isfinite(split_factor) and split_factor > 1.01:
            split_notes.append(f"直近分割 {pd.Timestamp(split_date).date()} / {split_factor:g}:1")
        elif np.isfinite(split_factor) and split_factor > 1.01:
            split_notes.append(f"過去分割 {split_factor:g}:1（3年以上前）")
        else:
            split_notes.append("日本版の株式分割履歴は未提供・EPS/PER整合性で照合" if domestic else "直近分割情報なし")

        if np.isfinite(info_price) and np.isfinite(price) and price > 0:
            price_gap = abs(info_price / price - 1.0)
            if price_gap >= 0.20:
                split_warning = True
                split_notes.append(f"情報株価と日足株価に{price_gap*100:.1f}%差")

        # スコア：取得できない項目は中立にし、欠損で過剰に低評価しない。
        scores = []

        if np.isfinite(revenue_growth):
            scores.append(100 if revenue_growth >= .30 else 85 if revenue_growth >= .15 else
                          70 if revenue_growth >= .05 else 50 if revenue_growth >= 0 else 25)
        if np.isfinite(earnings_growth):
            scores.append(100 if earnings_growth >= .30 else 85 if earnings_growth >= .15 else
                          70 if earnings_growth >= .05 else 50 if earnings_growth >= 0 else 20)
        if np.isfinite(roe):
            scores.append(100 if roe >= .20 else 85 if roe >= .12 else 70 if roe >= .08 else 45 if roe >= 0 else 20)
        if np.isfinite(op_margin):
            scores.append(100 if op_margin >= .20 else 85 if op_margin >= .12 else 70 if op_margin >= .07 else 45 if op_margin >= 0 else 20)
        if np.isfinite(debt_to_equity):
            scores.append(90 if debt_to_equity <= 50 else 75 if debt_to_equity <= 100 else
                          55 if debt_to_equity <= 200 else 30)
        if np.isfinite(current_ratio):
            scores.append(90 if current_ratio >= 1.5 else 75 if current_ratio >= 1.0 else 45)
        growth_score = float(np.mean(scores)) if scores else 50.0

        # 割安度はPE/PBを単独で断定せず、成長性を加味した簡易評価。
        valuation_scores = []
        pe = forward_pe if np.isfinite(forward_pe) else trailing_pe
        if np.isfinite(pe):
            valuation_scores.append(95 if pe <= 12 else 85 if pe <= 18 else 70 if pe <= 25
                                    else 50 if pe <= 40 else 25)
        if np.isfinite(price_to_book):
            valuation_scores.append(90 if price_to_book <= 1.5 else 80 if price_to_book <= 2.5
                                    else 65 if price_to_book <= 4 else 40)
        valuation_score = float(np.mean(valuation_scores)) if valuation_scores else 50.0

        # AI参考適正株価レンジ。
        # 株式分割がある銘柄では「現在株価・EPS・目標株価」が同一株式基準かを先に照合する。
        # 一致を確認できない材料は、無理に補正せず適正株価計算から除外する。
        sec = (sector + " " + industry).lower()
        if any(k in sec for k in ["bank", "insurance", "financial"]):
            sector_pe = 12.0
        elif any(k in sec for k in ["semiconductor", "software", "technology", "electronic"]):
            sector_pe = 24.0
        elif any(k in sec for k in ["pharmaceutical", "biotech", "healthcare"]):
            sector_pe = 22.0
        elif any(k in sec for k in ["utility", "telecom", "communication"]):
            sector_pe = 15.0
        elif any(k in sec for k in ["industrial", "machinery", "steel", "metal", "auto"]):
            sector_pe = 16.0
        else:
            sector_pe = 18.0

        # EPSはPEから逆算できる場合、分割前/後基準のズレを機械的に照合。
        trailing_eps_adj = trailing_eps
        forward_eps_adj = forward_eps
        trailing_implied = price / trailing_pe if np.isfinite(price) and price > 0 and np.isfinite(trailing_pe) and trailing_pe > 0 else np.nan
        forward_implied = price / forward_pe if np.isfinite(price) and price > 0 and np.isfinite(forward_pe) and forward_pe > 0 else np.nan
        if split_recent and np.isfinite(split_factor) and split_factor > 1.01:
            trailing_eps_adj, tr_adj, tr_note = _adjust_by_split_to_implied(trailing_eps, trailing_implied, split_factor, True)
            forward_eps_adj, fw_adj, fw_note = _adjust_by_split_to_implied(forward_eps, forward_implied, split_factor, True)
            if tr_adj or fw_adj:
                split_adjusted = True
                split_notes.append("EPS:" + ",".join([n for n in [tr_note if tr_adj else "", fw_note if fw_adj else ""] if n]))
            # PE照合ができない直近分割銘柄のEPSは安全のため価値算定に使わない。
            if np.isfinite(forward_eps_adj) and not np.isfinite(forward_implied):
                forward_eps_adj = np.nan
                split_warning = True
                split_notes.append("予想EPSは分割基準を照合できず除外")
            if np.isfinite(trailing_eps_adj) and not np.isfinite(trailing_implied):
                trailing_eps_adj = np.nan
                split_warning = True
                split_notes.append("実績EPSは分割基準を照合できず除外")

        eps_for_value = forward_eps_adj if np.isfinite(forward_eps_adj) and forward_eps_adj > 0 else trailing_eps_adj
        earnings_fair_raw = eps_for_value * sector_pe if np.isfinite(eps_for_value) and eps_for_value > 0 else np.nan

        target_mean_adj, target_adj, target_blocked, target_note = _adjust_target_for_split(
            target_mean, price, split_factor, split_recent
        )
        if target_adj:
            split_adjusted = True
            split_notes.append("目標株価:" + target_note)
        if target_blocked:
            split_warning = True
            split_notes.append(target_note)

        # ------------------------------------------------------------
        # Ver.17.4 適正株価の異常値ガード
        # 現在株価・EPS理論株価・アナリスト目標株価を相互照合し、
        # 分割やデータ基準ズレで一方だけ極端な場合は、その材料を自動除外する。
        # ------------------------------------------------------------
        valuation_guard_notes = []
        eps_fair_used = earnings_fair_raw
        target_used = target_mean_adj
        eps_guarded = False
        target_guarded = False

        # 1) 現在株価に対して極端すぎる理論値は単独採用しない。
        #    EPS×業種PERは粗い推定なので、3倍超 / 1/3未満なら安全側で除外。
        if np.isfinite(eps_fair_used) and eps_fair_used > 0 and np.isfinite(price) and price > 0:
            eps_price_ratio = eps_fair_used / price
            if eps_price_ratio > 3.0 or eps_price_ratio < (1.0 / 3.0):
                if (domestic is not None and finance_freshness_usable(domestic.get("_freshness"))
                        and not split_warning and eps_price_consistent(price, forward_eps_adj, forward_pe)):
                    valuation_guard_notes.append(
                        f"EPS理論株価は現在株価の{eps_price_ratio:.2f}倍。EPS×予想PERと株価は整合。業種PER仮定による参考値")
                else:
                    valuation_guard_notes.append(
                        f"EPS理論株価が現在株価の{eps_price_ratio:.2f}倍で基準整合未確認→EPS評価除外")
                    eps_fair_used = np.nan
                    eps_guarded = True

        # 2) アナリスト目標株価も現在株価から極端なら除外。
        #    分割検出の有無にかかわらず、古い基準値・単位ズレの混入を防ぐ。
        if np.isfinite(target_used) and target_used > 0 and np.isfinite(price) and price > 0:
            target_price_ratio = target_used / price
            if target_price_ratio > 3.0 or target_price_ratio < (1.0 / 3.0):
                valuation_guard_notes.append(
                    f"目標株価が現在株価の{target_price_ratio:.2f}倍で異常域→目標株価除外"
                )
                target_used = np.nan
                target_guarded = True

        # 3) 両方とも一応妥当でも、互いに1.8倍以上食い違う場合は、
        #    現在株価から遠い方を除外して一方の異常値に引っ張られないようにする。
        if (np.isfinite(eps_fair_used) and eps_fair_used > 0 and
            np.isfinite(target_used) and target_used > 0 and
            np.isfinite(price) and price > 0):
            disagreement = max(eps_fair_used, target_used) / min(eps_fair_used, target_used)
            if disagreement >= 1.8:
                eps_dist = abs(math.log(eps_fair_used / price))
                target_dist = abs(math.log(target_used / price))
                if eps_dist >= target_dist:
                    valuation_guard_notes.append(
                        f"EPS理論株価と目標株価が{disagreement:.2f}倍乖離→EPS評価除外"
                    )
                    eps_fair_used = np.nan
                    eps_guarded = True
                else:
                    valuation_guard_notes.append(
                        f"EPS理論株価と目標株価が{disagreement:.2f}倍乖離→目標株価除外"
                    )
                    target_used = np.nan
                    target_guarded = True

        # 4) YahooのEPSとPEから逆算したEPSが大きく食い違う場合も記録。
        #    直近分割の検出漏れや株式数基準のズレを拾う二重安全装置。
        implied_eps = forward_implied if np.isfinite(forward_implied) and forward_implied > 0 else trailing_implied
        if (np.isfinite(eps_for_value) and eps_for_value > 0 and
            np.isfinite(implied_eps) and implied_eps > 0):
            eps_basis_ratio = max(eps_for_value, implied_eps) / min(eps_for_value, implied_eps)
            if eps_basis_ratio >= 1.8:
                valuation_guard_notes.append(
                    f"EPSと株価÷PERの逆算EPSが{eps_basis_ratio:.2f}倍乖離"
                )
                if np.isfinite(eps_fair_used):
                    eps_fair_used = np.nan
                    eps_guarded = True
                    valuation_guard_notes.append("EPS基準不整合→EPS評価除外")

        fair_candidates = []
        fair_weights = []
        fair_parts = []
        if np.isfinite(target_used) and target_used > 0:
            fair_candidates.append(target_used); fair_weights.append(.60); fair_parts.append("アナリスト目標")
        if np.isfinite(eps_fair_used) and eps_fair_used > 0:
            fair_candidates.append(eps_fair_used); fair_weights.append(.40); fair_parts.append(f"EPS×業種PER({sector_pe:.0f}倍)")

        if fair_candidates and np.isfinite(price) and price > 0:
            w = np.array(fair_weights[:len(fair_candidates)], dtype=float)
            w = w / w.sum()
            fair_base = float(np.average(np.array(fair_candidates, dtype=float), weights=w))
            # 分割警告が残る場合はレンジを広げず「算定不能」に倒す。
            if split_warning and split_recent:
                fair_low = fair_high = np.nan
                fair_base = np.nan
                upside = np.nan
                fair_source = "⚠️株式分割基準を確認できず算定停止"
            else:
                fair_low = fair_base * .82
                fair_high = fair_base * 1.18
                upside = (fair_base / price - 1) * 100
                fair_source = " + ".join(fair_parts)
        else:
            fair_low = fair_base = fair_high = np.nan
            upside = np.nan
            fair_source = "適正株価レンジ算出材料不足"

        valuation_guard_status = (
            "⚠️ EPS異常値ガード" if eps_guarded and not target_guarded else
            "⚠️ 目標株価異常値ガード" if target_guarded and not eps_guarded else
            "⚠️ 複数材料異常値ガード" if eps_guarded and target_guarded else
            "✅ 整合"
        )
        valuation_guard_note_text = " / ".join(dict.fromkeys([str(x) for x in valuation_guard_notes if str(x).strip()]))
        if valuation_guard_notes:
            fair_source = (fair_source + " / " if fair_source else "") + valuation_guard_status

        split_status = (
            "⚠️ 分割補正確認" if split_warning and split_recent else
            "✅ 分割補正済" if split_adjusted else
            "✅ 分割影響確認済" if split_recent else
            "— 直近分割なし"
        )
        split_note_text = " / ".join(dict.fromkeys([str(x) for x in split_notes if str(x).strip()]))

        value_score = float(np.clip(
            valuation_score * .45 + growth_score * .35 +
            (70 if np.isfinite(upside) and upside >= 30 else
             55 if np.isfinite(upside) and upside >= 10 else
             40 if np.isfinite(upside) and upside >= 0 else 20 if np.isfinite(upside) else 50) * .20,
            0, 100
        ))

        source_status = "OK" if has_fundamentals else "取得不可: 企業価値指標が全て欠損"
        evaluation_reason = finance_evaluation_reason(domestic) if domestic else "財務通信失敗・有効指標なし"
        if has_fundamentals and evaluation_reason:
            source_status = "評価保留:" + evaluation_reason
        freshness = (                     "Yahoo期末日が未来" if yahoo_quarter_age is not None and yahoo_quarter_age < 0 else
                     "Yahoo期末日が古い" if yahoo_quarter_age is not None and yahoo_quarter_age > 200 else
                     "Yahoo期末日を確認" if yahoo_quarter_age is not None else
                     "Yahoo開示期末日未提供" if has_fundamentals else "最新性を確認できず")
        if domestic is not None:
            freshness = domestic["_freshness"]
        return {
            "取得状態":source_status,
            "財務情報源":finance_source if has_fundamentals else "取得不可",
            "参考指標基準日":domestic.get("_reference_date", "") if domestic else "",
            "予想EPS対象期":domestic.get("_forecast_period", "") if domestic else "",
            "財務参照URL":domestic.get("_url", "") if domestic else "",
            "財務取得経路":domestic.get("_attempts", "") if domestic else yahoo_status,
            "業績発表日":domestic.get("_disclosure_date", "") if domestic else "",
            "実績EPS対象期":domestic.get("_actual_period", "") if domestic else "",
            "財務情報鮮度":freshness,"Yahoo期末日":str(yahoo_quarter or ""),
            "Yahoo取得状態":yahoo_status,
            "財務取得日時":domestic.get("_fetched_at", "") if domestic else "",
            "財務データ区分":domestic.get("_cache_mode", "") if domestic else "",
            "現在株価":price,"時価総額":market_cap,
            "PER":trailing_pe,"予想PER":forward_pe,"PBR":price_to_book,
            "ROE":roe,"営業利益率":op_margin,"売上成長率":revenue_growth,
            "利益成長率":earnings_growth,"D/E":debt_to_equity,
            "流動比率":current_ratio,"FCF":free_cf,"目標株価参考":target_mean,
            "52週高値":fifty_two_high,"52週安値":fifty_two_low,
            "予想EPS":forward_eps_adj,"実績EPS":trailing_eps_adj,"BPS":book_value,
            "予想EPS_元値":forward_eps,"実績EPS_元値":trailing_eps,
            "目標株価参考_補正後":target_mean_adj,
            "EPS理論株価_元":earnings_fair_raw,
            "EPS理論株価_採用後":eps_fair_used,
            "目標株価_採用後":target_used,
            "適正株価異常値ガード":valuation_guard_status,
            "適正株価クロスチェック":valuation_guard_note_text,
            "セクター":sector,"業種":industry,
            "株式分割補正":split_status,"直近分割日":str(pd.Timestamp(split_date).date()) if not pd.isna(split_date) else "",
            "直近分割比率":split_factor if np.isfinite(split_factor) else np.nan,
            "株式分割メモ":split_note_text,
            "成長性スコア":growth_score,"バリュエーションスコア":valuation_score,
            "企業価値スコア":value_score,"AI参考価値下限":fair_low,
            "AI参考価値":fair_base,"AI参考価値上限":fair_high,
            "参考価値上昇余地":upside,"価値算定根拠":fair_source,
        }
    except Exception as e:
        return {"取得状態":f"ERROR: {e}","企業価値スコア":50.0,"成長性スコア":50.0,
                "バリュエーションスコア":50.0}

# ------------------------------------------------------------
# テンバガーAI
# ------------------------------------------------------------
def tenbagger_score(f):
    cap = safe_float(f.get("時価総額"))
    growth = safe_float(f.get("成長性スコア"), 50)
    value = safe_float(f.get("企業価値スコア"), 50)
    pe = safe_float(f.get("予想PER"))
    roe = safe_float(f.get("ROE"))
    rev = safe_float(f.get("売上成長率"))
    earn = safe_float(f.get("利益成長率"))
    debt = safe_float(f.get("D/E"))

    score = 0.0
    # 小型であるほど将来の時価総額拡大余地を評価。ただし小型だけでは高得点にしない。
    if np.isfinite(cap):
        score += 20 if cap < 20e9 else 16 if cap < 50e9 else 12 if cap < 100e9 else 7 if cap < 200e9 else 2
    else:
        score += 8

    if np.isfinite(rev):
        score += 18 if rev >= .30 else 14 if rev >= .20 else 10 if rev >= .10 else 5 if rev >= 0 else 0
    else:
        score += 8

    if np.isfinite(earn):
        score += 18 if earn >= .30 else 14 if earn >= .20 else 10 if earn >= .10 else 5 if earn >= 0 else 0
    else:
        score += 8

    score += growth * .15
    score += value * .10

    if np.isfinite(roe):
        score += 8 if roe >= .20 else 6 if roe >= .12 else 3 if roe >= .08 else 0
    else:
        score += 4

    if np.isfinite(pe):
        score += 7 if pe <= 20 else 5 if pe <= 30 else 2 if pe <= 45 else 0
    else:
        score += 3

    if np.isfinite(debt):
        score += 6 if debt <= 50 else 4 if debt <= 100 else 2 if debt <= 200 else 0
    else:
        score += 3

    return clamp(score)

# ------------------------------------------------------------
# SBI証券 約定履歴CSV → 現在保有銘柄 自動復元
# スクリーンショット/OCRは使用しない
# ------------------------------------------------------------
def _num(v):
    """CSV由来の数値を安全にfloat化する。'--'や空欄はNaN。"""
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        return safe_float(v)
    s = str(v).strip().replace(",", "").replace("円", "").replace("%", "")
    if s in {"", "--", "nan", "NaN", "None"}:
        return np.nan
    return safe_float(s)


def _decode_sbi_csv(raw_bytes):
    """SBIのCSVは通常CP932。UTF-8系も受け付ける。"""
    last_error = None
    for enc in ("cp932", "shift_jis", "utf-8-sig", "utf-8"):
        try:
            return raw_bytes.decode(enc), enc
        except Exception as e:
            last_error = e
    raise ValueError(f"CSV文字コードを判定できません: {last_error}")


def normalize_sbi_uploads(value):
    """単一ファイルを行・バイトの列として反復しない。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def sbi_upload_name(value):
    return str(getattr(value, "name", None) or "約定履歴CSV")


def read_sbi_upload_bytes(value):
    """UploadedFile / BytesIO / バイト列を位置に依存せず読む。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
    elif callable(getattr(value, "getvalue", None)):
        raw = value.getvalue()
    elif callable(getattr(value, "read", None)):
        position = None
        try:
            if callable(getattr(value, "seek", None)):
                position = value.tell()
                value.seek(0)
            raw = value.read()
        finally:
            if position is not None:
                value.seek(position)
    else:
        raise ValueError("CSVの入力形式を確認できません。CSVを選択し直してください。")
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ValueError("CSVをバイト形式で読み込めません。CSVを選択し直してください。")
    if not raw:
        raise ValueError("CSVが空です。約定履歴CSVを選択し直してください。")
    return bytes(raw)


def parse_sbi_execution_csv(uploaded_file):
    """SBI『約定履歴照会』CSVを読み、明細部分だけDataFrame化する。

    ファイル先頭の検索条件・注記行を自動で飛ばし、
    『約定日,銘柄,銘柄コード,...』の見出し行から読み込む。
    """
    raw = read_sbi_upload_bytes(uploaded_file)
    txt, enc = _decode_sbi_csv(raw)
    lines = txt.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        norm = line.replace('"', '').replace(' ', '')
        if norm.startswith("約定日,銘柄,銘柄コード,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("SBI『約定履歴照会』CSVの明細見出しを見つけられません。")

    body = "\n".join(lines[header_idx:]).strip()
    df = pd.read_csv(io.StringIO(body), dtype=str)
    if df.empty:
        return pd.DataFrame(), enc

    df.columns = [str(c).strip() for c in df.columns]
    required = ["約定日", "銘柄", "銘柄コード", "取引", "預り", "約定数量", "約定単価"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("必要列がありません: " + ", ".join(missing))

    for c in df.columns:
        df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)

    df["銘柄コード"] = df["銘柄コード"].astype(str).str.extract(r"(\d{4})", expand=False)
    df["約定日"] = pd.to_datetime(df["約定日"], errors="coerce")
    df["約定数量"] = pd.to_numeric(df["約定数量"].str.replace(",", "", regex=False), errors="coerce")
    df["約定単価"] = pd.to_numeric(df["約定単価"].str.replace(",", "", regex=False), errors="coerce")
    df["預り"] = df["預り"].astype(str).str.strip()
    df["取引"] = df["取引"].astype(str).str.strip()
    df["銘柄"] = df["銘柄"].astype(str).str.strip()
    df = df.dropna(subset=["約定日", "銘柄コード", "約定数量", "約定単価"]).copy()
    df = df[df["約定数量"] > 0].copy()

    # 同じCSVを複数回アップロードしても二重計上しないための照合キー。
    key_cols = [c for c in [
        "約定日","銘柄","銘柄コード","市場","取引","期限","預り","課税",
        "約定数量","約定単価","手数料/諸経費等","税額","受渡日","受渡金額/決済損益"
    ] if c in df.columns]
    df["_source_file"] = sbi_upload_name(uploaded_file)
    df["_source_order"] = np.arange(len(df))
    df["_dedupe_key"] = df[key_cols].astype(str).agg("|".join, axis=1)
    return df, enc


def rebuild_holdings_from_trades(trades):
    """約定履歴から現在株数と平均取得単価を再構築する。

    ・現物買：保有株数と平均取得単価を加重平均で更新
    ・現物売：株数だけ減らし、残存株の平均取得単価は維持
    ・全売却後：取得単価をリセット
    ・履歴期間より前の保有を売った形跡がある場合は警告し、その口座区分は不完全扱い
    """
    if trades is None or trades.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    x = trades.copy()
    x = x.drop_duplicates("_dedupe_key", keep="first")
    # SBI CSVには約定時刻が無いため、同一日・同一銘柄では買付を先、売却を後に処理する。
    # これにより「同日に買って売った」取引を、履歴開始前保有と誤判定しにくくする。
    x["_action_rank"] = np.where(
        x["取引"].astype(str).str.contains("買", na=False), 0,
        np.where(x["取引"].astype(str).str.contains("売", na=False), 1, 2)
    )
    x = x.sort_values(["約定日", "銘柄コード", "預り", "_action_rank", "_source_order"], kind="stable").reset_index(drop=True)

    states = {}
    audit = []
    warnings = []

    for i, r in x.iterrows():
        c = str(r["銘柄コード"])
        nm = str(r.get("銘柄", STOCK_NAMES.get(c, c))).strip()
        acct = str(r.get("預り", "不明")).strip() or "不明"
        trade = str(r.get("取引", "")).strip()
        qty = int(round(float(r["約定数量"])))
        price = float(r["約定単価"])
        key = (c, acct)

        STOCK_NAMES[c] = nm or STOCK_NAMES.get(c, c)
        stt = states.setdefault(key, {
            "code": c, "name": nm, "account": acct,
            "shares": 0, "avg_price": np.nan,
            "complete": True, "warning": "", "buy_count": 0, "sell_count": 0,
            "first_date": r["約定日"], "last_date": r["約定日"]
        })
        before_sh = int(stt["shares"])
        before_avg = stt["avg_price"]
        action = "対象外"

        if "現物買" in trade or ("株式" in trade and "買" in trade):
            action = "BUY"
            if before_sh <= 0 or not np.isfinite(_num(before_avg)):
                stt["shares"] = qty
                stt["avg_price"] = price
            else:
                stt["avg_price"] = (before_sh * float(before_avg) + qty * price) / (before_sh + qty)
                stt["shares"] = before_sh + qty
            stt["buy_count"] += 1

        elif "現物売" in trade or ("株式" in trade and "売" in trade):
            action = "SELL"
            stt["sell_count"] += 1
            if qty > before_sh:
                stt["complete"] = False
                msg = f"{c} {nm}（{acct}）: 履歴内の保有{before_sh}株に対して{qty}株売却。履歴開始前の保有が存在する可能性。"
                stt["warning"] = msg
                warnings.append({"コード": c, "銘柄名": nm, "預り": acct, "警告": msg})
                stt["shares"] = 0
                stt["avg_price"] = np.nan
            else:
                stt["shares"] = before_sh - qty
                if stt["shares"] == 0:
                    stt["avg_price"] = np.nan
        else:
            # 信用・投信などは現時点の国内現物保有復元には使わない。
            pass

        stt["last_date"] = r["約定日"]
        audit.append({
            "約定日": r["約定日"], "コード": c, "銘柄名": nm, "預り": acct,
            "取引": trade, "処理": action, "約定数量": qty, "約定単価": price,
            "処理前株数": before_sh, "処理前取得単価": before_avg,
            "処理後株数": stt["shares"], "処理後取得単価": stt["avg_price"],
            "履歴完全性": "OK" if stt["complete"] else "要確認",
            "元ファイル": r.get("_source_file", "")
        })

    lot_rows = []
    for (_, _), s in states.items():
        lot_rows.append({
            "コード": s["code"], "銘柄名": s["name"], "預り": s["account"],
            "株数": int(s["shares"]), "取得単価": s["avg_price"],
            "履歴完全性": "OK" if s["complete"] else "要確認",
            "警告": s["warning"], "買付回数": s["buy_count"], "売却回数": s["sell_count"],
            "履歴初日": s["first_date"], "履歴最終日": s["last_date"]
        })
    lots = pd.DataFrame(lot_rows)

    holdings_rows = []
    if not lots.empty:
        active = lots[(lots["株数"] > 0) & (lots["履歴完全性"] == "OK")].copy()
        for c, g in active.groupby("コード", sort=False):
            shares = int(g["株数"].sum())
            avg = float((g["株数"] * g["取得単価"]).sum() / shares) if shares > 0 else np.nan
            holdings_rows.append({
                "code": c,
                "name": str(g["銘柄名"].iloc[-1]),
                "shares": shares,
                "avg_price": avg,
                "account_types": " / ".join(dict.fromkeys(g["預り"].astype(str).tolist())),
                "source": "SBI約定履歴CSV自動復元",
                "history_status": "OK",
            })
    holdings = pd.DataFrame(holdings_rows)
    warning_df = pd.DataFrame(warnings).drop_duplicates() if warnings else pd.DataFrame(columns=["コード","銘柄名","預り","警告"])
    return holdings, lots, pd.DataFrame(audit), warning_df

# ------------------------------------------------------------
# 手入力した買付余力から購入株数を計算
# ------------------------------------------------------------
def build_purchase_plan(candidates, buying_power, current_assets, held_codes, max_positions,
                        max_per_stock, stop_loss_pct, reserve_pct, daily_deploy_pct,
                        risk_per_trade_pct, price_buffer_pct, allow_addon=False,
                        market_block=False):
    """S株を前提に、買付余力・リスク上限から購入株数を算出する。

    資金上限 = min(
      1銘柄最大購入額,
      1日投資上限の候補按分,
      1トレード損失許容額 ÷ 損切り率
    )
    寄付価格上振れに備え、計算用価格には price_buffer_pct を加える。
    """
    cols = [
        "購入優先度","コード","銘柄名","総合AIスコア","現在株価","計算用株価",
        "購入株数","予定購入額","余力引当額","買付余力使用率","注文想定",
        "買付可否","見送り理由","購入後推定余力"
    ]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=cols)

    bp = max(float(buying_power or 0), 0.0)
    assets = max(float(current_assets or 0), 0.0)
    if bp <= 0:
        x = candidates.head(3).copy()
        rows = []
        for i, (_, r) in enumerate(x.iterrows(), 1):
            rows.append({
                "購入優先度": i, "コード": r["コード"], "銘柄名": r["銘柄名"],
                "総合AIスコア": r["総合AIスコア"], "現在株価": r["現在株価"],
                "計算用株価": np.nan, "購入株数": 0, "予定購入額": 0,
                "余力引当額": 0, "買付余力使用率": 0.0, "注文想定": "S株",
                "買付可否": "⛔ NO BUY", "見送り理由": "買付余力が0円/未入力",
                "購入後推定余力": bp,
            })
        return pd.DataFrame(rows, columns=cols)

    x = candidates.sort_values("総合AIスコア", ascending=False).copy()
    if not allow_addon:
        x = x[~x["コード"].astype(str).isin(set(map(str, held_codes)))]

    slots = max(int(max_positions) - len(set(map(str, held_codes))), 0)
    x = x.head(min(3, slots if slots > 0 else 3)).copy()

    if x.empty:
        return pd.DataFrame(columns=cols)

    reserve_yen = bp * float(reserve_pct) / 100.0
    usable = max(bp - reserve_yen, 0.0)
    daily_cap = usable * float(daily_deploy_pct) / 100.0
    risk_yen = assets * float(risk_per_trade_pct) / 100.0
    risk_notional_cap = risk_yen / max(float(stop_loss_pct) / 100.0, 0.001)
    per_candidate_daily = daily_cap / max(len(x), 1)
    base_cap = min(float(max_per_stock), per_candidate_daily, risk_notional_cap)

    remaining = bp
    rows = []
    for i, (_, r) in enumerate(x.iterrows(), 1):
        price = float(r["現在株価"])
        calc_price = price * (1.0 + float(price_buffer_pct) / 100.0)
        reason = ""
        can_buy = True

        if market_block:
            can_buy = False
            reason = "市場環境悪化によるNO TRADE"
        elif slots <= 0:
            can_buy = False
            reason = f"最大保有銘柄数{int(max_positions)}に到達"
        elif calc_price <= 0:
            can_buy = False
            reason = "株価データ不正"

        budget = min(base_cap, remaining, max(remaining - reserve_yen, 0.0)) if can_buy else 0.0
        shares = int(math.floor(budget / calc_price)) if calc_price > 0 and budget > 0 else 0
        if shares < 1 and can_buy:
            can_buy = False
            reason = "安全余力・リスク上限内では1株も購入できない"
            shares = 0

        planned_cost = shares * price
        reserved_cost = shares * calc_price
        if reserved_cost > remaining + 1e-9:
            shares = int(math.floor(remaining / calc_price))
            planned_cost = shares * price
            reserved_cost = shares * calc_price

        if shares <= 0:
            can_buy = False
        if can_buy:
            remaining = max(remaining - reserved_cost, 0.0)

        rows.append({
            "購入優先度": i,
            "コード": str(r["コード"]),
            "銘柄名": r["銘柄名"],
            "総合AIスコア": float(r["総合AIスコア"]),
            "現在株価": price,
            "計算用株価": calc_price,
            "購入株数": int(shares),
            "予定購入額": float(planned_cost),
            "余力引当額": float(reserved_cost),
            "買付余力使用率": (reserved_cost / bp * 100.0) if bp else 0.0,
            "注文想定": "S株（価格上振れバッファ込みで株数計算）",
            "買付可否": "🟢 BUY" if can_buy and shares > 0 else "⛔ NO BUY",
            "見送り理由": reason,
            "購入後推定余力": float(remaining),
        })

    return pd.DataFrame(rows, columns=cols)


def standard_limit_up_price(reference):
    """東証の通常制限値幅による概算上限。臨時拡大・呼値単位は注文画面を優先。"""
    bands = ((100, 30), (200, 50), (500, 80), (700, 100), (1000, 150),
             (1500, 300), (2000, 400), (3000, 500), (5000, 700),
             (7000, 1000), (10000, 1500), (15000, 3000), (20000, 4000),
             (30000, 5000), (50000, 7000), (70000, 10000),
             (100000, 15000), (150000, 30000), (200000, 40000),
             (300000, 50000), (500000, 70000), (700000, 100000),
             (1000000, 150000), (1500000, 300000), (2000000, 400000))
    if not np.isfinite(reference) or reference <= 0:
        return np.nan
    for below, width in bands:
        if reference < below:
            return reference + width
    return reference * 1.4


def reference_tick_round(price, upward=True):
    """通常銘柄の呼値で丸めた参考価格（TOPIX100でも有効な粗い刻み）。"""
    p = float(price)
    for _ in range(3):
        tick = (1 if p <= 3000 else 5 if p <= 5000 else 10 if p <= 30000
                else 50 if p <= 50000 else 100 if p <= 300000
                else 500 if p <= 500000 else 1000 if p <= 3000000 else 5000)
        q = (math.ceil(p / tick) if upward else math.floor(p / tick)) * tick
        if q == p:
            return max(float(q), float(tick))
        p = q
    return max(float(p), float(tick))


def build_reference_ifdoco(candidates, take_profit=15.0, stop_pct=7.0):
    columns = ["コード", "銘柄名", "参考株数", "買い指値（参考）", "利確指値（参考）",
               "逆指値発動（参考）", "逆指値後の売却", "買い概算額", "利確率%", "逆指値率%"]
    rows = []
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=columns)
    for _, r in candidates.iterrows():
        qty = int(safe_float(r.get("通常注文参考株数"), 0))
        px = safe_float(r.get("現在株価"))
        if qty < 100 or not np.isfinite(px) or px <= 0:
            continue
        entry = reference_tick_round(px, False)
        rows.append({"コード":str(r["コード"]), "銘柄名":r["銘柄名"], "参考株数":qty,
                     "買い指値（参考）":entry,
                     "利確指値（参考）":reference_tick_round(entry * (1 + take_profit / 100), True),
                     "逆指値発動（参考）":reference_tick_round(entry * (1 - stop_pct / 100), True),
                     "逆指値後の売却":"成行", "買い概算額":qty * entry,
                     "利確率%":take_profit, "逆指値率%":stop_pct})
    return pd.DataFrame(rows, columns=columns)


def prepare_allocation_history(history):
    required = {"コード", "日付", "Open", "High", "Low", "Close"}
    if not required.issubset(history.columns):
        raise ValueError("コード・日付・Open・High・Low・Close列が必要です")
    h = history.copy()
    h["コード"] = h["コード"].astype(str).str.replace(r"\.T$|\.0$", "", regex=True)
    h["日付"] = pd.to_datetime(h["日付"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Stock Splits"]:
        if col in h:
            h[col] = pd.to_numeric(h[col], errors="coerce")
    h = h.dropna(subset=list(required - {"コード"}))
    h = h[(h[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    if h.empty:
        raise ValueError("有効な日足がありません")
    frames = {}
    excluded = []
    split_aware = "Stock Splits" in h.columns
    for c, group in h.groupby("コード"):
        g = group.sort_values("日付").drop_duplicates("日付", keep="last").set_index("日付")
        if len(g) < 80:
            excluded.append({"コード":c,"理由":"有効日足80日未満","日付":"","終値":np.nan,"前日比倍率":np.nan})
            continue
        # Yahooの分割調整済み終値で非連続・異常履歴を検出。黙って利益として計上しない。
        ratio = g["Close"].div(g["Close"].shift(1))
        broken = ratio.gt(3) | ratio.lt(0.2)
        if broken.any():
            day = broken[broken].index[0]
            excluded.append({"コード":c,"理由":"調整済み終値が3倍超/5分の1未満に非連続。要確認のため銘柄全体を除外",                             "日付":str(day.date()),"終値":float(g.loc[day,"Close"]),"前日比倍率":float(ratio.loc[day])})
            continue
        # シグナルは現行関数と同じ調整価格。注文価格はYahooの分割調整済みOHLCから当時価格へ戻す。
        x = stoch_quality_frame(g)
        for research_method in ("高値更新20・安値割れ10", "高値更新55＋MA200・安値割れ20", "63日モメンタム＋MA200"):
            research = public_replacement_frame(g, research_method)
            for field in ("BUY_研究", "SELL_研究", "ready_研究", "buy_priority"):
                x[research_method + "_" + field] = research[field]
        splits = g["Stock Splits"].fillna(0) if split_aware else pd.Series(0.0, index=g.index)
        if (splits < 0).any():
            raise ValueError(f"{c}: 株式分割比率が不正です")
        factors = splits.where(splits > 0, 1.0)
        future = factors.iloc[::-1].cumprod().iloc[::-1] / factors
        for col in ["Open", "High", "Low", "Close"]:
            x[col] = g[col] * future
        x["split"] = factors
        x["ready"] = x["STOCH_K"].notna() & x["STOCH_D"].notna()
        frames[c] = x
    if not frames:
        raise ValueError("80日以上の日足がある銘柄がありません")
    return frames, split_aware, pd.DataFrame(excluded, columns=["コード","理由","日付","終値","前日比倍率"])


def allocation_portfolio_backtest(frames, allowed_codes, start, end, initial_cash=600000,
                                  cap_yen=0, max_shares=None, lot_size=1, fee_bps=10.0, normal_cap=100000):
    """前日終値で判定→翌取引日の始値。最大3銘柄、余力は候補順に引当。"""
    selected = {c:x for c,x in frames.items() if c in set(map(str, allowed_codes))}
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    dates = sorted(set(d for x in frames.values() for d in x.index if start <= d <= end))
    if not dates or not selected:
        raise ValueError("指定期間・対象に検証可能な日足がありません")
    lookup = {c:x[[col for col in ("Open", "Close", "split", "SELL_現行", "BUY_現行", "ready", "STOCH_K", "buy_priority") if col in x.columns]].to_dict("index") for c,x in selected.items()}
    cash = float(initial_cash)
    fee = float(fee_bps) / 10000
    positions, last, sells, buys = {}, {}, {}, []
    trades, curve = [], []
    skipped = 0
    missing = 0
    for day in dates:
        bars = {c: rows[day] for c,rows in lookup.items() if day in rows}
        # 当日の分割は寄り付き前に保有株と取得単価へ反映する。
        for c,pos in positions.items():
            ratio = float(bars.get(c, {}).get("split", 1.0))
            if ratio != 1:
                old_qty = pos["qty"]
                exact_qty = old_qty * ratio
                pos["qty"] = exact_qty  # 分割/併合端数は参考評価（現金交付額は履歴にない）。
                pos["entry_unit"] /= ratio
                if c in last:
                    last[c] /= ratio
        # 売却できない欠損日は注文を持ち越す。買いは欠損日を追いかけず見送り。
        for c,reason in list(sells.items()):
            if c not in positions:
                del sells[c]
                continue
            if c not in bars:
                missing += 1
                continue
            pos = positions.pop(c)
            price = float(bars[c]["Open"])
            proceeds = pos["qty"] * price * (1 - fee)
            cash += proceeds
            pnl = proceeds - pos["cost"]
            trades.append({"コード":c,"買い日":pos["date"],"売り日":day,
                           "買い株数":pos["initial_qty"],"売り株数":pos["qty"],
                           "買い始値":pos["original_entry"],"売り始値":price,
                           "保有営業日数":sum(pos["date"] < d <= day for d in dates),
                           "買い費用込":pos["cost"],"売却費用引後":proceeds,
                           "損益":pnl,"騰落率%":pnl/pos["cost"]*100,"売り理由":reason})
            del sells[c]
        for c,b in bars.items():
            last[c] = float(b["Open"])
        equity_open = cash + sum(p["qty"] * last[c] for c,p in positions.items())
        remaining_collateral = cash
        for c,signal in buys[:3]:
            if c in positions or len(positions) >= 3:
                skipped += 1
                continue
            if c not in bars:
                missing += 1
                skipped += 1
                continue
            opening = float(bars[c]["Open"])
            ratio = float(bars[c].get("split", 1.0))
            reference = signal["close"] / ratio
            calc_price = reference * 1.05
            collateral = standard_limit_up_price(reference)
            cap = float(cap_yen) if cap_yen else equity_open / 3
            qty = min(int(min(cap, remaining_collateral) // calc_price),
                      int(remaining_collateral // collateral),
                      int(cash // (opening * (1+fee))),
                      int(cap // (opening * (1+fee))))
            if max_shares is not None:
                qty = min(qty, int(max_shares))
            if lot_size == 0:
                normal_qty = min(qty, int(normal_cap // calc_price), int(normal_cap // (opening * (1+fee))))
                normal_qty = (normal_qty // 100) * 100
                qty = normal_qty if normal_qty >= 100 else min(qty, 99)
            else:
                qty = (qty // lot_size) * lot_size
            if qty <= 0:
                skipped += 1
                continue
            cost = qty * opening * (1+fee)
            cash -= cost
            remaining_collateral -= max(qty * collateral, cost)
            remaining_collateral = min(remaining_collateral, cash)
            positions[c] = {"qty":qty,"initial_qty":qty,"cost":cost,"entry_unit":opening,
                            "original_entry":opening,"date":day}
        for c,b in bars.items():
            last[c] = float(b["Close"])
        equity = cash + sum(p["qty"] * last[c] for c,p in positions.items())
        assert cash >= -1e-6 and len(positions) <= 3
        curve.append({"日付":day,"総資産":equity,"現金":cash,"保有件数":len(positions)})
        buys = []
        for c,b in bars.items():
            if c in positions:
                if b["Close"] <= positions[c]["entry_unit"] * .93:
                    sells[c] = "終値7%損切り"
                elif bool(b["SELL_現行"]):
                    sells[c] = "研究ルール売り" if "buy_priority" in b else "Stoch DC"
            elif bool(b["BUY_現行"]) and bool(b["ready"]):
                buys.append((c,{"k":float(b.get("buy_priority", b["STOCH_K"])),"close":float(b["Close"])}))
        buys.sort(key=lambda item:(item[1]["k"],item[0]))
    t = pd.DataFrame(trades)
    e = pd.DataFrame(curve)
    opening_equity = pd.concat([pd.Series([float(initial_cash)]), e["総資産"]], ignore_index=True)
    dd = opening_equity / opening_equity.cummax() - 1
    gain = t.loc[t["損益"] > 0,"損益"].sum() if not t.empty else 0
    loss = -t.loc[t["損益"] < 0,"損益"].sum() if not t.empty else 0
    opened = pd.DataFrame([{"コード":c,"買い日":p["date"],"株数":p["qty"],
                           "取得費用込":p["cost"],"最終評価額":p["qty"]*last[c],
                           "評価損益":p["qty"]*last[c]-p["cost"]} for c,p in positions.items()])
    realized = t["損益"].sum() if not t.empty else 0
    unrealized = opened["評価損益"].sum() if not opened.empty else 0
    if not np.isclose(initial_cash + realized + unrealized, e["総資産"].iloc[-1]):
        raise ValueError("資産と実現・未実現損益が一致しません")
    summary = {"最終資産":round(e["総資産"].iloc[-1],2),
               "利益率%":round((e["総資産"].iloc[-1]/initial_cash-1)*100,2),
               "最大DD%":round(dd.min()*100,2),"決済件数":len(t),
               "勝率%":round((t["損益"] > 0).mean()*100,2) if len(t) else np.nan,
               "平均保有営業日数":round(t["保有営業日数"].mean(),2) if len(t) else np.nan,
               "PF":round(gain/loss,3) if loss else np.nan,"未決済件数":len(opened),
               "購入見送り件数":skipped,"欠損による注文待ち/見送り":missing,
               "平均投資比率%":round((1-e["現金"]/e["総資産"]).mean()*100,2),
               "対象件数":len(selected)}
    return summary,t,e,opened


def public_replacement_frame(df, method):
    """Stochを参照しない売買ルール。終値判定のため翌朝に約定する。"""
    x = df.copy().sort_index()
    close = pd.to_numeric(x["Close"], errors="coerce")
    ratio = (pd.to_numeric(x["Adj Close"], errors="coerce") / close
             if "Adj Close" in x else pd.Series(1.0, index=x.index))
    ratio = ratio.where(np.isfinite(ratio) & ratio.gt(0), 1.0)
    p = close * ratio
    high = pd.to_numeric(x["High"], errors="coerce") * ratio
    low = pd.to_numeric(x["Low"], errors="coerce") * ratio
    momentum = p.pct_change(63, fill_method=None)
    ma200 = p.rolling(200, min_periods=200).mean()
    if method == "高値更新20・安値割れ10":
        buy = p.gt(high.shift(1).rolling(20, min_periods=20).max())
        sell = p.lt(low.shift(1).rolling(10, min_periods=10).min())
        ready = momentum.notna()
    elif method == "高値更新55＋MA200・安値割れ20":
        buy = p.gt(high.shift(1).rolling(55, min_periods=55).max()) & p.gt(ma200)
        sell = p.lt(low.shift(1).rolling(20, min_periods=20).min())
        ready = ma200.notna() & momentum.notna()
    elif method == "63日モメンタム＋MA200":
        buy = momentum.gt(0) & p.gt(ma200)
        sell = momentum.le(0) | p.lt(ma200)
        ready = ma200.notna() & momentum.notna()
    else:
        raise ValueError("未対応の研究方式")
    x["BUY_研究"] = buy.fillna(False) & ready
    x["SELL_研究"] = sell.fillna(False) & ready
    x["ready_研究"] = ready
    # 高い63日騰落率を優先。Stochの値を順位に使わない。
    x["buy_priority"] = -momentum.fillna(0)
    return x


PUBLIC_RESEARCH_RESULTS_20261012 = [{'対象': '取得済み全銘柄', '方式': '現行GC', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 4.96, '最大DD%': -19.4, '決済件数': 341}, {'対象': '取得済み全銘柄', '方式': '現行GC', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 13.08, '最大DD%': -17.35, '決済件数': 187}, {'対象': '固定45（取得済み）', '方式': '現行GC', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 23.66, '最大DD%': -8.69, '決済件数': 263}, {'対象': '固定45（取得済み）', '方式': '現行GC', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 16.77, '最大DD%': -9.84, '決済件数': 167}, {'対象': '取得済み全銘柄', '方式': 'GC＋下降回避', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 11.35, '最大DD%': -12.09, '決済件数': 286}, {'対象': '取得済み全銘柄', '方式': 'GC＋下降回避', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 7.69, '最大DD%': -14.13, '決済件数': 177}, {'対象': '固定45（取得済み）', '方式': 'GC＋下降回避', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 3.73, '最大DD%': -6.08, '決済件数': 122}, {'対象': '固定45（取得済み）', '方式': 'GC＋下降回避', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 3.16, '最大DD%': -6.21, '決済件数': 81}, {'対象': '取得済み全銘柄', '方式': 'GC＋上昇確認', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': -3.98, '最大DD%': -7.94, '決済件数': 61}, {'対象': '取得済み全銘柄', '方式': 'GC＋上昇確認', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': -3.68, '最大DD%': -6.82, '決済件数': 49}, {'対象': '固定45（取得済み）', '方式': 'GC＋上昇確認', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': -2.15, '最大DD%': -2.21, '決済件数': 9}, {'対象': '固定45（取得済み）', '方式': 'GC＋上昇確認', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': -0.42, '最大DD%': -1.98, '決済件数': 8}, {'対象': '取得済み全銘柄', '方式': '高値更新20・安値割れ10', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': -13.65, '最大DD%': -26.17, '決済件数': 92}, {'対象': '取得済み全銘柄', '方式': '高値更新20・安値割れ10', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 38.96, '最大DD%': -28.01, '決済件数': 50}, {'対象': '固定45（取得済み）', '方式': '高値更新20・安値割れ10', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 1.02, '最大DD%': -14.5, '決済件数': 76}, {'対象': '固定45（取得済み）', '方式': '高値更新20・安値割れ10', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 14.66, '最大DD%': -13.18, '決済件数': 43}, {'対象': '取得済み全銘柄', '方式': '高値更新55＋MA200・安値割れ20', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 8.76, '最大DD%': -18.29, '決済件数': 50}, {'対象': '取得済み全銘柄', '方式': '高値更新55＋MA200・安値割れ20', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 90.85, '最大DD%': -15.16, '決済件数': 23}, {'対象': '固定45（取得済み）', '方式': '高値更新55＋MA200・安値割れ20', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 7.52, '最大DD%': -15.02, '決済件数': 40}, {'対象': '固定45（取得済み）', '方式': '高値更新55＋MA200・安値割れ20', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 32.39, '最大DD%': -14.36, '決済件数': 27}, {'対象': '取得済み全銘柄', '方式': '63日モメンタム＋MA200', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': 22.23, '最大DD%': -32.45, '決済件数': 50}, {'対象': '取得済み全銘柄', '方式': '63日モメンタム＋MA200', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 125.28, '最大DD%': -43.93, '決済件数': 20}, {'対象': '固定45（取得済み）', '方式': '63日モメンタム＋MA200', '期間': '前半70%', '開始': '2022-08-02', '終了': '2025-04-04', '利益率%': -7.62, '最大DD%': -24.95, '決済件数': 40}, {'対象': '固定45（取得済み）', '方式': '63日モメンタム＋MA200', '期間': '後半30%（再確認）', '開始': '2025-04-07', '終了': '2026-10-07', '利益率%': 25.84, '最大DD%': -14.93, '決済件数': 23}]


def compare_public_research_logic(frames, initial_cash=600000, fee_bps=10.0,
                                  cap_yen=100000, normal_cap=100000, lot_size=0,
                                  max_shares=None, return_details=False):
    """公開研究のトレンド着想を現行と比較。閾値最適化・学習モデルは使用しない。"""
    dates = sorted(set(d for x in frames.values() for d in x.index))
    if len(dates) < 500:
        raise ValueError("期間を分けた比較には500取引日以上が必要です")
    start = dates[200]
    split = dates[int(len(dates) * .70)]
    periods = [("前半70%", start, dates[dates.index(split)-1]),
               ("後半30%（再確認）", split, dates[-1])]
    groups = [("取得済み全銘柄", list(frames))]
    fixed = [c for c in VERIFIED_45_CODES if c in frames]
    if fixed:
        groups.append(("固定45（取得済み）", fixed))
    rows, details = [], []
    for mode in ("現行GC", "GC＋下降回避", "GC＋上昇確認",
                 "高値更新20・安値割れ10", "高値更新55＋MA200・安値割れ20", "63日モメンタム＋MA200"):
        filtered = {}
        for c, x in frames.items():
            y = x.copy()
            if mode == "GC＋下降回避":
                y["BUY_現行"] = y["BUY_現行"] & ~y["下降確認"]
            elif mode == "GC＋上昇確認":
                y["BUY_現行"] = y["BUY_現行"] & y["上昇トレンド"]
            elif mode in ("高値更新20・安値割れ10", "高値更新55＋MA200・安値割れ20", "63日モメンタム＋MA200"):
                y["BUY_現行"] = y[mode + "_BUY_研究"]
                y["SELL_現行"] = y[mode + "_SELL_研究"]
                y["ready"] = y[mode + "_ready_研究"]
                y["buy_priority"] = y[mode + "_buy_priority"]
            # Require 200 valid bars for every mode, including newly listed stocks.
            # Common availability prevents comparing different eligible histories.
            common_ready = x["高値更新55＋MA200・安値割れ20_ready_研究"].fillna(False)
            y["ready"] = y["ready"].fillna(False) & common_ready
            y["BUY_現行"] = y["BUY_現行"].fillna(False) & y["ready"]
            filtered[c] = y
        for group, codes in groups:
            for period, begin, finish in periods:
                summary, trades, curve, opened = allocation_portfolio_backtest(
                    filtered, codes, begin, finish, initial_cash=initial_cash,
                    cap_yen=cap_yen, max_shares=max_shares, lot_size=lot_size,
                    fee_bps=fee_bps, normal_cap=normal_cap)
                rows.append({"対象":group,"方式":mode,"期間":period,
                             "開始":str(begin.date()),"終了":str(finish.date()),**summary})
                details.append(({"対象": group, "方式": mode, "期間": period}, trades, curve, opened))
    result = pd.DataFrame(rows)
    return (result, details) if return_details else result


def render_public_research_logic():
    with st.expander("🧪 公開研究ロジックの比較", expanded=False):
        st.caption("トレンド追随の着想を現行GCと比較します。この研究比較は旧条件の3銘柄上限・終値7%損切り・翌日始値で検証します（現在の実売買の上限解除は未反映）。資金・予算・注文方式は下で指定します。全方式に200日以上の日足を要求します。独立3案では売り判定と候補順位もStochを使いません。FinRLの期間分割・コスト評価の考え方を取り入れた検証で、学習済みAIモデルではありません。")
        st.caption("過去の構成銘柄と当時の財務判定を再現しない参考検証です。後半30%も既存検証で見た可能性があり、未使用データとは断定できません。結果による自動切替は行いません。")
        st.info("保存済みの参考値では55日方式が有望でしたが、現在のTOPIX全体での優位性は未確認です。正式採用は行わず、初期設定は現行GCを維持します。")
        st.caption("保存済み結果：10/7の拡大対象履歴・有効344銘柄（6銘柄除外）、初期60万円・片道10bps。TOPIX全体の検証ではなく、現在の割高除外と候補順位も再現していません。")
        show_stock_table(pd.DataFrame(PUBLIC_RESEARCH_RESULTS_20261012), use_container_width=True, hide_index=True)
        upload = st.file_uploader("比較用5年日足ZIP / CSV", type=["zip", "csv"], key="v17246_research_upload")
        initial = st.number_input("ロジック比較の初期資金（円）", 10000, 200000000, 600000, 10000, key="v17248_initial")
        budget = st.selectbox("ロジック比較の1銘柄予算", ["10万円", "資産の3分の1（従来）", "15万円", "20万円"], key="v17248_budget")
        cap = {"10万円": 100000, "資産の3分の1（従来）": 0, "15万円": 150000, "20万円": 200000}[budget]
        order = st.selectbox("ロジック比較の注文方式", ["自動（通常注文優先・不足はS株）", "S株のみ（1〜99株）", "100株単位（通常注文）"], key="v17248_order")
        lot, limit = ((0, None) if order.startswith("自動") else (1, 99) if order.startswith("S株") else (100, None))
        normal_cap = st.number_input("ロジック比較の通常注文予算（円）", 10000, 2000000, 100000, 10000, key="v17248_normal")
        st.caption("現在の財務情報は過去に流用しません。割高・財務判定不能の除外と現行GCの流動性等による順位は再現せず、GC側は%K順で比較します。後半は未使用期間とは扱いません。IFDOCOの日中注文も含みません。")
        cost = st.number_input("研究比較の片道コスト（bps）", 0.0, 100.0, 10.0, 1.0, key="v17246_cost")
        signature = hashlib.sha256(upload.getvalue()).hexdigest() if upload is not None else ""
        params = {"元日足SHA256": signature, "初期資金": initial, "1銘柄予算": budget, "通常注文予算": normal_cap, "注文方式": order, "片道bps": cost, "最大保有": 3, "終値損切り%": 7, "共通最低日足": 200, "財務除外再現": False, "GC順位": "%K順（現行シグナル先行の順位は未再現）", "後半未使用保証": False}
        if upload is not None and st.button("Stochを使わない3案も含めて比較", key="v17246_research_run"):
            try:
                payload = upload.getvalue()
                if upload.name.lower().endswith(".zip"):
                    with ZipFile(io.BytesIO(payload)) as z:
                        matches = [n for n in z.namelist() if n.endswith("backtest_history_5y.csv")]
                        if len(matches) != 1:
                            raise ValueError("backtest_history_5y.csv が1つ必要です。通常の分析ZIPではなく5年日足ZIPを使用してください。")
                        history = pd.read_csv(z.open(matches[0]), dtype={"コード":str})
                else:
                    history = pd.read_csv(io.BytesIO(payload), dtype={"コード":str})
                frames, split_aware, exclusions = prepare_allocation_history(history)
                if not split_aware:
                    raise ValueError("Stock Splits列付きの新しい5年日足を使用してください")
                with st.spinner("同じ資金と売買条件で比較中…"):
                    result, details = compare_public_research_logic(frames, initial_cash=initial, fee_bps=cost, cap_yen=cap, normal_cap=normal_cap, lot_size=lot, max_shares=limit, return_details=True)
                buf = io.BytesIO()
                with ZipFile(buf, "w") as z:
                    z.writestr("comparison.csv", result.to_csv(index=False, encoding="utf-8-sig"))
                    z.writestr("parameters.csv", pd.DataFrame([params]).to_csv(index=False, encoding="utf-8-sig"))
                    z.writestr("history_exclusions.csv", exclusions.to_csv(index=False, encoding="utf-8-sig"))
                    for i, (metadata, trades, curve, opened) in enumerate(details):
                        for suffix, frame in (("trades", trades), ("curve", curve), ("open_positions", opened)):
                            export = frame.copy()
                            for field, value in metadata.items():
                                export[field] = value
                            z.writestr(f"{i:02d}_{suffix}.csv", export.to_csv(index=False, encoding="utf-8-sig"))
                st.session_state["v17248_research_result"] = {"params": params, "result": result, "exclusions": exclusions, "zip": buf.getvalue()}
            except Exception as exc:
                st.error(f"研究比較を実行できませんでした：{exc}")
        saved = st.session_state.get("v17248_research_result")
        if saved and saved["params"] == params:
            show_stock_table(saved["result"], use_container_width=True, hide_index=True)
            if not saved["exclusions"].empty:
                show_stock_table(saved["exclusions"], use_container_width=True, hide_index=True)
            st.download_button("比較結果ZIP（条件・売買明細・資産推移）", saved["zip"], "public_logic_comparison.zip", "application/zip", key="v17248_download")
        elif saved:
            st.info("日足または比較条件が変わりました。比較を再実行してください。")
        st.markdown("研究元：[AQRのトレンド追随研究](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing) / [FinRL論文](https://arxiv.org/abs/2011.09607)")


def render_allocation_research():
    with st.expander("🧪 45銘柄解除・購入額・100株単位の比較", expanded=False):
        st.caption("現在の売買代金上位銘柄を固定した参考検証です。当時の母集団・上場廃止銘柄は再現しません。45銘柄は過去結果を基に選定済みのため、過去成績だけで優劣を確定しないでください。")
        n = st.number_input("拡大対象の取得件数", 200, 500, 350, 25, key="v1724_bt_n")
        if st.button("比較用：拡大対象＋45銘柄の5年日足を作成", key="v1724_export"):
            with st.spinner("拡大対象と5年日足を取得中…"):
                broad = discover_japan_stock_universe(int(n))
                if broad.empty:
                    st.error("拡大対象を取得できません。後で再実行してください。")
                else:
                    fixed = pd.DataFrame([{"コード":c,"銘柄名":name(c),"ticker":c+".T"} for c in VERIFIED_45_CODES])
                    combined = pd.concat([broad,fixed], ignore_index=True).drop_duplicates("コード")
                    hist, failed = export_backtest_history_5y(tuple(combined["ticker"].astype(str)))
                    buf = io.BytesIO()
                    with ZipFile(buf,"w") as z:
                        for filename,frame in [("backtest_history_5y.csv",hist),("backtest_universe.csv",combined),("expanded_universe.csv",broad),("backtest_history_failures.csv",failed)]:
                            z.writestr(filename,frame.to_csv(index=False,encoding="utf-8-sig"))
                    st.session_state["v1724_history_zip"] = buf.getvalue()
                    st.success(f"取得：{hist['コード'].nunique() if not hist.empty else 0}銘柄 / 失敗：{len(failed)}件")
        if st.session_state.get("v1724_history_zip"):
            st.download_button("比較用5年日足ZIPを保存",st.session_state["v1724_history_zip"],"ver17_allocation_history_5y.zip","application/zip",key="v1724_history_dl")
        uploaded = st.file_uploader("比較用5年日足ZIPまたはCSV",type=["zip","csv"],key="v1724_history_upload")
        payload = uploaded.getvalue() if uploaded else st.session_state.get("v1724_history_zip")
        if payload is None:
            st.info("上の作成ボタン、または過去日足のアップロードで準備できます。")
            return
        try:
            broad_codes = None
            if payload[:2] == b"PK":
                with ZipFile(io.BytesIO(payload)) as z:
                    matches = [f for f in z.namelist() if f.endswith("backtest_history_5y.csv")]
                    if len(matches) != 1:
                        raise ValueError("backtest_history_5y.csvが1つ必要です（通常の分析ZIPには入っていません）")
                    history = pd.read_csv(z.open(matches[0]),dtype={"コード":str})
                    if "expanded_universe.csv" in z.namelist():
                        broad_codes = set(pd.read_csv(z.open("expanded_universe.csv"),dtype={"コード":str})["コード"])
            else:
                history = pd.read_csv(io.BytesIO(payload),dtype={"コード":str})
            frames, split_aware, history_exclusions = prepare_allocation_history(history)
            if not history_exclusions.empty:
                st.warning(f"履歴の異常・不足で{len(history_exclusions)}銘柄を除外しています。")
                show_stock_table(history_exclusions, use_container_width=True, hide_index=True)
            all_dates = sorted(set(d for x in frames.values() for d in x.index))
            first,last = all_dates[0].date(),all_dates[-1].date()
            start = st.date_input("比較開始日", min(last,first+timedelta(days=120)),min_value=first,max_value=last,key="v1724_start")
            end = st.date_input("比較終了日",last,min_value=first,max_value=last,key="v1724_end")
            initial = st.number_input("比較の初期資金（円）",10000,200000000,600000,10000,key="v1724_initial")
            fee_bps = st.number_input("片道コスト（bps、10bps＝0.1%）",0.0,100.0,10.0,1.0,key="v1724_fee")
            bt_normal_cap = st.number_input("比較時の通常注文予算上限（円）",10000,2000000,100000,10000,key="v1724_bt_normal_cap")
            st.caption("この比較は旧条件です（現在の実売買の上限解除は未反映）。全案：最大3銘柄、終値判定→翌取引日始値。自動方式は通常注文の予算内で100株単位、不可ならS株最大99株。優先度は%Kの低い順→コード。5%の価格余裕と注文余力引当を適用。IFDOCOの日中利確・逆指値はこの比較に含めません。")
            if not split_aware:
                st.warning("旧CSVには分割イベントがありません。株数・100株単位の比較には、上のボタンで新しく作成したZIPを使用してください。")
                return
            st.caption("Yahooの分割調整OHLCを分割イベントで当時価格へ近似復元します。配当現金収入、税金、併合端数の現金交付、売却余力の翌朝までの拘束は再現しません。欠損日による売却待ちは明細の件数で確認してください。")
            fixed_missing = sorted(set(VERIFIED_45_CODES)-set(frames))
            if fixed_missing:
                st.error("固定45銘柄に日足不足があります："+", ".join(fixed_missing)+"。同じ条件で比較するため再取得してください。")
                return
            groups = [("固定45",set(VERIFIED_45_CODES))]
            expanded = (broad_codes if broad_codes is not None else set(frames)) | set(VERIFIED_45_CODES)
            if expanded-set(VERIFIED_45_CODES):
                groups.append(("拡大対象＋45",expanded))
            else:
                st.warning("この日足は固定45銘柄だけです。対象拡大の比較には拡大対象ZIPを作成してください。")
            if broad_codes and broad_codes-set(frames):
                st.warning(f"拡大対象の日足不足：{len(broad_codes-set(frames))}銘柄。取得できた銘柄での参考比較になります。")
            if st.button("同じ資金で比較を実行",key="v1724_compare"):
                if start >= end:
                    raise ValueError("開始日は終了日より前にしてください")
                results = []
                configs = [("現行：資産1/3・99株",0,99,1),("資産1/3・通常注文/S株自動",0,None,0),
                           ("10万円・通常注文/S株自動",100000,None,0),("15万円・通常注文/S株自動",150000,None,0),
                           ("20万円・通常注文/S株自動",200000,None,0),("10万円・100株単位",100000,None,100)]
                with st.spinner("資金・保有枠を共通にして比較中…"):
                    for group,codes in groups:
                        for label,cap,limit,lot in configs:
                            summary,trades,curve,opened = allocation_portfolio_backtest(frames,codes,start,end,initial,cap,limit,lot,fee_bps,bt_normal_cap)
                            summary = {"対象":group,"配分":label,**summary}
                            results.append((summary,trades,curve,opened))
                st.session_state["v1724_comparison"] = results
                st.session_state["v1724_comparison_params"] = {"開始":str(start),"終了":str(end),"初期資金":initial,"片道bps":fee_bps,"通常注文予算":bt_normal_cap,"元日足": __import__('hashlib').sha256(payload).hexdigest()}
            results = st.session_state.get("v1724_comparison")
            saved = st.session_state.get("v1724_comparison_params", {})
            if saved.get("元日足") != __import__("hashlib").sha256(payload).hexdigest():
                results = None
            if results:
                params=st.session_state["v1724_comparison_params"]
                st.caption(f"表示中の結果：{params['開始']}～{params['終了']} / 初期資金{params['初期資金']:,}円 / 片道{params['片道bps']}bps。通常注文上限{params.get('通常注文予算', 100000):,}円。入力変更後は再実行してください。")
                show_stock_table(pd.DataFrame([r[0] for r in results]),use_container_width=True,hide_index=True)
                st.line_chart(pd.concat([r[2].set_index("日付")["総資産"].rename(r[0]["対象"]+" / "+r[0]["配分"]) for r in results],axis=1))
                buf=io.BytesIO()
                with ZipFile(buf,"w") as z:
                    z.writestr("comparison.csv",pd.DataFrame([r[0] for r in results]).to_csv(index=False,encoding="utf-8-sig"))
                    z.writestr("parameters.csv",pd.DataFrame([params]).to_csv(index=False,encoding="utf-8-sig"))
                    z.writestr("history_exclusions.csv",history_exclusions.to_csv(index=False,encoding="utf-8-sig"))
                    for i,(summary,trades,curve,opened) in enumerate(results):
                        for key,frame in [("trades",trades),("equity",curve),("open_positions",opened)]:
                            z.writestr(f"scenario_{i:02d}_{key}.csv",frame.to_csv(index=False,encoding="utf-8-sig"))
                st.download_button("配分比較の結果ZIP",buf.getvalue(),"ver17_allocation_comparison.zip","application/zip",key="v1724_results_dl")
        except Exception as exc:
            st.error(f"比較データを処理できません：{exc}")


def build_fixed45_purchase_plan(candidates, buying_power, current_assets, held_codes,
                                cap_yen=None, execution_mode="自動（通常注文優先・不足はS株）",
                                lot_cap=None, order_inputs_ready=True):
    """候補は余力と独立。未保有TOP3へ予算を均等配分し、余力入力後に株数を計算。"""
    cols = ["購入優先度", "コード", "銘柄名", "総合AIスコア", "現在株価", "計算用株価",
            "購入株数", "予定購入額", "余力引当額", "買付余力使用率", "注文想定",
            "買付可否", "見送り理由", "購入後推定余力", "注文区分",
            "通常注文参考株数", "通常注文概算額", "均等配分予算"]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=cols)
    held = set(map(str, held_codes or []))
    x = candidates[~candidates["コード"].astype(str).isin(held)].copy()
    x = (x.sort_values(["候補スコア", "%K", "コード"], ascending=[False, True, True])
         if "候補スコア" in x else x.sort_values(["%K", "コード"], ascending=[True, True])).head(3)
    remaining = max(float(buying_power or 0), 0)
    allocation_count = len(x)
    equal_budget = remaining / allocation_count if allocation_count else 0
    rows = []
    for i, (_, r) in enumerate(x.iterrows(), 1):
        c = str(r["コード"])
        price = safe_float(r["現在株価"])
        calc_price = price * 1.05
        collateral_price = standard_limit_up_price(price)
        allocated = min(equal_budget, remaining)
        reason = ""
        if allocated <= 0:
            reason = "買付余力が0円"
        elif not np.isfinite(calc_price) or calc_price <= 0:
            reason = "株価データ不正"
        budget = (min(allocated, float(cap_yen)) if cap_yen is not None and cap_yen > 0 else allocated) if not reason else 0
        lot_budget = min(budget, float(lot_cap)) if lot_cap is not None and lot_cap > 0 else budget
        lot_shares = (100 * (min(int(lot_budget // calc_price),
                                int(budget // collateral_price)) // 100)
                      if not reason and calc_price > 0 and np.isfinite(calc_price)
                      and collateral_price > 0 and np.isfinite(collateral_price) else 0)
        shares = (min(int(budget // calc_price), int(budget // collateral_price))
                  if calc_price > 0 and np.isfinite(calc_price) and
                  np.isfinite(collateral_price) and collateral_price > 0 else 0)
        if execution_mode == "100株単位（通常注文）":
            shares = lot_shares
            order_kind = "通常注文（100株単位）"
        elif execution_mode.startswith("自動") and lot_shares >= 100:
            shares = lot_shares
            order_kind = "通常注文（100株単位）"
        else:
            shares = min(99, shares)
            order_kind = "S株（1〜99株）"
        if shares <= 0 and not reason:
            reason = "予算・注文余力内で購入単位を満たせない"
        if shares:
            remaining -= shares * collateral_price
        rows.append({"購入優先度": i, "コード": c, "銘柄名": r["銘柄名"],
                     "総合AIスコア": r["総合AIスコア"], "現在株価": price,
                     "計算用株価": calc_price, "購入株数": shares,
                     "予定購入額": shares * price, "余力引当額": shares * collateral_price,
                     "買付余力使用率": (shares * collateral_price / float(buying_power) * 100
                                      if buying_power else 0),
                     "注文想定": order_kind, "注文区分": order_kind,
                     "通常注文参考株数": lot_shares,
                     "通常注文概算額": lot_shares * price,
                     "買付可否": ("🟡 買い候補・株数未計算" if not order_inputs_ready else "🟢 BUY" if shares else "🟠 予算不足・株数0"),
                     "見送り理由": reason, "購入後推定余力": max(remaining, 0), "均等配分予算": allocated})
    result = pd.DataFrame(rows, columns=cols)
    if not order_inputs_ready:
        for col in ("購入株数", "予定購入額", "余力引当額", "買付余力使用率",
                    "購入後推定余力", "通常注文参考株数", "通常注文概算額", "均等配分予算"):
            result[col] = np.nan
        result["注文想定"] = "入力待ち"
        result["注文区分"] = "入力待ち"
        result["見送り理由"] = "余力の入力後に参考株数を計算"
    return result


def holding_horizon_signal(frame, fundamentals, horizon, entry_price=np.nan):
    """中長期の独立シグナル。財務は現時点の判定専用で、過去検証に流用しない。"""
    if frame is None or len(frame) < (225 if horizon == "長期" else 160):
        return {"判定": "データ不足", "理由": "必要な日足が不足"}
    x = frame.sort_index().copy()
    close = pd.to_numeric(x["Close"], errors="coerce")
    if "Adj Close" in x:
        factor = (pd.to_numeric(x["Adj Close"], errors="coerce") / close).replace(
            [np.inf, -np.inf], np.nan).ffill().bfill()
        signal_price = close * factor
    else:
        signal_price = close
    signal_price = signal_price.dropna()
    if len(signal_price) < (225 if horizon == "長期" else 160):
        return {"判定": "データ不足", "理由": "調整後株価が不足"}
    last = float(close.iloc[-1])
    ma50 = signal_price.rolling(50).mean()
    ma150 = signal_price.rolling(150).mean()
    ma200 = signal_price.rolling(200).mean()
    p = float(signal_price.iloc[-1])
    roe = safe_float(fundamentals.get("ROE%", np.nan))
    sales = safe_float(fundamentals.get("売上成長率%", np.nan))
    earnings = safe_float(fundamentals.get("利益成長率%", np.nan))
    pe = safe_float(fundamentals.get("PER", np.nan))
    valid_finance = str(fundamentals.get("ファンダ取得状態", "")) == "OK"
    # Yahooに開示日がない場合、古い数字を「最新」とは断定しない。
    fresh_days = safe_float(fundamentals.get("JQuants開示経過日数", np.nan))
    if np.isfinite(fresh_days) and fresh_days > 210:
        valid_finance = False
    held = np.isfinite(entry_price) and entry_price > 0
    loss = last / entry_price - 1 if held else np.nan
    if horizon == "中期":
        momentum = p / float(signal_price.iloc[-61]) - 1
        trend = p > ma50.iloc[-1] > ma150.iloc[-1] and ma50.iloc[-1] > ma50.iloc[-21]
        quality = (valid_finance and np.isfinite(roe) and roe >= 8 and
                   np.isfinite(sales) and sales >= 0 and np.isfinite(pe) and 0 < pe <= 45)
        exit_trend = bool((signal_price.iloc[-2:] < ma50.iloc[-2:]).all())
        stop = held and loss <= -0.10
        exit_reason = "終値で取得単価−10%" if stop else "調整後終値が50日線を2日連続下回る" if exit_trend else ""
        buy = trend and quality and .03 <= momentum <= .40
        rule = "50日線>150日線・60日上昇3〜40%・ROE8%以上・売上成長0%以上・PER45以下"
    else:
        momentum = p / float(signal_price.iloc[-127]) - 1
        trend = p > ma200.iloc[-1] and ma200.iloc[-1] > ma200.iloc[-21]
        quality = (valid_finance and np.isfinite(roe) and roe >= 10 and
                   np.isfinite(sales) and sales >= 0 and np.isfinite(earnings) and earnings >= 0 and
                   np.isfinite(pe) and 0 < pe <= 35)
        exit_trend = bool((signal_price.iloc[-5:] < ma200.iloc[-5:]).all())
        stop = held and loss <= -0.18
        deterioration = held and valid_finance and np.isfinite(roe) and roe < 5
        exit_reason = ("終値で取得単価−18%" if stop else
                       "ROEが5%未満" if deterioration else
                       "調整後終値が200日線を5日連続下回る" if exit_trend else "")
        buy = trend and quality and 0 < momentum <= .80
        rule = "200日線上向き・126日上昇0〜80%・ROE10%以上・売上と利益が成長・PER35以下"
    if held:
        verdict = "売り" if exit_reason else "保有継続"
        reason = exit_reason or "撤退条件に該当せず"
    else:
        verdict = "買い候補" if buy else "見送り"
        reason = rule if buy else "トレンド・財務・上昇率の条件が未成立"
    return {"判定": verdict, "理由": reason, "終値": last,
            "上昇率%": round(momentum * 100, 2), "ROE%": roe,
            "売上成長率%": sales, "利益成長率%": earnings, "PER": pe,
            "順位スコア": (float(momentum) + max(roe, 0) / 200 if np.isfinite(roe) else float(momentum)),
            "日足最終日": str(pd.Timestamp(x.index[-1]).date())}


def build_horizon_views(data, value_df, confirmed, owned_codes, all_held_codes, horizon,
                        budget, daily_status):
    """枠別に候補と売却対象を分離。余力は他枠との共用を避ける。"""
    columns = ["コード", "銘柄名", "判定", "理由", "終値", "上昇率%", "ROE%",
               "売上成長率%", "利益成長率%", "PER", "日足最終日", "株数", "取得単価", "参考S株数"]
    if not data:
        return pd.DataFrame(columns=columns), pd.DataFrame(columns=columns), []
    financial = (value_df.set_index(value_df["コード"].astype(str)).to_dict("index")
                 if isinstance(value_df, pd.DataFrame) and not value_df.empty else {})
    fresh = (daily_status.set_index(daily_status["コード"].astype(str))["状態"].to_dict()
             if isinstance(daily_status, pd.DataFrame) and not daily_status.empty else {})
    rows, notices = [], []
    for t, frame in data.items():
        c = code(t)
        if c not in VERIFIED_45_CODES and c not in owned_codes:
            continue
        is_owned = c in owned_codes
        if not is_owned and c in all_held_codes:
            continue
        if fresh.get(c) != "🟢 当日確定":
            if is_owned:
                notices.append(f"{c} {name(c)}：確定日足を確認できず、売り判定を保留")
            continue
        h = confirmed.get(c, {}) if is_owned else {}
        signal = holding_horizon_signal(frame, financial.get(c, {}), horizon,
                                        safe_float(h.get("avg_price", np.nan)))
        if signal["判定"] == "データ不足":
            if is_owned:
                notices.append(f"{c} {name(c)}：{signal['理由']}")
            continue
        rows.append({"コード": c, "銘柄名": name(c), **signal,
                     "株数": int(h.get("shares", 0)),
                     "取得単価": h.get("avg_price", np.nan), "参考S株数": 0})
    held = pd.DataFrame([r for r in rows if r["コード"] in owned_codes], columns=columns)
    candidates = [r for r in rows if r["判定"] == "買い候補"]
    candidates.sort(key=lambda r: r["順位スコア"], reverse=True)
    slots = max(2 - len(owned_codes), 0)
    reserved = max(int(budget), 0)
    buys = []
    for r in candidates[:2]:
        row = {k: r.get(k) for k in columns}
        limit_collateral = standard_limit_up_price(r["終値"])
        allocation = min(reserved, max(int(budget) // 2, 0))
        shares = (min(99, int(allocation // (r["終値"] * 1.05)),
                      int(reserved // limit_collateral))
                  if slots > 0 and limit_collateral > 0 else 0)
        row["参考S株数"] = shares
        if shares:
            slots -= 1
            reserved -= shares * limit_collateral
        else:
            row["判定"] = "見送り"
            row["理由"] = "保有枠2銘柄または配分余力が不足"
        buys.append(row)
    return pd.DataFrame(buys, columns=columns), held, notices


AI_RESEARCH_METHODS = {
    "上昇率": "6か月上昇率を順位付け。200日線が上向きの銘柄のみ。",
    "安定トレンド": "3か月・6か月上昇率から20日変動率を差し引く。200日線が上向きの銘柄のみ。",
    "押し目": "200日線が上向きで6か月上昇中、50日線の付近に戻った銘柄。",
}


def prepare_ai_research_prices(history, allowed_codes=VERIFIED_45_CODES, min_history=300):
    """候補の比較用。調整後株価を使い分割と配当を総収益の概算として扱う。"""
    required = {"日付", "コード", "Open", "Close", "Adj Close", "Volume"}
    if not required.issubset(history.columns):
        raise ValueError("5年日足CSVに必要な列がありません: " + ", ".join(sorted(required - set(history.columns))))
    x = history[list(required)].copy()
    x["コード"] = x["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
    x = x[x["コード"].isin(set(allowed_codes))].copy()
    x["日付"] = pd.to_datetime(x["日付"], errors="coerce")
    for col in ["Open", "Close", "Adj Close", "Volume"]:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.dropna(subset=["日付", "Open", "Close", "Adj Close"])
    x = x[(x["Open"] > 0) & (x["Close"] > 0) & (x["Adj Close"] > 0)]
    x = x.drop_duplicates(["日付", "コード"], keep="last")
    counts = x.groupby("コード").size()
    x = x[x["コード"].isin(counts[counts >= min_history].index)]
    if x["コード"].nunique() < 10:
        raise ValueError("300営業日以上の履歴がある銘柄が10未満です")
    x["調整始値"] = x["Open"] * x["Adj Close"] / x["Close"]
    close = x.pivot(index="日付", columns="コード", values="Adj Close").sort_index()
    opening = x.pivot(index="日付", columns="コード", values="調整始値").reindex_like(close)
    volume = x.pivot(index="日付", columns="コード", values="Volume").reindex_like(close)
    return close, opening, volume


def ai_research_features(close, volume):
    ma50 = close.rolling(50, min_periods=50).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    mom63 = close / close.shift(63) - 1
    mom126 = close / close.shift(126) - 1
    volatility = close.pct_change(fill_method=None).rolling(20, min_periods=20).std() * np.sqrt(252)
    liquid = volume.rolling(20, min_periods=20).mean() >= 100000
    trend = (close > ma200) & (ma200 > ma200.shift(20)) & (mom126 > 0) & liquid
    return {"close": close, "ma50": ma50, "ma200": ma200,
            "mom63": mom63, "mom126": mom126, "volatility": volatility, "trend": trend}


def ai_research_rank(features, index_date, method):
    trend = features["trend"].loc[index_date]
    m126 = features["mom126"].loc[index_date]
    if method == "上昇率":
        score = m126
    elif method == "安定トレンド":
        score = (m126 + .5 * features["mom63"].loc[index_date] -
                 2.0 * features["volatility"].loc[index_date])
    elif method == "押し目":
        distance = features["close"].loc[index_date] / features["ma50"].loc[index_date] - 1
        trend = trend & distance.between(-.08, .03)
        score = m126 - (distance + .02).abs()
    else:
        raise ValueError("未知の比較方式")
    score = score.where(trend).replace([np.inf, -np.inf], np.nan).dropna()
    return score.sort_values(ascending=False)


def ai_research_backtest(close, opening, volume, method, start, end,
                         initial_cash=600000, fee_bps=10):
    """前日終値だけで判定、次の取引日の調整始値で約定。月次入替と日次撤退。"""
    features = ai_research_features(close, volume)
    dates = close.index
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    cash = float(initial_cash)
    positions = {}
    entries = {}
    trades, curve = [], []
    for i in range(250, len(dates)):
        date, signal_date = dates[i], dates[i-1]
        if date < start or date > end:
            continue
        monthly = date.to_period("M") != signal_date.to_period("M") or not curve
        ranking = ai_research_rank(features, signal_date, method) if monthly else pd.Series(dtype=float)
        for c in list(positions):
            p = opening.at[date, c]
            prev = close.at[signal_date, c]
            if not np.isfinite(p) or not np.isfinite(prev):
                continue
            trend_broken = bool((close[c].iloc[i-3:i] < features["ma200"][c].iloc[i-3:i]).all())
            stop = prev <= entries[c][0] * .88
            rotation = monthly and c not in ranking.index
            if stop or trend_broken or rotation:
                shares = positions.pop(c)
                proceeds = shares * p * (1 - fee_bps / 10000)
                cash += proceeds
                entry_price, entry_date, entry_cost = entries.pop(c)
                trades.append({"方式":method,"コード":c,"買付日":entry_date,"売却日":date,
                               "株数":shares,"取得価格_調整値":entry_price,"売却価格_調整値":p,
                               "実現損益円":proceeds - entry_cost,
                               "売り理由":"12%損切り" if stop else "200日線割れ" if trend_broken else "月次見直し"})
        if monthly:
            equity_at_open = cash + sum(
                shares * (opening.at[date,c] if np.isfinite(opening.at[date,c]) else close.at[signal_date,c])
                for c, shares in positions.items())
            for c in ranking.index:
                if len(positions) >= 3:
                    break
                if c in positions:
                    continue
                p = opening.at[date, c]
                if not np.isfinite(p) or p <= 0:
                    continue
                allocation = min(cash, equity_at_open / 3)
                shares = min(99, int(allocation // (p * (1 + fee_bps / 10000))))
                if shares < 1:
                    continue
                cost = shares * p * (1 + fee_bps / 10000)
                cash -= cost
                positions[c] = shares
                entries[c] = (p, date, cost)
        equity = cash + sum(
            shares * (close.at[date,c] if np.isfinite(close.at[date,c]) else close.at[signal_date,c])
            for c, shares in positions.items())
        curve.append({"日付":date,"方式":method,"資産円":equity,"現金円":cash,"保有件数":len(positions)})
    equity_df = pd.DataFrame(curve)
    trades_df = pd.DataFrame(trades)
    if equity_df.empty:
        raise ValueError("検証期間に日足がありません")
    equity_df["累積最高"] = equity_df["資産円"].cummax().clip(lower=initial_cash)
    equity_df["DD%"] = (equity_df["資産円"] / equity_df["累積最高"] - 1) * 100
    final = float(equity_df["資産円"].iloc[-1])
    last_date = equity_df["日付"].iloc[-1]
    open_rows = []
    for c, shares in positions.items():
        entry_price, entry_date, entry_cost = entries[c]
        current_price = close.at[last_date, c]
        if not np.isfinite(current_price):
            current_price = close[c].loc[:last_date].dropna().iloc[-1]
        open_rows.append({"方式":method,"コード":c,"買付日":entry_date,"株数":shares,
                          "取得価格_調整値":entry_price,"最終価格_調整値":current_price,
                          "取得額円":entry_cost,"最終評価額円":shares * current_price,
                          "未決済評価損益円":shares * current_price - entry_cost})
    open_df = pd.DataFrame(open_rows, columns=["方式","コード","買付日","株数",
                                               "取得価格_調整値","最終価格_調整値",
                                               "取得額円","最終評価額円","未決済評価損益円"])
    realized = float(trades_df["実現損益円"].sum()) if not trades_df.empty else 0.0
    unrealized = float(open_df["未決済評価損益円"].sum()) if not open_df.empty else 0.0
    if not np.isclose(final - initial_cash, realized + unrealized, atol=.01):
        raise ValueError("バックテストの確定損益と評価損益が資産と一致しません")
    summary = {"方式":method,"開始日":str(equity_df["日付"].iloc[0].date()),
               "終了日":str(equity_df["日付"].iloc[-1].date()),"初期資金円":int(initial_cash),
               "最終資産円":round(final),"損益率%":round((final/initial_cash-1)*100,2),
               "最大DD%":round(float(equity_df["DD%"].min()),2),
               "確定損益円":round(realized),"未決済評価損益円":round(unrealized),
               "決済件数":len(trades_df),"未決済件数":len(positions)}
    return summary, trades_df, equity_df, open_df


def compare_ai_research_methods(history, initial_cash=600000, fee_bps=10):
    close, opening, volume = prepare_ai_research_prices(history)
    if len(close) < 755:
        raise ValueError("学習・未使用期間の分離には、少なくとも約3年分の日足が必要です")
    train_start, train_end = close.index[250], close.index[-254]
    holdout_start, holdout_end = close.index[-253], close.index[-1]
    summaries, detail = [], {}
    for method in AI_RESEARCH_METHODS:
        for period, start, end in (("学習", train_start, train_end),
                                   ("未使用", holdout_start, holdout_end)):
            summary, trades, curve, positions = ai_research_backtest(
                close, opening, volume, method, start, end, initial_cash, fee_bps)
            summary["期間"] = period
            summaries.append(summary)
            detail[(method, period)] = (trades, curve, positions)
    result = pd.DataFrame(summaries)
    training = result[result["期間"].eq("学習")].copy()
    training["選定点"] = training["損益率%"] + .5 * training["最大DD%"]
    selected = str(training.sort_values(["選定点", "方式"], ascending=[False, True]).iloc[0]["方式"])
    return result, detail, selected, (close, opening, volume)


# ------------------------------------------------------------
# Ver.17.4 企業価値AI TOP50 — 株式分割＋適正株価異常値ガード付き比較検証用
# ------------------------------------------------------------
def _percentile_score(series, higher_better=True):
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() <= 1:
        return pd.Series(50.0, index=series.index)
    r = s.rank(pct=True, method="average") * 100.0
    if not higher_better:
        r = 100.0 - r
    return r.fillna(50.0).clip(0, 100)


@st.cache_data(ttl=900, max_entries=1200, show_spinner=False)
def stoch_churn_diagnostic(df):
    """判定日までの日足のみを用いた、新規買い対象の短期反転検査。"""
    empty = {"直近20日クロス回数": 0, "直近30日早期反転回数": 0,
             "25日騰落率_反転検査%": np.nan, "新規買い除外理由": ""}
    if df is None or len(df) < 60:
        return {**empty, "新規買い除外理由": "日足不足"}
    x = stoch_prepare_light(df, 14, 3, 3).tail(65)
    k, d = x["STOCH_K"], x["STOCH_D"]
    if k.tail(30).isna().any() or d.tail(30).isna().any():
        return {**empty, "新規買い除外理由": "ストキャス算定不能"}
    gc, dc = x["STOCH_GC"].fillna(False), x["STOCH_DC"].fillna(False)
    flips = int((gc | dc).tail(20).sum())
    close = pd.to_numeric(x["Close"], errors="coerce")
    ret25 = (float(close.iloc[-1] / close.iloc[-26] - 1) * 100.0
             if len(close) >= 26 and close.iloc[-26] > 0 else np.nan)
    # 買い圏のGCのあと5営業日以内にDCとなるケースを重複なく数える。
    buy_gc = (gc & (k <= 20)).to_numpy(dtype=bool)
    sell_dc = dc.to_numpy(dtype=bool)
    early = sum(bool(sell_dc[i+1:min(i+6, len(x))].any())
                for i in range(max(0, len(x)-30), len(x)) if buy_gc[i])
    reasons = []
    if flips >= 5 and np.isfinite(ret25) and abs(ret25) <= 5.0:
        reasons.append("20営業日で5回以上クロス＋25日騰落率±5%以内")
    if early >= 2:
        reasons.append("30営業日で買いGCから5日以内のDCが2回以上")
    return {"直近20日クロス回数": flips, "直近30日早期反転回数": int(early),
            "25日騰落率_反転検査%": round(ret25, 2) if np.isfinite(ret25) else np.nan,
            "新規買い除外理由": "／".join(reasons)}


class DomesticHistoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.row = []
        if tag in ("td", "th"): self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip()); self.cell = None
        if tag == "tr" and self.row: self.rows.append(self.row)


def parse_domestic_daily_history(text):
    parser = DomesticHistoryParser(); parser.feed(text)
    records = []
    for row in parser.rows:
        if len(row) < 6 or not re.fullmatch(r"\d{4}(?:年\d{1,2}月\d{1,2}日|/\d{1,2}/\d{1,2})", row[0]):
            continue
        try:
            dt = pd.to_datetime(row[0], format="%Y/%m/%d" if "/" in row[0] else "%Y年%m月%d日")
            nums = [float(v.replace(",", "").replace("株", "")) for v in row[1:6]]
            if all(np.isfinite(v) for v in nums) and min(nums[:4]) > 0:
                records.append([dt, *nums])
        except (ValueError, TypeError):
            continue
    return pd.DataFrame(records, columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def _parse_history_provider(html, provider):
    parser = DomesticHistoryParser(); parser.feed(html)
    records = []
    for row in parser.rows:
        if len(row) < (8 if provider == "traders" else 6) or not re.fullmatch(r"20[0-9]{2}/[0-9]{1,2}/[0-9]{1,2}", row[0].strip()): continue
        positions = (1,2,3,4,7) if provider == "traders" else (1,2,3,4,5)
        vals = [_finance_number(row[i]) for i in positions]
        if not all(np.isfinite(v) for v in vals) or min(vals[:4]) <= 0 or vals[4] < 0: continue
        op, hi, lo, cl, vol = vals
        if hi < max(op,cl) or lo > min(op,cl) or hi < lo: continue
        records.append([pd.Timestamp(row[0]), *vals])
    return pd.DataFrame(records, columns=["Date","Open","High","Low","Close","Volume"])


_INITIAL_JP_DAILY_HISTORY = """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"""


@st.cache_resource(show_spinner=False)
def _initial_jp_history():
    # ユーザー保存済み日本株履歴（2026-10-09まで）。現在値としては使用しない。
    raw = json.loads(zlib.decompress(base64.b64decode(_INITIAL_JP_DAILY_HISTORY)))
    output = {}
    for t, records in raw.items():
        f = pd.DataFrame(records, columns=["Date","Open","High","Low","Close","Volume"])
        f["Date"] = pd.to_datetime(f["Date"])
        f = f.set_index("Date").sort_index()
        f.attrs["origin"] = "初期保存履歴（2026-10-09まで）"
        output[t] = f
    return output


def _history_overlap_compatible(cached, current):
    dates = cached.index.intersection(current.index).sort_values()[-5:]
    if len(dates) < 2: return False
    for col in ("Open","High","Low","Close"):
        old = pd.to_numeric(cached.loc[dates,col],errors="coerce")
        new = pd.to_numeric(current.loc[dates,col],errors="coerce")
        if not (old.gt(0).all() and new.gt(0).all()): return False
        if not ((new / old - 1).abs() <= .01).all(): return False
    return True


def _daily_cache_path(t, provider):
    if not re.fullmatch(r"[0-9A-Za-z]{4,5}\.T", str(t)) or provider not in ("traders", "yahoo"): raise ValueError("国内日足コード形式不正")
    root = os.environ.get("DAILY_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "jp_stock_daily_cache_v21")
    return os.path.join(root, t + "-" + provider + ".json")


def _daily_cache_read(t, provider):
    try:
        with open(_daily_cache_path(t, provider), encoding="utf-8") as f: item = json.load(f)
        if item.get("ticker") != t or item.get("provider") != provider: return pd.DataFrame()
        frame = pd.DataFrame(item["bars"])
        frame["Date"] = pd.to_datetime(frame["Date"], errors="raise")
        frame = frame.set_index("Date").sort_index()
        frame.attrs["origin"] = item.get("origin", "")
        return frame
    except (OSError, ValueError, TypeError, KeyError): return pd.DataFrame()


def _daily_cache_write(t, provider, frame):
    path = _daily_cache_path(t, provider); tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = frame.tail(500).reset_index(); rows["Date"] = rows["Date"].dt.strftime("%Y-%m-%d")
        item = {"ticker": t, "provider": provider, "fetched_at": tokyo_now().isoformat(), "origin":frame.attrs.get("origin", ""), "bars": rows.to_dict("records")}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path), suffix=".tmp", delete=False) as f:
            tmp = f.name; json.dump(_finance_json_safe(item), f, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, path)
        return "保存済"
    except (OSError, ValueError, TypeError): return "保存失敗・取得値は利用可能"
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass


def _history_provider(t, cutoff, provider, target_bars=150):
    cached = _daily_cache_read(t, provider)
    initial = _initial_jp_history().get(t)
    if initial is not None and (cached.empty or len(cached) < len(initial)):
        cached = initial.copy()
    cached = cached.loc[cached.index <= cutoff] if not cached.empty else cached
    origin = cached.attrs.get("origin", "")
    # 日付が確定済みなら、同一取引日への再アクセスを省く。前日以前を最新と偽らない。
    if not cached.empty and len(cached) >= target_bars and pd.Timestamp(cached.index.max()).normalize() == cutoff:
        return cached, 0, "保存履歴を再利用", "保存値"
    parts, seen, errors, overlapping = [], set(), [], False
    limit = 3 if provider == "traders" else min(15, int(math.ceil(target_bars / 20)))
    pages = 0
    for page in range(1, limit+1):
        url = (f"https://www.traders.co.jp/stocks/61_{code(t)}/historical" + (f"/{page}" if page > 1 else "")
               if provider == "traders" else f"https://finance.yahoo.co.jp/quote/{t}/history?page={page}")
        try:
            html = _fetch_free_finance_html(url)
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            if not title or code(t).upper() not in _finance_plain(title.group(1)).upper(): raise ValueError("日足ページの銘柄コード未確認")
            part = _parse_history_provider(html, provider)
            if part.empty: errors.append(f"page={page}: 日足表の数値未提供・公開範囲終了"); break
            if not (set(part["Date"]) - seen): break
            parts.append(part); seen.update(part["Date"]); pages += 1
            if not cached.empty and set(part["Date"]) & set(cached.index):
                indexed = part.set_index("Date")
                if _history_overlap_compatible(cached, indexed):
                    overlapping = True
                    if len(cached) >= target_bars: break
                else:
                    errors.append("保存履歴との価格基準不一致・初期履歴を結合せず公開範囲を再取得")
                    cached = pd.DataFrame(); origin = ""; overlapping = False
            if len(seen) >= target_bars: break
        except Exception as exc:
            errors.append(f"page={page}: {type(exc).__name__}: {str(exc)[:180]}"); break
    current = pd.concat(parts).drop_duplicates("Date").set_index("Date").sort_index() if parts else pd.DataFrame()
    # 同一取得先で日付が重なった場合だけ更新を結合する。別取得先や空白期間を継ぎ足さない。
    if not current.empty:
        current = current.loc[current.index <= cutoff]
        if overlapping:
            overlap_dates = cached.index.intersection(current.index).sort_values()[-5:]
            # 古い未調整価格で保存履歴を上書きしない。照合済み直近日と新規分だけを反映する。
            if len(overlap_dates): current = current.loc[current.index >= overlap_dates[0]]
            current = pd.concat([cached, current]).loc[lambda x:~x.index.duplicated(keep="last")].sort_index()
        current.attrs["origin"] = origin
        _daily_cache_write(t, provider, current)
        return current, pages, " / ".join(errors), ("今回取得＋"+origin+"・重複照合済" if origin else "今回取得＋保存履歴") if overlapping else "今回取得"
    return cached, pages, " / ".join(errors), "過去保存値（最終日を確認）" if not cached.empty else "取得不可"


def _download_domestic_history(t, cutoff, target_bars=150):
    best, audit = pd.DataFrame(), []
    source, mode, pages = "", "", 0
    for provider, label in (("traders", "トレーダーズ・ウェブ無料日足"), ("yahoo", "Yahoo!ファイナンス日本版・時系列日足")):
        frame, count, message, data_mode = _history_provider(t, cutoff, provider, target_bars)
        audit.append(label + ": " + (message or "取得済"))
        if not frame.empty and (best.empty or (frame.index.max(),len(frame)) > (best.index.max(),len(best))):
            best, source, mode, pages = frame, (frame.attrs.get("origin", "")+"＋" if frame.attrs.get("origin") else "")+label, data_mode, count
        if not best.empty and len(best) >= max(134, target_bars) and pd.Timestamp(best.index.max()).normalize() == cutoff: break
    if not best.empty: best.attrs.update({"source":source, "data_mode":mode})
    return best, pages, " / ".join(audit)


@st.cache_data(ttl=300, show_spinner=False)
def batch_domestic_stock_data(tickers_tuple, refresh_token="", history_bars=150):
    from concurrent.futures import ThreadPoolExecutor
    today = tokyo_now()
    cutoff = pd.Timestamp(today.date()) if today.hour >= 16 else pd.Timestamp(today.date()) - pd.Timedelta(days=1)
    # 土日は直前の金曜日へ。祝日などは誤って最新扱いせず、日付確認待ちにする。
    while cutoff.weekday() >= 5: cutoff -= pd.Timedelta(days=1)
    def fetch(t):
        audit = {"コード":code(t),"銘柄名":name(t),"情報源":"", "取得行数":0,"指標行数":0,"取得ページ数":0,
                 "日足最終日":"","状態":"取得不可","使用データ":"","詳細":""}
        try:
            raw,pages,details = _download_domestic_history(t,cutoff,target_bars=int(history_bars))
            audit.update({"情報源":raw.attrs.get("source",""),"使用データ":raw.attrs.get("data_mode",""),
                          "取得行数":len(raw),"取得ページ数":pages,"詳細":details})
            frame = _prepare_light_history_frame(raw)
            if frame.empty:
                audit["状態"] = "日足不足" if len(raw) else "取得不可"
                return t,None,audit
            latest = pd.Timestamp(frame.index.max()).normalize()
            split_suspect = bool(raw["Close"].pct_change().tail(60).abs().gt(.40).any())
            confirmed = latest == cutoff and not split_suspect
            frame.attrs.update({"source":audit["情報源"],"latest_date":latest,"session_date":cutoff,
                "daily_bar_status":"🟢 当日確定" if confirmed else "🔴 日付・価格断絶確認待ち", "domestic_history":True})
            audit.update({"指標行数":len(frame),"日足最終日":latest.strftime("%Y-%m-%d"),"状態":"取得済" if confirmed else "取得済・新規買い確認待ち"})
            return t,frame,audit
        except Exception as exc:
            audit["詳細"] = type(exc).__name__+": "+str(exc)[:300]
            return t,None,audit
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(fetch,tickers_tuple))
    return {t:f for t,f,a in results if f is not None}, pd.DataFrame([a for t,f,a in results])



def multibagger_finance_fields(f):
    """追加通信せず、取得済み国内財務の生値と比較期間を研究へ引き継ぐ。"""
    return {**{label: safe_float(f.get(key)) * scale for label, key, scale in (
        ("時価総額", "marketCap", 1), ("売上成長率%", "revenueGrowth", 100),
        ("利益成長率%", "earningsGrowth", 100), ("ROE%", "returnOnEquity", 100),
        ("営業利益率%", "operatingMargins", 100), ("D/E", "debtToEquity", 1),
        ("FCF", "freeCashflow", 1))},
        "成長比較期": str(f.get("_growth_period", "")),
        "成長比較前期": str(f.get("_growth_previous_period", ""))}


def _finance_acquisition_state(f):
    if f.get("_cache_mode") == "過去保存値・表示のみ": return "取得不可（過去保存値のみ）"
    if finance_freshness_usable(f.get("_freshness")): return "取得済"
    reason = finance_evaluation_reason(f)
    if "未提供" in reason or "PER評価対象外" in reason:
        return "取得済（" + reason + "）"
    if not all(np.isfinite(safe_float(f.get(k))) for k in ("forwardEps", "forwardPE")):
        return "取得済（予想指標不足・評価保留）"
    return "取得済（基準日・対象期未確認）"


@st.cache_data(ttl=300, max_entries=8, show_spinner=False)
def batch_domestic_finance_audit(tickers_tuple, session_key=""):
    """日足・買いサインの成否に関わらず、財務の取得結果を残す。評価計算とは別。"""
    from concurrent.futures import ThreadPoolExecutor
    def fetch(t):
        row = {"コード": code(t), "銘柄名": name(t), "財務取得状態": "取得不可",
               "財務情報源": "", "財務情報鮮度": "", "参考指標基準日": "", "予想EPS対象期": "",
               "業績発表日": "", "予想EPS": np.nan, "予想PER": np.nan, "PBR": np.nan,
               "財務参照URL": "", "財務取得経路": "", "財務取得日時": "", "財務データ区分": "", "欠損項目": "",
               "今回確認日時": tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST"), "新規買い評価": "不可（通信失敗）"}
        try:
            f = _domestic_finance_snapshot(t)
            row.update(multibagger_finance_fields(f))
            row.update({"財務取得状態": _finance_acquisition_state(f), "評価保留理由": finance_evaluation_reason(f),
                "財務情報源": f.get("_source", ""), "財務情報鮮度": f.get("_freshness", ""),
                "参考指標基準日": f.get("_reference_date", ""), "予想EPS対象期": f.get("_forecast_period", ""),
                "業績発表日": f.get("_disclosure_date", ""), "予想EPS": safe_float(f.get("forwardEps")),
                "予想PER": safe_float(f.get("forwardPE")), "PBR": safe_float(f.get("priceToBook")),
                "財務参照URL": f.get("_url", ""), "財務取得経路": f.get("_attempts", ""),
                "財務取得日時": f.get("_fetched_at", ""), "財務データ区分": f.get("_cache_mode", ""),
                "新規買い評価": "評価可能" if finance_freshness_usable(f.get("_freshness")) else "不可（必要指標・鮮度未確認）",
                "実績EPS":safe_float(f.get("trailingEps")), "実績EPS対象期":f.get("_actual_period", ""),
                "欠損項目": "／".join(label for label, key in (("予想EPS", "forwardEps"), ("予想PER", "forwardPE"), ("PBR", "priceToBook")) if not np.isfinite(safe_float(f.get(key))))})
        except Exception as exc:
            row["財務取得経路"] = type(exc).__name__ + ": " + str(exc)[:500]
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        return pd.DataFrame(list(pool.map(fetch, tickers_tuple)))


def selection_range_metrics(df):
    """直近確定足のATR14/終値と、連続する10営業日安値を比較。"""
    if df is None or len(df) < 31:
        return {"値幅区分": "値幅未確認", "ATR比率%": np.nan, "安値切上げ": False}
    x = df.iloc[-31:]
    close = pd.to_numeric(x["Close"], errors="coerce")
    high = pd.to_numeric(x["High"], errors="coerce")
    low = pd.to_numeric(x["Low"], errors="coerce")
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    pct = safe_float(tr.tail(14).mean() / close.iloc[-1] * 100) if close.iloc[-1] > 0 else np.nan
    lows = [safe_float(low.iloc[a:b].min()) for a,b in [(-30,-20),(-20,-10),(-10,None)]]
    rising = all(np.isfinite(v) for v in lows) and lows[0] < lows[1] < lows[2] and close.iloc[-1] > close.tail(25).mean()
    return {"値幅区分": "値幅未確認" if not np.isfinite(pct) else "値動きが大きい" if pct >= 3 else "値動きが中くらい" if pct >= 1.5 else "値動きが小さい",
            "ATR比率%": pct, "安値切上げ": bool(rising)}


@st.cache_data(ttl=1800, show_spinner=False)
def discover_domestic_topix_turnover100():
    """JPX構成一覧と日本版Yahoo売買代金ランキングの積集合を順位順に100件。"""
    topix = discover_topix_growth_stock_universe()
    allowed = set(topix["コード"].astype(str))
    selected = []
    for page in range(1, 11):
        response = requests.get(
            "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh",
            params={"market": "all", "page": page},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        # SSRランキングの銘柄リンクを文書順に読み取る（JSON内の順不同コードは使わない）。
        found = re.findall(r'href=["\'](?:https://finance.yahoo.co.jp)?/quote/([0-9A-Z]{4})\.T(?:[?/#][^"\']*)?["\']', response.text)
        added = 0
        for c in found:
            if c in allowed and c not in selected:
                selected.append(c)
                added += 1
            if len(selected) == 100:
                out = topix.set_index("コード").loc[selected].reset_index()
                out["売買代金順位"] = np.arange(1, 101)
                out["選定情報源"] = "JPX構成一覧＋Yahoo!ファイナンス日本版売買代金順位"
                out.attrs["universe_notice"] = "TOPIX＋グロース内の売買代金上位100。ランキングは取得時点（日中は進行中）、買いサインは最新確定日足。"
                return out
        if not found:
            break
    raise ValueError("国内売買代金ランキングからTOPIX＋グロース100件を確認できません。新規買いを停止します。")


def signal_first_candidates(data, held_codes, min_turnover=0, filter_downtrend=False, filter_churn=False, replacement=False):
    """当日までの確定日足だけから候補を抽出。財務評価は呼出し側で行う。"""
    rows = []
    held = set(map(str, held_codes))
    for t, df in (data or {}).items():
        if df is None or len(df) < 60 or code(t) in held:
            continue
        sx = (public_replacement_frame(df, "高値更新55＋MA200・安値割れ20")
              if replacement else stoch_quality_frame(df))
        r = sx.iloc[-1]
        if not bool(r.get("BUY_研究" if replacement else "BUY_現行", False)):
            continue
        turnover = safe_float((df["Close"] * df["Volume"]).tail(20).mean())
        reasons = []
        metrics = selection_range_metrics(df)
        if metrics["値幅区分"] != "値動きが大きい":
            reasons.append("値動きが大きい条件未達（ATR14/終値3%未満または不明）")
        if not metrics["安値切上げ"]:
            reasons.append("安値切上げ未確認")
        if df.attrs.get("daily_bar_status") != "🟢 当日確定":
            reasons.append("最新確定日足を確認できず")
        if not np.isfinite(turnover) or turnover < max(min_turnover, 100000000):
            reasons.append("流動性不足")
        if filter_downtrend and bool(r.get("下降確認", False)):
            reasons.append("下降確認")
        diagnostic = stoch_churn_diagnostic(df) if not replacement else {"新規買い除外理由":""}
        if filter_churn and not replacement and diagnostic["新規買い除外理由"]:
            reasons.append(diagnostic["新規買い除外理由"])
        rows.append({"ticker": t, "コード": code(t), "銘柄名": name(t),
                     "抽出除外理由": "／".join(reasons), "20日平均売買代金": turnover, **metrics})
    return pd.DataFrame(rows, columns=["ticker", "コード", "銘柄名", "抽出除外理由", "20日平均売買代金", "値幅区分", "ATR比率%", "安値切上げ"])


def valuation_label(upside, split_status, guard_status, acquisition, freshness):
    """全画面で共用する企業価値判定。閾値と取得状態の優先順を統一。"""
    if "⚠️" in str(split_status):
        return "⚠️ 分割補正確認"
    unavailable = financial_unavailable_label(acquisition, freshness)
    if unavailable:
        return unavailable
    upside = safe_float(upside)
    if "⚠️" in str(guard_status) and not np.isfinite(upside):
        return "⚠️ 異常値ガード・算定不能"
    if not np.isfinite(upside):
        return "算定不能"
    return ("🟢 割安" if upside >= 20 else "🟡 やや割安" if upside >= 5
            else "⚪ 適正圏" if upside > -10 else "🔴 割高")


def common_new_buy_reasons(label, daily_status, downtrend=False, churn_reason="", replacement=False):
    """全監視モード共通。確定・評価可能な日足/財務のみ注文候補へ渡す。"""
    reasons = []
    if daily_status != "🟢 当日確定":
        reasons.append("日足未確定のため新規買い見送り")
    label = str(label)
    if "割高" in label:
        reasons.append("割高判定のため新規買い除外")
    elif label not in {"🟢 割安", "🟡 やや割安", "⚪ 適正圏"}:
        reasons.append("財務評価未確認・赤字予想等のため新規買い見送り")
    if not replacement:
        if churn_reason:
            reasons.append("短期クロス検査：" + str(churn_reason))
    return reasons


def build_valuation_master(frames, holdings, price_data, display_frames):
    """同一実行内の共通判定。TOP50外の保有・表示対象も一度だけ算定する。"""
    records = {}
    for frame in frames:
        if isinstance(frame, pd.DataFrame) and "コード" in frame.columns:
            for row in frame.to_dict("records"):
                records[str(code(str(row["コード"]).removesuffix(".0")))] = row
    targets = {str(code(str(t))) for t in holdings}
    for frame in display_frames:
        if isinstance(frame, pd.DataFrame) and "コード" in frame.columns:
            targets.update(str(code(str(t).removesuffix(".0"))) for t in frame["コード"])
    for c in sorted(targets - records.keys()):
        symbol = c + ".T"
        history = price_data.get(symbol, pd.DataFrame())
        hint = safe_float(history["Close"].iloc[-1]) if not history.empty else np.nan
        f = fundamental_snapshot(symbol, market_price_hint=hint) if np.isfinite(hint) and hint > 0 else {}
        records[c] = {"コード": c, "割安判定": valuation_label(
            f.get("参考価値上昇余地"), f.get("株式分割補正", ""),
            f.get("適正株価異常値ガード", ""), f.get("取得状態"), f.get("財務情報鮮度"))}
    return pd.DataFrame(records.values())


def build_value_ai_top50(data, max_rows=50, fundamental_pool_size=90, churn_filter=True):
    """数百銘柄の母集団から一次選抜→企業価値AIで最終TOP50。

    重いファンダ取得を全銘柄へ直列実行するとStreamlit Cloudでタイムアウトしやすいため、
    まず全母集団を流動性・トレンド・値動き安定性で一次選抜し、上位だけ詳細な企業価値評価を行う。
    これにより「49銘柄全部がTOP50」という旧状態を避けつつ、数百銘柄から本当の50銘柄を選ぶ。
    """
    tech_rows = []
    for t, df in (data or {}).items():
        if df is None or df.empty or len(df) < 60:
            continue
        x = df.sort_index().copy()
        r = x.iloc[-1]
        close = safe_float(r.get("Close"))
        if not np.isfinite(close) or close <= 0:
            continue
        turnover20 = pd.to_numeric(x.get("Turnover", x["Close"] * x["Volume"]), errors="coerce").tail(20).mean()
        volume20 = pd.to_numeric(x["Volume"], errors="coerce").tail(20).mean()
        ret25 = safe_float(r.get("Return_25d"), 0.0)
        atr = safe_float(r.get("ATR14"))
        atr_pct = (atr / close * 100.0) if np.isfinite(atr) and close > 0 else np.nan
        ma25 = safe_float(r.get("MA25"))
        ma75 = safe_float(r.get("MA75"))
        trend_raw = 0.0
        if np.isfinite(ma25) and close > ma25: trend_raw += 1.0
        if np.isfinite(ma75) and close > ma75: trend_raw += 1.0
        if np.isfinite(ma25) and np.isfinite(ma75) and ma25 > ma75: trend_raw += 1.0
        trend_raw += np.clip(ret25 / 10.0, -1.0, 1.0)
        diagnostic = stoch_churn_diagnostic(x) if churn_filter else {
            "直近20日クロス回数": 0, "直近30日早期反転回数": 0,
            "25日騰落率_反転検査%": np.nan, "新規買い除外理由": ""}
        signal_quality = stoch_signal_quality(x)
        tech_rows.append({
            "ticker": t, "コード": code(t), "銘柄名": name(t), "現在株価_価格": close,
            "20日平均売買代金": turnover20, "20日平均出来高": volume20,
            "25日騰落率%": ret25, "ATR%": atr_pct, "トレンド原点": trend_raw,
            **diagnostic, **signal_quality,
        })
    if not tech_rows:
        return pd.DataFrame(), pd.DataFrame()

    base = pd.DataFrame(tech_rows)
    mother_count = len(base)
    base["売買代金スコア"] = _percentile_score(base["20日平均売買代金"], True)
    base["出来高スコア"] = _percentile_score(base["20日平均出来高"], True)
    base["流動性スコア"] = (base["売買代金スコア"] * .70 + base["出来高スコア"] * .30).clip(0, 100)
    base["トレンドスコア"] = ((base["トレンド原点"] + 1.0) / 5.0 * 100.0).clip(0, 100)
    base["値動き安定スコア"] = base["ATR%"].apply(
        lambda a: 50.0 if not np.isfinite(a) else
        90.0 if 1.5 <= a <= 4.5 else
        75.0 if 0.8 <= a < 1.5 or 4.5 < a <= 6.5 else
        55.0 if 0.4 <= a < 0.8 or 6.5 < a <= 9.0 else 30.0
    )
    # 一次選抜は「企業の良し悪し」ではなく、売買対象として十分な流動性があり、
    # 極端に扱いにくい値動きでないかを絞る入口。企業価値はこの後に評価する。
    # 5年日足（2021-09-13〜2026-09-11）で70/30 OOS検証した堅牢値。
    # 学習期間の利益最大ではなく、未学習期間でもPF・DDが崩れにくい組み合わせを採用。
    # 流動性45% / トレンド30% / 値動き安定性25%
    base["一次選抜スコア"] = (
        base["流動性スコア"] * .45 +
        base["トレンドスコア"] * .30 +
        base["値動き安定スコア"] * .25
    ).clip(0, 100)
    # 少数のクロスから銘柄を即除外しない。十分な件数で悪い場合だけ小幅減点。
    if "検証GC件数" in base:
        base["シグナル品質減点"] = np.where(
            (base["検証GC件数"] >= 4) & (base["検証GC平均5日騰落%"] < 0), 5.0, 0.0)
        base["一次選抜スコア"] = (base["一次選抜スコア"] - base["シグナル品質減点"]).clip(0, 100)
    # 除外は一次選抜の前。欠員には次順位の候補を充当する。
    audit = base[["コード", "銘柄名", "検証GC件数", "検証GC平均5日騰落%", "シグナル品質減点",
                  "直近20日クロス回数", "直近30日早期反転回数",
                  "25日騰落率_反転検査%", "新規買い除外理由"]].copy()
    audit.insert(2, "判定", np.where(audit["新規買い除外理由"].ne(""), "新規買い除外", "通過"))
    if churn_filter:
        base = base[base["新規買い除外理由"].eq("")].copy()
    if len(base) < int(max_rows):
        return pd.DataFrame(), audit
    base = base.sort_values(["一次選抜スコア", "流動性スコア"], ascending=[False, False]).reset_index(drop=True)
    base["一次選抜順位"] = np.arange(1, len(base) + 1)

    pool_n = min(max(int(fundamental_pool_size), int(max_rows)), len(base))
    pool = base.head(pool_n).copy()
    fund_rows = []
    for _, r in pool.iterrows():
        t = r["ticker"]
        f = fundamental_snapshot(t, market_price_hint=safe_float(r.get("現在株価_価格")))
        fund_rows.append({
            "ticker": t,
            "企業価値スコア": safe_float(f.get("企業価値スコア"), 50),
            "成長性スコア": safe_float(f.get("成長性スコア"), 50),
            "バリュエーションスコア": safe_float(f.get("バリュエーションスコア"), 50),
            "AI参考価値下限": safe_float(f.get("AI参考価値下限")),
            "AI参考価値": safe_float(f.get("AI参考価値")),
            "AI参考価値上限": safe_float(f.get("AI参考価値上限")),
            "参考価値上昇余地%": safe_float(f.get("参考価値上昇余地")),
            "PER": safe_float(f.get("PER")), "予想PER": safe_float(f.get("予想PER")),
            "PBR": safe_float(f.get("PBR")), "ROE%": safe_float(f.get("ROE")) * 100.0,
            "売上成長率%": safe_float(f.get("売上成長率")) * 100.0,
            "利益成長率%": safe_float(f.get("利益成長率")) * 100.0,
            "適正株価根拠": str(f.get("価値算定根拠", "")),
            "株式分割補正": str(f.get("株式分割補正", "")),
            "直近分割日": str(f.get("直近分割日", "")),
            "直近分割比率": safe_float(f.get("直近分割比率")),
            "株式分割メモ": str(f.get("株式分割メモ", "")),
            "目標株価参考_補正後": safe_float(f.get("目標株価参考_補正後")),
            "予想EPS_元値": safe_float(f.get("予想EPS_元値")),
            "予想EPS_補正後": safe_float(f.get("予想EPS")),
            "EPS理論株価_元": safe_float(f.get("EPS理論株価_元")),
            "EPS理論株価_採用後": safe_float(f.get("EPS理論株価_採用後")),
            "目標株価_採用後": safe_float(f.get("目標株価_採用後")),
            "適正株価異常値ガード": str(f.get("適正株価異常値ガード", "")),
            "適正株価クロスチェック": str(f.get("適正株価クロスチェック", "")),
            "ファンダ取得状態": str(f.get("取得状態", "")),
            "財務情報源": str(f.get("財務情報源", "")),
            "財務情報鮮度": str(f.get("財務情報鮮度", "")),
            "Yahoo期末日": str(f.get("Yahoo期末日", "")),
            "Yahoo取得状態": str(f.get("Yahoo取得状態", "")),
            "財務取得日時": str(f.get("財務取得日時", "")),
            "参考指標基準日": str(f.get("参考指標基準日", "")),
            "予想EPS対象期": str(f.get("予想EPS対象期", "")),
            **{k: str(f.get(k, "")) for k in ("財務参照URL", "財務取得経路", "業績発表日", "実績EPS対象期", "財務データ区分")},
        })
    out = pool.merge(pd.DataFrame(fund_rows), on="ticker", how="left")
    for c in ["企業価値スコア", "成長性スコア", "バリュエーションスコア"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(50).clip(0, 100)

    out["AI_TOP50スコア"] = (
        out["企業価値スコア"] * .30 +
        out["成長性スコア"] * .22 +
        out["流動性スコア"] * .25 +
        out["トレンドスコア"] * .13 +
        out["値動き安定スコア"] * .10
    ).clip(0, 100)
    out["割安判定"] = out.apply(lambda rr: valuation_label(
        rr.get("参考価値上昇余地%"), rr.get("株式分割補正", ""),
        rr.get("適正株価異常値ガード", ""), rr.get("ファンダ取得状態"),
        rr.get("財務情報鮮度")), axis=1)
    # 取得不可の中立点を企業価値スコアとして競わせない。
    # 有効な評価を持つ銘柄を先に並べ、欠損は監査用に残す。
    out["企業価値算定済"] = (
        out["ファンダ取得状態"].eq("OK") &
        out["財務情報鮮度"].map(finance_freshness_usable) &
        pd.to_numeric(out["参考価値上昇余地%"], errors="coerce").notna()
    )
    out["母集団件数"] = int(mother_count)
    out["詳細企業価値評価件数"] = int(pool_n)
    out = out.sort_values(["企業価値算定済", "AI_TOP50スコア", "流動性スコア"],
                          ascending=[False, False, False]).reset_index(drop=True)
    out.insert(0, "順位", np.arange(1, len(out) + 1))
    return out.head(int(max_rows)).copy(), audit


def parse_topix_constituents(content):
    """JPX公開CSVのヘッダー前の基準日行と文字コード差を吸収する。"""
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        for start, line in enumerate(lines[:30]):
            if "コード" not in line and "Code" not in line:
                continue
            try:
                frame = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str)
                frame.columns = [str(c).strip() for c in frame.columns]
                code_col = next((c for c in frame if c in ("コード", "銘柄コード", "Code", "Local Code")), None)
                name_col = next((c for c in frame if c in ("銘柄名", "名称", "Name", "Issue Name")), None)
                if not code_col or not name_col:
                    continue
                codes = frame[code_col].str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
                valid = codes.str.fullmatch(r"[0-9A-Z]{4}", na=False)
                out = pd.DataFrame({"コード": codes[valid], "銘柄名": frame.loc[valid, name_col].str.strip()})
                out = out.drop_duplicates("コード").reset_index(drop=True)
                out["ticker"] = out["コード"] + ".T"
                out["構成銘柄情報源"] = "JPX公開TOPIX構成銘柄別ウエイト一覧"
                if len(out) >= 50:
                    return out
            except (ValueError, pd.errors.ParserError):
                continue
    raise ValueError("TOPIX構成銘柄CSVを解釈できませんでした（50銘柄未満または列形式変更）")


@st.cache_data(ttl=21600, show_spinner=False)
def discover_topix_stock_universe():
    """JPXの最新公開構成一覧全体を母集団にする。取得失敗時は対象を代替しない。"""
    response = requests.get(
        "https://www.jpx.co.jp/automation/markets/indices/topix/files/topixweight_j.csv",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
    )
    response.raise_for_status()
    return parse_topix_constituents(response.content)


def parse_growth_constituents(content):
    """JPXのxlsxを標準ライブラリで読み、グロース内国普通株だけを抽出。"""
    from xml.etree import ElementTree as ET
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(io.BytesIO(content)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(si.itertext()) for si in
                      ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in root.findall("s:sheetData/s:row", ns):
            values = {}
            for cell in row.findall("s:c", ns):
                col = re.sub(r"[0-9]", "", cell.get("r", ""))
                value = cell.find("s:v", ns)
                text = value.text if value is not None and value.text else ""
                if cell.get("t") == "s":
                    text = shared[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(t.text or "" for t in cell.findall("s:is//s:t", ns))
                values[col] = text.strip()
            rows.append(values)
    header_idx = next((i for i, row in enumerate(rows[:30])
                       if {"コード", "銘柄名", "市場・商品区分"}.issubset(set(row.values()))), None)
    if header_idx is None:
        raise ValueError("JPXグロース一覧の列形式を確認できません")
    cols = {v: k for k, v in rows[header_idx].items()}
    records = []
    for row in rows[header_idx + 1:]:
        market = row.get(cols["市場・商品区分"], "").replace(" ", "").replace("　", "")
        c = re.sub(r"\.0$", "", row.get(cols["コード"], "").upper())
        if market not in ("グロース（内国株式）", "グロース(内国株式)") or not re.fullmatch(r"[0-9A-Z]{4}", c):
            continue
        records.append({"コード": c, "銘柄名": row.get(cols["銘柄名"], c),
                        "ticker": c + ".T", "対象指数": "グロース",
                        "構成銘柄情報源": "JPX東証上場銘柄一覧（グロース内国株式）"})
    if len(records) < 50:
        raise ValueError("JPXグロース一覧の件数が不足しています。新規買いを停止します")
    return pd.DataFrame(records).drop_duplicates("コード").reset_index(drop=True)


@st.cache_data(ttl=21600, show_spinner=False)
def discover_growth_stock_universe():
    response = requests.get(
        "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return parse_growth_constituents(response.content)


def add_growth_universe(base):
    """重複の市場タグを残し、どちらかの取得失敗を黙って隠さない。"""
    growth = discover_growth_stock_universe()
    notice = base.attrs.get("universe_notice", "")
    base = base.copy()
    if "対象指数" not in base:
        base["対象指数"] = "TOPIX"
    common = set(base["コード"]) & set(growth["コード"])
    mask = base["コード"].isin(common)
    base.loc[mask, "対象指数"] = base.loc[mask, "対象指数"].astype(str) + "＋グロース"
    out = pd.concat([base, growth.loc[~growth["コード"].isin(common)]], ignore_index=True)
    out.attrs["universe_notice"] = notice
    return out


@st.cache_data(ttl=21600, show_spinner=False)
def discover_topix_growth_stock_universe():
    return add_growth_universe(discover_topix_stock_universe())


class NikkeiConstituentParser(HTMLParser):
    """公式構成一覧の表セルを読み、リンクを含む銘柄名を保持する。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.parts = [], [], []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag in ("td", "th"):
            self.in_cell, self.parts = True, []

    def handle_data(self, data):
        if self.in_cell:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.cells.append(" ".join("".join(self.parts).split()))
            self.in_cell = False
        elif tag == "tr" and len(self.cells) >= 2:
            if re.fullmatch(r"[0-9A-Z]{4}", self.cells[0]) and self.cells[1]:
                self.rows.append({"コード": self.cells[0], "銘柄名": self.cells[1]})


def parse_nikkei225_constituents(text):
    parser = NikkeiConstituentParser()
    parser.feed(text)
    out = pd.DataFrame(parser.rows, columns=["コード", "銘柄名"]).drop_duplicates("コード")
    if len(out) != 225:
        raise ValueError(f"日経225構成一覧を確認できませんでした（{len(out)}銘柄、225銘柄が必要）")
    out = out.reset_index(drop=True)
    out["ticker"] = out["コード"] + ".T"
    out["構成銘柄情報源"] = "日経平均プロフィル公式構成銘柄一覧"
    return out


NIKKEI225_SNAPSHOT_DATE = '2026-10-09'
NIKKEI225_SNAPSHOT = [('285A', 'キオクシア'), ('4062', 'イビデン'), ('6479', 'ミネベア'), ('6501', '日立'), ('6503', '三菱電'), ('6504', '富士電機'), ('6506', '安川電'), ('6525', 'コクサイエレ'), ('6526', 'ソシオネクス'), ('6645', 'オムロン'), ('6701', 'ＮＥＣ'), ('6702', '富士通'), ('6723', 'ルネサス'), ('6724', 'エプソン'), ('6752', 'パナＨＤ'), ('6753', 'シャープ'), ('6758', 'ソニーＧ'), ('6762', 'ＴＤＫ'), ('6770', 'アルプスアル'), ('6841', '横河電'), ('6857', 'アドテスト'), ('6861', 'キーエンス'), ('6902', 'デンソー'), ('6920', 'レーザーテク'), ('6954', 'ファナック'), ('6963', 'ローム'), ('6971', '京セラ'), ('6976', '太陽誘電'), ('6981', '村田製'), ('7735', 'スクリン'), ('7751', 'キヤノン'), ('7752', 'リコー'), ('8035', '東エレク'), ('7201', '日産自'), ('7202', 'いすゞ'), ('7203', 'トヨタ'), ('7211', '三菱自'), ('7261', 'マツダ'), ('7267', 'ホンダ'), ('7269', 'スズキ'), ('7270', 'ＳＵＢＡＲＵ'), ('7272', 'ヤマハ発'), ('4543', 'テルモ'), ('6146', 'ディスコ'), ('7731', 'ニコン'), ('7733', 'オリンパス'), ('7741', 'ＨＯＹＡ'), ('2432', 'ディーエヌエ'), ('3659', 'ネクソン'), ('3697', 'ＳＨＩＦＴ'), ('4307', '野村総研'), ('4385', 'メルカリ'), ('4689', 'ラインヤフー'), ('4704', 'トレンド'), ('7974', '任天堂'), ('9432', 'ＮＴＴ'), ('9433', 'ＫＤＤＩ'), ('9434', 'ＳＢ'), ('9602', '東宝'), ('9697', 'カプコン'), ('9766', 'コナミＧ'), ('9984', 'ＳＢＧ'), ('5831', 'しずおかＦＧ'), ('7186', '横浜ＦＧ'), ('8304', 'あおぞら銀'), ('8306', '三菱ＵＦＪ'), ('8308', 'りそなＨＤ'), ('8309', '三井住友トラ'), ('8316', '三井住友ＦＧ'), ('8331', '千葉銀'), ('8354', 'ふくおかＦＧ'), ('8411', 'みずほＦＧ'), ('8253', 'クレセゾン'), ('8591', 'オリックス'), ('8697', '日本取引所'), ('8601', '大和'), ('8604', '野村'), ('8630', 'ＳＯＭＰＯ'), ('8725', 'ＭＳ＆ＡＤ'), ('8750', '第一ライフ'), ('8766', '東京海上'), ('8795', 'Ｔ＆Ｄ'), ('1332', 'ニッスイ'), ('2002', '日清粉Ｇ'), ('2269', '明治ＨＤ'), ('2282', '日ハム'), ('2501', 'サッポロ'), ('2502', 'アサヒ'), ('2503', 'キリンＨＤ'), ('2801', 'キッコマン'), ('2802', '味の素'), ('2871', 'ニチレイ'), ('2914', 'ＪＴ'), ('4151', '協和キリン'), ('4502', '武田'), ('4503', 'アステラス'), ('4506', '住友ファーマ'), ('4507', '塩野義'), ('4519', '中外薬'), ('4523', 'エーザイ'), ('4568', '第一三共'), ('4578', '大塚ＨＤ'), ('3086', 'Ｊフロント'), ('3092', 'ＺＯＺＯ'), ('3099', '三越伊勢丹'), ('3382', 'セブン＆アイ'), ('7453', '良品計画'), ('7532', 'パンパシＨＤ'), ('8233', '高島屋'), ('8252', '丸井Ｇ'), ('8267', 'イオン'), ('9843', 'ニトリＨＤ'), ('9983', 'ファストリ'), ('2413', 'エムスリー'), ('4324', '電通グループ'), ('4661', 'ＯＬＣ'), ('4751', 'サイバー'), ('4755', '楽天グループ'), ('6098', 'リクルート'), ('6178', '日本郵政'), ('6532', 'ベイカレント'), ('9735', 'セコム'), ('1605', 'ＩＮＰＥＸ'), ('3401', '帝人'), ('3402', '東レ'), ('3861', '王子ＨＤ'), ('3405', 'クラレ'), ('3407', '旭化成'), ('4004', 'レゾナック'), ('4005', '住友化'), ('4021', '日産化'), ('4042', '東ソー'), ('4043', 'トクヤマ'), ('4061', 'デンカ'), ('4063', '信越化'), ('4183', '三井化学'), ('4188', '三菱ケミＧ'), ('4208', 'ＵＢＥ'), ('4452', '花王'), ('4901', '富士フイルム'), ('4911', '資生堂'), ('6988', '日東電'), ('5019', '出光興産'), ('5020', 'ＥＮＥＯＳ'), ('5101', '浜ゴム'), ('5108', 'ブリヂストン'), ('5201', 'ＡＧＣ'), ('5214', '日電硝'), ('5233', '太平洋セメ'), ('5301', '東海カーボン'), ('5332', 'ＴＯＴＯ'), ('5333', 'ＮＧＫ'), ('5401', '日本製鉄'), ('5406', '神戸鋼'), ('5411', 'ＪＦＥ'), ('3436', 'ＳＵＭＣＯ'), ('5016', 'ＪＸ金属'), ('5706', '三井金属'), ('5711', '三菱マ'), ('5713', '住友鉱'), ('5714', 'ＤＯＷＡ'), ('5801', '古河電'), ('5802', '住友電'), ('5803', 'フジクラ'), ('2768', '双日'), ('8001', '伊藤忠'), ('8002', '丸紅'), ('8015', '豊田通商'), ('8031', '三井物'), ('8053', '住友商'), ('8058', '三菱商'), ('1721', 'コムシスＨＤ'), ('1801', '大成建'), ('1802', '大林組'), ('1803', '清水建'), ('1808', '長谷工'), ('1812', '鹿島'), ('1925', 'ハウス'), ('1928', '積ハウス'), ('1963', '日揮ＨＤ'), ('5631', '日製鋼'), ('6103', 'オークマ'), ('6113', 'アマダ'), ('6273', 'ＳＭＣ'), ('6301', 'コマツ'), ('6302', '住友重'), ('6305', '日立建機'), ('6326', 'クボタ'), ('6361', '荏原'), ('6367', 'ダイキン'), ('6471', '日精工'), ('6472', 'ＮＴＮ'), ('6473', 'ジェイテクト'), ('7011', '三菱重'), ('7013', 'ＩＨＩ'), ('7012', '川重'), ('7832', 'バンナムＨＤ'), ('7911', 'ＴＯＰＰＡＮ'), ('7912', '大日印'), ('7951', 'ヤマハ'), ('3289', '東急不ＨＤ'), ('8801', '三井不'), ('8802', '菱地所'), ('8804', '東建物'), ('8830', '住友不'), ('9001', '東武'), ('9005', '東急'), ('9007', '小田急'), ('9008', '京王'), ('9009', '京成'), ('9020', 'ＪＲ東日本'), ('9021', 'ＪＲ西日本'), ('9022', 'ＪＲ東海'), ('9064', 'ヤマトＨＤ'), ('9147', 'ＮＸＨＤ'), ('9101', '郵船'), ('9104', '商船三井'), ('9107', '川崎汽'), ('9201', 'ＪＡＬ'), ('9202', 'ＡＮＡＨＤ'), ('9501', '東電ＨＤ'), ('9502', '中部電'), ('9503', '関西電'), ('9531', '東ガス'), ('9532', '大ガス')]


def parse_nikkei225_csv(content):
    """日経日本公式ウエートCSV。先頭の説明行と文字コードの差を許容。"""
    for encoding in ("utf-8-sig", "cp932", "utf-16"):
        try:
            text = content.decode(encoding)
        except UnicodeError:
            continue
        for start, line in enumerate(text.splitlines()[:30]):
            if "コード" not in line:
                continue
            try:
                frame = pd.read_csv(io.StringIO("\n".join(text.splitlines()[start:])), dtype=str)
                frame.columns = [str(c).strip() for c in frame.columns]
                cc = next(c for c in frame if "コード" in c)
                nc = next(c for c in frame if "銘柄名" in c or "社名" in c)
                codes = frame[cc].str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
                mask = codes.str.fullmatch(r"[0-9A-Z]{4}", na=False)
                out = pd.DataFrame({"コード": codes[mask], "銘柄名": frame.loc[mask, nc].str.strip()})
                out = out.drop_duplicates("コード").reset_index(drop=True)
                if len(out) != 225 or out["銘柄名"].isna().any():
                    continue
                out["ticker"] = out["コード"] + ".T"
                out["構成銘柄情報源"] = "日経日本公式ウエート一覧CSV"
                return out
            except (ValueError, StopIteration, pd.errors.ParserError):
                continue
    raise ValueError("日経日本公式CSVで225銘柄を確認できません")


@st.cache_data(ttl=1800, show_spinner=False)
def discover_nikkei225_stock_universe():
    errors = []
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja-JP,ja;q=0.9"}
    sources = (
        ("https://indexes.nikkei.co.jp/nkave/archives/file/nikkei_stock_average_weight_jp.csv", "csv"),
        ("https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225", "html"),
    )
    for url, kind in sources:
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            if kind == "csv":
                out = parse_nikkei225_csv(response.content)
            else:
                response.encoding = "utf-8"
                out = parse_nikkei225_constituents(response.text)
            out.attrs["universe_notice"] = ""
            out.attrs["confirmed_at_jst"] = tokyo_now().isoformat()
            out.attrs["source_url"] = url
            return out
        except (requests.RequestException, ValueError) as exc:
            errors.append(kind + ": " + type(exc).__name__ + " " + str(exc)[:120])
    out = pd.DataFrame(NIKKEI225_SNAPSHOT, columns=["コード", "銘柄名"])
    if len(out) != 225 or not out["コード"].is_unique:
        raise ValueError("日経225の保存済み構成一覧が不正です")
    out["ticker"] = out["コード"] + ".T"
    out["構成銘柄情報源"] = f"日経公式保存一覧（{NIKKEI225_SNAPSHOT_DATE}更新）"
    out.attrs["universe_notice"] = (
        f"日経日本公式CSV・公式ページの取得に失敗したため、保存一覧（{NIKKEI225_SNAPSHOT_DATE}・225銘柄）を使用しています。"
        "最新の構成変更は未確認です。30分後に再取得します。"
        " 取得状況: " + " / ".join(errors)
    )
    return out


def merge_index_universes(topix, nikkei):
    topix, nikkei = topix.copy(), nikkei.copy()
    topix["対象指数"] = "TOPIX"
    nikkei["対象指数"] = "日経225"
    combined = pd.concat([topix, nikkei], ignore_index=True)
    out = combined.drop_duplicates("コード", keep="first").copy()
    common = set(topix["コード"]) & set(nikkei["コード"])
    mask = out["コード"].isin(common)
    out.loc[mask, "対象指数"] = "TOPIX＋日経225"
    out.loc[mask, "構成銘柄情報源"] = "JPX公開TOPIX一覧＋" + nikkei["構成銘柄情報源"].iloc[0]
    out.attrs["universe_notice"] = nikkei.attrs.get("universe_notice", "")
    return out.reset_index(drop=True)


@st.cache_data(ttl=21600, show_spinner=False)
def discover_topix_nikkei225_stock_universe():
    # 日経側のアクセス制限時は保存一覧を使い、注意表示を統合結果へ引き継ぐ。
    return add_growth_universe(merge_index_universes(discover_topix_stock_universe(),
                                 discover_nikkei225_stock_universe()))


@st.cache_data(ttl=21600, show_spinner=False)
def discover_japan_stock_universe(limit=350):
    """TradingView日本株スキャナーから売買代金上位の普通株を数百銘柄取得する。
    旧49銘柄へ黙ってフォールバックすると「本物のTOP50」ではなくなるため、失敗時は空を返す。
    """
    limit = int(np.clip(limit, 200, 500))
    columns = ["name", "description", "close", "volume", "Value.Traded", "market_cap_basic", "exchange"]
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "options": {"lang": "ja"},
        "markets": ["japan"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, limit],
    }
    headers = {
        "Accept": "application/json", "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/",
        "User-Agent": "Mozilla/5.0 (compatible; JPStockAssistant/17.7)",
    }
    try:
        r = requests.post("https://scanner.tradingview.com/japan/scan", headers=headers, json=payload, timeout=25)
        r.raise_for_status()
        rows = []
        for item in (r.json().get("data") or []):
            sym = str(item.get("s", ""))
            vals = list(item.get("d") or [])
            if ":" not in sym or len(vals) < len(columns):
                continue
            ex, raw_code = sym.split(":", 1)
            c = raw_code.strip().upper()
            # 東証の通常の4桁/英数字コードだけを採用。ETF等はscanner側type=stockで大半を除外。
            if not re.fullmatch(r"[0-9A-Z]{4}", c):
                continue
            d = dict(zip(columns, vals))
            px = safe_float(d.get("close"))
            vol = safe_float(d.get("volume"), 0.0)
            traded = safe_float(d.get("Value.Traded"))
            if not np.isfinite(px) or px <= 0 or vol <= 0:
                continue
            if not np.isfinite(traded) or traded <= 0:
                traded = px * vol
            rows.append({
                "コード": c,
                "銘柄名": str(d.get("description") or c),
                "ticker": c + ".T",
                "現在値": px,
                "出来高": vol,
                "売買代金": traded,
                "時価総額": safe_float(d.get("market_cap_basic")),
                "取引所": str(d.get("exchange") or ex),
            })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).drop_duplicates("コード", keep="first")
        return df.sort_values("売買代金", ascending=False).head(limit).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _prepare_light_history_frame(df):
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(-1)
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in x.columns for c in cols):
        return pd.DataFrame()
    x = x[cols + (["Adj Close"] if "Adj Close" in x.columns else [])].copy()
    for c in cols:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    if "Adj Close" in x:
        x["Adj Close"] = pd.to_numeric(x["Adj Close"], errors="coerce")
    x = x.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_index()
    if len(x) < 80:
        return pd.DataFrame()
    x["MA25"] = x["Close"].rolling(25).mean()
    x["MA75"] = x["Close"].rolling(75).mean()
    x["VOL20"] = x["Volume"].rolling(20).mean()
    x["Turnover"] = x["Close"] * x["Volume"]
    tr = pd.concat([
        x["High"] - x["Low"],
        (x["High"] - x["Close"].shift()).abs(),
        (x["Low"] - x["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    x["ATR14"] = tr.rolling(14).mean()
    x["Return_25d"] = x["Close"].pct_change(25) * 100.0
    # GFS研究の252日高値は、MA75の準備期間74行を削る前に計算する。
    # スカラーだけをattrsへ保存し、現行売買に渡す行・列・指標値は変えない。
    gfs_high = safe_float(x["High"].shift(1).rolling(252, min_periods=252).max().iloc[-1])
    out = x.dropna(subset=["MA25", "MA75", "ATR14", "Return_25d"])
    if not out.empty:
        out.attrs.update({"gfs_history_version": 2,
                          "gfs_252_previous_high": float(gfs_high) if np.isfinite(gfs_high) else None,
                          "gfs_prior_bars": max(len(x) - 1, 0),
                          "gfs_source_final_date": pd.Timestamp(x.index[-1]).isoformat()})
    return out




@st.cache_data(ttl=3600, show_spinner=False)
def export_backtest_history_5y(tickers_tuple):
    """管理者検証用。現在の大規模母集団について5年分の日足OHLCVを長形式で取得する。
    売買ロジックには一切使用しない。
    """
    ts = list(dict.fromkeys([str(t) for t in tickers_tuple if str(t).endswith(".T")]))
    rows = []
    failed = []
    if not ts:
        return pd.DataFrame(), pd.DataFrame(columns=["ticker","コード","理由"])
    for i in range(0, len(ts), 50):
        chunk = ts[i:i+50]
        try:
            raw = yf.download(
                tickers=chunk, period="5y", interval="1d",
                auto_adjust=False, actions=True, progress=False, threads=True,
                group_by="ticker", timeout=30,
            )
        except Exception as e:
            raw = pd.DataFrame()
            for t in chunk:
                failed.append({"ticker":t,"コード":code(t),"理由":f"一括取得失敗: {e}"})
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if t in raw.columns.get_level_values(0):
                        one = raw[t].copy()
                    elif t in raw.columns.get_level_values(1):
                        one = raw.xs(t, axis=1, level=1).copy()
                    else:
                        failed.append({"ticker":t,"コード":code(t),"理由":"銘柄列なし"})
                        continue
                else:
                    if len(chunk) != 1:
                        failed.append({"ticker":t,"コード":code(t),"理由":"列形式不一致"})
                        continue
                    one = raw.copy()
                one = one.rename_axis("日付").reset_index()
                keep = [c for c in ["日付","Open","High","Low","Close","Adj Close","Volume","Stock Splits"] if c in one.columns]
                one = one[keep].copy()
                one["ticker"] = t
                one["コード"] = code(t)
                one["銘柄名"] = name(t)
                for c in ["Open","High","Low","Close","Adj Close","Volume"]:
                    if c in one.columns:
                        one[c] = pd.to_numeric(one[c], errors="coerce")
                one = one.dropna(subset=[c for c in ["Open","High","Low","Close"] if c in one.columns])
                if one.empty:
                    failed.append({"ticker":t,"コード":code(t),"理由":"有効日足なし"})
                    continue
                rows.append(one)
            except Exception as e:
                failed.append({"ticker":t,"コード":code(t),"理由":str(e)[:160]})
    hist = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not hist.empty:
        hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce").dt.strftime("%Y-%m-%d")
        hist = hist.sort_values(["コード","日付"]).reset_index(drop=True)
    return hist, pd.DataFrame(failed)

@st.cache_data(ttl=300, show_spinner=False)
def batch_stock_data_light(tickers_tuple, months=15, gfs_history_version=2):
    """数百銘柄の日足を取得し、16時以降は直近東証日を一括補完する。

    過去履歴はyfinanceを使い、遅れやすい最終1日だけTradingViewの東証
    一括OHLCVで補う。補完不能時は従来履歴へ安全にフォールバックする。
    """
    ts = list(dict.fromkeys([str(t) for t in tickers_tuple if str(t).endswith(".T")]))
    out = {}
    if not ts:
        return out
    session = latest_tse_session_status("7203.T")
    session_date = session.get("session_date", pd.NaT)
    same_day_quotes = {}
    quote_diagnostics = []
    if bool(session.get("confirmed")) and pd.notna(session_date):
        same_day_quotes, quote_diagnostics = tradingview_batch_quotes(tuple(ts))
    # 大きすぎる一括DLは不安定なので60銘柄ずつ。
    for i in range(0, len(ts), 60):
        chunk = ts[i:i+60]
        try:
            raw = yf.download(
                tickers=chunk, period=f"{int(months)}mo", interval="1d",
                auto_adjust=False, actions=False, progress=False, threads=True,
                group_by="ticker", timeout=25,
            )
        except Exception:
            raw = pd.DataFrame()
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if t in raw.columns.get_level_values(0):
                        one = raw[t].copy()
                    elif t in raw.columns.get_level_values(1):
                        one = raw.xs(t, axis=1, level=1).copy()
                    else:
                        continue
                else:
                    if len(chunk) != 1:
                        continue
                    one = raw.copy()
                one, same_day_added = _merge_same_day_quote(
                    one, same_day_quotes.get(t), session_date
                )
                fx = _prepare_light_history_frame(one)
                if not fx.empty:
                    latest = pd.Timestamp(fx.index.max()).normalize()
                    is_current = bool(pd.notna(session_date) and latest >= pd.Timestamp(session_date).normalize())
                    fx.attrs.update({
                        "source": (
                            "yfinance履歴 + TradingView東証一括OHLCV（当日補完）"
                            if same_day_added else "yfinance一括日足"
                        ),
                        "same_day_added": bool(same_day_added),
                        "session_date": session_date,
                        "latest_date": latest,
                        "daily_bar_status": (
                            "🟢 当日確定" if is_current and bool(session.get("confirmed"))
                            else "🟡 取引中・未確定" if pd.notna(session_date) and not bool(session.get("confirmed"))
                            else "🔴 更新待ち"
                        ),
                        "session_source": session.get("source", ""),
                        "quote_diagnostics": quote_diagnostics,
                    })
                    out[t] = fx
            except Exception:
                continue
    return out


def build_daily_bar_status(data):
    """画面表示とZIP保存用に、銘柄別の日足鮮度を一覧化する。"""
    rows = []
    for t, frame in (data or {}).items():
        if frame is None or frame.empty:
            continue
        latest = pd.Timestamp(frame.index.max()).normalize()
        session_date = frame.attrs.get("session_date", pd.NaT)
        session_date = pd.Timestamp(session_date).normalize() if pd.notna(session_date) else pd.NaT
        rows.append({
            "コード": code(t), "銘柄名": name(t),
            "日足最終日": latest.strftime("%Y-%m-%d"),
            "直近東証取引日": session_date.strftime("%Y-%m-%d") if pd.notna(session_date) else "確認不可",
            "状態": frame.attrs.get("daily_bar_status", "不明"),
            "当日補完": bool(frame.attrs.get("same_day_added", False)),
            "データ元": frame.attrs.get("source", "不明"),
        })
    return pd.DataFrame(rows)


# ============================================================
# Ver.17 MAIN — ストキャスティクス実運用版
# ============================================================


def rsi_prepare_light(df, period=5):
    """管理者比較用RSI。実売買シグナルには使わない。
    BUY: RSI(period) が15以下の領域から15を上抜く。
    """
    if df is None or df.empty or len(df) < max(20, period + 3):
        return pd.DataFrame()
    x = df.sort_index().copy()
    close = pd.to_numeric(x["Close"], errors="coerce")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    x[f"RSI{period}"] = rsi
    prev = rsi.shift(1)
    x["RSI_BUY"] = (prev <= 15.0) & (rsi > 15.0)
    x["RSI_SELL"] = (prev < 65.0) & (rsi >= 65.0)
    return x


def bb_prepare_light(df, period=20, std_mult=2.0):
    """管理者比較用ボリンジャーバンド。実売買シグナルには使わない。
    BUY: 前日終値が-2σ以下、当日終値が-2σの内側へ復帰。
    """
    if df is None or df.empty or len(df) < period + 3:
        return pd.DataFrame()
    x = df.sort_index().copy()
    close = pd.to_numeric(x["Close"], errors="coerce")
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    lower = mid - std_mult * sd
    upper = mid + std_mult * sd
    x["BB_MID"] = mid
    x["BB_LOWER"] = lower
    x["BB_UPPER"] = upper
    x["BB_BUY"] = (close.shift(1) <= lower.shift(1)) & (close > lower)
    x["BB_SELL"] = close >= mid
    return x


def build_indicator_compare(data, top50_codes, held_codes=None):
    """管理者研究用: 同一TOP50で Stoch / RSI5 / BB20 の現在シグナルを比較。
    実売買のBUY/SELL判定には一切接続しない。
    """
    held_codes = set(map(str, held_codes or []))
    rows = []
    for t, df0 in (data or {}).items():
        c = code(t)
        if c not in set(map(str, top50_codes)) or c in held_codes:
            continue
        if df0 is None or df0.empty:
            continue
        px = safe_float(pd.to_numeric(df0["Close"], errors="coerce").iloc[-1])
        if not np.isfinite(px) or px <= 0:
            continue

        sx = stoch_prepare_light(df0, 14, 3, 3)
        rx = rsi_prepare_light(df0, 5)
        bx = bb_prepare_light(df0, 20, 2.0)

        sk = sd = rsi5 = bbl = np.nan
        st_buy = rsi_buy = bb_buy = False
        if not sx.empty:
            rr = sx.iloc[-1]
            sk, sd = safe_float(rr.get("STOCH_K")), safe_float(rr.get("STOCH_D"))
            st_buy = bool(rr.get("STOCH_GC", False)) and np.isfinite(sk) and sk <= 20.0
        if not rx.empty:
            rr = rx.iloc[-1]
            rsi5 = safe_float(rr.get("RSI5"))
            rsi_buy = bool(rr.get("RSI_BUY", False))
        if not bx.empty:
            rr = bx.iloc[-1]
            bbl = safe_float(rr.get("BB_LOWER"))
            bb_buy = bool(rr.get("BB_BUY", False))

        methods = []
        if st_buy: methods.append("Stoch")
        if rsi_buy: methods.append("RSI5")
        if bb_buy: methods.append("BB20")
        if not methods:
            continue
        rows.append({
            "コード": c, "銘柄名": name(t), "現在株価": px,
            "Stoch_BUY": bool(st_buy), "%K": sk, "%D": sd,
            "RSI5_BUY": bool(rsi_buy), "RSI5": rsi5,
            "BB20_BUY": bool(bb_buy), "BB下限": bbl,
            "一致数": int(st_buy) + int(rsi_buy) + int(bb_buy),
            "発火方式": "+".join(methods),
        })
    if not rows:
        return pd.DataFrame(columns=[
            "コード","銘柄名","現在株価","Stoch_BUY","%K","%D",
            "RSI5_BUY","RSI5","BB20_BUY","BB下限","一致数","発火方式"
        ])
    out = pd.DataFrame(rows)
    out = out.sort_values(["一致数","コード"], ascending=[False, True]).reset_index(drop=True)
    return out


def build_top50_surge_radar(data, value_top50_df):
    """旧Ver.5.5系の急騰予兆センサーを、現在の企業価値AI TOP50だけへ適用する。

    管理者向けの観察センサーであり、正式なBUY/SELL判定には接続しない。
    旧版にあった「株価2,000円未満」の除外条件は復活させない。
    ニュース取得も追加せず、取得済みOHLCVだけで軽量に計算する。
    """
    columns = [
        "急騰順位", "TOP50順位", "コード", "銘柄名", "現在株価",
        "急騰予兆スコア", "急騰予兆判定", "AI_TOP50スコア", "割安判定",
        "RSI", "5日騰落率", "25日騰落率", "出来高倍率", "MA25乖離率",
        "MA25傾き", "20日高値更新", "出来高ポイント", "騰落率ポイント",
        "トレンドポイント", "ブレイクポイント", "過熱減点",
        "Stoch_BUY", "%K", "%D", "正式売買へ影響",
    ]
    if not isinstance(value_top50_df, pd.DataFrame) or value_top50_df.empty:
        return pd.DataFrame(columns=columns)

    meta = value_top50_df.copy()
    meta["コード"] = meta["コード"].astype(str)
    meta = meta.head(50).set_index("コード", drop=False)
    target_codes = set(meta.index.tolist())
    rows = []

    for t, df0 in (data or {}).items():
        c = code(t)
        if c not in target_codes or df0 is None or df0.empty or len(df0) < 30:
            continue

        x = df0.sort_index().copy()
        close = pd.to_numeric(x["Close"], errors="coerce")
        high = pd.to_numeric(x["High"], errors="coerce")
        volume = pd.to_numeric(x["Volume"], errors="coerce")
        ma25 = close.rolling(25).mean()
        vol20 = volume.rolling(20).mean()
        ma25_slope = ma25 - ma25.shift(5)
        ret5 = close.pct_change(5) * 100.0
        ret25 = close.pct_change(25) * 100.0
        volume_ratio = volume / vol20.replace(0, np.nan)

        # 旧版と同じ14日RSI（単純移動平均方式）。
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(14).mean()
        loss = (-delta.clip(upper=0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi14 = 100.0 - (100.0 / (1.0 + rs))

        px = safe_float(close.iloc[-1])
        r5 = safe_float(ret5.iloc[-1])
        r25 = safe_float(ret25.iloc[-1])
        vr = safe_float(volume_ratio.iloc[-1])
        m25 = safe_float(ma25.iloc[-1])
        slope = safe_float(ma25_slope.iloc[-1])
        rsi = safe_float(rsi14.iloc[-1])
        if not all(np.isfinite(v) for v in [px, r5, r25, vr, m25, slope]) or px <= 0 or m25 <= 0:
            continue

        ma25_gap = (px / m25 - 1.0) * 100.0
        prior20 = safe_float(high.shift(1).rolling(20).max().iloc[-1])
        breakout = bool(np.isfinite(prior20) and px > prior20)

        # 旧・急騰予兆レーダーのポイント配分を復元（ニュース点は0点）。
        vol_pts = 30 if vr >= 3 else 22 if vr >= 2 else 14 if vr >= 1.5 else 6 if vr >= 1.2 else 0
        ret_pts = 24 if 8 <= r5 <= 25 else 18 if 5 <= r5 < 8 else 10 if 0 <= r5 < 5 else 6 if r5 > 25 else 0
        trend_pts = 18 if slope > 0 and px > m25 else 10 if slope > 0 else 0
        breakout_pts = 18 if breakout else 0
        overheat_penalty = 18 if r5 >= 30 or (np.isfinite(rsi) and rsi >= 80) else 10 if r5 >= 20 or (np.isfinite(rsi) and rsi >= 75) else 0
        score = int(np.clip(vol_pts + ret_pts + trend_pts + breakout_pts - overheat_penalty, 0, 100))

        state = (
            "🚨 強い急騰予兆" if score >= 70 else
            "🟠 急騰予兆" if score >= 55 else
            "🟡 変化検知" if score >= 40 else
            "⚪ 通常"
        )

        sx = stoch_prepare_light(df0, 14, 3, 3)
        sk = sd = np.nan
        st_buy = False
        if not sx.empty:
            sr = sx.iloc[-1]
            sk, sd = safe_float(sr.get("STOCH_K")), safe_float(sr.get("STOCH_D"))
            st_buy = bool(sr.get("STOCH_GC", False)) and np.isfinite(sk) and sk <= 20.0

        mr = meta.loc[c]
        rows.append({
            "TOP50順位": int(safe_float(mr.get("順位"), 0)),
            "コード": c, "銘柄名": str(mr.get("銘柄名", name(t))), "現在株価": px,
            "急騰予兆スコア": score, "急騰予兆判定": state,
            "AI_TOP50スコア": safe_float(mr.get("AI_TOP50スコア")),
            "割安判定": str(mr.get("割安判定", "")),
            "RSI": rsi, "5日騰落率": r5, "25日騰落率": r25,
            "出来高倍率": vr, "MA25乖離率": ma25_gap, "MA25傾き": slope,
            "20日高値更新": "🟢 更新" if breakout else "—",
            "出来高ポイント": vol_pts, "騰落率ポイント": ret_pts,
            "トレンドポイント": trend_pts, "ブレイクポイント": breakout_pts,
            "過熱減点": -overheat_penalty,
            "Stoch_BUY": bool(st_buy), "%K": sk, "%D": sd,
            "正式売買へ影響": False,
        })

    if not rows:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(rows).sort_values(
        ["急騰予兆スコア", "出来高倍率", "TOP50順位"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    out.insert(0, "急騰順位", np.arange(1, len(out) + 1))
    return out[[c for c in columns if c in out.columns]]


def add_research_judgements_first(df, surge_df, value_df):
    """表示用の全銘柄表へ研究判定を付け、必ず左端へ並べる。

    急騰予兆は観察情報。割安判定は表示にも使い、割高だけは別工程で新規BUYから除外する。
    TOP50外など算定値がない銘柄も、空欄にせず対象外/未算定と明示する。
    """
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    out = df.copy()
    if "コード" not in out.columns:
        return out

    out["コード"] = out["コード"].astype(str).str.replace(r"\.0$", "", regex=True)

    surge_map = {}
    if isinstance(surge_df, pd.DataFrame) and not surge_df.empty and "コード" in surge_df.columns:
        sm = surge_df.copy()
        sm["コード"] = sm["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
        if "急騰予兆判定" in sm.columns:
            surge_map = sm.drop_duplicates("コード").set_index("コード")["急騰予兆判定"].to_dict()

    value_map = {}
    if isinstance(value_df, pd.DataFrame) and not value_df.empty and "コード" in value_df.columns:
        vm = value_df.copy()
        vm["コード"] = vm["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
        if "割安判定" in vm.columns:
            value_map = vm.drop_duplicates("コード").set_index("コード")["割安判定"].to_dict()

    # 既存列があっても共通マスターを優先し、欠損時だけ既存表示を残す。
    old_surge = out["急騰予兆判定"].copy() if "急騰予兆判定" in out.columns else pd.Series("", index=out.index)
    old_value = out["割安判定"].copy() if "割安判定" in out.columns else pd.Series("", index=out.index)
    mapped_surge = out["コード"].map(surge_map)
    mapped_value = out["コード"].map(value_map)
    out["急騰予兆判定"] = mapped_surge.where(mapped_surge.notna(), old_surge)
    out["割安判定"] = mapped_value.where(mapped_value.notna(), old_value)
    out["急騰予兆判定"] = out["急騰予兆判定"].replace("", np.nan).fillna("⚪ 判定対象外")
    out["割安判定"] = out["割安判定"].replace("", np.nan).fillna("— 未算定")

    first = ["急騰予兆判定", "割安判定"]
    return out[first + [c for c in out.columns if c not in first]]

def stoch_prepare_light(df, k_period=14, k_smooth=3, d_period=3):
    """Slow Stochastic 14,3,3。Ver.16で採用したBUY/SELL判定専用。"""
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy().sort_index()
    ratio = (pd.to_numeric(x["Adj Close"], errors="coerce") / x["Close"]
             if "Adj Close" in x.columns else pd.Series(1.0, index=x.index))
    ratio = ratio.where(np.isfinite(ratio) & ratio.gt(0), 1.0)
    low_n = (x["Low"] * ratio).rolling(int(k_period), min_periods=int(k_period)).min()
    high_n = (x["High"] * ratio).rolling(int(k_period), min_periods=int(k_period)).max()
    den = (high_n - low_n).replace(0, np.nan)
    fast_k = (x["Close"] * ratio - low_n) / den * 100.0
    x["STOCH_K"] = fast_k.rolling(int(k_smooth), min_periods=int(k_smooth)).mean()
    x["STOCH_D"] = x["STOCH_K"].rolling(int(d_period), min_periods=int(d_period)).mean()
    x["STOCH_GC"] = (x["STOCH_K"] > x["STOCH_D"]) & (x["STOCH_K"].shift(1) <= x["STOCH_D"].shift(1))
    x["STOCH_DC"] = (x["STOCH_K"] < x["STOCH_D"]) & (x["STOCH_K"].shift(1) >= x["STOCH_D"].shift(1))
    return x


def stoch_quality_frame(df, stop_pct=7.0, band_window=20):
    """前日までの帯と当日までの価格だけでクロスを評価。欠損時はシグナルを出さない。"""
    x = stoch_prepare_light(df)
    if x.empty:
        return x
    close = pd.to_numeric(x["Close"], errors="coerce")
    k = x["STOCH_K"]
    d = x["STOCH_D"]
    # 前日までの20日間で帯を固定。狭すぎる帯の上限/下限には床を設ける。
    low = k.shift(1).rolling(band_window, min_periods=band_window).quantile(.25)
    high = k.shift(1).rolling(band_window, min_periods=band_window).quantile(.75)
    x["帯下限"] = np.minimum(low, 35.0)
    x["帯上限"] = np.maximum(high, 65.0)
    ma25 = close.rolling(25, min_periods=25).mean()
    ma75 = close.rolling(75, min_periods=75).mean()
    x["上昇トレンド"] = (close > ma25) & (ma25 > ma25.shift(5)) & (ma25 > ma75)
    x["下降確認"] = (close < ma25) & (ma25 < ma25.shift(5))
    x["小幅クロス"] = (k - d).abs() < 1.0
    x["BUY_現行"] = x["STOCH_GC"].fillna(False) & k.le(20)
    x["SELL_現行"] = x["STOCH_DC"].fillna(False)
    x["BUY_改善"] = (x["BUY_現行"] & k.le(x["帯下限"]) &
                    x["上昇トレンド"] & ~x["小幅クロス"] &
                    close.gt(close.shift(1)))
    # 帯の下側でのデッドクロス、または価格も下向きのデッドクロス。
    x["SELL_改善"] = (x["SELL_現行"] & ~x["小幅クロス"] &
                     (k.ge(x["帯上限"]) | x["下降確認"]) &
                     close.lt(close.shift(1)))
    return x


@st.cache_data(ttl=900, max_entries=1200, show_spinner=False)
def stoch_signal_quality(df):
    """直近の買いクロスの事後成績。未成熟な直近5日は数えない。"""
    x = stoch_quality_frame(df)
    if len(x) < 85:
        return {"検証GC件数": 0, "検証GC平均5日騰落%": np.nan}
    hit = x["BUY_現行"].iloc[max(0, len(x)-100):-5]
    future = x["Close"].shift(-5).div(x["Close"]).sub(1).mul(100)
    values = future.loc[hit.index[hit]].dropna()
    return {"検証GC件数": int(len(values)),
            "検証GC平均5日騰落%": round(float(values.mean()), 2) if len(values) else np.nan}


def compare_stoch_backtest(history, stop_pct=7.0, top_codes=None):
    """銘柄別独立1ポジション。終値判定→翌日始値約定、未決済は評価損益を分離。"""
    required = {"コード", "日付", "Open", "High", "Low", "Close"}
    if not required.issubset(history.columns):
        raise ValueError("日足CSVには コード・日付・Open・High・Low・Close が必要です")
    rows, trades = [], []
    hist = history.copy()
    hist["コード"] = hist["コード"].astype(str).str.replace(r"\.T$", "", regex=True)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    for col in ("Open", "High", "Low", "Close"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=["日付", "Open", "High", "Low", "Close"])
    hist = hist[(hist["Open"] > 0) & (hist["High"] > 0) & (hist["Low"] > 0) & (hist["Close"] > 0)]
    if top_codes is not None:
        hist = hist[hist["コード"].isin(set(map(str, top_codes)))]
    for c, df in hist.groupby("コード", sort=False):
        df = df.sort_values("日付").drop_duplicates("日付", keep="last").set_index("日付")
        if len(df) < 90:
            continue
        x = stoch_quality_frame(df, stop_pct)
        for mode in ("現行", "改善"):
            entry = None
            for i in range(75, len(x)-1):
                today, tomorrow = x.iloc[i], x.iloc[i+1]
                op = float(tomorrow["Open"])
                if entry is None:
                    if bool(today[f"BUY_{mode}"]):
                        entry = (op, x.index[i+1])
                    continue
                entry_px, entry_date = entry
                stop = float(today["Close"]) / entry_px - 1 <= -stop_pct / 100
                if stop or bool(today[f"SELL_{mode}"]):
                    trades.append({"方式": mode, "コード": c, "買い日": entry_date,
                                   "売り日": x.index[i+1], "買い始値": entry_px,
                                   "売り始値": op, "騰落率%": (op / entry_px - 1) * 100,
                                   "売り理由": "損切り" if stop else "ストキャスDC"})
                    entry = None
            if entry is not None:
                rows.append({"方式": mode, "コード": c, "買い日": entry[1],
                             "最終日": x.index[-1], "評価損益率%":
                             (float(x["Close"].iloc[-1]) / entry[0] - 1) * 100})
    tdf = pd.DataFrame(trades)
    udf = pd.DataFrame(rows)
    summary = []
    for mode in ("現行", "改善"):
        a = tdf[tdf["方式"].eq(mode)] if not tdf.empty else pd.DataFrame()
        p = a["騰落率%"] if not a.empty else pd.Series(dtype=float)
        gains, losses = p[p > 0].sum(), -p[p < 0].sum()
        summary.append({"方式": mode, "決済件数": len(a), "勝率%": round(float((p > 0).mean()*100), 2) if len(p) else np.nan,
                        "平均取引騰落率%": round(float(p.mean()), 2) if len(p) else np.nan,
                        "PF_単純合算": round(float(gains/losses), 2) if losses > 0 else np.nan,
                        "未決済件数": int(udf["方式"].eq(mode).sum()) if not udf.empty else 0})
    return pd.DataFrame(summary), tdf, udf


def compare_value_addon_backtest(history, stop_pct=7.0, top_codes=None,
                                valuation_history=None, max_value_age_days=120,
                                addon_fraction=0.5):
    """終値シグナル→翌日始値、銘柄別の独立検証。過去の評価だけを参照する。"""
    required = {"コード", "日付", "Open", "High", "Low", "Close"}
    if not required.issubset(history.columns):
        raise ValueError("日足CSVには コード・日付・Open・High・Low・Close が必要です")
    hist = history.copy()
    hist["コード"] = hist["コード"].astype(str).str.replace(r"\.T$", "", regex=True)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce").dt.normalize()
    for col in ("Open", "High", "Low", "Close"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=list(required))
    hist = hist[(hist[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    if top_codes is not None:
        hist = hist[hist["コード"].isin(set(map(str, top_codes)))]
    values = {}
    if valuation_history is not None:
        need = {"コード", "評価日", "AI参考価値"}
        if not need.issubset(valuation_history.columns):
            raise ValueError("過去評価CSVには コード・評価日・AI参考価値 が必要です")
        val = valuation_history.copy()
        val["コード"] = val["コード"].astype(str).str.replace(r"\.T$", "", regex=True)
        val["評価日"] = pd.to_datetime(val["評価日"], errors="coerce").dt.normalize()
        val["AI参考価値"] = pd.to_numeric(val["AI参考価値"], errors="coerce")
        val = val.dropna(subset=["コード", "評価日", "AI参考価値"])
        val = val[val["AI参考価値"] > 0]
        for c, part in val.groupby("コード"):
            part = part.sort_values("評価日").drop_duplicates("評価日", keep="last")
            values[c] = (part["評価日"].to_numpy(dtype="datetime64[ns]"),
                         part["AI参考価値"].to_numpy(dtype=float))

    def valuation_at(c, date, close):
        dates, fair = values.get(c, (None, None))
        if dates is None:
            return "評価なし", np.nan, pd.NaT
        pos = dates.searchsorted(np.datetime64(date), side="right") - 1
        if pos < 0:
            return "評価なし", np.nan, pd.NaT
        age = (pd.Timestamp(date) - pd.Timestamp(dates[pos])).days
        if age > max_value_age_days:
            return "期限切れ", np.nan, pd.Timestamp(dates[pos])
        upside = (float(fair[pos]) / close - 1.0) * 100.0
        return ("割高" if upside <= -10.0 else "通過"), upside, pd.Timestamp(dates[pos])

    modes = ["現行"] + (["割高除外"] if valuation_history is not None else [])
    trades, open_positions, audits = [], [], []
    for c, df in hist.groupby("コード", sort=False):
        df = df.sort_values("日付").drop_duplicates("日付", keep="last").set_index("日付")
        if len(df) < 90:
            continue
        x = stoch_quality_frame(df, stop_pct)
        for mode in modes:
            for addon_enabled in (False, True):
                variant = f"{mode}・{'買い増しあり' if addon_enabled else '買い増しなし'}"
                lots = []  # (株数, 翌日始値, 買い日)。初回は1株相当、追加はその一定割合。
                for i in range(75, len(x) - 1):
                    today, tomorrow = x.iloc[i], x.iloc[i + 1]
                    date, next_date = x.index[i], x.index[i + 1]
                    close, op = float(today["Close"]), float(tomorrow["Open"])
                    avg = (sum(q * p for q, p, _ in lots) / sum(q for q, _, _ in lots)) if lots else np.nan
                    stop = bool(lots and close / avg - 1 <= -stop_pct / 100)
                    sell = bool(today["SELL_現行"])
                    # 売り・損切りを優先。売った当日に買い直すこともしない。
                    if lots and (stop or sell):
                        paid = sum(q * p for q, p, _ in lots)
                        proceeds = op * sum(q for q, _, _ in lots)
                        trades.append({"方式": variant, "コード": c, "買い日": lots[0][2],
                                       "売り日": next_date, "買い増し回数": len(lots) - 1,
                                       "買い増し日": lots[1][2] if len(lots) > 1 else pd.NaT,
                                       "投下額_1株換算": paid, "回収額_1株換算": proceeds,
                                       "騰落率%": (proceeds / paid - 1) * 100,
                                       "売り理由": "損切り" if stop else "ストキャスDC"})
                        lots = []
                        continue
                    # DCで売却する戦略では、保有中に次のGCを待つと必ず先に売却される。
                    # 買い増しは、初回GCの後に上向きが継続した場合だけ別途判定する。
                    fresh_buy = bool(today["BUY_現行"])
                    addon_buy = bool(
                        lots and addon_enabled and len(lots) == 1 and
                        i - x.index.get_loc(lots[0][2]) >= 2 and
                        np.isfinite(safe_float(today["STOCH_K"])) and
                        np.isfinite(safe_float(today["STOCH_D"])) and
                        float(today["STOCH_K"]) <= 50 and
                        float(today["STOCH_K"]) > float(today["STOCH_D"]) and
                        float(today["STOCH_K"]) > float(x.iloc[i-1]["STOCH_K"]) and
                        close > avg
                    )
                    if (not lots and fresh_buy) or addon_buy:
                        state, upside, valuation_date = valuation_at(c, date, close)
                        action = "買い増し" if lots else "新規"
                        permitted = mode == "現行" or state == "通過"
                        audits.append({"方式": variant, "コード": c, "判定日": date,
                                       "判定": action, "評価日": valuation_date,
                                       "参考価値上昇余地%": upside, "割高判定": state,
                                       "結果": "採用" if permitted else "見送り"})
                        if permitted:
                            lots.append((float(addon_fraction) if lots else 1.0, op, next_date))
                if lots:
                    paid = sum(q * p for q, p, _ in lots)
                    market_value = float(x["Close"].iloc[-1]) * sum(q for q, _, _ in lots)
                    open_positions.append({"方式": variant, "コード": c, "買い日": lots[0][2],
                                           "買い増し回数": len(lots) - 1, "最終日": x.index[-1],
                                           "評価損益率%": (market_value / paid - 1) * 100})
    tdf, udf, adf = pd.DataFrame(trades), pd.DataFrame(open_positions), pd.DataFrame(audits)
    summary = []
    for mode in modes:
        for addon_enabled in (False, True):
            variant = f"{mode}・{'買い増しあり' if addon_enabled else '買い増しなし'}"
            t = tdf[tdf["方式"].eq(variant)] if not tdf.empty else pd.DataFrame()
            a = adf[adf["方式"].eq(variant)] if not adf.empty else pd.DataFrame()
            p = t["騰落率%"] if not t.empty else pd.Series(dtype=float)
            gain, loss = p[p > 0].sum(), -p[p < 0].sum()
            summary.append({"方式": variant, "決済件数": len(t),
                            "勝率%": round(float((p > 0).mean() * 100), 2) if len(p) else np.nan,
                            "平均取引騰落率%": round(float(p.mean()), 2) if len(p) else np.nan,
                            "PF_単純合算": round(float(gain / loss), 2) if loss > 0 else np.nan,
                            "買い増し実行回数": int(t["買い増し回数"].sum()) if not t.empty else 0,
                            "評価なし・期限切れ見送り": int((a["結果"].eq("見送り") & a["割高判定"].isin(["評価なし", "期限切れ"])).sum()) if not a.empty else 0,
                            "割高見送り": int((a["結果"].eq("見送り") & a["割高判定"].eq("割高")).sum()) if not a.empty else 0,
                            "未決済件数": int(udf["方式"].eq(variant).sum()) if not udf.empty else 0})
    return pd.DataFrame(summary), tdf, udf, adf


def _mobile_text(value, fallback="—"):
    """カード表示用に欠損値を安全な短い文字列へ変換する。"""
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except Exception:
        pass
    text = str(value).strip()
    return html_escape(text if text else fallback)


def _mobile_number(value, decimals=0, suffix=""):
    """スマホカード用の数値表記。"""
    number = safe_float(value)
    if not np.isfinite(number):
        return "—"
    if decimals == 0:
        return f"{number:,.0f}{suffix}"
    return f"{number:,.{int(decimals)}f}{suffix}"


def _short_surge_label(value):
    text = str(value or "")
    if "強い急騰" in text:
        return "🚨 強い予兆"
    if "急騰予兆" in text:
        return "🟠 急騰予兆"
    if "変化検知" in text:
        return "🟡 変化"
    if "対象外" in text:
        return "⚪ 対象外"
    return "⚪ 通常"


def _short_value_label(value):
    text = str(value or "")
    if "財務取得不可" in text:
        return "⚠️ 財務取得不可"
    if "最新性未確認" in text:
        return "⚠️ 財務鮮度未確認"
    if "分割補正" in text:
        return "⚠️ 分割確認"
    if "異常値" in text:
        return "⚠️ 算定確認"
    if "やや割安" in text:
        return "🟡 やや割安"
    if "割安" in text:
        return "🟢 割安"
    if "割高" in text:
        return "🔴 割高"
    if "適正" in text:
        return "⚪ 適正"
    return "— 未算定"


def _trade_trend_text(code_value, trend_by_code, side, is_stop=False):
    """研究用トレンド判定を候補カードに表示するだけ。売買条件には使わない。"""
    if is_stop:
        return "🚨 損切り優先"
    trend = trend_by_code.get(str(code_value), {})
    if not trend:
        return "⚪ トレンド未判定"
    if trend.get("上昇トレンド", False):
        return "↗ 上昇トレンド・買いと一致" if side == "buy" else "↗ 上昇トレンド・売りは慎重"
    if trend.get("下降確認", False):
        return "↘ 下降トレンド・買いは慎重" if side == "buy" else "↘ 下降トレンド・売りと一致"
    return "→ 横ばいトレンド・振り回され注意"


def render_mobile_trade_cards(df, side, trend_by_code=None):
    """メイン画面専用の2行カード。元DataFrameとZIP出力は変更しない。"""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return
    trend_by_code = trend_by_code or {}

    for _, row in df.iterrows():
        code_text = _mobile_text(row.get("コード"))
        name_text = _mobile_text(row.get("銘柄名"))
        price_text = _mobile_number(row.get("現在株価"), 0, "円")
        surge_text = html_escape(_short_surge_label(row.get("急騰予兆判定")))
        value_text = html_escape(_short_value_label(row.get("割安判定")))
        valuation_text = f"銘柄判定（参考）：{value_text}"
        # 固定45銘柄の採用条件はストキャスのみ。企業価値判定は表示用。
        is_overvalued = False
        k_text = _mobile_number(row.get("%K"), 1)
        d_text = _mobile_number(row.get("%D"), 1)
        entry_detail = "55日高値更新＋MA200" if "55日高値" in str(row.get("ロジック", "")) else f"K {k_text} / D {d_text}"
        stop_hit = side == "sell" and "損切り" in str(row.get("売り理由", ""))
        trend_text = html_escape(_trade_trend_text(row.get("コード"), trend_by_code, side, stop_hit))

        if side == "sell":
            shares_text = _mobile_number(row.get("保有株数"), 0, "株")
            pnl_text = _mobile_number(row.get("損益率%"), 1, "%")
            reason_text = _mobile_text(row.get("売り理由"))
            signal_text = "損切り" if stop_hit else "安値割れ" if "20日安値割れ" in str(row.get("売り理由", "")) else "DC"
            action_text = "🚨 売り" if "損切り" in str(row.get("売り理由", "")) else "🔴 売り"
            card_class = "sell-card"
            quantity_text = f"保有 {shares_text}"
            detail_text = (
                f"<span class=\"trade-valuation\">{valuation_text}</span>"
                f"<span class=\"trade-trend\">{trend_text}</span>"
                f"<span>{signal_text}・損益 {pnl_text}</span><span class=\"trade-card-secondary\">現在 {price_text}</span>"
                f"<span class=\"trade-card-secondary\">{reason_text}</span>"
                f"<span class=\"trade-card-secondary\">{surge_text}</span>"
            )
        else:
            shares = int(max(safe_float(row.get("参考購入株数", row.get("参考S株数")), 0), 0))
            can_buy = "BUY" in str(row.get("買付可否", "")) and shares > 0
            pending = "株数未計算" in str(row.get("買付可否", ""))
            action_text = "🟡 買い候補" if pending else "🟢 買い" if can_buy else "🟠 買い候補"
            card_class = "skip-card" if is_overvalued else "buy-card" if can_buy or pending else "skip-card"
            quantity_text = "割高" if is_overvalued else "株数未計算" if pending else f"参考 {shares:,}株" if can_buy else _mobile_text(row.get("買付可否"), "株数0")
            order_text = _mobile_text(row.get("注文区分", "S株"))
            detail_text = (
                f"<span class=\"trade-valuation\">{valuation_text}</span>"
                f"<span class=\"trade-trend\">{trend_text}</span>"
                f"<span>{entry_detail}</span>"
                f"<span class=\"trade-card-secondary\">{order_text}</span>"
                f"<span class=\"trade-card-secondary\">現在 {price_text}</span>"
                f"<span class=\"trade-card-secondary\">{surge_text}</span>"
            )

        range_df = next((v for t, v in globals().get("data", {}).items() if code(t) == str(row.get("コード"))), None)
        rm = selection_range_metrics(range_df)
        range_text = html_escape(rm["値幅区分"])
        range_pct = _mobile_number(rm["ATR比率%"], 1, "%")
        detail_text = f"<span>{range_text}（ATR14 {range_pct}）</span>" + detail_text
        st.markdown(
            f"""
            <div class="trade-card {card_class}">
              <div class="trade-card-main">
                <span class="trade-action">{action_text}</span>
                <span class="trade-name">{code_text} {name_text}</span>
                <span class="trade-quantity">{quantity_text}</span>
              </div>
              <div class="trade-card-detail">{detail_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def prepare_admin_display(df, rename_columns=None):
    """管理者表だけを短い判定・統一桁数に整える。元データは変更しない。"""
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    out = df.copy()
    if "急騰予兆判定" in out.columns:
        out["急騰予兆判定"] = out["急騰予兆判定"].map(_short_surge_label)
    if "割安判定" in out.columns:
        out["割安判定"] = out["割安判定"].map(_short_value_label)

    zero_decimal = ["現在株価", "現在株価_価格", "AI参考価値", "取得単価", "株数", "保有株数"]
    one_decimal = [
        "参考価値上昇余地%", "企業価値スコア", "成長性スコア", "流動性スコア",
        "AI_TOP50スコア", "急騰予兆スコア", "RSI", "5日騰落率", "25日騰落率",
        "MA25乖離率", "MA25傾き", "%K", "%D", "RSI5", "BB下限", "損益率%",
    ]
    two_decimal = ["出来高倍率"]
    for col in zero_decimal:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(0)
    for col in one_decimal:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(1)
    for col in two_decimal:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    for col in ["Stoch_BUY", "RSI5_BUY", "BB20_BUY"]:
        if col in out.columns:
            out[col] = out[col].fillna(False).astype(bool).map({True: "🟢", False: "—"})
    if rename_columns:
        out = out.rename(columns=rename_columns)
    return out


def admin_table_height(df, maximum=520):
    """件数が少ない表は余白を減らし、多い表は表内スクロールにする。"""
    rows = len(df) if isinstance(df, pd.DataFrame) else 0
    return min(maximum, max(150, 36 * (rows + 1) + 8))


def current_holdings_rows(holdings, sell_candidates, signal_audit, value_df, price_data):
    """現在保有一覧へ売買判定と企業価値判定を付ける。"""
    sell_codes = (set(sell_candidates["コード"].astype(str))
                  if isinstance(sell_candidates, pd.DataFrame) and "コード" in sell_candidates else set())
    checked_codes = {str(row["コード"]) for row in signal_audit if "コード" in row}
    value_map = {}
    if isinstance(value_df, pd.DataFrame) and not value_df.empty and "コード" in value_df.columns:
        vm = value_df.copy()
        vm["コード"] = vm["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
        if "割安判定" in vm.columns:
            value_map = vm.drop_duplicates("コード").set_index("コード")["割安判定"].to_dict()

    rows = []
    for ticker, holding in holdings.items():
        ticker = str(ticker)
        verdict = "売り" if ticker in sell_codes else "保有継続" if ticker in checked_codes else ""
        value_label = value_map.get(ticker, "")
        rows.append({"銘柄コード": ticker, "銘柄名": holding.get("name") or name(ticker),
                     "保有株数": int(holding["shares"]), "判定": verdict,
                     "銘柄判定": _short_value_label(value_label),
                     **selection_range_metrics(price_data.get(ticker if ticker.endswith(".T") else ticker + ".T"))})
    return sorted(rows, key=lambda row: (row["判定"] != "売り", row["銘柄コード"]))


def render_current_holdings(rows):
    """現在保有銘柄の売買判定と割安・割高判定を表示する。"""
    st.subheader("現在保有中の銘柄")
    if not rows:
        show_stock_table(pd.DataFrame([{
            "銘柄コード": "", "銘柄名": "NO DATA", "保有株数": "", "判定": "", "銘柄判定": "",
        }]), use_container_width=True, hide_index=True, height=120)
        return
    sell_count = sum(row["判定"] == "売り" for row in rows)
    keep_count = sum(row["判定"] == "保有継続" for row in rows)
    pending_count = len(rows) - sell_count - keep_count
    summary = f"🔴 **売り {sell_count}件**　🟢 **保有継続 {keep_count}件**"
    if pending_count:
        summary += f"　⚪ **判定待ち {pending_count}件**"
    st.markdown(summary)
    st.markdown(
        '<div class="holding-grid holding-head"><span>銘柄判定</span><span>銘柄コード</span>'
        '<span>銘柄名</span><span>保有株数</span><span>売買判定</span></div>', unsafe_allow_html=True,
    )
    for row in sorted(rows, key=lambda r: valuation_display_rank(r.get("銘柄判定", ""))):
        verdict = row["判定"]
        tone = "sell" if verdict == "売り" else "keep" if verdict == "保有継続" else "pending"
        st.markdown(
            f'<div class="holding-grid holding-row {tone}">'
            f'<span class="holding-value">{html_escape(str(row.get("銘柄判定") or "—"))}</span>'
            f'<span>{html_escape(str(row["銘柄コード"]))}</span>'
            f'<span class="holding-name">{html_escape(str(row["銘柄名"]))}<br><small>{html_escape(str(row.get("値幅区分", "値幅未確認")))}</small></span>'
            f'<span class="holding-shares">{int(row["保有株数"]):,}</span>'
            f'<span class="holding-verdict">{html_escape(verdict or "—")}</span></div>',
            unsafe_allow_html=True,
        )
    if any(not row["判定"] for row in rows):
        st.caption("「—」は日足を取得できていない、または判定をまだ更新していない銘柄です。")
    st.caption("銘柄判定は企業価値の参考表示です。売買判定そのものは変更しません。")


def admin_judgement_table(df, key, maximum=520):
    """管理者表の判定をグループ化し、判定名で絞り込む。元データは変更しない。"""
    if not isinstance(df, pd.DataFrame):
        return
    choices = {
        "急騰": ["🚨 強い予兆", "🟠 急騰予兆", "🟡 変化", "⚪ 通常", "⚪ 対象外"],
        "割安": ["🟢 割安", "🟡 やや割安", "⚪ 適正", "🔴 割高", "⚠️ 分割確認", "⚠️ 算定確認", "— 未算定"],
    }
    available = {}
    for kind, labels in choices.items():
        col = kind if kind in df.columns else kind + ("予兆判定" if kind == "急騰" else "判定")
        if col in df.columns:
            available[kind] = col
    if available:
        options = ["急騰 → 割安", "割安 → 急騰", "急騰のみ", "割安のみ", "元の順番"]
        if len(available) == 1:
            only = next(iter(available))
            options = [only + "順", "元の順番"]
        order = st.session_state.get(key + "_saved_order", options[0])
        filters = {kind: st.session_state.get(key + "_saved_filter_" + kind, []) for kind in available}
        if st.checkbox("🔽 判定で並び替え・絞り込み", key=key + "_show_controls"):
            order = st.selectbox("並び順", options,
                                 index=options.index(order) if order in options else 0,
                                 key=key + "_order")
            st.session_state[key + "_saved_order"] = order
            for kind, col in available.items():
                present = set(df[col].fillna("").astype(str))
                labels = [label for label in choices[kind] if label in present]
                labels += sorted(present.difference(choices[kind]).difference({""}))
                filters[kind] = st.multiselect(
                    kind + "の判定を抽出（未選択＝すべて）", labels,
                    default=[label for label in filters[kind] if label in labels],
                    key=key + "_filter_" + kind,
                )
                st.session_state[key + "_saved_filter_" + kind] = filters[kind]
        for kind, selected in filters.items():
            if selected:
                df = df.loc[df[available[kind]].fillna("").astype(str).isin(selected)]
        if order != "元の順番":
            priority = (["割安", "急騰"] if order.startswith("割安") else ["急騰", "割安"])
            if "のみ" in order or "順" in order:
                priority = priority[:1]
            priority = [kind for kind in priority if kind in available]
            sort_keys = []
            for kind in priority:
                # カテゴリーの順番を明示し、同じ判定名の行を連続させる。
                ranks = {label: i for i, label in enumerate(choices[kind])}
                sort_keys.append(df[available[kind]].fillna("").astype(str).map(ranks).fillna(len(ranks)).astype(int))
            if sort_keys:
                sort_frame = pd.concat(sort_keys, axis=1)
                sort_frame.columns = range(len(sort_keys))
                df = df.iloc[sort_frame.reset_index(drop=True).sort_values(
                    list(sort_frame.columns), kind="stable"
                ).index.to_numpy()]
        st.caption(f"{order}・{len(df)}件表示")
    show_stock_table(df, use_container_width=True, hide_index=True,
                 height=admin_table_height(df, maximum))


st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem; padding-bottom: 2rem;}
    .trade-card {
        border: 1px solid rgba(128,128,128,.25);
        border-left: 6px solid #808080;
        border-radius: 12px;
        padding: 10px 11px 9px 11px;
        margin: 0 0 9px 0;
        background: rgba(128,128,128,.055);
        box-shadow: 0 1px 3px rgba(0,0,0,.06);
    }
    .trade-card.buy-card {border-left-color: #18a058; background: rgba(24,160,88,.075);}
    .trade-card.sell-card {border-left-color: #e5484d; background: rgba(229,72,77,.075);}
    .trade-card.skip-card {border-left-color: #d49b16; background: rgba(212,155,22,.07);}
    .holding-grid {display: grid; grid-template-columns: minmax(3.4rem, .72fr) minmax(4.5rem, 1.55fr)
        minmax(3rem, .65fr) minmax(4.1rem, .9fr) minmax(4.8rem, 1.1fr); align-items: center; gap: 5px;
        padding: 8px 7px; font-size: .78rem; line-height: 1.3;}
    .holding-head {font-size: .68rem; font-weight: 700; padding-bottom: 3px;}
    .holding-head span {white-space: nowrap;}
    .holding-row {border-radius: 7px; margin-bottom: 4px; border-left: 4px solid #888;}
    .holding-row.sell {background: rgba(229,72,77,.20); border-left-color: #e5484d;}
    .holding-row.keep {background: rgba(24,160,88,.17); border-left-color: #18a058;}
    .holding-row.pending {background: rgba(128,128,128,.12);}
    .holding-name {overflow-wrap: anywhere;}
    .holding-shares {text-align: right; white-space: nowrap;}
    .holding-verdict {font-weight: 700; white-space: nowrap;}
    .holding-value {font-weight: 750; white-space: nowrap;}
    .trade-card-main {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        line-height: 1.25;
    }
    .trade-action {font-size: 1.02rem; font-weight: 800; white-space: nowrap;}
    .trade-name {
        min-width: 0;
        flex: 1;
        font-size: 1rem;
        font-weight: 750;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .trade-quantity {
        font-size: .96rem;
        font-weight: 800;
        white-space: nowrap;
        padding: 2px 7px;
        border-radius: 7px;
        background: rgba(128,128,128,.12);
    }
    .trade-card-detail {
        display: flex;
        flex-wrap: wrap;
        column-gap: 9px;
        row-gap: 2px;
        margin-top: 7px;
        padding-top: 6px;
        border-top: 1px solid rgba(128,128,128,.18);
        font-size: .76rem;
        line-height: 1.25;
        opacity: .90;
    }
    .trade-card-detail span {white-space: nowrap;}
    .trade-valuation {font-weight: 800;}
    .trade-trend {font-weight: 750;}
    div[data-testid="stTabs"] button p {font-size: .88rem; font-weight: 700;}
    div[data-testid="stDataFrame"] {border-radius: 9px; overflow: hidden;}
    @media (max-width: 640px) {
        .block-container {padding-left: .75rem; padding-right: .75rem; padding-top: .8rem;}
        h1 {font-size: 1.55rem !important;}
        h2 {font-size: 1.28rem !important;}
        h3 {font-size: 1.12rem !important;}
        .trade-card {padding: 9px 9px 8px 9px; border-radius: 10px; margin-bottom: 7px;}
        .trade-card-main {gap: 6px;}
        .trade-action {font-size: .94rem;}
        .trade-card.skip-card .trade-action {white-space: normal; max-width: 48%; line-height: 1.1;}
        .trade-name {font-size: .91rem;}
        .trade-quantity {font-size: .86rem; padding: 2px 5px;}
        .trade-card-detail {font-size: .69rem; column-gap: 7px; margin-top: 6px; padding-top: 5px; flex-wrap: wrap;}
        .trade-card-detail .trade-valuation {white-space: normal;}
        .trade-card-secondary {display: none;}
        div[data-testid="stTabs"] button {padding-left: .48rem; padding-right: .48rem;}
        div[data-testid="stTabs"] button p {font-size: .72rem; white-space: nowrap;}
        div[data-testid="stDataFrame"] {font-size: .70rem;}
        div[data-testid="stMetric"] {padding: .2rem;}
        div[data-testid="stMetricValue"] {font-size: 1.1rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.title("📈 日本株 AI投資アシスタント Ver.17.24.47")
st.caption(f"{VERSION} / BUILD: {BUILD}")
st.caption("売買条件は管理者設定の売買ロジックで選択。保有銘柄数・1銘柄購入額の固定上限なし。終値7%損切りを適用します。")
st.caption("45銘柄外の保有銘柄も、DCまたは終値で−7%の売りを判定します。")
st.caption("監視対象・購入予算・注文方式は設定欄で選択。株数上限解除・対象拡大の成績は比較検証で確認してください。")

main_tab, single_tab, bottom_tab, video_tab, flow_tab, multibagger_tab, admin_tab = st.tabs(["今日の売買", "1銘柄・利益1万円研究", "底打ち・上昇転換研究", "動画式AI投資研究", "資金流入初期研究", "大化け候補", "管理者"])

# ------------------------------------------------------------
# 下部の入力値はウィジェットの再実行時に取得する。
# ------------------------------------------------------------
trade_files = normalize_sbi_uploads(st.session_state.get("sbi_execution_csvs_v177", []))
buying_power_input = st.session_state.get("v172431_buying_power", None)
buying_power = int(buying_power_input or 0)
st.caption("① ページを開くと自動判定 → ② 買い候補を確認 → ③ 下部でCSV・余力を入力し参考株数を計算")
# 約定履歴 → 保有復元
confirmed = {}
sbi_trades_df = pd.DataFrame()
sbi_warning_df = pd.DataFrame()
parsed_parts, parse_errors = [], []
if trade_files:
    for f in trade_files:
        try:
            part, _enc = parse_sbi_execution_csv(f)
            if not part.empty:
                parsed_parts.append(part)
        except Exception as e:
            parse_errors.append(f"{sbi_upload_name(f)}: {e}")
    if parsed_parts:
        sbi_trades_df = pd.concat(parsed_parts, ignore_index=True)
        sbi_trades_df = sbi_trades_df.drop_duplicates("_dedupe_key", keep="first").copy()
        holdings_auto_df, _lots, _audit, sbi_warning_df = rebuild_holdings_from_trades(sbi_trades_df)
        if not holdings_auto_df.empty:
            confirmed = {
                str(r["code"]): {
                    "shares": int(r["shares"]), "avg_price": float(r["avg_price"]),
                    "name": str(r.get("name", "")),
                    "source": str(r["source"]), "account_types": str(r.get("account_types", "")),
                }
                for _, r in holdings_auto_df.iterrows()
            }
    if parse_errors:
        st.warning("一部CSVを読めませんでした：\n- " + "\n- ".join(parse_errors))
all_held_codes = list(confirmed.keys())
short_confirmed = confirmed
held_codes = all_held_codes
short_buying_power = int(buying_power)
# 余力入力のみで参考株数を算出。CSVの有無・警告は保有照合の注意として別表示。
order_inputs_ready = buying_power_input is not None
holdings_verified = bool(not sbi_trades_df.empty and not parse_errors and sbi_warning_df.empty) if trade_files else False
if not sbi_warning_df.empty:
    st.error("履歴不足の可能性がある銘柄があります。警告対象は安全のため保有復元から除外しています。")

# ------------------------------------------------------------
# 管理者設定は目立たせない
# ------------------------------------------------------------
for _key, _fixed in {
    "v177_sl": 7.0, "v177_maxpos": 3,
    "v177_reserve": 0, "v177_daily": 100, "v177_risk": 1.0,
    "v177_buffer": 5.0, "v1716_churn_filter": False,
    "v1717_quality_mode": False,
}.items():
    st.session_state[_key] = _fixed
with st.expander("⚙ 購入額・注文方式", expanded=False):
    live_universe_mode = st.selectbox("新規買いの監視対象", ["TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン", "TOPIX＋日経225＋グロースからAI選定TOP50", "固定45銘柄", "対象拡大（売買代金上位）"], index=0, key="v172440_universe")
    signal_first = live_universe_mode == "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン"
    live_trade_method = st.selectbox("売買ロジック", ["現行Stoch GC/DC", "55日高値更新＋MA200（試験採用）"], key="v17247_method")
    replacement_live = live_trade_method == "55日高値更新＋MA200（試験採用）"
    st.caption("試験採用：買い＝前日までの55日高値を終値で更新＋終値>MA200、売り＝前日までの20日安値を終値で割る、または取得単価から終値7%損切り。候補は63日騰落率順。Stochは売買・順位に使いません。")
    if replacement_live:
        st.warning("拡大344銘柄の過去比較では有望ですが、TOPIX全体と財務除外込みの成績は未検証です。保有銘柄の売り条件もこの方式へ切り替わります。")
    block_unrated = True
    st.caption("国内財務の判定不能は新規買いを見送ります。")
    min_turnover = st.number_input("買い候補の20日平均売買代金下限（円・最低1億円）", 100000000, 10000000000, 100000000, 10000000, key="v172416_liquidity", disabled=not signal_first)
    filter_downtrend = False  # 下降トレンドによる新規買い除外を解除
    filter_churn = not replacement_live
    st.caption("全監視対象で割高・赤字予想・財務未確認・日足未確定を新規買いから除外します。Stochでは短期クロス多発も除外します。下降トレンドは新規買い除外に使用しません。成績改善は未検証です。")
    st.caption("値幅：ATR14/終値が3%以上＝大、1.5%以上＝中、それ未満＝小。大のみ新規対象。安値切上げ：直近3つの10営業日安値が順に上昇し、終値が25日平均より上。20日平均売買代金は最低1億円。国内財務の判定不能は除外。")
    cap_mode = "上限なし（買付余力を候補へ均等配分）"
    purchase_cap = None
    lot_cap = None
    st.caption("1銘柄の購入額・通常注文予算の固定上限は解除済みです。資産の3分の1による制限も適用しません。")
    execution_mode = st.selectbox("実行する注文方式", ["自動（通常注文優先・不足はS株）", "S株のみ（1〜99株）", "100株単位（通常注文）"], key="v1724_order")
    ifd_take_profit = st.slider("参考IFDOCOの利確率（%）", 3.0, 30.0, 15.0, 0.5, key="v1724_tp")
    st.caption("保有数上限なし。買い候補TOP3の件数で余力を均等配分します。注文単位・価格余裕・値幅上限による注文拘束額を考慮するため、未使用の余力が残る場合があります。自動は余力内で100株買えれば通常注文、買えなければS株（最大99株）。参考IFDOCO表は代替案なので、買いカードと二重発注しないでください。")
    st.caption("IFDOCOは参考案です。日中の逆指値と固定利確は、終値損切り・Stoch売りとは異なります。利確15%は検証結果ではなく初期値です。")
with st.expander("⚙ 管理者設定", expanded=False):
    a1, a2, a3, a4 = st.columns(4)
    current_assets = a1.number_input("現在資産（円）", 10000, 200000000, 600000, 10000, key="v177_assets")
    maxbuy = "上限なし（買付余力内）"
    a2.metric("1銘柄の購入予算", "上限なし")
    sl = a3.slider("損切りセンサー（%）", 3.0, 15.0, 7.0, .5, key="v177_sl", disabled=True)
    maxpos = None
    a4.metric("最大保有銘柄数", "上限なし")
    b1, b2, b3, b4 = st.columns(4)
    reserve_pct = b1.slider("現金温存率（%）", 0, 80, 0, 5, key="v177_reserve", disabled=True)
    daily_deploy_pct = b2.slider("1日使用上限（%）", 10, 100, 100, 5, key="v177_daily", disabled=True)
    risk_per_trade_pct = b3.slider("1銘柄許容損失（資産比%）", 0.25, 3.0, 1.0, 0.25, key="v177_risk", disabled=True)
    price_buffer_pct = b4.slider("価格上振れバッファ（%）", 0.0, 10.0, 5.0, .5, key="v177_buffer", disabled=True)
    c1, c2 = st.columns(2)
    universe_size = c1.number_input("日本株母集団（売買代金上位）", 200, 500, 350, 25, key="v177_universe_n", disabled=True)
    fundamental_pool_size = c2.number_input("詳細企業価値評価へ進める一次選抜数", 60, 150, 90, 10, key="v177_fund_pool", disabled=live_universe_mode not in ("TOPIX＋日経225＋グロースからAI選定TOP50", "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン"))
    st.caption("標準選定：TOPIX＋グロース（旧マザーズ相当の現行市場）→国内売買代金上位100→値動きが大きい＋買いサイン→流動性→安値切上げ→国内財務で割安／やや割安。構成一覧には基準日の差があります。")
    st.caption("Stochの買い・売りと終値7%損切りは維持。対象拡大は未検証のため、比較結果を確認してから選択してください。")
    churn_filter = st.checkbox("短期クロス反転が多い銘柄を新規買いTOP50から除外", value=False, key="v1716_churn_filter", disabled=True)
    st.caption("今回の主ロジックでは短期反転除外を使用しません。")
    quality_mode = st.checkbox("トレンド＋振れ幅フィルターを実売買判定に使用（検証後に切替）",
                               value=False, key="v1717_quality_mode", disabled=True)
    st.caption("トレンド＋振れ幅フィルターは主ロジックでは使用しません。")

# 既存のStreamlitセッションに過去の設定が残っていても検証済み条件を固定する。
sl, churn_filter, quality_mode = 7.0, False, False

manual_run_clicked = st.button("▶ 今日の判定を更新", type="primary", use_container_width=True, key="v177_run")
# ページ初回表示（または日本時間の日付変更）のみ自動実行。
# 入力ウィジェットによる再実行では価格・財務を再取得しない。
_auto_day = str(tokyo_now().date())
auto_run_clicked = st.session_state.get("v172433_auto_run_day") != _auto_day
run_clicked = bool(manual_run_clicked or auto_run_clicked)
if run_clicked:
    st.session_state["v172433_auto_run_day"] = _auto_day

if not run_clicked:
    previous_data = st.session_state.get("v177_data", {})
    if previous_data and any(isinstance(d, pd.DataFrame) and d.attrs.get("gfs_history_version") != 2 for d in previous_data.values()):
        st.session_state["v172412_history_refresh_required"] = True
    previous_universe = st.session_state.get("v177_universe_df", pd.DataFrame())
    previous_codes = (set(previous_universe["コード"].astype(str))
                      if isinstance(previous_universe, pd.DataFrame) and
                      "コード" in previous_universe else set())
    if (st.session_state.get("v1724_loaded_mode") != live_universe_mode or
            (live_universe_mode == "固定45銘柄" and previous_codes != set(VERIFIED_45_CODES))):
        for key in ("v177_universe_df", "v177_data", "v177_daily_bar_status",
                    "v177_value_top50", "v1716_churn_audit", "v177_run_error",
                    "v172419_finance_audit", "v172419_daily_diagnostics"):
            st.session_state.pop(key, None)

if run_clicked:
    _update_started = time.perf_counter()
    _update_times = {}
    st.session_state.pop("v172412_history_refresh_required", None)
    universe_error = ""
    try:
        universe_df = (discover_domestic_topix_turnover100() if live_universe_mode == "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン" else discover_topix_nikkei225_stock_universe() if live_universe_mode in ("TOPIX＋日経225＋グロースからAI選定TOP50", "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン")
                       else pd.DataFrame([
                           {"コード": c, "銘柄名": STOCK_NAMES[c], "ticker": c + ".T"}
                           for c in VERIFIED_45_CODES
                       ]) if live_universe_mode == "固定45銘柄"
                       else discover_japan_stock_universe(int(universe_size)))
    except Exception as exc:
        universe_df = pd.DataFrame()
        universe_error = str(exc)
    st.session_state["v1724_loaded_mode"] = live_universe_mode
    if universe_df.empty:
        st.error("対象銘柄の取得に失敗しました。保有銘柄のみ確認します。" + universe_error)
    else:
        STOCK_NAMES.update(dict(zip(universe_df["コード"].astype(str), universe_df["銘柄名"].astype(str))))
        if universe_df.attrs.get("universe_notice"):
            st.warning(universe_df.attrs["universe_notice"])
        if "対象指数" in universe_df.columns:
            index_tags = universe_df["対象指数"].astype(str)
            st.caption(f"監視対象：TOPIX {index_tags.str.contains('TOPIX').sum()}銘柄／日経225 {index_tags.str.contains('日経225').sum()}銘柄／グロース {index_tags.str.contains('グロース').sum()}銘柄／重複除外後 {len(universe_df)}銘柄")
    st.session_state["v177_universe_df"] = universe_df
    selected_codes = universe_df["コード"].astype(str).tolist() if not universe_df.empty else []
    all_codes = list(dict.fromkeys(selected_codes + [str(c) for c in all_held_codes]))
    all_tickers = tuple(tickers(",".join(all_codes)))
    _stage_started = time.perf_counter()
    with st.spinner(f"{len(all_tickers)}銘柄の無料国内財務を取得・確認中…"):
        st.session_state["v172419_finance_audit"] = batch_domestic_finance_audit(all_tickers, (str(tokyo_now().date()), tokyo_now().hour >= 16))
    _update_times["財務確認"] = round(time.perf_counter() - _stage_started, 2)
    _fa = st.session_state["v172419_finance_audit"]
    if not _fa.empty:
        st.caption(f"財務の必要指標確認済 {int(_fa['財務取得状態'].eq('取得済').sum())} / {len(_fa)}銘柄。日足取得とは独立して確認しました。")
    _stage_started = time.perf_counter()
    with st.spinner(f"{len(all_tickers)}銘柄の国内日足を取得中…"):
        if signal_first:
            data, _daily_diagnostics = batch_domestic_stock_data(all_tickers, (str(tokyo_now().date()), tokyo_now().hour >= 16), 300 if replacement_live else 150)
        else:
            data = batch_stock_data_light(all_tickers, months=15)
            _daily_diagnostics = pd.DataFrame()
        st.session_state["v172419_daily_diagnostics"] = _daily_diagnostics
    _update_times["日足確認"] = round(time.perf_counter() - _stage_started, 2)
    _stage_started = time.perf_counter()
    st.session_state["v177_data"] = data
    st.session_state["v177_daily_bar_status"] = build_daily_bar_status(data)
    mother_data = {t: d for t, d in data.items() if code(t) in selected_codes}
    if not mother_data:
        # 固定45の取得失敗でも保有株の売り判定は止めない。
        st.session_state["v177_value_top50"] = pd.DataFrame()
        st.session_state["v1716_churn_audit"] = pd.DataFrame()
        st.session_state["v177_run_error"] = ("日足を取得できないため売買判定を停止しています。財務取得結果と日足の失敗理由は管理者の企業価値タブで確認できます。"
                                               if not data else "")
    else:
        # 企業価値タブは参考表示として維持。売買対象は順位や評価で変えない。
        eligible_count = sum(isinstance(d, pd.DataFrame) and len(d) >= 60
                             for d in mother_data.values())
        value_top50_df, _ = (build_value_ai_top50(
            mother_data, max_rows=min(50, eligible_count) if live_universe_mode in ("TOPIX＋日経225＋グロースからAI選定TOP50", "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン") else eligible_count,
            fundamental_pool_size=int(fundamental_pool_size) if live_universe_mode in ("TOPIX＋日経225＋グロースからAI選定TOP50", "TOPIX＋グロース売買代金TOP100・値動きが大きい＋買いサイン") else eligible_count,
            churn_filter=False,
        ) if eligible_count else (pd.DataFrame(), pd.DataFrame()))
        st.session_state["v177_value_top50"] = value_top50_df
        st.session_state["v1716_churn_audit"] = pd.DataFrame()
        st.session_state["v177_generated_at"] = tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST")
        st.session_state["v177_run_error"] = ("TOPIX＋日経225＋グロースから50銘柄を選定できませんでした。新規買いを停止し、保有銘柄の売り判定のみ行います。"
            if live_universe_mode == "TOPIX＋日経225＋グロースからAI選定TOP50" and value_top50_df.empty else "")
    _update_times["一次選抜・参考評価"] = round(time.perf_counter() - _stage_started, 2)
    _update_times["取得〜参考評価合計"] = round(time.perf_counter() - _update_started, 2)
    st.session_state["v172429_update_times"] = _update_times

def sync_post_input_holdings(state, holdings, signal_first, replacement_live, now, clock):
    """CSV後入力で不足した保有株だけ補完。成功済みデータは再取得しない。"""
    if not holdings:
        return []
    day_key = (str(now.date()), now.hour >= 16)
    attempted = dict(state.get("v172432_holding_attempts", {}))
    finance = state.get("v172419_finance_audit", pd.DataFrame())
    prices = dict(state.get("v177_data", {}))
    finance_status = (finance.drop_duplicates("コード", keep="last").set_index("コード")["財務取得状態"].to_dict()
                      if isinstance(finance, pd.DataFrame) and {"コード", "財務取得状態"}.issubset(finance.columns) else {})
    finance_status = {str(c): status for c, status in finance_status.items()}
    for c, holding in holdings.items():
        if holding.get("name"):
            STOCK_NAMES[str(c)] = str(holding["name"])
    required_bars = 200 if replacement_live else 60
    def due(stage, c):
        return clock - attempted.get((day_key, stage, c), -float("inf")) >= 900
    finance_codes = [str(c) for c in holdings
                     if finance_status.get(str(c)) != "取得済" and due("finance", str(c))]
    price_codes = [str(c) for c in holdings
                   if (str(c) + ".T" not in prices or prices[str(c) + ".T"] is None
                       or len(prices[str(c) + ".T"]) < required_bars)
                   and due("price", str(c))]
    errors = []
    def merge_rows(old, extra):
        if not isinstance(extra, pd.DataFrame) or extra.empty:
            return old
        combined = pd.concat([old, extra], ignore_index=True)
        if "コード" in combined:
            combined["コード"] = combined["コード"].astype(str)
            combined = combined.drop_duplicates("コード", keep="last")
        return combined.reset_index(drop=True)
    if finance_codes:
        for c in finance_codes:
            attempted[(day_key, "finance", c)] = clock
        try:
            extra = batch_domestic_finance_audit(tuple(c + ".T" for c in finance_codes), day_key)
            state["v172419_finance_audit"] = merge_rows(finance, extra)
        except Exception as exc:
            errors.append("保有銘柄の追加財務取得に失敗：" + str(exc)[:200])
    if price_codes:
        for c in price_codes:
            attempted[(day_key, "price", c)] = clock
        try:
            symbols = tuple(c + ".T" for c in price_codes)
            if signal_first:
                extra_prices, extra_diagnostics = batch_domestic_stock_data(symbols, day_key, 300 if replacement_live else 150)
                state["v172419_daily_diagnostics"] = merge_rows(
                    state.get("v172419_daily_diagnostics", pd.DataFrame()), extra_diagnostics)
            else:
                extra_prices = batch_stock_data_light(symbols, months=15)
            prices.update({t: frame for t, frame in extra_prices.items()
                           if isinstance(frame, pd.DataFrame) and not frame.empty})
            state["v177_data"] = prices
            state["v177_daily_bar_status"] = build_daily_bar_status(prices)
        except Exception as exc:
            errors.append("保有銘柄の追加日足取得に失敗：" + str(exc)[:200])
    state["v172432_holding_attempts"] = {k: v for k, v in attempted.items() if k[0] == day_key}
    current_finance = state.get("v172419_finance_audit", pd.DataFrame())
    checked = (set(current_finance.loc[current_finance["財務取得状態"].eq("取得済"), "コード"].astype(str))
               if {"コード", "財務取得状態"}.issubset(current_finance.columns) else set())
    unresolved = set(map(str, holdings)) - checked
    if unresolved:
        errors.append("保有銘柄の財務未確認：" + "、".join(sorted(unresolved)) +
                      "。取得不足のまま割安・割高は断定しません。再試行は15分間隔です。")
    return errors


# 全体更新を繰り返さず、後入力で不足した保有銘柄を判定表の作成前に補完する。
if not run_clicked and short_confirmed:
    with st.spinner("後入力した保有銘柄の財務・日足を確認中…"):
        for _message in sync_post_input_holdings(st.session_state, short_confirmed,
                                                signal_first, replacement_live,
                                                tokyo_now(), time.time()):
            st.warning(_message)

if st.session_state.get("v172429_update_times"):
    with st.expander("更新処理の所要時間", expanded=False):
        show_stock_table(pd.DataFrame([{"処理": k, "秒": v} for k, v in st.session_state["v172429_update_times"].items()]), hide_index=True)
        st.caption("通信と一次選抜の実測値です。この後の売買表作成・画面描画時間は含みません。短時間の再更新は保存結果を再利用します。")

universe_df = st.session_state.get("v177_universe_df", pd.DataFrame())
if isinstance(universe_df, pd.DataFrame) and not universe_df.empty:
    STOCK_NAMES.update(dict(zip(universe_df["コード"].astype(str), universe_df["銘柄名"].astype(str))))
data = st.session_state.get("v177_data", {})
value_top50_df = st.session_state.get("v177_value_top50", pd.DataFrame())
churn_audit_df = st.session_state.get("v1716_churn_audit", pd.DataFrame())
run_error = st.session_state.get("v177_run_error", "")
daily_bar_status_df = st.session_state.get("v177_daily_bar_status", pd.DataFrame())
finance_acquisition_df = st.session_state.get("v172419_finance_audit", pd.DataFrame())
daily_acquisition_df = st.session_state.get("v172419_daily_diagnostics", pd.DataFrame())
_scan_complete_for_buy = not signal_first
if not daily_acquisition_df.empty:
    _required_codes = set(universe_df["コード"].astype(str)) if "コード" in universe_df else set()
    _confirmed_codes = {code(t) for t,f in data.items() if len(f) >= (200 if replacement_live else 60) and f.attrs.get("daily_bar_status") == "🟢 当日確定"}
    _missing_scan = _required_codes - _confirmed_codes
    _scan_complete_for_buy = bool(_required_codes) and not _missing_scan
    _unconfirmed_holdings = set(map(str, all_held_codes)) - _confirmed_codes
    if _unconfirmed_holdings:
        st.warning("保有銘柄の日足未確認：" + "、".join(sorted(_unconfirmed_holdings)) + "。該当銘柄の売買判定は保留します。")
    if _missing_scan:
        st.warning(f"銘柄選定は未完了です：TOP{len(_required_codes)}中{len(_required_codes & _confirmed_codes)}銘柄のみ日足確認済。未確認{len(_missing_scan)}銘柄があるため新規買いを停止します。買い候補0件は『全体に買いなし』を意味しません。")


# ------------------------------------------------------------
# 固定45銘柄の未保有株の新規BUYと、全保有銘柄のSELLを監視。
# ------------------------------------------------------------
buy_view = pd.DataFrame()
sell_view = pd.DataFrame()
buy_signal_rows, sell_signal_rows = [], []
signal_audit_rows = []
true_top50_codes = (set(value_top50_df["コード"].astype(str))
                    if isinstance(value_top50_df, pd.DataFrame) and "コード" in value_top50_df else set()) if live_universe_mode == "TOPIX＋日経225＋グロースからAI選定TOP50" else (
                    set(universe_df["コード"].astype(str))
                    if isinstance(universe_df, pd.DataFrame) and "コード" in universe_df else set())
signal_first_audit_df = pd.DataFrame()
signal_value_df = pd.DataFrame()
if signal_first:
    mother_codes = set(universe_df["コード"].astype(str)) if "コード" in universe_df else set()
    signal_data = {t: df for t, df in data.items() if code(t) in mother_codes}
    signal_first_audit_df = signal_first_candidates(signal_data, all_held_codes, min_turnover, filter_downtrend, filter_churn, replacement_live)
    pass_tickers = set(signal_first_audit_df.loc[signal_first_audit_df["抽出除外理由"].eq(""), "ticker"])
    candidate_data = {t: df for t, df in signal_data.items() if t in pass_tickers}
    if candidate_data:
        signal_value_df, _ = build_value_ai_top50(candidate_data, max_rows=len(candidate_data), fundamental_pool_size=len(candidate_data), churn_filter=False)
        signal_value_df["候補スコア"] = signal_value_df["コード"].map({code(t): selection_range_metrics(df)["ATR比率%"] for t, df in candidate_data.items()})
        if replacement_live and not signal_first:
            priority = {code(t): -safe_float(public_replacement_frame(df, "高値更新55＋MA200・安値割れ20").iloc[-1].get("buy_priority"), 0) * 100 for t, df in candidate_data.items()}
            signal_value_df["候補スコア"] = signal_value_df["コード"].map(priority).fillna(0)
        signal_value_df = signal_value_df.sort_values(["候補スコア", "コード"], ascending=[False, True]).reset_index(drop=True)
        signal_value_df["順位"] = np.arange(1, len(signal_value_df) + 1)
    true_top50_codes = mother_codes
# BUYを確定する前に共通評価マスターを構築。TOP50外も同じ判定を使用。
# 生の買いシグナルだけ追加評価し、描画時には再取得しない。
_signal_frames = {}
_raw_buy_targets = []
for _t, _df in data.items():
    _c = code(_t)
    if _c not in true_top50_codes and _c not in set(all_held_codes):
        continue
    _sx = (public_replacement_frame(_df, "高値更新55＋MA200・安値割れ20")
           if replacement_live else stoch_quality_frame(_df, sl))
    _signal_frames[_t] = _sx
    if not _sx.empty and bool(_sx.iloc[-1].get("BUY_研究" if replacement_live else "BUY_現行", False)):
        _raw_buy_targets.append({"コード": _c})
valuation_master_df = build_valuation_master(
    [value_top50_df, signal_value_df], short_confirmed, data,
    [pd.DataFrame(_raw_buy_targets)])
value_judgement_by_code = (valuation_master_df.set_index("コード")["割安判定"].to_dict()
                          if not valuation_master_df.empty else {})
signal_meta_by_code = signal_value_df.set_index("コード").to_dict("index") if not signal_value_df.empty else {}
extract_reason_by_code = signal_first_audit_df.set_index("コード")["抽出除外理由"].to_dict() if not signal_first_audit_df.empty else {}
value_unrated_count = 0  # 全監視モードで未算定の新規買いを見送る。
held_code_set = set(map(str, held_codes))

if data and (true_top50_codes or held_code_set):
    for t, df0 in data.items():
        c = code(t)
        if c in held_code_set and df0.attrs.get("daily_bar_status") != "🟢 当日確定":
            continue  # 古い初期履歴で保有銘柄の売り指示を出さない。
        # 新規BUYは固定45の未保有株、保有銘柄は45銘柄外もSELLを判定する。
        if c not in true_top50_codes and c not in held_code_set:
            continue
        sx = _signal_frames.get(t, pd.DataFrame())
        if sx.empty:
            continue
        r = sx.iloc[-1]
        sk, sd, px = safe_float(r.get("STOCH_K")), safe_float(r.get("STOCH_D")), safe_float(r.get("Close"))
        if not (np.isfinite(px) and px > 0):
            continue
        buy_old = bool(r.get("BUY_研究" if replacement_live else "BUY_現行", False))
        buy_new = bool(r.get("BUY_改善", False))
        sell_old = bool(r.get("SELL_研究" if replacement_live else "SELL_現行", False))
        sell_new = bool(r.get("SELL_改善", False))
        buy_signal_hit = buy_old
        value_judgement = value_judgement_by_code.get(c, "— 未算定")
        overvalued = "割高" in str(value_judgement)
        new_buy_target = c in true_top50_codes and c not in set(all_held_codes)
        h = short_confirmed.get(c, {})
        shares = int(safe_float(h.get("shares", 0)) or 0)
        avg = safe_float(h.get("avg_price", np.nan))
        pnl_pct = ((px / avg - 1.0) * 100.0) if np.isfinite(avg) and avg > 0 else np.nan
        dc_hit = sell_old and (replacement_live or np.isfinite(sk))
        stop_hit = np.isfinite(pnl_pct) and pnl_pct <= -7.0
        final_buy_hit = bool(new_buy_target and buy_signal_hit)
        buy_skip_reason = ""
        if new_buy_target and buy_signal_hit:
            _churn = stoch_churn_diagnostic(df0).get("新規買い除外理由", "") if filter_churn else ""
            _reasons = common_new_buy_reasons(
                value_judgement, df0.attrs.get("daily_bar_status"),
                r.get("下降確認", np.nan), _churn, replacement_live)
            if any("財務評価" in reason for reason in _reasons):
                value_unrated_count += 1
            buy_skip_reason = "／".join(_reasons)
        if signal_first and new_buy_target and buy_signal_hit and not buy_skip_reason:
            buy_skip_reason = extract_reason_by_code.get(c, "日足不足または抽出対象外")
            meta = signal_meta_by_code.get(c, {})
            if not buy_skip_reason and overvalued:
                buy_skip_reason = "割高判定のため新規買い除外"
            if not buy_skip_reason and block_unrated and not bool(meta.get("企業価値算定済", False)):
                buy_skip_reason = "企業価値判定不能のため新規買い見送り"
                value_unrated_count += 1
            if not buy_skip_reason and (not bool(meta.get("企業価値算定済", False)) or "割安" not in str(value_judgement)):
                buy_skip_reason = "国内財務による割安・やや割安条件未達"
            if not buy_skip_reason and not _scan_complete_for_buy:
                buy_skip_reason = "TOP100の日足確認が未完了のため新規買い停止"
        final_buy_hit = final_buy_hit and not buy_skip_reason
        if c in true_top50_codes or c in held_code_set:
            signal_audit_rows.append({"コード": c, "銘柄名": name(t), "監視対象": "保有" if c in held_code_set else ("TOPIX＋日経225＋グロース全体" if signal_first else "監視対象"),
                                      "現行買い": buy_old, "改善買い": buy_new,
                                      "採用方式の買いシグナル": bool(buy_signal_hit),
                                      "銘柄判定": value_judgement,
                                      "割高除外": bool(new_buy_target and buy_signal_hit and overvalued),
                                      "最終買い": final_buy_hit,
                                      "買い見送り理由": buy_skip_reason,
                                      "現行売り": sell_old if c in held_code_set else False,
                                      "改善売り": sell_new if c in held_code_set else False,
                                      "上昇トレンド": bool(r.get("上昇トレンド", False)),
                                      "下降確認": bool(r.get("下降確認", False)),
                                      "帯下限": safe_float(r.get("帯下限")), "帯上限": safe_float(r.get("帯上限")),
                                      "%K": sk, "%D": sd})
        if final_buy_hit:
            buy_signal_rows.append({
                "コード": c, "銘柄名": name(t), "総合AIスコア": safe_float(signal_meta_by_code.get(c, {}).get("候補スコア"), -safe_float(r.get("buy_priority"), 0) * 100 if replacement_live else 100.0-float(sk)),
                "ロジック":live_trade_method,
                "63日騰落率%":-safe_float(r.get("buy_priority"), 0) * 100 if replacement_live else np.nan,
                **({"候補スコア": safe_float(signal_meta_by_code.get(c, {}).get("候補スコア"), 0)} if signal_first else {}),
                "現在株価": float(px), "%K": float(sk), "%D": float(sd),
                "条件": live_universe_mode + "の新規買い",
            })
        if c in held_code_set:
            if dc_hit or stop_hit:
                reasons=[]
                if stop_hit: reasons.append("損切り -7.0%")
                if dc_hit: reasons.append("20日安値割れ" if replacement_live else "ストキャスDC")
                sell_signal_rows.append({
                    "コード":c,"銘柄名":name(t),"保有株数":shares,"取得単価":avg,"現在株価":float(px),
                    "損益率%":pnl_pct,"%K":float(sk) if np.isfinite(sk) else np.nan,
                    "%D":float(sd) if np.isfinite(sd) else np.nan,"売り理由":"＋".join(reasons),
                })

signal_audit_df = pd.DataFrame(signal_audit_rows)

if signal_first and not signal_first_audit_df.empty:
    with st.expander("TOPIX＋日経225＋グロース買いシグナルの抽出・見送り理由", expanded=False):
        st.caption(f"買いシグナル抽出 {len(signal_first_audit_df)}銘柄 / 財務判定不能による見送り {value_unrated_count}銘柄。" + ("通過候補はATR比率の大きい順。" if replacement_live else "通過候補はATR比率の大きい順。入口変更後の収益性は未検証。"))
        show_stock_table(signal_audit_df[signal_audit_df["現行買い"].eq(True)], use_container_width=True, hide_index=True)

if not signal_audit_df.empty and "買い見送り理由" in signal_audit_df.columns:
    overvalued_buy_exclusions_df = signal_audit_df[
        signal_audit_df["買い見送り理由"].str.contains("割高判定のため新規買い除外", na=False)
    ].copy()
else:
    overvalued_buy_exclusions_df = pd.DataFrame(columns=[
        "コード", "銘柄名", "銘柄判定", "採用方式の買いシグナル", "最終買い", "買い見送り理由"
    ])

# カード表示だけに使う。買い・売りの採用判定やZIPの列には接続しない。
trend_by_code = {str(r["コード"]): r for r in signal_audit_rows}

if buy_signal_rows:
    buy_signal_df = pd.DataFrame(buy_signal_rows)
    buy_signal_df = (buy_signal_df.sort_values(["候補スコア", "コード"], ascending=[False, True]) if signal_first else buy_signal_df.sort_values(["63日騰落率%", "コード"], ascending=[False, True]) if replacement_live else buy_signal_df.sort_values(["%K", "コード"])).reset_index(drop=True)
    plan = build_fixed45_purchase_plan(
        buy_signal_df, short_buying_power, current_assets, held_codes,
        purchase_cap, execution_mode, lot_cap, order_inputs_ready=order_inputs_ready,
    )
    buy_view = plan.merge(buy_signal_df[["コード","%K","%D","条件","ロジック","63日騰落率%"]], on="コード", how="left")
    buy_view = buy_view.rename(columns={"購入株数":"参考S株数","予定購入額":"概算購入額"})
    buy_view["参考購入株数"] = buy_view["参考S株数"]
    buy_view["S株注文参考株数"] = buy_view["参考S株数"].where(buy_view["注文区分"].str.startswith("S株"), 0)
    buy_view["参考S株数"] = buy_view["S株注文参考株数"]
    buy_meta_df = signal_value_df if signal_first else value_top50_df
    meta_cols=[c for c in ["コード","順位","候補スコア","AI_TOP50スコア","割安判定"] if c in buy_meta_df.columns]
    if meta_cols:
        m=buy_meta_df[meta_cols].copy(); m["コード"]=m["コード"].astype(str); buy_view["コード"]=buy_view["コード"].astype(str)
        buy_view=buy_view.merge(m,on="コード",how="left")
    cols=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","ロジック","63日騰落率%","%K","%D","候補スコア","AI_TOP50スコア","割安判定","買付可否","参考購入株数","S株注文参考株数","注文区分","通常注文参考株数","通常注文概算額","余力引当額","均等配分予算","見送り理由"]
    buy_view=buy_view[[c for c in cols if c in buy_view.columns]].head(3).copy()
    for c in ["現在株価","概算購入額"]:
        if c in buy_view: buy_view[c]=pd.to_numeric(buy_view[c],errors="coerce").round(0)
    for c in ["%K","%D","AI_TOP50スコア"]:
        if c in buy_view: buy_view[c]=pd.to_numeric(buy_view[c],errors="coerce").round(2)

sell_cols=["優先","コード","銘柄名","保有株数","取得単価","現在株価","損益率%","%K","%D","売り理由"]
if sell_signal_rows:
    sell_view=pd.DataFrame(sell_signal_rows)
    sell_view["優先"]=sell_view["売り理由"].str.contains("損切り",na=False).map({True:"🚨 最優先",False:"🔻 SELL"})
    sell_view=sell_view[sell_cols].copy()
    for c in ["取得単価","現在株価","損益率%","%K","%D"]:
        sell_view[c]=pd.to_numeric(sell_view[c],errors="coerce").round(2)
else:
    sell_view=pd.DataFrame(columns=sell_cols)

for _view in (buy_view, sell_view):
    if not _view.empty:
        _metrics = {code(t): selection_range_metrics(df) for t, df in data.items()}
        for _col in ("値幅区分", "ATR比率%", "安値切上げ"):
            _view[_col] = _view["コード"].astype(str).map(lambda c: _metrics.get(c, {}).get(_col))

# 管理者研究用。実売買には一切使わない3方式比較。
indicator_compare_df = build_indicator_compare(data, true_top50_codes, held_codes) if data and true_top50_codes else pd.DataFrame()
if not isinstance(indicator_compare_df, pd.DataFrame):
    indicator_compare_df = pd.DataFrame()

# 管理者研究用。旧Ver.5.5系の急騰予兆を企業価値AI TOP50だけへ適用。
surge_top50_df = build_top50_surge_radar(data, value_top50_df) if data and true_top50_codes else pd.DataFrame()
if not isinstance(surge_top50_df, pd.DataFrame):
    surge_top50_df = pd.DataFrame()

valuation_master_df = build_valuation_master(
    [value_top50_df, signal_value_df, valuation_master_df], short_confirmed, data,
    [buy_view, sell_view, indicator_compare_df])
# 算定はマスター作成時だけ行い、描画時には取得し直さない。
value_top50_df = add_research_judgements_first(value_top50_df, surge_top50_df, valuation_master_df)
signal_value_df = add_research_judgements_first(signal_value_df, surge_top50_df, valuation_master_df)

# 画面に出るすべての銘柄表で、研究判定を左端に統一表示する。
# 急騰予兆は表示情報。企業価値判定は新規買いで使用した共通マスターを再利用。
buy_view = add_research_judgements_first(buy_view, surge_top50_df, valuation_master_df)
sell_view = add_research_judgements_first(sell_view, surge_top50_df, valuation_master_df)
indicator_compare_df = add_research_judgements_first(indicator_compare_df, surge_top50_df, valuation_master_df)

# ------------------------------------------------------------
# 独立研究: 公表日時時点の財務情報だけを用いる2年バックテスト
# 現行の売買判定・保有・注文処理へは接続しない。
# ------------------------------------------------------------
def _fl_num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _fl_history_from_zip(upload):
    with ZipFile(io.BytesIO(upload)) as zf:
        if "backtest_history_5y.csv" not in zf.namelist():
            raise ValueError("backtest_history_5y.csv がZIP内にありません")
        df = pd.read_csv(zf.open("backtest_history_5y.csv"), low_memory=False)
    required = {"日付", "コード", "Open", "High", "Low", "Close", "Adj Close"}
    if not required.issubset(df.columns):
        raise ValueError("日足CSVに必要な列がありません: " + ", ".join(sorted(required - set(df.columns))))
    df["date"] = pd.to_datetime(df["日付"], errors="coerce").dt.normalize()
    df["code"] = df["コード"].astype(str).str.replace(r"\.0$", "", regex=True).str[:4]
    for c in ["Open", "High", "Low", "Close", "Adj Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "Open", "High", "Low", "Close", "Adj Close"])
    df = df[(df["Close"] > 0) & (df["Adj Close"] > 0)].copy()
    ratio = df["Adj Close"] / df["Close"]
    for c in ["Open", "High", "Low", "Close"]:
        df["a" + c] = df[c] * ratio
    return df.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")


def _fl_fetch_worker(state, key, codes):
    # 無料プラン全体の5回/分を超えない。既存の画面処理と併用しない。
    last_call = 0.0
    try:
        for code4 in codes:
            if state["stop"]:
                break
            rows = []
            page = None
            while True:
                interval = 12.5 - (time.monotonic() - last_call)
                if interval > 0:
                    time.sleep(interval)
                params = {"code": code4}
                if page:
                    params["pagination_key"] = page
                last_call = time.monotonic()
                try:
                    response = requests.get(
                        "https://api.jquants.com/v2/fins/summary",
                        headers={"x-api-key": key}, params=params, timeout=30,
                    )
                    if response.status_code in (401, 403):
                        state["fatal"] = "J-Quants認証エラー。APIキーとプランを確認してください"
                        state["stop"] = True
                        break
                    if response.status_code == 429:
                        time.sleep(65)
                        continue
                    response.raise_for_status()
                    body = response.json()
                    rows.extend(body.get("data", []))
                    next_page = body.get("pagination_key")
                    if not next_page or next_page == page:
                        break
                    page = next_page
                except Exception as exc:
                    state["errors"][code4] = str(exc)[:180]
                    break
            if rows:
                state["rows"][code4] = rows
            state["done"].add(code4)
    except Exception as exc:
        state["fatal"] = str(exc)[:180]
    finally:
        state["running"] = False


def _fl_events(state):
    records = []
    # 取得スレッドが辞書へ銘柄を追加するため、表示側は時点スナップショットを読む。
    for code4, rows in list(state["rows"].items()):
        for r in rows:
            d = pd.to_datetime(r.get("DiscDate"), errors="coerce")
            if pd.isna(d):
                continue
            # 当日の場中開示も翌営業日から利用可能にする。
            records.append({"code": code4, "disc_date": d.normalize(),
                            "disc_time": str(r.get("DiscTime", "")),
                            "disc_no": str(r.get("DiscNo", "")),
                            "fy_end": str(r.get("CurFYEn", "")),
                            "eps": _fl_num(r.get("FEPS")),
                            "bps": _fl_num(r.get("BPS")),
                            "roe": _fl_num(r.get("ROE")),
                            "sales": _fl_num(r.get("Sales")),
                            "forecast_sales": _fl_num(r.get("FSales"))})
    if not records:
        return pd.DataFrame()
    out = pd.DataFrame(records).sort_values(["code", "disc_date", "disc_time", "disc_no"])
    # 同一会計年度の予想値が空欄の開示で、以前の予想値を消さない。
    out["eps"] = out.groupby(["code", "fy_end"])["eps"].ffill()
    for col in ["bps", "roe"]:
        out[col] = out.groupby("code")[col].ffill()
    return out


def _fl_backtest(prices, events, discount=0.25, fee_bps=10.0, improved=False):
    if events.empty:
        raise ValueError("財務履歴がありません")
    # 無料プランの開示日範囲だけを対象とし、全銘柄共通の2年窓に限定。
    cutoff = pd.Timestamp(tokyo_now().date()) - pd.Timedelta(weeks=12)
    end = min(cutoff, prices["date"].max())
    start = max(events["disc_date"].min(), end - pd.DateOffset(years=2))
    if start >= end:
        raise ValueError("財務履歴と日足の検証期間が重なりません")
    price_by_code = {}
    for code4, p in prices.groupby("code", sort=False):
        p = p.sort_values("date").copy()
        p["ma50"] = p["aClose"].rolling(50, min_periods=50).mean()
        p["ma200"] = p["aClose"].rolling(200, min_periods=200).mean()
        p["ma50_prev20"] = p["ma50"].shift(20)
        p["ma200_prev20"] = p["ma200"].shift(20)
        price_by_code[code4] = p.reset_index(drop=True)
    timeline = {}
    available = []
    for code4, e in events.groupby("code", sort=False):
        if code4 not in price_by_code:
            continue
        p = price_by_code[code4]
        e = e.sort_values(["disc_date", "disc_time", "disc_no"])
        erows = e.to_dict("records")
        pointer = 0
        current = None
        for i, row in p.iterrows():
            day = row["date"]
            if day < start or day > end:
                continue
            while pointer < len(erows) and erows[pointer]["disc_date"] < day:
                current = erows[pointer]
                pointer += 1
            if current is None or (day - current["disc_date"]).days > 200:
                continue
            eps, bps, roe = (current[k] for k in ("eps", "bps", "roe"))
            if not all(np.isfinite(x) and x > 0 for x in (eps, bps, roe)):
                continue
            # J-QuantsのROEは小数表記の場合があるため正規化。
            roe_ratio = roe / 100 if roe > 1 else roe
            if not 0.03 <= roe_ratio <= 0.40:
                continue
            if improved and roe_ratio < 0.08:
                continue
            # 事前に固定した試験用評価式。EPS×(8～18倍)とBPS×(0.7～2倍)の平均。
            pe = min(18., max(8., 10. + (roe_ratio - .08) * 40.))
            pb = min(2., max(.7, .8 + roe_ratio * 5.))
            # 調整済み日足の株式分割単位に揃える。
            ratio = row["Adj Close"] / row["Close"]
            fair = (eps * pe + bps * pb) * .5 * ratio
            if not np.isfinite(fair) or fair <= 0:
                continue
            available.append((code4, day))
            if (i + 1 >= len(p) or not np.isfinite(row["ma200"]) or
                row["aClose"] <= row["ma50"] or row["ma50"] <= row["ma200"] or
                row["aClose"] > fair * (1 - discount)):
                continue
            # 比較案: 財務の収益性と、直近20営業日の中期・長期トレンドを確認。
            if improved and (not np.isfinite(row["ma50_prev20"]) or
                             not np.isfinite(row["ma200_prev20"]) or
                             row["ma50"] <= row["ma50_prev20"] or
                             row["ma200"] <= row["ma200_prev20"]):
                continue
            nxt = p.iloc[i + 1]
            if nxt["date"] > end or nxt["aOpen"] <= 0:
                continue
            timeline.setdefault(nxt["date"], []).append(
                (code4, float(fair), float(row["aClose"]), str(current["disc_date"].date()),
                 float(nxt["aOpen"]), float(row["ma50"])))
    dates = sorted(prices.loc[prices["date"].between(start, end), "date"].unique())
    positions, trades, curve = {}, [], []
    last_exit = {}
    cash, initial = 600000., 600000.
    last_close = {}
    fee = fee_bps / 10000.
    for day in dates:
        day = pd.Timestamp(day)
        for code4 in list(positions):
            p = price_by_code[code4]
            match = p[p["date"].eq(day)]
            if match.empty:
                continue
            bar = match.iloc[0]
            pos = positions[code4]
            last_close[code4] = float(bar["aClose"])
            stop, target = pos["entry"] * .93, pos["target"]
            # 同一日に両方触れた場合は損切りを先に約定とみなす。
            if bar["aOpen"] <= stop:
                price, reason = float(bar["aOpen"]), "損切り（ギャップ）"
            elif bar["aLow"] <= stop:
                price, reason = stop, "損切り"
            elif bar["aOpen"] >= target:
                price, reason = float(bar["aOpen"]), "適正株価（ギャップ）"
            elif bar["aHigh"] >= target:
                price, reason = target, "適正株価"
            else:
                continue
            proceeds = pos["shares"] * price * (1 - fee)
            cash += proceeds
            trades.append({"コード": code4, "買い日":pos["day"].date(),
                           "売り日":day.date(), "買値":pos["entry"], "売値":price,
                           "適正株価":target, "株数":pos["shares"], "売り理由":reason,
                           "損益円":round(proceeds - pos["cost"], 2),
                           "損益率%":round((proceeds / pos["cost"] - 1) * 100, 2),
                           "開示日":pos["disc_date"]})
            last_exit[code4] = day
            del positions[code4]
        for code4, target, signal_close, disc_date, entry, _ in sorted(
            timeline.get(day, []), key=lambda x: (x[2] / x[1], x[0])
        ):
            if code4 in positions or len(positions) >= 5 or entry >= target * (1 - discount):
                continue
            if improved and code4 in last_exit and (day - last_exit[code4]).days < 30:
                continue
            # 終値のシグナルから翌日の寄付まで大きく下落した銘柄は見送る。
            if improved and entry < signal_close * .95:
                continue
            equity = cash + sum(v["shares"] * last_close.get(c, v["entry"])
                                for c, v in positions.items())
            budget = min(cash, equity * .20)
            shares = int(budget / (entry * (1 + fee)))
            if shares < 1:
                continue
            cost = shares * entry * (1 + fee)
            cash -= cost
            positions[code4] = {"entry":entry, "target":target, "shares":shares,
                                "cost":cost, "day":day, "disc_date":disc_date}
            last_close[code4] = entry
        equity = cash + sum(v["shares"] * last_close.get(c, v["entry"])
                            for c, v in positions.items())
        curve.append({"日付":day.date(), "総資産":round(equity, 2), "現金":round(cash, 2)})
    tdf, edf = pd.DataFrame(trades), pd.DataFrame(curve)
    if edf.empty:
        raise ValueError("株価と財務情報に共通の営業日がありません")
    peak = edf["総資産"].cummax()
    closed = len(tdf)
    gains = tdf["損益円"].clip(lower=0).sum() if closed else 0
    losses = -tdf["損益円"].clip(upper=0).sum() if closed else 0
    summary = {"開始日":str(start.date()), "終了日":str(end.date()),
               "財務取得銘柄数":events["code"].nunique(),
               "評価可能銘柄数":len({x[0] for x in available}),
               "決済件数":closed, "未決済件数":len(positions),
               "最終資産円":round(float(edf["総資産"].iloc[-1])),
               "損益率%":round((float(edf["総資産"].iloc[-1]) / initial - 1) * 100, 2),
               "最大DD%":round(float((edf["総資産"] / peak - 1).min() * 100), 2),
               "勝率%":round(float((tdf["損益円"] > 0).mean() * 100), 2) if closed else np.nan,
               "PF":round(float(gains / losses), 2) if losses else np.nan}
    open_rows = [{"コード":c, "買い日":p["day"].date(), "買値":p["entry"],
                  "株数":p["shares"], "最終評価値":round(p["shares"] * last_close.get(c, p["entry"]), 2),
                  "取得費用":round(p["cost"], 2), "開示日":p["disc_date"]}
                 for c, p in positions.items()]
    odf = pd.DataFrame(open_rows)
    summary["確定損益円"] = round(float(tdf["損益円"].sum()), 2) if closed else 0.
    summary["未決済評価損益円"] = round(float((odf["最終評価値"] - odf["取得費用"]).sum()), 2) if not odf.empty else 0.
    return summary, tdf, edf, odf


def render_fundamental_lab():
    st.subheader("🧪 ファンダメンタル単独・2年バックテスト")
    st.caption("研究専用です。今日の買い・売り判定には反映しません。ストキャスティクスは使いません。")
    st.info("無料プランは過去2年・12週間遅延です。財務履歴の取得には対象銘柄数に応じて時間がかかります。")
    st.caption("元の条件と改善案を同じ日足・財務履歴・手数料で比較します。改善案はROE 8%以上、50日・200日線が20営業日前より上、損切り後30日間は同銘柄を再購入しない、翌寄付が前日終値から5%以上下落したら見送ります。現行売買画面には反映しません。")
    source = st.file_uploader("5年日足ZIP（管理者タブで出力したファイル）", type="zip", key="fl_zip")
    history_upload = st.file_uploader("保存済み財務履歴CSV（再取得せず比較する場合）", type="csv", key="fl_history_upload")
    key = jquants_api_key()
    if not key and history_upload is None:
        st.error("JQUANTS_API_KEY がSecretsにありません。")
        return
    if source is None:
        st.caption("管理者 → 企業価値 → バックテスト用5年日足データからZIPを保存して読み込んでください。")
        return
    try:
        raw = source.getvalue()
        prices = _fl_history_from_zip(raw)
    except Exception as exc:
        st.error(f"日足ZIPを読めません: {exc}")
        return
    codes = sorted(prices["code"].unique())
    st.caption(f"日足: {len(codes)}銘柄。現時点の母集団を遡る検証であり、過去に上場廃止した銘柄は含まれません。")
    if "fl_state" not in st.session_state or st.session_state.fl_state["zip_name"] != source.name:
        st.session_state.fl_state = {"zip_name":source.name, "rows":{}, "errors":{},
                                     "done":set(), "running":False, "stop":False, "fatal":""}
        st.session_state.pop("fl_result", None)
    state = st.session_state.fl_state
    if st.button("財務履歴の取得を開始・再開", disabled=state["running"] or not key, key="fl_start"):
        state["stop"] = False
        state["fatal"] = ""
        for c in list(state["errors"]):
            state["done"].discard(c)
        state["errors"].clear()
        st.session_state.pop("fl_result", None)
        remaining = [c for c in codes if c not in state["done"]]
        if remaining:
            state["running"] = True
            threading.Thread(target=_fl_fetch_worker, args=(state, key, remaining), daemon=True).start()
    if st.button("取得を停止", disabled=not state["running"], key="fl_stop"):
        state["stop"] = True
    st.progress(len(state["done"]) / max(1, len(codes)),
                text=f"財務取得 {len(state['done'])}/{len(codes)}銘柄、取得成功 {len(state['rows'])}銘柄")
    if state["running"]:
        st.caption("この画面を開いたまま待ち、進捗を確認する場合は『進捗を更新』を押してください。")
        st.button("進捗を更新", key="fl_refresh")
    if state["fatal"]:
        st.error(state["fatal"])
    if state["errors"]:
        with st.expander(f"取得エラー {len(state['errors'])}銘柄"):
            show_stock_table(pd.DataFrame([{"コード":c,"理由":v} for c,v in state["errors"].items()]))
    if history_upload is not None:
        try:
            events = pd.read_csv(io.BytesIO(history_upload.getvalue()), dtype={"code":str})
            required = {"code", "disc_date", "disc_time", "disc_no", "fy_end", "eps", "bps", "roe"}
            if not required.issubset(events.columns):
                raise ValueError("財務履歴CSVの必須列が不足しています")
            events["code"] = events["code"].str.replace(r"\.0$", "", regex=True).str[:4]
            events["disc_date"] = pd.to_datetime(events["disc_date"], errors="coerce").dt.normalize()
            for col in ("eps", "bps", "roe"):
                events[col] = pd.to_numeric(events[col], errors="coerce")
            events = events.dropna(subset=["code", "disc_date"]).sort_values(
                ["code", "disc_date", "disc_time", "disc_no"])
            complete_input = set(codes).issubset(set(events["code"]))
            st.caption(f"保存済み履歴: {events['code'].nunique()}銘柄。CSVから再計算します。")
        except Exception as exc:
            st.error(f"財務履歴CSVを読めません: {exc}")
            return
    else:
        if not state["rows"]:
            return
        events = _fl_events(state)
        complete_input = len(state["done"]) == len(codes) and not state["errors"]
    if not events.empty:
        st.download_button("取得済み財務履歴CSVを保存", events.to_csv(index=False).encode("utf-8-sig"),
                           "fundamental_history_observed.csv", "text/csv", key="fl_export")
    discount = st.slider("買い時の必要割安率", 10, 50, 25, 5, format="%d%%", key="fl_discount")
    fee_bps = st.number_input("片道の売買コスト（bps）", 0., 100., 10., 1., key="fl_fee")
    if st.button("取得済み銘柄でバックテスト", key="fl_run"):
        try:
            results = []
            for name, improved in (("元の条件", False), ("改善案", True)):
                summary, trades, curve, open_positions = _fl_backtest(
                    prices, events, discount/100, fee_bps, improved=improved)
                summary["条件"] = name
                results.append((summary, trades, curve, open_positions))
            st.session_state.fl_result = (results, complete_input)
        except Exception as exc:
            st.error(f"バックテストを実行できません: {exc}")
    if "fl_result" in st.session_state:
        results, complete = st.session_state.fl_result
        st.warning("一部銘柄のみの途中結果です。全銘柄の比較には使わないでください。" if not complete else
                   "現在の母集団による研究結果です。上場廃止銘柄の欠落と株式分割の近似調整に注意してください。")
        show_stock_table(pd.DataFrame([r[0] for r in results]), use_container_width=True, hide_index=True)
        st.line_chart(pd.concat([r[2].set_index("日付")["総資産"].rename(r[0]["条件"])
                                 for r in results], axis=1))
        for summary, trades, curve, open_positions in results:
            with st.expander(f"{summary['条件']}：決済と未決済の内訳"):
                show_stock_table(trades, use_container_width=True, hide_index=True)
                st.caption("最終評価値は売却時の手数料を差し引く前の評価です。")
                show_stock_table(open_positions, use_container_width=True, hide_index=True)
        buf = io.BytesIO()
        with ZipFile(buf, "w") as zf:
            zf.writestr("comparison.csv", pd.DataFrame([r[0] for r in results]).to_csv(index=False, encoding="utf-8-sig"))
            for summary, trades, curve, open_positions in results:
                suffix = "baseline" if summary["条件"] == "元の条件" else "improved"
                for name, frame in (("trades", trades), ("equity", curve), ("open_positions", open_positions)):
                    zf.writestr(f"{suffix}_{name}.csv", frame.to_csv(index=False, encoding="utf-8-sig"))
            zf.writestr("fundamentals.csv", events.to_csv(index=False, encoding="utf-8-sig"))
        st.download_button("比較結果ZIP", buf.getvalue(), "fundamental_lab_comparison_2y.zip", "application/zip", key="fl_results")


# ------------------------------------------------------------
# 3指標一致＋無料公開RSS。一推しの参考表示。生成AI/有料APIは使用しない。
# ------------------------------------------------------------
def news_source_url(value):
    from urllib.parse import urlsplit, urlunsplit
    try:
        p = urlsplit(str(value).strip())
        if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
            return ""
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
    except ValueError:
        return ""


def triple_news_candidates(compare, freshness, price_data, held_codes, value_frames):
    """同じ比較表の3つBUYがTrueの銘柄だけ。除外理由を全件残す。"""
    rows, audit = [], []
    required = {"コード", "Stoch_BUY", "RSI5_BUY", "BB20_BUY"}
    if not isinstance(compare, pd.DataFrame) or not required.issubset(compare.columns):
        return rows, pd.DataFrame(columns=["コード", "銘柄名", "見送り理由"])
    def flag(v):
        return v is True or isinstance(v, (bool, np.bool_)) and bool(v) or str(v).lower() == "true"
    triple = compare[compare[["Stoch_BUY", "RSI5_BUY", "BB20_BUY"]].apply(lambda col: col.map(flag)).all(axis=1)]
    fresh = {str(r["コード"]): r.to_dict() for _, r in freshness.iterrows()} if isinstance(freshness, pd.DataFrame) and not freshness.empty and "コード" in freshness else {}
    values = {}
    for frame in value_frames:
        if isinstance(frame, pd.DataFrame) and not frame.empty and "コード" in frame:
            values.update({str(r["コード"]): str(r.get("割安判定", "— 未算定")) for _, r in frame.iterrows()})
    held = set(map(str, held_codes))
    for _, r in triple.drop_duplicates("コード").iterrows():
        c = str(r["コード"])
        f = fresh.get(c, {})
        verdict = values.get(c, str(r.get("割安判定", "— 未算定")))
        reason = ""
        d = price_data.get(c + ".T", pd.DataFrame())
        px = safe_float(r.get("現在株価"))
        if c in held:
            reason = "既に保有中"
        elif "割高" in verdict:
            reason = "割高判定"
        elif f.get("状態") != "🟢 当日確定":
            reason = "最新確定日足を確認できず"
        elif not np.isfinite(px) or px <= 0 or not isinstance(d, pd.DataFrame) or len(d) < 20:
            reason = "有効日足不足"
        if not reason:
            turnover = safe_float((pd.to_numeric(d["Close"], errors="coerce") * pd.to_numeric(d["Volume"], errors="coerce")).tail(20).mean()) if "Volume" in d else np.nan
            if not np.isfinite(turnover) or turnover <= 0:
                reason = "流動性を確認できず"
        audit.append({"コード": c, "銘柄名": str(r.get("銘柄名", c)), "見送り理由": reason, "銘柄判定": verdict})
        if not reason:
            rows.append({"code": c, "name": str(r.get("銘柄名", c)), "close": float(px),
                         "bar_date": str(f.get("日足最終日", "")), "value_status": verdict,
                         "turnover20_yen": round(float(turnover), 0), "signals": "Stoch+RSI5+BB20"})
    return sorted(rows, key=lambda x: x["code"]), pd.DataFrame(audit)


def free_news_normalize(value):
    import unicodedata
    value = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s・･＆&ー\-_.、。()（）]+", "", value)


def parse_free_news_rss(content, candidate, now, window_days=7):
    """公開RSSの見出しのみ。記事本文の読解や事実確認と混同しない。"""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import urlsplit
    if len(content) > 2000000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("RSSの形式またはサイズを確認できません")
    root = ET.fromstring(content)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("RSSフィードを確認できません")
    trusted = ("finance.yahoo.co.jp", "kabutan.jp", "minkabu.jp", "nikkei.com", "reuters.com",
               "bloomberg.co.jp", "bloomberg.com", "nhk.or.jp", "jiji.com", "tdnet.info",
               "release.tdnet.info", "jpx.co.jp", "prtimes.jp")
    full_name = free_news_normalize(candidate["name"])
    short_name = re.sub(r"ホールディングス$|holdings$|グループ$|group$|株式会社$", "", full_name)
    aliases = {full_name, short_name}
    seen, rows = set(), []
    for item in root.findall("./channel/item"):
        title = str(item.findtext("title") or "").strip()
        headline = title.rsplit(" - ", 1)[0]
        norm = free_news_normalize(headline)
        # A company-name match is mandatory: stock codes alone can match unrelated numbers.
        if not any(len(a) >= 2 and a in norm for a in aliases):
            continue
        link = news_source_url(item.findtext("link"))
        source = item.find("source")
        source_url = news_source_url(source.attrib.get("url", "")) if source is not None else ""
        domain = (urlsplit(source_url).hostname or "").lower()
        if not link or not any(domain == d or domain.endswith("." + d) for d in trusted):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
            published = published.astimezone(now.tzinfo)
        except (TypeError, ValueError, OverflowError):
            continue
        if not now - timedelta(days=window_days) <= published <= now:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        rows.append({"url": link, "title": headline[:240], "published_date": published.date().isoformat(),
                     "published_at": published.isoformat(), "publisher": str(source.text or domain)[:120],
                     "publisher_url": source_url, "headline": headline, "fact": "見出しに記載された材料（本文未確認）"})
    return sorted(rows, key=lambda x: x["published_at"], reverse=True)[:40]


def fetch_free_candidate_news(candidate, now, window_days=7):
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
    import xml.etree.ElementTree as ET
    params = {"q": '"' + candidate["name"] + '" 株式 when:' + str(window_days) + 'd',
              "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    url = "https://news.google.com/rss/search?" + urlencode(params)
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (JPStockNewsReader)"})
        with urlopen(req, timeout=10) as response:
            content = response.read(2000001)
        return {"code": candidate["code"], "articles": parse_free_news_rss(content, candidate, now, window_days), "error": ""}
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, ET.ParseError):
        return {"code": candidate["code"], "articles": [], "error": "無料ニュースの取得に失敗"}


def score_free_news(candidate, articles, error=""):
    """未検証の固定ルール。転載数を加点しない。意味の判定は行わない。"""
    positive = {"上方修正": 35, "最高益": 30, "自社株買い": 30, "自己株式取得": 30,
                "増配": 25, "増益": 20, "黒字転換": 25, "大型受注": 25, "受注": 20,
                "業務提携": 20, "資本提携": 20, "承認取得": 25}
    negative = ("下方修正", "減益", "減配", "赤字拡大", "赤字転落", "債務超過", "不正", "不祥事",
                "行政処分", "上場廃止", "公募増資", "第三者割当", "新株予約権", "希薄化")
    speculative = ("期待", "思惑", "かも", "株価予想", "可能性", "観測", "検討", "見送り", "否定")
    # Discard forecasts/rumours for positive scoring; never add points for volume of articles.
    hits, negatives, evidence = {}, [], []
    for e in articles:
        title = free_news_normalize(e["headline"])
        bad = [w for w in negative if free_news_normalize(w) in title]
        if bad:
            negatives.extend(bad)
            evidence.append({**e, "detected": "注意材料：" + "・".join(bad)})
        if not any(free_news_normalize(w) in title for w in speculative):
            found = [w for w in positive if free_news_normalize(w) in title]
            if found:
                for w in found:
                    hits[w] = max(hits.get(w, 0), positive[w])
                evidence.append({**e, "detected": "見出し材料：" + "・".join(found)})
    weights = sorted(hits.values(), reverse=True)
    # Related terms (e.g. 上方修正/増益) are not treated as independent confirmation.
    material_score = 40 + weights[0] if weights else 0
    if len(weights) > 1:
        material_score += min(5, weights[1] / 5)
    liquidity = min(10, max(0, np.log10(max(candidate["turnover20_yen"], 1)) - 7) * 5)
    score = round(min(100, material_score + liquidity), 1) if weights else 0
    skip = (error if error else "注意材料の語を検出（本文確認が必要）" if negatives else
            "出典・日時・企業名を確認できる記事なし" if not articles else
            "見出しに具体的な好材料なし" if not hits else
            "無料材料評価が基準未満" if score < 70 else "")
    return {**candidate, "assessment": score, "reason": "3指標一致に加え、見出しで「" + "・".join(hits) + "」を検出。" if hits else "具体的な好材料を見出しから確認できず。",
            "risk": "見出しの語による評価です。本文の文脈・材料の新規性・織込み・決算予定は未確認。" + (" 注意語：" + "・".join(dict.fromkeys(negatives)) if negatives else ""),
            "evidence": list({e["url"]: e for e in evidence}.values()), "skip_reason": skip,
            "news_count": len(articles), "fetch_error": error}


def request_free_news_pick(candidates, now, window_days=7):
    from concurrent.futures import ThreadPoolExecutor
    base = {"selected": None, "assessments": [], "generated_at": now.isoformat(),
            "model": "無料RSS・見出しルール評価（生成AIなし）", "paid_api_used": False}
    if not candidates:
        return {**base, "status": "3指標一致の分析対象なし"}
    if len(candidates) > 80:
        return {**base, "status": "対象が80銘柄を超えました。全件取得の負荷を抑えるため今回は保留します"}
    with ThreadPoolExecutor(max_workers=4) as executor:
        fetched = list(executor.map(lambda c: fetch_free_candidate_news(c, now, window_days), candidates))
    results = [score_free_news(c, f["articles"], f["error"]) for c, f in zip(candidates, fetched)]
    results.sort(key=lambda x: (-x["assessment"], -x["turnover20_yen"], x["code"]))
    eligible = [r for r in results if not r["skip_reason"]]
    failures = sum(bool(f["error"]) for f in fetched)
    return {**base, "status": "完了", "selected": eligible[0] if eligible else None,
            "assessments": results, "取得失敗件数": failures,
            "overview": f"比較対象{len(candidates)}銘柄・ニュース取得失敗{failures}銘柄。点数は固定ルールの参考評価で、勝率ではありません。"}


def triple_news_purchase_status(selected, buy_view, signal_audit, held_codes, buying_power, block_unrated):
    """既存の財務除外・正式BUY・TOP3の余力引当を照合する。"""
    c = selected["code"]
    reasons = []
    if buying_power <= 0:
        reasons.append("買付余力が未入力または0円")
    if "割高" in selected["value_status"]:
        reasons.append("割高判定")
    # The current audit already enforces the actual financial setting and trading method.
    rows = signal_audit[signal_audit["コード"].astype(str).eq(c)] if isinstance(signal_audit, pd.DataFrame) and "コード" in signal_audit else pd.DataFrame()
    if rows.empty or not rows["最終買い"].fillna(False).any():
        specific = str(rows["買い見送り理由"].iloc[0]) if not rows.empty and "買い見送り理由" in rows else ""
        reasons.append(specific if specific and specific != "nan" else "選択中の売買方式の正式買い条件を満たさない")
    plan = buy_view[buy_view["コード"].astype(str).eq(c)] if isinstance(buy_view, pd.DataFrame) and "コード" in buy_view else pd.DataFrame()
    if plan.empty:
        reasons.append("現在の買いTOP3の注文計画に含まれていない")
    elif "NO BUY" in str(plan.iloc[0].get("買付可否", "")) or safe_float(plan.iloc[0].get("参考購入株数"), 0) <= 0:
        reasons.append(str(plan.iloc[0].get("見送り理由", "購入株数なし")))
    return list(dict.fromkeys(reasons)), (plan.iloc[0].to_dict() if not plan.empty else {})


def render_triple_news_pick(compare, freshness, price_data, held_codes, values, buy_view, signal_audit, buying_power, block_unrated, run_clicked):
    st.subheader("⭐ AIイチオシ（無料材料評価）")
    with st.expander("無料ニュース評価の設定", expanded=False):
        auto = st.checkbox("今日の判定を更新と一緒に無料ニュースも更新", value=False, key="v172429_news_auto")
        st.caption("3指標すべて点灯した銘柄を、直近7日間の無料公開RSSの見出しで比較します。有料API・APIキーは使いません。同じ結果は30分間再利用します。")
        st.caption("生成AIの読解ではなく、上方修正・自社株買い等の語で評価します。本文未確認のため、根拠リンクで材料を確認してください。")
    candidates, exclusions = triple_news_candidates(compare, freshness, price_data, held_codes, values)
    model = "無料RSS・見出しルール評価（生成AIなし）"
    now = tokyo_now()
    signature = hashlib.sha256(json.dumps({"date": now.date().isoformat(), "model": model, "candidates": candidates}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = st.session_state.get("v172410_news_result")
    compatible = previous and previous.get("signature") == signature
    age = float("inf")
    if compatible:
        try:
            age = (now - datetime.fromisoformat(previous["generated_at"])).total_seconds()
        except (ValueError, TypeError, KeyError):
            pass
    refresh = st.button("🔎 無料ニュースを更新", key="v172410_news_refresh", disabled=not candidates)
    if refresh or (run_clicked and auto and (not compatible or age > 1800 or previous.get("status") != "完了")):
        # Clear stale recommendation before attempting refresh; errors never reuse old picks.
        st.session_state.pop("v172410_news_result", None)
        with st.spinner(f"3指標一致の{len(candidates)}銘柄をニュースで比較中…"):
            previous = request_free_news_pick(candidates, now)
        previous["signature"] = signature
        st.session_state["v172410_news_result"] = previous
        compatible, age = True, 0
    result = dict(previous) if compatible else None
    selected = result.get("selected") if result and 0 <= age <= 1800 else None
    status = "材料未判定：無料ニュースを更新してください" if not result else result.get("status", "未判定")
    if result and age > 1800:
        status = "前回の材料評価から30分経過しました。無料ニュースを更新してください。"
        result["selected"] = None
        result["status"] = "期限切れ：" + status
    if not candidates:
        status = "最新確定日足・割高除外後に3指標一致の対象がありません。"
    if result and result.get("取得失敗件数", 0):
        st.warning(f"ニュース取得失敗：{result['取得失敗件数']}銘柄。一推しは取得できた対象の中での暫定評価です。")
    if selected:
        st.write(f"**{selected['code']}　{selected['name']}**")
        st.write("**選定理由：** " + selected["reason"])
        st.write("**注意点：** " + selected["risk"])
        st.caption(f"最新終値 ¥{selected['close']:,.1f}（{selected['bar_date']}） / 銘柄判定：{selected['value_status']}")
        reasons, plan = triple_news_purchase_status(selected, buy_view, signal_audit, held_codes, buying_power, block_unrated)
        if reasons:
            if "株数未計算" in str(plan.get("買付可否", "")):
                st.info("買い候補・株数未計算：余力を下部で入力してください。" + " / ".join(reasons))
            else:
                st.warning("注文条件の確認：" + " / ".join(reasons))
        else:
            st.success(f"現行システムの買い条件・注文計画も一致：参考{int(plan.get('参考購入株数', 0))}株 / {plan.get('注文区分', '')}")
            st.caption("上の株数は既存の買いTOP3内の注文です。追加で重複注文しないでください。")
        for e in selected["evidence"][:3]:
            st.link_button(f"{e['published_date']}｜{e['title'] or e['publisher']}", e["url"])
            st.write(e.get("detected", e["fact"]))
        st.caption("日時はRSSの公開時刻です。見出しのみの評価で、材料の発生日や本文は未確認です。")
    else:
        st.info("今日の一推し：該当なし。" + ("出典付きの有力材料を確認できませんでした。" if status == "完了" else status))
    if result:
        st.caption(f"調査時刻 {result.get('generated_at', '')} / 比較対象 {len(candidates)}銘柄 / 評価方式 {result.get('model') or model}")
    st.caption("3つの点灯は独立した3つの証拠ではありません。無料材料評価の選定効果は未検証です。売り・損切りと買付余力による注文計画を適用します。")
    with st.expander("一推しの比較・見送り理由", expanded=False):
        if result and result.get("assessments"):
            audit = pd.DataFrame([{ "コード": a["code"], "銘柄名": a["name"], "無料材料評価（勝率ではない）": a["assessment"], "銘柄判定": a["value_status"], "理由": a["reason"], "注意点": a["risk"], "見送り理由": a["skip_reason"]} for a in result["assessments"]])
            show_stock_table(audit, use_container_width=True, hide_index=True)
        if not exclusions.empty:
            show_stock_table(exclusions, use_container_width=True, hide_index=True)
    return result, exclusions, signature


# 大化け候補は独立した研究スクリーニング。既存売買・配分・損切りへ接続しない。
MULTIBAGGER_RULES = pd.DataFrame([
    {"項目": "候補の必須条件", "条件": "実績売上成長率15%以上・実績利益成長率20%以上・実績EPSと予想EPSが正・予想PER0超35以下"},
    {"項目": "鮮度", "条件": "当日の財務確認、参考指標基準日10日以内、成長実績の取得7日以内、成長比較期の期末から550日以内、連続12か月の実績比較、当日確定日足"},
    {"項目": "除外", "条件": "共通判定が割高、D/Eが200超、FCFが負、営業利益率が0以下（取得できた場合）"},
    {"項目": "研究点の内訳", "条件": "売上成長最大30・利益成長最大25・ROE最大15・予想PER最大15・時価総額最大10・FCF最大5。欠損は0点"},
    {"項目": "候補採用", "条件": "主要必須条件と共通企業価値確認を満たし研究点60以上。FCF・D/E・営業利益率の欠損は暫定、3項目取得済みは財務確認済。点数は上昇確率や予想倍率ではない。閾値は未検証"},
    {"項目": "対象範囲", "条件": "現在選択した監視対象の国内財務取得対象。全上場銘柄の網羅ではない"},
    {"項目": "AI参考選択", "条件": "候補内で財務確認済み優先→研究点降順→財務欠損数昇順→予想PER昇順→コード昇順。追加通信なし。第一候補でも買い指示ではない"},
    {"項目": "想定・限界", "条件": "数年の成長を調べる入口。単年実績で成長継続・競争優位・受注・希薄化は確認できない"},
])



def _parse_traders_growth(html, today):
    """通期実績同士の売上・純利益を比較。会社予想・中間決算を混ぜない。"""
    try:
        values = _parse_traders_finance(html, today)
    except ValueError:
        values = {"_source": "トレーダーズ・ウェブ・無料公開業績表"}
    parser = DomesticHistoryParser(); parser.feed(html)
    rows = parser.rows
    start = next((i for i, r in enumerate(rows) if len(r) > 2 and re.fullmatch(r"20[0-9]{2}年[0-9]{2}月", r[1].strip())), None)
    if start is None or start + 1 >= len(rows):
        raise ValueError("成長比較用の通期表なし")
    dates, kinds = rows[start], rows[start+1]
    actuals = []
    for i in range(1, min(len(dates), len(kinds))):
        if "本決算" not in kinds[i] or "予想" in kinds[i]: continue
        text = dates[i].strip().replace("年", "-").replace("月", "")
        if re.fullmatch(r"20[0-9]{2}-[0-9]{2}", text): actuals.append((text, i))
    actuals = sorted(actuals, reverse=True)
    if len(actuals) < 2: raise ValueError("連続する2期の通期実績なし")
    (latest, ni), (previous, oi) = actuals[:2]
    if (pd.Period(latest, freq="M") - pd.Period(previous, freq="M")).n != 12:
        raise ValueError("通期実績の間隔が12か月でない")
    values.update({"_growth_period": latest, "_growth_previous_period": previous,
                   "_actual_period": latest})
    epsrow = next((r for r in rows[start+2:start+10] if r and _finance_plain(r[0]) == "1株利益(円)"), None)
    values["trailingEps"] = _finance_number(epsrow[ni]) if epsrow and ni < len(epsrow) else np.nan
    for label, key in (("売上高(百万円)", "revenueGrowth"), ("純利益(百万円)", "earningsGrowth")):
        row = next((r for r in rows[start+2:start+10] if r and _finance_plain(r[0]).replace("（", "(").replace("）", ")") == label), None)
        if row and max(ni, oi) < len(row):
            now, old = _finance_number(row[ni]), _finance_number(row[oi])
            if np.isfinite(now) and np.isfinite(old) and old > 0: values[key] = now / old - 1
    return values



def fetch_traders_growth_page(c):
    """市場別URLを404の場合だけ切り替える。アクセス制限時は追加要求しない。"""
    state = _finance_transport_state()
    with state["lock"]:
        preferred = state.setdefault("traders_markets", {}).get(c, "61")
    markets = list(dict.fromkeys([preferred, "61", "62", "63"]))
    errors = []
    for market in markets:
        url = f"https://www.traders.co.jp/stocks/{market}_{c}/achievement"
        try:
            html = _fetch_free_finance_html(url)
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            if not title or not re.search(r"(?<![0-9A-Z])" + re.escape(c) + r"(?![0-9A-Z])", _finance_plain(title.group(1)), re.I):
                raise ValueError("銘柄コードを確認できず")
            with state["lock"]: state["traders_markets"][c] = market
            return html, url
        except requests.HTTPError as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) != 404: raise
            errors.append(market + ":404")
    raise ValueError("市場別ページなし " + "/".join(errors))


def restore_multibagger_optional(finance, baseline, today):
    """成長取得先で未提供の補助指標を、当日確認済みの元の財務から補完する。"""
    if not isinstance(finance, pd.DataFrame) or finance.empty or not isinstance(baseline, pd.DataFrame) or baseline.empty:
        return finance
    base = {str(r["コード"]): r for r in baseline.to_dict("records")}
    records = []
    for r in finance.to_dict("records"):
        b = base.get(str(r["コード"]), {})
        if (str(b.get("財務取得日時", ""))[:10] == str(today) and
                finance_freshness_usable(b.get("財務情報鮮度", "")) and
                "過去保存値" not in str(b.get("財務データ区分", ""))):
            restored = []
            for k in ("ROE%", "時価総額", "FCF", "D/E", "営業利益率%"):
                if not np.isfinite(safe_float(r.get(k))) and np.isfinite(safe_float(b.get(k))):
                    r[k] = b[k]; restored.append(k)
            if restored:
                r["補助指標出典"] = b.get("財務情報源", "")
                r["補助指標URL"] = b.get("財務参照URL", "")
                r["補助指標基準日"] = b.get("参考指標基準日", "")
                r["補完した指標"] = "／".join(restored)
        records.append(r)
    return pd.DataFrame(records)


def fetch_multibagger_growth(c, today):
    """通常のアクセス制限・休止を維持し、別の国内取得先へフォールバック。"""
    attempts, partials = [], []
    for label, url, parser in (
        ("トレーダーズ", f"https://www.traders.co.jp/stocks/61_{c}/achievement", _parse_traders_growth),
        ("株探", f"https://kabutan.jp/stock/?code={c}", _parse_kabutan_finance)):
        try:
            if label == "トレーダーズ":
                html, url = fetch_traders_growth_page(c)
            else:
                html = _fetch_free_finance_html(url)
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            if not title or not re.search(r"(?<![0-9A-Z])" + re.escape(c) + r"(?![0-9A-Z])", _finance_plain(title.group(1)), re.I):
                raise ValueError("銘柄コードを確認できず")
            f = parser(html, today)
            f.update({"_url": url, "_fetched_at": tokyo_now().isoformat()})
            complete = all(np.isfinite(safe_float(f.get(k))) for k in ("revenueGrowth", "earningsGrowth", "trailingEps")) and bool(f.get("_growth_period"))
            attempts.append(label + (": 成長実績取得済" if complete else ": 成長実績の一部が不足"))
            partials.append(f)
            if complete:
                f["_growth_status"] = "取得済"
                f["_growth_attempts"] = " / ".join(attempts)
                return f
        except Exception as exc:
            attempts.append(label + ": " + type(exc).__name__ + ": " + str(exc)[:200])
    if partials:
        f = max(partials, key=lambda v: sum(np.isfinite(safe_float(v.get(k))) for k in ("revenueGrowth", "earningsGrowth", "trailingEps")))
        f.update({"_growth_status": "一部取得・評価保留", "_growth_attempts": " / ".join(attempts)})
        return f
    raise ValueError(" / ".join(attempts))


def multibagger_growth_complete(r):
    return (all(np.isfinite(safe_float(r.get(k))) for k in
                ("売上成長率%", "利益成長率%", "実績EPS")) and
            bool(str(r.get("成長比較期", "")).strip()) and
            bool(str(r.get("成長比較前期", "")).strip()))


def multibagger_growth_cache_path():
    root = os.environ.get("FINANCE_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "jp_stock_finance_cache_v20")
    return os.path.join(root, "multibagger_growth_v27.json")


def load_multibagger_growth():
    try:
        with open(multibagger_growth_cache_path(), encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema") != 1: return pd.DataFrame()
        frame = pd.DataFrame(payload.get("records", []))
        if not frame.empty:
            frame["コード"] = frame["コード"].astype(str)
            frame = frame[frame["コード"].str.fullmatch(r"[0-9A-Za-z]{4,5}")]
        return frame
    except (OSError, ValueError, KeyError, TypeError):
        return pd.DataFrame()


def save_multibagger_growth(frame):
    path = multibagger_growth_cache_path()
    temp_path = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path), delete=False) as handle:
            temp_path = handle.name
            json.dump({"schema": 1, "records": _finance_json_safe(frame.to_dict("records"))}, handle, ensure_ascii=False, allow_nan=False)
        os.replace(temp_path, path)
        return "保存済"
    except (OSError, ValueError, TypeError) as exc:
        return "保存失敗: " + str(exc)[:160]
    finally:
        if temp_path and os.path.exists(temp_path): os.unlink(temp_path)


def merge_multibagger_growth(finance, extra):
    """当日財務は維持し、実績とその出典だけを別系列として合流する。"""
    if not isinstance(extra, pd.DataFrame) or extra.empty: return finance.copy()
    cached = {str(r["コード"]): r for r in extra.to_dict("records")}
    fields = ("売上成長率%", "利益成長率%", "実績EPS", "実績EPS対象期",
              "成長比較期", "成長比較前期", "成長確認日時", "成長取得日時",
              "成長追加確認", "成長取得経路", "成長出典URL", "成長情報源")
    rows = []
    for r in finance.to_dict("records"):
        b = cached.get(str(r["コード"]), {})
        for k in fields:
            if k in b: r[k] = b[k]
        if b:
            r["成長取得日時"] = b.get("成長取得日時", b.get("財務取得日時", ""))
            r["成長出典URL"] = b.get("成長出典URL", b.get("財務参照URL", ""))
            r["成長情報源"] = b.get("成長情報源", b.get("財務情報源", ""))
        rows.append(r)
    return pd.DataFrame(rows)


def multibagger_sources_paused():
    state = _finance_transport_state()
    with state["lock"]:
        return all(state["hosts"].get(host, {}).get("blocked_until", 0) > time.monotonic()
                   for host in ("www.traders.co.jp", "kabutan.jp"))


def collect_multibagger_growth(finance, extra, scope, limit, today):
    additions = []
    for c in multibagger_fetch_targets(scope, extra, limit, today):
        # 全取得先が休止中なら未試行分を失敗扱いにせず、次回へ残す。
        if multibagger_sources_paused(): break
        original = finance[finance["コード"].astype(str).eq(c)].iloc[0].to_dict()
        original["成長確認日時"] = tokyo_now().isoformat()
        try:
            f = fetch_multibagger_growth(c, today)
            incoming = multibagger_finance_fields(f)
            # 部分取得・失敗で既存の完全な比較実績を消さない。
            complete = all(np.isfinite(safe_float(f.get(k))) for k in ("revenueGrowth", "earningsGrowth", "trailingEps"))
            if complete or not multibagger_growth_complete(original):
                original.update({k: incoming[k] for k in ("売上成長率%", "利益成長率%", "成長比較期", "成長比較前期")})
                original.update({"実績EPS": safe_float(f.get("trailingEps")), "実績EPS対象期": f.get("_actual_period", ""),
                    "成長取得日時": tokyo_now().isoformat(), "成長出典URL": f.get("_url", ""), "成長情報源": f.get("_source", "")})
            original.update({"成長追加確認": f.get("_growth_status", "一部取得・評価保留"), "成長取得経路": f.get("_growth_attempts", "")})
        except Exception as exc:
            original["成長追加確認"] = "取得失敗・再確認対象"
            original["成長取得経路"] = type(exc).__name__ + ": " + str(exc)[:1000]
        additions.append(original)
    combined = pd.concat([extra, pd.DataFrame(additions)], ignore_index=True)
    return combined.drop_duplicates("コード", keep="last") if not combined.empty else combined


def multibagger_fetch_targets(scope, extra, limit, today):
    """未確認を優先し、その後に失敗・前日取得分を再試行する。"""
    records = {str(r["コード"]): r for r in extra.to_dict("records")} if isinstance(extra, pd.DataFrame) and "コード" in extra else {}
    unseen, retry = [], []
    for c in sorted(scope):
        r = records.get(c)
        if r is None: unseen.append(c)
        else:
            checked = pd.to_datetime(r.get("成長確認日時", ""), errors="coerce", utc=True)
            if not pd.isna(checked) and (pd.Timestamp.now(tz="UTC") - checked).total_seconds() < 900:
                continue
            fetched = pd.to_datetime(str(r.get("成長取得日時", r.get("財務取得日時", "")))[:10], errors="coerce")
            recent = not pd.isna(fetched) and 0 <= (pd.Timestamp(today).normalize() - fetched.normalize()).days <= 7
            if not multibagger_growth_complete(r) or not recent: retry.append(c)
    return (unseen + retry)[:int(limit)]


def build_multibagger_research(finance, price_data, freshness, valuations, buy, today=None):
    """純粋な表計算。欠損・古いデータ・赤字からの見かけの成長を候補へ昇格させない。"""
    if not isinstance(finance, pd.DataFrame) or finance.empty:
        return pd.DataFrame()
    today = pd.Timestamp(today if today is not None else tokyo_now().date()).normalize()
    labels = {str(code(str(r["コード"]))): str(r.get("割安判定", "算定不能"))
              for r in valuations.to_dict("records")} if isinstance(valuations, pd.DataFrame) and "コード" in valuations else {}
    buys = {str(code(str(c))) for c in buy["コード"]} if isinstance(buy, pd.DataFrame) and "コード" in buy else set()
    daily = {str(code(str(r["コード"]))): str(r.get("状態", "")) for r in freshness.to_dict("records")} if isinstance(freshness, pd.DataFrame) and "コード" in freshness else {}
    rows = []
    for r in finance.drop_duplicates("コード", keep="last").to_dict("records"):
        c = str(code(str(r["コード"])))
        rev, earn, roe, pe, cap, fcf, debt, margin, eps, actual = [safe_float(r.get(k)) for k in (
            "売上成長率%", "利益成長率%", "ROE%", "予想PER", "時価総額", "FCF", "D/E", "営業利益率%", "予想EPS", "実績EPS")]
        missing, failed, reasons = [], [], []
        unconfirmed = [name + "未確認" for name, value in (("FCF", fcf), ("D/E", debt), ("営業利益率", margin)) if not np.isfinite(value)]
        for label, value, threshold in (("売上成長率", rev, 15), ("利益成長率", earn, 20)):
            if not np.isfinite(value): missing.append(label)
            elif value < threshold: failed.append(label + "条件未達")
            else: reasons.append(f"{label}{value:.1f}%")
        for label, value in (("予想EPS", eps), ("実績EPS", actual)):
            if not np.isfinite(value): missing.append(label)
            elif value <= 0: failed.append(label + "が正でない")
        if not np.isfinite(pe): missing.append("予想PER")
        elif not 0 < pe <= 35: failed.append("予想PER条件外")
        def age_ok(value, days):
            try:
                d = pd.to_datetime(value, errors="coerce")
                if pd.isna(d):
                    return False
                # NaTにはnormalizeがないため欠損判定を先に行う。
                # 時刻・タイムゾーンを除き、日本の暦日として比較する。
                d = pd.Timestamp(d.date())
                return 0 <= (today-d).days <= days
            except (ValueError, TypeError, OverflowError):
                return False
        if not age_ok(str(r.get("財務取得日時", ""))[:10], 0): missing.append("当日の財務取得")
        if not age_ok(r.get("参考指標基準日", ""), 10) or not finance_freshness_usable(r.get("財務情報鮮度", "")) or "過去保存値" in str(r.get("財務データ区分", "")):
            missing.append("財務基準日・鮮度")
        try:
            end = pd.Period(r.get("成長比較期", ""), freq="M")
            prev = pd.Period(r.get("成長比較前期", ""), freq="M")
            if pd.isna(end) or pd.isna(prev) or (end-prev).n != 12 or not age_ok(end.end_time.date(), 550): missing.append("成長比較期間")
        except (ValueError, TypeError, AttributeError): missing.append("成長比較期間")
        if daily.get(c) != "🟢 当日確定": missing.append("最新確定日足")
        if not age_ok(str(r.get("成長取得日時", r.get("財務取得日時", "")))[:10], 7):
            missing.append("成長実績の再確認（7日以内）")
        label = labels.get(c, "算定不能")
        if any(word in label for word in ("不能", "未確認", "未算定")):
            missing.append("企業価値評価未確認")
        if "割高" in label: failed.append("共通判定が割高")
        for condition, text in ((np.isfinite(debt) and debt > 200, "D/Eが200超"),
                                 (np.isfinite(fcf) and fcf < 0, "FCFが負"),
                                 (np.isfinite(margin) and margin <= 0, "営業利益率が0以下")):
            if condition: failed.append(text)
        score = (min(max(rev, 0), 30) if np.isfinite(rev) else 0)
        score += min(max(earn, 0), 40) / 40 * 25 if np.isfinite(earn) else 0
        score += min(max(roe, 0), 20) / 20 * 15 if np.isfinite(roe) else 0
        score += (15 if 0 < pe <= 20 else 10 if 20 < pe <= 30 else 5 if 30 < pe <= 35 else 0)
        score += (10 if 0 < cap <= 100e9 else 5 if 100e9 < cap <= 300e9 else 0)
        score += 5 if np.isfinite(fcf) and fcf > 0 else 0
        score = round(float(score), 1)
        status = "条件外" if failed else "評価保留" if missing else ("大化け候補〈暫定〉" if unconfirmed else "大化け候補〈財務確認済〉") if score >= 60 else "成長監視"
        risks = [k + "未取得" for k, v in (("ROE", roe), ("時価総額", cap), ("FCF", fcf), ("D/E", debt), ("営業利益率", margin)) if not np.isfinite(v)]
        if label == "算定不能" or "不能" in label or "未確認" in label: risks.append("企業価値評価未確認")
        risks += ["競争優位・成長の持続性・増資予定はIRで確認"]
        history = price_data.get(c + ".T", pd.DataFrame())
        close = safe_float(history["Close"].iloc[-1]) if isinstance(history, pd.DataFrame) and not history.empty and "Close" in history else np.nan
        if not np.isfinite(close) or close <= 0:
            if status != "条件外": status = "評価保留"
            missing.append("現在株価")
        rows.append({**{k: r.get(k, "") for k in ("補助指標出典", "補助指標URL", "補助指標基準日", "補完した指標")},
            "研究判定": status, "財務健全性確認": "／".join(unconfirmed) or "3指標確認済", "未確認項目数": len(unconfirmed), "コード": c, "銘柄名": r.get("銘柄名", c), "研究点": score,
            "今日の買いと一致": "一致" if c in buys else "なし", "銘柄判定": label, "現在株価": close,
            "選定根拠": "／".join(reasons) or "成長実績未確認", "保留・条件外理由": "／".join(failed + missing),
            "確認するリスク": "／".join(risks), "売上成長率%": rev, "利益成長率%": earn,
            "予想PER": pe, "ROE%": roe, "時価総額": cap, "FCF": fcf, "D/E": debt, "営業利益率%": margin,
            "成長比較期": r.get("成長比較期", ""), "成長比較前期": r.get("成長比較前期", ""),
            "実績EPS": actual, "成長追加確認": r.get("成長追加確認", "未確認"),
            "成長取得経路": r.get("成長取得経路", ""),
            "成長取得日時": r.get("成長取得日時", ""), "成長出典URL": r.get("成長出典URL", ""),
            "財務情報源": r.get("財務情報源", ""), "財務参照URL": r.get("財務参照URL", ""),
            "財務取得日時": r.get("財務取得日時", ""), "参考指標基準日": r.get("参考指標基準日", "")})
    out = pd.DataFrame(rows)
    order = {"大化け候補〈財務確認済〉": 0, "大化け候補〈暫定〉": 1, "成長監視": 2, "評価保留": 3, "条件外": 4}
    return out.assign(_order=out["研究判定"].map(order)).sort_values(["_order", "研究点", "コード"], ascending=[True, False, True]).drop(columns="_order").reset_index(drop=True)


def annotate_multibagger_choice(audit):
    """追加通信なし。確認済み→研究点→欠損数→PER→コードで安定比較する。"""
    out = audit.copy()
    out["AI参考順位"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out["AI参考選択"] = "対象外"
    out["AI選定理由"] = ""
    if out.empty: return out
    mask = out["研究判定"].isin(["大化け候補〈財務確認済〉", "大化け候補〈暫定〉"])
    eligible = out.loc[mask].copy()
    if eligible.empty: return out
    eligible["_confirmed"] = eligible["研究判定"].eq("大化け候補〈財務確認済〉").astype(int)
    eligible["_missing"] = eligible[["ROE%", "時価総額", "FCF", "D/E", "営業利益率%"]].isna().sum(axis=1)
    eligible = eligible.sort_values(["_confirmed", "研究点", "_missing", "予想PER", "コード"],
                                    ascending=[False, False, True, True, True])
    for rank, (index, row) in enumerate(eligible.iterrows(), 1):
        provisional = row["研究判定"] == "大化け候補〈暫定〉"
        out.at[index, "AI参考順位"] = rank
        out.at[index, "AI参考選択"] = ("第一候補（暫定）" if provisional else "第一候補（財務確認済）") if rank == 1 else "比較候補"
        out.at[index, "AI選定理由"] = (
            f"財務確認済みを優先し、研究点・欠損数・PERの順で比較した第{rank}位。"
            f"研究点{row['研究点']:.1f}、売上成長{row['売上成長率%']:.1f}%、"
            f"利益成長{row['利益成長率%']:.1f}%、予想PER{row['予想PER']:.2f}倍。"
            + ("財務健全性：" + str(row["財務健全性確認"]) + "。" if provisional else "財務3指標は取得済み。")
            + "成長継続・競争優位・増資予定は別途確認。")
    return out


def render_multibagger_choice(frame):
    if frame.empty:
        st.info("AI参考選択：該当なし。主要条件を満たす候補がないため、選定を見送ります。")
        return
    row = frame.sort_values("AI参考順位").iloc[0]
    st.markdown(f"**AIならこの候補：{row['コード']} {row['銘柄名']}**")
    st.write(str(row["AI参考選択"]))
    st.caption(str(row["AI選定理由"]))
    st.caption("取得済み指標をルールで比較した研究用の参考選択です。将来の上昇予測や買い指示ではありません。未取得銘柄を含む全銘柄での最良を保証するものではありません。")


def render_multibagger_cards(frame, limit=3):
    if frame.empty:
        st.info("大化け候補の条件を満たす銘柄はありません。評価保留の理由は大化け候補タブで確認できます。")
    for r in frame.head(limit).to_dict("records"):
        st.markdown(f"**{r['コード']} {r['銘柄名']}**　研究点 {r['研究点']:.1f} / 100")
        st.write(f"{r['研究判定']} ／ AI参考順位 {r['AI参考順位']}")
        st.write(f"今日の買いと一致：{r['今日の買いと一致']} ／ 銘柄判定：{r['銘柄判定']}")
        if r["未確認項目数"]:
            st.warning("未確認のリスク：" + r["財務健全性確認"])
        st.caption("根拠：" + r["選定根拠"])
        st.caption("確認：" + r["確認するリスク"])


# 一回の通常判定で得た財務全件を利用。TOP50の順位では絞らない。
mb_finance = finance_acquisition_df.copy()
if "v172423_growth_extra" not in st.session_state:
    st.session_state["v172423_growth_extra"] = load_multibagger_growth()
mb_extra = st.session_state["v172423_growth_extra"]
mb_scope = set(mb_finance["コード"].astype(str)) if "コード" in mb_finance else set()
if isinstance(mb_extra, pd.DataFrame) and not mb_extra.empty:
    mb_extra = mb_extra[mb_extra["コード"].astype(str).isin(mb_scope)]
    mb_finance = merge_multibagger_growth(mb_finance, mb_extra)
# 通常の売買更新では研究用通信をしない。専用ボタンで明示更新する。
mb_finance = restore_multibagger_optional(mb_finance, finance_acquisition_df, tokyo_now().date())
if isinstance(mb_extra, pd.DataFrame) and not mb_extra.empty:
    mb_extra = restore_multibagger_optional(mb_extra, finance_acquisition_df, tokyo_now().date())
multibagger_audit = build_multibagger_research(mb_finance, data, daily_bar_status_df, valuation_master_df, buy_view)
multibagger_audit = annotate_multibagger_choice(multibagger_audit)
multibagger_candidates = multibagger_audit[multibagger_audit["AI参考順位"].notna()].sort_values("AI参考順位").copy() if not multibagger_audit.empty else pd.DataFrame()


# Independent research: no references from live BUY/SELL/allocation paths.
def build_buy_exclusions(audit, purchases, holdings, previous=None):
    rows, seen = [], set()
    current = set(purchases['コード'].astype(str)) if 'コード' in purchases else set()
    held = set(map(str, holdings))
    if isinstance(audit, pd.DataFrame) and not audit.empty:
        for _, r in audit.iterrows():
            c = str(r['コード'])
            if c in current or c in held or not bool(r.get('採用方式の買いシグナル', False)):
                continue
            reason = str(r.get('買い見送り理由', '') or '')
            if not reason or reason == 'nan':
                reason = '購入優先順位により今回のTOP3対象外' if bool(r.get('最終買い', False)) else '最終買い条件未達'
            rows.append({'コード': c, '銘柄名': r.get('銘柄名', c), '銘柄判定': r.get('銘柄判定', ''), '理由': reason})
            seen.add(c)
    for r in previous or []:
        c = str(r['コード'])
        if c in current or c in seen:
            continue
        reason = '保有銘柄として確認済み' if c in held else '今回の買いサインが不成立、または監視対象・日足確認条件から外れたため'
        rows.append({'コード': c, '銘柄名': r.get('銘柄名', c), '銘柄判定': r.get('割安判定', ''), '理由': reason})
    return pd.DataFrame(rows, columns=['コード', '銘柄名', '銘柄判定', '理由'])


@st.cache_data(ttl=21600, show_spinner=False)
def download_bottom_research(tickers_tuple, target_day, retry_tag=0):
    """Explicit research request only; raw prices for turnover, adjusted for patterns."""
    result = {}
    for offset in range(0, len(tickers_tuple), 60):
        chunk = list(tickers_tuple[offset:offset + 60])
        try:
            raw = yf.download(tickers=chunk, period='6mo', interval='1d', auto_adjust=False,
                              actions=False, progress=False, threads=8, group_by='ticker', timeout=25)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                one = raw[t] if isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0) else raw.xs(t, axis=1, level=1) if isinstance(raw.columns, pd.MultiIndex) else raw if len(chunk) == 1 else pd.DataFrame()
                x = _normalize_ohlcv(one)
                x = x.loc[x.index <= pd.Timestamp(target_day)].copy()
                if x.empty:
                    continue
                x['Turnover'] = x['Close'] * x['Volume']
                # Split/dividend adjusted OHLC for shape; turnover remains unadjusted.
                if 'Adj Close' not in one:
                    continue
                adj = pd.to_numeric(one['Adj Close'], errors='coerce')
                adj.index = pd.DatetimeIndex(pd.to_datetime(adj.index)).tz_localize(None).normalize()
                ratio = adj[~adj.index.duplicated(keep='last')].reindex(x.index) / x['Close']
                if ratio.isna().any() or (ratio <= 0).any():
                    continue
                x['RawClose'] = x['Close'].copy()
                for col in ['Open', 'High', 'Low', 'Close']:
                    x[col] = x[col] * ratio
                result[t] = x
            except (KeyError, ValueError, TypeError, IndexError):
                continue
    return result


def rank_bottom_research(universe, prices, target_day, valuations):
    """Only information observable at target_day; no centred pivots/future bars."""
    coverage, eligible, rows = [], [], []
    target = pd.Timestamp(target_day).normalize()
    for _, u in universe.iterrows():
        c = str(u['コード']); x = prices.get(c + '.T', pd.DataFrame())
        status = '取得不可'
        if not x.empty:
            x = x.loc[x.index <= target]
            status = '日足更新待ち' if x.empty or x.index.max() != target else '履歴不足' if len(x) < 60 else '確認済'
        coverage.append({'コード': c, '銘柄名': u['銘柄名'], '状態': status})
        if status == '確認済':
            eligible.append((float(x['Turnover'].tail(20).mean()), c, u['銘柄名'], x))
    cov = pd.DataFrame(coverage)
    complete = len(eligible) == len(universe) and len(eligible) >= 300
    eligible.sort(key=lambda r: (-r[0], r[1]))
    # Missing stocks may change the true TOP300: publish explicitly provisional research.
    if len(eligible) < 300:
        return pd.DataFrame(), pd.DataFrame(), cov, False
    benchmark = prices.get('1306.T', pd.DataFrame())
    if not benchmark.empty:
        benchmark = benchmark.loc[benchmark.index <= target]
    bench_return = np.nan
    if len(benchmark) >= 21 and benchmark.index.max() == target:
        bench_return = (benchmark.Close.iloc[-1] / benchmark.Close.iloc[-21] - 1) * 100
    labels = dict(zip(valuations['コード'].astype(str), valuations['割安判定'])) if not valuations.empty and {'コード', '割安判定'}.issubset(valuations.columns) else {}
    for turnover_rank, (turnover, c, nm, x) in enumerate(eligible[:300], 1):
        close, low, high = x.Close, x.Low, x.High
        bottom_pos = len(x) - 20 + int(np.argmin(low.tail(20).to_numpy()))
        elapsed = len(x) - 1 - bottom_pos
        bottom = float(low.iloc[bottom_pos])
        stopped = elapsed >= 3
        # Two separated 3-session low zones AFTER the identified bottom.
        higher_low = elapsed >= 6 and float(low.iloc[-3:].min()) > float(low.iloc[-6:-3].min())
        ma5 = close.rolling(5).mean(); ma25 = close.rolling(25).mean()
        break_high = close.iloc[-1] > high.iloc[-6:-1].max()
        ma_up = ma5.iloc[-1] > ma5.iloc[-2] and close.iloc[-1] > ma5.iloc[-1]
        delta = close.diff(); gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss == 0), 50)
        rsi_up = rsi.iloc[-1] > rsi.iloc[-2] and rsi.iloc[-1] >= 40
        volume_ratio = float(x.Volume.iloc[-1] / x.Volume.iloc[-21:-1].mean()) if x.Volume.iloc[-21:-1].mean() > 0 else 0
        rebound = float((close.iloc[-1] / bottom - 1) * 100)
        ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
        rs = ret20 - bench_return
        score = 20 * stopped + 20 * higher_low + 15 * break_high + 10 * ma_up + 5 * rsi_up + (20 if volume_ratio >= 1.5 else 10 if volume_ratio >= 1 else 0) + (10 if np.isfinite(rs) and rs > 0 else 0)
        label = str(labels.get(c, '未確認（研究対象のみ取得）'))
        reasons = []
        if not stopped: reasons.append('底から3営業日未経過')
        if not higher_low: reasons.append('安値切上げ未確認')
        if not break_high: reasons.append('直前5日高値未突破')
        if not ma_up: reasons.append('5日線の上向き・終値回復未確認')
        if rebound > 12: reasons.append('底から12%超上昇・追いかけ見送り')
        if '割高' in label: reasons.append('割高判定')
        if not any(v in label for v in ['割安', '適正']): reasons.append('財務評価未確認・評価対象外')
        confirmed = stopped and higher_low and break_high and ma_up
        stage = '上昇転換確認' if confirmed else '底打ち監視' if stopped else '条件外'
        if confirmed and reasons: stage = '上昇転換・購入検討保留'
        rows.append({'コード': c, '銘柄名': nm, '研究判定': stage, '研究点': int(score), '売買代金順位': turnover_rank,
                     '20日平均売買代金_概算': turnover, '現在株価': float(x.RawClose.iloc[-1]), '銘柄判定': label,
                     '底から営業日数': elapsed, '底から上昇率%': round(rebound, 2), '出来高倍率': round(volume_ratio, 2),
                     'RSI14': round(float(rsi.iloc[-1]), 2), '対TOPIX連動ETF20日超過率%': round(rs, 2),
                     '底打ち否定ライン_参考': round(bottom * float(x.RawClose.iloc[-1] / close.iloc[-1]), 2),
                     '選定根拠': '／'.join(v for b, v in [(stopped,'安値更新停止'), (higher_low,'安値切上げ'), (break_high,'5日高値突破'), (ma_up,'5日線上向き'), (rsi_up,'RSI改善'), (volume_ratio >= 1.5,'出来高増加')] if b),
                     '条件_安値更新停止': '○' if stopped else '×',
                     '条件_安値切上げ': '○' if higher_low else '×',
                     '条件_5日高値突破': '○' if break_high else '×',
                     '条件_5日線改善': '○' if ma_up else '×',
                     '条件_RSI改善': '○' if rsi_up else '×',
                     '保留・条件外理由': '／'.join(reasons), '日足基準日': str(target.date())})
    audit = pd.DataFrame(rows).sort_values(['研究点', '売買代金順位'], ascending=[False, True]).reset_index(drop=True)
    ranking = audit[audit['研究判定'].eq('上昇転換確認')].copy().reset_index(drop=True)
    ranking.insert(0, '順位', np.arange(1, len(ranking) + 1))
    scope = '確定TOP300' if complete else '暫定TOP300（未確認銘柄あり）'
    audit['母集団確認'] = scope
    ranking['母集団確認'] = scope
    return ranking, audit, cov, complete


def bottom_price_context(frame, target_day):
    """Adjusted patterns converted to the latest raw-price scale; reject invalid bars."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    x = frame.copy()
    x.index = pd.DatetimeIndex(pd.to_datetime(x.index)).tz_localize(None).normalize()
    x = x[~x.index.duplicated(keep='last')].sort_index().loc[:pd.Timestamp(target_day)]
    needed = ['Open', 'High', 'Low', 'Close', 'Volume', 'RawClose']
    if not set(needed).issubset(x.columns) or len(x) < 60:
        return pd.DataFrame()
    x[needed] = x[needed].apply(pd.to_numeric, errors='coerce')
    if (not np.isfinite(x[needed].tail(60).to_numpy()).all()
            or (x[['Open', 'High', 'Low', 'Close', 'RawClose']].tail(60) <= 0).any().any()
            or (x.Volume.tail(60) < 0).any()
            or (x.High.tail(60) < x[['Open', 'Close', 'Low']].tail(60).max(axis=1)).any()
            or (x.Low.tail(60) > x[['Open', 'Close', 'High']].tail(60).min(axis=1)).any()):
        return pd.DataFrame()
    scale = float(x.RawClose.iloc[-1] / x.Close.iloc[-1])
    x[['Open', 'High', 'Low', 'Close']] *= scale
    return x


def bottom_trade_plan(history, entry):
    """Frozen plan from bars known BEFORE entry; never lower a stop using later lows."""
    if len(history) < 21 or not np.isfinite(entry) or entry <= 0:
        return None
    tr = pd.concat([history.High - history.Low,
                    (history.High - history.Close.shift()).abs(),
                    (history.Low - history.Close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    if not np.isfinite(atr) or atr <= 0:
        return None
    bottom = float(history.Low.tail(20).min())
    stop = reference_tick_round(max(entry * .93, bottom - atr * .25), upward=True)
    if not 0 < stop < entry:
        return None
    risk = entry - stop
    # Use observed overhead resistance, or at most three ATR of room when none exists.
    overhead = history.High.tail(60)
    overhead = overhead[overhead > entry]
    ceiling = min(entry + atr * 3, float(overhead.max())) if not overhead.empty else entry + atr * 3
    target = reference_tick_round(entry + risk * 2, upward=True)
    rr = (target - entry) / risk
    limit = reference_tick_round(min(entry * 1.02, (ceiling + 2 * stop) / 3), upward=False)
    return {'買い指値_参考': entry, '買付上限_参考': limit,
            '利確指値_参考': target, '損切り逆指値_参考': stop,
            '損切り幅%': round(risk / entry * 100, 2), '損益比': round(rr, 2),
            '上値余地_参考': round(ceiling, 2), 'ATR14': round(atr, 2),
            '底打ち否定ライン_固定': bottom,
            '注文条件適合': bool(risk / entry >= .01 and target <= ceiling and limit >= entry)}


def build_bottom_trade_decisions(audit, prices, target_day, valuations, holdings=None, registrations=None):
    """Independent deterministic analysis: no Stoch signals, live guards or priorities."""
    holdings, registrations = holdings or {}, registrations or {}
    target_day = pd.Timestamp(target_day).normalize()
    rows = []
    labels = dict(zip(valuations['コード'].astype(str), valuations['割安判定'])) if isinstance(valuations, pd.DataFrame) and {'コード', '割安判定'}.issubset(valuations.columns) else {}
    records = {str(r['コード']): r for r in audit.to_dict('records')} if isinstance(audit, pd.DataFrame) and not audit.empty else {}
    for c in registrations:
        if c in holdings and c not in records:
            records[c] = {'コード': c, '銘柄名': holdings[c].get('name', c), '研究点': np.nan}
    for c, r in records.items():
        held = c in holdings
        row = {'コード': c, '銘柄名': r.get('銘柄名', c), '独立売買判定': '見送り',
               '判定理由': '', '研究点': r.get('研究点', np.nan), '日足基準日': str(target_day.date()),
               '銘柄判定': str(labels.get(c, r.get('銘柄判定', '未確認')))}
        x = bottom_price_context(prices.get(c + '.T'), target_day)
        if x.empty or x.index[-1] != target_day:
            row.update(独立売買判定='判定保留', 判定理由='基準日の確定日足・60日履歴・価格整合性を確認できず')
            rows.append(row)
            continue
        px = float(x.Close.iloc[-1])
        row['現在株価'] = px
        if held:
            if c not in registrations:
                row.update(独立売買判定='対象外', 判定理由='保有中。この独立方式の保有として未指定')
            else:
                h = holdings[c]
                avg = safe_float(h.get('avg_price'))
                qty = safe_float(h.get('shares'), 0)
                day = pd.to_datetime(registrations[c], errors='coerce')
                if pd.isna(day) or day.normalize() > target_day or qty <= 0:
                    row.update(独立売買判定='判定保留', 判定理由='購入日・保有株数を確認してください')
                else:
                    plan = bottom_trade_plan(x.loc[x.index < day.normalize()], avg)
                    if plan is None:
                        row.update(独立売買判定='判定保留', 判定理由='購入前21日以上の日足・取得単価・損切り価格を確認できず')
                    else:
                        row.update(plan)
                        row.update({'保有株数': int(qty), '取得単価': avg, '購入日': str(day.date()),
                                    '損益率%': round((px / avg - 1) * 100, 2)})
                        after = x.loc[x.index >= day.normalize()]
                        ma10 = x.Close.rolling(10).mean()
                        weakness = len(after) >= 2 and bool((x.Close < ma10).tail(2).all())
                        reason = ('損切りライン以下の終値' if px <= plan['損切り逆指値_参考'] else
                                  '固定利確目標以上の終値' if px >= plan['利確指値_参考'] else
                                  '底打ち否定ライン割れ' if px < plan['底打ち否定ライン_固定'] else
                                  '10日線を終値で2日連続下回る' if weakness else '')
                        row.update(独立売買判定='売り' if reason else '保有継続',
                                   判定理由=reason or '損切り・利確・上昇失速の売り条件に未該当')
                        row['参考売却株数'] = int(qty) if reason else 0
            rows.append(row)
            continue
        entry = reference_tick_round(px, upward=False)
        plan = bottom_trade_plan(x, entry)
        reasons = []
        stage = str(r.get('研究判定', ''))
        if not stage.startswith('上昇転換'):
            reasons.append('底打ち・高値突破・5日線改善の確認待ち')
        if safe_float(r.get('底から上昇率%'), 999) > 12:
            reasons.append('底から12%超上昇')
        if safe_float(r.get('出来高倍率'), 0) < 1.2:
            reasons.append('出来高が前20日平均の1.2倍未満')
        if safe_float(r.get('研究点'), 0) < 75:
            reasons.append('研究点75点未満')
        label = row['銘柄判定']
        if '割高' in label:
            reasons.append('割高判定')
        elif not any(v in label for v in ['割安', '適正']):
            reasons.append('財務評価未確認')
        if plan:
            row.update(plan)
            if not plan['注文条件適合']:
                reasons.append('損切り幅1%以上・7%以内／上値余地2Rの条件未達')
        else:
            reasons.append('有効な利確・損切り価格を計算できず')
        ready = stage.startswith('上昇転換')
        verdict = '買い' if not reasons else '買い待ち' if not ready and not any(v in '／'.join(reasons) for v in ['割高', '財務', '12%']) else '見送り'
        row.update(独立売買判定=verdict, 判定理由='／'.join(reasons) or '上昇転換・出来高増加・財務・上値余地2Rを確認')
        rows.append(row)
    return pd.DataFrame(rows)


def render_bottom_trade_decisions(result, valuations, holdings, verified):
    st.subheader('底打ち・上昇転換 独立売買判定')
    st.caption('AI設計の規則式による分析です。ストキャスティクスは使いません。学習済み予測モデル・勝率ではなく、成績未検証の研究判定です。通常タブの売買条件は維持します。')
    registrations = {}
    if holdings:
        st.caption('この方式で購入した保有だけを指定し、実際の購入日を入力してください。再起動時は再指定が必要です。追加購入・分割で取得単価や株数が変わった場合は注文価格を再確認してください。')
        selected = st.multiselect('独立方式で売り判定する保有銘柄', options=sorted(holdings),
                                 format_func=lambda c: c + ' ' + str(holdings[c].get('name', '')),
                                 key='bottom_independent_held')
        for c in selected:
            registrations[c] = st.date_input(c + ' の購入日', value=None, key='bottom_entry_day_' + c)
        if not verified:
            st.warning('保有CSVの照合が完了していません。売り判定は保留します。')
    frames = result.get('prices', {})
    if not frames:
        st.info('独立判定用の日足を取得するため、研究ランキングを更新してください。')
        return
    table = build_bottom_trade_decisions(result['audit'], frames, result['target'], valuations,
                                        holdings, registrations if verified else {})
    if not verified and not table.empty:
        table.loc[table['コード'].isin(holdings), ['独立売買判定', '判定理由']] = ['判定保留', '保有CSV照合未完了']
    session = latest_tse_session_status()
    fresh = bool(session.get('confirmed')) and str(pd.Timestamp(session['session_date']).date()) == result['target'] if session.get('session_date') is not None else False
    if not fresh and not table.empty:
        table['保存時判定'] = table['独立売買判定']
        table['独立売買判定'] = '判定保留'
        table['判定理由'] = '研究結果の基準日が最新確定日と一致しないか、営業日未確認。研究ランキングを更新してください'
        st.warning('保存結果の鮮度を確認できないため、売買判定を保留しています。')
    if not table.empty:
        audit = result.get('audit', pd.DataFrame())
        condition_cols = [c for c in audit.columns if c.startswith('条件_')]
        if condition_cols and 'コード' in audit:
            table = table.merge(audit[['コード'] + condition_cols].drop_duplicates('コード'), on='コード', how='left')
        table['条件_上昇転換'] = table['コード'].map(audit.set_index('コード')['研究判定'].to_dict()) if not audit.empty and '研究判定' in audit else '未確認'
        for col, values, threshold, upper in [
                ('条件_研究点75以上', table['研究点'], 75, False),
                ('条件_出来高1.2倍以上', table['コード'].map(audit.set_index('コード')['出来高倍率'].to_dict()) if '出来高倍率' in audit else pd.Series(index=table.index, dtype=float), 1.2, False),
                ('条件_底から12%以内', table['コード'].map(audit.set_index('コード')['底から上昇率%'].to_dict()) if '底から上昇率%' in audit else pd.Series(index=table.index, dtype=float), 12, True)]:
            numeric = pd.to_numeric(values, errors='coerce')
            passed = numeric.le(threshold) if upper else numeric.ge(threshold)
            table[col] = np.where(numeric.isna(), '未確認', np.where(passed, '○', '×'))
        table['条件_割安または適正'] = table['銘柄判定'].map(lambda v: '×' if '割高' in str(v) else '○' if any(t in str(v) for t in ['割安', '適正']) else '未確認')
        table['条件_注文価格適合'] = table.get('注文条件適合', pd.Series(index=table.index, dtype=object)).map({True: '○', False: '×'}).fillna('未確認')
        table['買い待ち理由'] = table['判定理由'].where(table['独立売買判定'].eq('買い待ち'), '')
        table['見送り理由'] = table['判定理由'].where(table['独立売買判定'].isin(['見送り', '判定保留', '対象外']), '')
    result['trades'] = table
    if table.empty:
        st.info('判定対象がありません。日足取得状況を確認してください。')
        return
    leading = ['コード', '銘柄名', '独立売買判定', '判定理由', '現在株価', '買い指値_参考',
               '買付上限_参考', '利確指値_参考', '損切り逆指値_参考', '損切り幅%', '損益比', '銘柄判定']
    columns = [c for c in leading if c in table]
    for title, statuses in [('買い候補', ['買い']), ('売り・保有継続', ['売り', '保有継続', '判定保留'])]:
        st.markdown('**' + title + '**')
        part = table[table['独立売買判定'].isin(statuses)]
        if part.empty:
            st.info('該当なし')
        else:
            show_stock_table(part[[c for c in columns + ['保有株数', '取得単価', '損益率%', '参考売却株数'] if c in part]], use_container_width=True, hide_index=True)
    st.markdown('**全件の条件判定・買い待ち・見送り理由**')
    detail_cols = ['銘柄判定', 'コード', '銘柄名', '独立売買判定', '買い待ち理由', '見送り理由', '判定理由']
    detail_cols += [c for c in table if c.startswith('条件_')]
    detail_cols += [c for c in table if c not in detail_cols]
    show_reference_choice_table(table[detail_cols], candidates=table.loc[table['独立売買判定'].isin(['買い待ち', '見送り']), 'コード'], use_container_width=True, hide_index=True)
    st.caption('翌営業日の参考指値です。買付上限を超える場合は見送り、買い指値で約定しなければ追いかけません。利確2R・損切り最大7%の価格を示します。購入後はCSVの取得単価と購入日前の日足で再計算します。上値余地条件と価格の丸め後に買い可否を再確認します。')
    st.caption('売りは確定終値で判定します。場中に既存OCOが約定したかは日足では判別しません。逆指値後は成行の参考案で、窓開け時の損失は7%を超える場合があります。S株にはIFDOCOを使えません。自動発注は行いません。')
    with st.expander('独立判定の初期ルール'):
        st.write('買い：上昇転換確認＋研究点75以上＋出来高1.2倍以上＋底から12%以内＋割安/適正＋損切り幅1〜7%＋観測上値まで2R以上。研究点にはRSI改善を含みます。')
        st.write('損切り：購入前20日安値−0.25ATRと取得単価×93%の高い方。利確：取得単価＋損切り幅の2倍。売り：終値が損切り/固定底値を割る、利確目標到達、または購入後に10日線を終値で2日連続下回る。購入前データだけで決済ラインを固定し、後日の安値更新では引き下げません。')
        st.write('上値余地：直近60日の取得価格を上回る最高値と取得価格＋3ATRの低い方。閾値は初期案で、過去時点の構成銘柄・財務・翌日約定・費用を含む成績検証は未実施です。')
    st.download_button('独立売買判定CSV', table.to_csv(index=False).encode('utf-8-sig'),
                       'bottom_independent_trades.csv', 'text/csv', key='bottom_independent_csv')


def render_bottom_research(valuations, holdings=None, verified=False):
    st.subheader('底打ち・上昇転換ランキング〈研究〉')
    st.caption('独立売買判定を追加。通常タブの売買・株数には反映しません。研究点は勝率ではありません。確定日足で判定します。')
    st.write('TOPIX全構成銘柄 → 直近20営業日の平均売買代金上位300 → 底打ち・上昇転換を採点。売買代金は終値×出来高の概算です。')
    st.caption('相対評価はTOPIX連動ETF（1306）の調整後20日騰落率と比較。底打ち否定ラインは観測した安値の参考値です。')
    if st.button('TOPIX全体から研究ランキングを更新', key='v172437_bottom_run'):
        st.session_state.pop('v172437_bottom_result', None)
        try:
            with st.spinner('研究専用：TOPIX構成銘柄と6か月日足を確認中。通常更新とは別に数分かかる場合があります…'):
                session = latest_tse_session_status()
                if not session.get('confirmed') or pd.isna(session.get('session_date')):
                    raise ValueError('直近東証日の確定を確認できません。朝または16時以降に再実行してください。')
                target = str(pd.Timestamp(session['session_date']).date())
                universe = discover_topix_stock_universe()
                ts = tuple(dict.fromkeys(universe['ticker'].astype(str).tolist() + ['1306.T'] + [str(c) + '.T' for c in (holdings or {})]))
                started = time.perf_counter()
                frames = download_bottom_research(ts, target)
                # Retry only missing/stale histories; bypass the initial cached failure.
                retry_codes = []
                for code in dict.fromkeys(universe['コード'].astype(str).tolist() + list(holdings or {})):
                    history = frames.get(code + '.T', pd.DataFrame())
                    if history.empty or history.index.max() != pd.Timestamp(target) or len(history) < 60:
                        retry_codes.append(code + '.T')
                if retry_codes:
                    for retry_no in (1, 2):
                        recovered = download_bottom_research(tuple(retry_codes), target, retry_tag=retry_no)
                        frames.update(recovered)
                        retry_codes = [t for t in retry_codes if t not in frames or frames[t].empty
                                       or frames[t].index.max() != pd.Timestamp(target) or len(frames[t]) < 60]
                        if not retry_codes:
                            break
                ranking, audit, coverage, complete = rank_bottom_research(universe, frames, target, valuations)
                st.session_state['v172437_bottom_result'] = {'ranking': ranking, 'audit': audit, 'coverage': coverage,
                    'complete': complete, 'target': target, 'created': tokyo_now().isoformat(),
                    'seconds': round(time.perf_counter() - started, 1), 'universe_count': len(universe),
                    'prices': {t: f for t, f in frames.items() if t == '1306.T' or t[:-2] in set(audit.get('コード', pd.Series(dtype=str)).astype(str)) | set(holdings or {})}}
        except Exception as exc:
            st.error('研究ランキングを更新できませんでした：' + str(exc))
    result = st.session_state.get('v172437_bottom_result')
    if not result:
        st.info('専用ボタンで研究を実行します。通常の判定更新・余力入力では追加取得しません。')
        return
    st.caption(f"日足基準日 {result['target']} ／ 実行日時 {result['created']} ／ 取得処理 {result['seconds']}秒")
    if result['target'] != str(tokyo_now().date()):
        st.caption('保存された研究結果です。基準日を確認してください。朝は前営業日の日足が判定対象です。')
    if not result['complete']:
        good = int(result['coverage']['状態'].eq('確認済').sum())
        st.warning(f"TOPIX {result['universe_count']}銘柄中、確認済み{good}銘柄・未確認{result['universe_count'] - good}銘柄。暫定TOP300として表示します。未確認銘柄の復旧で順位・構成が変わる可能性があります。" if good >= 300 else f"確認済み{good}銘柄のためTOP300を作成できません。300銘柄以上の確認が必要です。")
    render_bottom_trade_decisions(result, valuations, holdings or {}, verified)
    if result['ranking'].empty and not result['audit'].empty:
        st.info('上昇転換と財務条件をすべて満たす研究候補は0件です。')
    elif not result['ranking'].empty:
        show_stock_table(result['ranking'], use_container_width=True, hide_index=True)
    if not result['audit'].empty and '対TOPIX連動ETF20日超過率%' in result['audit'] and result['audit']['対TOPIX連動ETF20日超過率%'].isna().all():
        st.warning('比較用ETFの日足未確認のため、相対強さ10点は未算定です。')
    with st.expander('TOP300全件・監視銘柄・見送り理由', expanded=False):
        audit = result.get('audit')
        if not isinstance(audit, pd.DataFrame) or audit.empty:
            st.info('研究対象の一覧はありません。取得状況を確認して研究ランキングを更新してください。')
        elif not {'研究判定', 'コード'}.issubset(audit.columns):
            st.warning('研究結果に必要な項目が不足しています。研究ランキングを更新してください。AI参考候補は選択できません。')
            show_stock_table(audit, use_container_width=True, hide_index=True)
        else:
            candidates = audit.loc[~audit['研究判定'].eq('上昇転換確認'), 'コード']
            show_reference_choice_table(audit, candidates=candidates, use_container_width=True, hide_index=True)
    with st.expander('構成銘柄の日足取得状況', expanded=False):
        show_stock_table(result['coverage'], use_container_width=True, hide_index=True)
    st.caption('財務は通常更新の取得済み評価を照合。研究対象にしかない銘柄は財務未確認として保留します。全件確認時のみ確定TOP300、欠損時は確認済み銘柄内の暫定TOP300です。')
    for key in ['ranking', 'audit', 'coverage']:
        st.download_button('研究CSV：' + key, result[key].to_csv(index=False).encode('utf-8-sig'), 'bottom_research_' + key + '.csv', 'text/csv', key='bottom_dl_' + key)
    with st.expander('判定ルール・本番採用前の確認', expanded=False):
        st.write('底の形40点（安値更新停止20・安値切上げ20）、上昇転換30点（高値突破15・5日線改善10・RSI改善5）、出来高20点、対ETF強さ10点。')
        st.write('安値切上げは底から6営業日以上経過後、直近3日安値がその前3日安値を上回る条件。底から12%超上昇は購入検討保留。数値は未検証の初期設定です。')
        st.write('本番採用前に、過去時点の構成銘柄・売買代金順位を使い、翌営業日約定・費用込みで現行Stochと比較してください。今回の研究ランキングだけでは優位性を確認できません。')



# ------------------------------------------------------------
# 短期売買と管理者タブ
# ------------------------------------------------------------
with main_tab:
    st.subheader("🟢 今日の買い候補 TOP3")
    if buy_view.empty:
        st.info("監視対象に、現在の買い条件を満たす銘柄はありません。")
    else:
        render_mobile_trade_cards(buy_view, "buy", trend_by_code)
        simple_buy_cols=[c for c in ["急騰予兆判定","割安判定","順位","コード","銘柄名","現在株価","均等配分予算","参考購入株数","注文区分","通常注文参考株数","通常注文概算額","%K","%D","買付可否"] if c in buy_view.columns]
        with st.expander("買い候補の詳細表", expanded=False):
            buy_display = buy_view[simple_buy_cols].rename(columns={"割安判定": "銘柄判定"}).copy()
            show_stock_table(buy_display, use_container_width=True, hide_index=True)
            st.caption("100株単位・参考IFDOCO")
            ifd_table = build_reference_ifdoco(buy_view, ifd_take_profit, 7.0)
            if ifd_table.empty:
                st.info("余力入力後に参考IFDOCOを計算します。" if not order_inputs_ready else "予算・注文余力内で100株買える正式買い候補はありません。")
            else:
                show_stock_table(ifd_table, use_container_width=True, hide_index=True)
                st.caption("買い指値は最新終値の参考値。約定しない場合があります。呼値・値幅・SBIの注文受付を確認し、実際の約定価格に応じて売り条件を見直してください。逆指値売却は成行の参考案で、価格は保証されません。")
    st.caption(f"新規買い：{live_universe_mode}の未保有株 / 売り：現在保有 {len(held_codes)}銘柄 / 企業価値判定：{'割高・判定不能を除外' if signal_first and block_unrated else '割高を除外・判定不能は許可' if signal_first else '参考表示'}")
    st.subheader("買い候補から外れた銘柄・理由")
    _snapshot = buy_view.to_dict('records') if not buy_view.empty else []
    _signature = (st.session_state.get('v177_generated_at', ''), tuple(str(r['コード']) for r in _snapshot))
    if st.session_state.get('v172437_buy_signature') != _signature:
        st.session_state['v172437_previous_buys'] = st.session_state.get('v172437_last_buys', [])
        st.session_state['v172437_last_buys'] = _snapshot
        st.session_state['v172437_buy_signature'] = _signature
    excluded_buy_df = build_buy_exclusions(signal_audit_df, buy_view, held_codes,
                                           st.session_state.get('v172437_previous_buys', []))
    if excluded_buy_df.empty:
        st.info("今回の買いサインから見送った銘柄はありません。")
    else:
        show_reference_choice_table(excluded_buy_df, context=signal_audit_df, use_container_width=True, hide_index=True)
    st.caption("今回の買いサインの見送り、およびこの起動中に直前の買い候補から外れた銘柄です。再起動前の候補履歴は含みません。")
    st.subheader("🔻 売り")
    if sell_view.empty:
        st.success("売り候補はありません。")
    else:
        render_mobile_trade_cards(sell_view, "sell", trend_by_code)
        with st.expander("売り候補の詳細表", expanded=False):
            sell_display = sell_view.rename(columns={"割安判定": "銘柄判定"})
            show_stock_table(sell_display, use_container_width=True, hide_index=True)
    render_current_holdings(current_holdings_rows(
        short_confirmed, sell_view, signal_audit_rows, valuation_master_df, data
    ))
    st.subheader("参考株数の計算（候補確認後に入力）")
    st.caption("余力を候補数で均等に分けて参考株数を計算します。入力確定後に自動再計算します。CSVは保有照合に使用します。")
    st.subheader("約定履歴CSV")
    st.file_uploader("SBI 約定履歴CSV", type=["csv"], accept_multiple_files=True,
                     key="sbi_execution_csvs_v177")
    st.subheader("余力入力")
    st.number_input("現物買付余力（円）", min_value=0, max_value=1_000_000_000,
                    value=None, step=1000, key="v172431_buying_power",
                    placeholder="候補確認後に入力")
    if st.button("参考株数を再計算", key="v172434_recalculate"):
        st.rerun()
    if not order_inputs_ready:
        st.info("余力を入力すると参考株数を計算します。未入力は買い条件の不成立を意味しません。")
    if order_inputs_ready and not holdings_verified:
        st.warning("参考株数は保有照合前の暫定値です。CSV未入力・読込エラー・履歴不足の場合、保有株を新規候補から除外できない場合があります。注文前に確認してください。")
    loaded_codes = {code(t) for t in data}
    if set(held_codes) - loaded_codes:
        st.warning("追加取得後も保有銘柄の日足を確認できません。該当銘柄の売り判定は未確認です。『今日の判定を更新』で再確認してください。")

    news_pick_result, news_pick_exclusions, news_pick_signature = render_triple_news_pick(
        indicator_compare_df, daily_bar_status_df, data, held_codes,
        [value_top50_df, signal_value_df], buy_view, signal_audit_df,
        short_buying_power, bool(block_unrated), run_clicked,
    )
    with st.expander("🌱 大化け候補 TOP3（研究）", expanded=False):
        st.caption("数年の成長を調べる候補です。購入株数・売買指示には反映しません。")
        render_multibagger_cards(multibagger_candidates)
    m1, m2 = st.columns(2)
    m1.metric("保有", f"{len(held_codes)}銘柄")
    m2.metric("買付余力", "未入力" if buying_power_input is None else f"¥{int(short_buying_power):,}")
    if isinstance(daily_bar_status_df, pd.DataFrame) and not daily_bar_status_df.empty:
        current_count = int(daily_bar_status_df["状態"].eq("🟢 当日確定").sum())
        total_count = len(daily_bar_status_df)
        target_dates = daily_bar_status_df.loc[
            daily_bar_status_df["直近東証取引日"].ne("確認不可"), "直近東証取引日"
        ]
        target_label = target_dates.mode().iloc[0] if not target_dates.empty else "確認不可"
        if current_count == total_count:
            st.success(f"日足データ：{target_label} 確定（{current_count}/{total_count}銘柄）")
        elif current_count > 0:
            st.warning(f"日足データ：{target_label} 確定 {current_count}/{total_count}銘柄。更新待ち銘柄は判定に注意してください。")
        else:
            st.warning("最新日足は取引中または更新待ちです。確定後にもう一度『今日の判定を更新』を押してください。")
    if run_error:
        st.error(run_error)
    elif not data:
        st.info("「今日の判定を更新」を押してください。")


def build_single_profit_research(price_data, live_audit, budget=500000, costs=0):
    """正式買い通過銘柄のみ。取得済み確定足を使用し、追加通信しない。"""
    rows = []
    if live_audit is None or live_audit.empty:
        return pd.DataFrame()
    eligible = set(live_audit.loc[live_audit["最終買い"].eq(True), "コード"].astype(str))
    for t, frame in (price_data or {}).items():
        if code(t) not in eligible or frame is None or len(frame) < 21:
            continue
        if frame.attrs.get("daily_bar_status") != "🟢 当日確定":
            continue
        x = frame[["High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(x.tail(21).to_numpy()).all():
            continue
        px = float(x["Close"].iloc[-1])
        daily_range = float((x["High"] - x["Low"]).tail(20).mean())
        if not 0 < px <= 5000 or daily_range < 100:
            continue
        entry = reference_tick_round(px, upward=False)
        qty = int(max(0, budget - costs) // (entry * 100)) * 100
        if qty < 100:
            continue
        tr = pd.concat([x["High"] - x["Low"], (x["High"] - x["Close"].shift()).abs(),
                        (x["Low"] - x["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        if not np.isfinite(atr) or atr <= 0:
            continue
        target = reference_tick_round(entry + (10000 + costs) / qty, upward=True)
        stop = reference_tick_round(max(1, entry - atr), upward=False)
        if not stop < entry < target:
            continue
        reward = (target - entry) * qty
        risk = (entry - stop) * qty
        required_atr = (target - entry) / atr
        rr = (reward - costs) / (risk + costs)
        volume_ratio = float(x["Volume"].iloc[-1] / x["Volume"].tail(20).mean()) if x["Volume"].tail(20).mean() > 0 else 0
        change5 = (px / float(x["Close"].iloc[-6]) - 1) * 100
        # 到達距離、損益比、出来高、直近の勢いを明示的に採点。学習モデルや勝率ではない。
        score = (40 * max(0, 1 - required_atr / 2) + 30 * min(rr / 2, 1)
                 + 20 * min(volume_ratio / 2, 1) + 10 * min(max(change5, 0) / 5, 1))
        rows.append({"コード": code(t), "銘柄名": name(t), "日足日": str(frame.index[-1])[:10],
                     "終値": px, "20日平均日中値幅": round(daily_range, 2), "ATR14": round(atr, 2),
                     "参考株数": qty, "IFD買い指値": entry, "OCO利確指値": target,
                     "OCO逆指値発動": stop, "逆指値後": "成行売り", "買い概算額": entry * qty,
                     "未使用予算": budget - entry * qty - costs, "目標粗利益": reward,
                     "費用控除後利益（税引前）": reward - costs, "想定損失（費用込）": risk + costs,
                     "損益比": round(rr, 2), "目標までATR倍率": round(required_atr, 2),
                     "出来高倍率": round(volume_ratio, 2), "5日騰落率%": round(change5, 2),
                     "AI参考スコア": round(score, 2), "到達距離条件": required_atr <= 2})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["到達距離条件", "AI参考スコア", "コード"], ascending=[False, False, True]).reset_index(drop=True)


def render_single_profit_research(price_data, live_audit):
    st.subheader("1銘柄集中・利益1万円を目指す研究")
    st.caption("予算50万円／100株単位／株価5,000円以下／直近20日平均の高値−安値が100円以上。通常の正式買い通過銘柄全件から選定（TOP3に限定しません）。")
    costs = st.number_input("往復手数料・諸費用の想定合計（円）", min_value=0, max_value=100000,
                            value=0, step=100, key="single_profit_costs")
    table = build_single_profit_research(price_data, live_audit, 500000, int(costs))
    st.caption("取得済み日足のみを使用。通常の『今日の判定を更新』で更新されます。50万円は研究用の固定予算で、実際の買付余力とは連動しません。")
    if table.empty:
        st.info("全条件を満たす銘柄はありません。通常の判定更新と日足・財務の取得状況を確認してください。")
        return
    picks = table[table["到達距離条件"]]
    if picks.empty:
        st.info("目標までの上昇幅がATR14の2倍以内となる候補がないため、今回の推奨IFDOCOは見送ります。")
    else:
        r = picks.iloc[0]
        st.success(f"AI参考おすすめ：{r['コード']} {r['銘柄名']}（1銘柄のみ）")
        st.write(f"IFD：買い指値 {r['IFD買い指値']:,.0f}円 × {int(r['参考株数']):,}株")
        st.write(f"OCO：利確指値 {r['OCO利確指値']:,.0f}円 ／ 逆指値 {r['OCO逆指値発動']:,.0f}円以下で成行売り（全株）")
        st.write(f"買い概算 {r['買い概算額']:,.0f}円 ／ 費用控除後の目標利益 {r['費用控除後利益（税引前）']:,.0f}円 ／ 想定損失 {r['想定損失（費用込）']:,.0f}円 ／ 損益比 {r['損益比']:.2f}")
        st.caption(f"選定根拠：目標までATR {r['目標までATR倍率']:.2f}倍、出来高 {r['出来高倍率']:.2f}倍、5日騰落率 {r['5日騰落率%']:.2f}%。日足日 {r['日足日']}。損切りは買い指値からATR14約1本分。")
        if r["損益比"] < 1:
            st.warning("想定損失が目標利益を上回る案です。期待値・勝率は未検証です。")
    show_stock_table(table, use_container_width=True, hide_index=True)
    st.caption("AI参考スコア＝目標への近さ40点・損益比30点・出来高20点・5日上昇率10点の規則式。勝率や利益保証ではありません。到達時期は予測しません。")
    st.warning("利益1万円は税引前です。逆指値の成行売りは窓開けで想定損失を超える場合があります。呼値・制限値幅・実余力・注文期限はSBI画面で確認してください。通常タブの注文と重複させず、約定価格が変わった場合は決済価格を再計算してください。研究タブのATR損切りは本番の終値7%損切りとは別の参考案です。自動発注はしません。")
    st.download_button("1銘柄研究CSV", table.to_csv(index=False).encode("utf-8-sig"),
                       "single_profit_research.csv", "text/csv", key="single_profit_csv")


with single_tab:
    render_single_profit_research(data, signal_audit_df)

with bottom_tab:
    render_bottom_research(valuation_master_df, short_confirmed, holdings_verified)

# 動画2本の文字起こしに基づく独立研究。実売買へは接続しない。
VIDEO_RESEARCH_RULES = pd.DataFrame([
    {"区分":"原動画", "確認できた考え方":"支持帯付近で買い、抵抗帯手前で売る／上向きMA75への押し目と反発／暴落時の大型・高配当・割安株", "実装":"3手法を別々に判定。原文の銘柄例・価格は流用しない"},
    {"区分":"原動画", "確認できた考え方":"市場全体・日足と週足・決算とニュースの影響を確認", "実装":"日経平均、確定週のみの週足、決算・個別悪材料の確認欄"},
    {"区分":"追加動画", "確認できた考え方":"年間・四半期成長、進捗、PER、MA5/25 GC、出来高。全12条件のうち公開は6条件のみ", "実装":"取得済みの範囲を表示。四半期・進捗は未確認と明示。12条件の完全再現ではない"},
    {"区分":"研究用補完", "確認できた考え方":"原動画は具体的な日数・許容幅を指定していない", "実装":"うねり60日・10/90%分位、上下各2回以上（5日間隔）・5～35%幅・端25%内・反発確認"},
    {"区分":"研究用補完", "確認できた考え方":"モメンタムの数値化", "実装":"MA75の5日傾き>0、直近5日でMA75±3%に接触、終値>前日終値かつMA75超、出来高>前20日平均、確定週終値>13週線"},
    {"区分":"研究用補完", "確認できた考え方":"暴落・高配当・大型の数値化", "実装":"日経と個別株の過去取得範囲最大252日高値から15%以上下落、時価総額1兆円以上、配当利回り3%以上、割安・やや割安、反発・IR確認"},
    {"区分":"研究用補完", "確認できた考え方":"売りと資金管理", "実装":"新規参考損切りは支持帯×0.99と終値×0.93の高い方。利益幅/損失幅>=2。モメンタムの利確目安はMA75×1.10。呼値調整前"},
    {"区分":"運用差", "確認できた考え方":"追加動画は引け直前の判断を提案", "実装":"このアプリでは直近確定日足を翌営業日朝に確認。引け直前運用と同じ成績とは扱わない。本番の終値7%損切りは変更しない"},
])


def video_price_frame(frame):
    if not isinstance(frame, pd.DataFrame) or not {"Close", "High", "Low", "Volume"}.issubset(frame.columns):
        return pd.DataFrame()
    x = frame.copy()
    x.index = pd.to_datetime(x.index, errors="coerce")
    x = x.loc[~x.index.isna()]
    if x.index.tz is not None:
        x.index = x.index.tz_convert("Asia/Tokyo").tz_localize(None)
    x.index = x.index.normalize()
    x = x[~x.index.duplicated(keep="last")].sort_index()
    for k in ("Close", "High", "Low", "Volume", "MA75"):
        if k in x: x[k] = pd.to_numeric(x[k], errors="coerce")
    x = x.dropna(subset=["Close", "High", "Low", "Volume"])
    x = x[(x.Close > 0) & (x.Low > 0) & (x.High >= x.Low) & (x.Volume >= 0)]
    if "MA75" not in x: x["MA75"] = x.Close.rolling(75).mean()
    return x


def video_reference_day(freshness):
    if not isinstance(freshness, pd.DataFrame) or "直近東証取引日" not in freshness:
        return pd.NaT
    days = pd.to_datetime(freshness["直近東証取引日"], errors="coerce").dropna()
    return pd.Timestamp(days.max()).normalize() if len(days) else pd.NaT


def video_market_context(market, target):
    base = {"状態":"未確認", "上昇":False, "暴落":False, "日付":"", "下落率%":np.nan, "確認日数":0}
    if not isinstance(market, pd.DataFrame) or market.empty or pd.isna(target) or "Close" not in market:
        return base
    x = market.copy()
    x.index = pd.to_datetime(x.index)
    if x.index.tz is not None: x.index = x.index.tz_localize(None)
    x = x.sort_index().loc[:target]
    if x.empty or x.index[-1].normalize() != target or len(x) < 80: return base
    c = pd.to_numeric(x.Close, errors="coerce")
    ma = pd.to_numeric(x.MA75, errors="coerce") if "MA75" in x else c.rolling(75).mean()
    if not np.isfinite(c.iloc[-1]) or not np.isfinite(ma.iloc[-6]): return base
    draw = float((c.iloc[-1] / c.tail(252).max() - 1) * 100)
    up = bool(c.iloc[-1] > ma.iloc[-1] > ma.iloc[-6])
    return {"状態":"上昇" if up else "暴落圏" if draw <= -15 else "中立・下降", "上昇":up, "暴落":draw <= -15,
            "日付":str(target.date()), "下落率%":round(draw, 2), "確認日数":min(len(x),252)}


def video_evidence_map(frame, today):
    """IR確認は自動推測せず、日付・URL付きの明示入力だけを扱う。"""
    result = {}
    if not isinstance(frame, pd.DataFrame) or "コード" not in frame: return result
    for r in frame.to_dict("records"):
        c = str(code(str(r.get("コード", ""))))
        day = pd.to_datetime(r.get("確認日", ""), errors="coerce")
        valid = pd.notna(day) and 0 <= (pd.Timestamp(today).normalize() - pd.Timestamp(day.date())).days <= 7
        urls = str(r.get("根拠URL", "")).strip()
        url_ok = urls.startswith("https://") or urls.startswith("http://")
        result[c] = {**r, "有効":bool(valid and url_ok)}
    return result


def build_video_research(prices, freshness, valuations, finance, market, holdings, evidence, today):
    target = video_reference_day(freshness)
    context = video_market_context(market, target)
    fresh = {str(code(str(r["コード"]))):r for r in freshness.to_dict("records")} if isinstance(freshness,pd.DataFrame) and "コード" in freshness else {}
    values = {str(code(str(r["コード"]))):str(r.get("割安判定", "算定不能")) for r in valuations.to_dict("records")} if isinstance(valuations,pd.DataFrame) and "コード" in valuations else {}
    fins = {str(code(str(r["コード"]))):r for r in finance.to_dict("records")} if isinstance(finance,pd.DataFrame) and "コード" in finance else {}
    evs = video_evidence_map(evidence, today)
    rows = []
    for ticker, frame in prices.items():
        c = str(code(str(ticker)))
        x = video_price_frame(frame)
        if x.empty: continue
        if pd.notna(target): x = x.loc[:target]
        if x.empty: continue
        px = float(x.Close.iloc[-1]); prev = float(x.Close.iloc[-2]) if len(x)>1 else px
        label = values.get(c, "算定不能"); f = fins.get(c, {}); ev = evs.get(c, {})
        blockers = []
        if pd.isna(target) or x.index[-1] != target or fresh.get(c,{}).get("状態") != "🟢 当日確定": blockers.append("最新確定日足未確認")
        if (x.Close.pct_change().tail(60).abs() > .4).any(): blockers.append("価格断絶・分割等の確認待ち")
        if "割高" in label: blockers.append("割高除外")
        if len(x)<21 or x.Volume.iloc[-1]<=0 or x.Volume.iloc[-21:-1].mean()<=0: blockers.append("出来高・流動性未確認")
        if label not in ("割安", "やや割安", "適正", "妥当") and not any(z in label for z in ("割安", "適正", "妥当")): blockers.append("企業価値未確認")
        recent = x.iloc[-61:-1]
        support = float(recent.Low.quantile(.10)) if len(recent)>=60 else np.nan
        resist = float(recent.High.quantile(.90)) if len(recent)>=60 else np.nan
        width = resist/support-1 if np.isfinite(support) and support>0 else np.nan
        rebound = px > prev
        ma = x.MA75; mav = safe_float(ma.iloc[-1]); slope = bool(len(x)>=6 and np.isfinite(ma.iloc[-6]) and mav>ma.iloc[-6])
        volbase = x.Volume.iloc[-21:-1].mean() if len(x)>=21 else np.nan
        vr = float(x.Volume.iloc[-1]/volbase) if volbase>0 else np.nan
        weekly = x.Close.resample("W-FRI").last().dropna()
        weekly = weekly.loc[weekly.index<=x.index[-1]]  # 未完の当週を除外
        week_up = len(weekly)>=13 and weekly.iloc[-1]>weekly.tail(13).mean()
        wlabel = "上向き" if week_up else "未確認" if len(weekly)<13 else "弱い"
        def touches(series, level, tol):
            indices = np.flatnonzero((series/level-1).abs().to_numpy()<=tol)
            count=0; last=-100
            for i in indices:
                if i-last>=5: count+=1;last=i
            return count
        lo_hits = touches(recent.Low, support, .02) if np.isfinite(support) else 0
        hi_hits = touches(recent.High, resist, .02) if np.isfinite(resist) else 0
        visits=[]
        if np.isfinite(support) and np.isfinite(resist):
            for _,bar in recent.iterrows():
                low_near=abs(bar.Low/support-1)<=.02
                high_near=abs(bar.High/resist-1)<=.02
                side="下" if low_near and not high_near else "上" if high_near and not low_near else ""
                if side and (not visits or visits[-1]!=side): visits.append(side)
        near = bool(np.isfinite(mav) and mav>0 and ((x.Low.tail(5)/ma.tail(5)-1).abs()<=.03).any())
        cap=safe_float(f.get("時価総額")); dy=safe_float(ev.get("配当利回り%"))
        annual_end=pd.to_datetime(str(f.get("成長比較期", "")),errors="coerce")
        annual_prev=pd.to_datetime(str(f.get("成長比較前期", "")),errors="coerce")
        annual_ok = (pd.notna(annual_end) and pd.notna(annual_prev) and (annual_end.year-annual_prev.year)*12+annual_end.month-annual_prev.month==12
                     and 0 <= (pd.Timestamp(today)-annual_end).days <= 580
                     and finance_freshness_usable(f.get("財務情報鮮度", "")))
        rg=safe_float(f.get("売上成長率%")); eg=safe_float(f.get("利益成長率%"))
        yearly="確認" if annual_ok and np.isfinite(rg) and np.isfinite(eg) and rg>0 and eg>0 else "未達" if annual_ok and np.isfinite(rg) and np.isfinite(eg) else "未確認"
        ma5=x.Close.rolling(5).mean();ma25=x.Close.rolling(25).mean()
        gc=bool(len(x)>=26 and ma5.iloc[-1]>ma25.iloc[-1] and ma5.iloc[-2]<=ma25.iloc[-2])
        dd=(px/x.Close.tail(252).max()-1)*100
        schemas = [
            ("うねり", [(len(recent)>=60,"60日履歴不足"),(np.isfinite(width) and .05<=width<=.35,"レンジ幅5～35%外"),(lo_hits>=2 and hi_hits>=2 and len(visits)>=4,"支持・抵抗の反復不足"),
                         (np.isfinite(support) and support<=px<=support+(resist-support)*.25,"支持帯の近くではない"),(rebound,"反発待ち")], support, resist*.99 if np.isfinite(resist) else np.nan),
            ("モメンタム",[(context["上昇"],"市場の上昇未確認"),(slope and px>mav,"MA75上向き・上抜け未達"),(near,"MA75付近への押し目待ち"),(rebound,"反発待ち"),(np.isfinite(vr) and vr>1,"出来高増加未確認"),(week_up,"確定週の上昇未確認")], min(float(x.Low.tail(5).min()),mav) if np.isfinite(mav) else np.nan, mav*1.10),
            ("暴落・高配当割安",[(context["暴落"],"市場の15%下落未確認"),(dd<=-15,"個別15%下落未達"),(np.isfinite(cap) and cap>=1e12,"時価総額1兆円未確認"),(np.isfinite(dy) and dy>=3,"配当利回り3%以上未確認"),
                         ("割安" in label and "未" not in label and "不能" not in label,"割安未確認"),(bool(ev.get("有効")) and str(ev.get("長期業績・財務健全性", ""))=="確認済","長期業績・財務のIR確認待ち"),
                         (bool(ev.get("有効")) and str(ev.get("個別悪材料", ""))=="問題なし確認済","個別悪材料の確認待ち"),(rebound,"反発待ち")],float(x.Low.tail(20).min()),float(x.Close.tail(252).max()))]
        for strategy, checks, floor, take in schemas:
            missing=blockers+[msg for ok,msg in checks if not ok]
            stop=max(px*.93,floor*.99) if np.isfinite(floor) else np.nan
            rr=(take-px)/(px-stop) if np.isfinite(take) and np.isfinite(stop) and stop<px<take else np.nan
            if not np.isfinite(rr) or rr<2: missing.append("利益幅/損失幅2倍未達")
            held=holdings.get(c,{}) if isinstance(holdings,dict) else {}
            status="見送り" if missing else "研究候補（注文前IR確認）"
            if held:
                avg=safe_float(held.get("avg_price"))
                if any(b in blockers for b in ("最新確定日足未確認","価格断絶・分割等の確認待ち")): status="保有判定保留"
                elif np.isfinite(avg) and avg>0 and px<=avg*.93: status="保有：7%損切り条件"
                elif strategy=="うねり" and np.isfinite(resist) and px>=resist*.99: status="保有：抵抗帯で利確検討"
                elif strategy=="うねり" and np.isfinite(support) and px<support*.99: status="保有：支持帯割れ撤退検討"
                elif strategy=="モメンタム" and np.isfinite(mav) and (px<mav or not slope): status="保有：トレンド悪化・撤退検討"
                elif strategy=="モメンタム" and px>=take: status="保有：MA75乖離で利確検討"
                else: status="保有：研究継続（取得時のルール優先）"
            rows.append({"銘柄判定":label,"手法":strategy,"研究判定":status,"コード":c,"銘柄名":name(ticker),"終値":px,"基準日":str(x.index[-1].date()),
                         "条件通過数":sum(bool(ok) for ok,msg in checks),"条件数":len(checks),"見送り理由":"／".join(dict.fromkeys(missing)),
                         "支持帯":support,"抵抗帯":resist,"MA75":mav,"出来高倍率":vr,"週足":wlabel,"最大252日確認数":min(len(x),252),"高値から下落率%":dd,
                         "新規参考損切り":stop,"新規参考利確":take,"利益幅/損失幅":rr,"年間増収増益":yearly,"四半期増収増益":"未確認","進捗率":"未確認",
                         "MA5/25当日GC":"点灯" if gc else "なし","予想PER":safe_float(f.get("予想PER")),"配当利回り%":dy,
                         "財務基準日":f.get("参考指標基準日",""),"財務情報鮮度":f.get("財務情報鮮度",""),"財務出典":f.get("財務参照URL",""),"IR根拠":ev.get("根拠URL","")})
    return pd.DataFrame(rows),context


def annotate_video_leaders(table):
    """各手法の全条件通過銘柄だけを比較。表示・CSV専用で注文には使わない。"""
    out = table.copy()
    out["有力銘柄"] = ""
    out["選定根拠"] = ""
    if out.empty:
        return out
    for strategy, part in out.groupby("手法", sort=False):
        pool = part[part["研究判定"].eq("研究候補（注文前IR確認）") & part["見送り理由"].fillna("").eq("")].copy()
        if pool.empty:
            continue
        # 低い損切り幅だけでRRが過大になる銘柄を優先しない。
        pool["_value"] = pool["銘柄判定"].map(valuation_display_rank)
        pool["_rr"] = pd.to_numeric(pool["利益幅/損失幅"], errors="coerce").clip(upper=5).fillna(-1)
        pool["_volume"] = pd.to_numeric(pool["出来高倍率"], errors="coerce").clip(upper=3).fillna(-1)
        pool["_code"] = pool["コード"].astype(str)
        row = pool.sort_values(["_value", "_rr", "_volume", "_code"], ascending=[True, False, False, True], kind="stable").iloc[0]
        evidence = {
            "うねり": "支持・抵抗の反復、支持帯付近で反発を確認",
            "モメンタム": "市場上昇、上向きMA75への押し目反発、出来高増・確定週上昇を確認",
            "暴落・高配当割安": "市場・個別15%以上下落、大型・配当3%以上・割安、入力済みIR確認条件を通過",
        }[strategy]
        reason = (f"{evidence}／条件{int(row['条件通過数'])}/{int(row['条件数'])}通過"
                  f"／{row['銘柄判定']}／利益幅÷損失幅{float(row['利益幅/損失幅']):.2f}倍"
                  f"／出来高{float(row['出来高倍率']):.2f}倍／基準日{row['基準日']}")
        out.loc[row.name, "有力銘柄"] = "★ 最有力（研究用）"
        out.loc[row.name, "選定根拠"] = reason
    return out


def show_video_leader_table(frame):
    display = prepare_stock_table(frame)
    cols = ["銘柄判定", "有力銘柄", "コード", "銘柄名", "選定根拠"]
    display = display[[c for c in cols if c in display] + [c for c in display if c not in cols]]
    styled = display.style.apply(
        lambda row: ["background-color: #fff2b3; color: #17202a; font-weight: 700" if row.get("有力銘柄", "") else ""] * len(row), axis=1)
    st.dataframe(styled, hide_index=True, use_container_width=True)


def video_position_plan(row, cash, risk):
    px=safe_float(row.get("終値"));stop=safe_float(row.get("新規参考損切り"));take=safe_float(row.get("新規参考利確"))
    if not all(np.isfinite(v) for v in (px,stop,take)) or not 0<stop<px<take or cash<=0 or risk<=0: return None
    qty=int(min(cash/px,risk/(px-stop))//100)*100
    if qty<100: return None
    return {"コード":row["コード"],"手法":row["手法"],"参考株数":qty,"参考価格":px,"必要資金":round(qty*px,2),"想定損失":round(qty*(px-stop),2),"想定利益":round(qty*(take-px),2),"参考損切り":stop,"参考利確":take}


def render_video_research():
    st.subheader("動画式AI投資研究")
    st.caption("元動画の3手法と追加動画の業績・資金管理を、取得済みデータで比較します。生成AIの予測や動画手法の完全再現ではなく、条件に基づく研究です。")
    st.info("朝の運用向けに前営業日までの確定日足を使います。候補は本番の買い候補・配分へ自動追加されません。過去成績は未検証です。")
    with st.expander("動画の要点・補完条件・運用の違い"):
        show_stock_table(VIDEO_RESEARCH_RULES,hide_index=True,use_container_width=True)
        st.link_button("元動画", "https://youtu.be/TR0rURA4ySo")
        st.caption("週足は取得範囲から作成し金曜日ラベルが基準日以下の確定週のみ使用。動画の2年以上の長期確認は履歴不足の場合未実施です。")
    research_freshness=daily_bar_status_df.copy()
    now=tokyo_now()
    expected=pd.Timestamp(now.date()) - pd.Timedelta(days=0 if now.hour>=16 else 1)
    while expected.weekday()>=5: expected-=pd.Timedelta(days=1)
    target=video_reference_day(research_freshness)
    if pd.notna(target) and target!=expected:
        st.warning("保存日足の基準日が現在の営業日目安と一致しません。『今日の判定を更新』を実行してください。祝日等で確認できない場合も研究候補は保留します。")
        research_freshness["直近東証取引日"]=str(expected.date())
        research_freshness["状態"]="更新確認待ち"
        target=expected
    if st.button("研究用の日経平均を確認・更新",key="video_market_refresh"):
        if pd.isna(target): st.warning("先に『今日の判定を更新』で確定日足の基準日を取得してください。")
        else:
            try:
                with st.spinner("研究用の日経平均を確認中…"):
                    st.session_state["video_market_frame"]=market_data(target)
            except Exception as exc:
                st.session_state.pop("video_market_frame",None)
                st.warning("市場データを確認できません："+type(exc).__name__)
    with st.expander("暴落時の配当・IR確認CSV（任意）"):
        st.caption("根拠URLと7日以内の確認日を記録。配当利回りは%単位。最新の減配・下方修正・個別悪材料と長期業績・財務を公式IRで確認した場合のみ指定語を記入してください。URLの内容を自動検証する機能ではありません。")
        template=pd.DataFrame([{"コード":"", "確認日":"", "配当利回り%":"", "長期業績・財務健全性":"未確認", "個別悪材料":"未確認", "根拠URL":""}])
        st.code("長期業績・財務健全性：確認済\n個別悪材料：問題なし確認済\n確認日：YYYY-MM-DD")
        st.download_button("IR確認CSVひな形",template.to_csv(index=False).encode("utf-8-sig"),"video_ir_template.csv",key="video_ir_template")
        upload=st.file_uploader("IR確認CSV",type=["csv"],key="video_ir_csv")
        evidence=pd.DataFrame()
        if upload is not None:
            try:
                evidence=pd.read_csv(upload,dtype={"コード":str},encoding="utf-8-sig").fillna("")
                if not set(template.columns).issubset(evidence.columns):
                    st.warning("ひな形の列が不足しています。IR情報は採用しません。")
                    evidence=pd.DataFrame()
            except Exception:
                st.warning("IR確認CSVを読み込めません。UTF-8のひな形を使ってください。")
    table,context=build_video_research(data,research_freshness,valuation_master_df,mb_finance,st.session_state.get("video_market_frame"),short_confirmed,evidence,tokyo_now().date())
    st.write("市場："+context["状態"]+" ／ 基準日："+(context["日付"] or "未確認"))
    if context["確認日数"]: st.caption(f"市場の高値比較は取得済み{context['確認日数']}日。下落率 {context['下落率%']:.2f}%")
    if table.empty:
        st.info("NO DATA：先に今日の判定を更新してください。")
        return
    st.caption(f"調査範囲 {table['コード'].nunique()}銘柄。現在取得済みの範囲のみで、日本株全体を網羅した結果ではありません。")
    table=annotate_video_leaders(table)
    st.caption("★黄色の行は手法ごとの最有力研究候補。全条件通過銘柄から、銘柄判定→利益幅/損失幅（比較上限5倍）→出来高倍率（比較上限3倍）→同点はコード順で1銘柄を選びます。予測AIによる上昇確率ではありません。")
    tabs=st.tabs(["うねり","モメンタム","暴落・高配当割安","業績6項目・確認事項"])
    for panel,strategy in zip(tabs[:3],["うねり","モメンタム","暴落・高配当割安"]):
        with panel:
            part=table[table["手法"].eq(strategy)].copy()
            candidates=part[part["研究判定"].eq("研究候補（注文前IR確認）")]
            st.write(f"研究候補 {len(candidates)}銘柄")
            if candidates.empty: st.info("最有力：該当なし。条件を満たす候補がありません。下の一覧で待ち条件を確認できます。")
            else:
                leader=candidates[candidates["有力銘柄"].ne("")]
                if not leader.empty:
                    row=leader.iloc[0]
                    st.info(f"★ 最有力：{row['銘柄名']}（{row['コード']}）")
                    st.write("選定根拠："+row["選定根拠"])
                show_video_leader_table(candidates)
            held=part[part["研究判定"].str.startswith("保有")]
            if not held.empty:
                st.caption("現在保有をこの手法で比較した参考結果。購入時の採用手法を記録したものではありません。")
                show_stock_table(held,hide_index=True,use_container_width=True)
            with st.expander("全銘柄の条件・見送り理由"):
                show_video_leader_table(part)
    with tabs[3]:
        st.info("この欄の最有力選定は保留：四半期増収増益・進捗率が未確認で、動画の全12条件も公開されていません。3手法の最有力は各タブで確認できます。")
        cols=["銘柄判定","コード","銘柄名","年間増収増益","四半期増収増益","進捗率","予想PER","MA5/25当日GC","出来高倍率","財務基準日","財務情報鮮度","財務出典"]
        show_stock_table(table.drop_duplicates("コード")[cols],hide_index=True,use_container_width=True)
        st.caption("年間成長率は比較期が12か月差の場合だけ表示判定。財務取得の鮮度は別列で確認。PERの絶対値だけで割安とはせず共通銘柄判定を参照。四半期と進捗は現在のパイプラインに十分な情報がないため未確認。")
        st.code("選んだ銘柄の最新公式IR・適時開示を確認し、出典URL・発表日を付けて、総合判定／根拠／見通し／注目点を整理してください。売上と利益の前年同期比、通期進捗、利益の使途、下方修正・減配・希薄化を確認し、事実と推論を分け、不明は未確認としてください。ニュースは一時的要因と構造的問題を区別してください。")
    st.caption("候補の資金管理：余力と許容損失から100株単位で逆算。各案は単独での計算です。複数案を同時に注文しないでください。")
    cash=st.number_input("研究に使う余力（円）",min_value=0,value=int(buying_power),step=1000,key="video_cash")
    risk=st.number_input("1取引の許容損失（円）",min_value=0,value=0,step=1000,key="video_risk")
    picks=table[table["研究判定"].eq("研究候補（注文前IR確認）")]
    plans=[plan for row in picks.to_dict("records") if (plan:=video_position_plan(row,cash,risk)) is not None]
    if plans: show_stock_table(pd.DataFrame(plans),hide_index=True,use_container_width=True)
    elif len(picks): st.info("余力・許容損失を設定し、100株を買える範囲か確認してください。")
    st.warning("参考価格は直近終値で、翌朝の約定価格・注文可能価格ではありません。窓開けや滑りで許容損失を超えます。注文前にIR、実余力、呼値・制限値幅を確認し、買値が変わった場合は再計算してください。S株では同じ決済注文を使えるとは限りません。本番の売り指示・終値7%損切りを優先します。")
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("video_research_audit.csv",table.to_csv(index=False,encoding="utf-8-sig"))
        z.writestr("video_research_rules.csv",VIDEO_RESEARCH_RULES.to_csv(index=False,encoding="utf-8-sig"))
        z.writestr("video_research_market.csv",pd.DataFrame([context]).to_csv(index=False,encoding="utf-8-sig"))
        z.writestr("video_research_position_plans.csv",pd.DataFrame(plans).to_csv(index=False,encoding="utf-8-sig"))
    st.download_button("動画研究CSV一括ZIP",buf.getvalue(),"video_research.zip","application/zip",key="video_export")


# 資金流入初期研究：公開済み確定日足の代理指標。実際の資金流入・AI利用を測定しない。
def build_flow_research(prices, freshness, valuations, evidence, today):
    target = video_reference_day(freshness)
    now = pd.Timestamp(today)
    if now.tzinfo is not None:
        now = now.tz_convert('Asia/Tokyo').tz_localize(None)
    expected = now.normalize() - pd.Timedelta(days=0 if now.hour >= 16 else 1)
    while expected.weekday() >= 5:
        expected -= pd.Timedelta(days=1)
    fresh = {str(code(str(r['コード']))): r for r in freshness.to_dict('records')} if isinstance(freshness, pd.DataFrame) and 'コード' in freshness else {}
    vals = {str(code(str(r['コード']))): str(r.get('割安判定', r.get('銘柄判定', '算定不能'))) for r in valuations.to_dict('records')} if isinstance(valuations, pd.DataFrame) and 'コード' in valuations else {}
    evs = {}
    if isinstance(evidence, pd.DataFrame) and 'コード' in evidence:
        for r in evidence.to_dict('records'):
            c = str(code(str(r['コード'])))
            day = pd.to_datetime(r.get('確認日', ''), errors='coerce', utc=True)
            valid = pd.notna(day) and 0 <= (now.date() - day.date()).days <= 7
            url = str(r.get('公式IR_URL', '')).strip()
            evs[c] = dict(r, valid=bool(valid and url.startswith('https://') and str(r.get('確認結果', '')) == '確認済' and str(r.get('裏付け内容', '')).strip()))
    rows = []
    for ticker, frame in prices.items():
        c = str(code(str(ticker)))
        x = video_price_frame(frame)
        if pd.notna(target):
            x = x.loc[:min(target, expected)]
        blockers = []
        if pd.isna(target) or target != expected or x.empty or x.index[-1] != target or fresh.get(c, {}).get('状態') != '🟢 当日確定':
            blockers.append('最新確定日足未確認（祝日も要確認）')
        invalid = not x.empty and not np.isfinite(x[['Close','High','Low','Volume']].to_numpy()).all()
        if invalid:
            blockers.append('価格・出来高に非有限値')
        if len(x) < 61 or invalid:
            rows.append({'コード':c, '銘柄名':name(ticker), '銘柄判定':vals.get(c,'算定不能'), '研究判定':'データ不足', '確認事項':'／'.join(blockers + (['日足61本未満'] if len(x) < 61 else [])), '条件通過数':0})
            continue
        last = x.iloc[-1]
        px = float(last.Close)
        if not np.isfinite(x[['Close','High','Low','Volume']].to_numpy()).all() or not ((x.Low <= x.Close) & (x.Close <= x.High)).all():
            blockers.append('価格・出来高の整合性未確認')
        if (x.Close.pct_change().tail(60).abs() > .30).any():
            blockers.append('価格断絶・分割等の確認待ち')
        prev = x.iloc[-21:-1]
        vbase = float(prev.Volume.mean())
        vr = float(last.Volume/vbase) if vbase > 0 else np.nan
        turnover = float((prev.Close * prev.Volume).mean())
        width = float((prev.High.max()/prev.Low.min()-1)*100)
        r5 = float((px/x.Close.iloc[-6]-1)*100)
        r20 = float((px/x.Close.iloc[-21]-1)*100)
        lowup = bool(x.Low.iloc[-5:].min() > x.Low.iloc[-10:-5].min() and px > x.Close.tail(5).mean())
        high10 = float(x.High.iloc[-11:-1].max())
        checks = {
            '条件_出来高1.5倍': bool(np.isfinite(vr) and vr >= 1.5),
            '条件_平均売買代金1億円': bool(turnover >= 100000000),
            '条件_持合幅15%以内': bool(width <= 15),
            '条件_急騰前または初期': bool(-3 <= r5 <= 8 and -5 <= r20 <= 15),
            '条件_安値切上げ': lowup,
            '条件_10日高値2%以内': bool(high10*.98 <= px <= high10*1.03),
        }
        label = vals.get(c, '算定不能')
        ev = evs.get(c, {})
        rows.append({'銘柄判定':label, 'コード':c, '銘柄名':name(ticker), '研究判定':'', '注目':'', '終値':px,
                     '基準日':str(x.index[-1].date()), '出来高倍率':round(vr,2), '20日平均売買代金_概算':round(turnover),
                     '20日持合幅%':round(width,2), '5日騰落率%':round(r5,2), '20日騰落率%':round(r20,2),
                     '10日高値':high10, '直近5日安値_否定目安':float(x.Low.tail(5).min()),
                     '業種・テーマ':str(ev.get('業種・テーマ','')).strip(), 'IR確認': '確認済（利用者入力）' if ev.get('valid') else '未確認',
                     '公式IR_URL':str(ev.get('公式IR_URL','')), '裏付け内容':str(ev.get('裏付け内容','')),
                     'データ確認':'／'.join(blockers), **checks})
    out = pd.DataFrame(rows)
    if out.empty or '終値' not in out:
        return out
    valid = out['データ確認'].fillna('データ不足').eq('')
    median5 = out.loc[valid, '5日騰落率%'].median()
    # 比較母集団10銘柄未満は相対強度を算定しない。市場指数比ではない。
    relative_ok = int(valid.sum()) >= 10 and np.isfinite(median5)
    out['取得母集団比5日超過率%'] = (out['5日騰落率%'] - median5).round(2) if relative_ok else np.nan
    out['条件_母集団比2%以上'] = out['取得母集団比5日超過率%'].ge(2)
    checkcols = [k for k in out if k.startswith('条件_')]
    out['条件通過数'] = out[checkcols].fillna(False).astype(bool).sum(axis=1)
    out['テーマ波及'] = '未確認（対応表なし・対象不足）'
    for theme, group in out[valid & out['業種・テーマ'].fillna('').ne('')].groupby('業種・テーマ'):
        strong = group['条件_出来高1.5倍'].fillna(False) & group['条件_母集団比2%以上']
        if len(group) >= 3:
            out.loc[group.index, 'テーマ波及'] = f'{theme}：取得{len(group)}銘柄中{int(strong.sum())}銘柄が出来高・相対強度通過' + ('（広がりあり）' if strong.sum() >= 2 and strong.mean() >= .5 else '（広がり不足）')
    for idx, row in out.iterrows():
        if row.get('研究判定') == 'データ不足':
            continue
        reasons = [str(row['データ確認'])] if row['データ確認'] else []
        if not relative_ok: reasons.append('相対比較の有効銘柄10未満')
        reasons += [k.replace('条件_', '')+'未達' for k in checkcols if not bool(row.get(k, False))]
        label = str(row['銘柄判定'])
        value_ok = any(k in label for k in ['割安','適正','妥当']) and '割高' not in label
        if not value_ok: reasons.append('割高または企業価値未確認')
        ir_ok = str(row['IR確認']).startswith('確認済')
        if not ir_ok: reasons.append('公式IRの裏付け未確認')
        tech_ok = not row['データ確認'] and relative_ok and row['条件通過数'] == len(checkcols)
        out.at[idx, '研究判定'] = '初期候補（注文前確認）' if tech_ok and value_ok and ir_ok else '技術条件通過・確認待ち' if tech_ok else '見送り'
        out.at[idx, '確認事項'] = '／'.join(reasons) if reasons else '当日の価格・ニュース・注文条件を確認'
    out = out.sort_values(['条件通過数','出来高倍率','コード'], ascending=[False,False,True], kind='stable')
    picks = out[out['研究判定'].eq('初期候補（注文前確認）')]
    if not picks.empty:
        out.at[picks.index[0], '注目'] = '★ 最優先研究候補'
    first = ['銘柄判定','注目','研究判定','コード','銘柄名','条件通過数','確認事項']
    return out[[k for k in first if k in out] + [k for k in out if k not in first]]


def render_flow_research():
    st.subheader('資金流入の初期候補（研究）')
    st.info('確定日足の出来高と価格から注目の集まり始めを探す研究です。AI投資家の注文や実際の資金流入は識別しません。候補は正式な買い・配分へ自動追加されません。')
    st.caption('取得済み銘柄のみを比較し追加の株価通信は行いません。場中の板・同時刻出来高・SNSは未使用。後場のリアルタイム判定とは別です。')
    with st.expander('条件と判定の読み方'):
        st.write('前20日比出来高1.5倍以上／前20日平均売買代金1億円以上／前20日高安幅15%以内／5日騰落−3～8%・20日騰落−5～15%／直近5日安値の切上げと5日平均終値回復／前10日高値の−2～＋3%／有効な取得母集団の5日騰落中央値比＋2%以上。この7条件は研究仮説で、上昇確率ではありません。')
        st.write('確定日足61本・データ鮮度・価格整合性を確認します。相対比較は有効10銘柄以上が必要。割高・企業価値不明・IR未確認は初期候補にしません。テーマ波及は入力された対応表に3銘柄以上ある場合だけ補助表示します。')
        st.caption('祝日を自動照合する機能は未追加です。営業日の目安と日足が一致しない場合は保留し、安全側に候補を出しません。価格断絶は分割調整の確認待ちとして扱います。')
    with st.expander('業種・テーマと公式IR確認CSV（任意）'):
        st.caption('公式IRを実際に確認した場合だけ確認結果に「確認済」を記入。確認日7日以内・https URL・裏付け内容が必要です。URLの内容・発表日を自動検証する機能ではありません。同一テーマ名で波及を比較します。')
        template = pd.DataFrame(columns=['コード','業種・テーマ','確認日','確認結果','公式IR_URL','裏付け内容'])
        st.download_button('資金流入研究・確認CSVひな形', template.to_csv(index=False).encode('utf-8-sig'), 'flow_evidence_template.csv', key='flow_template')
        upload = st.file_uploader('資金流入研究の確認CSV', type=['csv'], key='flow_evidence')
        evidence = pd.DataFrame()
        if upload is not None:
            try:
                evidence = pd.read_csv(upload, dtype=str, encoding='utf-8-sig').fillna('')
                if not set(template.columns).issubset(evidence.columns):
                    st.warning('ひな形の列が不足しています。確認情報を採用しません。')
                    evidence = pd.DataFrame()
            except Exception:
                st.warning('CSVを読み込めません。UTF-8のひな形を使用してください。')
    result = build_flow_research(data, daily_bar_status_df, valuation_master_df, evidence, tokyo_now())
    if result.empty:
        st.info('NO DATA：今日の判定を更新してください。')
        return
    st.caption(f'取得済み{len(result)}銘柄を確認。データ鮮度・母集団の偏りにより候補が変わります。')
    candidates = result[result['研究判定'].eq('初期候補（注文前確認）')]
    if candidates.empty:
        st.info('初期候補：該当なし。技術条件通過でもIR・企業価値未確認なら確認待ちです。')
    else:
        leader = candidates[candidates['注目'].fillna('').ne('')].iloc[0]
        st.success(f"★ {leader['銘柄名']}（{leader['コード']}）：7条件通過。出来高 {leader['出来高倍率']:.2f}倍／母集団比 {leader['取得母集団比5日超過率%']:+.2f}%")
        st.caption('同条件では出来高倍率→コード順で1銘柄を強調。急騰の予測や注文指示ではありません。')
        st.dataframe(candidates.style.apply(lambda r: ['background-color: #fff2b3; color: #17202a' if r.get('注目') else '']*len(r), axis=1), hide_index=True, use_container_width=True)
    waiting = result[result['研究判定'].eq('技術条件通過・確認待ち')]
    if not waiting.empty:
        st.write('技術条件通過・確認待ち')
        st.dataframe(waiting, hide_index=True, use_container_width=True)
    with st.expander('全銘柄の条件・見送り理由'):
        st.dataframe(result, hide_index=True, use_container_width=True)
    st.download_button('資金流入研究・全件CSV', result.to_csv(index=False).encode('utf-8-sig'), 'flow_research_audit.csv', key='flow_audit_download')
    st.caption('直近5日安値は研究の否定目安です。利確・IFDOCOは本機能では作成しません。値幅・支持抵抗と公式情報を再確認し、既存の売買ルールを優先してください。')


with flow_tab:
    render_flow_research()


with video_tab:
    render_video_research()


with multibagger_tab:
    st.subheader("🌱 大化け候補（研究）")
    st.caption("数年で大きく成長する企業を探す入口です。研究点は上昇確率・予想倍率ではありません。成績未検証の独立条件です。")
    st.info("注文判断は『今日の売買』で確認してください。候補入りだけでは買い指示になりません。既存の利確・終値7%損切りは引き続き適用します。")
    st.write(f"調査対象 {len(multibagger_audit)}銘柄 ／ 大化け候補 {len(multibagger_candidates)}銘柄")
    if not multibagger_audit.empty:
        complete_count = int(multibagger_audit[["売上成長率%", "利益成長率%"]].notna().all(axis=1).sum())
        st.caption(f"成長率2項目の取得 {complete_count}/{len(multibagger_audit)}銘柄。未取得は低成長と区別して保留します。")
    if multibagger_sources_paused():
        st.warning("業績取得先がアクセス制限中です。追加通信を停止しています。時間を空けて追加確認してください。未試行分は次回へ残します。")
    st.caption("主要条件を満たし、FCF・D/E・営業利益率に欠損がある場合は暫定候補として表示します。企業価値・主要データ未確認は評価保留、取得済みの健全性指標が除外条件に該当すれば条件外です。")
    render_multibagger_choice(multibagger_candidates)
    render_multibagger_cards(multibagger_candidates)
    st.caption("取得済み財務に成長率がない場合は、下のボタンでトレーダーズ・株探の無料公開業績表を追加確認できます。監視対象の一部を順番に確認します。")
    st.caption("通常の売買更新では研究用の追加通信を行いません。下の追加確認ボタンで取得し、結果を保存・復元します。サーバー再構築で保存が消えた場合は追加取得結果CSVを読み込めます。過去の取得日は書き換えず、鮮度条件を満たさないデータは保留します。")
    save_status = st.session_state.get("v172427_growth_save", "")
    if str(save_status).startswith("保存失敗"):
        st.warning("成長実績を保存できませんでした。追加取得結果CSVをダウンロードしてください。" + save_status)
    imported_growth = st.file_uploader("成長実績の追加取得結果CSVを復元", type=["csv"], key="v172427_growth_import")
    if imported_growth is not None and st.button("CSVの成長実績を読み込む", key="v172427_growth_import_apply"):
        try:
            imported = pd.read_csv(imported_growth, encoding="utf-8-sig", dtype={"コード": str})
            required = {"コード", "売上成長率%", "利益成長率%", "実績EPS", "成長比較期", "成長比較前期", "財務取得日時", "財務参照URL"}
            if not required.issubset(imported.columns): raise ValueError("追加取得結果CSVの必要列が不足しています")
            imported = imported[imported["コード"].isin(mb_scope)]
            # 現在セッションの結果を優先し、CSVで欠けている銘柄だけを復元する。
            restored = pd.concat([imported, mb_extra], ignore_index=True).drop_duplicates("コード", keep="last")
            st.session_state["v172423_growth_extra"] = restored
            st.session_state["v172427_growth_save"] = save_multibagger_growth(restored)
        except (ValueError, TypeError, pd.errors.ParserError) as exc:
            st.error("CSVを読み込めませんでした: " + str(exc))
        else:
            st.rerun()
    growth_limit = st.number_input("1回に成長実績を追加確認する件数", 5, 20, 10, 5, key="v172423_growth_limit")
    if st.button("成長実績を追加確認", key="v172423_growth_fetch", disabled=not mb_scope):
        targets = multibagger_fetch_targets(mb_scope, mb_extra, growth_limit, tokyo_now().date())
        if not targets:
            st.info("追加確認の対象がないか、再試行の待機中です。失敗分は15分以上空けて再確認してください。")
        else:
            with st.spinner("国内無料公開の連続する年度実績を確認中…"):
                combined_growth = collect_multibagger_growth(mb_finance, mb_extra, mb_scope, growth_limit, tokyo_now().date())
            combined_growth = restore_multibagger_optional(combined_growth, finance_acquisition_df, tokyo_now().date())
            st.session_state["v172423_growth_extra"] = combined_growth
            st.session_state["v172427_growth_save"] = save_multibagger_growth(combined_growth)
            st.rerun()
    if st.button("成長実績の追加確認をリセット", key="v172423_growth_reset"):
        st.session_state["v172423_growth_extra"] = pd.DataFrame()
        st.session_state["v172427_growth_save"] = save_multibagger_growth(pd.DataFrame())
        st.rerun()
    if isinstance(mb_extra, pd.DataFrame) and not mb_extra.empty:
        st.download_button("成長実績の追加取得結果CSV", mb_extra.to_csv(index=False).encode("utf-8-sig"), "multibagger_growth_fetch.csv", "text/csv", key="v172425_growth_csv")
    if not multibagger_audit.empty:
        with st.expander("全件の判定・評価保留・条件外理由", expanded=False):
            show_stock_table(multibagger_audit, use_container_width=True, hide_index=True)
        st.download_button("大化け候補の全件CSV", multibagger_audit.to_csv(index=False).encode("utf-8-sig"), "multibagger_research_audit.csv", "text/csv", key="v172423_mb_csv")
    if isinstance(mb_extra, pd.DataFrame) and not mb_extra.empty:
        with st.expander("成長実績の追加取得結果", expanded=False):
            show_stock_table(mb_extra[[c for c in ("コード", "銘柄名", "成長追加確認", "成長取得経路", "財務参照URL", "財務取得日時") if c in mb_extra]], use_container_width=True, hide_index=True)
    with st.expander("選定条件と確認すべき点", expanded=False):
        show_stock_table(MULTIBAGGER_RULES, use_container_width=True, hide_index=True)
        st.write("決算説明資料で、売上・利益が伸びた理由、翌期も続く根拠、顧客集中、増資予定を確認してください。減収・減益や資金繰り悪化は成長シナリオの再確認が必要です。")

with admin_tab:
    st.caption("比較・検証用。通常の朝はここを見る必要はありません。")
    ucnt = len(universe_df) if isinstance(universe_df, pd.DataFrame) else 0
    dcnt = len([1 for t in data if code(t) not in held_code_set]) if data else 0
    tcnt = len(value_top50_df) if isinstance(value_top50_df, pd.DataFrame) else 0
    c1,c2,c3,c4=st.columns(4)
    c1.metric("母集団", f"{ucnt}銘柄")
    c2.metric("日足取得", f"{dcnt}銘柄")
    c3.metric("新規買い監視", f"{len(true_top50_codes)}銘柄")
    c4.metric("保有", f"{len(held_codes)}銘柄")

    with st.expander("🔎 TOP50選定前の短期反転チェック", expanded=False):
        if isinstance(churn_audit_df, pd.DataFrame) and not churn_audit_df.empty:
            excluded = churn_audit_df[churn_audit_df["判定"].eq("新規買い除外")]
            st.caption(f"新規買い除外：{len(excluded)} / 検査対象：{len(churn_audit_df)}。閾値は試験設定で、成績改善は未検証です。")
            show_stock_table(excluded, use_container_width=True, hide_index=True,
                         height=admin_table_height(excluded, 480))
        else:
            st.info("『今日の判定を更新』後に除外理由を表示します。")

    with st.expander("🕒 日足データの鮮度", expanded=False):
        if isinstance(daily_bar_status_df, pd.DataFrame) and not daily_bar_status_df.empty:
            show_stock_table(daily_bar_status_df, use_container_width=True, hide_index=True,
                         height=admin_table_height(daily_bar_status_df, 520))
            st.caption("16:00以降は直近東証取引日のOHLCVを確認し、履歴が遅れている銘柄だけ当日分を補完します。")
        else:
            st.info("『今日の判定を更新』後に日足最終日と取得元を表示します。")

    value_tab, surge_tab, compare_tab, holdings_tab = st.tabs([
        "企業価値", "急騰予兆", "3指標比較", "保有銘柄"
    ])

    with value_tab:
        st.subheader("無料国内財務の取得結果（日足とは独立）")
        st.caption("日足は初期保存履歴と国内最新日足の重複日を照合して更新します。保存履歴だけでは最新判定を出しません。価格基準が合わない銘柄は再取得・確認待ちにします。")
        st.caption("正常取得値は当日6時間以内だけ再利用し、過去保存値は表示専用です。取得日時と今回確認日時を区別します。403・405等の取得先は一時休止し、連続アクセスを抑制します。保存値は同一サーバー内のため再起動等で失われる場合があります。")
        if not finance_acquisition_df.empty:
            _ok = int(finance_acquisition_df["財務取得状態"].eq("取得済").sum())
            st.caption(f"必要指標・鮮度条件を満たす財務 {_ok} / {len(finance_acquisition_df)}銘柄。予想値の未提供と通信失敗を分けて表示します。")
            show_stock_table(finance_acquisition_df, use_container_width=True, hide_index=True)
        else:
            st.info("『今日の判定を更新』で財務を直接取得します。空欄は財務取得失敗を意味しません。")
        with st.expander("日足取得の診断（失敗銘柄を含む）", expanded=False):
            if not daily_acquisition_df.empty:
                show_stock_table(daily_acquisition_df, use_container_width=True, hide_index=True)
            else:
                st.info("判定更新後に表示します。")
        render_allocation_research()
        render_public_research_logic()
        if isinstance(signal_value_df, pd.DataFrame) and not signal_value_df.empty:
            missing_finance = signal_value_df[~signal_value_df["ファンダ取得状態"].eq("OK")]
            if not missing_finance.empty:
                st.warning(f"財務指標を取得できない銘柄：{len(missing_finance)} / {len(signal_value_df)}。日足の鮮度とは別です。判定不能除外が有効なら新規買いを見送ります。")
                with st.expander("財務取得不可の詳細", expanded=False):
                    show_stock_table(missing_finance[["コード", "銘柄名", "ファンダ取得状態", "Yahoo取得状態", "財務情報鮮度", "財務取得日時"]], use_container_width=True, hide_index=True)
        with st.expander("無料財務データの取得状況・情報源", expanded=False):
            _finance_frames = [f for f in (signal_value_df, value_top50_df) if isinstance(f, pd.DataFrame) and not f.empty]
            if _finance_frames:
                _finance_audit = pd.concat(_finance_frames, ignore_index=True).drop_duplicates("コード", keep="first")
                _audit_cols = ["コード", "銘柄名", "割安判定", "ファンダ取得状態", "財務情報源", "財務情報鮮度", "参考指標基準日", "予想EPS対象期", "業績発表日", "実績EPS対象期", "財務取得日時", "財務参照URL", "財務取得経路", "財務データ区分"]
                show_stock_table(_finance_audit[[c for c in _audit_cols if c in _finance_audit]], use_container_width=True, hide_index=True)
            else:
                st.info("判定更新後に財務の取得状況を表示します。")
        st.caption("全監視対象で共通マスターの企業価値判定を新規買いに使用します。割高・赤字予想・未算定・鮮度未確認は見送ります。保有銘柄は従来の売り条件で判定します。")
        st.caption("会社予想EPS未提供・赤字予想は通信失敗と区別します。EPS×予想PERが株価と整合する場合、参考理論株価との価格差だけでは異常値にしません。理論株価は業種PER仮定に依存します。")
        st.caption("財務はYahoo!ファイナンス日本版を優先し、トレーダーズ・ウェブ、株探の無料公開データへ順に切り替えます。取得サイト・指標基準日・EPS対象期・株探業績発表日・取得経路を表示します。サイトをまたいでEPSとPERを混ぜません。欠損をゼロや推測値で補わず、最新性・算定不能は新規買い対象外です。J-Quants遅延財務・有料APIは使用しません。")
        if not isinstance(value_top50_df, pd.DataFrame) or value_top50_df.empty:
            st.info("先に『今日の判定を更新』で監視対象のデータを取得してください。")
        else:
            value_show = add_research_judgements_first(value_top50_df, surge_top50_df, valuation_master_df)
            show_cols=[
                "急騰予兆判定","割安判定","順位","コード","銘柄名","一次選抜順位",
                "現在株価_価格","AI参考価値","参考価値上昇余地%","予想EPS_元値","予想PER","PBR","適正株価クロスチェック","企業価値スコア",
                "成長性スコア","流動性スコア","AI_TOP50スコア",
                "財務情報源","財務情報鮮度","Yahoo期末日","Yahoo取得状態",
                "財務取得日時","参考指標基準日","予想EPS対象期","業績発表日","実績EPS対象期","財務参照URL","財務取得経路","ファンダ取得状態",
                "適正株価異常値ガード","株式分割補正"
            ]
            value_full = prepare_admin_display(value_show[[c for c in show_cols if c in value_show.columns]])
            value_compact_cols = [
                "急騰予兆判定","割安判定","順位","コード","銘柄名",
                "現在株価_価格","AI_TOP50スコア"
            ]
            value_compact = value_full[[c for c in value_compact_cols if c in value_full.columns]].rename(columns={
                "急騰予兆判定":"急騰", "割安判定":"割安", "現在株価_価格":"株価",
                "AI_TOP50スコア":"AI点"
            })
            admin_judgement_table(value_compact, "v7_value_compact", 520)
            with st.expander("企業価値TOP50の詳細列", expanded=False):
                admin_judgement_table(value_full, "v7_value_full", 560)

        with st.expander("🧪 5年OOS検証メモ", expanded=False):
            st.caption("TOPIX＋日経225＋グロースモードではAI上位50銘柄を新規買い監視対象に使用します。固定45・売買代金上位モードでは企業価値は参考表示です。現在の構成一覧を過去に流用した検証は、過去の選定を再現しません。")
            st.write("採用値：流動性45% / トレンド30% / 値動き安定性25%")
            st.write("学習期間：+54.27% / PF 2.10 / 最大DD -10.04% / 257決済")
            st.write("OOS期間：+16.10% / PF 1.89 / 最大DD -5.25% / 81決済")
            st.write("5年通算参考：60万円 → 約105.6万円 / +76.01% / PF 2.09 / 最大DD -10.04% / 339決済")

        with st.expander("🧪 バックテスト用5年日足データ", expanded=False):
            st.caption("この出力は検証専用です。売買判定には使いません。現在の大規模母集団の5年OHLCVを取得します。")
            st.warning("企業価値AIを過去時点で完全再現するには、当時のファンダメンタル/目標株価データが別途必要です。このデータだけでできるのは347銘柄ストキャス検証と、現TOP50を固定した参考検証です。")
            if isinstance(universe_df, pd.DataFrame) and not universe_df.empty:
                bt_tickers = tuple(universe_df["ticker"].astype(str).tolist()) if "ticker" in universe_df.columns else tuple(tickers(",".join(universe_df["コード"].astype(str).tolist())))
                if st.button("5年日足バックテストデータを作成", key="v178_make_btdata"):
                    with st.spinner(f"{len(bt_tickers)}銘柄の5年日足を取得中… 数分かかる場合があります。"):
                        bt_hist, bt_failed = export_backtest_history_5y(bt_tickers)
                    bbuf = io.BytesIO()
                    with ZipFile(bbuf, "w") as bzf:
                        bzf.writestr("backtest_history_5y.csv", bt_hist.to_csv(index=False, encoding="utf-8-sig"))
                        bzf.writestr("backtest_universe.csv", universe_df.to_csv(index=False, encoding="utf-8-sig"))
                        if isinstance(value_top50_df, pd.DataFrame):
                            bzf.writestr("current_value_ai_top50.csv", value_top50_df.to_csv(index=False, encoding="utf-8-sig"))
                        bzf.writestr("backtest_history_failures.csv", bt_failed.to_csv(index=False, encoding="utf-8-sig"))
                        manifest = pd.DataFrame([{
                            "Version": VERSION, "Build": BUILD,
                            "母集団件数": len(universe_df),
                            "5年日足取得成功銘柄数": int(bt_hist["コード"].nunique()) if not bt_hist.empty and "コード" in bt_hist.columns else 0,
                            "総日足行数": len(bt_hist), "取得失敗件数": len(bt_failed),
                            "用途": "この場での347銘柄ストキャス5年バックテスト用",
                            "注意": "過去時点の企業価値AI TOP50完全再現にはpoint-in-timeファンダメンタルが必要",
                        }])
                        bzf.writestr("backtest_manifest.csv", manifest.to_csv(index=False, encoding="utf-8-sig"))
                    bbuf.seek(0)
                    st.success(f"5年日足：{bt_hist['コード'].nunique() if not bt_hist.empty and 'コード' in bt_hist.columns else 0}銘柄 / {len(bt_hist):,}行")
                    st.download_button("📦 バックテスト用5年日足ZIP", data=bbuf.getvalue(), file_name="ver17_backtest_history_5y.zip", mime="application/zip", use_container_width=True, key="v178_btzip")
            else:
                st.info("先に『今日の判定を更新』で大規模母集団を作成してください。")

        with st.expander("🧪 ストキャス振り回され対策の比較バックテスト", expanded=False):
            st.caption("上で作成した5年日足ZIP、または backtest_history_5y.csv をアップロード。現行GC/DCとトレンド＋振れ幅判定を同じ日足で比較します。")
            st.warning("銘柄別に独立した1ポジションの検証です。TOP50を現在の銘柄で固定する選択は過去の選定を再現しません。企業価値、買付余力、実際の約定・手数料・税金は再現していません。")
            bt_input = st.file_uploader("5年日足ZIPまたはCSV", type=["zip", "csv"], key="v1717_bt_input")
            bt_fixed_top = st.checkbox("現在のTOP50だけに限定（参考検証）", value=False, key="v1717_bt_top")
            if st.button("現行版と改善版を比較", key="v1717_bt_run", disabled=bt_input is None):
                try:
                    raw = bt_input.getvalue()
                    if len(raw) > 80_000_000:
                        raise ValueError("ファイルは80MB以内にしてください")
                    if bt_input.name.lower().endswith(".zip"):
                        with ZipFile(io.BytesIO(raw)) as z:
                            names = [n for n in z.namelist() if n.endswith("backtest_history_5y.csv")]
                            if not names:
                                raise ValueError("ZIPに backtest_history_5y.csv がありません")
                            if z.getinfo(names[0]).file_size > 150_000_000:
                                raise ValueError("展開後の日足CSVが大きすぎます")
                            raw = z.read(names[0])
                    hist = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", low_memory=False)
                    if len(hist) > 750_000:
                        raise ValueError("75万行以内のデータを使用してください")
                    codes = true_top50_codes if bt_fixed_top else None
                    if bt_fixed_top and not codes:
                        raise ValueError("先に『今日の判定を更新』でTOP50を作成してください")
                    summary, trades, unrealized = compare_stoch_backtest(hist, sl, codes)
                    st.session_state["v1717_bt_result"] = (summary, trades, unrealized)
                except Exception as e:
                    st.error(f"比較バックテストを実行できません: {e}")
            if "v1717_bt_result" in st.session_state:
                summary, trades, unrealized = st.session_state["v1717_bt_result"]
                show_stock_table(summary, use_container_width=True, hide_index=True)
                st.caption("終値で判定し、翌営業日の始値で約定。最終日の未決済は集計から分離。PFは取引騰落率の単純合算比で、ポートフォリオの資産推移・最大DDを示すものではありません。")
                for label, frame, filename in [("決済明細CSV", trades, "stoch_backtest_trades.csv"),
                                               ("未決済明細CSV", unrealized, "stoch_backtest_open.csv")]:
                    st.download_button(label, data=frame.to_csv(index=False).encode("utf-8-sig"),
                                       file_name=filename, mime="text/csv", key="v1717_" + filename)

        with st.expander("🧪 割高除外・保有買い増しの比較バックテスト", expanded=False):
            st.caption("過去の研究用比較です。主ロジックでは買い増しを行いません。判定日の終値で判断し、翌営業日の始値で約定します。")
            st.warning("割高除外の過去検証には、各評価日当時に公表・取得できたAI参考価値が必要です。現在のTOP50や現在の参考価値を過去へ流用しません。過去評価CSVがない場合は買い増しだけを比較します。")
            new_history_file = st.file_uploader("5年日足ZIPまたはCSV", type=["zip", "csv"], key="v1718_history")
            new_value_file = st.file_uploader("過去時点の企業価値CSV（任意）", type=["csv"], key="v1718_values")
            st.download_button("過去評価CSVの書式をダウンロード",
                               data="コード,評価日,AI参考価値\n".encode("utf-8-sig"),
                               file_name="historical_value_template.csv", mime="text/csv",
                               key="v1718_template")
            st.caption("過去評価CSV：コード,評価日,AI参考価値。評価日は、その参考価値を実際に利用できた日。既定では120日を超えた評価は期限切れ。現在の評価を過去の日付に書き換えないでください。")
            new_fixed_top = st.checkbox("現在のTOP50に限定（過去の選定再現ではない）",
                                        value=False, key="v1718_fixed_top")
            addon_fraction = st.slider("買い増し量（初回買付を1とした比率）",
                                       min_value=0.25, max_value=1.0, value=0.5,
                                       step=0.25, key="v1718_addon_fraction")
            if st.button("割高除外・買い増しを比較", key="v1718_run", disabled=new_history_file is None):
                try:
                    raw = new_history_file.getvalue()
                    if len(raw) > 80_000_000:
                        raise ValueError("日足ファイルは80MB以内にしてください")
                    if new_history_file.name.lower().endswith(".zip"):
                        with ZipFile(io.BytesIO(raw)) as z:
                            names = [n for n in z.namelist() if n.endswith("backtest_history_5y.csv")]
                            if not names:
                                raise ValueError("ZIPに backtest_history_5y.csv がありません")
                            if z.getinfo(names[0]).file_size > 150_000_000:
                                raise ValueError("展開後の日足CSVが大きすぎます")
                            raw = z.read(names[0])
                    hist = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", low_memory=False)
                    if len(hist) > 750_000:
                        raise ValueError("日足は75万行以内にしてください")
                    old_values = None
                    if new_value_file is not None:
                        raw_value = new_value_file.getvalue()
                        if len(raw_value) > 20_000_000:
                            raise ValueError("過去評価CSVは20MB以内にしてください")
                        old_values = pd.read_csv(io.BytesIO(raw_value), encoding="utf-8-sig",
                                                 dtype={"コード": str})
                    codes = true_top50_codes if new_fixed_top else None
                    if new_fixed_top and not codes:
                        raise ValueError("先に『今日の判定を更新』でTOP50を作成してください")
                    result = compare_value_addon_backtest(
                        hist, sl, codes, old_values, addon_fraction=addon_fraction)
                    st.session_state["v1718_result"] = result
                except Exception as e:
                    st.session_state.pop("v1718_result", None)
                    st.error(f"比較バックテストを実行できません: {e}")
            if "v1718_result" in st.session_state:
                summary, trades, unrealized, audit = st.session_state["v1718_result"]
                show_stock_table(summary, use_container_width=True, hide_index=True)
                st.caption("買い増しは初回の約定から2営業日以上経過し、売り・損切りがなく、%Kが%Dより上で上昇中・%K≤50・終値が平均取得額を上回る場合に1回だけ。追加量は初回の指定比率。損切りは追加後の平均取得額で判定します。")
                st.caption("銘柄別の独立検証です。過去のTOP50、実際の買付余力、ポートフォリオ収益・最大DD、手数料、税金は再現しません。PFは決済した取引騰落率の単純合算比です。未決済は別表に分離します。")
                for label, frame, filename in [
                    ("決済明細CSV", trades, "value_addon_trades.csv"),
                    ("未決済明細CSV", unrealized, "value_addon_open.csv"),
                    ("判定・見送り理由CSV", audit, "value_addon_audit.csv"),
                ]:
                    st.download_button(label, data=frame.to_csv(index=False).encode("utf-8-sig"),
                                       file_name=filename, mime="text/csv", key="v1718_" + filename)

    with surge_tab:
        st.caption("旧Ver.5.5系の観察センサーです。正式なBUY/SELLには影響しません。")
        st.write("70点以上＝強い予兆、55点以上＝急騰予兆、40点以上＝変化検知")
        if surge_top50_df.empty:
            st.info("先に『今日の判定を更新』で企業価値AI TOP50を作成してください。")
        else:
            surge_score = pd.to_numeric(surge_top50_df["急騰予兆スコア"], errors="coerce")
            strong_n = int((surge_score >= 70).sum())
            alert_n = int((surge_score >= 55).sum())
            change_n = int((surge_score >= 40).sum())
            stoch_match_n = int(((surge_score >= 55) & surge_top50_df["Stoch_BUY"].fillna(False).astype(bool)).sum())
            s1,s2,s3,s4 = st.columns(4)
            s1.metric("強予兆", strong_n)
            s2.metric("予兆以上", alert_n)
            s3.metric("変化以上", change_n)
            s4.metric("予兆＋Stoch", stoch_match_n)

            surge_display_cols = [
                "急騰予兆判定","割安判定","急騰順位","TOP50順位","コード","銘柄名",
                "現在株価","急騰予兆スコア","AI_TOP50スコア","RSI","5日騰落率",
                "25日騰落率","出来高倍率","MA25乖離率","20日高値更新",
                "Stoch_BUY","%K","%D"
            ]
            surge_full = prepare_admin_display(surge_top50_df[[c for c in surge_display_cols if c in surge_top50_df.columns]])
            surge_compact_cols = [
                "急騰予兆判定","割安判定","急騰順位","コード","銘柄名",
                "現在株価","急騰予兆スコア","出来高倍率","Stoch_BUY"
            ]
            surge_compact = surge_full[[c for c in surge_compact_cols if c in surge_full.columns]].rename(columns={
                "急騰予兆判定":"急騰", "割安判定":"割安", "急騰順位":"順位",
                "現在株価":"株価", "急騰予兆スコア":"予兆点", "出来高倍率":"出来高倍",
                "Stoch_BUY":"Stoch"
            })
            admin_judgement_table(surge_compact, "v7_surge_compact", 520)
            with st.expander("急騰予兆の詳細列", expanded=False):
                admin_judgement_table(surge_full, "v7_surge_full", 560)
                st.caption("詳細な配点は全処理ZIPにも保存します。株価2,000円以上も除外しません。")

    with compare_tab:
        st.caption("この欄は研究専用。実売買方式は管理者設定で選択します。")
        st.write("RSI5＝15以下から15上抜け / BB20＝-2σ外から内側復帰")
        if indicator_compare_df.empty:
            st.info("現在、TOP50内で3方式のいずれかがBUY点灯している銘柄はありません。")
        else:
            raw_compare = indicator_compare_df.copy()
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Stoch", int(raw_compare["Stoch_BUY"].fillna(False).astype(bool).sum()) if "Stoch_BUY" in raw_compare else 0)
            c2.metric("RSI5", int(raw_compare["RSI5_BUY"].fillna(False).astype(bool).sum()) if "RSI5_BUY" in raw_compare else 0)
            c3.metric("BB20", int(raw_compare["BB20_BUY"].fillna(False).astype(bool).sum()) if "BB20_BUY" in raw_compare else 0)
            c4.metric("2方式以上", int((pd.to_numeric(raw_compare.get("一致数", 0), errors="coerce") >= 2).sum()))

            comp_full = prepare_admin_display(raw_compare)
            comp_cols = [
                "急騰予兆判定","割安判定","コード","銘柄名","現在株価",
                "一致数","発火方式","Stoch_BUY","RSI5_BUY","BB20_BUY"
            ]
            comp_compact = comp_full[[c for c in comp_cols if c in comp_full.columns]].rename(columns={
                "急騰予兆判定":"急騰", "割安判定":"割安", "現在株価":"株価",
                "Stoch_BUY":"Stoch", "RSI5_BUY":"RSI5", "BB20_BUY":"BB20"
            })
            if "一致数" in comp_compact.columns:
                comp_compact = comp_compact.sort_values(["一致数", "コード"], ascending=[False, True])
            admin_judgement_table(comp_compact, "v7_comp_compact", 480)
            with st.expander("3指標比較の詳細列", expanded=False):
                admin_judgement_table(comp_full, "v7_comp_full", 520)
        st.caption("比較結果は全処理ZIP内の indicator_compare_candidates.csv に保存します。")

    with holdings_tab:
        st.caption("SBI約定履歴CSVから復元できた現在保有銘柄だけを表示します。")
        if confirmed:
            held_show = pd.DataFrame([
                {"コード":c,"銘柄名":name(c),"株数":v["shares"],"取得単価":v["avg_price"]}
                for c,v in confirmed.items()
            ])
            held_show = add_research_judgements_first(held_show, surge_top50_df, valuation_master_df)
            held_full = prepare_admin_display(held_show)
            held_compact_cols = ["急騰予兆判定","割安判定","コード","銘柄名","株数","取得単価"]
            held_compact = held_full[[c for c in held_compact_cols if c in held_full.columns]].rename(columns={
                "急騰予兆判定":"急騰", "割安判定":"割安"
            })
            admin_judgement_table(held_compact, "v7_held_compact", 460)
            with st.expander("保有銘柄の詳細列", expanded=False):
                admin_judgement_table(held_full, "v7_held_full", 500)
        else:
            st.info("保有情報はありません。SBI約定履歴CSVを読み込んでください。")

# ------------------------------------------------------------
# ZIP — メイン画面の最下部に1ボタンだけ
# ------------------------------------------------------------
st.divider()
try:
    zip_buf=io.BytesIO()
    with ZipFile(zip_buf,"w") as zf:
        settings_df=pd.DataFrame([{
            "Version":VERSION,"Build":BUILD,"買付余力":int(buying_power),"現在資産":int(current_assets),
            "買付余力（短期）":short_buying_power,"保有件数":len(held_codes),
            "日本株母集団設定":int(universe_size),"取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "詳細企業価値評価件数設定":int(fundamental_pool_size),"TOP50件数":len(true_top50_codes) if live_universe_mode == "TOPIX＋日経225＋グロースからAI選定TOP50" else 0,
            "固定45監視件数":len(VERIFIED_45_CODES) if live_universe_mode == "固定45銘柄" else 0, "新規監視件数":len(true_top50_codes),
            "企業価値参考表示件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "売買ロジック":live_trade_method, "Stoch不使用の試験採用":bool(replacement_live), "新規BUY対象":live_universe_mode + "の未保有株", "判定不能除外":bool(block_unrated), "流動性下限":int(min_turnover), "下降確認除外":bool(filter_downtrend), "クロス多発除外":bool(filter_churn), "購入予算方式":cap_mode,
            "最大保有銘柄数":"上限なし", "1銘柄購入予算":maxbuy, "通常注文予算":"上限なし（買付余力内）", "注文方式":execution_mode,
            "IFDOCO参考利確率":ifd_take_profit,"BUY条件":live_trade_method + "＋全モード共通財務・日足確認" + ("＋クロス多発除外" if not replacement_live else ""),"管理者比較":"RSI5 / BB20 / 急騰予兆（すべて実売買には不使用）",
            "割高の新規BUY除外":True,
            "SELL条件":("全保有銘柄 / 前日までの20日安値を終値で割る または終値で含み損 -7.0%" if replacement_live else "全保有銘柄 / Slow Stoch DC または終値で含み損 -7.0%"),
            "旧49銘柄固定ユニバース使用":False,"検証済み45銘柄固定ユニバース使用":live_universe_mode == "固定45銘柄",
            "短期反転フィルター":bool(churn_filter),
            "トレンド振れ幅フィルター実売買":bool(quality_mode),
        }])
        zf.writestr("ver17_settings.csv",settings_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("update_processing_times.csv", pd.DataFrame([{"処理": k, "秒": v} for k, v in st.session_state.get("v172429_update_times", {}).items()]).to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("finance_acquisition_status.csv",finance_acquisition_df.to_csv(index=False,encoding="utf-8-sig"))
        for mb_filename, mb_frame in (("multibagger_candidates.csv", multibagger_candidates),
                                     ("multibagger_research_audit.csv", multibagger_audit),
                                     ("multibagger_rules.csv", MULTIBAGGER_RULES),
                                     ("multibagger_growth_fetch.csv", mb_extra)):
            zf.writestr(mb_filename, mb_frame.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("daily_acquisition_diagnostics.csv",daily_acquisition_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(universe_df,pd.DataFrame):
            zf.writestr("japan_large_universe.csv",universe_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(value_top50_df,pd.DataFrame):
            zf.writestr("value_ai_top50.csv",value_top50_df.to_csv(index=False,encoding="utf-8-sig"))
        if signal_first:
            zf.writestr("signal_first_candidates.csv",signal_first_audit_df.to_csv(index=False,encoding="utf-8-sig"))
            zf.writestr("signal_first_values.csv",signal_value_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(churn_audit_df,pd.DataFrame):
            zf.writestr("stoch_churn_audit.csv",churn_audit_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("stoch_signal_quality_audit.csv", signal_audit_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("overvalued_buy_exclusions.csv", overvalued_buy_exclusions_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(daily_bar_status_df,pd.DataFrame):
            zf.writestr("daily_bar_freshness.csv",daily_bar_status_df.to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_trades_df.empty:
            zf.writestr("sbi_execution_history.csv",sbi_trades_df.to_csv(index=False,encoding="utf-8-sig"))
        if confirmed:
            zf.writestr("current_holdings.csv",pd.DataFrame([{
                "コード":c,"銘柄名":name(c),"株数":v["shares"],"取得単価":v["avg_price"],
            } for c,v in confirmed.items()]).to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_warning_df.empty:
            zf.writestr("sbi_history_warnings.csv",sbi_warning_df.to_csv(index=False,encoding="utf-8-sig"))
        buy_cols_default=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","ロジック","63日騰落率%","%K","%D","候補スコア","AI_TOP50スコア","割安判定","買付可否","参考購入株数","S株注文参考株数","注文区分","通常注文参考株数","通常注文概算額","余力引当額","均等配分予算","見送り理由"]
        if isinstance(buy_view, pd.DataFrame) and not buy_view.empty:
            buy_export = buy_view.copy()
        else:
            buy_export = pd.DataFrame(columns=buy_cols_default)
        zf.writestr("stoch_buy_candidates.csv",buy_export.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("buy_candidate_exclusions.csv", excluded_buy_df.to_csv(index=False, encoding="utf-8-sig"))
        _bottom_saved = st.session_state.get('v172437_bottom_result')
        if _bottom_saved:
            if isinstance(_bottom_saved.get('trades'), pd.DataFrame):
                zf.writestr('bottom_independent_trades.csv', _bottom_saved['trades'].to_csv(index=False, encoding='utf-8-sig'))
            for _key in ['ranking', 'audit', 'coverage']:
                zf.writestr('bottom_research_' + _key + '.csv', _bottom_saved[_key].to_csv(index=False, encoding='utf-8-sig'))
            zf.writestr('bottom_research_metadata.csv', pd.DataFrame([{k: v for k, v in _bottom_saved.items() if k not in ['ranking', 'audit', 'coverage', 'prices', 'trades']}]).to_csv(index=False, encoding='utf-8-sig'))
        zf.writestr("reference_ifdoco.csv",build_reference_ifdoco(buy_view, ifd_take_profit, 7.0).to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("stoch_sell_candidates.csv",sell_view.to_csv(index=False,encoding="utf-8-sig"))
        compare_export = indicator_compare_df.copy() if isinstance(indicator_compare_df, pd.DataFrame) else pd.DataFrame()
        zf.writestr("indicator_compare_candidates.csv", compare_export.to_csv(index=False,encoding="utf-8-sig"))
        # Public research only. Never export credentials, requests or account headers.
        if news_pick_result:
            zf.writestr("triple_news_pick.json", json.dumps(news_pick_result, ensure_ascii=False, indent=2))
        zf.writestr("triple_news_candidate_exclusions.csv", news_pick_exclusions.to_csv(index=False, encoding="utf-8-sig"))
        surge_export = surge_top50_df.copy() if isinstance(surge_top50_df, pd.DataFrame) else pd.DataFrame()
        zf.writestr("surge_prediction_top50.csv", surge_export.to_csv(index=False,encoding="utf-8-sig"))
        surge_summary = pd.DataFrame([{
            "生成日時": st.session_state.get("v177_generated_at",""),
            "対象": live_universe_mode + "（参考センサー）",
            "強い急騰予兆_70点以上": int((pd.to_numeric(surge_export.get("急騰予兆スコア", pd.Series(dtype=float)), errors="coerce") >= 70).sum()),
            "急騰予兆_55点以上": int((pd.to_numeric(surge_export.get("急騰予兆スコア", pd.Series(dtype=float)), errors="coerce") >= 55).sum()),
            "変化検知_40点以上": int((pd.to_numeric(surge_export.get("急騰予兆スコア", pd.Series(dtype=float)), errors="coerce") >= 40).sum()),
            "予兆55点以上かつStoch_BUY": int(((pd.to_numeric(surge_export.get("急騰予兆スコア", pd.Series(dtype=float)), errors="coerce") >= 55) & surge_export.get("Stoch_BUY", pd.Series(False, index=surge_export.index)).fillna(False).astype(bool)).sum()),
            "株価2000円以上除外": False,
            "ニュース加点": False,
            "実売買へ影響": False,
        }])
        zf.writestr("surge_prediction_summary.csv", surge_summary.to_csv(index=False,encoding="utf-8-sig"))
        compare_summary_row = {
            "生成日時": st.session_state.get("v177_generated_at",""),
            "実売買方式": live_trade_method,
            "研究_RSI5": "RSI(5) 15以下から15上抜け",
            "研究_BB20": "BB20 -2σ外から内側復帰",
            "Stoch_BUY件数": int(compare_export["Stoch_BUY"].sum()) if not compare_export.empty and "Stoch_BUY" in compare_export else 0,
            "RSI5_BUY件数": int(compare_export["RSI5_BUY"].sum()) if not compare_export.empty and "RSI5_BUY" in compare_export else 0,
            "BB20_BUY件数": int(compare_export["BB20_BUY"].sum()) if not compare_export.empty and "BB20_BUY" in compare_export else 0,
            "2方式以上一致件数": int((pd.to_numeric(compare_export["一致数"], errors="coerce") >= 2).sum()) if not compare_export.empty and "一致数" in compare_export else 0,
            "実売買へ影響": False,
        }
        compare_summary = pd.DataFrame([compare_summary_row])
        zf.writestr("indicator_compare_summary.csv", compare_summary.to_csv(index=False,encoding="utf-8-sig"))
        status_df=pd.DataFrame([{
            "本物TOP50モード":live_universe_mode == "TOPIX＋日経225＋グロースからAI選定TOP50","母集団最低200銘柄ガード":False,
            "固定45銘柄モード":live_universe_mode == "固定45銘柄","固定45監視件数":len(VERIFIED_45_CODES) if live_universe_mode == "固定45銘柄" else 0, "新規監視件数":len(true_top50_codes),
            "取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "日足取得成功件数":len(data) if isinstance(data,dict) else 0,
            "当日確定日足件数":int(daily_bar_status_df["状態"].eq("🟢 当日確定").sum()) if isinstance(daily_bar_status_df,pd.DataFrame) and not daily_bar_status_df.empty else 0,
            "TOP50件数":len(true_top50_codes) if live_universe_mode == "TOPIX＋日経225＋グロースからAI選定TOP50" else 0,
            "企業価値参考表示件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "一次選抜_流動性重み":np.nan,"一次選抜_トレンド重み":np.nan,"一次選抜_安定性重み":np.nan,
            "OOS検証済み":False,"OOS_PF参考":np.nan,"5年通算PF参考":np.nan,
            "BUY候補件数":len(buy_export),"割高除外件数":len(overvalued_buy_exclusions_df),
            "企業価値未算定件数":int((~value_top50_df["企業価値算定済"].fillna(False)).sum()) if "企業価値算定済" in value_top50_df else 0,
            "財務直接取得対象件数":len(finance_acquisition_df),
            "財務直接取得済件数":int(finance_acquisition_df["財務取得状態"].eq("取得済").sum()) if not finance_acquisition_df.empty else 0,
            "財務直接取得不可件数":int(finance_acquisition_df["財務取得状態"].str.startswith("取得不可").sum()) if not finance_acquisition_df.empty else 0,
            "財務指標データ取得件数":int(finance_acquisition_df["財務取得状態"].str.startswith("取得済").sum()) if not finance_acquisition_df.empty else 0,
            "日足診断件数":len(daily_acquisition_df),
            "SELL候補件数":len(sell_view),"エラー":run_error,
            "生成日時":st.session_state.get("v177_generated_at","")
        }])
        zf.writestr("value_ai_status.csv",status_df.to_csv(index=False,encoding="utf-8-sig"))
    zip_buf.seek(0)
    st.download_button("📦 全処理結果ZIP",data=zip_buf.getvalue(),file_name="ver17_all_analysis.zip",mime="application/zip",use_container_width=True,key="v177_zip")
except Exception as e:
    st.warning(f"ZIP作成エラー: {e}")

st.caption("売買判断補助です。自動発注は行いません。")
