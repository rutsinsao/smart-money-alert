"""
Smart Money + Dropping Odds (1X2) Alert – Streamlit App

สิ่งที่สคริปต์นี้ทำ
- ดึงข้อมูลจาก 2 หน้า:
  1) Moneyway 1X2 (เปอร์เซ็นต์เงินเทไปที่ผล 1/X/2)
  2) Dropping Odds 1X2 (ราคาดรอปจากราคาเปิด)
- แมตช์คู่แข่งขันให้ตรงกัน (home/away และวันที่ ตามเวลา +07:00)
- ตั้งเกณฑ์:
  * SMART_MONEY_THRESHOLD = 90.0  (เช่น เงินเทไปฝั่งใดฝั่งหนึ่ง > 90%)
  * DROPPING_THRESHOLD = 7.0      (เช่น ราคาในฝั่งเดียวกันดรอป ≥ 7%)
- เงื่อนไขการแจ้งเตือน: ต้องเป็น “คู่เดียวกัน” และ “ผลเดียวกัน (1/X/2) ตรงกันทั้งสองหน้า”
- แสดงบนหน้า Streamlit พร้อมไฮไลต์ ‘สีแดง’ และแจ้งเตือนทันทีด้วย st.toast
- มีตัวเลือกส่งเตือนไป Telegram (ถ้าตั้งค่า ENV: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

การติดตั้ง
    pip install streamlit pandas requests beautifulsoup4 lxml python-dateutil python-dotenv

การรัน
    streamlit run smart_money_and_dropping_alert.py

หมายเหตุ
- โครงสร้างหน้าเว็บอาจเปลี่ยนได้ ควรตรวจแก้ mapping ถ้าพบว่า index คอลัมน์ไม่ตรง
- ใช้เวลา +07:00 (Bangkok) และ day=Today เป็นค่าเริ่มต้น ปรับได้จาก sidebar
- โปรดเคารพ ToS/robots ของเว็บและจำกัดความถี่ในการดึงข้อมูล
"""

import os
import re
from datetime import datetime
from dateutil import tz

import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
from dotenv import load_dotenv

# -------------------- Config --------------------
load_dotenv()

DEFAULT_TIMEZONE = "+07:00"  # Asia/Bangkok
DEFAULT_DAY = "Today"        # Today | Tomorrow | All
REFRESH_SEC_DEFAULT = 60

SMART_MONEY_THRESHOLD = 90.0  # %
DROPPING_THRESHOLD = 7.0      # %

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

MONEYWAY_URL = "https://arbworld.net/en/moneyway/football-1-x-2"
DROPPING_URL = "https://arbworld.net/en/dropping-odds/football-1-x-2"

# -------------------- Helpers --------------------

def _num(s: str):
    if s is None:
        return None
    s = str(s).replace("£", "").replace(",", "").strip()
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s else None
    except Exception:
        return None


def _norm_team(x: str) -> str:
    if not isinstance(x, str):
        return ""
    x = x.strip().lower()
    x = re.sub(r"\s+fc$", "", x)
    x = re.sub(r"\s+cf$", "", x)
    x = re.sub(r"\s{2,}", " ", x)
    return x


def _extract_date(s: str):
    # พยายามลดรูปวันเวลาให้เหลือ YYYY-MM-DD สำหรับการ match
    # ตัวเว็บมักแสดงเป็น "28 Oct" + เวลาท้องถิ่น; เราจะรับสตริงแล้วคืนสตริงเดิมถ้า parse ไม่ได้
    try:
        # ไม่มีปี ให้เติมปีปัจจุบัน
        now = datetime.now()
        s_full = f"{s} {now.year}"
        dt = pd.to_datetime(s_full, errors="coerce", dayfirst=True)
        if pd.isna(dt):
            return s
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


def _match_key(row):
    return (
        _extract_date(str(row.get("date", ""))),
        _norm_team(str(row.get("home", ""))),
        _norm_team(str(row.get("away", ""))),
    )

# -------------------- Scrapers --------------------

def fetch_moneyway(timezone=DEFAULT_TIMEZONE, day=DEFAULT_DAY):
    params = {
        "hidden": "",
        "shown": "",
        "timeZone": timezone,
        "day": day,
        "refreshInterval": str(REFRESH_SEC_DEFAULT),
        "order": "Percentage on sign",
        "min": "0",
        "max": "100",
    }
    r = requests.get(MONEYWAY_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    if not table:
        return pd.DataFrame()

    rows = []
    for tr in table.select("tbody tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not tds or len(tds) < 10:
            continue
        # โครงสร้างโดยรวม (อาจต่างไปบ้างในบางวัน):
        # [League, Date, Time, Home, odds1, oddsX, odds2, pct1, pctX, pct2, Away, Volume, ...]
        # ตำแหน่งอาจคลาดเคลื่อนเล็กน้อย ให้ปรับหากจำเป็น
        row = {
            "league": tds[0],
            "date": tds[1],
            "time": tds[2] if len(tds) > 2 else None,
            "home": tds[3] if len(tds) > 3 else None,
            "odds1": _num(tds[4]) if len(tds) > 4 else None,
            "oddsx": _num(tds[5]) if len(tds) > 5 else None,
            "odds2": _num(tds[6]) if len(tds) > 6 else None,
            "pct1": _num(tds[7]) if len(tds) > 7 else None,
            "pctx": _num(tds[8]) if len(tds) > 8 else None,
            "pct2": _num(tds[9]) if len(tds) > 9 else None,
            "away": tds[-2] if len(tds) >= 2 else None,
            "volume": _num(tds[-1]) if len(tds) >= 1 else None,
        }
        # หาว่าฝั่งไหน % สูงสุด
        pct_map = {"1": row["pct1"], "X": row["pctx"], "2": row["pct2"]}
        best_sign = max(pct_map, key=lambda k: pct_map[k] if pct_map[k] is not None else -1)
        row["smart_sign"] = best_sign
        row["smart_pct"] = pct_map[best_sign]
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["match_key"] = df.apply(_match_key, axis=1)
    return df


def fetch_dropping(timezone=DEFAULT_TIMEZONE, day=DEFAULT_DAY):
    params = {
        "hidden": "",
        "shown": "",
        "timeZone": timezone,
        "refreshInterval": str(REFRESH_SEC_DEFAULT),
        "order": "Drop",
        "min": "0",
        "max": "100",
        "day": day,
    }
    r = requests.get(DROPPING_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    if not table:
        return pd.DataFrame()

    rows = []
    for tr in table.select("tbody tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not tds or len(tds) < 12:
            continue
        # โครงสร้างโดยรวม (อาจต่างไปบ้างในบางวัน):
        # [League, Date, Time, Home, 1_open, 1_now, X_open, X_now, 2_open, 2_now, Away, Volume, ...]
        row = {
            "league": tds[0],
            "date": tds[1],
            "time": tds[2],
            "home": tds[3],
            "odds1_open": _num(tds[4]),
            "odds1_now": _num(tds[5]),
            "oddsx_open": _num(tds[6]),
            "oddsx_now": _num(tds[7]),
            "odds2_open": _num(tds[8]),
            "odds2_now": _num(tds[9]),
            "away": tds[-2],
            "volume": _num(tds[-1]),
        }
        # คำนวณ % ดรอปต่อฝั่ง
        def drop_pct(o, n):
            if o is None or n is None or o == 0:
                return None
            return (o - n) / o * 100.0

        row["drop1"] = drop_pct(row["odds1_open"], row["odds1_now"])
        row["dropx"] = drop_pct(row["oddsx_open"], row["oddsx_now"])
        row["drop2"] = drop_pct(row["odds2_open"], row["odds2_now"])

        # เลือกฝั่งที่ดรอปมากสุด
        drop_map = {"1": row["drop1"], "X": row["dropx"], "2": row["drop2"]}
        best_sign = max(drop_map, key=lambda k: drop_map[k] if drop_map[k] is not None else -1)
        row["drop_sign"] = best_sign
        row["drop_pct"] = drop_map[best_sign]
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["match_key"] = df.apply(_match_key, axis=1)
    return df

# -------------------- Alert & UI --------------------

def send_telegram(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception:
        pass


def build_app():
    st.set_page_config(page_title="Smart Money x Dropping (1X2) Alert", layout="wide")
    st.title("Smart Money x Dropping Odds (1X2) – Live Alert")

    with st.sidebar:
        st.header("Settings")
        tz_choice = st.text_input("Time Zone (+HH:MM)", DEFAULT_TIMEZONE)
        day_choice = st.selectbox("Day", ["Today", "Tomorrow", "All"], index=["Today","Tomorrow","All"].index(DEFAULT_DAY))
        refresh_sec = st.number_input("Auto refresh (sec)", min_value=10, max_value=300, value=REFRESH_SEC_DEFAULT, step=10)
        smart_th = st.number_input("Smart money ≥ %", min_value=50.0, max_value=100.0, value=SMART_MONEY_THRESHOLD, step=1.0)
        drop_th = st.number_input("Drop ≥ %", min_value=1.0, max_value=50.0, value=DROPPING_THRESHOLD, step=0.5)
        st.caption("Optional Telegram via env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")

    # Auto refresh (simple approach: user can manually refresh or re-run; advanced: use streamlit-autorefresh)
    # Keeping placeholders to avoid background loop in this notebook environment.

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Fetching Moneyway…")
        mw = fetch_moneyway(timezone=tz_choice, day=day_choice)
        st.dataframe(mw, use_container_width=True)
    with col2:
        st.subheader("Fetching Dropping odds…")
        dr = fetch_dropping(timezone=tz_choice, day=day_choice)
        st.dataframe(dr, use_container_width=True)

    if mw.empty or dr.empty:
        st.warning("ไม่พบข้อมูลเพียงพอจากอย่างน้อยหนึ่งหน้า")
        return

    # รวมข้อมูลตาม match_key
    merged = mw.merge(dr, on="match_key", suffixes=("_mw", "_dr"), how="inner")

    # เติมคอลัมน์ volume_dr (ถ้าไม่มีให้สร้างว่าง)
    if "volume_dr" not in merged.columns:
        merged["volume_dr"] = None

    # กรองตามเงื่อนไข: sign ต้องตรงกัน + smart ≥ th + drop ≥ th
    cond = (
        (merged["smart_sign"] == merged["drop_sign"]) &
        (merged["smart_pct"] >= smart_th) &
        (merged["drop_pct"] >= drop_th)
    )

    alerts = merged.loc[cond].copy()

    st.markdown("---")
    st.subheader("Matched & Triggered Alerts (สีแดง)")

    def _pretty(df: pd.DataFrame):
        if df.empty:
            return df
        # ทำสำเนาแสดงผลให้สะอาด
        out = pd.DataFrame({
            "League": df.get("league_mw"),
            "Date": df.get("date_mw"),
            "Time": df.get("time_mw"),
            "Home": df.get("home_mw"),
            "Away": df.get("away_mw"),
            "Sign": df.get("smart_sign"),
            "Smart %": df.get("smart_pct").round(2),
            "Drop %": df.get("drop_pct").round(2),
            "1 open→now": df.apply(lambda r: f"{r.get('odds1_open', '')}→{r.get('odds1_now', '')}", axis=1),
            "X open→now": df.apply(lambda r: f"{r.get('oddsx_open', '')}→{r.get('oddsx_now', '')}", axis=1),
            "2 open→now": df.apply(lambda r: f"{r.get('odds2_open', '')}→{r.get('odds2_now', '')}", axis=1),
            "Vol(MW)": df.get("volume_mw"),
            "Vol(DR)": df.get("volume_dr"),
        })
        return out

    pretty_alerts = _pretty(alerts)
    if not pretty_alerts.empty:
        # สไตล์สีแดง
        def highlight_red(_):
            return ["background-color: #ffdddd" for _ in _.index]
        st.dataframe(pretty_alerts.style.apply(highlight_red, axis=1), use_container_width=True)

        # Toast + Telegram
        for _, r in pretty_alerts.iterrows():
            msg = (
                f"ALERT: {r['League']} {r['Date']} {r['Time']}\n"
                f"{r['Home']} vs {r['Away']} | Sign {r['Sign']} | "
                f"Smart {r['Smart %']}% & Drop {r['Drop %']}%"
            )
            st.toast(msg, icon="🚨")
            send_telegram(msg)
    else:
        st.info("ยังไม่มีคู่ที่เข้าเงื่อนไขพร้อมกันทั้งสองหน้า")

    st.markdown("---")
    st.subheader("All Matched (ไม่กรองเกณฑ์)")
    st.dataframe(_pretty(merged), use_container_width=True)


if __name__ == "__main__":
    build_app()
