# ============================================================
# 日本株 AI投資アシスタント Ver.17.19
# BUILD: VER17-19-JQUANTS-YAHOO-FRESHNESS-20260923
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
import json
import math
import os
import re
import threading
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
    page_title="日本株 AI投資アシスタント Ver.17.19",
    page_icon="📈",
    layout="wide",
)

VERSION = "17.19 STOCH DUAL FUNDAMENTALS"
BUILD = "VER17-19-JQUANTS-YAHOO-FRESHNESS-20260923"

JST = ZoneInfo("Asia/Tokyo")
TRADINGVIEW_QUOTES_CACHE = {}
JQUANTS_CALL_TIMES = []
JQUANTS_CALL_LOCK = threading.Lock()


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

DEFAULT_UNIVERSE = ",".join(list(STOCK_NAMES.keys()))

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


@st.cache_data(ttl=1800)
def market_data(required_date=None):
    start, end = yahoo_history_window(5)
    required_date = pd.Timestamp(required_date).normalize() if pd.notna(required_date) else pd.NaT
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


@st.cache_data(ttl=21600, show_spinner=False)
def jquants_financial_summary(ticker, api_key):
    """J-Quants V2無料プランの開示サマリー。取得失敗は明示し、空欄を0として扱わない。"""
    if not api_key:
        return {}, "APIキー未設定"
    try:
        code4 = code(ticker)
        response = jquants_get(api_key, {"code": code4})
        if response.status_code == 429:
            return {}, "取得制限（429）"
        response.raise_for_status()
        body = response.json()
        rows = body.get("data", [])
        # 長期履歴でページが分割されても最新の開示を取りこぼさない。
        pages = 0
        seen = set()
        while body.get("pagination_key") and pages < 8:
            page_key = str(body["pagination_key"])
            if page_key in seen:
                break
            seen.add(page_key)
            response = jquants_get(api_key, {"code": code4, "pagination_key": page_key})
            response.raise_for_status()
            body = response.json()
            rows.extend(body.get("data", []))
            pages += 1
        if body.get("pagination_key"):
            return {}, "ページ上限で最新開示を確認できず"
        today = tokyo_now().date()
        candidates = []
        for row in rows:
            d = pd.to_datetime(row.get("DiscDate"), errors="coerce")
            if (not pd.isna(d) and d.date() <= today and
                any(np.isfinite(safe_float(row.get(field))) for field in
                    ("FEPS", "EPS", "BPS", "ROE"))):
                candidates.append(row)
        if not candidates:
            return {}, "開示データなし（無料プランは12週間遅延）"
        candidates.sort(key=lambda r: (str(r.get("DiscDate", "")),
                                       str(r.get("DiscTime", "")), str(r.get("DiscNo", ""))))
        return candidates[-1], "OK（無料プラン・12週間遅延）"
    except Exception as exc:
        return {}, "取得失敗: " + type(exc).__name__

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


@st.cache_data(ttl=21600)
def fundamental_snapshot(t, market_price_hint=np.nan, jq_api_key=""):
    """現在情報のみ。過去バックテストには使用しない。株式分割ズレを最優先で防止する。"""
    try:
        try:
            info = yf.Ticker(t).info or {}
        except Exception:
            info = {}
        if not isinstance(info, dict):
            info = {}
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
        jq_row, jq_status = {}, "APIキー未設定" if not jq_api_key else "Yahooの必要項目あり"
        jq_date, jq_age = None, None
        yahoo_value_basis = any(np.isfinite(x) and x > 0 for x in
                                (forward_eps, trailing_eps, target_mean))
        # J-QuantsはYahooに必要な財務値が欠けたときだけ参照する。
        if jq_api_key and (not yahoo_value_basis or not np.isfinite(price_to_book) or not np.isfinite(roe)):
            jq_row, jq_status = jquants_financial_summary(t, jq_api_key)
            jq_dt = pd.to_datetime(jq_row.get("DiscDate"), errors="coerce")
            if not pd.isna(jq_dt):
                jq_date = jq_dt.date()
                jq_age = (today - jq_date).days
            fiscal_end = pd.to_datetime(jq_row.get("CurFYEn"), errors="coerce")
            valid_forecast = not pd.isna(fiscal_end) and fiscal_end.date() >= today - timedelta(days=30)
            jq_forward = safe_float(jq_row.get("FEPS")) if valid_forecast else np.nan
            jq_actual = safe_float(jq_row.get("EPS")) if jq_row.get("CurPerType") == "FY" else np.nan
            if not np.isfinite(forward_eps) and np.isfinite(jq_forward):
                forward_eps = jq_forward
            if not np.isfinite(trailing_eps) and np.isfinite(jq_actual):
                trailing_eps = jq_actual
            if not np.isfinite(book_value):
                book_value = safe_float(jq_row.get("BPS"))
            if not np.isfinite(roe):
                roe = safe_float(jq_row.get("ROE"))
            if not np.isfinite(price_to_book) and np.isfinite(book_value) and book_value > 0 and np.isfinite(price):
                price_to_book = price / book_value
            if not np.isfinite(forward_pe) and np.isfinite(forward_eps) and forward_eps > 0 and np.isfinite(price):
                forward_pe = price / forward_eps
        jq_used_for_value = bool(not yahoo_value_basis and jq_row and
                                 (np.isfinite(forward_eps) or np.isfinite(trailing_eps)))
        # 株価ヒントだけでは企業価値の取得成功とみなさない。
        # Yahoo側が空のinfoを返す場合、従来は中立点50と「OK」が全銘柄に付いていた。
        fundamental_fields = (trailing_pe, forward_pe, price_to_book, roe,
                              revenue_growth, earnings_growth, trailing_eps,
                              forward_eps, target_mean)
        has_fundamentals = any(np.isfinite(x) for x in fundamental_fields)

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
            split_notes.append("直近分割情報なし")

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
                valuation_guard_notes.append(
                    f"EPS理論株価が現在株価の{eps_price_ratio:.2f}倍で異常域→EPS評価除外"
                )
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

        source_status = ("参考のみ: J-Quants無料12週間遅延" if jq_used_for_value else
                         "OK" if has_fundamentals else "取得不可: 企業価値指標が全て欠損")
        freshness = ("12週間遅延・最新開示は未確認" if jq_used_for_value else
                     "Yahoo期末日が未来" if yahoo_quarter_age is not None and yahoo_quarter_age < 0 else
                     "Yahoo期末日が古い" if yahoo_quarter_age is not None and yahoo_quarter_age > 200 else
                     "Yahoo期末日を確認" if yahoo_quarter_age is not None else
                     "Yahoo開示期末日未提供" if has_fundamentals else "最新性を確認できず")
        return {
            "取得状態":source_status,"財務情報源":"J-Quants無料（補完）" if jq_used_for_value else
            "Yahoo Finance＋J-Quants補完" if jq_row and has_fundamentals else
            "Yahoo Finance" if has_fundamentals else "取得不可",
            "財務情報鮮度":freshness,"Yahoo期末日":str(yahoo_quarter or ""),
            "JQuants開示日":str(jq_date or ""),"JQuants開示経過日数":jq_age,
            "JQuants取得状態":jq_status,
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


def parse_sbi_execution_csv(uploaded_file):
    """SBI『約定履歴照会』CSVを読み、明細部分だけDataFrame化する。

    ファイル先頭の検索条件・注記行を自動で飛ばし、
    『約定日,銘柄,銘柄コード,...』の見出し行から読み込む。
    """
    raw = uploaded_file.getvalue()
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
    df["_source_file"] = getattr(uploaded_file, "name", "uploaded.csv")
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
        f = fundamental_snapshot(t, market_price_hint=safe_float(r.get("現在株価_価格")),
                                 jq_api_key=jquants_api_key())
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
            "JQuants開示日": str(f.get("JQuants開示日", "")),
            "JQuants開示経過日数": f.get("JQuants開示経過日数"),
            "JQuants取得状態": str(f.get("JQuants取得状態", "")),
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
    out["割安判定"] = out.apply(
        lambda rr: "⚠️ 分割補正確認" if "⚠️" in str(rr.get("株式分割補正", "")) else
        "⚠️ 最新性未確認" if rr.get("財務情報鮮度") != "Yahoo期末日を確認" or rr.get("ファンダ取得状態") != "OK" else
        ("⚠️ 異常値ガード・算定不能" if "⚠️" in str(rr.get("適正株価異常値ガード", "")) and not np.isfinite(safe_float(rr.get("参考価値上昇余地%"))) else
         "算定不能" if not np.isfinite(safe_float(rr.get("参考価値上昇余地%"))) else
         "🟢 割安" if safe_float(rr.get("参考価値上昇余地%")) >= 20 else
         "🟡 やや割安" if safe_float(rr.get("参考価値上昇余地%")) >= 5 else
         "⚪ 適正圏" if safe_float(rr.get("参考価値上昇余地%")) > -10 else "🔴 割高"),
        axis=1
    )
    # 取得不可の中立点を企業価値スコアとして競わせない。
    # 有効な評価を持つ銘柄を先に並べ、欠損は監査用に残す。
    out["企業価値算定済"] = (
        out["ファンダ取得状態"].eq("OK") &
        out["財務情報鮮度"].eq("Yahoo期末日を確認") &
        pd.to_numeric(out["参考価値上昇余地%"], errors="coerce").notna()
    )
    out["母集団件数"] = int(mother_count)
    out["詳細企業価値評価件数"] = int(pool_n)
    out = out.sort_values(["企業価値算定済", "AI_TOP50スコア", "流動性スコア"],
                          ascending=[False, False, False]).reset_index(drop=True)
    out.insert(0, "順位", np.arange(1, len(out) + 1))
    return out.head(int(max_rows)).copy(), audit


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
    x = x[cols].copy()
    for c in cols:
        x[c] = pd.to_numeric(x[c], errors="coerce")
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
    return x.dropna(subset=["MA25", "MA75", "ATR14", "Return_25d"])




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
                auto_adjust=False, actions=False, progress=False, threads=True,
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
                keep = [c for c in ["日付","Open","High","Low","Close","Adj Close","Volume"] if c in one.columns]
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
def batch_stock_data_light(tickers_tuple, months=15):
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
    low_n = x["Low"].rolling(int(k_period), min_periods=int(k_period)).min()
    high_n = x["High"].rolling(int(k_period), min_periods=int(k_period)).max()
    den = (high_n - low_n).replace(0, np.nan)
    fast_k = (x["Close"] - low_n) / den * 100.0
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
    if "最新性未確認" in text:
        return "⚠️ 鮮度未確認"
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
        valuation_text = f"銘柄判定：{value_text}"
        is_overvalued = side == "buy" and "割高" in str(row.get("割安判定", ""))
        if is_overvalued:
            valuation_text += "（買い見送ってください）"
        k_text = _mobile_number(row.get("%K"), 1)
        d_text = _mobile_number(row.get("%D"), 1)
        stop_hit = side == "sell" and "損切り" in str(row.get("売り理由", ""))
        trend_text = html_escape(_trade_trend_text(row.get("コード"), trend_by_code, side, stop_hit))

        if side == "sell":
            shares_text = _mobile_number(row.get("保有株数"), 0, "株")
            pnl_text = _mobile_number(row.get("損益率%"), 1, "%")
            reason_text = _mobile_text(row.get("売り理由"))
            signal_text = "損切り" if stop_hit else "DC"
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
            shares = int(max(safe_float(row.get("参考S株数"), 0), 0))
            can_buy = "BUY" in str(row.get("買付可否", "")) and shares > 0
            action_text = "⛔ 買い見送ってください" if is_overvalued else "🟢 買い" if can_buy else "⛔ 見送り"
            card_class = "skip-card" if is_overvalued else "buy-card" if can_buy else "skip-card"
            quantity_text = "割高" if is_overvalued else f"参考 {shares:,}株" if can_buy else _mobile_text(row.get("買付可否"), "購入不可")
            detail_text = (
                f"<span class=\"trade-valuation\">{valuation_text}</span>"
                f"<span class=\"trade-trend\">{trend_text}</span>"
                f"<span>K {k_text} / D {d_text}</span>"
                f"<span class=\"trade-card-secondary\">現在 {price_text}</span>"
                f"<span class=\"trade-card-secondary\">{surge_text}</span>"
            )

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
        # TOP50外の保有銘柄も、現在情報を取得できる場合は企業価値を個別算定する。
        if not str(value_label).strip():
            symbol = ticker if ticker.endswith(".T") else ticker + ".T"
            history = price_data.get(symbol, pd.DataFrame()) if isinstance(price_data, dict) else pd.DataFrame()
            price_hint = safe_float(history["Close"].iloc[-1]) if isinstance(history, pd.DataFrame) and not history.empty else np.nan
            f = (fundamental_snapshot(symbol, market_price_hint=price_hint,
                                      jq_api_key=jquants_api_key())
                 if np.isfinite(price_hint) and price_hint > 0 else {})
            upside = safe_float(f.get("参考価値上昇余地"))
            split_status = str(f.get("株式分割補正", ""))
            guard_status = str(f.get("適正株価異常値ガード", ""))
            if f.get("財務情報鮮度") != "Yahoo期末日を確認" or f.get("取得状態") != "OK":
                value_label = "⚠️ 最新性未確認"
            elif "⚠️" in split_status:
                value_label = "⚠️ 分割補正確認"
            elif "⚠️" in guard_status and not np.isfinite(upside):
                value_label = "⚠️ 異常値ガード・算定不能"
            elif not np.isfinite(upside):
                value_label = "算定不能"
            elif upside >= 20:
                value_label = "🟢 割安"
            elif upside >= 5:
                value_label = "🟡 やや割安"
            elif upside > -10:
                value_label = "⚪ 適正圏"
            else:
                value_label = "🔴 割高"
        rows.append({"銘柄コード": ticker, "銘柄名": holding.get("name") or name(ticker),
                     "保有株数": int(holding["shares"]), "判定": verdict,
                     "銘柄判定": _short_value_label(value_label)})
    return sorted(rows, key=lambda row: (row["判定"] != "売り", row["銘柄コード"]))


def render_current_holdings(rows):
    """現在保有銘柄の売買判定と割安・割高判定を表示する。"""
    st.subheader("現在保有中の銘柄")
    if not rows:
        st.dataframe(pd.DataFrame([{
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
        '<div class="holding-grid holding-head"><span>銘柄コード</span><span>銘柄名</span>'
        '<span>保有株数</span><span>売買判定</span><span>銘柄判定</span></div>', unsafe_allow_html=True,
    )
    for row in rows:
        verdict = row["判定"]
        tone = "sell" if verdict == "売り" else "keep" if verdict == "保有継続" else "pending"
        st.markdown(
            f'<div class="holding-grid holding-row {tone}">'
            f'<span>{html_escape(str(row["銘柄コード"]))}</span>'
            f'<span class="holding-name">{html_escape(str(row["銘柄名"]))}</span>'
            f'<span class="holding-shares">{int(row["保有株数"]):,}</span>'
            f'<span class="holding-verdict">{html_escape(verdict or "—")}</span>'
            f'<span class="holding-value">{html_escape(str(row.get("銘柄判定") or "—"))}</span></div>',
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
    st.dataframe(df, use_container_width=True, hide_index=True,
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


st.title("📈 日本株 AI投資アシスタント Ver.17.17")
st.caption(f"{VERSION} / BUILD: {BUILD}")
st.success("本物の企業価値AI TOP50 → Slow Stochastic 14,3,3 → BUY候補だけをシンプル表示")
st.caption("一次選抜は5年OOS検証済み：流動性45% / トレンド30% / 値動き安定性25%")
st.caption("母集団は日本株の売買代金上位を数百銘柄自動取得。旧49銘柄の固定リストは新規BUY選定には使いません。")

# ------------------------------------------------------------
# 朝の入力：ここだけは常に見える
# ------------------------------------------------------------
st.header("朝の入力")
input_left, input_right = st.columns([1.35, 1.0], gap="large")
with input_left:
    trade_files = st.file_uploader(
        "SBI 約定履歴CSV", type=["csv"], accept_multiple_files=True, key="sbi_execution_csvs_v177"
    )
with input_right:
    if "sbi_buying_power_yen_v177" not in st.session_state:
        st.session_state["sbi_buying_power_yen_v177"] = 0
    buying_power = st.number_input(
        "現物買付余力（円）", min_value=0, max_value=1_000_000_000, step=1000,
        key="sbi_buying_power_yen_v177"
    )

# 約定履歴 → 保有復元
confirmed = {}
sbi_trades_df = pd.DataFrame()
sbi_warning_df = pd.DataFrame()
if trade_files:
    parsed_parts, parse_errors = [], []
    for f in trade_files:
        try:
            part, _enc = parse_sbi_execution_csv(f)
            if not part.empty:
                parsed_parts.append(part)
        except Exception as e:
            parse_errors.append(f"{f.name}: {e}")
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
held_codes = list(confirmed.keys())

if int(buying_power) <= 0:
    st.warning("買付余力を入力すると参考S株数を計算できます。")
if not sbi_warning_df.empty:
    st.error("履歴不足の可能性がある銘柄があります。警告対象は安全のため保有復元から除外しています。")

# ------------------------------------------------------------
# 管理者設定は目立たせない
# ------------------------------------------------------------
with st.expander("⚙ 管理者設定", expanded=False):
    a1, a2, a3, a4 = st.columns(4)
    current_assets = a1.number_input("現在資産（円）", 10000, 200000000, 600000, 10000, key="v177_assets")
    maxbuy = a2.number_input("1銘柄最大購入額（円）", 1000, 20000000, 100000, 1000, key="v177_maxbuy")
    sl = a3.slider("損切りセンサー（%）", 3.0, 15.0, 7.0, .5, key="v177_sl")
    maxpos = a4.number_input("最大保有銘柄数", 1, 50, 10, key="v177_maxpos")
    b1, b2, b3, b4 = st.columns(4)
    reserve_pct = b1.slider("現金温存率（%）", 0, 80, 20, 5, key="v177_reserve")
    daily_deploy_pct = b2.slider("1日使用上限（%）", 10, 100, 50, 5, key="v177_daily")
    risk_per_trade_pct = b3.slider("1銘柄許容損失（資産比%）", 0.25, 3.0, 1.0, 0.25, key="v177_risk")
    price_buffer_pct = b4.slider("価格上振れバッファ（%）", 0.0, 10.0, 3.0, .5, key="v177_buffer")
    c1, c2 = st.columns(2)
    universe_size = c1.number_input("日本株母集団（売買代金上位）", 200, 500, 350, 25, key="v177_universe_n")
    fundamental_pool_size = c2.number_input("詳細企業価値評価へ進める一次選抜数", 60, 150, 90, 10, key="v177_fund_pool")
    st.caption("標準：350銘柄 → 5年OOS検証済み一次選抜（45/30/25）で90銘柄 → 企業価値AIでTOP50。BUYはそのTOP50だけ。")
    churn_filter = st.checkbox("短期クロス反転が多い銘柄を新規買いTOP50から除外", value=True, key="v1716_churn_filter")
    st.caption("試験設定：20日で5回以上のクロス＋25日騰落率±5%以内、または30日で買いGC後5日以内のDCが2回以上。損切り・保有銘柄の売り判定には適用しません。")
    quality_mode = st.checkbox("トレンド＋振れ幅フィルターを実売買判定に使用（検証後に切替）",
                               value=False, key="v1717_quality_mode")
    st.caption("オフは従来判定。TOP50一次選抜には直近のGC後5日成績を最大5点の小幅減点として加えます。")

run_clicked = st.button("▶ 今日の判定を更新", type="primary", use_container_width=True, key="v177_run")

if run_clicked:
    # 1) 数百銘柄の母集団を動的取得
    with st.spinner("日本株の大きな母集団を取得中…"):
        universe_df = discover_japan_stock_universe(int(universe_size))
    st.session_state["v177_universe_df"] = universe_df
    if isinstance(universe_df, pd.DataFrame) and not universe_df.empty:
        for _, rr in universe_df.iterrows():
            STOCK_NAMES[str(rr["コード"])] = str(rr["銘柄名"])

    # 「本物TOP50」の最低条件。失敗時に旧49銘柄へ戻さない。
    if not isinstance(universe_df, pd.DataFrame) or len(universe_df) < 200:
        st.session_state["v177_data"] = {}
        st.session_state["v177_daily_bar_status"] = pd.DataFrame()
        st.session_state["v177_value_top50"] = pd.DataFrame()
        st.session_state["v1716_churn_audit"] = pd.DataFrame()
        st.session_state["v177_run_error"] = "大規模母集団を200銘柄以上取得できなかったため、BUY判定を安全停止しました。旧49銘柄へは戻していません。"
    else:
        universe_codes = universe_df["コード"].astype(str).tolist()
        # SELL監視のため保有銘柄は母集団外でも追加する。
        all_codes = list(dict.fromkeys(universe_codes + [str(c) for c in held_codes]))
        all_tickers = tuple(tickers(",".join(all_codes)))
        with st.spinner(f"{len(all_tickers)}銘柄の日足を一括取得中…"):
            data = batch_stock_data_light(all_tickers, months=15)
        st.session_state["v177_data"] = data
        st.session_state["v177_daily_bar_status"] = build_daily_bar_status(data)
        # データ取得成功数が小さすぎる場合も偽TOP50を作らない。
        mother_data = {t:d for t,d in data.items() if code(t) in set(universe_codes)}
        if len(mother_data) < 150:
            st.session_state["v177_value_top50"] = pd.DataFrame()
            st.session_state["v1716_churn_audit"] = pd.DataFrame()
            st.session_state["v177_run_error"] = f"日足取得成功が{len(mother_data)}銘柄のみのため、BUY判定を安全停止しました。"
        else:
            with st.spinner("企業価値AIで一次選抜 → 本物TOP50を作成中…"):
                value_top50_df, churn_audit_df = build_value_ai_top50(
                    mother_data, max_rows=50, fundamental_pool_size=int(fundamental_pool_size),
                    churn_filter=bool(churn_filter),
                )
            st.session_state["v177_value_top50"] = value_top50_df
            st.session_state["v1716_churn_audit"] = churn_audit_df
            st.session_state["v177_generated_at"] = tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST")
            st.session_state["v177_run_error"] = ("反転検査後の候補が50銘柄未満のため新規買い判定を停止しました。"
                                                  if value_top50_df.empty else "")

universe_df = st.session_state.get("v177_universe_df", pd.DataFrame())
data = st.session_state.get("v177_data", {})
value_top50_df = st.session_state.get("v177_value_top50", pd.DataFrame())
churn_audit_df = st.session_state.get("v1716_churn_audit", pd.DataFrame())
run_error = st.session_state.get("v177_run_error", "")
daily_bar_status_df = st.session_state.get("v177_daily_bar_status", pd.DataFrame())

# ------------------------------------------------------------
# TOP50だけを新規BUY対象にする。SELLは保有全銘柄を監視。
# ------------------------------------------------------------
buy_view = pd.DataFrame()
sell_view = pd.DataFrame()
buy_signal_rows, sell_signal_rows = [], []
signal_audit_rows = []
true_top50_codes = set()
value_judgement_by_code = {}
if isinstance(value_top50_df, pd.DataFrame) and not value_top50_df.empty:
    value_meta = value_top50_df.head(50).copy()
    value_meta["コード"] = value_meta["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
    true_top50_codes = set(value_meta["コード"].tolist())
    if "割安判定" in value_meta.columns:
        value_judgement_by_code = (
            value_meta.drop_duplicates("コード").set_index("コード")["割安判定"].astype(str).to_dict()
        )
value_unrated_count = (int((~value_top50_df["企業価値算定済"].fillna(False).astype(bool)).sum())
                       if isinstance(value_top50_df, pd.DataFrame) and "企業価値算定済" in value_top50_df else 0)
held_code_set = set(map(str, held_codes))

if data and true_top50_codes:
    for t, df0 in data.items():
        c = code(t)
        # 新規BUYは本物TOP50のみ、SELLは保有のみ。それ以外は計算不要。
        if c not in true_top50_codes and c not in held_code_set:
            continue
        sx = stoch_quality_frame(df0, sl)
        if sx.empty:
            continue
        r = sx.iloc[-1]
        sk, sd, px = safe_float(r.get("STOCH_K")), safe_float(r.get("STOCH_D")), safe_float(r.get("Close"))
        if not (np.isfinite(px) and px > 0):
            continue
        buy_old = bool(r.get("BUY_現行", False))
        buy_new = bool(r.get("BUY_改善", False))
        sell_old = bool(r.get("SELL_現行", False))
        sell_new = bool(r.get("SELL_改善", False))
        buy_signal_hit = buy_new if quality_mode else buy_old
        value_judgement = value_judgement_by_code.get(c, "— 未算定")
        overvalued = "割高" in str(value_judgement)
        new_buy_target = c in true_top50_codes and c not in held_code_set
        valuation_ready = bool(c in value_judgement_by_code and
                               value_judgement not in ("算定不能", "— 未算定") and
                               "⚠️" not in str(value_judgement))
        final_buy_hit = bool(new_buy_target and buy_signal_hit and valuation_ready and not overvalued)
        buy_skip_reason = ("企業価値が未算定のため新規買い停止" if new_buy_target and buy_signal_hit and not valuation_ready
                           else "割高判定のため新規買い除外" if new_buy_target and buy_signal_hit and overvalued else "")
        if c in true_top50_codes or c in held_code_set:
            signal_audit_rows.append({"コード": c, "銘柄名": name(t), "監視対象": "保有" if c in held_code_set else "TOP50",
                                      "現行買い": buy_old, "改善買い": buy_new,
                                      "採用方式の買いシグナル": bool(buy_signal_hit),
                                      "銘柄判定": value_judgement,
                                      "割高除外": bool(overvalued and new_buy_target),
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
                "コード": c, "銘柄名": name(t), "総合AIスコア": 100.0-float(sk),
                "現在株価": float(px), "%K": float(sk), "%D": float(sd),
                "条件": "買い条件成立＋割高除外通過",
            })
        if c in held_code_set:
            h = confirmed.get(c, {})
            shares = int(safe_float(h.get("shares", 0)) or 0)
            avg = safe_float(h.get("avg_price", np.nan))
            pnl_pct = ((px / avg - 1.0) * 100.0) if np.isfinite(avg) and avg > 0 else np.nan
            dc_hit = (sell_new if quality_mode else sell_old) and np.isfinite(sk)
            stop_hit = np.isfinite(pnl_pct) and pnl_pct <= -float(sl)
            if dc_hit or stop_hit:
                reasons=[]
                if stop_hit: reasons.append(f"損切り -{float(sl):.1f}%")
                if dc_hit: reasons.append("トレンド確認DC" if quality_mode else "ストキャスDC")
                sell_signal_rows.append({
                    "コード":c,"銘柄名":name(t),"保有株数":shares,"取得単価":avg,"現在株価":float(px),
                    "損益率%":pnl_pct,"%K":float(sk) if np.isfinite(sk) else np.nan,
                    "%D":float(sd) if np.isfinite(sd) else np.nan,"売り理由":"＋".join(reasons),
                })

signal_audit_df = pd.DataFrame(signal_audit_rows)
if not signal_audit_df.empty and "買い見送り理由" in signal_audit_df.columns:
    overvalued_buy_exclusions_df = signal_audit_df[
        signal_audit_df["買い見送り理由"].eq("割高判定のため新規買い除外")
    ].copy()
else:
    overvalued_buy_exclusions_df = pd.DataFrame(columns=[
        "コード", "銘柄名", "銘柄判定", "採用方式の買いシグナル", "最終買い", "買い見送り理由"
    ])

# カード表示だけに使う。買い・売りの採用判定やZIPの列には接続しない。
trend_by_code = {str(r["コード"]): r for r in signal_audit_rows}

if buy_signal_rows:
    buy_signal_df = pd.DataFrame(buy_signal_rows).sort_values(["%K","コード"]).reset_index(drop=True)
    plan = build_purchase_plan(
        buy_signal_df, buying_power, current_assets, held_codes,
        max(int(maxpos), len(held_code_set)+3), maxbuy, sl,
        reserve_pct, daily_deploy_pct, risk_per_trade_pct, price_buffer_pct,
        allow_addon=False, market_block=False,
    )
    buy_view = plan.merge(buy_signal_df[["コード","%K","%D","条件"]], on="コード", how="left")
    buy_view = buy_view.rename(columns={"購入株数":"参考S株数","予定購入額":"概算購入額"})
    meta_cols=[c for c in ["コード","順位","AI_TOP50スコア","割安判定"] if c in value_top50_df.columns]
    if meta_cols:
        m=value_top50_df[meta_cols].copy(); m["コード"]=m["コード"].astype(str); buy_view["コード"]=buy_view["コード"].astype(str)
        buy_view=buy_view.merge(m,on="コード",how="left")
    cols=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","%K","%D","AI_TOP50スコア","割安判定","買付可否"]
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

# 管理者研究用。実売買には一切使わない3方式比較。
indicator_compare_df = build_indicator_compare(data, true_top50_codes, held_codes) if data and true_top50_codes else pd.DataFrame()
if not isinstance(indicator_compare_df, pd.DataFrame):
    indicator_compare_df = pd.DataFrame()

# 管理者研究用。旧Ver.5.5系の急騰予兆を企業価値AI TOP50だけへ適用。
surge_top50_df = build_top50_surge_radar(data, value_top50_df) if data and true_top50_codes else pd.DataFrame()
if not isinstance(surge_top50_df, pd.DataFrame):
    surge_top50_df = pd.DataFrame()

# 画面に出るすべての銘柄表で、研究判定を左端に統一表示する。
# 表示専用の付加情報であり、正式な売買ロジックには接続しない。
buy_view = add_research_judgements_first(buy_view, surge_top50_df, value_top50_df)
sell_view = add_research_judgements_first(sell_view, surge_top50_df, value_top50_df)
indicator_compare_df = add_research_judgements_first(indicator_compare_df, surge_top50_df, value_top50_df)

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
    for code4, rows in state["rows"].items():
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


def _fl_backtest(prices, events, discount=0.25, fee_bps=10.0):
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
            nxt = p.iloc[i + 1]
            if nxt["date"] > end or nxt["aOpen"] <= 0:
                continue
            timeline.setdefault(nxt["date"], []).append(
                (code4, float(fair), float(row["aClose"]), str(current["disc_date"].date()),
                 float(nxt["aOpen"]), float(row["ma50"])))
    dates = sorted(prices.loc[prices["date"].between(start, end), "date"].unique())
    positions, trades, curve = {}, [], []
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
            del positions[code4]
        for code4, target, signal_close, disc_date, entry, _ in sorted(
            timeline.get(day, []), key=lambda x: (x[2] / x[1], x[0])
        ):
            if code4 in positions or len(positions) >= 5 or entry >= target * (1 - discount):
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
    return summary, tdf, edf


def render_fundamental_lab():
    st.subheader("🧪 ファンダメンタル単独・2年バックテスト")
    st.caption("研究専用です。今日の買い・売り判定には反映しません。ストキャスティクスは使いません。")
    st.info("無料プランは過去2年・12週間遅延です。財務履歴の取得には対象銘柄数に応じて時間がかかります。")
    st.caption("試験用の適正株価＝予想EPS×ROE連動PERとBPS×ROE連動PBRの平均。25%割安で上昇トレンドなら翌営業日寄付で買い、買った時点の適正株価または-7%に触れたら売ります。上場廃止銘柄は含まれません。")
    source = st.file_uploader("5年日足ZIP（管理者タブで出力したファイル）", type="zip", key="fl_zip")
    key = jquants_api_key()
    if not key:
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
    if st.button("財務履歴の取得を開始・再開", disabled=state["running"], key="fl_start"):
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
            st.dataframe(pd.DataFrame([{"コード":c,"理由":v} for c,v in state["errors"].items()]))
    if not state["rows"]:
        return
    events = _fl_events(state)
    if not events.empty:
        st.download_button("取得済み財務履歴CSVを保存", events.to_csv(index=False).encode("utf-8-sig"),
                           "fundamental_history_observed.csv", "text/csv", key="fl_export")
    discount = st.slider("買い時の必要割安率", 10, 50, 25, 5, format="%d%%", key="fl_discount")
    fee_bps = st.number_input("片道の売買コスト（bps）", 0., 100., 10., 1., key="fl_fee")
    if st.button("取得済み銘柄でバックテスト", key="fl_run"):
        try:
            summary, trades, curve = _fl_backtest(prices, events, discount/100, fee_bps)
            st.session_state.fl_result = (summary, trades, curve, len(state["done"]) == len(codes))
        except Exception as exc:
            st.error(f"バックテストを実行できません: {exc}")
    if "fl_result" in st.session_state:
        summary, trades, curve, complete = st.session_state.fl_result
        st.warning("一部銘柄のみの途中結果です。全銘柄の比較には使わないでください。" if not complete else
                   "現在の母集団による研究結果です。上場廃止銘柄の欠落と株式分割の近似調整に注意してください。")
        st.dataframe(pd.DataFrame([summary]), use_container_width=True, hide_index=True)
        st.line_chart(curve.set_index("日付")["総資産"])
        st.dataframe(trades, use_container_width=True, hide_index=True)
        buf = io.BytesIO()
        with ZipFile(buf, "w") as zf:
            zf.writestr("summary.csv", pd.DataFrame([summary]).to_csv(index=False, encoding="utf-8-sig"))
            zf.writestr("trades.csv", trades.to_csv(index=False, encoding="utf-8-sig"))
            zf.writestr("equity.csv", curve.to_csv(index=False, encoding="utf-8-sig"))
            zf.writestr("fundamentals.csv", events.to_csv(index=False, encoding="utf-8-sig"))
        st.download_button("検証結果ZIP", buf.getvalue(), "fundamental_lab_2y.zip", "application/zip", key="fl_results")


# ------------------------------------------------------------
# シンプル画面 + 目立たない管理者タブ
# ------------------------------------------------------------
main_tab, admin_tab, fundamental_tab = st.tabs(["今日の売買", "管理者", "🧪 テスト"])
with fundamental_tab:
    render_fundamental_lab()
with main_tab:
    m1, m2 = st.columns(2)
    m1.metric("現在保有", f"{len(held_codes)}銘柄")
    m2.metric("買付余力", f"¥{int(buying_power):,}")
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
    render_current_holdings(current_holdings_rows(
        confirmed, sell_view, signal_audit_rows, value_top50_df, data
    ))
    if run_error:
        st.error(run_error)
    elif not data or not true_top50_codes:
        st.info("「今日の判定を更新」を押してください。")
    else:
        if value_unrated_count:
            st.warning(f"企業価値を算定できない銘柄がTOP50中{value_unrated_count}件あります。該当銘柄の新規買いは停止しています。日足による保有銘柄の売り判定は表示します。")
            if not jquants_api_key():
                st.caption("J-Quants無料プランの補完はAPIキー未設定のため停止中です。")
        st.subheader("🔻 売り")
        if sell_view.empty:
            st.success("売り候補はありません。")
        else:
            render_mobile_trade_cards(sell_view, "sell", trend_by_code)
            with st.expander("売り候補の詳細表", expanded=False):
                sell_display = sell_view.rename(columns={"割安判定": "銘柄判定"})
                st.dataframe(sell_display, use_container_width=True, hide_index=True)

        st.subheader("🟢 買い")
        if not overvalued_buy_exclusions_df.empty:
            st.caption(f"割高判定により {len(overvalued_buy_exclusions_df)}銘柄を買い候補から除外しました。")
        if buy_view.empty:
            st.info("企業価値を算定でき、現在BUY条件も満たす銘柄はありません。" if value_unrated_count else
                    "本物の企業価値AI TOP50内に、現在BUY条件を満たす銘柄はありません。")
        else:
            render_mobile_trade_cards(buy_view, "buy", trend_by_code)
            simple_buy_cols=[c for c in ["急騰予兆判定","割安判定","順位","コード","銘柄名","現在株価","参考S株数","%K","%D","買付可否"] if c in buy_view.columns]
            with st.expander("買い候補の詳細表", expanded=False):
                buy_display = buy_view[simple_buy_cols].rename(columns={"割安判定": "銘柄判定"}).copy()
                if "銘柄判定" in buy_display and "買付可否" in buy_display:
                    overvalued = buy_display["銘柄判定"].astype(str).str.contains("割高", na=False)
                    buy_display.loc[overvalued, "買付可否"] = "⛔ 割高：買い見送ってください"
                st.dataframe(buy_display, use_container_width=True, hide_index=True)
        st.caption(f"新規BUY監視：企業価値AI TOP50のみ / SELL監視：現在保有 {len(held_codes)}銘柄")

with admin_tab:
    st.caption("比較・検証用。通常の朝はここを見る必要はありません。")
    ucnt = len(universe_df) if isinstance(universe_df, pd.DataFrame) else 0
    dcnt = len([1 for t in data if code(t) not in held_code_set]) if data else 0
    tcnt = len(value_top50_df) if isinstance(value_top50_df, pd.DataFrame) else 0
    c1,c2,c3,c4=st.columns(4)
    c1.metric("母集団", f"{ucnt}銘柄")
    c2.metric("日足取得", f"{dcnt}銘柄")
    c3.metric("AI TOP", f"{tcnt}銘柄")
    c4.metric("保有", f"{len(held_codes)}銘柄")

    with st.expander("🔎 TOP50選定前の短期反転チェック", expanded=False):
        if isinstance(churn_audit_df, pd.DataFrame) and not churn_audit_df.empty:
            excluded = churn_audit_df[churn_audit_df["判定"].eq("新規買い除外")]
            st.caption(f"新規買い除外：{len(excluded)} / 検査対象：{len(churn_audit_df)}。閾値は試験設定で、成績改善は未検証です。")
            st.dataframe(excluded, use_container_width=True, hide_index=True,
                         height=admin_table_height(excluded, 480))
        else:
            st.info("『今日の判定を更新』後に除外理由を表示します。")

    with st.expander("🕒 日足データの鮮度", expanded=False):
        if isinstance(daily_bar_status_df, pd.DataFrame) and not daily_bar_status_df.empty:
            st.dataframe(daily_bar_status_df, use_container_width=True, hide_index=True,
                         height=admin_table_height(daily_bar_status_df, 520))
            st.caption("16:00以降は直近東証取引日のOHLCVを確認し、履歴が遅れている銘柄だけ当日分を補完します。")
        else:
            st.info("『今日の判定を更新』後に日足最終日と取得元を表示します。")

    value_tab, surge_tab, compare_tab, holdings_tab = st.tabs([
        "企業価値", "急騰予兆", "3指標比較", "保有銘柄"
    ])

    with value_tab:
        st.caption("企業価値AI TOP50：重要列だけを先に表示します。")
        st.caption("財務の鮮度は開示日・期末日で確認します。J-Quants無料版は12週間遅延するため、最新開示との一致は確認できません。")
        if not isinstance(value_top50_df, pd.DataFrame) or value_top50_df.empty:
            st.info("先に『今日の判定を更新』で企業価値AI TOP50を作成してください。")
        else:
            value_show = add_research_judgements_first(value_top50_df, surge_top50_df, value_top50_df)
            show_cols=[
                "急騰予兆判定","割安判定","順位","コード","銘柄名","一次選抜順位",
                "現在株価_価格","AI参考価値","参考価値上昇余地%","企業価値スコア",
                "成長性スコア","流動性スコア","AI_TOP50スコア",
                "財務情報源","財務情報鮮度","Yahoo期末日","JQuants開示日",
                "JQuants開示経過日数","JQuants取得状態","ファンダ取得状態",
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
            st.caption("過去時点のOHLCVだけで作るTOP50フィルターを70%学習 / 30%未学習で検証。企業価値ファンダメンタル自体の過去再現ではありません。")
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
                st.dataframe(summary, use_container_width=True, hide_index=True)
                st.caption("終値で判定し、翌営業日の始値で約定。最終日の未決済は集計から分離。PFは取引騰落率の単純合算比で、ポートフォリオの資産推移・最大DDを示すものではありません。")
                for label, frame, filename in [("決済明細CSV", trades, "stoch_backtest_trades.csv"),
                                               ("未決済明細CSV", unrealized, "stoch_backtest_open.csv")]:
                    st.download_button(label, data=frame.to_csv(index=False).encode("utf-8-sig"),
                                       file_name=filename, mime="text/csv", key="v1717_" + filename)

        with st.expander("🧪 割高除外・保有買い増しの比較バックテスト", expanded=False):
            st.caption("5年日足で現行売買と買い増しを比較。判定日の終値で判断し、翌営業日の始値で約定します。")
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
                st.dataframe(summary, use_container_width=True, hide_index=True)
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
        st.caption("研究専用。実売買はStoch 14,3,3 / %K≤20 GCのみです。")
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
            held_show = add_research_judgements_first(held_show, surge_top50_df, value_top50_df)
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
            "日本株母集団設定":int(universe_size),"取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "詳細企業価値評価件数設定":int(fundamental_pool_size),"TOP50件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "新規BUY対象":"企業価値AI TOP50のみ","BUY条件":"トレンド＋振れ幅＋GC" if quality_mode else "Slow Stoch 14,3,3 / %K<=20 GC","管理者比較":"RSI5 / BB20 / 急騰予兆（すべて実売買には不使用）",
            "割高の新規BUY除外":True,
            "SELL条件":f"保有銘柄のみ / {'トレンド確認DC' if quality_mode else 'Slow Stoch DC'} または 損切り -{float(sl):.1f}%",
            "旧49銘柄固定ユニバース使用":False,
            "短期反転フィルター":bool(churn_filter),
            "トレンド振れ幅フィルター実売買":bool(quality_mode),
        }])
        zf.writestr("ver17_settings.csv",settings_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(universe_df,pd.DataFrame):
            zf.writestr("japan_large_universe.csv",universe_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(value_top50_df,pd.DataFrame):
            zf.writestr("value_ai_top50.csv",value_top50_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(churn_audit_df,pd.DataFrame):
            zf.writestr("stoch_churn_audit.csv",churn_audit_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("stoch_signal_quality_audit.csv", signal_audit_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("overvalued_buy_exclusions.csv", overvalued_buy_exclusions_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(daily_bar_status_df,pd.DataFrame):
            zf.writestr("daily_bar_freshness.csv",daily_bar_status_df.to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_trades_df.empty:
            zf.writestr("sbi_execution_history.csv",sbi_trades_df.to_csv(index=False,encoding="utf-8-sig"))
        if confirmed:
            zf.writestr("current_holdings.csv",pd.DataFrame([{"コード":c,"銘柄名":name(c),"株数":v["shares"],"取得単価":v["avg_price"]} for c,v in confirmed.items()]).to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_warning_df.empty:
            zf.writestr("sbi_history_warnings.csv",sbi_warning_df.to_csv(index=False,encoding="utf-8-sig"))
        buy_cols_default=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","%K","%D","AI_TOP50スコア","割安判定","買付可否"]
        buy_export=buy_view.copy() if isinstance(buy_view,pd.DataFrame) and not buy_view.empty else 
