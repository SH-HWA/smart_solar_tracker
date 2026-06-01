import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

# API 호출 관련 환경변수 등 설정
load_dotenv()
SERVICE_KEY = os.environ.get("SUN_API_KEY")
URL = os.environ.get("SUN_ALT_API_URL")
LOC_NAME = "서울"

# 조회대상 날짜 범위 설정
current_year = datetime.now().year

target_start_year = current_year - 3
target_end_year = current_year - 1

start_date = datetime(target_start_year, 1, 1)
end_date = datetime(target_end_year, 12, 31)
delta = timedelta(days=1)

## 직전 3개년치 날짜 리스트(YYYYMMDD) 생성
date_list = []
current_date = start_date
while current_date <= end_date:
    date_list.append(current_date.strftime("%Y%m%d"))
    current_date += delta

print(f"[REST API] {LOC_NAME} 지역 데이터 수집을 시작합니다.")
print(
    f"수집 대상 기간: {target_start_year}년 ~ {target_end_year}년 (직전 3개년, 총 {len(date_list)}일 치)"
)
all_days_data = []


# [데이터 전처리 함수]
def parse_azimuth_value(val_str):
    if not val_str or val_str.strip() == "":
        return 0.0
    try:
        # 공백 제거 후 '도(˚)'와 '분(´)' 기준 쪼개기
        val_str = val_str.replace(" ", "")
        deg_part = val_str.split("˚")[0]
        min_part = val_str.split("˚")[1].split("´")[0]

        # 음수 각도 예외 처리
        is_negative = False
        if "-" in deg_part:
            is_negative = True
            deg_part = deg_part.replace("-", "")

        deg = float(deg_part) if deg_part else 0.0
        mn = float(min_part) if min_part else 0.0

        # 공식: 도 + (분 / 60)
        decimal_value = deg + (mn / 60.0)
        decimal_value = round(decimal_value, 2)

        return -decimal_value if is_negative else decimal_value
    except Exception:
        return 0.0


# 3개년 루프 가동
for idx, target_date in enumerate(date_list):

    params = {
        "ServiceKey": SERVICE_KEY,
        "location": LOC_NAME,
        "locdate": target_date,
        "_type": "json",
    }

    try:
        response = requests.get(URL, params=params, timeout=10)

        if response.status_code == 200:
            data_json = response.json()

            try:
                item = data_json["response"]["body"]["items"]["item"]

                day_record = {
                    "조회날짜": target_date,
                    "연도": int(target_date[:4]),
                    "월": int(target_date[4:6]),
                    "일": int(target_date[6:]),
                    # 고도는 과감히 생략하고 수평 서보모터 제어용 방위각만 정제하여 적재
                    "방위각_09시": parse_azimuth_value(item.get("azimuth_09")),
                    "방위각_12시": parse_azimuth_value(item.get("azimuth_12")),
                    "방위각_15시": parse_azimuth_value(item.get("azimuth_15")),
                }
                all_days_data.append(day_record)

            except (KeyError, TypeError):
                print(f"[{target_date}] 데이터 누락 또는 파싱 실패 (Skipped)")
        else:
            print(f"[{target_date}] 서버 응답 에러 (Status: {response.status_code})")

    except Exception as e:
        print(f"[{target_date}] 네트워크 통신 오류: {e}")

    # API 서버 안정성을 위한 미세 딜레이
    time.sleep(0.05)

    # 100일 단위 진행률 브리핑
    if (idx + 1) % 100 == 0 or (idx + 1) == len(date_list):
        print(f" 🟩 데이터 수집 진행률: {idx + 1}/{len(date_list)} 일 완료")

# 판다스를 이용한 CSV 최종 저장
if all_days_data:
    df = pd.DataFrame(all_days_data)
    filename = "solar_azimuth_3years.csv"

    # 한글 깨짐 방지를 위해 인코딩은 utf-8-sig 적용
    df.to_csv(filename, index=False, encoding="utf-8-sig")

    print(f"\n==================================================")
    print(f"🎉 1축 추적용 3개년 방위각 데이터셋 빌드 성공!")
    print(f"📁 파일 저장 위치: {filename}")
    print(f"📊 총 저장 데이터 행 수: {len(df)}행")
    print(f"==================================================")
else:
    print("\n🚨 데이터 적재에 실패했습니다. 수집된 내용이 비어있습니다.")
