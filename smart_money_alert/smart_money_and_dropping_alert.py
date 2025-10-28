"""
Smart Money + Dropping Odds (1X2) Alert – Streamlit App

สิ่งที่สคริปต์นี้ทำ
- ดึงข้อมูลจาก 2 หน้า:
  1) Moneyway 1X2 (เปอร์เซ็นต์เงินเทไปที่ผล 1/X/2)
  2) Dropping Odds 1X2 (ราคาดรอปจากราคาเปิด)
- แมตช์คู่แข่งขันให้ตรงกัน (home/away และวันที่ ตามเวลา +07:00)
- เกณฑ์เริ่มต้น:
  * SMART_MONEY_THRESHOLD = 90.0  (เงินเทไปฝั่งใดฝั่งหนึ่ง ≥ 90%)
  * DROPPING_THRESHOLD   = 7.0    (ราคาฝั่งเดียวกันดรอป ≥ 7%)
- เงื่อนไขแจ้งเตือน: ต้องเป็น “คู่เดียวกัน” และ “ผลเดียวกัน (1/X/2) ตรงกันทั้งสองหน้า”
- แสดงผลพร้อมไฮไลต์สีแดง + toast และส่ง Telegram (ถ้าตั้ง ENV)
"""

import os
import re
from datetime import datetime

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
    ),
    # เพิ่มเติมบางเว็บจะชอบ header เพิ่ม
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://arbworld.net/",
}

MONEYWAY_URL = "https://arbworld.net/en/moneyway/football-1-x-2"
DROPPING_URL = "https://arbworld.net/en/dropping-odds/football-1-x-2"


# -------------------- Helpers --------------------
def _num(s):
    """แปลงข้อความเป็นตัวเลข float (รองรับ £ , % , ช่องว่าง)"""
    if s is None:
        return None
    s = str(s).replace("£", "").replace(",", "").strip()
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s else None
    except Exception:
        return None


def _norm_team(x: str) -> str:
    """normalize ชื่อทีมเพื่อใช้จับคู่"""
    if not isinstance(x, str):
        return ""
    x = x.strip().lower()
    x = re.sub(r"\s+fc$", "", x)
    x = re.sub(r"\s+cf$", "", x)
    x = re.sub(r"\s{2,}", " ", x)
    return x


def _extract_date(s: str):
    """
    พยายามแปลงวันที่จากข้อความ (เช่น '28 Oct') เป็น 'YYYY-MM-DD'
    ถ้าแปลงไม่ได้ ส่งกลับค่าดั้งเดิม
    """
    try:
        now = datetime.now()
        s_full = f"{s} {now.year}"
        dt = pd.to_datetime(s_full, errors="coerce", dayfirst=True)
        if pd.isna(dt):
            return s
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


def _match_key(row):
    """คีย์สำหรับจับคู่แมตช์: (วันที่, home, away) หลัง normalize"""
    return (
        _extract_date(str(row.get("date", ""))),
        _norm_team(str(row.get("home", ""))),
        _norm_team(str(row.get("away", ""))),
    )


def _get_html(url, params):
    """ดึง HTML พร้อม header; โยน exception หาก status ไม่ 200"""
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


# -------------------- Scrapers --------------------
def fetch_moneyway(timezone=DEFAULT_TIMEZONE, day=DEFAULT_DAY):
    params = {
        "hidden": "", "shown": "", "timeZone": timezone, "day": day,
        "refreshInterval": str(REFRESH_SEC_DEFAULT),
        "order": "Percentage on sign", "min": "0", "max": "100",
    }
    html = _get_html(MONEYWAY_URL, params)
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return pd.DataFrame()

    rows = []
    pct_re = re.compile(r"(\d+(?:\.\d+)?)\s*%")  # จับตัวเลขเปอร์เซ็นต์จาก cell

    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue

        # ดึงข้อความแบบ strip และเก็บสำรองแบบ raw ด้วย
        txt = [td.get_text(" ", strip=True) for td in tds]

        # โครงหลักคาดหวัง: 0:league,1:date,2:time,3:home,4:1,5:x,6:2,7:%1,8:%x,9:%2, -2:away, -1:volume
        # ถ้ามีคอลัมน์รูป/ธงแทรก กลางๆ จะทำให้เลื่อน index; เราจะหาค่า % จากช่วงกลางด้วย regex แทน
        league = txt[0]
        date = txt[1]
        time_ = txt[2]
        home = txt[3]

        # ราคาหลัก 1/X/2
        def fnum(i):
            try:
                return _num(txt[i]) if i < len(txt) else None
            except Exception:
                return None

        odds1 = fnum(4)
        oddsx = fnum(5)
        odds2 = fnum(6)

        # หาเปอร์เซ็นต์จากช่วงกลางของแถว (กันคอลัมน์เลื่อน)
        mid = " ".join(txt[4:11])  # ครอบคลุม 1,X,2 และเปอร์เซ็นต์
        pcts = pct_re.findall(mid)
        pct1 = _num(pcts[0]) if len(pcts) >= 1 else None
        pctx = _num(pcts[1]) if len(pcts) >= 2 else None
        pct2 = _num(pcts[2]) if len(pcts) >= 3 else None

        away = txt[-2] if len(txt) >= 2 else None
        volume = _num(txt[-1]) if len(txt) >= 1 else None

        # เลือก smart_sign เฉพาะเมื่อมีอย่างน้อยหนึ่งเปอร์เซ็นต์
        pct_map = {"1": pct1, "X": pctx, "2": pct2}
        have_any = any(v is not None for v in pct_map.values())
        if have_any:
            smart_sign = max(pct_map, key=lambda k: pct_map[k] if pct_map[k] is not None else -1)
            smart_pct = pct_map[smart_sign]
        else:
            smart_sign, smart_pct = None, None

        row = {
            "league": league, "date": date, "time": time_, "home": home, "away": away,
            "odds1": odds1, "oddsx": oddsx, "odds2": odds2,
            "pct1": pct1, "pctx": pctx, "pct2": pct2,
            "smart_sign": smart_sign, "smart_pct": smart_pct,
            "volume": volume,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["match_key"] = df.apply(_match_key, axis=1)
    return df



def fetch_dropping(timezone=DEFAULT_TIMEZONE, day=DEFAULT_DAY):
    params = {
        "hidden": "", "shown": "", "timeZone": timezone,
        "refreshInterval": str(REFRESH_SEC_DEFAULT),
        "order": "Drop", "min": "0", "max": "100", "day": day,
    }
    html = _get_html(DROPPING_URL, params)
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return pd.DataFrame()

    rows = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        txt = [td.get_text(" ", strip=True) for td in tds]

        league = txt[0]
        date = txt[1]
        time_ = txt[2] if len(txt) > 2 else None
        home = txt[3] if len(txt) > 3 else None

        def fnum(i):
            try:
                return _num(txt[i]) if i < len(txt) else None
            except Exception:
                return None

        odds1_open, odds1_now = fnum(4), fnum(5)
        oddsx_open, oddsx_now = fnum(6), fnum(7)
        odds2_open, odds2_now = fnum(8), fnum(9)

        away = txt[-2] if len(txt) >= 2 else None
        volume = _num(txt[-1]) if len(txt) >= 1 else None

        def drop_pct(o, n):
            if o is None or n is None or o == 0:
                return None
            return (o - n) / o * 100.0

        drop1 = drop_pct(odds1_open, odds1_now)
        dropx = drop_pct(oddsx_open, oddsx_now)
        drop2 = drop_pct(odds2_open, odds2_now)

        dm = {"1": drop1, "X": dropx, "2": drop2}
        have_any = any(v is not None for v in dm.values())
        if have_any:
            drop_sign = max(dm, key=lambda k: dm[k] if dm[k] is not None else -1)
            drop_pct_val = dm[drop_sign]
        else:
            drop_sign, drop_pct_val = None, None

        rows.append({
            "league": league, "date": date, "time": time_, "home": home, "away": away,
            "odds1_open": odds1_open, "odds1_now": odds1_now,
            "oddsx_open": oddsx_open, "oddsx_now": oddsx_now,
            "odds2_open": odds2_open, "odds2_now": odds2_now,
            "drop1": drop1, "dropx": dropx, "drop2": drop2,
            "drop_sign": drop_sign, "drop_pct": drop_pct_val,
            "volume": volume,
        })

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
        day_choice = st.selectbox("Day", ["Today", "Tomorrow", "All"], index=["Today", "Tomorrow", "All"].index(DEFAULT_DAY))
        refresh_sec = st.number_input("Auto refresh (sec)", min_value=10, max_value=300, value=REFRESH_SEC_DEFAULT, step=10)
        smart_th = st.number_input("Smart money ≥ %", min_value=50.0, max_value=100.0, value=SMART_MONEY_THRESHOLD, step=1.0)
        drop_th = st.number_input("Drop ≥ %", min_value=1.0, max_value=50.0, value=DROPPING_THRESHOLD, step=0.5)
        st.caption("Optional Telegram via env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Fetching Moneyway…")
        mw = fetch_moneyway(timezone=tz_choice, day=day_choice)
        st.caption(f"rows: {len(mw)}")
        st.dataframe(mw, use_container_width=True)

    with col2:
        st.subheader("Fetching Dropping odds…")
        dr = fetch_dropping(timezone=tz_choice, day=day_choice)
        st.caption(f"rows: {len(dr)}")
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
        # ไฮไลต์สีแดง
        def highlight_red(_row):
            return ["background-color: #ffdddd"] * len(_row)
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
