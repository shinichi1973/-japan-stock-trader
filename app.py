# ============================================================
# 日本株 AI投資アシスタント Ver.6.0
# BUILD: VER6.0-RC6.12-FAST-2STAGE-1OKU-20260908
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
    page_title="日本株 AI投資アシスタント Ver.17.7",
    page_icon="📈",
    layout="wide",
)

VERSION = "17.7 TRUE VALUE AI TOP50"
BUILD = "VER17-7-TRUE-VALUE-AI-TOP50-SIMPLE-20260913"

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
def fundamental_snapshot(t, market_price_hint=np.nan):
    """現在情報のみ。過去バックテストには使用しない。株式分割ズレを最優先で防止する。"""
    try:
        tk = yf.Ticker(t)
        info = tk.info or {}
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

        return {
            "取得状態":"OK","現在株価":price,"時価総額":market_cap,
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


def build_value_ai_top50(data, max_rows=50, fundamental_pool_size=90):
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
        tech_rows.append({
            "ticker": t, "コード": code(t), "銘柄名": name(t), "現在株価_価格": close,
            "20日平均売買代金": turnover20, "20日平均出来高": volume20,
            "25日騰落率%": ret25, "ATR%": atr_pct, "トレンド原点": trend_raw,
        })
    if not tech_rows:
        return pd.DataFrame()

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
    base["一次選抜スコア"] = (
        base["流動性スコア"] * .62 +
        base["トレンドスコア"] * .23 +
        base["値動き安定スコア"] * .15
    ).clip(0, 100)
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
        ("⚠️ 異常値ガード・算定不能" if "⚠️" in str(rr.get("適正株価異常値ガード", "")) and not np.isfinite(safe_float(rr.get("参考価値上昇余地%"))) else
         "算定不能" if not np.isfinite(safe_float(rr.get("参考価値上昇余地%"))) else
         "🟢 割安" if safe_float(rr.get("参考価値上昇余地%")) >= 20 else
         "🟡 やや割安" if safe_float(rr.get("参考価値上昇余地%")) >= 5 else
         "⚪ 適正圏" if safe_float(rr.get("参考価値上昇余地%")) > -10 else "🔴 割高"),
        axis=1
    )
    out["母集団件数"] = int(mother_count)
    out["詳細企業価値評価件数"] = int(pool_n)
    out = out.sort_values(["AI_TOP50スコア", "流動性スコア"], ascending=[False, False]).reset_index(drop=True)
    out.insert(0, "順位", np.arange(1, len(out) + 1))
    return out.head(int(max_rows)).copy()


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
def batch_stock_data_light(tickers_tuple, months=15):
    """数百銘柄の日足をyfinanceで分割一括取得。1銘柄ずつ取得するより大幅に軽量。"""
    ts = list(dict.fromkeys([str(t) for t in tickers_tuple if str(t).endswith(".T")]))
    out = {}
    if not ts:
        return out
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
                fx = _prepare_light_history_frame(one)
                if not fx.empty:
                    out[t] = fx
            except Exception:
                continue
    return out


# ============================================================
# Ver.17 MAIN — ストキャスティクス実運用版
# ============================================================
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

st.title("📈 日本株 AI投資アシスタント Ver.17.7")
st.caption(f"{VERSION} / BUILD: {BUILD}")
st.success("本物の企業価値AI TOP50 → Slow Stochastic 14,3,3 → BUY候補だけをシンプル表示")
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
    bp_file = st.file_uploader(
        "買付余力ファイル（任意）", type=["csv", "txt", "html", "htm", "webarchive"], key="sbi_buying_power_file_v177"
    )
    if "sbi_buying_power_yen_v177" not in st.session_state:
        st.session_state["sbi_buying_power_yen_v177"] = 0
    if bp_file is not None:
        try:
            detected_buying_power, _ = extract_buying_power_from_file(bp_file)
            st.session_state["sbi_buying_power_yen_v177"] = int(detected_buying_power)
        except Exception as e:
            st.warning(f"余力自動読取不可：{e}")
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
                    "source": str(r["source"]), "account_types": str(r.get("account_types", "")),
                }
                for _, r in holdings_auto_df.iterrows()
            }
        if parse_errors:
            st.warning("一部CSVを読めませんでした：\n- " + "\n- ".join(parse_errors))
held_codes = list(confirmed.keys())

m1, m2 = st.columns(2)
m1.metric("現在保有", f"{len(held_codes)}銘柄")
m2.metric("買付余力", f"¥{int(buying_power):,}")
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
    st.caption("標準：350銘柄 → 流動性等で90銘柄へ一次選抜 → 企業価値AIでTOP50。BUYはそのTOP50だけ。")

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
        st.session_state["v177_value_top50"] = pd.DataFrame()
        st.session_state["v177_run_error"] = "大規模母集団を200銘柄以上取得できなかったため、BUY判定を安全停止しました。旧49銘柄へは戻していません。"
    else:
        universe_codes = universe_df["コード"].astype(str).tolist()
        # SELL監視のため保有銘柄は母集団外でも追加する。
        all_codes = list(dict.fromkeys(universe_codes + [str(c) for c in held_codes]))
        all_tickers = tuple(tickers(",".join(all_codes)))
        with st.spinner(f"{len(all_tickers)}銘柄の日足を一括取得中…"):
            data = batch_stock_data_light(all_tickers, months=15)
        st.session_state["v177_data"] = data
        # データ取得成功数が小さすぎる場合も偽TOP50を作らない。
        mother_data = {t:d for t,d in data.items() if code(t) in set(universe_codes)}
        if len(mother_data) < 150:
            st.session_state["v177_value_top50"] = pd.DataFrame()
            st.session_state["v177_run_error"] = f"日足取得成功が{len(mother_data)}銘柄のみのため、BUY判定を安全停止しました。"
        else:
            with st.spinner("企業価値AIで一次選抜 → 本物TOP50を作成中…"):
                value_top50_df = build_value_ai_top50(
                    mother_data, max_rows=50, fundamental_pool_size=int(fundamental_pool_size)
                )
            st.session_state["v177_value_top50"] = value_top50_df
            st.session_state["v177_generated_at"] = tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST")
            st.session_state["v177_run_error"] = ""

universe_df = st.session_state.get("v177_universe_df", pd.DataFrame())
data = st.session_state.get("v177_data", {})
value_top50_df = st.session_state.get("v177_value_top50", pd.DataFrame())
run_error = st.session_state.get("v177_run_error", "")

# ------------------------------------------------------------
# TOP50だけを新規BUY対象にする。SELLは保有全銘柄を監視。
# ------------------------------------------------------------
buy_view = pd.DataFrame()
sell_view = pd.DataFrame()
buy_signal_rows, sell_signal_rows = [], []
true_top50_codes = set()
if isinstance(value_top50_df, pd.DataFrame) and not value_top50_df.empty:
    true_top50_codes = set(value_top50_df["コード"].astype(str).head(50).tolist())
held_code_set = set(map(str, held_codes))

if data and true_top50_codes:
    for t, df0 in data.items():
        c = code(t)
        # 新規BUYは本物TOP50のみ、SELLは保有のみ。それ以外は計算不要。
        if c not in true_top50_codes and c not in held_code_set:
            continue
        sx = stoch_prepare_light(df0, 14, 3, 3)
        if sx.empty:
            continue
        r = sx.iloc[-1]
        sk, sd, px = safe_float(r.get("STOCH_K")), safe_float(r.get("STOCH_D")), safe_float(r.get("Close"))
        if not (np.isfinite(px) and px > 0):
            continue
        if c in true_top50_codes and c not in held_code_set and bool(r.get("STOCH_GC", False)) and np.isfinite(sk) and sk <= 20:
            buy_signal_rows.append({
                "コード": c, "銘柄名": name(t), "総合AIスコア": 100.0-float(sk),
                "現在株価": float(px), "%K": float(sk), "%D": float(sd), "条件": "%K≤20 GC",
            })
        if c in held_code_set:
            h = confirmed.get(c, {})
            shares = int(safe_float(h.get("shares", 0)) or 0)
            avg = safe_float(h.get("avg_price", np.nan))
            pnl_pct = ((px / avg - 1.0) * 100.0) if np.isfinite(avg) and avg > 0 else np.nan
            dc_hit = bool(r.get("STOCH_DC", False)) and np.isfinite(sk)
            stop_hit = np.isfinite(pnl_pct) and pnl_pct <= -float(sl)
            if dc_hit or stop_hit:
                reasons=[]
                if stop_hit: reasons.append(f"損切り -{float(sl):.1f}%")
                if dc_hit: reasons.append("ストキャスDC")
                sell_signal_rows.append({
                    "コード":c,"銘柄名":name(t),"保有株数":shares,"取得単価":avg,"現在株価":float(px),
                    "損益率%":pnl_pct,"%K":float(sk) if np.isfinite(sk) else np.nan,
                    "%D":float(sd) if np.isfinite(sd) else np.nan,"売り理由":"＋".join(reasons),
                })

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

# ------------------------------------------------------------
# シンプル画面 + 目立たない管理者タブ
# ------------------------------------------------------------
main_tab, admin_tab = st.tabs(["今日の売買", "管理者"])
with main_tab:
    if run_error:
        st.error(run_error)
    elif not data or not true_top50_codes:
        st.info("「今日の判定を更新」を押してください。")
    else:
        st.subheader("🔻 売り")
        if sell_view.empty:
            st.success("売り候補はありません。")
        else:
            st.dataframe(sell_view, use_container_width=True, hide_index=True)

        st.subheader("🟢 買い")
        if buy_view.empty:
            st.info("本物の企業価値AI TOP50内に、現在BUY条件を満たす銘柄はありません。")
        else:
            simple_buy_cols=[c for c in ["順位","コード","銘柄名","現在株価","参考S株数","%K","%D","買付可否"] if c in buy_view.columns]
            st.dataframe(buy_view[simple_buy_cols], use_container_width=True, hide_index=True)
        st.caption(f"新規BUY監視：企業価値AI TOP50のみ / SELL監視：現在保有 {len(held_codes)}銘柄")

with admin_tab:
    st.caption("比較・検証用。通常の朝はここを見る必要はありません。")
    ucnt = len(universe_df) if isinstance(universe_df, pd.DataFrame) else 0
    dcnt = len([1 for t in data if code(t) not in held_code_set]) if data else 0
    tcnt = len(value_top50_df) if isinstance(value_top50_df, pd.DataFrame) else 0
    c1,c2,c3,c4=st.columns(4)
    c1.metric("取得母集団", f"{ucnt}銘柄")
    c2.metric("日足取得", f"{dcnt}銘柄")
    c3.metric("企業価値AI TOP", f"{tcnt}銘柄")
    c4.metric("生成時刻", st.session_state.get("v177_generated_at", "—"))
    if isinstance(value_top50_df, pd.DataFrame) and not value_top50_df.empty:
        show_cols=["順位","コード","銘柄名","一次選抜順位","現在株価_価格","AI参考価値","参考価値上昇余地%","割安判定",
                   "企業価値スコア","成長性スコア","流動性スコア","AI_TOP50スコア","適正株価異常値ガード","株式分割補正"]
        admin_show=value_top50_df[[c for c in show_cols if c in value_top50_df.columns]].copy()
        st.dataframe(admin_show, use_container_width=True, hide_index=True)
    with st.expander("現在保有", expanded=False):
        if confirmed:
            st.dataframe(pd.DataFrame([{"コード":c,"銘柄名":name(c),"株数":v["shares"],"取得単価":v["avg_price"]} for c,v in confirmed.items()]), use_container_width=True, hide_index=True)
        else:
            st.caption("保有情報なし")

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
            "新規BUY対象":"企業価値AI TOP50のみ","BUY条件":"Slow Stoch 14,3,3 / %K<=20 GC",
            "SELL条件":f"保有銘柄のみ / Slow Stoch DC または 損切り -{float(sl):.1f}%",
            "旧49銘柄固定ユニバース使用":False,
        }])
        zf.writestr("ver17_settings.csv",settings_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(universe_df,pd.DataFrame):
            zf.writestr("japan_large_universe.csv",universe_df.to_csv(index=False,encoding="utf-8-sig"))
        if isinstance(value_top50_df,pd.DataFrame):
            zf.writestr("value_ai_top50.csv",value_top50_df.to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_trades_df.empty:
            zf.writestr("sbi_execution_history.csv",sbi_trades_df.to_csv(index=False,encoding="utf-8-sig"))
        if confirmed:
            zf.writestr("current_holdings.csv",pd.DataFrame([{"コード":c,"銘柄名":name(c),"株数":v["shares"],"取得単価":v["avg_price"]} for c,v in confirmed.items()]).to_csv(index=False,encoding="utf-8-sig"))
        if not sbi_warning_df.empty:
            zf.writestr("sbi_history_warnings.csv",sbi_warning_df.to_csv(index=False,encoding="utf-8-sig"))
        buy_cols_default=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","%K","%D","AI_TOP50スコア","割安判定","買付可否"]
        buy_export=buy_view.copy() if isinstance(buy_view,pd.DataFrame) and not buy_view.empty else pd.DataFrame(columns=buy_cols_default)
        zf.writestr("stoch_buy_candidates.csv",buy_export.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("stoch_sell_candidates.csv",sell_view.to_csv(index=False,encoding="utf-8-sig"))
        status_df=pd.DataFrame([{
            "本物TOP50モード":True,"母集団最低200銘柄ガード":True,
            "取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "日足取得成功件数":len(data) if isinstance(data,dict) else 0,
            "TOP50件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "BUY候補件数":len(buy_export),"SELL候補件数":len(sell_view),"エラー":run_error,
            "生成日時":st.session_state.get("v177_generated_at","")
        }])
        zf.writestr("value_ai_status.csv",status_df.to_csv(index=False,encoding="utf-8-sig"))
    zip_buf.seek(0)
    st.download_button("📦 全処理結果ZIP",data=zip_buf.getvalue(),file_name="ver17_all_analysis.zip",mime="application/zip",use_container_width=True,key="v177_zip")
except Exception as e:
    st.warning(f"ZIP作成エラー: {e}")

st.caption("売買判断補助です。自動発注は行いません。")
