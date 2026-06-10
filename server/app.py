# app.py
# Flask REST API — DB 센서 데이터 제공 및 STM32 실시간 수집

from datetime import datetime
import os
import threading
import time
from flask import Flask, jsonify, render_template_string, request
import db
import joblib
import serial
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# ── 시리얼 하드웨어 통신 설정 ────────────────────────────────────────────────────
SERIAL_PORT = os.environ.get("SERIAL_PORT")
BAUD_RATE = 115200
SENSOR_DEVICE_NAME = "STM32_NUCLEO"

# 전역 변수 관리
ser = None
PREDICT_INTERVAL = 60.0  # AI 추론 주기 (초)
COLLECT_INTERVAL = 15.0  # 센서 수집 주기 (초)
CURRENT_MODE = "NORMAL"
VIRTUAL_HOUR = 8  # 시연 모드 가상 시각

# 제어-수집 sync를 위한 전역 각도 관리 변수
LIVE_TARGET_ANGLE = 0.0
IS_FIRST_SENT = False

CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(CURRENT_FILE_DIR)
MODEL_PATH = os.path.join(PROJECT_ROOT_DIR, "ml_engine", "solar_model.pkl")

# ML모델 로드
try:
    solar_model = joblib.load(MODEL_PATH)
    print(f"[ML Engine] 모델 로드 성공! ({MODEL_PATH})")
except FileNotFoundError:
    print(f"[에러] {MODEL_PATH} 파일 없음.")
    solar_model = None


# ── 모델을 통한 추론 후 5도 단위 변환 및 STM32 제어 송신 스레드 ──
def predict_and_send_stm32():
    global ser, PREDICT_INTERVAL, CURRENT_MODE, VIRTUAL_HOUR, IS_FIRST_SENT, LIVE_TARGET_ANGLE
    # 직전 전송한 모터 각도를 기억하여, 값이 바뀔 때만 시리얼 전송
    last_sent_servo_angle = None

    while True:
        try:
            if solar_model is not None and ser is not None and ser.is_open:
                # 1. 현재 시각 로드
                if CURRENT_MODE == "DEMO (FAST)":
                    now = datetime.now()
                    month, day = now.month, now.day
                    hour = VIRTUAL_HOUR
                    minute = 0
                else:
                    now = datetime.now()
                    month, day, hour, minute = (
                        now.month,
                        now.day,
                        now.hour,
                        now.minute,
                    )

                # 2. 모델을 통한 방위각 추론 (0도 ~ 360도 사이 결과 반환)
                prediction = solar_model.predict([[month, day, hour, minute]])
                raw_azimuth = float(prediction[0])

                # 3. 물리적 서보모터 각도(0~180도)로 매핑 계산 (동쪽=180 / 서쪽=0)
                if raw_azimuth <= 90.0:
                    servo_angle = 180.0
                elif raw_azimuth >= 270.0:
                    servo_angle = 0.0
                else:
                    # 동(90)~서(270) 범위를 모터(180~0) 범위로 1:1 선형 반전 매핑
                    servo_angle = 180.0 - (
                        (raw_azimuth - 90.0) * 180.0 / (270.0 - 90.0)
                    )

                # 4. 5도 단위로 반올림 조정 및 170도 상한선 제한
                # 예: 82.4도 -> 80.0도 / 83.1도 -> 85.0도
                rounded_servo_angle = int(round(servo_angle / 5) * 5)
                rounded_servo_angle = max(0, min(rounded_servo_angle, 170))

                # 5. 시연모드이거나 값이 실제로 변했을 때만 STM32로 시리얼 데이터 전송
                if (
                    CURRENT_MODE == "DEMO (FAST)"
                    or rounded_servo_angle != last_sent_servo_angle
                ):
                    packet = f"SERVO:{rounded_servo_angle}\n"
                    ser.write(packet.encode("utf-8"))
                    ser.flush()

                    # 대시보드 및 수집 스레드가 바라볼 실제 태양 기준 각도 동기화
                    LIVE_TARGET_ANGLE = 180.0 - float(rounded_servo_angle)

                    mode_prefix = (
                        "[시연 타임랩스]"
                        if CURRENT_MODE == "DEMO (FAST)"
                        else "[일반 제어]"
                    )
                    print(
                        f"{mode_prefix} 시각: {hour:02d}:{minute:02d} | 방위각: {raw_azimuth:.2f}° ➔ 추적 목표 각도: {LIVE_TARGET_ANGLE:.1f}° 전송 완료"
                    )
                    last_sent_servo_angle = rounded_servo_angle
                    IS_FIRST_SENT = True

                # 시연모드용 시간 흐름 처리
                if CURRENT_MODE == "DEMO (FAST)":
                    VIRTUAL_HOUR += 1
                    if VIRTUAL_HOUR > 18:
                        VIRTUAL_HOUR = 8

        except Exception as e:
            print(f"[송신 에러] 루프 내 예외 발생: {e}")

        starting_mode = CURRENT_MODE
        elapsed = 0.0
        while elapsed < PREDICT_INTERVAL:
            time.sleep(0.2)
            elapsed += 0.2

            # 1. 자는 도중에 시연 모드로 바뀌었거나 (60초 자다가 즉시 깨어남)
            # 2. 자는 도중에 일반 모드로 복귀했다면 (6초 자다가 즉시 깨어남)
            if CURRENT_MODE != starting_mode:
                break


# ── 백그라운드 문자열 파싱 및 실시간 저장 엔진 (독립 스레드) ──────────────────────
def watch_serial_and_insert():
    global ser, COLLECT_INTERVAL, IS_FIRST_SENT, LIVE_TARGET_ANGLE
    print(f"[시리얼] 포트 {SERIAL_PORT} 연결 시도 중...")

    while ser is None:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException:
            print(f"[시리얼 에러] {SERIAL_PORT} 사용 중. 3초 후 재시도...")
            time.sleep(3)

    print(f"[시리얼] {SERIAL_PORT} 연결 성공.")

    # 중복 저장 방지를 위한 상태 기억 변수 초기화
    last_inserted_lux = None
    last_inserted_angle = None

    while True:
        try:
            if not IS_FIRST_SENT:
                time.sleep(0.1)
                continue

            if ser.is_open and ser.in_waiting > 0:
                # 1. STM32 패킷 수신 및 깨짐 방어
                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not raw_line:
                    continue

                for char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ=:|":
                    raw_line = raw_line.replace(char, "")

                clean_line = "".join(
                    [c for c in raw_line if c.isdigit() or c in [".", "-", ","]]
                )
                tokens = [t for t in clean_line.replace(",", " ").split() if t]

                if len(tokens) < 1:
                    continue
                # 2. 값 대입 및 변환 처리
                try:
                    current_lux = float(tokens[0])
                except ValueError:
                    continue

                current_angle = LIVE_TARGET_ANGLE

                # 중복 저장 방지 필터
                if (
                    current_lux == last_inserted_lux
                    and current_angle == last_inserted_angle
                ):
                    ser.reset_input_buffer()
                    continue

                # 3. 데이터베이스 적재
                # 현재 모드가 시연(DEMO) 모드라면 DB에 가짜 데이터를 넣지 않고 콘솔 출력만 수행
                if CURRENT_MODE == "DEMO (FAST)":
                    print(
                        f"[시연 데이터 모니터링] 조도: {current_lux:<7} Lux | 모터 각도: {current_angle}° (DB저장 x)"
                    )
                else:
                    # 일반 모드일 때만 데이터베이스에 실제 데이터를 적재
                    rid = db.insert(
                        sensor=SENSOR_DEVICE_NAME,
                        lux=current_lux,
                        predicted_angle=current_angle,
                    )
                    print(
                        f"[실시간 저장 완료] ID: {rid:<4} | 조도: {current_lux:<7} Lux | 모터 각도: {current_angle}°"
                    )

                last_inserted_lux = current_lux
                last_inserted_angle = current_angle
                ser.reset_input_buffer()

        except Exception as e:
            print(f"[시스템에러] 수집 루프 내 예외 발생: {e}")

        time.sleep(COLLECT_INTERVAL)


# ── 시연 모드 전환 스위치 API ──────────────────────
@app.route("/api/mode", methods=["GET"])
def change_system_mode():
    global PREDICT_INTERVAL, COLLECT_INTERVAL, CURRENT_MODE, VIRTUAL_HOUR, IS_FIRST_SENT

    mode_type = request.args.get("type", "normal").lower()

    if mode_type == "demo":
        IS_FIRST_SENT = False  # 제어 우선순위 확보를 위해 플래그 리셋
        PREDICT_INTERVAL = 6.0
        COLLECT_INTERVAL = 3.0
        CURRENT_MODE = "DEMO (FAST)"
        VIRTUAL_HOUR = 8  # 무조건 아침 8시부터 가속 시작
    else:
        IS_FIRST_SENT = False
        PREDICT_INTERVAL = 60.0
        COLLECT_INTERVAL = 15.0
        CURRENT_MODE = "NORMAL"

    return jsonify(
        {
            "status": "success",
            "changed_mode": CURRENT_MODE,
            "ai_predict_interval_seconds": PREDICT_INTERVAL,
            "db_collect_interval_seconds": COLLECT_INTERVAL,
            "message": f"시스템이 {CURRENT_MODE} 모드로 즉각 전환되었습니다. 타임랩스가 구동됩니다.",
        }
    )


@app.route("/api/predict", methods=["GET"])
def predict_current_azimuth():
    if solar_model is None:
        return jsonify({"status": "error", "message": "⚠️ 모델 로드 실패"}), 500
    now = datetime.now()
    month, day, hour, minute = now.month, now.day, now.hour, now.minute
    prediction = solar_model.predict([[month, day, hour, minute]])
    predicted_angle = round(float(prediction[0]), 2)
    return jsonify(
        {
            "status": "success",
            "server_time": f"{month}월 {day}일 {hour}시 {minute}분",
            "predicted_azimuth": predicted_angle,
        }
    )


# ── [GET] /api/data ─────────────────────────────────────────────────────────
@app.route("/api/data", methods=["GET"])
def get_data():
    return jsonify({"data": db.fetch_recent(request.args.get("limit", 20, type=int))})


# ── [GET] /api/stats ────────────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def get_stats():
    return jsonify(db.fetch_stats())


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
    return jsonify(rows[0]) if rows else (jsonify({"error": "No data"}), 404)


# ── [GET] / ─────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "name": "STM32 IoT Solar Tracking API",
            "version": "2.2 (Instant Scheduling Synced)",
            "endpoints": {
                "GET /": "API 안내 정보",
                "GET /api/predict": "현재 시각 기준 태양 위치 추론",
                "GET /api/data": "최근 N건 조회",
                "GET /api/stats": "조도 전체 통계 데이터",
                "GET /api/latest": "가장 최근 데이터 1건",
                "GET /api/mode?type=demo": "시연 모드 전환",
                "GET /api/mode?type=normal": "일반 모드 복귀",
                "GET /dashboard": "종합 웹 관제 대시보드",
            },
        }
    )


@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(current_dir, "index.html"), "r", encoding="utf-8") as f:
            return render_template_string(f.read())
    except FileNotFoundError:
        return jsonify({"error": "index.html not found"}), 404


if __name__ == "__main__":
    db.init_db()
    threading.Thread(target=watch_serial_and_insert, daemon=True).start()
    time.sleep(0.5)
    threading.Thread(target=predict_and_send_stm32, daemon=True).start()
    print("[Flask] API 서버 및 모니터링 관제반 가동 중: http://localhost:5000")
    print(
        "[대시보드] 웹 브라우저를 열고 다음 주소로 접속하세요: http://localhost:5000/dashboard\n"
    )
    app.run(host="0.0.0.0", port=5000, debug=False)
