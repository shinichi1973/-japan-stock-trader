# ============================================================
# 日本株 AI投資アシスタント Ver.17.24.19
# BUILD: VER17-24-19-INDEPENDENT-FINANCE-DAILY-DIAGNOSTICS-20261016
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
import hashlib
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
    page_title="日本株 AI投資アシスタント Ver.17.24.19",
    page_icon="📈",
    layout="wide",
)

VERSION = "17.24.19 TOPIX売買代金TOP100・国内データ"
BUILD = "VER17-24-19-INDEPENDENT-FINANCE-DAILY-DIAGNOSTICS-20261016"

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


def _fetch_free_finance_html(url):
    for attempt in range(2):
        response = requests.get(url, headers={"User-Agent":"Mozilla/5.0", "Accept":"text/html"}, timeout=12)
        if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(0.4)
            continue
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")
    raise ValueError("無料公開ページ取得失敗")


@st.cache_data(ttl=300, show_spinner=False)
def _domestic_finance_snapshot(t):
    if not re.fullmatch(r"[0-9A-Za-z]{4,5}\.T", str(t)):
        raise ValueError("国内株コード形式が不正")
    today = tokyo_now().date()
    sources = [
        ("Yahoo!日本版", f"https://finance.yahoo.co.jp/quote/{t}", _parse_domestic_finance),
        ("株探", f"https://kabutan.jp/stock/?code={code(t)}", _parse_kabutan_finance),
    ]
    attempts, partials = [], []
    for label, url, parser in sources:
        try:
            values = parser(_fetch_free_finance_html(url), today)
            values["_url"] = url
            attempts.append(label + ": " + values.get("_freshness", "不明"))
            partials.append(values)
            if finance_freshness_usable(values.get("_freshness")):
                values["_attempts"] = " / ".join(attempts)
                return values
        except Exception as exc:
            attempts.append(label + ": " + type(exc).__name__ + " " + str(exc)[:160])
    if partials:
        # 期の違うEPS/PERを混ぜず、1社分のスナップショット単位で採用する。
        values = max(partials, key=lambda v: sum(not k.startswith("_") for k in v))
        values["_attempts"] = " / ".join(attempts)
        return values
    raise ValueError(" / ".join(attempts))


def financial_unavailable_label(status, freshness):
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

        source_status = "OK" if has_fundamentals else "取得不可: 企業価値指標が全て欠損"
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
            "財務取得日時":tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST"),
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
        st.dataframe(pd.DataFrame(PUBLIC_RESEARCH_RESULTS_20261012), use_container_width=True, hide_index=True)
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
            st.dataframe(saved["result"], use_container_width=True, hide_index=True)
            if not saved["exclusions"].empty:
                st.dataframe(saved["exclusions"], use_container_width=True, hide_index=True)
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
                st.dataframe(history_exclusions, use_container_width=True, hide_index=True)
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
                st.dataframe(pd.DataFrame([r[0] for r in results]),use_container_width=True,hide_index=True)
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
                                lot_cap=None):
    """保有数・購入額の固定上限なし。TOP3へ候補順に注文余力を引き当てる。"""
    cols = ["購入優先度", "コード", "銘柄名", "総合AIスコア", "現在株価", "計算用株価",
            "購入株数", "予定購入額", "余力引当額", "買付余力使用率", "注文想定",
            "買付可否", "見送り理由", "購入後推定余力", "注文区分",
            "通常注文参考株数", "通常注文概算額"]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=cols)
    held = set(map(str, held_codes or []))
    x = candidates[~candidates["コード"].astype(str).isin(held)].copy()
    x = (x.sort_values(["候補スコア", "%K", "コード"], ascending=[False, True, True])
         if "候補スコア" in x else x.sort_values(["%K", "コード"], ascending=[True, True])).head(3)
    remaining = max(float(buying_power or 0), 0)
    rows = []
    for i, (_, r) in enumerate(x.iterrows(), 1):
        c = str(r["コード"])
        price = safe_float(r["現在株価"])
        calc_price = price * 1.05
        collateral_price = standard_limit_up_price(price)
        reason = ""
        if remaining <= 0:
            reason = "買付余力が0円/未入力"
        elif not np.isfinite(calc_price) or calc_price <= 0:
            reason = "株価データ不正"
        budget = (min(remaining, float(cap_yen)) if cap_yen is not None and cap_yen > 0 else remaining) if not reason else 0
        lot_budget = min(budget, float(lot_cap)) if lot_cap is not None and lot_cap > 0 else budget
        lot_shares = (100 * (min(int(lot_budget // calc_price),
                                int(remaining // collateral_price)) // 100)
                      if not reason and calc_price > 0 and np.isfinite(calc_price)
                      and collateral_price > 0 and np.isfinite(collateral_price) else 0)
        shares = (min(int(budget // calc_price), int(remaining // collateral_price))
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
                     "買付可否": "🟢 BUY" if shares else "⛔ NO BUY",
                     "見送り理由": reason, "購入後推定余力": max(remaining, 0)})
    return pd.DataFrame(rows, columns=cols)


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


def _download_domestic_history(t, cutoff, target_bars=300):
    """1ページの失敗で、それまでに取得済みの日足を捨てない。"""
    parts, seen, errors = [], set(), []
    pages = 0
    for page in range(1, 16):
        try:
            # requestsの推測文字コードを使うと、日本語の日付が文字化けする場合がある。
            for attempt in range(2):
                try:
                    response = requests.get(f"https://finance.yahoo.co.jp/quote/{t}/history",
                        params={"page": page}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if attempt == 0 and (status is None or status in (429, 500, 502, 503, 504)):
                        time.sleep(0.4)
                        continue
                    raise
            part = parse_domestic_daily_history(response.content.decode("utf-8", errors="replace"))
            if part.empty:
                errors.append(f"page={page}: 日付・OHLCVを読み取れず（表示変更・提供範囲・アクセス制限を確認）")
                break
            if not (set(part["Date"]) - seen):
                errors.append(f"page={page}: 前ページと同じ日付。公開範囲の取得を終了")
                break
            seen.update(part["Date"]); parts.append(part); pages += 1
            if len(seen) >= target_bars: break
        except Exception as exc:
            errors.append(f"page={page}: {type(exc).__name__}: {str(exc)[:160]}")
            break
    raw = (pd.concat(parts).drop_duplicates("Date").set_index("Date").sort_index()
           if parts else pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))
    if not raw.empty: raw = raw.loc[raw.index <= cutoff]
    return raw, pages, " / ".join(errors)


@st.cache_data(ttl=300, show_spinner=False)
def batch_domestic_stock_data(tickers_tuple, refresh_token=""):
    """国内日足と、失敗した銘柄も含む取得診断を返す。"""
    from concurrent.futures import ThreadPoolExecutor
    today = tokyo_now()
    cutoff = pd.Timestamp(today.date()) if today.hour >= 16 else pd.Timestamp(today.date()) - pd.Timedelta(days=1)
    def fetch(t):
        audit = {"コード": code(t), "銘柄名": name(t), "情報源": "Yahoo!ファイナンス日本版・時系列日足",
                 "取得行数": 0, "指標行数": 0, "取得ページ数": 0, "日足最終日": "", "状態": "取得不可", "詳細": ""}
        try:
            raw, pages, error = _download_domestic_history(t, cutoff)
            audit.update({"取得行数": len(raw), "取得ページ数": pages, "詳細": error})
            frame = _prepare_light_history_frame(raw)
            if frame.empty:
                audit["状態"] = "日足不足" if len(raw) else "取得不可"
                audit["詳細"] = (error + " / " if error else "") + f"移動平均計算に最低80行必要・取得{len(raw)}行"
                return t, None, audit
            latest = pd.Timestamp(frame.index.max()).normalize()
            weekdays = len(pd.bdate_range(latest, cutoff)) - 1
            split_suspect = bool(raw["Close"].pct_change().tail(60).abs().gt(.40).any())
            frame.attrs.update({"source": audit["情報源"], "latest_date": latest, "session_date": cutoff,
                "daily_bar_status": "🟢 当日確定" if weekdays <= 0 and not split_suspect else "🔴 更新待ち・価格断絶確認",
                "domestic_history": True})
            audit.update({"指標行数": len(frame), "日足最終日": latest.strftime("%Y-%m-%d"),
                          "状態": "取得済（一部ページ未取得）" if error else "取得済"})
            return t, frame, audit
        except Exception as exc:
            audit["詳細"] = type(exc).__name__ + ": " + str(exc)[:240]
            return t, None, audit
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, tickers_tuple))
    return ({t: f for t, f, a in results if f is not None}, pd.DataFrame([a for t, f, a in results]))


def batch_domestic_finance_audit(tickers_tuple):
    """日足・買いサインの成否に関わらず、財務の取得結果を残す。評価計算とは別。"""
    from concurrent.futures import ThreadPoolExecutor
    def fetch(t):
        row = {"コード": code(t), "銘柄名": name(t), "財務取得状態": "取得不可",
               "財務情報源": "", "財務情報鮮度": "", "参考指標基準日": "", "予想EPS対象期": "",
               "業績発表日": "", "予想EPS": np.nan, "予想PER": np.nan, "PBR": np.nan,
               "財務参照URL": "", "財務取得経路": "", "財務取得日時": tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST")}
        try:
            f = _domestic_finance_snapshot(t)
            row.update({"財務取得状態": "取得済" if finance_freshness_usable(f.get("_freshness")) else "一部取得・最新性未確認",
                "財務情報源": f.get("_source", ""), "財務情報鮮度": f.get("_freshness", ""),
                "参考指標基準日": f.get("_reference_date", ""), "予想EPS対象期": f.get("_forecast_period", ""),
                "業績発表日": f.get("_disclosure_date", ""), "予想EPS": safe_float(f.get("forwardEps")),
                "予想PER": safe_float(f.get("forwardPE")), "PBR": safe_float(f.get("priceToBook")),
                "財務参照URL": f.get("_url", ""), "財務取得経路": f.get("_attempts", "")})
        except Exception as exc:
            row["財務取得経路"] = type(exc).__name__ + ": " + str(exc)[:500]
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
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
    return {"値幅区分": "値幅未確認" if not np.isfinite(pct) else "値幅大" if pct >= 3 else "値幅中" if pct >= 1.5 else "値幅小",
            "ATR比率%": pct, "安値切上げ": bool(rising)}


@st.cache_data(ttl=1800, show_spinner=False)
def discover_domestic_topix_turnover100():
    """JPX構成一覧と日本版Yahoo売買代金ランキングの積集合を順位順に100件。"""
    topix = discover_topix_stock_universe()
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
                out["対象指数"] = "TOPIX"
                out["選定情報源"] = "JPX構成一覧＋Yahoo!ファイナンス日本版売買代金順位"
                out.attrs["universe_notice"] = "TOPIX内の売買代金上位100。ランキングは取得時点（日中は進行中）、買いサインは最新確定日足。"
                return out
        if not found or not added:
            break
    raise ValueError("国内売買代金ランキングからTOPIX100件を確認できません。新規買いを停止します。")


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
        if metrics["値幅区分"] != "値幅大":
            reasons.append("値幅大条件未達（ATR14/終値3%未満または不明）")
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
            **{k: str(f.get(k, "")) for k in ("財務参照URL", "財務取得経路", "業績発表日", "実績EPS対象期")},
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
        financial_unavailable_label(rr.get("ファンダ取得状態"), rr.get("財務情報鮮度")) if financial_unavailable_label(rr.get("ファンダ取得状態"), rr.get("財務情報鮮度")) else
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
    return merge_index_universes(discover_topix_stock_universe(),
                                 discover_nikkei225_stock_universe())


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
            action_text = "🟢 買い" if can_buy else "⛔ 見送り"
            card_class = "skip-card" if is_overvalued else "buy-card" if can_buy else "skip-card"
            quantity_text = "割高" if is_overvalued else f"参考 {shares:,}株" if can_buy else _mobile_text(row.get("買付可否"), "購入不可")
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
        # TOP50外の保有銘柄も、現在情報を取得できる場合は企業価値を個別算定する。
        if not str(value_label).strip():
            symbol = ticker if ticker.endswith(".T") else ticker + ".T"
            history = price_data.get(symbol, pd.DataFrame()) if isinstance(price_data, dict) else pd.DataFrame()
            price_hint = safe_float(history["Close"].iloc[-1]) if isinstance(history, pd.DataFrame) and not history.empty else np.nan
            f = (fundamental_snapshot(symbol, market_price_hint=price_hint)
                 if np.isfinite(price_hint) and price_hint > 0 else {})
            upside = safe_float(f.get("参考価値上昇余地"))
            split_status = str(f.get("株式分割補正", ""))
            guard_status = str(f.get("適正株価異常値ガード", ""))
            if not finance_freshness_usable(f.get("財務情報鮮度")) or f.get("取得状態") != "OK":
                value_label = financial_unavailable_label(f.get("取得状態"), f.get("財務情報鮮度"))
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
                     "銘柄判定": _short_value_label(value_label),
                     **selection_range_metrics(price_data.get(ticker if ticker.endswith(".T") else ticker + ".T"))})
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
            f'<span class="holding-name">{html_escape(str(row["銘柄名"]))}<br><small>{html_escape(str(row.get("値幅区分", "値幅未確認")))}</small></span>'
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


st.title("📈 日本株 AI投資アシスタント Ver.17.24.19")
st.caption(f"{VERSION} / BUILD: {BUILD}")
st.caption("売買条件は管理者設定の売買ロジックで選択。保有銘柄数・1銘柄購入額の固定上限なし。終値7%損切りを適用します。")
st.caption("45銘柄外の保有銘柄も、DCまたは終値で−7%の売りを判定します。")
st.caption("監視対象・購入予算・注文方式は設定欄で選択。株数上限解除・対象拡大の成績は比較検証で確認してください。")

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
all_held_codes = list(confirmed.keys())
short_confirmed = confirmed
held_codes = all_held_codes
short_buying_power = int(buying_power)
if int(buying_power) <= 0:
    st.warning("買付余力を入力すると参考S株数を計算できます。")
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
    live_universe_mode = st.selectbox("新規買いの監視対象", ["TOPIX売買代金TOP100・値幅大＋買いサイン", "TOPIX＋日経225からAI選定TOP50", "固定45銘柄", "対象拡大（売買代金上位）"], key="v172417_universe")
    signal_first = live_universe_mode == "TOPIX売買代金TOP100・値幅大＋買いサイン"
    live_trade_method = st.selectbox("売買ロジック", ["現行Stoch GC/DC", "55日高値更新＋MA200（試験採用）"], key="v17247_method")
    replacement_live = live_trade_method == "55日高値更新＋MA200（試験採用）"
    st.caption("試験採用：買い＝前日までの55日高値を終値で更新＋終値>MA200、売り＝前日までの20日安値を終値で割る、または取得単価から終値7%損切り。候補は63日騰落率順。Stochは売買・順位に使いません。")
    if replacement_live:
        st.warning("拡大344銘柄の過去比較では有望ですが、TOPIX全体と財務除外込みの成績は未検証です。保有銘柄の売り条件もこの方式へ切り替わります。")
    block_unrated = True
    st.caption("国内財務の判定不能は新規買いを見送ります。")
    min_turnover = st.number_input("買い候補の20日平均売買代金下限（円・最低1億円）", 100000000, 10000000000, 100000000, 10000000, key="v172416_liquidity", disabled=not signal_first)
    filter_downtrend = st.checkbox("下降確認の候補を除外（未検証）", value=False, key="v17243_down", disabled=not signal_first or replacement_live)
    filter_churn = st.checkbox("短期クロス多発の候補を除外（未検証）", value=False, key="v17243_churn", disabled=not signal_first or replacement_live)
    st.caption("値幅：ATR14/終値が3%以上＝大、1.5%以上＝中、それ未満＝小。大のみ新規対象。安値切上げ：直近3つの10営業日安値が順に上昇し、終値が25日平均より上。20日平均売買代金は最低1億円。国内財務の判定不能は除外。")
    cap_mode = "上限なし（買付余力内・候補順に引当）"
    purchase_cap = None
    lot_cap = None
    st.caption("1銘柄の購入額・通常注文予算の固定上限は解除済みです。資産の3分の1による制限も適用しません。")
    execution_mode = st.selectbox("実行する注文方式", ["自動（通常注文優先・不足はS株）", "S株のみ（1〜99株）", "100株単位（通常注文）"], key="v1724_order")
    ifd_take_profit = st.slider("参考IFDOCOの利確率（%）", 3.0, 30.0, 15.0, 0.5, key="v1724_tp")
    st.caption("保有数上限なし。買い候補TOP3へ順位順に余力を引き当てます。1位が余力の大半を使う場合があります。自動は余力内で100株買えれば通常注文、買えなければS株（最大99株）。参考IFDOCO表は代替案なので、買いカードと二重発注しないでください。")
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
    fundamental_pool_size = c2.number_input("詳細企業価値評価へ進める一次選抜数", 60, 150, 90, 10, key="v177_fund_pool", disabled=live_universe_mode not in ("TOPIX＋日経225からAI選定TOP50", "TOPIX売買代金TOP100・値幅大＋買いサイン"))
    st.caption("標準選定：TOPIX→国内売買代金上位100→値幅大＋買いサイン→流動性→安値切上げ→国内財務で割安／やや割安。構成一覧には基準日の差があります。")
    st.caption("Stochの買い・売りと終値7%損切りは維持。対象拡大は未検証のため、比較結果を確認してから選択してください。")
    churn_filter = st.checkbox("短期クロス反転が多い銘柄を新規買いTOP50から除外", value=False, key="v1716_churn_filter", disabled=True)
    st.caption("今回の主ロジックでは短期反転除外を使用しません。")
    quality_mode = st.checkbox("トレンド＋振れ幅フィルターを実売買判定に使用（検証後に切替）",
                               value=False, key="v1717_quality_mode", disabled=True)
    st.caption("トレンド＋振れ幅フィルターは主ロジックでは使用しません。")

# 既存のStreamlitセッションに過去の設定が残っていても検証済み条件を固定する。
sl, churn_filter, quality_mode = 7.0, False, False

run_clicked = st.button("▶ 今日の判定を更新", type="primary", use_container_width=True, key="v177_run")

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
    st.session_state.pop("v172412_history_refresh_required", None)
    universe_error = ""
    try:
        universe_df = (discover_domestic_topix_turnover100() if live_universe_mode == "TOPIX売買代金TOP100・値幅大＋買いサイン" else discover_topix_nikkei225_stock_universe() if live_universe_mode in ("TOPIX＋日経225からAI選定TOP50", "TOPIX売買代金TOP100・値幅大＋買いサイン")
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
            st.caption(f"監視対象：TOPIX {index_tags.str.contains('TOPIX').sum()}銘柄／日経225 {index_tags.str.contains('日経225').sum()}銘柄／重複除外後 {len(universe_df)}銘柄")
    st.session_state["v177_universe_df"] = universe_df
    selected_codes = universe_df["コード"].astype(str).tolist() if not universe_df.empty else []
    all_codes = list(dict.fromkeys(selected_codes + [str(c) for c in all_held_codes]))
    all_tickers = tuple(tickers(",".join(all_codes)))
    with st.spinner(f"{len(all_tickers)}銘柄の無料国内財務を取得・確認中…"):
        st.session_state["v172419_finance_audit"] = batch_domestic_finance_audit(all_tickers)
    _fa = st.session_state["v172419_finance_audit"]
    if not _fa.empty:
        st.caption(f"財務取得済 {int(_fa['財務取得状態'].eq('取得済').sum())} / {len(_fa)}銘柄。日足取得とは独立して確認しました。")
    with st.spinner(f"{len(all_tickers)}銘柄の国内日足を取得中…"):
        if signal_first:
            data, _daily_diagnostics = batch_domestic_stock_data(all_tickers, tokyo_now().isoformat())
        else:
            data = batch_stock_data_light(all_tickers, months=15)
            _daily_diagnostics = pd.DataFrame()
        st.session_state["v172419_daily_diagnostics"] = _daily_diagnostics
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
            mother_data, max_rows=min(50, eligible_count) if live_universe_mode in ("TOPIX＋日経225からAI選定TOP50", "TOPIX売買代金TOP100・値幅大＋買いサイン") else eligible_count,
            fundamental_pool_size=int(fundamental_pool_size) if live_universe_mode in ("TOPIX＋日経225からAI選定TOP50", "TOPIX売買代金TOP100・値幅大＋買いサイン") else eligible_count,
            churn_filter=False,
        ) if eligible_count else (pd.DataFrame(), pd.DataFrame()))
        st.session_state["v177_value_top50"] = value_top50_df
        st.session_state["v1716_churn_audit"] = pd.DataFrame()
        st.session_state["v177_generated_at"] = tokyo_now().strftime("%Y-%m-%d %H:%M:%S JST")
        st.session_state["v177_run_error"] = ("TOPIX＋日経225から50銘柄を選定できませんでした。新規買いを停止し、保有銘柄の売り判定のみ行います。"
            if live_universe_mode == "TOPIX＋日経225からAI選定TOP50" and value_top50_df.empty else "")

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

# ------------------------------------------------------------
# 固定45銘柄の未保有株の新規BUYと、全保有銘柄のSELLを監視。
# ------------------------------------------------------------
buy_view = pd.DataFrame()
sell_view = pd.DataFrame()
buy_signal_rows, sell_signal_rows = [], []
signal_audit_rows = []
true_top50_codes = (set(value_top50_df["コード"].astype(str))
                    if isinstance(value_top50_df, pd.DataFrame) and "コード" in value_top50_df else set()) if live_universe_mode == "TOPIX＋日経225からAI選定TOP50" else (
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
value_judgement_by_code = {}
if isinstance(value_top50_df, pd.DataFrame) and not value_top50_df.empty:
    value_meta = value_top50_df.head(50).copy()
    value_meta["コード"] = value_meta["コード"].astype(str).str.replace(r"\.0$", "", regex=True)
    if "割安判定" in value_meta.columns:
        value_judgement_by_code = (
            value_meta.drop_duplicates("コード").set_index("コード")["割安判定"].astype(str).to_dict()
        )
if signal_first and not signal_value_df.empty:
    value_judgement_by_code.update(signal_value_df.set_index("コード")["割安判定"].astype(str).to_dict())
signal_meta_by_code = signal_value_df.set_index("コード").to_dict("index") if not signal_value_df.empty else {}
extract_reason_by_code = signal_first_audit_df.set_index("コード")["抽出除外理由"].to_dict() if not signal_first_audit_df.empty else {}
value_unrated_count = 0  # 企業価値の算定可否は固定45銘柄の売買判定を止めない。
held_code_set = set(map(str, held_codes))

if data and (true_top50_codes or held_code_set):
    for t, df0 in data.items():
        c = code(t)
        # 新規BUYは固定45の未保有株、保有銘柄は45銘柄外もSELLを判定する。
        if c not in true_top50_codes and c not in held_code_set:
            continue
        sx = (public_replacement_frame(df0, "高値更新55＋MA200・安値割れ20")
              if replacement_live else stoch_quality_frame(df0, sl))
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
        if signal_first and new_buy_target and buy_signal_hit:
            buy_skip_reason = extract_reason_by_code.get(c, "日足不足または抽出対象外")
            meta = signal_meta_by_code.get(c, {})
            if not buy_skip_reason and overvalued:
                buy_skip_reason = "割高判定のため新規買い除外"
            if not buy_skip_reason and block_unrated and not bool(meta.get("企業価値算定済", False)):
                buy_skip_reason = "企業価値判定不能のため新規買い見送り"
                value_unrated_count += 1
            if not buy_skip_reason and (not bool(meta.get("企業価値算定済", False)) or "割安" not in str(value_judgement)):
                buy_skip_reason = "国内財務による割安・やや割安条件未達"
            final_buy_hit = final_buy_hit and not buy_skip_reason
        if c in true_top50_codes or c in held_code_set:
            signal_audit_rows.append({"コード": c, "銘柄名": name(t), "監視対象": "保有" if c in held_code_set else ("TOPIX＋日経225全体" if signal_first else "監視対象"),
                                      "現行買い": buy_old, "改善買い": buy_new,
                                      "採用方式の買いシグナル": bool(buy_signal_hit),
                                      "銘柄判定": value_judgement,
                                      "割高除外": bool(signal_first and overvalued),
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
    with st.expander("TOPIX＋日経225買いシグナルの抽出・見送り理由", expanded=False):
        st.caption(f"買いシグナル抽出 {len(signal_first_audit_df)}銘柄 / 財務判定不能による見送り {value_unrated_count}銘柄。" + ("通過候補はATR比率の大きい順。" if replacement_live else "通過候補はATR比率の大きい順。入口変更後の収益性は未検証。"))
        st.dataframe(signal_audit_df[signal_audit_df["現行買い"].eq(True)], use_container_width=True, hide_index=True)

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
    buy_signal_df = pd.DataFrame(buy_signal_rows)
    buy_signal_df = (buy_signal_df.sort_values(["候補スコア", "コード"], ascending=[False, True]) if signal_first else buy_signal_df.sort_values(["63日騰落率%", "コード"], ascending=[False, True]) if replacement_live else buy_signal_df.sort_values(["%K", "コード"])).reset_index(drop=True)
    plan = build_fixed45_purchase_plan(
        buy_signal_df, short_buying_power, current_assets, held_codes,
        purchase_cap, execution_mode, lot_cap,
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
    cols=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","ロジック","63日騰落率%","%K","%D","候補スコア","AI_TOP50スコア","割安判定","買付可否","参考購入株数","S株注文参考株数","注文区分","通常注文参考株数","通常注文概算額","余力引当額","見送り理由"]
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
            st.dataframe(pd.DataFrame([{"コード":c,"理由":v} for c,v in state["errors"].items()]))
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
        st.dataframe(pd.DataFrame([r[0] for r in results]), use_container_width=True, hide_index=True)
        st.line_chart(pd.concat([r[2].set_index("日付")["総資産"].rename(r[0]["条件"])
                                 for r in results], axis=1))
        for summary, trades, curve, open_positions in results:
            with st.expander(f"{summary['条件']}：決済と未決済の内訳"):
                st.dataframe(trades, use_container_width=True, hide_index=True)
                st.caption("最終評価値は売却時の手数料を差し引く前の評価です。")
                st.dataframe(open_positions, use_container_width=True, hide_index=True)
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
        reasons.append("買付余力なし")
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
    st.subheader("⭐ 今日の一推し（無料材料評価）")
    with st.expander("無料ニュース評価の設定", expanded=False):
        auto = st.checkbox("今日の判定を更新と一緒に無料ニュースも更新", value=True, key="v172410_news_auto")
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
            st.warning("注目のみ・購入不可：" + " / ".join(reasons))
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
            st.dataframe(audit, use_container_width=True, hide_index=True)
        if not exclusions.empty:
            st.dataframe(exclusions, use_container_width=True, hide_index=True)
    return result, exclusions, signature


# GFSの公開講義概要を参考にした独立研究。動画本編の完全再現ではない。
GFS_RESEARCH_SOURCES = [
    {"資料": "新高値ブレイク投資・実践編", "確認できた考え方": "年初来高値・52週高値を起点に選別", "URL": "https://gfs.tokyo/video/6031"},
    {"資料": "最も重要な損切りと資金管理", "確認できた考え方": "取得価格から8%損切り・資金管理", "URL": "https://gfs.tokyo/video/6032"},
    {"資料": "売り方の極意", "確認できた考え方": "株価2倍で半分利確という例", "URL": "https://gfs.tokyo/video/6373"},
]


def build_gfs_research_signals(price_data, freshness, holdings, value_frames, live_audit,
                               high_window=252, volume_min=0.0, ma_exit=False):
    """終値で前日までの高値を更新。8%損切り/2倍半分利確は保有の取得単価基準。"""
    buy_cols = ["コード", "銘柄名", "日足日", "現在株価", "前日までの高値", "高値超過率%", "出来高倍率", "銘柄判定", "研究買い理由", "現行の買い条件も一致", "扱い"]
    sell_cols = ["コード", "銘柄名", "日足日", "現在株価", "保有株数", "取得単価", "損益率%", "研究売り理由", "参考売却株数", "現行の売り条件も一致", "扱い"]
    buys, sells, audit = [], [], []
    fresh_map = {str(r["コード"]): r.to_dict() for _, r in freshness.iterrows()} if isinstance(freshness, pd.DataFrame) and "コード" in freshness else {}
    values, live = {}, {}
    for frame in value_frames:
        if isinstance(frame, pd.DataFrame) and "コード" in frame:
            values.update({str(r["コード"]): str(r.get("割安判定", "— 未算定")) for _, r in frame.iterrows()})
    if isinstance(live_audit, pd.DataFrame) and "コード" in live_audit:
        live = {str(r["コード"]): r.to_dict() for _, r in live_audit.iterrows()}
    held = {str(c): v for c, v in holdings.items()}
    for ticker, df in (price_data or {}).items():
        c = code(ticker)
        company = name(ticker)
        f = fresh_map.get(c, {})
        status = ""
        if not isinstance(df, pd.DataFrame) or df.empty or not {"Close", "High"}.issubset(df.columns):
            status = "有効日足なし"
        elif f.get("状態") != "🟢 当日確定":
            status = "最新確定日足を確認できず"
        if status:
            audit.append({"コード": c, "銘柄名": company, "状態": status, "研究買い": False, "研究売り": False})
            continue
        x = df.sort_index().copy()
        close = pd.to_numeric(x["Close"], errors="coerce")
        high = pd.to_numeric(x["High"], errors="coerce")
        px = safe_float(close.iloc[-1])
        if not np.isfinite(px) or px <= 0:
            audit.append({"コード": c, "銘柄名": company, "状態": "終値欠損", "研究買い": False, "研究売り": False})
            continue
        # Both series share the data source's split-adjusted price basis.
        source_date = str(df.attrs.get("gfs_source_final_date", ""))
        raw_high_ready = (high_window == 252 and df.attrs.get("gfs_history_version") == 2 and
                          source_date == pd.Timestamp(x.index[-1]).isoformat())
        previous_high = (safe_float(df.attrs.get("gfs_252_previous_high")) if raw_high_ready else
                         safe_float(high.shift(1).rolling(high_window, min_periods=high_window).max().iloc[-1]))
        prior_bars = int(df.attrs.get("gfs_prior_bars", 0)) if raw_high_ready else max(len(x) - 1, 0)
        ma25 = safe_float(close.rolling(25, min_periods=25).mean().iloc[-1])
        volume_ratio = np.nan
        if "Volume" in x:
            vol = pd.to_numeric(x["Volume"], errors="coerce")
            previous_volume = safe_float(vol.shift(1).rolling(20, min_periods=20).mean().iloc[-1])
            if np.isfinite(previous_volume) and previous_volume > 0:
                volume_ratio = safe_float(vol.iloc[-1]) / previous_volume
        verdict = values.get(c, "— 未算定")
        raw_buy = bool(np.isfinite(previous_high) and px > previous_high)
        reason = ""
        if c in held:
            reason = "既に保有中（買い増しなし）"
        elif not np.isfinite(previous_high):
            reason = f"前日までの{high_window}営業日日足不足"
        elif "割高" in verdict:
            reason = "割高の研究買い除外（独自補完）"
        elif volume_min > 0 and (not np.isfinite(volume_ratio) or volume_ratio < volume_min):
            reason = "独自の出来高条件を満たさない"
        final_buy = raw_buy and not reason
        bar_date = str(f.get("日足最終日", ""))
        if final_buy:
            buys.append({"コード": c, "銘柄名": company, "日足日": bar_date, "現在株価": px,
                         "前日までの高値": previous_high, "高値超過率%": round((px/previous_high-1)*100, 2),
                         "出来高倍率": round(volume_ratio, 2) if np.isfinite(volume_ratio) else np.nan,
                         "銘柄判定": verdict, "研究買い理由": f"終値が前日までの{high_window}営業日高値を更新",
                         "現行の買い条件も一致": bool(live.get(c, {}).get("最終買い", False)), "扱い": "研究候補・注文には使用しない"})
        sell_reason = []
        if c in held:
            h = held[c]
            qty = int(safe_float(h.get("shares"), 0))
            avg = safe_float(h.get("avg_price"))
            pnl = (px / avg - 1) * 100 if np.isfinite(avg) and avg > 0 else np.nan
            sell_qty = 0
            if qty > 0 and np.isfinite(pnl):
                if px <= avg * 0.92:
                    sell_reason.append("終値で8%損切り（研究）")
                    sell_qty = qty
                elif px >= avg * 2.0 and qty >= 2:
                    sell_reason.append("株価2倍・半分利確候補（初回のみ／過去利確未確認）")
                    sell_qty = qty // 2
                if ma_exit and np.isfinite(ma25) and px < ma25:
                    sell_reason.append("MA25割れ（独自補完）")
                    sell_qty = qty
            else:
                reason = "保有株数または取得単価を確認できず"
            if sell_reason:
                live_sell = bool(live.get(c, {}).get("現行売り", False)) or bool(np.isfinite(pnl) and pnl <= -7.0)
                sells.append({"コード": c, "銘柄名": company, "日足日": bar_date, "現在株価": px,
                              "保有株数": qty, "取得単価": avg, "損益率%": round(pnl, 2),
                              "研究売り理由": " / ".join(sell_reason), "参考売却株数": sell_qty,
                              "現行の売り条件も一致": live_sell, "扱い": "研究候補・注文には使用しない"})
        audit.append({"コード": c, "銘柄名": company, "状態": reason or "判定済み", "研究買い": final_buy,
                      "研究売り": bool(sell_reason), "高値更新のみ": raw_buy, "銘柄判定": verdict,
                      "前日までの高値": previous_high, "現在株価": px, "高値計算に使える前日日足本数": prior_bars,
                      "高値取得元": "準備期間を削る前の日足" if raw_high_ready else "受取日足から計算"})
    buy = pd.DataFrame(buys, columns=buy_cols)
    if not buy.empty:
        buy = buy.sort_values(["高値超過率%", "コード"], ascending=[False, True]).reset_index(drop=True)
    return buy, pd.DataFrame(sells, columns=sell_cols), pd.DataFrame(audit)


def render_gfs_research(price_data, freshness, holdings, value_frames, live_audit):
    st.subheader("GFSロジック研究")
    st.warning("GFSの公開講義概要を参考にした研究案です。動画本編の完全再現・GFS公認・収益検証済みのロジックではありません。")
    st.caption("現在取得している監視対象＋保有銘柄を判定します。ここで点灯しても、今日の売買・購入株数・正式注文には反映しません。")
    conditions = pd.DataFrame([
        {"項目": "買いの着想", "内容": "年初来高値・52週高値の銘柄選別", "根拠": "公式講義概要で確認"},
        {"項目": "研究買いの計算", "内容": "前日までの252営業日高値を当日の終値で上回る", "根拠": "52週の近似と終値判定は独自補完"},
        {"項目": "研究売り", "内容": "取得単価から終値8%損切り／株価2倍で初回のみ半分利確", "根拠": "割合は公開概要、終値判定等は独自補完"},
        {"項目": "補助条件", "内容": "既知の割高・未確定日足・保有中の新規買いを除外", "根拠": "独自補完。財務未算定は研究参考として残す"},
        {"項目": "未再現", "内容": "カップ形状・事業の成長ストーリー・材料の持続性・地合い判断", "根拠": "詳細条件を確認できず自動判定しない"},
    ])
    with st.expander("研究条件・出典", expanded=False):
        st.dataframe(conditions, use_container_width=True, hide_index=True)
        for source in GFS_RESEARCH_SOURCES:
            st.link_button(source["資料"], source["URL"])
        st.caption("公開概要の確認日：2026-10-12。2倍利確は過去の利確実施を追跡していないため、繰り返し半分売る指示ではありません。")
    volume_min = st.number_input("追加研究：出来高倍率の下限（0で無効）", 0.0, 10.0, 0.0, 0.1, key="v172411_gfs_vol")
    ma_exit = st.checkbox("追加研究：MA25割れを売り条件にする（GFS原文の条件ではない）", value=False, key="v172411_gfs_maexit")
    buy, sell, audit = build_gfs_research_signals(price_data, freshness, holdings, value_frames, live_audit, 252, volume_min, ma_exit)
    c1, c2, c3 = st.columns(3)
    c1.metric("研究買い", len(buy)); c2.metric("研究売り", len(sell)); c3.metric("確認対象", len(audit))
    st.write("**研究買いシグナル**")
    if buy.empty:
        st.info("研究条件に該当する新規買い銘柄はありません。")
    else:
        st.dataframe(buy, use_container_width=True, hide_index=True)
    st.write("**保有銘柄の研究売りシグナル**")
    if sell.empty:
        st.info("現在保有している銘柄に研究売りシグナルはありません。")
    else:
        st.dataframe(sell, use_container_width=True, hide_index=True)
    st.caption("研究用8%損切りは現行の7%損切りを変更しません。日中の逆指値条件とも異なります。")
    with st.expander("判定不足・見送り理由", expanded=False):
        if not audit.empty:
            st.dataframe(audit[audit["状態"].ne("判定済み")], use_container_width=True, hide_index=True)
    st.write("**新しい動画・ロジックの監視**")
    st.caption("ChatGPTの監視タスクで公式サイト・公式YouTubeを確認します。新着は通知で確認し、本文や字幕から条件を検証してから研究案を更新します。このアプリのコードが自動で書き換わることはありません。")
    st.link_button("GFS公式YouTube", "https://www.youtube.com/channel/UCpLNShj3M-bQVPDZ8lD66fw")
    params = {"研究名": "GFS公開概要に基づく新高値研究", "高値期間_営業日": 252,
              "研究損切り%": 8.0, "半分利確_上昇率%": 100.0, "追加出来高下限": volume_min,
              "追加MA25売り": ma_exit, "正式売買に接続": False, "原文完全再現": False,
              "資料確認日": "2026-10-12", "高値計算の履歴処理版": 2}
    buf = io.BytesIO()
    with ZipFile(buf, "w") as z:
        for filename, frame in [("gfs_research_buy.csv", buy), ("gfs_research_sell.csv", sell),
                                ("gfs_research_audit.csv", audit), ("gfs_research_conditions.csv", conditions),
                                ("gfs_research_sources.csv", pd.DataFrame(GFS_RESEARCH_SOURCES)),
                                ("gfs_research_settings.csv", pd.DataFrame([params]))]:
            z.writestr(filename, frame.to_csv(index=False, encoding="utf-8-sig"))
    st.download_button("GFS研究シグナルZIP", buf.getvalue(), "gfs_research_signals.zip", "application/zip", key="v172411_gfs_download")
    return buy, sell, audit, params


# ------------------------------------------------------------
# 短期売買と管理者タブ
# ------------------------------------------------------------
main_tab, admin_tab = st.tabs(["今日の売買", "管理者"])
with main_tab:
    news_pick_result, news_pick_exclusions, news_pick_signature = render_triple_news_pick(
        indicator_compare_df, daily_bar_status_df, data, held_codes,
        [value_top50_df, signal_value_df], buy_view, signal_audit_df,
        short_buying_power, bool(signal_first and block_unrated), run_clicked,
    )
    m1, m2 = st.columns(2)
    m1.metric("保有", f"{len(held_codes)}銘柄")
    m2.metric("買付余力", f"¥{int(short_buying_power):,}")
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
        short_confirmed, sell_view, signal_audit_rows, value_top50_df, data
    ))
    if run_error:
        st.error(run_error)
    elif not data:
        st.info("「今日の判定を更新」を押してください。")
    else:
        st.subheader("🔻 売り")
        if sell_view.empty:
            st.success("売り候補はありません。")
        else:
            render_mobile_trade_cards(sell_view, "sell", trend_by_code)
            with st.expander("売り候補の詳細表", expanded=False):
                sell_display = sell_view.rename(columns={"割安判定": "銘柄判定"})
                st.dataframe(sell_display, use_container_width=True, hide_index=True)

        st.subheader("🟢 買い")
        if buy_view.empty:
            st.info("監視対象に、現在の買い条件を満たす銘柄はありません。")
        else:
            render_mobile_trade_cards(buy_view, "buy", trend_by_code)
            simple_buy_cols=[c for c in ["急騰予兆判定","割安判定","順位","コード","銘柄名","現在株価","参考購入株数","注文区分","通常注文参考株数","通常注文概算額","%K","%D","買付可否"] if c in buy_view.columns]
            with st.expander("買い候補の詳細表", expanded=False):
                buy_display = buy_view[simple_buy_cols].rename(columns={"割安判定": "銘柄判定"}).copy()
                st.dataframe(buy_display, use_container_width=True, hide_index=True)
        st.caption(f"新規買い：{live_universe_mode}の未保有株 / 売り：現在保有 {len(held_codes)}銘柄 / 企業価値判定：{'割高・判定不能を除外' if signal_first and block_unrated else '割高を除外・判定不能は許可' if signal_first else '参考表示'}")
        with st.expander("100株単位・参考IFDOCO", expanded=False):
            ifd_table = build_reference_ifdoco(buy_view, ifd_take_profit, 7.0)
            if ifd_table.empty:
                st.info("予算・注文余力内で100株買える正式買い候補はありません。")
            else:
                st.dataframe(ifd_table, use_container_width=True, hide_index=True)
                st.caption("買い指値は最新終値の参考値。約定しない場合があります。呼値・値幅・SBIの注文受付を確認し、実際の約定価格に応じて売り条件を見直してください。逆指値売却は成行の参考案で、価格は保証されません。")

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

    value_tab, surge_tab, compare_tab, gfs_tab, holdings_tab = st.tabs([
        "企業価値", "急騰予兆", "3指標比較", "GFSロジック研究", "保有銘柄"
    ])

    with value_tab:
        st.subheader("無料国内財務の取得結果（日足とは独立）")
        if not finance_acquisition_df.empty:
            _ok = int(finance_acquisition_df["財務取得状態"].eq("取得済").sum())
            st.caption(f"財務取得済 {_ok} / {len(finance_acquisition_df)}銘柄。取得済でも日足不足・割安条件未達なら買い候補にはなりません。")
            st.dataframe(finance_acquisition_df, use_container_width=True, hide_index=True)
        else:
            st.info("『今日の判定を更新』で財務を直接取得します。空欄は財務取得失敗を意味しません。")
        with st.expander("日足取得の診断（失敗銘柄を含む）", expanded=False):
            if not daily_acquisition_df.empty:
                st.dataframe(daily_acquisition_df, use_container_width=True, hide_index=True)
            else:
                st.info("判定更新後に表示します。")
        render_allocation_research()
        render_public_research_logic()
        if isinstance(signal_value_df, pd.DataFrame) and not signal_value_df.empty:
            missing_finance = signal_value_df[~signal_value_df["ファンダ取得状態"].eq("OK")]
            if not missing_finance.empty:
                st.warning(f"財務指標を取得できない銘柄：{len(missing_finance)} / {len(signal_value_df)}。日足の鮮度とは別です。判定不能除外が有効なら新規買いを見送ります。")
                with st.expander("財務取得不可の詳細", expanded=False):
                    st.dataframe(missing_finance[["コード", "銘柄名", "ファンダ取得状態", "Yahoo取得状態", "財務情報鮮度", "財務取得日時"]], use_container_width=True, hide_index=True)
        with st.expander("無料財務データの取得状況・情報源", expanded=False):
            _finance_frames = [f for f in (signal_value_df, value_top50_df) if isinstance(f, pd.DataFrame) and not f.empty]
            if _finance_frames:
                _finance_audit = pd.concat(_finance_frames, ignore_index=True).drop_duplicates("コード", keep="first")
                _audit_cols = ["コード", "銘柄名", "割安判定", "ファンダ取得状態", "財務情報源", "財務情報鮮度", "参考指標基準日", "予想EPS対象期", "業績発表日", "実績EPS対象期", "財務取得日時", "財務参照URL", "財務取得経路"]
                st.dataframe(_finance_audit[[c for c in _audit_cols if c in _finance_audit]], use_container_width=True, hide_index=True)
            else:
                st.info("判定更新後に財務の取得状況を表示します。")
        st.caption("シグナル先行では企業価値を割高除外・判定不能除外に使用します。他の監視対象設定では参考表示です。")
        st.caption("財務は無料公開のYahoo!ファイナンス日本版を優先し、取得不可・基準日未確認時は株探の無料基本情報へ切り替えます。取得サイト・指標基準日・EPS対象期・株探業績発表日・取得経路を表示します。サイトをまたいでEPSとPERを混ぜません。欠損をゼロや推測値で補わず、最新性・算定不能は新規買い対象外です。J-Quants遅延財務・有料APIは使用しません。")
        if not isinstance(value_top50_df, pd.DataFrame) or value_top50_df.empty:
            st.info("先に『今日の判定を更新』で監視対象のデータを取得してください。")
        else:
            value_show = add_research_judgements_first(value_top50_df, surge_top50_df, value_top50_df)
            show_cols=[
                "急騰予兆判定","割安判定","順位","コード","銘柄名","一次選抜順位",
                "現在株価_価格","AI参考価値","参考価値上昇余地%","企業価値スコア",
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
            st.caption("TOPIX＋日経225モードではAI上位50銘柄を新規買い監視対象に使用します。固定45・売買代金上位モードでは企業価値は参考表示です。現在の構成一覧を過去に流用した検証は、過去の選定を再現しません。")
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

    with gfs_tab:
        if st.session_state.get("v172412_history_refresh_required"):
            st.warning("旧版の取得日足が残っています。『今日の判定を更新』を押して研究用高値を再計算してください。")
        gfs_research_buy, gfs_research_sell, gfs_research_audit, gfs_research_settings = render_gfs_research(
            data, daily_bar_status_df, short_confirmed, [value_top50_df, signal_value_df], signal_audit_df
        )

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
            "買付余力（短期）":short_buying_power,"保有件数":len(held_codes),
            "日本株母集団設定":int(universe_size),"取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "詳細企業価値評価件数設定":int(fundamental_pool_size),"TOP50件数":len(true_top50_codes) if live_universe_mode == "TOPIX＋日経225からAI選定TOP50" else 0,
            "固定45監視件数":len(VERIFIED_45_CODES) if live_universe_mode == "固定45銘柄" else 0, "新規監視件数":len(true_top50_codes),
            "企業価値参考表示件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "売買ロジック":live_trade_method, "Stoch不使用の試験採用":bool(replacement_live), "新規BUY対象":live_universe_mode + "の未保有株", "判定不能除外":bool(signal_first and block_unrated), "流動性下限":int(min_turnover), "下降確認除外":bool(signal_first and filter_downtrend), "クロス多発除外":bool(signal_first and filter_churn), "購入予算方式":cap_mode,
            "最大保有銘柄数":"上限なし", "1銘柄購入予算":maxbuy, "通常注文予算":"上限なし（買付余力内）", "注文方式":execution_mode,
            "IFDOCO参考利確率":ifd_take_profit,"BUY条件":live_trade_method,"管理者比較":"RSI5 / BB20 / 急騰予兆（すべて実売買には不使用）",
            "割高の新規BUY除外":bool(signal_first),
            "SELL条件":("全保有銘柄 / 前日までの20日安値を終値で割る または終値で含み損 -7.0%" if replacement_live else "全保有銘柄 / Slow Stoch DC または終値で含み損 -7.0%"),
            "旧49銘柄固定ユニバース使用":False,"検証済み45銘柄固定ユニバース使用":live_universe_mode == "固定45銘柄",
            "短期反転フィルター":bool(churn_filter),
            "トレンド振れ幅フィルター実売買":bool(quality_mode),
        }])
        zf.writestr("ver17_settings.csv",settings_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("finance_acquisition_status.csv",finance_acquisition_df.to_csv(index=False,encoding="utf-8-sig"))
        zf.writestr("daily_acquisition_diagnostics.csv",daily_acquisition_df.to_csv(index=False,encoding="utf-8-sig"))
        for gfs_name, gfs_frame in [("gfs_research_buy.csv", gfs_research_buy),
                                    ("gfs_research_sell.csv", gfs_research_sell),
                                    ("gfs_research_audit.csv", gfs_research_audit),
                                    ("gfs_research_settings.csv", pd.DataFrame([gfs_research_settings])),
                                    ("gfs_research_sources.csv", pd.DataFrame(GFS_RESEARCH_SOURCES))]:
            zf.writestr(gfs_name, gfs_frame.to_csv(index=False, encoding="utf-8-sig"))
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
        buy_cols_default=["購入優先度","順位","コード","銘柄名","現在株価","参考S株数","概算購入額","ロジック","63日騰落率%","%K","%D","候補スコア","AI_TOP50スコア","割安判定","買付可否","参考購入株数","S株注文参考株数","注文区分","通常注文参考株数","通常注文概算額","余力引当額","見送り理由"]
        if isinstance(buy_view, pd.DataFrame) and not buy_view.empty:
            buy_export = buy_view.copy()
        else:
            buy_export = pd.DataFrame(columns=buy_cols_default)
        zf.writestr("stoch_buy_candidates.csv",buy_export.to_csv(index=False,encoding="utf-8-sig"))
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
            "本物TOP50モード":live_universe_mode == "TOPIX＋日経225からAI選定TOP50","母集団最低200銘柄ガード":False,
            "固定45銘柄モード":live_universe_mode == "固定45銘柄","固定45監視件数":len(VERIFIED_45_CODES) if live_universe_mode == "固定45銘柄" else 0, "新規監視件数":len(true_top50_codes),
            "取得母集団件数":len(universe_df) if isinstance(universe_df,pd.DataFrame) else 0,
            "日足取得成功件数":len(data) if isinstance(data,dict) else 0,
            "当日確定日足件数":int(daily_bar_status_df["状態"].eq("🟢 当日確定").sum()) if isinstance(daily_bar_status_df,pd.DataFrame) and not daily_bar_status_df.empty else 0,
            "TOP50件数":len(true_top50_codes) if live_universe_mode == "TOPIX＋日経225からAI選定TOP50" else 0,
            "企業価値参考表示件数":len(value_top50_df) if isinstance(value_top50_df,pd.DataFrame) else 0,
            "一次選抜_流動性重み":np.nan,"一次選抜_トレンド重み":np.nan,"一次選抜_安定性重み":np.nan,
            "OOS検証済み":False,"OOS_PF参考":np.nan,"5年通算PF参考":np.nan,
            "BUY候補件数":len(buy_export),"割高除外件数":len(overvalued_buy_exclusions_df),
            "企業価値未算定件数":value_unrated_count,
            "財務直接取得対象件数":len(finance_acquisition_df),
            "財務直接取得済件数":int(finance_acquisition_df["財務取得状態"].eq("取得済").sum()) if not finance_acquisition_df.empty else 0,
            "財務直接取得不可件数":int(finance_acquisition_df["財務取得状態"].eq("取得不可").sum()) if not finance_acquisition_df.empty else 0,
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
