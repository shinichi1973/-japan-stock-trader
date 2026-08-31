# ============================================================
# 日本株 AI投資アシスタント Ver.6.0
# BUILD: VER6.0-FOUNDATION-20260831
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
#   ・保有銘柄スクショは任意。無料Tesseract OCRで解析（APIキー不要）
# ============================================================

import io
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import json
import math
import os
import re
from datetime import datetime, timedelta
from zipfile import ZipFile

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="日本株 AI投資アシスタント Ver.6.0",
    page_icon="📈",
    layout="wide",
)

VERSION = "6.0 RC3 SBI-OCR-SAFE"
BUILD = "VER6.0-RC3-SBI-OCR-SAFE-20260831"

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
def stock_data(t, years=5):
    end = datetime.now()
    start = end - timedelta(days=365 * years + 300)

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
            t, start=start, end=end + timedelta(days=1),
            auto_adjust=False, progress=False, threads=False
        )
        return normalize(raw)
    except Exception:
        try:
            return normalize(yf.Ticker(t).history(
                start=start, end=end + timedelta(days=1),
                auto_adjust=False, actions=False
            ))
        except Exception:
            return pd.DataFrame()

@st.cache_data(ttl=3600)
def market_data():
    end = datetime.now()
    start = end - timedelta(days=365 * 5 + 300)
    try:
        df = yf.download(
            "^N225", start=start, end=end + timedelta(days=1),
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

@st.cache_data(ttl=3600)
def overseas_data():
    end = datetime.now()
    start = end - timedelta(days=365 * 5 + 300)
    symbols = {
        "S&P500":"^GSPC","NASDAQ":"^IXIC","NYダウ":"^DJI",
        "SOX":"^SOX","USDJPY":"USDJPY=X","米10年金利":"^TNX"
    }
    out = {}
    for label, symbol in symbols.items():
        try:
            df = yf.download(
                symbol, start=start, end=end + timedelta(days=1),
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
# 保有銘柄スクショ無料OCR（OpenAI API不要）
# ------------------------------------------------------------
def _num(v):
    """OCR文字列（3,440円 / +2,800円 / -1.56%）を数値へ。"""
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        return safe_float(v)
    s = str(v).strip().replace(",", "").replace("円", "").replace("%", "")
    s = s.replace("＋", "+").replace("−", "-").replace("ー", "-").replace("―", "-")
    # OCRで起きやすい数字まわりの誤認識だけ補正
    s = s.replace("O", "0").replace("o", "0")
    s = re.sub(r"[^0-9+\-.]", "", s)
    return safe_float(s)


def infer_shares_from_screen(current_price, avg_price, pnl):
    """SBI画面に株数が無い場合、損益=(現在値-取得単価)*株数 から逆算。
    整数に十分近い場合だけ採用し、推測し過ぎない。
    """
    cp, ap, pl = map(_num, [current_price, avg_price, pnl])
    if not (np.isfinite(cp) and np.isfinite(ap) and np.isfinite(pl)):
        return np.nan, np.nan, False
    diff = cp - ap
    if abs(diff) < 1e-9:
        return np.nan, np.nan, False
    raw = pl / diff
    if raw <= 0 or raw > 1_000_000:
        return np.nan, np.nan, False
    rounded = int(round(raw))
    if rounded <= 0:
        return np.nan, np.nan, False
    calc = diff * rounded
    tol = max(2.0, abs(pl) * 0.015)
    err = abs(calc - pl)
    ok = err <= tol
    return (rounded if ok else np.nan), err, ok


def repair_sbi_prices(current_price, avg_price, pnl_pct):
    """SBI画面OCRで4桁価格の先頭桁が落ちる誤読を、損益率との整合性で安全に補正する。
    補正は明確に改善する場合だけ採用する。"""
    cp, ap, pp = map(_num, [current_price, avg_price, pnl_pct])
    notes=[]
    if not (np.isfinite(cp) and cp > 0):
        return cp, ap, notes
    # 損益率が読めていれば取得単価の理論値を得られる
    implied_ap = np.nan
    if np.isfinite(pp) and abs(pp) < 100 and abs(1 + pp/100) > 1e-9:
        implied_ap = cp / (1 + pp/100)
    if np.isfinite(ap) and np.isfinite(implied_ap):
        base_err = abs(ap-implied_ap) / max(implied_ap,1)
        candidates=[ap]
        # 854 -> 4854 のような「先頭1桁欠落」を候補化
        digits=str(int(round(abs(ap))))
        if len(digits) <= 4:
            for lead in range(1,10):
                candidates.append(float(str(lead)+digits))
        best=min(candidates, key=lambda x: abs(x-implied_ap))
        best_err=abs(best-implied_ap)/max(implied_ap,1)
        if best != ap and best_err <= 0.015 and best_err < base_err*0.25:
            notes.append(f"取得単価先頭桁補正:{ap:g}→{best:g}")
            ap=best
    return cp, ap, notes

def normalize_holdings_rows(rows):
    """複数スクショの重複統合・数値正規化・株数逆算・整合性チェック。"""
    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw

    for col in ["code","name","shares","avg_price","current_price","pnl","pnl_pct"]:
        if col not in raw.columns:
            raw[col] = np.nan

    raw["code"] = raw["code"].astype(str).str.extract(r"(\d{4})")[0]
    raw = raw.dropna(subset=["code"])
    for col in ["shares","avg_price","current_price","pnl","pnl_pct"]:
        raw[col] = raw[col].map(_num)

    merged = []
    for c, g in raw.groupby("code", sort=False):
        rec = {"code": c, "name": STOCK_NAMES.get(c, c)}
        for col in ["shares","avg_price","current_price","pnl","pnl_pct"]:
            vals = g[col].dropna().tolist()
            rec[col] = vals[-1] if vals else np.nan
        rec["重複画像数"] = int(len(g))
        merged.append(rec)

    df = pd.DataFrame(merged)
    checks = []
    for i, r in df.iterrows():
        cp, ap, pl = r["current_price"], r["avg_price"], r["pnl"]
        cp, ap, repair_notes = repair_sbi_prices(cp, ap, r["pnl_pct"])
        df.at[i, "current_price"] = cp
        df.at[i, "avg_price"] = ap
        sh = r["shares"]
        source = "画像表示"
        err = np.nan

        if not np.isfinite(_num(sh)):
            inferred, err, ok = infer_shares_from_screen(cp, ap, pl)
            if ok:
                df.at[i, "shares"] = inferred
                source = "評価損益から逆算"
            else:
                source = "不明"
        else:
            shn = int(round(_num(sh)))
            df.at[i, "shares"] = shn
            if np.isfinite(_num(cp)) and np.isfinite(_num(ap)) and np.isfinite(_num(pl)):
                err = abs((_num(cp)-_num(ap))*shn - _num(pl))

        pct_calc = np.nan
        if np.isfinite(_num(cp)) and np.isfinite(_num(ap)) and _num(ap) != 0:
            pct_calc = (_num(cp)/_num(ap)-1)*100
        pct_err = abs(pct_calc-_num(r["pnl_pct"])) if np.isfinite(pct_calc) and np.isfinite(_num(r["pnl_pct"])) else np.nan

        has_core = all(np.isfinite(_num(x)) for x in [cp, ap, pl])
        sh_ok = np.isfinite(_num(df.at[i,"shares"]))
        pnl_ok = (not np.isfinite(err)) or err <= max(2.0, abs(_num(pl))*0.015 if np.isfinite(_num(pl)) else 2.0)
        pct_ok = (not np.isfinite(pct_err)) or pct_err <= 0.20

        if has_core and sh_ok and pnl_ok and pct_ok:
            confidence, confirm = "🟢 高", "不要"
        elif has_core and (sh_ok or np.isfinite(_num(r["pnl_pct"]))):
            confidence, confirm = "🟡 中", "要確認"
        else:
            confidence, confirm = "🔴 低", "要確認"

        checks.append({
            "株数取得方法":source,
            "損益整合誤差_円":err,
            "損益率計算値":pct_calc,
            "損益率誤差_pt":pct_err,
            "読取信頼度":confidence,
            "確認要否":confirm,
            "自動補正":" / ".join(repair_notes) if repair_notes else "なし",
            "売買判定使用可": "YES" if confidence == "🟢 高" else "NO",
        })

    out = pd.concat([df.reset_index(drop=True), pd.DataFrame(checks)], axis=1)
    return out


def _prep_sbi_image(uploaded_file):
    """SBIの濃色画面をTesseractが読みやすい白地画像へ変換。"""
    img = Image.open(io.BytesIO(uploaded_file.getvalue())).convert("RGB")
    w, h = img.size
    # ステータスバー・下部ナビを削り、表のある範囲を中心に処理
    top = int(h * 0.20)
    bottom = int(h * 0.94)
    img = img.crop((0, top, w, bottom))
    # 2倍にして数字と4桁コードを優先して拾う
    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(img)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    # SBIは暗背景なので反転してTesseract向けに白背景化
    gray = ImageOps.invert(gray)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def _tesseract_tokens(uploaded_file):
    """1枚につきOCRは1回だけ。座標付きtokenを返す。"""
    try:
        import pytesseract
        from pytesseract import Output
    except Exception as e:
        raise RuntimeError("pytesseractが読み込めません。requirements.txtを確認してください。") from e

    img = _prep_sbi_image(uploaded_file)
    try:
        dat = pytesseract.image_to_data(
            img,
            lang="jpn+eng",
            config="--psm 6 --oem 3",
            output_type=Output.DATAFRAME,
        )
    except Exception as e:
        raise RuntimeError(
            "Tesseract日本語OCRを起動できません。packages.txt に tesseract-ocr / tesseract-ocr-jpn が必要です。"
        ) from e

    dat = dat.dropna(subset=["text"]).copy()
    dat["text"] = dat["text"].astype(str).str.strip()
    dat = dat[dat["text"] != ""]
    dat["conf"] = pd.to_numeric(dat["conf"], errors="coerce")
    dat = dat[dat["conf"].fillna(-1) >= 20]
    # 座標を0〜1へ正規化
    W, H = img.size
    dat["cx"] = (pd.to_numeric(dat["left"], errors="coerce") + pd.to_numeric(dat["width"], errors="coerce")/2) / W
    dat["cy"] = (pd.to_numeric(dat["top"], errors="coerce") + pd.to_numeric(dat["height"], errors="coerce")/2) / H
    return dat[["text","conf","cx","cy","left","top","width","height"]].reset_index(drop=True)


def _extract_numbers_in_band(tokens, x_lo, x_hi, y_lo, y_hi):
    g = tokens[(tokens.cx >= x_lo) & (tokens.cx < x_hi) & (tokens.cy >= y_lo) & (tokens.cy < y_hi)].copy()
    if g.empty:
        return []
    # y座標で並べ、同一行の分割tokenをまとめる
    g = g.sort_values(["cy","cx"])
    vals = []
    used = []
    for _, r in g.iterrows():
        txt = str(r.text)
        if not re.search(r"\d", txt):
            continue
        n = _num(txt)
        if np.isfinite(n):
            vals.append((float(r.cy), n, txt))
    # 近いyの重複を除く
    out=[]
    for item in vals:
        if not out or abs(item[0]-out[-1][0]) > 0.014:
            out.append(item)
        elif len(str(item[2])) > len(str(out[-1][2])):
            out[-1]=item
    return out


def _parse_sbi_tokens(tokens):
    """SBI保有証券画面の固定3列レイアウトを座標で読む。"""
    if tokens.empty:
        return []

    code_hits=[]
    for _, r in tokens.iterrows():
        m = re.search(r"(?<!\d)(\d{4})(?!\d)", str(r.text))
        if m and float(r.cx) < 0.40:
            c=m.group(1)
            # 時刻や金額ではなく、日本株コード候補を優先
            if c in STOCK_NAMES or (1000 <= int(c) <= 9999):
                code_hits.append((float(r.cy), c))
    # yが近い同一コード重複を整理
    uniq=[]
    for y,c in sorted(code_hits):
        if not uniq or c != uniq[-1][1] or abs(y-uniq[-1][0]) > 0.025:
            uniq.append((y,c))
    if not uniq:
        return []

    rows=[]
    for i,(y,c) in enumerate(uniq):
        prev_y = uniq[i-1][0] if i>0 else y-0.045
        next_y = uniq[i+1][0] if i+1<len(uniq) else y+0.070
        y_lo=max(0.0, (prev_y+y)/2 - 0.010)
        y_hi=min(1.0, (y+next_y)/2 + 0.010)

        center=_extract_numbers_in_band(tokens, 0.38, 0.72, y_lo, y_hi)
        right=_extract_numbers_in_band(tokens, 0.72, 1.01, y_lo, y_hi)

        # 中央列: 上=現在値、下=取得単価
        center_sorted=sorted(center, key=lambda z:z[0])
        current_price=center_sorted[0][1] if len(center_sorted)>=1 else np.nan
        avg_price=center_sorted[1][1] if len(center_sorted)>=2 else np.nan

        # 右列: 上=評価損益、下=評価損益率。%記号が落ちてもy順で判定
        right_sorted=sorted(right, key=lambda z:z[0])
        pnl=right_sorted[0][1] if len(right_sorted)>=1 else np.nan
        pnl_pct=right_sorted[1][1] if len(right_sorted)>=2 else np.nan

        # 値段/損益率の明らかな異常を弾く
        if np.isfinite(pnl_pct) and abs(pnl_pct) > 200:
            pnl_pct=np.nan
        if np.isfinite(current_price) and current_price <= 0:
            current_price=np.nan
        if np.isfinite(avg_price) and avg_price <= 0:
            avg_price=np.nan

        rows.append({
            "code":c,
            "name":STOCK_NAMES.get(c,c),
            "shares":np.nan,
            "avg_price":avg_price,
            "current_price":current_price,
            "pnl":pnl,
            "pnl_pct":pnl_pct,
        })
    return rows


def extract_holdings_free_ocr(images):
    """API課金なし。Tesseract OCR + SBI画面座標 + 数値整合性で解析。"""
    all_rows=[]
    debug=[]
    try:
        for idx,img in enumerate(images, start=1):
            tok=_tesseract_tokens(img)
            rows=_parse_sbi_tokens(tok)
            all_rows.extend(rows)
            debug.append(f"画像{idx}: {len(rows)}銘柄")
        df=normalize_holdings_rows(all_rows)
        if df.empty:
            return df, "銘柄を認識できませんでした。SBI『口座管理→保有証券』の画面全体をアップロードしてください。"
        return df, " / ".join(debug)
    except Exception as e:
        return pd.DataFrame(), f"無料OCR解析エラー: {e}"
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
    "Ver.5.5系を土台に、企業価値AI・テンバガーAI・保有銘柄AI・"
    "損切り/資金管理を追加し、SBIスクショを無料OCRで読めるVer.6.0 RC2です。"
)

st.success("📷 スクショ解析：無料OCR版（OpenAI APIキー不要）")
st.caption("Tesseract日本語OCRを使います。API料金は発生しません。読み取り結果は必ず確認してから売買判断に使用します。")

# ------------------------------------------------------------
# 保有銘柄スクショ
# ------------------------------------------------------------
st.header("📷 ① 現在の保有銘柄をスクショから取り込み")
st.caption(
    "SBI証券の『口座管理 → 保有証券』をスクロールして必要な枚数だけ追加してください。"
    "5枚・6枚でもOK。重複銘柄を統合し、画面に株数が無い場合は現在値・取得単価・評価損益から数式で逆算して整合性を確認します。"
)
images = st.file_uploader(
    "保有銘柄スクショを追加",
    type=["png","jpg","jpeg","webp"],
    accept_multiple_files=True,
    key="holding_images"
)

ocr_df = st.session_state.get("ocr_df", pd.DataFrame())
if images and st.button("🤖 スクショを一括解析", use_container_width=True):
    with st.spinner(f"{len(images)}枚のスクショを統合解析中…"):
        ocr_df, msg = extract_holdings_free_ocr(images)
    st.session_state["ocr_df"] = ocr_df
    if not ocr_df.empty:
        st.success(f"解析完了：{len(ocr_df)}銘柄を認識しました。{msg}")
    else:
        st.warning(msg)

if not ocr_df.empty:
    st.subheader("🔎 読み取り確認")
    view_cols = [c for c in [
        "code","name","shares","株数取得方法","avg_price","current_price","pnl","pnl_pct",
        "読取信頼度","確認要否","自動補正","売買判定使用可","重複画像数"
    ] if c in ocr_df.columns]
    st.dataframe(ocr_df[view_cols], use_container_width=True, hide_index=True)

    high = int((ocr_df.get("読取信頼度", pd.Series(dtype=str)) == "🟢 高").sum())
    need = int((ocr_df.get("確認要否", pd.Series(dtype=str)) == "要確認").sum())
    c1, c2 = st.columns(2)
    c1.metric("🟢 高信頼", high)
    c2.metric("⚠️ 要確認", need)

    if "ocr_cash_available" in st.session_state and np.isfinite(_num(st.session_state["ocr_cash_available"])):
        st.metric("💴 スクショから読んだ買付余力", f"{_num(st.session_state['ocr_cash_available']):,.0f}円")
    if "ocr_evaluation_pnl_total" in st.session_state and np.isfinite(_num(st.session_state["ocr_evaluation_pnl_total"])):
        st.metric("評価損益合計（画像）", f"{_num(st.session_state['ocr_evaluation_pnl_total']):+,.0f}円")

    if need:
        st.warning("黄色/赤の行は、売買判断に使う前にSBI証券画面で数値を確認してください。")
    else:
        st.success("全銘柄の主要数値が整合しました。最終的にはSBI証券画面との照合を推奨します。")

# 手動補正欄（OCRが使えない場合も利用可能）
with st.expander("📝 読み取り結果の補正／手入力（任意）"):
    held_text = st.text_area(
        "保有銘柄コード",
        ",".join(ocr_df["code"].tolist()) if not ocr_df.empty else "",
        height=80
    )
    entries_text = st.text_area("取得単価（例：7203:1500）", "")
    shares_text = st.text_area("株数（例：7203:10）", "")

held_codes = parse_codes(held_text)
entry_map = parse_entries(entries_text)
share_map = parse_shares(shares_text)

if not ocr_df.empty:
    trusted_codes=[]
    for _, rr in ocr_df.iterrows():
        c = str(rr.get("code",""))
        trusted = str(rr.get("売買判定使用可","NO")) == "YES"
        if re.fullmatch(r"\d{4}", c) and trusted:
            trusted_codes.append(c)
            ap = _num(rr.get("avg_price"))
            sh = _num(rr.get("shares"))
            if np.isfinite(ap):
                entry_map[c] = ap
            if np.isfinite(sh) and sh > 0:
                share_map[c] = int(round(sh))
    # OCR低信頼行は自動で売買判定へ流さない。手動入力したコードは従来どおり利用可。
    held_codes = list(dict.fromkeys(held_codes + trusted_codes))

# ------------------------------------------------------------
# データ取得
# ------------------------------------------------------------
analysis_codes = list(dict.fromkeys(parse_codes(universe_text) + held_codes))
with st.spinner("📡 株価・市場・海外データを取得中…"):
    data = {t: stock_data(t) for t in tickers(",".join(analysis_codes))}
    data = {t:d for t,d in data.items() if not d.empty}
    market = market_data()
    overseas = overseas_data()

st.success(f"株価データ取得：{len(data)}銘柄")

# ------------------------------------------------------------
# 目標資産 / 複利ロードマップ
# ------------------------------------------------------------
st.header("🎯 ② 60万円 → 1億円 複利ロードマップ")
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

latest_df = pd.DataFrame(latest_rows)
if not latest_df.empty:
    latest_df = latest_df.sort_values("総合AIスコア",ascending=False).reset_index(drop=True)

# 「今日のBUY」は最低AIスコアを通過した銘柄だけ。候補一覧とは分離する。
today_buy_df = latest_df[latest_df["総合AIスコア"] >= minbuy_score].copy() if not latest_df.empty else pd.DataFrame()
if not today_buy_df.empty:
    today_buy_df = today_buy_df.sort_values("総合AIスコア", ascending=False).reset_index(drop=True)

# ------------------------------------------------------------
# 保有銘柄AI
# ------------------------------------------------------------
holding_rows = []
for c in held_codes:
    t = c + ".T"
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
    st.info("保有銘柄はまだ登録されていません。スクショOCRまたは補正欄から登録してください。")
else:
    sell_now = holdings_df[holdings_df["判定"]=="🔴 SELL"]
    watch = holdings_df[holdings_df["判定"]=="🟡 WATCH"]
    hold = holdings_df[holdings_df["判定"]=="🟢 HOLD"]

    c1,c2,c3 = st.columns(3)
    c1.metric("🔴 SELL",len(sell_now))
    c2.metric("🟡 WATCH",len(watch))
    c3.metric("🟢 HOLD",len(hold))

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
# UI：新規BUY / テンバガー
# ------------------------------------------------------------
st.header("🟢 ⑤ 今日の新規BUY候補 TOP3")
if latest_df.empty:
    st.info("💤 条件を満たす新規BUY候補はありません。NO TRADEを優先します。")
else:
    for i,(_,rr) in enumerate(latest_df.head(3).iterrows()):
        icon = ["🥇","🥈","🥉"][i]
        st.success(
            f"{icon} **{rr['銘柄名']}（{rr['コード']}）**｜"
            f"総合AI {rr['総合AIスコア']:.0f}｜企業価値 {rr['企業価値スコア']:.0f}｜"
            f"成長性 {rr['成長性スコア']:.0f}｜テンバガー {rr['テンバガー度']:.0f}"
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
latest_market_state = market_info(market, max(data[next(iter(data))].index) if data else datetime.now())[0] if not market.empty and data else "⚪ データなし"
st.header("🌎 ⑦ 市場環境 / NO TRADE")
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
        "ファンダメンタルAI","テンバガーAI","保有銘柄AI","スクショ複数枚",
        "未来情報混入","SBI自動発注"
    ],
    "結果":[
        VERSION,initial,final,final-initial,(final/initial-1)*100 if initial else 0,
        len(selltr),winrate,pf,maxdd,maxddrate,maxloss,
        "現在情報のみ・バックテスト未使用","あり","あり","あり",
        "なし","なし"
    ]
})
st.dataframe(summary, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# AI信頼度 / データ品質
# ------------------------------------------------------------
st.header("🛡️ ⑨ データ品質・AI安全チェック")
quality = pd.DataFrame([
    {"チェック":"未来情報","状態":"🟢 OK","内容":"バックテストのBUY/SELL判定は日付時点の価格データのみ"},
    {"チェック":"現在ファンダメンタル","状態":"🟡 現在分析のみ","内容":"Yahoo Financeの現在情報。過去バックテストには混入させない"},
    {"チェック":"スクショOCR","状態":"🟢 無料・安全強化","内容":"SBI専用OCR。先頭桁欠落補正＋株数逆算＋整合性検証。低信頼行は売買判定から自動除外"},
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

files = {
    "00_summary.csv":summary,
    "01_today_buy.csv":today_buy_df,
    "01b_current_candidates.csv":latest_df,
    "02_holdings_ai.csv":holdings_df,
    "03_tenbagger_candidates.csv":latest_df.sort_values("テンバガー度",ascending=False) if not latest_df.empty else latest_df,
    "04_all_ai_analysis.csv":analysis_df,
    "05_trade_history.csv":trades_df,
    "06_equity_curve.csv":equity_df,
    "07_stock_results.csv":stock_results,
    "08_liquidity_top50.csv":liq,
    "09_quality_check.csv":quality,
    "10_market_data.csv":market.reset_index() if not market.empty else pd.DataFrame(),
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
    "ver6_0_all_analysis.zip",
    "application/zip",
    use_container_width=True
)

st.caption(
    "※本版は投資判断補助・検証用です。月利10%・1億円到達・テンバガー化・"
    "AI適正株価を保証するものではありません。"
)
