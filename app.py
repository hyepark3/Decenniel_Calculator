# app.py
import streamlit as st
import swisseph as swe
from datetime import datetime, date, timedelta
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
import pytz
import math
import pandas as pd
import re
import os
from io import BytesIO

# ==============================
# Ephemeris 경로 설정 (Streamlit Cloud 대응)
# ==============================
ephem_path = os.path.join(os.path.dirname(__file__), "ephem")
if os.path.exists(ephem_path):
    swe.set_ephe_path(ephem_path)

# ==============================
# Constants
# ==============================
LESSER_YEARS = {
    'Sun': 19, 'Moon': 25, 'Mercury': 20,
    'Venus': 8, 'Mars': 15, 'Jupiter': 12, 'Saturn': 30,
}
PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
PLANET_IDS = {
    'Sun': swe.SUN,
    'Moon': swe.MOON,
    'Mercury': swe.MERCURY,
    'Venus': swe.VENUS,
    'Mars': swe.MARS,
    'Jupiter': swe.JUPITER,
    'Saturn': swe.SATURN,
}
SIGN_NAMES = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
PLANET_KO = {
    'Sun': '태양',
    'Moon': '달',
    'Mercury': '수성',
    'Venus': '금성',
    'Mars': '화성',
    'Jupiter': '목성',
    'Saturn': '토성',
}

# ==============================
# 도시 자동 완성 & 좌표 가져오기
# ==============================
@st.cache_data(ttl=3600)
def search_cities(query: str):
    geolocator = Nominatim(user_agent="decennials_calculator")
    try:
        locations = geolocator.geocode(query, exactly_one=False, limit=5)
        if locations:
            return [(loc.address, loc.latitude, loc.longitude) for loc in locations]
    except Exception:
        pass
    return []

def get_coordinates_and_timezone(city_name: str):
    geolocator = Nominatim(user_agent="decennials_calculator")
    location = geolocator.geocode(city_name)
    if not location:
        raise ValueError(f"도시 '{city_name}'를 찾을 수 없습니다.")
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=location.latitude, lng=location.longitude)
    if not timezone_str:
        raise ValueError("타임존을 찾을 수 없습니다.")
    return location.latitude, location.longitude, timezone_str, location.address

# ==============================
# Ascensional Times (RA–AD–OA 정석 버전)
# ==============================
def get_obliquity(jd_ut: float) -> float:
    """황도 경사 ε (deg)"""
    return swe.calc_ut(jd_ut, swe.ECL_NUT)[0][0]

def oblique_ascension(lon_deg: float, lat_deg: float, eps_deg: float) -> float:
    """
    1) RA:   tan α = sinλ·cosε / cosλ  → atan2
    2) δ:    sin δ = sinε · sinλ      (β≈0 가정)
    3) AD:   sin AD = tanφ · tanδ
    4) OA:   OA = RA - AD
    """
    lam = math.radians(lon_deg)
    eps = math.radians(eps_deg)
    phi = math.radians(lat_deg)

    sin_l = math.sin(lam)
    cos_l = math.cos(lam)

    # RA
    alpha = math.atan2(sin_l * math.cos(eps), cos_l)

    # declination δ
    delta = math.asin(math.sin(eps) * sin_l)

    # Ascensional Difference
    td_tf = math.tan(phi) * math.tan(delta)
    td_tf = max(min(td_tf, 0.9999999), -0.9999999)
    AD = math.asin(td_tf)

    # Oblique Ascension
    OA = (alpha - AD)
    return (math.degrees(OA) + 360.0) % 360.0

def calculate_ascensional_times(latitude: float, longitude: float, jd_ut: float):
    """위도 latitude에서 12사인 Ascensional Time 계산 (합 ≈ 360°)"""
    eps_deg = get_obliquity(jd_ut)
    oa_list = []
    for i in range(13):  # 0, 30, ..., 360
        lam = i * 30.0
        oa = oblique_ascension(lam, latitude, eps_deg)
        oa_list.append(oa)

    asc_times = []
    for i in range(12):
        d_oa = (oa_list[i+1] - oa_list[i]) % 360.0
        asc_times.append(d_oa)

    total = sum(asc_times)
    if total <= 0:
        return [30.0] * 12
    return [a * 360.0 / total for a in asc_times]

def compute_unique_start_points(planet_positions, asc_times):
    """
    각 행성 USP = 이전 사인들 상승시간 합 + (사인 안에서의 비율 * 그 사인의 상승시간)
    planet_positions: {'Sun': lon, ...}
    """
    usp = {}
    details = {}
    for name, lon in planet_positions.items():
        lon = lon % 360.0
        sign_idx = int(lon // 30)
        deg_in_sign = lon % 30.0
        base = sum(asc_times[:sign_idx])              # 이전 사인들 합
        time_in_sign = asc_times[sign_idx] * (deg_in_sign / 30.0)
        usp[name] = base + time_in_sign
        details[name] = {
            "lon": lon,
            "sign_idx": sign_idx,
            "deg_in_sign": deg_in_sign,
            "base_time": base,
            "time_in_sign": time_in_sign,
            "usp": usp[name],
        }
    return usp, details

# ==============================
# Julian Day, 차트, 서브레벨 계산
# ==============================
def calculate_julian_day(dt, timezone_str):
    local_tz = pytz.timezone(timezone_str)
    local_dt = local_tz.localize(dt)
    utc_dt = local_dt.astimezone(pytz.UTC)
    return swe.julday(
        utc_dt.year, utc_dt.month, utc_dt.day,
        utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0
    )

def calculate_chart(birth_datetime, latitude, longitude, timezone_str):
    jd = calculate_julian_day(birth_datetime, timezone_str)
    houses = swe.houses(jd, latitude, longitude, b'P')
    asc = houses[1][0] % 360.0
    planets_data = {p: swe.calc_ut(jd, pid)[0][0] % 360.0 for p, pid in PLANET_IDS.items()}

    sun_lon = planets_data['Sun']
    # ASC 기준: Sun 이 ASC~DESC 위쪽(7~12하우스 근처)이면 주간
    is_diurnal = ((sun_lon - asc) % 360.0) >= 180.0

    return {
        'jd': jd,
        'asc': asc,
        'planets': planets_data,
        'is_diurnal': is_diurnal,
        'latitude': latitude,
        'longitude': longitude,
    }

def rotate_sequence(seq, start):
    return seq[seq.index(start):] + seq[:seq.index(start)]

def calculate_level1(chart_data, birth_datetime, asc_times, usp):
    """Level 1 Major Periods (USP 기반, Sect 루미나리 기준 회전)"""
    sect_lord = 'Sun' if chart_data['is_diurnal'] else 'Moon'

    base = usp[sect_lord]
    usp_rot = {p: (usp[p] - base) % 360.0 for p in PLANETS}

    others = sorted([p for p in PLANETS if p != sect_lord], key=lambda p: usp_rot[p])
    sequence = [sect_lord] + others

    arcs = []
    durations = []
    periods = []

    cur_dt = birth_datetime
    cum_years = 0.0

    for i in range(7):
        cur, nxt = sequence[i], sequence[(i+1) % 7]
        arc = (usp_rot[nxt] - usp_rot[cur]) % 360.0
        arc = max(arc, 1e-10)
        arcs.append(arc)

        years = arc * 75.0 / 360.0  # 75/360 규칙
        durations.append(years)

        days = years * 365.242199
        end_dt = cur_dt + timedelta(days=days)
        periods.append({
            'planet': cur,
            'start_date': cur_dt,
            'end_date': end_dt,
            'duration_years': years,
        })
        cur_dt = end_dt
        cum_years += years

    return {
        'sequence': sequence,
        'periods': periods,
        'arcs': arcs,
        'durations': durations,
        'sect_lord': sect_lord,
        'usp_rot': usp_rot,
    }

def calculate_sublevel(parent, seq, duration):
    subseq = rotate_sequence(seq, parent['planet'])
    total_ly = sum(LESSER_YEARS.values())
    subs = []
    cur = parent['start_date']
    for p in subseq:
        yrs = duration * LESSER_YEARS[p] / total_ly
        days = yrs * 365.242199
        end = cur + timedelta(days=days)
        subs.append({
            'planet': p,
            'start_date': cur,
            'end_date': end,
            'duration_years': yrs,
        })
        cur = end
    return subs

def find_active(periods, dt):
    for p in periods:
        if p['start_date'] <= dt < p['end_date']:
            return p
    return None

def find_active_with_block(blocks, dt):
    for blk in blocks:
        p = find_active(blk['periods'], dt)
        if p:
            return blk, p
    return None, None


# ==============================
# UI
# ==============================
st.set_page_config(page_title="Decennials Calculator", layout="wide")
st.title("데세니얼 계산기 (레벨4)")

with st.form("input_form"):
    col1, col2 = st.columns(2)
    with col1:
        birth_date = st.date_input(
            "출생일",
            value=date(1980, 5, 14),
            min_value=date(1500, 1, 1),
            max_value=date(2200, 12, 31),
        )
        time_str = st.text_input("출생 시간 (HH:MM)", value="05:30", help="예: 05:30, 14:27")
    with col2:
        city_input = st.text_input("출생 도시 (영문)", value="Seoul, South Korea")
        selected_city = city_input
        if city_input:
            cities = search_cities(city_input)
            if cities:
                labels = [c[0] for c in cities]
                selected_city = st.selectbox("검색 결과에서 선택", labels, index=0)

        target_date_input = st.date_input(
            "기준 날짜 (Level 3/4)",
            value=datetime.now().date(),
            min_value=date(1900, 1, 1),
            max_value=date(2100, 12, 31),
        )

    submitted = st.form_submit_button("계산 시작", key="submit_main")

if submitted:
    # 시간 유효성 검사
    if not re.match(r'^[0-2]?\d:[0-5]\d$', time_str.strip()):
        st.error("시간 형식이 잘못되었습니다. 예: 05:30")
        st.stop()

    try:
        hour, minute = map(int, time_str.split(':'))
        birth_datetime = datetime.combine(birth_date, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
    except Exception:
        st.error("시간 입력 오류")
        st.stop()

    # 도시 → 위도/경도/타임존
    with st.spinner("도시 정보 확인 중..."):
        try:
            lat, lon, tz, full_addr = get_coordinates_and_timezone(selected_city)
            st.success(f"위치: {full_addr}")
            st.info(f"위도: {lat:.4f}°, 경도: {lon:.4f}°, 타임존: {tz}")
        except Exception as e:
            st.error(f"도시 오류: {e}")
            st.stop()

    # 차트 계산
    with st.spinner("차트 계산 중..."):
        chart = calculate_chart(birth_datetime, lat, lon, tz)
        sect = "주간 (Diurnal)" if chart['is_diurnal'] else "야간 (Nocturnal)"
        st.info(f"Sect: {sect} | Ascendant: {chart['asc']:.2f}°")

    # 기본 정보 요약
    meta_rows = [
        {"항목": "출생일", "값": birth_datetime.strftime("%Y-%m-%d %H:%M")},
        {"항목": "기준 날짜", "값": target_date_input.strftime("%Y-%m-%d")},
        {"항목": "도시", "값": full_addr},
        {"항목": "위도", "값": f"{lat:.4f}"},
        {"항목": "경도", "값": f"{lon:.4f}"},
        {"항목": "타임존", "값": tz},
        {"항목": "섹트", "값": sect},
        {"항목": "어센던트 도수", "값": f"{chart['asc']:.4f}"},
    ]
    meta_df = pd.DataFrame(meta_rows)
    st.subheader("기본 정보 요약")
    st.table(meta_df)

    # Ascensional Times & USP
    asc_times = calculate_ascensional_times(lat, lon, chart['jd'])
    usp, usp_details = compute_unique_start_points(chart['planets'], asc_times)

    # 1) 사인별 상승시간 표
    asc_rows = []
    cum = 0.0
    for i, t in enumerate(asc_times):
        cum += t
        asc_rows.append({
            "사인": SIGN_NAMES[i],
            "상승시간(°)": round(t, 4),
            "누적합(°)": round(cum, 4),
        })
    asc_df = pd.DataFrame(asc_rows)
    st.subheader("사인별 상승 시간 (Ascensional Times)")
    st.table(asc_df)

    # ==============================
    # Level 1
    # ==============================
    with st.spinner("레벨 1 계산 중..."):
        level1 = calculate_level1(chart, birth_datetime, asc_times, usp)
        seq_str = " → ".join(level1['sequence'])
        st.success(f"Level 1 행성 순서: {seq_str}")

    l1_df = pd.DataFrame([{
        "행성": f"{PLANET_KO[p['planet']]} ({p['planet']})",
        "시작": p['start_date'].strftime("%Y-%m-%d"),
        "종료": p['end_date'].strftime("%Y-%m-%d"),
        "기간(년)": round(p['duration_years'], 6),
    } for p in level1['periods']])

    st.subheader("Level 1: Major Periods (주요 시기)")
    st.dataframe(l1_df, use_container_width=True)

    # Level 1 디버그: Arc / 비율 / 누적 연수
    l1_debug_rows = []
    cum_years = 0.0
    for i, planet in enumerate(level1['sequence']):
        arc = level1['arcs'][i]
        yrs = level1['durations'][i]
        prop = yrs / 75.0 if 75.0 != 0 else 0.0
        start_dt = level1['periods'][i]['start_date']
        end_dt = level1['periods'][i]['end_date']
        cum_years += yrs
        l1_debug_rows.append({
            "순서": i + 1,
            "행성": f"{PLANET_KO[planet]} ({planet})",
            "Arc of direction(°)": round(arc, 6),
            "분배 비율": round(prop, 12),
            "할당된 시간(년)": round(yrs, 6),
            "누적 시간(년)": round(cum_years, 6),
            "시작": start_dt.strftime("%Y-%m-%d"),
            "종료": end_dt.strftime("%Y-%m-%d"),
        })
    l1_debug_df = pd.DataFrame(l1_debug_rows)
    st.subheader("행성의 상승시간 (섹트 루미나리 기준)")
    st.table(l1_debug_df)

    # ==============================
    # Level 2, 3, 4 계산
    # ==============================
    target_date = datetime.combine(target_date_input, datetime.min.time())

    # Level 2
    level2_all = [{
        'parent': l1['planet'],
        'periods': calculate_sublevel(l1, level1['sequence'], l1['duration_years']),
    } for l1 in level1['periods']]

    # Level 3
    level3_all = []
    for blk in level2_all:
        for sub in blk['periods']:
            level3_all.append({
                'parent_l1': blk['parent'],
                'parent_l2': sub['planet'],
                'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years']),
            })

    # Level 4
    level4_all = []
    for blk in level3_all:
        for sub in blk['periods']:
            level4_all.append({
                'parent_l1': blk['parent_l1'],
                'parent_l2': blk['parent_l2'],
                'parent_l3': sub['planet'],
                'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years']),
            })

  # ==============================
    # Level 3 & 4 현재 활성 구간 (Level1 바로 아래에)
    # ==============================
    col3, col4 = st.columns(2)
    with col3:
        st.subheader(f"Level 3: Sub-Minor (기준일: {target_date_input})")
        blk3, act3 = find_active_with_block(level3_all, target_date)
        if act3:
            tag = f"{blk3['parent_l1']}-{blk3['parent_l2']}-{act3['planet']}"
            st.markdown(f"**활성**: `{tag}`")
            st.write(f"시작: `{act3['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act3['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act3['duration_years']:.6f}`년")
        else:
            st.info("활성 구간 없음")

    with col4:
        st.subheader(f"Level 4: Sub-Sub-Minor (기준일: {target_date_input})")
        blk4, act4 = find_active_with_block(level4_all, target_date)
        if act4:
            tag = f"{blk4['parent_l1']}-{blk4['parent_l2']}-{blk4['parent_l3']}-{act4['planet']}"
            st.markdown(f"**활성**: `{tag}`")
            st.write(f"시작: `{act4['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act4['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act4['duration_years']:.8f}`년")
        else:
            st.info("활성 구간 없음")

    # ==============================
    # Level 2 전체 (항상 보이게)
    # ==============================
    l2_rows = []
    for block in level2_all:
        for sp in block['periods']:
            l2_rows.append({
                "메이저(레벨 1)": block['parent'],
                "마이너(레벨 2)": sp['planet'],
                "시작": sp['start_date'].strftime("%Y-%m-%d"),
                "종료": sp['end_date'].strftime("%Y-%m-%d"),
                "기간(년)": f"{sp['duration_years']:.4f}"
            })
    st.subheader("Level 2: Minor Periods")
    st.dataframe(pd.DataFrame(l2_rows), use_container_width=True)

    # ==============================
    # Level 3 전체 (필요할 때만 펼침)
    # ==============================
    with st.expander("Level 3: Sub-Minor Periods (전체 보기)", expanded=False):
        l3_rows = []
        for block in level3_all:
            for sp in block['periods']:
                l3_rows.append({
                    "L1": block['parent_l1'],
                    "L2": block['parent_l2'],
                    "L3": sp['planet'],
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.6f}"
                })
        st.dataframe(pd.DataFrame(l3_rows), use_container_width=True)

    # ==============================
    # Level 4 전체 (필요할 때만 펼침)
    # ==============================
    with st.expander("Level 4: Sub-Sub-Minor Periods (전체 보기)", expanded=False):
        l4_rows = []
        for block in level4_all:
            for sp in block['periods']:
                l4_rows.append({
                    "L1": block['parent_l1'],
                    "L2": block['parent_l2'],
                    "L3": block['parent_l3'],
                    "L4": sp['planet'],
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.8f}"
                })
        st.dataframe(pd.DataFrame(l4_rows), use_container_width=True)

    st.success("모든 계산 완료!")
