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
import os

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
    'Venus': 8, 'Mars': 15, 'Jupiter': 12, 'Saturn': 30
}
PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
PLANET_IDS = {
    'Sun': swe.SUN, 'Moon': swe.MOON, 'Mercury': swe.MERCURY,
    'Venus': swe.VENUS, 'Mars': swe.MARS, 'Jupiter': swe.JUPITER, 'Saturn': swe.SATURN
}
SIGN_NAMES = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

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
    except Exception:
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

def calculate_ascensional_times(latitude, longitude, jd_ut):
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
    asc = houses[1][0]
    planets_data = {p: swe.calc_ut(jd, pid)[0][0] % 360.0 for p, pid in PLANET_IDS.items()}

    sun_lon = planets_data['Sun']
    # 네가 쓰던 기준: ASC에서 180° 이상 진행한 쪽(7~12하우스)에 태양이 있으면 주간
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
    lat, lon, jd = chart_data['latitude'], chart_data['longitude'], chart_data['jd']

    sect_lord = 'Sun' if chart_data['is_diurnal'] else 'Moon'

    # Sect lord 기준 회전
    base = usp[sect_lord]
    usp_rot = {p: (usp[p] - base) % 360.0 for p in PLANETS}

    others = sorted([p for p in PLANETS if p != sect_lord], key=lambda p: usp_rot[p])
    sequence = [sect_lord] + others

    # Arc & duration 계산
    arcs = []
    for i in range(7):
        cur, nxt = sequence[i], sequence[(i+1) % 7]
        arc = (usp_rot[nxt] - usp_rot[cur]) % 360.0
        arc = max(arc, 1e-10)  # 0 방지
        arcs.append(arc)

    total_arc = sum(arcs)

    # 엑셀 / Gemini 규칙 그대로: Arc * (75/360)
    if total_arc < 1e-8:
        st.warning("Arc 합계가 0에 가까움 → 평균 분배 사용")
        durations = [75.0 / 7] * 7
    else:
        # 참고용: 합이 360과 많이 다르면 경고
        if abs(total_arc - 360.0) > 1e-4:
            st.warning(f"Arc 합계가 360°와 {total_arc:.6f}°로 약간 다릅니다.")
        durations = [arc * 75.0 / 360.0 for arc in arcs]


    periods = []
    cur = birth_datetime
    for i, planet in enumerate(sequence):
        days = durations[i] * 365.242199
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
        subs.append({
            'planet': p,
            'start_date': cur,
            'end_date': end,
            'duration_years': yrs
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
st.title("Personalized Decennials Calculator (4-Level)")

with st.form("input_form"):
    col1, col2 = st.columns(2)
    with col1:
        birth_date = st.date_input("출생일", value=datetime(1970, 1, 1))
        time_str = st.text_input("출생 시간 (HH:MM)", value="00:00", help="예: 0:30, 14:27")
    with col2:
        city_input = st.text_input("출생 도시 (영문)", value="Seoul")
        selected_city = city_input
        if city_input:
            cities = search_cities(city_input)
            if cities:
                labels = [c[0] for c in cities]
                selected_city = st.selectbox("검색 결과에서 선택", labels, index=0)
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

    # ==============================
    # Ascensional Times & USP 디버그 (맨 위)
    # ==============================
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
    st.subheader("Ascensional Times (Sign Rising Times)")
    st.table(pd.DataFrame(asc_rows))

    # 2) 섹트 루미나리 기준 행성 상승 Arc 디버그
    sect_lord = 'Sun' if chart['is_diurnal'] else 'Moon'

    # 행성이름 한글 매핑
    PLANET_KO = {
        'Sun': '태양',
        'Moon': '달',
        'Mercury': '수성',
        'Venus': '금성',
        'Mars': '화성',
        'Jupiter': '목성',
        'Saturn': '토성',
    }

    # 섹트 루미나리 위치 정보
    lum_lon = chart['planets'][sect_lord] % 360.0
    lum_sign_idx = int(lum_lon // 30)
    lum_deg_in_sign = lum_lon % 30.0
    lum_sign_name = SIGN_NAMES[lum_sign_idx]

    # 루미나리 사인 전체 상승시간 & 사용/잔여 분해
    lum_sign_time = asc_times[lum_sign_idx]           # 루미나리 사인의 전체 AscTime
    lum_frac = lum_deg_in_sign / 30.0
    lum_used_in_sign = lum_sign_time * lum_frac       # 0°→루미나리까지
    lum_tail_arc = lum_sign_time - lum_used_in_sign   # 루미나리 이후→사인 끝까지

    # 중간 사인 Arc 계산 (루미나리 사인 다음 ~ 목표 사인의 직전)
    def intermediate_arc(sign_from, sign_to):
        """sign_from 다음 사인부터 sign_to 직전 사인까지 AscTime 합 (mod 12)"""
        if sign_from == sign_to:
            return 0.0
        arc = 0.0
        i = (sign_from + 1) % 12
        while i != sign_to:
            arc += asc_times[i]
            i = (i + 1) % 12
        return arc

    rows = []

    # 1) 맨 위에 섹트 루미나리 한 줄 (Arc = 0, 시작일 = 출생일)
    rows.append({
        "행성": f"{PLANET_KO[sect_lord]} ({sect_lord})",
        "사인": lum_sign_name,
        "행성도수(°)": round(lum_deg_in_sign, 4),
        "루미나리 사인": lum_sign_name,
        "루미나리도수(°)": round(lum_deg_in_sign, 4),
        "루미나리 잔여 Arc": round(lum_tail_arc, 4),
        "중간 사인 Arc": 0.0,
        "행성 Arc": 0.0,
        "총 Arc": 0.0,
        "상승 시작 날짜": birth_datetime.strftime("%Y-%m-%d"),
    })

    # 2) 나머지 행성들: 섹트 루미나리 → 각 행성까지 Arc 분해
    planet_rows = []
    for name in PLANETS:
        if name == sect_lord:
            continue

        info = usp_details[name]
        sign_idx = info["sign_idx"]
        sign_name = SIGN_NAMES[sign_idx]
        deg_in_sign = info["deg_in_sign"]

        # 행성 사인의 전체 AscTime & 그 안에서 행성 Arc
        sign_time = asc_times[sign_idx]
        frac_p = deg_in_sign / 30.0
        planet_arc = sign_time * frac_p

        # 루미나리 기준 중간 사인 합
        mid_arc = intermediate_arc(lum_sign_idx, sign_idx)

        # 총 Arc = 루미나리 tail + 중간사인 + 행성 Arc
        total_arc = lum_tail_arc + mid_arc + planet_arc

        # 총 Arc를 75년 스케일로 환산
        years = total_arc / 360.0 * 75.0
        start_date = birth_datetime + timedelta(days=years * 365.25)

        planet_rows.append({
            "행성": f"{PLANET_KO[name]} ({name})",
            "사인": sign_name,
            "행성도수(°)": round(deg_in_sign, 4),
            "루미나리 사인": lum_sign_name,
            "루미나리도수(°)": round(lum_deg_in_sign, 4),
            "루미나리 잔여 Arc": round(lum_tail_arc, 4),
            "중간 사인 Arc": round(mid_arc, 4),
            "행성 Arc": round(planet_arc, 4),
            "총 Arc": round(total_arc, 4),
            "상승 시작 날짜": start_date.strftime("%Y-%m-%d"),
        })

    # 총 Arc 기준으로 정렬 (섹트 루미나리 이후 누가 먼저 떠오르는지 순서)
    planet_rows.sort(key=lambda r: r["총 Arc"])

    # 최종 테이블: 루미나리 1줄 + 나머지 행성들
    rows.extend(planet_rows)

    st.subheader("섹트 루미나리 기준 행성 상승 Arc 디버그")
    st.table(pd.DataFrame(rows))



    # ==============================
    # Level 1
    # ==============================
    with st.spinner("Level 1 계산 중..."):
        level1 = calculate_level1(chart, birth_datetime, asc_times, usp)
        st.success(f"행성 순서: {' → '.join(level1['sequence'])}")

    l1_df = pd.DataFrame([{
        "행성": p['planet'],
        "시작": p['start_date'].strftime("%Y-%m-%d"),
        "종료": p['end_date'].strftime("%Y-%m-%d"),
        "기간(년)": f"{p['duration_years']:.6f}"
    } for p in level1['periods']])
    st.subheader("Level 1: Major Periods")
    st.dataframe(l1_df, use_container_width=True)

    # ==============================
    # Level 2, 3, 4 계산
    # ==============================
    target_date = datetime.combine(target_date_input, datetime.min.time())

    # Level 2
    level2_all = [{
        'parent': l1['planet'],
        'periods': calculate_sublevel(l1, level1['sequence'], l1['duration_years'])
    } for l1 in level1['periods']]

    # Level 3
    level3_all = []
    for blk in level2_all:
        for sub in blk['periods']:
            level3_all.append({
                'parent_l1': blk['parent'],
                'parent_l2': sub['planet'],
                'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years'])
            })

    # Level 4
    level4_all = []
    for blk in level3_all:
        for sub in blk['periods']:
            level4_all.append({
                'parent_l1': blk['parent_l1'],
                'parent_l2': blk['parent_l2'],
                'parent_l3': sub['planet'],
                'periods': calculate_sublevel(sub, level1['sequence'], sub['duration_years'])
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
                "Major(L1)": block['parent'],
                "Minor(L2)": sp['planet'],
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



