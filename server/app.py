# app.py
# Flask REST API — DB 센서 데이터 제공 및 STM32 실시간 수집 (중복 수집 방지 필터 내장)

from datetime import datetime
import os  # 📌 파일 시스템의 실제 경로를 추적하기 위해 임포트
import threading
import time
from flask import Flask, jsonify, render_template_string, request
import db  # SQLite 제어를 담당하는 db.py 모듈을 가져옵니다.
import joblib
import serial

app = Flask(__name__)

# ── 시리얼 하드웨어 통신 설정 ────────────────────────────────────────────────────
SERIAL_PORT = "COM3"  # 연결하는 PC의 환경 정보 확인 필요
BAUD_RATE = 115200
SENSOR_DEVICE_NAME = "STM32_NUCLEO"

# 전역 시리얼 객체를 공유하여 수집과 제어신호 송신 스레드가 함께 사용합니다.
ser = None

CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(CURRENT_FILE_DIR)
MODEL_PATH = os.path.join(PROJECT_ROOT_DIR, "ml_engine", "solar_model.pkl")

try:
    # 서버 기동 시 인공지능 모델을 딱 한 번만 메모리에 올립니다.
    solar_model = joblib.load(MODEL_PATH)
    print(f"[ML Engine] 상위 폴더를 거쳐 모델 로드 성공! ({MODEL_PATH})")
except FileNotFoundError:
    print(f"[에러] {MODEL_PATH} 경로에서 모델 파일을 찾을 수 없습니다.")
    print("ml_engine 폴더 내에 solar_model.pkl 파일이 생성되어 있는지 확인해 주세요.")
    solar_model = None  # 에러로 인한 서버 전체 크래시 방지


# ── 📌 [코드 추가] 모델을 통한 추론 후 5도 단위 변환 및 STM32 제어 송신 스레드 ──────────
def predict_and_send_stm32():
    """
    1분 주기로 현재 시간의 태양 방위각을 예측하고,
    이를 5도 단위 서보모터 각도(0~170도)로 변환하여 STM32에 패킷을 전송합니다.
    """
    global ser

    # 직전 전송한 모터 각도를 기억하여, 값이 바뀔 때만 시리얼 전송 (모터 무리 방지)
    last_sent_servo_angle = None

    while True:
        try:
            # 모델이 로드되었고, 시리얼 포트가 정상적으로 열려있을 때만 작동
            if solar_model is not None and ser is not None and ser.is_open:
                # 1. 현재 시각 구하기
                now = datetime.now()
                month, day, hour, minute = now.month, now.day, now.hour, now.minute

                # 2. 모델을 통한 방위각 추론 (0도 ~ 360도 사이 결과 반환)
                prediction = solar_model.predict([[month, day, hour, minute]])
                raw_azimuth = float(prediction[0])

                # 3. 물리적 서보모터 각도(0~170도)로 매핑 계산
                # 여름철 새벽/저녁 범위 경계 설정 (동쪽 90도 미만은 0도, 서쪽 270도 초과는 170도 고정)
                if raw_azimuth <= 90.0:
                    servo_angle = 0.0
                elif raw_azimuth >= 270.0:
                    servo_angle = 170.0
                else:
                    # 동(90)~서(270) 범위를 모터(0~170) 범위로 정밀 비례 변환
                    servo_angle = (raw_azimuth - 90.0) * 170.0 / (270.0 - 90.0)

                # 4. 5도 단위로 반올림 조정 및 170도 상한선 제한
                # 예: 82.4도 -> 80.0도 / 83.1도 -> 85.0도
                rounded_servo_angle = int(round(servo_angle / 5) * 5)
                rounded_servo_angle = max(0, min(rounded_servo_angle, 170))

                # 5. 값이 실제로 변했을 때만 STM32로 시리얼 데이터 전송
                if rounded_servo_angle != last_sent_servo_angle:
                    # STM32가 파싱하기 좋게 헤더와 줄바꿈(\n)을 붙여 문자열 생성 (예: "SERVO:90\n")
                    packet = f"SERVO:{rounded_servo_angle}\n"
                    ser.write(packet.encode("utf-8"))

                    print(
                        f"[제어 송신] 시각: {hour:02d}:{minute:02d} | 방위각: {raw_azimuth:.2f}° ➔ 서보 변환 각도: {rounded_servo_angle}° 전송 완료"
                    )
                    last_sent_servo_angle = rounded_servo_angle

        except Exception as e:
            print(f"[송신 에러] 루프 내 예외 발생: {e}")

        # 1분(60초) 간격으로 태양 위치 추적 및 모터 상태 연산
        time.sleep(60)


# ── 백그라운드 문자열 파싱 및 실시간 저장 엔진 (독립 스레드) ──────────────────────
def watch_serial_and_insert():
    """
    STM32 보드로부터 데이터를 읽어옵니다.
    [해결] 직전 저장 값과 비교하여 '변화가 있을 때만' 1건씩 깨끗하게 저장합니다.
    """
    global ser  # 전역 변수 ser를 사용하도록 명시
    print(f"[시리얼] 포트 {SERIAL_PORT} 연결 시도 중...")

    while ser is None:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException:
            print(
                f"[시리얼 에러] {SERIAL_PORT} 포트가 닫혀있거나 사용 중입니다. 3초 후 재시도..."
            )
            time.sleep(3)

    print(f"[시리얼] {SERIAL_PORT} 연결 성공. 실시간 데이터 중복 수집 필터 가동.")

    # 중복 저장 방지를 위한 상태 기억 변수 초기화
    last_inserted_lux = None
    last_inserted_angle = None

    while True:
        try:
            if ser.in_waiting > 0:
                # 1. STM32 패킷 수신 및 깨짐 방어
                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()

                if not raw_line:
                    continue

                # 이름 찌꺼기 텍스트 제거 및 기호 필터링
                for char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ=:":
                    raw_line = raw_line.replace(char, "")

                # 숫자, 소수점, 마이너스, 구분 공백/콤마만 필터링
                clean_line = "".join(
                    [c for c in raw_line if c.isdigit() or c in [".", "-", ",", " "]]
                )

                # 공백이나 콤마 기준으로 조도값과 각도값 분리
                tokens = [t for t in clean_line.replace(",", " ").split() if t]

                if not tokens:
                    continue

                current_lux = 0.0
                raw_pwm_value = 0.0

                # 2. 값 대입 및 변환 처리
                try:
                    if len(tokens) >= 2:
                        current_lux = float(tokens[0])
                        raw_pwm_value = float(tokens[1])
                    else:
                        current_lux = float(tokens[0])
                        latest_rows = db.fetch_recent(1)
                        if latest_rows:
                            current_angle = latest_rows[0]["predicted_angle"] or 0.0
                            raw_pwm_value = 500.0 + (current_angle * (1000.0 / 170.0))
                        else:
                            raw_pwm_value = 500.0
                except ValueError:
                    continue

                # STM32의 500 ~ 1500 범위를 0도 ~ 170도로 맵핑 연산
                raw_pwm_value = max(500.0, min(raw_pwm_value, 1500.0))
                current_angle = (raw_pwm_value - 500.0) * 170.0 / 1000.0
                current_angle = round(current_angle, 1)

                # 조도와 각도 값이 직전에 저장한 데이터와 소수점까지 완전히 똑같다면 DB 저장을 건너뜁니다.
                # 중복 저장 검사 필터링 규칙
                if (
                    current_lux == last_inserted_lux
                    and current_angle == last_inserted_angle
                ):
                    ser.reset_input_buffer()
                    continue

                # 3. 데이터베이스 적재
                rid = db.insert(
                    sensor=SENSOR_DEVICE_NAME,
                    lux=current_lux,
                    predicted_angle=current_angle,
                )
                print(
                    f"[실시간 저장 완료] ID: {rid} | 조도: {current_lux} Lux | 모터 각도: {current_angle}°"
                )

                # 직전 성공 데이터로 상태 업데이트
                last_inserted_lux = current_lux
                last_inserted_angle = current_angle

                ser.reset_input_buffer()

        except Exception as e:
            print(f"[시스템에러] 수집 루프 내 예외 발생: {e}")

        time.sleep(0.1)


# 호출한 순간의 현재 시각 기반 방위각 추론
@app.route("/api/predict", methods=["GET"])
def predict_current_azimuth():
    """
    호출된 그 순간의 서버 컴퓨터 시스템 시각(월, 일, 시, 분)을 감지하여
    랜덤 포레스트 모델로 예측한 실시간 태양 방위각을 반환합니다.
    """
    if solar_model is None:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "⚠️ 서버에 모델(pkl)이 정상적으로 로드되지 않았습니다.",
                }
            ),
            500,
        )

    # 현재 시각 자동 연산
    now = datetime.now()
    month, day, hour, minute = now.month, now.day, now.hour, now.minute

    # 모델 입력 데이터 레이아웃 빌드 [[월, 일, 시, 분]]
    input_features = [[month, day, hour, minute]]

    # 추론 수행 및 소수점 둘째 자리 반올림
    prediction = solar_model.predict(input_features)
    predicted_angle = round(float(prediction[0]), 2)

    return jsonify(
        {
            "status": "success",
            "server_time": f"{month}월 {day}일 {hour}시 {minute}분",
            "predicted_azimuth": predicted_angle,
            "guide": "이 방위각 데이터를 STM32 시리얼 송신 또는 프론트 대시보드에 연동하세요.",
        }
    )


# ── [GET] /api/data ─────────────────────────────────────────────────────────
@app.route("/api/data", methods=["GET"])
def get_data():
    limit = request.args.get("limit", 20, type=int)
    limit = max(1, min(limit, 500))
    rows = db.fetch_recent(limit)
    return jsonify({"count": len(rows), "data": rows})


# ── [GET] /api/stats ────────────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def get_stats():
    stats = db.fetch_stats()
    return jsonify(stats)


# ── [GET] /api/range ────────────────────────────────────────────────────────
@app.route("/api/range", methods=["GET"])
def get_range():
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        return (
            jsonify(
                {
                    "error": "start, end 파라미터가 필요합니다.",
                    "example": "/api/range?start=2026-05-13 14:00:00&end=2026-05-13 15:00:00",
                }
            ),
            400,
        )

    rows = db.fetch_by_range(start, end)
    return jsonify({"count": len(rows), "start": start, "end": end, "data": rows})


# ── [GET] /api/latest ───────────────────────────────────────────────────────
@app.route("/api/latest", methods=["GET"])
def get_latest():
    rows = db.fetch_recent(1)
    if not rows:
        return jsonify({"error": "데이터가 존재하지 않습니다."}), 404
    return jsonify(rows[0])


# ── [GET] / ─────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "name": "STM32 IoT Solar Tracking API",
            "version": "1.5 (ML Engine Integrated)",
            "endpoints": {
                "GET /": "API 안내 정보",
                "GET /api/predict": "현재 시각 기준 태양 위치 추론",
                "GET /api/data": "최근 N건 조회",
                "GET /api/stats": "조도 전체 통계 데이터",
                "GET /api/latest": "가장 최근 데이터 1건 (온습도 완전 배제)",
                "GET /api/range": "특정 시간 범위 조회",
                "GET /dashboard": "종합 웹 관제 대시보드",
            },
        }
    )


# ── 상위 폴더 실행 대응 절대 경로 index.html 대시보드 라우팅 ──────
@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = os.path.join(current_dir, "index.html")

        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return render_template_string(html_content)
    except FileNotFoundError:
        return (
            jsonify(
                {
                    "error": "index.html 파일을 찾을 수 없습니다.",
                    "resolved_path": index_path,
                    "guide": "app.py와 완벽히 같은 위치(폴더)에 index.html 파일이 저장되어 있는지 확인해 주세요.",
                }
            ),
            404,
        )


if __name__ == "__main__":
    db.init_db()

    # STM32 보드 통신 수집용 독립 스레드 가동
    collector_thread = threading.Thread(target=watch_serial_and_insert, daemon=True)
    collector_thread.start()

    # 모델을 통한 추론 및 STM32 모터 제어 신호 송신용 독립 스레드 가동
    sender_thread = threading.Thread(target=predict_and_send_stm32, daemon=True)
    sender_thread.start()

    print("[Flask] API 서버 및 모니터링 관제반 가동 중: http://localhost:5000")
    print(
        "[대시보드] 웹 브라우저를 열고 다음 주소로 접속하세요: http://localhost:5000/dashboard\n"
    )
    app.run(host="0.0.0.0", port=5000, debug=False)
