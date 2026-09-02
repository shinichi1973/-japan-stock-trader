# ============================================================
# 日本株 AI投資アシスタント Ver.6.0
# BUILD: VER6.0-RC6.7-TRADINGVIEW-BATCH-DIAGNOSTIC-20260903
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
import plistlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import unescape as html_unescape
from html.parser import HTMLParser
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.6.0",
    page_icon="📈",
    layout="wide",
)

VERSION = "6.0 RC6.7 TRADINGVIEW-BATCH-DIAGNOSTIC"
BUILD = "VER6.0-RC6.7-TRADINGVIEW-BATCH-DIAGNOSTIC-20260903"

JST = ZoneInfo("Asia/Tokyo")
TRADINGVIEW_QUOTES_CACHE = {}


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


@st.cache_data(ttl=900)
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


def _merge_tradingview_snapshot(frame, ticker):
    """Yahoo履歴の欠落した確定日だけTradingViewの東証OHLCVで補う。"""
    quote = TRADINGVIEW_QUOTES_CACHE.get(str(ticker))
    if frame is None or frame.empty or not quote:
        return frame, False, "一括価格なし"
    required_date = frame.attrs.get("regular_market_date", pd.NaT)
    required_date = pd.Timestamp(required_date).normalize() if pd.notna(required_date) else pd.NaT
    latest = pd.Timestamp(frame.index.max()).normalize()
    if pd.isna(required_date):
        return frame, False, "必要日を確認できないため不採用"
    if latest >= required_date:
        return frame, False, "既存日足が最新"
    hour = tokyo_now().hour
    if 9 <= hour < 16:
        return frame, False, "取引時間中の未確定日足は不採用"
    attrs = dict(frame.attrs)
    raw = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    raw.loc[required_date, ["Open", "High", "Low", "Close", "Volume"]] = [
        quote["Open"], quote["High"], quote["Low"], quote["Close"], max(quote["Volume"], 0.0)
    ]
    repaired = _add_indicators(raw.sort_index())
    if repaired.empty or pd.Timestamp(repaired.index.max()).normalize() < required_date:
        return frame, False, "指標再計算後も必要日に届かない"
    repaired.attrs.update(attrs)
    repaired.attrs["source"] = "TradingView東証スキャナー一括取得（無料） + 既存履歴"
    repaired.attrs["latest_row_repaired"] = True
    return repaired, True, "TradingView一括OHLCVを採用"


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


# RC6.7: TradingView一括取得を最優先にし、個別サイトは予備経路にする。
@st.cache_data(ttl=1800)
def stock_data(t, years=5):
    start, end = yahoo_history_window(years)
    try:
        raw, meta = _yahoo_chart(t, years)
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs.update(meta)
            out, _, _ = _merge_tradingview_snapshot(out, t)
            return out
    except Exception:
        pass
    try:
        raw = yf.Ticker(t).history(start=start, end=end,
                                   auto_adjust=False, actions=False)
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance Ticker.history"
            out, _, _ = _merge_tradingview_snapshot(out, t)
            return out
    except Exception:
        pass
    try:
        raw = yf.download(t, start=start, end=end,
                          auto_adjust=False, progress=False, threads=False)
        out = _add_indicators(raw)
        if not out.empty:
            out.attrs["source"] = "yfinance download"
            out, _, _ = _merge_tradingview_snapshot(out, t)
            return out
    except Exception:
        pass
    return pd.DataFrame()


def _append_1321_proxy_market_row(stale_market, required_date):
    """1321.Tの当日騰落率を、直近日経平均終値へ連結して市場判定を更新する。"""
    required_date = pd.Timestamp(required_date).normalize()
    if stale_market is None or stale_market.empty:
        raise ValueError("連結元の日経平均履歴がありません")
    proxy = stock_data("1321.T", 5)
    if proxy.empty or required_date not in proxy.index:
        raise ValueError("1321.Tが必要日まで更新されていません")

    market_close = pd.to_numeric(stale_market["Close"], errors="coerce").dropna()
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
                    return _append_1321_proxy_market_row(out, required_date)
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
                    return _append_1321_proxy_market_row(out, required_date)
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
                    return _append_1321_proxy_market_row(out, required_date)
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

@st.cache_data(ttl=21600)
def fundamental_snapshot(t):
    """現在情報のみ。過去バックテストには使用しない。"""
    try:
        tk = yf.Ticker(t)
        info = tk.info or {}
        price = info_num(info, "currentPrice", "regularMarketPrice")
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

        # 現在価値レンジ：市場アナリスト目標価格がある場合は参考値として表示。
        # ない場合は「算出不能」とする。無理に一点の適正株価を作らない。
        if np.isfinite(target_mean) and target_mean > 0 and np.isfinite(price) and price > 0:
            fair_low = target_mean * .80
            fair_base = target_mean
            fair_high = target_mean * 1.20
            upside = (fair_base / price - 1) * 100
            fair_source = "Yahoo Finance targetMeanPriceを参考"
        else:
            fair_low = fair_base = fair_high = np.nan
            upside = np.nan
            fair_source = "適正株価レンジ算出材料不足"

        value_score = float(np.clip(
            valuation_score * .45 + growth_score * .35 +
            (70 if np.isfinite(upside) and upside >= 30 else
             55 if np.isfinite(upside) and upside >= 10 else
             40 if np.isfinite(upside) and upside >= 0 else 20 if np.isfinite(upside) else 50) * .20,
            0, 100
        ))

        return {
            "取得状態":"OK","現在株価":price,"時価総額":market_cap,
            "PER":trailing_pe,"予想PER":forward_pe,"PBR":price_to_book,
            "ROE":roe,"営業利益率":op_margin,"売上成長率":revenue_growth,
            "利益成長率":earnings_growth,"D/E":debt_to_equity,
            "流動比率":current_ratio,"FCF":free_cf,"目標株価参考":target_mean,
            "52週高値":fifty_two_high,"52週安値":fifty_two_low,
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
# SBI買付余力ファイル読取 / 購入株数プラン
# ------------------------------------------------------------
def _decode_text_bytes(raw):
    """CSV/TXT/HTMLなどを文字列化。Apple WebArchiveにも対応。"""
    if raw is None:
        return ""
    if not isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw)

    # Safari等で保存した .webarchive はbinary plistの場合がある。
    try:
        obj = plistlib.loads(raw)
        main = obj.get("WebMainResource", {}) if isinstance(obj, dict) else {}
        data = main.get("WebResourceData")
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
    except Exception:
        pass

    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8", "euc_jp"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_buying_power_from_file(uploaded_file):
    """SBI口座サマリー等の保存ファイルから買付余力を抽出する。

    優先順:
      1) 買付余力（2営業日後）
      2) 現物買付余力
      3) 買付余力
    スクリーンショット/OCRは使用しない。
    """
    raw = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    text = _decode_text_bytes(raw)
    # HTMLタグ・連続空白を簡易正規化
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = plain.replace("&nbsp;", " ").replace("&#44;", ",")
    plain = re.sub(r"[\u00a0\s]+", " ", plain)

    labels = [
        r"買付余力\s*[（(]?\s*2営業日後\s*[）)]?",
        r"現物買付余力",
        r"買付余力",
    ]
    for label in labels:
        m = re.search(label + r"[^0-9]{0,80}([0-9][0-9,]{0,20})\s*円?", plain, flags=re.I)
        if m:
            try:
                val = int(m.group(1).replace(",", ""))
                if 0 <= val <= 10_000_000_000:
                    return val, plain[:5000]
            except Exception:
                pass
    raise ValueError("買付余力の金額を自動検出できませんでした。手入力欄を使用してください。")


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
# サイドバー
# ------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Ver.6.0設定")
    initial = st.number_input("バックテスト初期資金（円）", 10000, 200000000, 600000, 10000)
    current_assets = st.number_input("現在資産（円）", 10000, 200000000, 600000, 10000)
    target_assets = st.number_input("最終目標資産（円）", 1000000, 1000000000, 100000000, 1000000)

    st.subheader("短期売買")
    maxpos = st.number_input("最大保有銘柄数", 1, 50, 10)
    maxbuy = st.number_input("1銘柄最大購入額（円）", 1000, 20000000, 100000, 1000)
    sl = st.slider("基本損切り（%）", 3.0, 15.0, 7.0, .5)
    tp = st.slider("利確目安（%）", 8.0, 50.0, 15.0, 1.0)
    rlo = st.slider("RSI下限", 25, 60, 40)
    rhi = st.slider("RSI上限", 60, 80, 70)
    mintech = st.slider("最低テクニカルスコア", 60, 95, 75)
    minbuy_score = st.slider("BUY最低AIスコア", 70, 95, 80)
    max_gap = st.slider("翌営業日寄付ギャップ許容（%）", 1.0, 10.0, 5.0, .5)

    st.subheader("購入株数・資金管理")
    reserve_pct = st.slider("買付余力の現金温存率（%）", 0, 80, 20, 5)
    daily_deploy_pct = st.slider("1日に使う余力上限（%）", 10, 100, 50, 5)
    risk_per_trade_pct = st.slider("1銘柄の許容損失（総資産比%）", 0.25, 3.0, 1.0, 0.25)
    price_buffer_pct = st.slider("寄付価格上振れバッファ（%）", 0.0, 10.0, 3.0, .5)
    allow_addon = st.checkbox("保有銘柄への買い増しを許可", False)

    st.subheader("連敗ブレーキ")
    cooldown = st.number_input("4連敗後のBUY停止日数", 5, 30, 10)
    risk_cooldown = st.number_input("9連敗後のBUY停止日数", 5, 45, 15)
    severe_cooldown = st.number_input("10連敗後のBUY停止日数", 10, 60, 20)

    use_liq = st.checkbox("流動性TOP50を使用", True)
    no_trade_on_bad_market = st.checkbox("市場悪化時はNO TRADE", True)

    st.subheader("分析対象")
    universe_text = st.text_area("分析対象銘柄コード", DEFAULT_UNIVERSE, height=130)

# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
st.title("📈 日本株 AI投資アシスタント Ver.6.0")
st.caption(f"BUILD: {BUILD}")
st.info(
    "Ver.5.5系を土台に、企業価値AI・テンバガーAI・保有銘柄AI・損切り/資金管理を維持し、"
    "保有銘柄はSBI証券『約定履歴CSV』から自動復元し、買付余力からS株の購入株数まで計算します。"
    "RC6.7では有料APIを使わず、TradingView公開スキャナーから東証銘柄を一括取得します。"
    "個別サイトへの連続アクセスを避け、みんかぶ・Stooq・Yahoo系は予備経路として残します。"
    "日経平均を直接取得できない場合に限り、1321.Tの当日騰落率を"
    "市場判定用の代理データとして使用します。代理使用は画面とCSVに明記し、"
    "1321.Tも古い場合は売買判定を停止します。"
)
st.success("📄 保有銘柄：SBI約定履歴CSV / 💴 購入株数：買付余力連動（スクショ/OCR完全除外）")

# ------------------------------------------------------------
# SBI約定履歴CSV
# ------------------------------------------------------------
st.header("📄 ① SBI約定履歴CSVから現在保有を自動復元")
st.caption(
    "SBI証券『口座管理 → 取引履歴 → 約定履歴』からCSVを保存し、そのままアップロードしてください。"
    "買付・売却を時系列で相殺し、現在株数と平均取得単価を自動計算します。"
)
trade_files = st.file_uploader(
    "SBI約定履歴CSVを追加",
    type=["csv"],
    accept_multiple_files=True,
    key="sbi_execution_csvs"
)

sbi_trades_df = pd.DataFrame()
sbi_lots_df = pd.DataFrame()
sbi_audit_df = pd.DataFrame()
sbi_warning_df = pd.DataFrame()
confirmed = {}

if trade_files:
    parsed_parts = []
    parse_errors = []
    encodings = []
    for f in trade_files:
        try:
            part, enc = parse_sbi_execution_csv(f)
            if not part.empty:
                parsed_parts.append(part)
                encodings.append(f"{f.name}: {enc}")
        except Exception as e:
            parse_errors.append(f"{f.name}: {e}")

    if parsed_parts:
        sbi_trades_df = pd.concat(parsed_parts, ignore_index=True)
        before = len(sbi_trades_df)
        sbi_trades_df = sbi_trades_df.drop_duplicates("_dedupe_key", keep="first").copy()
        dupes = before - len(sbi_trades_df)
        holdings_auto_df, sbi_lots_df, sbi_audit_df, sbi_warning_df = rebuild_holdings_from_trades(sbi_trades_df)

        if not holdings_auto_df.empty:
            confirmed = {
                str(r["code"]): {
                    "shares": int(r["shares"]),
                    "avg_price": float(r["avg_price"]),
                    "source": str(r["source"]),
                    "account_types": str(r.get("account_types", "")),
                }
                for _, r in holdings_auto_df.iterrows()
            }
            st.session_state["confirmed_holdings"] = confirmed
        else:
            st.session_state["confirmed_holdings"] = {}

        dates = sbi_trades_df["約定日"].dropna()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("約定明細", len(sbi_trades_df))
        c2.metric("現在保有", len(holdings_auto_df))
        c3.metric("重複除外", dupes)
        c4.metric("履歴警告", len(sbi_warning_df))
        if not dates.empty:
            st.caption(f"履歴範囲：{dates.min().date()} ～ {dates.max().date()} / " + " / ".join(encodings))

        if not holdings_auto_df.empty:
            st.subheader("✅ 自動復元した現在保有")
            display_holdings = holdings_auto_df.rename(columns={
                "code":"コード", "name":"銘柄名", "shares":"株数", "avg_price":"取得単価",
                "account_types":"預り区分", "history_status":"履歴完全性"
            })
            cols = [c for c in ["コード","銘柄名","株数","取得単価","預り区分","履歴完全性"] if c in display_holdings.columns]
            st.dataframe(display_holdings[cols], use_container_width=True, hide_index=True)
            st.success(f"🤖 保有AIへ {len(holdings_auto_df)}銘柄を自動登録しました。確認・手入力は不要です。")
        else:
            st.warning("CSVから現在保有を復元できませんでした。履歴範囲を確認してください。")

        if not sbi_warning_df.empty:
            st.error(
                "⚠️ 履歴開始前から保有していた可能性のある取引があります。"
                "該当銘柄は安全のため保有AIへ自動登録していません。より古い期間を含むCSVで再実行してください。"
            )
            st.dataframe(sbi_warning_df, use_container_width=True, hide_index=True)

        with st.expander("🔎 約定履歴からの復元監査ログ"):
            st.dataframe(sbi_audit_df, use_container_width=True, hide_index=True)

    if parse_errors:
        st.error("読み込めなかったCSVがあります。\n\n- " + "\n- ".join(parse_errors))
else:
    st.info("SBI約定履歴CSVをアップロードすると、現在保有を自動復元して保有AIへ渡します。")
    st.session_state["confirmed_holdings"] = {}

confirmed = st.session_state.get("confirmed_holdings", {}) if trade_files else {}
held_codes = list(confirmed.keys())
entry_map = {c: float(v["avg_price"]) for c, v in confirmed.items()}
share_map = {c: int(v["shares"]) for c, v in confirmed.items()}

holdings_input_rows = [
    {
        "コード": c,
        "銘柄名": STOCK_NAMES.get(c, c),
        "株数": int(v["shares"]),
        "取得単価": float(v["avg_price"]),
        "預り区分": v.get("account_types", ""),
        "データ元": v.get("source", "SBI約定履歴CSV自動復元"),
        "保有AI使用可": "YES",
    }
    for c, v in confirmed.items()
]
holdings_input_df = pd.DataFrame(holdings_input_rows)

if held_codes:
    st.success(f"🤖 保有AI入力：{len(held_codes)}銘柄（CSV自動復元）")
else:
    st.warning("🤖 保有AI入力：0銘柄。SBI約定履歴CSVをアップロードしてください。")

# ------------------------------------------------------------
# SBI買付余力
# ------------------------------------------------------------
st.header("💴 ② SBI買付余力 / 購入可能資金")
st.caption(
    "SBI『口座管理 → 口座（円建）→ 買付余力』の買付余力を使用します。"
    "CSV/TXT/HTML/WebArchiveとして保存できた場合は自動読取できます。"
    "自動読取できない場合も、金額1つだけ入力すれば購入株数を自動計算します。"
)

bp_file = st.file_uploader(
    "SBI買付余力情報ファイル（任意・スクショ不可）",
    type=["csv", "txt", "html", "htm", "webarchive"],
    key="sbi_buying_power_file"
)

detected_buying_power = None
buying_power_source = "手入力"
bp_parse_error = ""
if bp_file is not None:
    try:
        detected_buying_power, _bp_text = extract_buying_power_from_file(bp_file)
        st.session_state["sbi_buying_power_yen"] = int(detected_buying_power)
        buying_power_source = f"SBI余力ファイル自動読取: {bp_file.name}"
        st.success(f"✅ 買付余力を自動取得：¥{int(detected_buying_power):,}")
    except Exception as e:
        bp_parse_error = str(e)
        st.warning(f"買付余力ファイルを自動読取できませんでした：{e}")

if "sbi_buying_power_yen" not in st.session_state:
    st.session_state["sbi_buying_power_yen"] = 0
buying_power = st.number_input(
    "SBI 現物買付余力（円）",
    min_value=0,
    max_value=1_000_000_000,
    step=1000,
    key="sbi_buying_power_yen",
    help="SBI画面の買付余力を入力。保有銘柄のような複数項目の手入力は不要で、この1項目だけです。"
)
if detected_buying_power is None:
    buying_power_source = "手入力"

bp_c1, bp_c2, bp_c3 = st.columns(3)
bp_c1.metric("買付余力", f"¥{int(buying_power):,}")
bp_c2.metric("現金温存", f"{reserve_pct}%")
bp_c3.metric("1日使用上限", f"{daily_deploy_pct}%")

# ------------------------------------------------------------
# データ取得
# ------------------------------------------------------------
analysis_codes = list(dict.fromkeys(parse_codes(universe_text) + held_codes))
with st.spinner("📡 株価・市場・海外データを取得中…"):
    analysis_tickers = tickers(",".join(analysis_codes))
    tv_quotes, tv_endpoint_diagnostics = tradingview_batch_quotes(tuple(analysis_tickers + ["1321.T"]))
    TRADINGVIEW_QUOTES_CACHE.clear()
    TRADINGVIEW_QUOTES_CACHE.update(tv_quotes)
    data = {t: stock_data(t) for t in analysis_tickers}
    data = {t:d for t,d in data.items() if not d.empty}
    individual_dates = [
        pd.Timestamp(d.attrs.get("regular_market_date")).normalize()
        for d in data.values() if pd.notna(d.attrs.get("regular_market_date", pd.NaT))
    ]
    expected_market_date = max(individual_dates) if individual_dates else pd.NaT
    market = market_data(expected_market_date)
    overseas = overseas_data()

diagnostic_rows = list(tv_endpoint_diagnostics)
for ticker in analysis_tickers:
    frame = data.get(ticker, pd.DataFrame())
    latest = pd.NaT if frame.empty else pd.Timestamp(frame.index.max()).normalize()
    required = pd.NaT if frame.empty else frame.attrs.get("regular_market_date", pd.NaT)
    required = pd.Timestamp(required).normalize() if pd.notna(required) else pd.NaT
    source = "取得失敗" if frame.empty else frame.attrs.get("source", "不明")
    diagnostic_rows.append({
        "段階":"銘柄別採用結果", "対象":f"{code(ticker)} {name(ticker)}", "取得先":source,
        "HTTP状態":"-", "結果":("最新" if pd.notna(latest) and pd.notna(required) and latest >= required else "未更新"),
        "詳細":f"最終日={latest.date() if pd.notna(latest) else 'なし'} / 必要日={required.date() if pd.notna(required) else '不明'} / TV一括={'あり' if ticker in tv_quotes else 'なし'}",
    })
data_source_diagnostics_df = pd.DataFrame(diagnostic_rows)

freshness_df, market_data_stale, stale_tickers = build_freshness_report(data, market)
market_latest_date = pd.NaT if market.empty else pd.Timestamp(market.index.max()).normalize()
market_required_date = pd.to_datetime(
    freshness_df.loc[freshness_df["種別"] == "市場", "基準日"], errors="coerce"
).max() if not freshness_df.empty else pd.NaT
market_mode = market.attrs.get("market_mode", "NIKKEI225_DIRECT") if not market.empty else "NO_DATA"
market_is_proxy = market_mode == "1321_ETF_PROXY"
market_source = market.attrs.get("source", "取得失敗") if not market.empty else "取得失敗"
stale_held_tickers = {c + ".T" for c in held_codes} & stale_tickers

st.success(f"株価データ取得：{len(data)}銘柄")
if market_data_stale:
    st.error(
        "🔴 DATA STALE：日経平均の最新確定データを確認できません。"
        "安全のため、本日の新規BUYと保有銘柄のSELL/HOLD判定を停止します。"
    )
elif stale_held_tickers:
    st.error(
        "🔴 DATA STALE：一部の保有銘柄が日経平均の最終日まで更新されていません。"
        "該当銘柄のSELL/HOLD判定を停止します。"
    )
else:
    latest_label = market_latest_date.date() if pd.notna(market_latest_date) else "不明"
    if market_is_proxy:
        st.warning(
            f"🟡 データ鮮度OK（代理）：日経平均の直接日足が古いため、"
            f"1321.Tの騰落率で市場判定を補完しました。最終日 {latest_label}"
        )
    else:
        st.success(f"🟢 データ鮮度OK：日経平均・保有銘柄の最終日 {latest_label}")

with st.expander("🔎 株価データの取得元・最終日"):
    st.dataframe(freshness_df, use_container_width=True, hide_index=True)
with st.expander("🧪 無料データ取得診断"):
    st.dataframe(data_source_diagnostics_df, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# 目標資産 / 複利ロードマップ
# ------------------------------------------------------------
st.header("🎯 ③ 60万円 → 1億円 複利ロードマップ")
if current_assets > 0 and target_assets > current_assets:
    months_10 = math.log(target_assets / current_assets) / math.log(1.10)
    st.write(f"月利10%を毎月完全に複利で達成した場合の理論値：**約{months_10:.1f}か月**")
    st.warning("これは数学上の試算であり、月利10%を保証するものではありません。実運用ではDD・損失・相場環境を必ず考慮します。")
else:
    st.info("現在資産が目標以上、または入力値を確認してください。")

milestones = [1_000_000, 2_000_000, 5_000_000, 10_000_000, 30_000_000, 50_000_000, 100_000_000]
road = []
for m in milestones:
    if m <= current_assets:
        status = "達成"
    else:
        status = f"{m/current_assets:.1f}倍"
    road.append({"目標資産":m, "現在資産から":status})
st.dataframe(pd.DataFrame(road), use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# 流動性
# ------------------------------------------------------------
liq = pd.DataFrame([
    {"コード":code(t),"銘柄名":name(t),"平均売買代金":d.Turnover.mean(),
     "平均出来高":d.Volume.mean()} for t,d in data.items()
])
if not liq.empty:
    liq = liq.sort_values("平均売買代金", ascending=False).reset_index(drop=True)
    liq["売買代金順位"] = liq.index + 1
    liq["売買代金TOP50"] = liq["売買代金順位"] <= 50
liq_codes = set(liq.loc[liq["売買代金TOP50"],"コード"]) if not liq.empty else set()

# ------------------------------------------------------------
# 5年バックテスト：未来情報を避けるためファンダメンタルは使用しない
# ------------------------------------------------------------
st.header("🧪 ③ 現行ロジック・バックテスト")
cash = float(initial)
pos = {}
stats = {t:{"trades":0,"wins":0,"gp":0.0,"gl":0.0,"recent_losses":0} for t in data}
trades, analyses, equity = [], [], []
losses = 0
maxloss = 0
block_until = None
severe_block_until = None
pending_buys = {}
pending_tickers = set()

dates = sorted(set(x for d in data.values() for x in d.index))

for dt in dates:
    # BUY予約を翌営業日寄付で約定
    for order in pending_buys.pop(dt, []):
        t = order["ticker"]
        pending_tickers.discard(t)
        if t not in data or dt not in data[t].index or t in pos:
            continue
        r = data[t].loc[dt]
        p = float(r.Open)
        signal_close = order["signal_close"]
        gap = (p/signal_close - 1) * 100 if signal_close > 0 else 999
        if p <= 0 or abs(gap) > max_gap:
            continue
        if is_blocked(dt, block_until, severe_block_until) or len(pos) >= maxpos:
            continue
        if order["market_factor"] <= 0:
            continue
        rf = risk_factor_from_losses(losses)
        budget = min(maxbuy, cash) * order["score_factor"] * rf
        shares = int(budget / p)
        if shares <= 0:
            continue
        cost = shares * p
        if cost > cash:
            continue
        cash -= cost
        pos[t] = {"entry":p,"shares":shares}
        trades.append({
            "日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"BUY",
            "価格":p,"株数":shares,"損益":0,"損益率":0,
            "理由":"Ver.6.0基盤AI BUY（翌営業日寄付）",
            "シグナル日":order["signal_date"],"テクニカルスコア":order["ts"],
            "総合AIスコア":order["score"],"市場判定":order["market_state"],
            "海外為替判定":order["overseas_state"],"寄付ギャップ率":gap,
            "未来情報使用":False
        })

    # 保有ポジション評価/SELL
    for t in list(pos):
        if dt not in data[t].index:
            continue
        r = data[t].loc[dt]
        p = float(r.Close)
        q = pos[t]
        pnl = (p-q["entry"]) * q["shares"]
        pct = (p/q["entry"] - 1) * 100
        ma25_confirm = p < r.MA25 and (r.MA25_Slope < 0 or tech(r,rlo,rhi) < 60)
        reason = ("損切り" if pct <= -sl else
                  "利確" if pct >= tp else
                  "25日線割れ確認" if ma25_confirm else None)
        if reason:
            cash += p * q["shares"]
            s = stats[t]; s["trades"] += 1
            if pnl > 0:
                s["wins"] += 1; s["gp"] += pnl; s["recent_losses"] = 0; losses = 0
            else:
                s["gl"] += abs(pnl); s["recent_losses"] += 1
                losses += 1; maxloss = max(maxloss, losses)
                if losses >= 10:
                    severe_block_until = dt + pd.tseries.offsets.BDay(severe_cooldown)
                elif losses >= 9:
                    block_until = dt + pd.tseries.offsets.BDay(risk_cooldown)
                elif losses >= 4:
                    block_until = dt + pd.tseries.offsets.BDay(cooldown)
            trades.append({
                "日付":dt,"コード":code(t),"銘柄名":name(t),"売買":"SELL",
                "価格":p,"株数":q["shares"],"損益":pnl,"損益率":pct,
                "理由":reason,"未来情報使用":False,"連敗数":losses
            })
            del pos[t]

    # BUYシグナル
    candidates = []
    for t,d in data.items():
        if dt not in d.index or t in pos:
            continue
        r = d.loc[dt]; p = float(r.Close); c = code(t)
        # 既存版の永続ルールを維持
        if p >= 2000:
            continue
        if use_liq and c not in liq_codes and c not in held_codes and c != "6085":
            continue
        ts = tech(r,rlo,rhi)
        if ts < mintech:
            continue
        hc = confidence(stats[t]) * recent_loss_penalty(stats[t])
        hp = conf_points(hc)
        ms, mp, mf = market_info(market, dt)
        os = overseas_snapshot(overseas, dt)
        qfactor, qblock, qreason, wr, pf, avg = stock_quality(stats[t])

        base = ts*.55 + hp*.30 + mp*.15
        raw = float(np.clip(base*qfactor,0,100))
        score = raw * os["海外為替係数"]
        threshold = 86 if ms in ["⚪ 中立","🟠 やや弱気","🔴 弱気"] else 82 if ms == "🟡 やや強気" else 80
        blocked = is_blocked(dt, block_until, severe_block_until)
        reject = qblock or score < minbuy_score or score < threshold or os["海外為替係数"] < .50
        if no_trade_on_bad_market and mf <= 0:
            reject = True

        analyses.append({
            "日付":dt,"コード":c,"銘柄名":name(t),"株価":p,
            "テクニカルスコア":ts,"総合AIスコア":score,
            "市場判定":ms,"市場ポイント":mp,
            "海外為替判定":os["海外為替判定"],"海外為替係数":os["海外為替係数"],
            "銘柄期待値係数":qfactor,"過去勝率":wr*100,"過去PF":pf,
            "過去平均損益":avg,"新規BUY停止":blocked,
            "未来情報使用":False
        })
        if not blocked and not reject:
            candidates.append((score,t,ts,hc,ms,mp,os, qfactor,wr,pf,avg))

    candidates.sort(reverse=True)
    for score,t,ts,hc,ms,mp,os,qfactor,wr,pf,avg in candidates:
        if t in pending_tickers:
            continue
        nxt = next_trade_date(data[t].index, dt)
        if nxt is None:
            continue
        pending_buys.setdefault(nxt,[]).append({
            "ticker":t,"score":score,"ts":ts,"market_state":ms,"market_factor":mp,
            "overseas_state":os["海外為替判定"],"signal_date":dt,
            "signal_close":float(data[t].loc[dt].Close),
            "score_factor":1.0 if score >= 85 else .85 if score >= 75 else .70
        })
        pending_tickers.add(t)

    hv = sum(float(data[t].loc[dt].Close)*q["shares"] for t,q in pos.items()
             if dt in data[t].index)
    equity.append({
        "日付":dt,"現金":cash,"保有株評価額":hv,"総資産":cash+hv,
        "保有銘柄数":len(pos),"連敗数":losses,
        "新規BUY停止中":is_blocked(dt,block_until,severe_block_until)
    })

trades_df = pd.DataFrame(trades)
analysis_df = pd.DataFrame(analyses)
equity_df = pd.DataFrame(equity)

if not equity_df.empty:
    equity_df["最高資産"] = equity_df["総資産"].cummax()
    equity_df["DD"] = equity_df["総資産"] - equity_df["最高資産"]
    equity_df["DD率"] = np.where(equity_df["最高資産"] != 0,
                                  equity_df["DD"]/equity_df["最高資産"]*100, 0)
    final = float(equity_df["総資産"].iloc[-1])
    maxdd = float(equity_df["DD"].min())
    maxddrate = float(equity_df["DD率"].min())
else:
    final, maxdd, maxddrate = initial, 0.0, 0.0

selltr = trades_df[trades_df["売買"]=="SELL"] if not trades_df.empty else pd.DataFrame()
winrate = (selltr["損益"] > 0).mean()*100 if not selltr.empty else 0
gp = selltr.loc[selltr["損益"]>0,"損益"].sum() if not selltr.empty else 0
gl = abs(selltr.loc[selltr["損益"]<0,"損益"].sum()) if not selltr.empty else 0
pf = gp/gl if gl else 0

# ------------------------------------------------------------
# 現在の新規BUY + 現在ファンダメンタル
# ------------------------------------------------------------
latest_rows = []
for t,d in data.items():
    r = d.iloc[-1]; p = float(r.Close); c = code(t)
    # 日経平均より古い個別株データは、現在のBUY候補に使用しない。
    if market_data_stale or t in stale_tickers:
        continue
    if p >= 2000:
        continue
    if use_liq and c not in liq_codes and c not in held_codes and c != "6085":
        continue
    ts = tech(r,rlo,rhi)
    if ts < mintech:
        continue
    hc = confidence(stats[t]) * recent_loss_penalty(stats[t])
    hp = conf_points(hc)
    ms,mp,mf = market_info(market, d.index[-1])
    os = overseas_snapshot(overseas, d.index[-1])
    qfactor,qblock,qreason,wr,pf_hist,avg_hist = stock_quality(stats[t])
    tech_score = ts
    current_base = tech_score*.40 + hp*.20 + mp*.10 + os["海外為替係数"]*100*.10
    f = fundamental_snapshot(t)
    value = safe_float(f.get("企業価値スコア"),50)
    growth = safe_float(f.get("成長性スコア"),50)
    ten = tenbagger_score(f)
    # 現在の総合スコア：ファンダメンタルを中心軸にする
    total = np.clip(
        value*.30 + growth*.20 + tech_score*.25 + hp*.10 + mp*.05 +
        os["海外為替係数"]*100*.05 + ten*.05, 0, 100
    )
    latest_rows.append({
        "コード":c,"銘柄名":name(t),"現在株価":p,
        "データ最終日":pd.Timestamp(d.index[-1]).normalize(),
        "データ元":d.attrs.get("source", "不明"),"データ鮮度":"🟢 OK",
        "総合AIスコア":float(total),"企業価値スコア":value,
        "成長性スコア":growth,"テンバガー度":ten,
        "テクニカルスコア":ts,"AI信頼度":hp,
        "市場判定":ms,"海外為替判定":os["海外為替判定"],
        "現在PER":f.get("PER",np.nan),"予想PER":f.get("予想PER",np.nan),
        "PBR":f.get("PBR",np.nan),"ROE":f.get("ROE",np.nan),
        "売上成長率":f.get("売上成長率",np.nan),
        "利益成長率":f.get("利益成長率",np.nan),
        "時価総額":f.get("時価総額",np.nan),
        "AI参考価値下限":f.get("AI参考価値下限",np.nan),
        "AI参考価値":f.get("AI参考価値",np.nan),
        "AI参考価値上限":f.get("AI参考価値上限",np.nan),
        "参考価値上昇余地":f.get("参考価値上昇余地",np.nan),
        "価値算定根拠":f.get("価値算定根拠",""),
        "実保有銘柄":c in held_codes,
    })

latest_columns = [
    "コード","銘柄名","現在株価","データ最終日","データ元","データ鮮度",
    "総合AIスコア","企業価値スコア","成長性スコア","テンバガー度",
    "テクニカルスコア","AI信頼度","市場判定","海外為替判定",
    "現在PER","予想PER","PBR","ROE","売上成長率","利益成長率","時価総額",
    "AI参考価値下限","AI参考価値","AI参考価値上限","参考価値上昇余地",
    "価値算定根拠","実保有銘柄",
]
latest_df = pd.DataFrame(latest_rows, columns=latest_columns)
if not latest_df.empty:
    latest_df = latest_df.sort_values("総合AIスコア",ascending=False).reset_index(drop=True)

# 「今日のBUY」は最低AIスコアを通過した銘柄だけ。候補一覧とは分離する。
today_buy_df = (
    latest_df[latest_df["総合AIスコア"] >= minbuy_score].copy()
    if not latest_df.empty and not market_data_stale else latest_df.iloc[0:0].copy()
)
if not today_buy_df.empty:
    today_buy_df = today_buy_df.sort_values("総合AIスコア", ascending=False).reset_index(drop=True)

# 現在市場のNO TRADE判定（購入株数計算にも使用）
_latest_dt_for_market = max(data[next(iter(data))].index) if data else datetime.now()
_current_market_state = market_info(market, _latest_dt_for_market)[0] if not market.empty and data else "⚪ データなし"
_market_block = bool(
    market_data_stale or
    (no_trade_on_bad_market and ("🔴" in _current_market_state or "🟠" in _current_market_state))
)

purchase_plan_df = build_purchase_plan(
    today_buy_df, buying_power, current_assets, held_codes, maxpos, maxbuy, sl,
    reserve_pct, daily_deploy_pct, risk_per_trade_pct, price_buffer_pct,
    allow_addon=allow_addon, market_block=_market_block
)

buying_power_df = pd.DataFrame([{
    "買付余力": float(buying_power),
    "データ元": buying_power_source,
    "現金温存率": float(reserve_pct),
    "1日使用上限率": float(daily_deploy_pct),
    "1銘柄許容損失率_総資産比": float(risk_per_trade_pct),
    "寄付価格上振れバッファ": float(price_buffer_pct),
    "最大保有銘柄数": int(maxpos),
    "現在保有銘柄数": len(held_codes),
    "買い増し許可": bool(allow_addon),
    "市場NO_TRADE": bool(_market_block),
    "市場判定": _current_market_state,
}])

# ------------------------------------------------------------
# 保有銘柄AI
# ------------------------------------------------------------
holding_rows = []
for c in held_codes:
    t = c + ".T"
    if market_data_stale or t in stale_tickers:
        d = data.get(t, pd.DataFrame())
        latest = pd.NaT if d.empty else pd.Timestamp(d.index.max()).normalize()
        holding_rows.append({
            "コード":c,"銘柄名":name(t),"株数":share_map.get(c, np.nan),
            "取得単価":entry_map.get(c, np.nan),
            "保有情報データ元":confirmed.get(c, {}).get("source", "SBI約定履歴CSV自動復元"),
            "現在価格":np.nan,"含み損益率":np.nan,"企業価値スコア":np.nan,
            "成長性スコア":np.nan,"テンバガー度":np.nan,"AI参考価値":np.nan,
            "参考価値上昇余地":np.nan,"テクニカルスコア":np.nan,
            "判定":"🔴 DATA STALE","売却期限目安":"判定停止・データ更新後に再実行",
            "警戒理由":f"株価データ最終日 {latest.date() if pd.notna(latest) else '取得不可'} / "
                       f"必要データ日 {market_required_date.date() if pd.notna(market_required_date) else '取得不可'}",
            "補足":"古いデータではSELL/HOLDを出しません",
        })
        continue
    if t not in data:
        holding_rows.append({"コード":c,"銘柄名":name(t),"判定":"データ不足","警戒理由":"株価データ取得不可"})
        continue
    d = data[t]; r = d.iloc[-1]; p = float(r.Close)
    ep = entry_map.get(c, np.nan)
    sh = share_map.get(c, np.nan)
    pct = (p/ep-1)*100 if np.isfinite(ep) and ep else np.nan
    alerts = []
    reasons = []

    if np.isfinite(pct) and pct <= -sl:
        alerts.append("損切りライン到達")
    if p < r.MA25 and (r.MA25_Slope < 0 or tech(r,rlo,rhi) < 60):
        alerts.append("25日線割れ確認")
    if r.MA25 < r.MA75:
        alerts.append("25日線<75日線")
    if r.MA25_Slope < 0:
        alerts.append("25日線下降")

    f = fundamental_snapshot(t)
    value = safe_float(f.get("企業価値スコア"),50)
    growth = safe_float(f.get("成長性スコア"),50)
    ten = tenbagger_score(f)
    fair = safe_float(f.get("AI参考価値"),np.nan)
    upside = safe_float(f.get("参考価値上昇余地"),np.nan)

    if value < 40:
        alerts.append("企業価値スコア低下")
    if growth < 40:
        alerts.append("成長性低下")
    if np.isfinite(fair) and p > fair*1.20:
        alerts.append("参考価値に対して割高")
    if np.isfinite(fair) and p < fair*.75:
        reasons.append("参考価値に対して割安")

    if len(alerts) >= 3 or ("損切りライン到達" in alerts):
        decision = "🔴 SELL"
        deadline = "原則：次の1～3営業日以内"
    elif len(alerts) >= 1:
        decision = "🟡 WATCH"
        deadline = "目安：数営業日～1週間で再判定"
    else:
        decision = "🟢 HOLD"
        deadline = "継続保有・決算/企業価値を監視"

    holding_rows.append({
        "コード":c,"銘柄名":name(t),"株数":sh,"取得単価":ep,
        "保有情報データ元": confirmed.get(c, {}).get("source", "SBI約定履歴CSV自動復元"),
        "現在価格":p,"含み損益率":pct,"企業価値スコア":value,
        "成長性スコア":growth,"テンバガー度":ten,
        "AI参考価値":fair,"参考価値上昇余地":upside,
        "テクニカルスコア":tech(r,rlo,rhi),
        "判定":decision,"売却期限目安":deadline,
        "警戒理由":" / ".join(alerts) if alerts else "重大警戒なし",
        "補足":" / ".join(reasons)
    })

holdings_df = pd.DataFrame(holding_rows)

# ------------------------------------------------------------
# UI：保有銘柄
# ------------------------------------------------------------
st.header("📦 ④ 現在保有銘柄AI診断")
if holdings_df.empty:
    st.info("保有銘柄はまだ登録されていません。SBI約定履歴CSVをアップロードしてください。")
else:
    sell_now = holdings_df[holdings_df["判定"]=="🔴 SELL"]
    watch = holdings_df[holdings_df["判定"]=="🟡 WATCH"]
    hold = holdings_df[holdings_df["判定"]=="🟢 HOLD"]
    stale_holdings = holdings_df[holdings_df["判定"]=="🔴 DATA STALE"]

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("🔴 SELL",len(sell_now))
    c2.metric("🟡 WATCH",len(watch))
    c3.metric("🟢 HOLD",len(hold))
    c4.metric("DATA STALE",len(stale_holdings))

    if not stale_holdings.empty:
        st.error("🔴 古い株価データの銘柄は売買判定を停止しています。")
        st.dataframe(stale_holdings, use_container_width=True, hide_index=True)

    if not sell_now.empty:
        st.subheader("🔴 早期売却候補")
        st.dataframe(sell_now, use_container_width=True, hide_index=True)
    if not watch.empty:
        st.subheader("🟡 注意・再判定")
        st.dataframe(watch, use_container_width=True, hide_index=True)
    if not hold.empty:
        st.subheader("🟢 保有継続")
        st.dataframe(hold, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# UI：新規BUY / 購入株数 / テンバガー
# ------------------------------------------------------------
st.header("🟢 ⑤ 今日の正式BUY / 購入株数 TOP3")
if market_data_stale:
    st.error("🔴 DATA STALEのため、本日の新規BUY判定を停止しています。データ更新後に再実行してください。")
elif today_buy_df.empty:
    st.info(f"💤 BUY基準（AI {minbuy_score}点以上）を満たす銘柄はありません。今日はNO TRADEです。")
elif purchase_plan_df.empty:
    st.warning("正式BUY候補はありますが、資金管理条件により購入株数を出せません。最大保有銘柄数などを確認してください。")
else:
    executable = purchase_plan_df[(purchase_plan_df["買付可否"] == "🟢 BUY") & (purchase_plan_df["購入株数"] > 0)]
    if executable.empty:
        st.warning("正式BUY候補はありますが、本日の買付余力・市場・保有上限では購入指示は0株です。")
    for _, rr in purchase_plan_df.iterrows():
        if rr["買付可否"] == "🟢 BUY":
            st.success(
                f"**{int(rr['購入優先度'])}位 {rr['銘柄名']}（{rr['コード']}）**｜"
                f"AI {rr['総合AIスコア']:.1f}｜**{int(rr['購入株数'])}株**｜"
                f"概算 ¥{rr['予定購入額']:,.0f}｜購入後余力 約¥{rr['購入後推定余力']:,.0f}"
            )
        else:
            st.info(
                f"{int(rr['購入優先度'])}位 {rr['銘柄名']}（{rr['コード']}）｜0株｜{rr['見送り理由']}"
            )
    st.dataframe(purchase_plan_df, use_container_width=True, hide_index=True)
    st.caption(
        "購入株数はS株1株単位。現在株価に上振れバッファを加えて余力を引き当てます。"
        "実際の約定価格は寄付等で変動するため、表示金額は概算です。"
    )

st.header("🔥 ⑥ テンバガーAI候補")
if latest_df.empty:
    st.info("候補なし")
else:
    ten_df = latest_df.sort_values("テンバガー度",ascending=False).head(10)
    st.dataframe(
        ten_df[["コード","銘柄名","テンバガー度","企業価値スコア","成長性スコア",
                "時価総額","予想PER","売上成長率","利益成長率","現在株価"]],
        use_container_width=True, hide_index=True
    )
    st.caption("テンバガー度は『将来10倍を保証する確率』ではなく、成長余地・規模・成長率・収益性等をまとめた探索スコアです。")

# ------------------------------------------------------------
# UI：市場とNO TRADE
# ------------------------------------------------------------
latest_market_state = _current_market_state
st.header("🌎 ⑦ 市場環境 / NO TRADE")
if market_data_stale:
    st.error("🔴 DATA STALE → 市場判定と新規BUYを停止")
else:
    if market_is_proxy:
        proxy_pct = safe_float(market.attrs.get("proxy_return_pct"), np.nan)
        proxy_label = f"{proxy_pct:+.2f}%" if np.isfinite(proxy_pct) else "取得不可"
        st.warning(
            "市場判定データ：1321.T（日経225連動ETF）の騰落率代理 "
            f"{proxy_label}。日経平均の正確な終値としては使用しません。"
        )
    if "🔴" in latest_market_state or "🟠" in latest_market_state:
        st.warning(f"市場環境：{latest_market_state} → 無理な新規BUYを抑制")
    elif latest_market_state == "⚪ 中立":
        st.info("市場環境：中立 → 銘柄選別を厳格化")
    else:
        st.success(f"市場環境：{latest_market_state}")

# ------------------------------------------------------------
# UI：バックテスト成績
# ------------------------------------------------------------
st.header("📊 ⑧ バックテスト結果")
summary = pd.DataFrame({
    "項目":[
        "Ver","初期資金","最終資産","損益","損益率","決済トレード数",
        "勝率","Profit Factor","最大DD","最大DD率","最大連続損失",
        "ファンダメンタルAI","テンバガーAI","保有銘柄AI","SBI約定履歴CSV",
        "SBI買付余力","購入株数自動計算","未来情報混入","SBI自動発注"
    ],
    "結果":[
        VERSION,initial,final,final-initial,(final/initial-1)*100 if initial else 0,
        len(selltr),winrate,pf,maxdd,maxddrate,maxloss,
        "現在情報のみ・バックテスト未使用","あり","あり","あり",
        f"¥{int(buying_power):,} ({buying_power_source})","あり","なし","なし"
    ]
})
summary = pd.concat([summary, pd.DataFrame([
    {"項目":"市場判定データ最終日", "結果":market_latest_date.date() if pd.notna(market_latest_date) else "取得失敗"},
    {"項目":"市場判定方式", "結果":"1321.T騰落率代理" if market_is_proxy else "日経平均直接"},
    {"項目":"市場判定データ元", "結果":market_source},
    {"項目":"データ鮮度安全装置", "結果":"🔴 DATA STALE・売買判定停止" if market_data_stale else "🟢 OK"},
])], ignore_index=True)
st.dataframe(summary, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# AI信頼度 / データ品質
# ------------------------------------------------------------
st.header("🛡️ ⑨ データ品質・AI安全チェック")
quality = pd.DataFrame([
    {"チェック":"市場データ鮮度","状態":"🔴 DATA STALE" if market_data_stale else "🟢 OK",
     "内容":f"市場判定最終日: {market_latest_date.date() if pd.notna(market_latest_date) else '取得失敗'} / 古い場合はBUY・SELL・HOLDを停止"},
    {"チェック":"市場判定方式","状態":"🟡 1321.T代理" if market_is_proxy else "🟢 日経平均直接",
     "内容":("1321.Tの当日騰落率を市場の移動平均・傾向判定だけに使用。日経平均の正確な終値ではありません"
             if market_is_proxy else "日経平均の直接取得値を使用")},
    {"チェック":"個別株データ鮮度","状態":"🔴 要停止" if stale_held_tickers else "🟢 OK",
     "内容":f"市場最終日より古い保有銘柄: {len(stale_held_tickers)}件 / 該当銘柄は売買判定停止"},
    {"チェック":"未来情報","状態":"🟢 OK","内容":"バックテストのBUY/SELL判定は日付時点の価格データのみ"},
    {"チェック":"現在ファンダメンタル","状態":"🟡 現在分析のみ","内容":"Yahoo Financeの現在情報。過去バックテストには混入させない"},
    {"チェック":"SBI約定履歴CSV","状態":"🟢 自動復元","内容":"約定履歴の買付・売却を時系列処理し、現在株数と平均取得単価を自動復元。スクショ/OCRは完全除外"},
    {"チェック":"買付余力","状態":"🟢 連動" if buying_power > 0 else "🟡 未入力","内容":f"{buying_power_source} / 購入株数計算に使用"},
    {"チェック":"購入株数","状態":"🟢 資金管理","内容":"買付余力・現金温存率・1日上限・損切り幅・1銘柄リスク・価格上振れバッファからS株数を算出"},
    {"チェック":"SBI自動発注","状態":"🟢 OFF","内容":"注文は行わない"},
    {"チェック":"NO TRADE","状態":"🟢 ON","内容":"市場悪化・条件不足時は無理なBUYを抑制"},
])
st.dataframe(quality, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# CSV/ZIP
# ------------------------------------------------------------
stock_results = (
    selltr.groupby(["コード","銘柄名"]).agg(
        トレード数=("損益","count"),
        勝ち=("損益",lambda x:(x>0).sum()),
        損益=("損益","sum"),
        平均損益=("損益","mean")
    ).reset_index()
    if not selltr.empty else pd.DataFrame()
)

if not market.empty:
    market_export = market.reset_index()
    market_export["市場判定方式"] = "1321.T騰落率代理" if market_is_proxy else "日経平均直接"
    market_export["データ元"] = market_source
    market_export["代理換算値"] = bool(market_is_proxy)
else:
    market_export = pd.DataFrame()

market_proxy_detail = pd.DataFrame([{
    "市場判定方式": "1321.T騰落率代理" if market_is_proxy else "日経平均直接",
    "データ元": market_source,
    "基準日": market.attrs.get("proxy_base_date", pd.NaT) if not market.empty else pd.NaT,
    "基準日日経平均終値": market.attrs.get("proxy_base_index_close", np.nan) if not market.empty else np.nan,
    "基準日1321.T終値": market.attrs.get("proxy_base_close", np.nan) if not market.empty else np.nan,
    "必要日1321.T終値": market.attrs.get("proxy_required_close", np.nan) if not market.empty else np.nan,
    "1321.T当日騰落率": market.attrs.get("proxy_return_pct", np.nan) if not market.empty else np.nan,
    "市場判定用換算値": market.attrs.get("converted_market_close", np.nan) if not market.empty else np.nan,
    "注意": ("市場環境判定専用の代理値。日経平均の正確な終値ではない"
             if market_is_proxy else "代理値は使用していない"),
}])

files = {
    "00_summary.csv":summary,
    "00b_buying_power.csv":buying_power_df,
    "01_today_buy.csv":today_buy_df,
    "01a_purchase_plan.csv":purchase_plan_df,
    "01b_current_candidates.csv":latest_df,
    "02_holdings_ai.csv":holdings_df,
    "02a_holdings_input.csv":holdings_input_df,
    "02b_sbi_trade_history.csv":sbi_trades_df.drop(columns=["_dedupe_key","_source_order"], errors="ignore"),
    "02c_sbi_rebuild_audit.csv":sbi_audit_df,
    "02d_sbi_account_lots.csv":sbi_lots_df,
    "02e_sbi_history_warnings.csv":sbi_warning_df,
    "03_tenbagger_candidates.csv":latest_df.sort_values("テンバガー度",ascending=False) if not latest_df.empty else latest_df,
    "04_all_ai_analysis.csv":analysis_df,
    "05_trade_history.csv":trades_df,
    "06_equity_curve.csv":equity_df,
    "07_stock_results.csv":stock_results,
    "08_liquidity_top50.csv":liq,
    "09_quality_check.csv":quality,
    "09a_data_freshness.csv":freshness_df,
    "09b_market_proxy_detail.csv":market_proxy_detail,
    "09c_data_source_diagnostics.csv":data_source_diagnostics_df,
    "10_market_data.csv":market_export,
    "11_overseas_data.csv":overseas.reset_index() if not overseas.empty else pd.DataFrame(),
}

buf = io.BytesIO()
with ZipFile(buf,"w") as z:
    for fn,df in files.items():
        z.writestr(fn,csv_bytes(df))
buf.seek(0)

st.header("📦 ⑩ 全処理データ")
st.download_button(
    "📦 Ver.6.0 全処理データをZIPでダウンロード",
    buf.getvalue(),
    "ver6_0_RC6_7_all_analysis.zip",
    "application/zip",
    use_container_width=True
)

st.caption(
    "※本版は投資判断補助・検証用です。月利10%・1億円到達・テンバガー化・"
    "AI適正株価・購入株数による利益を保証するものではありません。SBIへの自動発注は行いません。"
)
