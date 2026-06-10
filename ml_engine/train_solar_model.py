import os
import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from sklearn.ensemble import RandomForestRegressor
from scipy.interpolate import interp1d

# 1. 3개년 방위각 원본 CSV 데이터 로드
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
csv_filename = os.path.join(BASE_DIR, "solar_azimuth_3years.csv")
try:
    df_raw = pd.read_csv(csv_filename)
    print(f"원본 CSV 로드 완료! (총 {len(df_raw)}일 치 수평 데이터)")
except FileNotFoundError:
    print(f"에러: {csv_filename} 파일이 없습니다. 수집 코드를 먼저 실행해주세요.")
    exit()

# 2. 5분 단위 시간 보간 (Data Augmentation)
print(
    "09시/12시/15시 기준점을 바탕으로 활동 시간(06시~18시)을 5분 단위 곡선으로 생성 중..."
)
augmented_data = []

for _, row in df_raw.iterrows():
    month = int(row["월"])
    day = int(row["일"])

    # 알려진 3개 시점의 분 단위 타임스탬프 (00:00 기준 경과 분)
    # 09:00 -> 540분, 12:00 -> 720분, 15:00 -> 900분
    known_minutes = np.array([540, 720, 900])
    known_azimuths = np.array(
        [row["방위각_09시"], row["방위각_12시"], row["방위각_15시"]]
    )

    # 2차 곡선(quadratic) 스플라인 보간 함수
    interp_func = interp1d(
        known_minutes, known_azimuths, kind="quadratic", fill_value="extrapolate"
    )

    # 아침 6시(360분)부터 저녁 6시(1080분)까지 5분 간격 배열 생성
    target_minutes = np.arange(360, 1081, 5)
    interpolated_azimuths = interp_func(target_minutes)

    # 5분 간격 레코드를 학습용 리스트에 적재
    for current_min, az_val in zip(target_minutes, interpolated_azimuths):
        hour = current_min // 60
        minute = current_min % 60

        augmented_data.append(
            {
                "월": month,
                "일": day,
                "시": hour,
                "분": minute,
                "방위각": round(float(az_val), 2),
            }
        )

# 증강 완료된 데이터프레임 생성
df_augmented = pd.DataFrame(augmented_data)
print(
    f"데이터 증강 완료! 데이터셋 크기: {len(df_augmented):,} 행 (5분 제어 주기 최적화)"
)

# 3. 피처(X)와 정답지(y) 분리
X = df_augmented[["월", "일", "시", "분"]]  # 입력 특성
y = df_augmented["방위각"]  # 타겟 정답

# 4. 랜덤 포레스트 모델 생성 및 훈련 (fit)
print("\n1축 추적 랜덤 포레스트 회귀 모델 학습 시작...")
start_time = datetime.now()

# 병렬 연산(n_jobs=-1)을 활용해 초고속 훈련
model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
model.fit(X, y)

end_time = datetime.now()
print(f"모델 훈련 완료! (소요 시간: {end_time - start_time})")

# 5. 완성된 모델을 .pkl 파일로 내보내기 (저장)
model_filename = "solar_model.pkl"
joblib.dump(model, os.path.join(BASE_DIR, model_filename))

print(f"\n태양 수평 추적을 위한 학습된 파일 배포 준비 완료!")
print(f"\n생성된 파일: {model_filename}")
