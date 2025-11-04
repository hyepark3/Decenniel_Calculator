# app.py
import streamlit as st
import swisseph as swe
from datetime import datetime, timedelta
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
import pytz
import math
import pandas as pd

# ==============================
# Constants (동일)
# ==============================
LESSER_YEARS = {
    'Sun': 19, 'Moon': 25, 'Mercury': 20, 'Venus': 8,
    'Mars': 15, 'Jupiter': 12, 'Saturn': 30
}
PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
PLANET_IDS = {
    'Sun': swe.SUN, 'Moon': swe.MOON, 'Mercury': swe.MERCURY,
    'Venus': swe.VENUS, 'Mars': swe.MARS, 'Jupiter': swe.JUPITER, 'Saturn': swe.SATURN
}

# ==============================
# Utility Functions (동일)
# ==============================
def get_coordinates_and_timezone(city_name):
    try:
        geolocator = Nominatim(user_agent="decennials_calculator")
        location = geolocator.geocode(city_name)
        if not location:
            raise ValueError(f"도시 '{city_name}'를 찾을 수 없습니다.")
        tf = TimezoneFinder()
        timezone_str = tf.timezone_at(lat=location.latitude, lng=location.longitude)
        if not timezone_str:
            raise ValueError("타임존을 찾을 수 없습니다.")
        return location.latitude, location.longitude, timezone_str
    except Exception as e:
        st.error(f"도시 정보 오류: {e}")
        st.stop()

def calculate_julian_day(dt, timezone_str):
    local_tz = pytz.timezone(timezone_str)
    local_dt = local_tz.localize(dt)
    utc_dt = local_dt.astimezone(pytz.UTC)
    jd = swe.julday(
        utc_dt.year, utc_dt.month, utc_dt.day,
        utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0
    )
    return jd

def ecliptic_to_ra(ecl_lon, obliquity):
    lam = math.radians(ecl_lon)
    eps = math.radians(obliquity)
    ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam))
    return math.degrees(ra) % 360.0
# 기존의 calculate_ascensional_times 함수를 지우고,
# 아래의 새 함수로 완전히 대체해주세요.

def calculate_ascensional_times(latitude, jd):
    """
    [수정된 버전]
    주어진 위도에서 12궁의 실제 상승 시간(Oblique Ascension)을
    pyswisseph의 rise_trans 함수를 이용해 정확하게 계산합니다.
    """
    if abs(latitude) > 66.5:
        st.warning(f"고위도 지역 (위도 {latitude:.1f}도): 일부 궁 상승 불가. 정확도가 떨어질 수 있습니다.")

    # 계산의 기준이 될 하루 전후의 율리우스력을 준비합니다.
    jd_start = jd - 1

    # '고정된 별'처럼 취급하여 각 별자리 시작점의 상승 시각을 찾습니다.
    # geopos: [경도, 위도, 고도]
    geopos = [0, latitude, 0] 
    # rsmi: 상승(swe.CALC_RISE), 천문박명 아래(-18도), swe.BIT_FIXED_DISC_PERIMETER 플래그
    rsmi = swe.CALC_RISE | swe.BIT_DISC_CENTER

    sign_rise_jds = []
    for sign in range(13): # 0도부터 360도까지 (양자리 0도 ~ 다음 양자리 0도)
        ecl_lon = sign * 30.0
        # swe.fixstar_ut 함수는 ecl_lon을 별의 위치처럼 계산해줍니다.
        starname = f"sign_{sign}".encode()
        jd_ut, _, _ = swe.fixstar_ut(starname, jd_start, ecl_lon, 0, rsmi, geopos)
        sign_rise_jds.append(jd_ut)

    # 각 상승 시각의 차이를 계산하여 '기간'(일)으로 변환합니다.
    ascensional_times_in_days = []
    for i in range(12):
        # 율리우스력의 차이는 '일' 단위입니다.
        duration_days = sign_rise_jds[i+1] - sign_rise_jds[i]
        ascensional_times_in_days.append(duration_days)

    # 최종적으로 '일' 단위를 '도' 단위로 변환합니다. (1일 = 360도 회전)
    ascensional_times_in_degrees = [days * 360 for days in ascensional_times_in_days]

    return ascensional_times_in_degrees


def calculate_unique_starting_point(planet_longitude, ascensional_times):
    sign = int(planet_longitude // 30)
    degree_in_sign = planet_longitude % 30
    cumulative = sum(ascensional_times[:sign])
    proportional = (degree_in_sign / 30.0) * ascensional_times[sign]
    return cumulative + proportional

# ==============================
# Chart & Levels (동일)
# ==============================
def calculate_chart(birth_datetime, latitude, longitude, timezone_str):
    jd = calculate_julian_day(birth_datetime, timezone_str)
    houses = swe.houses(jd, latitude, longitude, b'P')
    asc = houses[1][0]
    planets_data = {}
    for planet_name, planet_id in PLANET_IDS.items():
        pos = swe.calc_ut(jd, planet_id)[0][0]
        planets_data[planet_name] = pos
    sun_lon = planets_data['Sun']
    Sun_DN_check = (sun_lon - asc) % 360
    is_diurnal = Sun_DN_check >= 180.0
    return {
        'jd': jd, 'asc': asc, 'planets': planets_data,
        'is_diurnal': is_diurnal, 'latitude': latitude, 'longitude': longitude
    }

def rotate_sequence(sequence, start_planet):
    idx = sequence.index(start_planet)
    return sequence[idx:] + sequence[:idx]

def calculate_level1(chart_data, birth_datetime):
    latitude = chart_data['latitude']
    planets_data = chart_data['planets']
    is_diurnal = chart_data['is_diurnal']
    jd = chart_data['jd']
    ascensional_times = calculate_ascensional_times(latitude, jd)
    usp = {p: calculate_unique_starting_point(planets_data[p], ascensional_times) for p in PLANETS}
    sect_lord = 'Sun' if is_diurnal else 'Moon'
    base = usp[sect_lord]
    usp_rot = {p: (usp[p] - base) % 360.0 for p in PLANETS}
    other_planets = [p for p in PLANETS if p != sect_lord]
    other_planets.sort(key=lambda p: usp_rot[p])
    final_sequence = [sect_lord] + other_planets
    arcs = []
    for i in range(len(final_sequence)):
        cur = final_sequence[i]
        nxt = final_sequence[(i + 1) % len(final_sequence)]
        arc = usp_rot[nxt] - usp_rot[cur]
        if arc < 0: arc += 360.0
        arcs.append(arc)
    total_arc = sum(arcs)
    if abs(total_arc - 360.0) > 1e-6:
        scale = 360.0 / total_arc
        arcs = [a * scale for a in arcs]
    durations = [(arc / 360.0) * 75.0 for arc in arcs]
    level1_periods = []
    current_date = birth_datetime
    for i, planet in enumerate(final_sequence):
        duration_years = durations[i]
        duration_days = duration_years * 365.25
        end_date = current_date + timedelta(days=duration_days)
        level1_periods.append({
            'planet': planet, 'start_date': current_date, 'end_date': end_date,
            'duration_years': duration_years, 'arc': arcs[i],
            'usp_raw': usp[planet], 'usp_rot': usp_rot[planet]
        })
        current_date = end_date
    return {
        'sequence': final_sequence, 'periods': level1_periods,
        'usp': usp, 'usp_rot': usp_rot, 'ascensional_times': ascensional_times,
        'sect_lord': sect_lord
    }

def calculate_sublevel(parent_period, planet_sequence, parent_duration_years):
    parent_lord = parent_period['planet']
    sublevel_sequence = rotate_sequence(planet_sequence, parent_lord)
    total_lesser_years = sum(LESSER_YEARS.values())
    subperiods = []
    current_date = parent_period['start_date']
    for planet in sublevel_sequence:
        ratio = LESSER_YEARS[planet] / total_lesser_years
        duration_years = parent_duration_years * ratio
        duration_days = duration_years * 365.25
        end_date = current_date + timedelta(days=duration_days)
        subperiods.append({
            'planet': planet, 'start_date': current_date, 'end_date': end_date,
            'duration_years': duration_years
        })
        current_date = end_date
    return subperiods

# ==============================
# UI & Display
# ==============================
st.set_page_config(page_title="Decennials Calculator", layout="wide")
st.title("🌟 Personalized Decennials Calculator (4-Level)")

with st.form("input_form"):
    col1, col2 = st.columns(2)
    with col1:
        birth_date = st.date_input("출생일", value=datetime(1980, 5, 14))
        birth_time = st.time_input("출생 시간", value=datetime.strptime("12:00", "%H:%M").time())
    with col2:
        city_name = st.text_input("출생 도시 (영문)", value="Seoul, South Korea")
    target_date_input = st.date_input("기준 날짜 (Level 3/4)", value=datetime.now().date(), help="오늘 기준이면 비워두세요")
    submitted = st.form_submit_button("계산 시작")

if submitted:
    birth_datetime = datetime.combine(birth_date, birth_time)

    with st.spinner("도시 정보를 가져오는 중..."):
        latitude, longitude, timezone_str = get_coordinates_and_timezone(city_name)
        st.success(f"위치: {city_name} → 위도 {latitude:.4f}°, 경도 {longitude:.4f}°, 타임존: {timezone_str}")

    with st.spinner("출생 차트 계산 중..."):
        chart_data = calculate_chart(birth_datetime, latitude, longitude, timezone_str)
        sect_type = "주간 (Diurnal)" if chart_data['is_diurnal'] else "야간 (Nocturnal)"
        st.info(f"Sect: {sect_type} | Ascendant: {chart_data['asc']:.2f}°")

    with st.spinner("Level 1 계산 중..."):
        level1 = calculate_level1(chart_data, birth_datetime)
        st.success(f"행성 순서: {' → '.join(level1['sequence'])}")

    # Level 1 표
    l1_df = pd.DataFrame([
        {
            "행성": p['planet'],
            "시작일": p['start_date'].strftime("%Y-%m-%d"),
            "종료일": p['end_date'].strftime("%Y-%m-%d"),
            "기간(년)": f"{p['duration_years']:.3f}"
        }
        for p in level1['periods']
    ])
    st.subheader("Level 1: Major Periods")
    st.dataframe(l1_df, use_container_width=True)

    # Level 2
    with st.spinner("Level 2 계산 중..."):
        level2_all = []
        for l1p in level1['periods']:
            subs = calculate_sublevel(l1p, level1['sequence'], l1p['duration_years'])
            level2_all.append({'parent': l1p['planet'], 'periods': subs})

    st.subheader("Level 2: Minor Periods (요약)")
    for block in level2_all:
        with st.expander(f"[{block['parent']} Major]"):
            df = pd.DataFrame([
                {
                    "Minor": f"{block['parent']}-{sp['planet']}",
                    "시작": sp['start_date'].strftime("%Y-%m-%d"),
                    "종료": sp['end_date'].strftime("%Y-%m-%d"),
                    "기간(년)": f"{sp['duration_years']:.4f}"
                }
                for sp in block['periods']
            ])
            st.dataframe(df, use_container_width=True)

    # Level 3 & 4 (현재 활성 구간)
    target_date = datetime.combine(target_date_input, datetime.min.time())

    def find_active(periods, dt):
        for p in periods:
            if p['start_date'] <= dt < p['end_date']:
                return p
        return None

    # Level 3 계산
    level3_all = []
    for l2 in level2_all:
        for l2p in l2['periods']:
            subs = calculate_sublevel(l2p, level1['sequence'], l2p['duration_years'])
            level3_all.append({
                'parent_l1': l2['parent'], 'parent_l2': l2p['planet'], 'periods': subs
            })

    # Level 4 계산
    level4_all = []
    for l3 in level3_all:
        for l3p in l3['periods']:
            subs = calculate_sublevel(l3p, level1['sequence'], l3p['duration_years'])
            level4_all.append({
                'parent_l1': l3['parent_l1'], 'parent_l2': l3['parent_l2'],
                'parent_l3': l3p['planet'], 'periods': subs
            })

    # 현재 활성 Level 3
    st.subheader(f"Level 3: Sub-Minor (기준일 {target_date_input})")
    found = False
    for block in level3_all:
        act = find_active(block['periods'], target_date)
        if act:
            found = True
            tag = f"{block['parent_l1']}-{block['parent_l2']}-{act['planet']}"
            st.markdown(f"**활성 Level 3**: `{tag}`")
            st.write(f"시작: `{act['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act['duration_years']:.6f}`년")
            st.markdown("---")
    if not found:
        st.info("해당 날짜에 활성화된 Level 3가 없습니다.")

    # 현재 활성 Level 4
    st.subheader(f"Level 4: Sub-Sub-Minor (기준일 {target_date_input})")
    found = False
    for block in level4_all:
        act = find_active(block['periods'], target_date)
        if act:
            found = True
            tag = f"{block['parent_l1']}-{block['parent_l2']}-{block['parent_l3']}-{act['planet']}"
            st.markdown(f"**활성 Level 4**: `{tag}`")
            st.write(f"시작: `{act['start_date'].strftime('%Y-%m-%d')}`")
            st.write(f"종료: `{act['end_date'].strftime('%Y-%m-%d')}`")
            st.write(f"기간: `{act['duration_years']:.8f}`년")
            st.markdown("---")
    if not found:
        st.info("해당 날짜에 활성화된 Level 4가 없습니다.")


    st.success("모든 계산 완료!")
