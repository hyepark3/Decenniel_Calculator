# app.py
import streamlit as st
import swisseph as swe
from datetime import datetime, timedelta
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
import pytz
import math
import pandas as pd
import re

# ==============================
# Ephemeris 경로 설정 (Streamlit Cloud 대응)
# ==============================
import os
ephem_path = os.path.join(os.path.dirname(__file__), "ephem")
if os.path.exists(ephem_path):
    swe.set_ephe_path(ephem_path)

# ==============================
# Constants
# ==============================
LESSER_YEARS = {'Sun': 19, 'Moon': 25, 'Mercury': 20, 'Venus': 8, 'Mars': 15, 'Jupiter': 12, 'Saturn': 30}
PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
PLANET_IDS = {
    'Sun': swe.SUN, 'Moon': swe.MOON, 'Mercury': swe.MERCURY,
    'Venus': swe.VENUS, 'Mars': swe.MARS, 'Jupiter': swe.JUPITER, 'Saturn': swe.SATURN
}

# ==============================
# 도시 자동 완성 & 좌표 가져오기
# ==============================
@st.cache_data(ttl=3600)
def search_cities(query):
    geolocator = Nominatim(user_agent="decennials_calculator")
    try:
        locations = geolocator.geocode(query, exactly_one=False, limit=5)
        if locations:
            return [(loc.address, loc.latitude, loc.longitude) for loc in locations]
    except:
        pass
    return []

def get_coordinates_and_timezone(city_name):
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
# 정확한 상승 시간 계산 (swe.rise_trans 기반)
# ==============================
def calculate_ascensional_times(latitude, longitude, jd):
    """기존 수학적 방법 (안정적) + swe.rise_trans 보완"""
    if abs(latitude) > 66.5:
        st.warning(f"고위도 지역 (위도 {latitude:.1f}도): 이론적 계산 사용.")

    # Step 1: 이론적 방법 (항상 안정적)
    eps = swe.calc_ut(jd, swe.ECL_NUT)[0][0]
    asc_times = []
    for sign in range(12):
        lon1 = sign * 30.0
        lon2 = (sign + 1) * 30.0
        ra1 = math.degrees(math.atan2(math.sin(math.radians(lon1)) * math.cos(math.radians(eps)), math.cos(math.radians(lon1))))
        ra2 = math.degrees(math.atan2(math.sin(math.radians(lon2)) * math.cos(math.radians(eps)), math.cos(math.radians(lon2))))
        diff = (ra2 - ra1) % 360
        asc_times.append(diff if diff > 0 else diff + 360)
    
    # Step 2: swe.rise_trans로 보완 (고위도 제외)
    if abs(latitude) <= 66.5:
        try:
            geopos = [longitude, latitude, 0]
            jd_start = jd - 1
            rsmi = swe.CALC_RISE | swe.BIT_DISC_CENTER
            rise_jds = []
            for sign in range(13):
                lon = sign * 30.0
                ret, t_ut = swe.rise_trans(jd_start, -1, '', rsmi, geopos, 0, 0, lon, 0)
                rise_jds.append(t_ut if ret > 0 else jd_start + 0.5)
            real_times = [(rise_jds[i+1] - rise_jds[i]) % 1 for i in range(12)]
            real_times = [t if t <= 0.5 else 1 - t for t in real_times]
            real_times = [max(t, 1e-6) for t in real_times]  # 0 방지
            total_real = sum(real_times)
            if total_real > 0:
                real_times = [t / total_real * 360 for t in real_times]
                # 이론값과 실제값 보간
                asc_times = [(asc_times[i] + real_times[i]) / 2 for i in range(12)]
        except:
            pass  # 실패 시 이론값 유지

    # 정규화
    total = sum(asc_times)
    if total < 1e-6:
        asc_times = [30.0] * 12  # 극단적 fallback
    else:
        asc_times = [a * 360 / total for a in asc_times]
    return asc_times

# ==============================
# 나머지 함수 (기존과 동일, 약간 최적화)
# ==============================
def calculate_julian_day(dt, timezone_str):
    local_tz = pytz.timezone(timezone_str)
    local_dt = local_tz.localize(dt)
    utc_dt = local_dt.astimezone(pytz.UTC)
    return swe.julday(utc_dt.year, utc_dt.month, utc_dt.day,
                      utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0)

def calculate_unique_starting_point(planet_longitude, ascensional_times):
    sign = int(planet_longitude // 30)
    degree_in_sign = planet_longitude % 30
    cumulative = sum(ascensional_times[:sign])
    proportional = (degree_in_sign / 30.0) * ascensional_times[sign]
    return cumulative + proportional

def calculate_chart(birth_datetime, latitude, longitude, timezone_str):
    jd = calculate_julian_day(birth_datetime, timezone_str)
    houses = swe.houses(jd, latitude, longitude, b'P')
    asc = houses[1][0]
    planets_data = {p: swe.calc_ut(jd, pid)[0][0] for p, pid in PLANET_IDS.items()}
    sun_lon = planets_data['Sun']
    is_diurnal = ((sun_lon - asc) % 360) >= 180.0
    return {'jd': jd, 'asc': asc, 'planets': planets_data, 'is_diurnal': is_diurnal,
            'latitude': latitude, 'longitude': longitude}

def rotate_sequence(seq, start): return seq[seq.index(start):] + seq[:seq.index(start)]

def calculate_level1(chart_data, birth_datetime):
    lat, lon, jd = chart_data['latitude'], chart_data['longitude'], chart_data['jd']
    asc_times = calculate_ascensional_times(lat, lon, jd)
    
    # USP 계산
    usp = {}
    for p in PLANETS:
        lon = chart_data['planets'][p]
        sign = int(lon // 30)
        deg = lon % 30
        cum = sum(asc_times[:sign])
        prop = (deg / 30.0) * asc_times[sign]
        usp[p] = cum + prop

    sect_lord = 'Sun' if chart_data['is_diurnal'] else 'Moon'
    base = usp[sect_lord]
    usp_rot = {p: (usp[p] - base) % 360.0 for p in PLANETS}
    
    # 시퀀스
    others = sorted([p for p in PLANETS if p != sect_lord], key=lambda p: usp_rot[p])
    sequence = [sect_lord] + others

    # Arc 계산 (최소값 보장)
    arcs = []
    for i in range(7):
        cur, nxt = sequence[i], sequence[(i+1)%7]
        arc = (usp_rot[nxt] - usp_rot[cur]) % 360
        arc = max(arc, 1e-10)  # 0 방지
        arcs.append(arc)

    total_arc = sum(arcs)
    
    # ZeroDivision 방어
    if total_arc < 1e-8:
        st.warning("Arc 합계가 0에 가까움 → 평균 분배 사용")
        durations = [75.0 / 7] * 7
    else:
        durations = [arc / total_arc * 75.0 for arc in arcs]

    # 기간 생성
    periods = []
    cur = birth_datetime
    for i, planet in enumerate(sequence):
        days = durations[i] * 365.25
        end = cur + timedelta(days=days)
        periods.append({
            'planet': planet,
            'start_date': cur,
            'end_date': end,
            'duration_years': durations[i]
        })
        cur = end

    return {'sequence': sequence, 'periods': periods}

def calculate_sublevel(parent, seq, duration):
    subseq = rotate_sequence(seq, parent['planet'])
    total_ly = sum(LESSER_YEARS.values())
    subs = []
    cur = parent['start_date']
    for p in subseq:
        yrs = duration * LESSER_YEARS[p] / total_ly
        days = yrs * 365.25
        end = cur + timedelta(days=days)
        subs.append({'planet': p, 'start_date': cur, 'end_date': end, 'duration_years': yrs})
        cur = end
    return subs

# ==============================
# UI
# ==============================
st.set_page_config(page_title="Decennials Calculator", layout="wide")
st.title("Personalized Decennials Calculator (4-Level)")

with st.form("input_form"):
    col1, col2 = st.columns(2)
    with col1:
        birth_date = st.date_input("출생일", value=datetime(1980, 5, 14))
        time_str = st.text_input("출생 시간 (HH:MM)", value="12:00", help="예: 05:30, 14:27")
    with col2:
        city_input = st.text_input("출생 도시 (영문)", value="Seoul, South Korea")
        if city_input:
            cities = search_cities(city_input)
            if cities:
                st.write("검색 결과:")
                for addr, lat, lon in cities:
                    st.write(f"• {addr}")
    target_date_input = st.date_input("기준 날짜 (Level 3/4)", value=datetime.now().date())
    submitted = st.form_submit_button("계산 시작")

if submitted:
    # 시간 유효성 검사
    if not re.match(r'^[0-2]\d:[0-5]\d$', time_str):
        st.error("시간 형식이 잘못되었습니다. 예: 05:30")
        st.stop()
    try:
        hour, minute = map(int, time_str.split(':'))
        birth_datetime = datetime.combine(birth_date, datetime.min.time()).replace(hour=hour, minute=minute)
    except:
        st.error("시간 입력 오류")
        st.stop()

    with st.spinner("도시 정보 확인 중..."):
        try:
            lat, lon, tz, full_addr = get_coordinates_and_timezone(city_input)
            st.success(f"위치: {full_addr}")
            st.info(f"위도: {lat:.4f}°, 경도: {lon:.4f}°, 타임존: {tz}")
        except Exception as e:
            st.error(f"도시 오류: {e}")
            st.stop()

    with st.spinner("차트 계산 중..."):
        chart = calculate_chart(birth_datetime, lat, lon, tz)
        sect = "주간 (Diurnal)" if chart['is_diurnal'] else "야간 (Nocturnal)"
        st.info(f"Sect: {sect} | Ascendant: {chart['asc']:.2f}°")

    # ==============================
    # Level 1
    # ==============================
    with st.spinner("Level 1 계산 중..."):
        level1 = calculate_level1(chart, birth_datetime)
        st.success(f"행성 순서: {' → '.join(level1['sequence'])}")

    l1_df = pd.DataFrame([{
        "행성": p['planet'],
        "시작": p['start_date'].strftime("%Y-%m-%d"),
        "종료": p['end_date'].strftime("%Y-%m-%d"),
        "기간(년)": f"{p['duration_years']:.3f}"
    } for p in level1['periods']])
    st.subheader("Level 1: Major Periods")
    st.dataframe(l1_df, use_container_width=True)

    # ==============================
    # Level 3 & 4 (현재 활성 구간) - Level 1 바로 아래!
    # ==============================
    target_date = datetime.combine(target_date_input, datetime.min.time())

    # Level 2 계산
    level2_all = [({'parent': l1['planet'], 'periods': calculate_sublevel(l1, level1['sequence'], l1['duration_years'])}) for l1 in level1['periods']]
    # Level 3
    level3_all = []
    for l2 in level2_all:
        for sub in l2['periods']:
            level3_all.append({'parent_l1': l2['parent'], 'parent_l2': sub['planet'], 'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years'])})
    # Level 4
    level4_all = []
    for l3 in level3_all:
        for sub in l3['periods']:
            level4_all.append({'parent_l1': l3['parent_l1'], 'parent_l2': l3['parent_l2'], 'parent_l3': sub['planet'], 'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years'])})

    def find_active(periods, dt):
        for p in periods:
            if p['start_date'] <= dt < p['end_date']:
                return p
        return None

    col3, col4 = st.columns(2)
    with col3:
        st.subheader(f"Level 3: Sub-Minor\n(기준일: {target_date_input})")
        act3 = next((find_active(b['periods'], target_date) for b in level3_all if find_active(b['periods'], target_date)), None)
        if act3:
            tag = f"{act3['planet']} → "
            prev = next(b for b in level3_all if b['periods'][0]['planet'] == act3['planet'])
            tag = f"{prev['parent_l1']}-{prev['parent_l2']}-{act3['planet']}"
            st.markdown(f"**활성**: `{tag}`")
            st.write(f"시작: `{act3['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act3['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act3['duration_years']:.6f}`년")
        else:
            st.info("활성 구간 없음")

    with col4:
        st.subheader(f"Level 4: Sub-Sub-Minor\n(기준일: {target_date_input})")
        act4 = next((find_active(b['periods'], target_date) for b in level4_all if find_active(b['periods'], target_date)), None)
        if act4:
            prev = next(b for b in level4_all if b['periods'][0]['planet'] == act4['planet'])
            tag = f"{prev['parent_l1']}-{prev['parent_l2']}-{prev['parent_l3']}-{act4['planet']}"
            st.markdown(f"**활성**: `{tag}`")
            st.write(f"시작: `{act4['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act4['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act4['duration_years']:.8f}`년")
        else:
            st.info("활성 구간 없음")

    # ==============================
    # Level 2 전체 접기/펴기
    # ==============================
    with st.expander("Level 2: Minor Periods (전체 보기)", expanded=False):
        l2_data = []
        for block in level2_all:
            for sp in block['periods']:
                l2_data.append({
                    "Major": block['parent'],
                    "Minor": sp['planet'],
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.4f}"
                })
        st.dataframe(pd.DataFrame(l2_data), use_container_width=True)

    # ==============================
    # Level 3 전체 접기/펴기
    # ==============================
    with st.expander("Level 3: Sub-Minor Periods (전체 보기)", expanded=False):
        l3_data = []
        for block in level3_all:
            for sp in block['periods']:
                l3_data.append({
                    "L1": block['parent_l1'],
                    "L2": block['parent_l2'],
                    "L3": sp['planet'],
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.6f}"
                })
        st.dataframe(pd.DataFrame(l3_data), use_container_width=True)

    # ==============================
    # Level 4 전체 접기/펴기
    # ==============================
    with st.expander("Level 4: Sub-Sub-Minor Periods (전체 보기)", expanded=False):
        l4_data = []
        for block in level4_all:
            for sp in block['periods']:
                l4_data.append({
                    "L1": block['parent_l1'],
                    "L2": block['parent_l2'],
                    "L3": block['parent_l3'],
                    "L4": sp['planet'],
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.8f}"
                })
        st.dataframe(pd.DataFrame(l4_data), use_container_width=True)

    st.success("모든 계산 완료!")

