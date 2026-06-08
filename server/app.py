# app.py
# Flask REST API — DB 센서 데이터 제공, STM32 실시간 수집 및 AI 태양 추적 제어 (시연용 10초 최적화 버전)

from datetime import datetime
import os
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string
import serial
import db  # SQLite 제어를 담당하는 db.py 모듈

app = Flask(__name__)

# ── 시스템 전역 통신 및 모델 변수 설정 ──────────────────────────────────────────
SERIAL_PORT = "COM3"
BAUD_RATE = 115200
SENSOR_DEVICE_NAME = "STM32_NUCLEO"

ser = None
solar_model = None  # 📌 메인 코드 어딘가에서 학습된 모델(solar_model)을 로드하고 있다고 가정합니다.


# ── [스레드 1] AI 방위각 추론 및 STM32 모터 제어 송신 루프 ─────────────────────
def predict_and_send_stm32():
    """
    [시연용 최적화 반영]
    10초 주기로 현재 시간의 태양 방위각을 예측하고,
    이를 서보모터 각도(0~180도)로 변환하여 STM32에 패킷을 전송합니다.
    """
    global ser, solar_model

    print("[AI 제어 엔진] 태양 위치 추론 및 모터 제어 스레드가 가동되었습니다.")

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

                # 3. 물리적 서보모터 각도 매핑 계산 (0~180도 최신 규격 반영)
                # 여름철 새벽/저녁 범위 경계 설정 (동쪽 90도 미만은 0도, 서쪽 270도 초과는 180도 고정)
                if raw_azimuth <= 90.0:
                    servo_angle = 0.0
                elif raw_azimuth >= 270.0:
                    servo_angle = 180.0
                else:
                    # 동(90)~서(270) 범위를 모터(0~180) 범위로 정밀 비례 변환
                    servo_angle = (raw_azimuth - 90.0) * 180.0 / (270.0 - 90.0)

                # 4. 5도 단위로 반올림 조정 및 180도 상한선 제한 (시연 시 시각적 움직임을 강조하기 위함)
                rounded_servo_angle = int(round(servo_angle / 5) * 5)
                rounded_servo_angle = max(0, min(rounded_servo_angle, 180))

                # 5. 값이 실제로 변했을 때만 STM32로 시리얼 데이터 전송
                if rounded_servo_angle != last_sent_servo_angle:
                    # STM32가 파싱하기 좋게 헤더와 줄바꿈(\n)을 붙여 문자열 생성 (예: "SERVO:90\n")
                    packet = f"SERVO:{rounded_servo_angle}\n"
                    ser.write(packet.encode("utf-8"))

                    print(
                        f"[AI 제어 송신] 시각: {hour:02d}:{minute:02d} | 방위각: {raw_azimuth:.2f}° ➔ 서보 변환 각도: {rounded_servo_angle}° 전송 완료"
                    )
                    last_sent_servo_angle = rounded_servo_angle

        except Exception as e:
            print(f"[송신 에러] 루프 내 예외 발생: {e}")

        # 📌 [시연 최적화 핵심] 눈앞에서 팍팍 변하게 하기 위해 추적 주기를 60초에서 10초로 변경!
        time.sleep(10)


# ── [스레드 2] 백그라운드 문자열 파싱 및 실시간 저장 엔진 ──────────────────────
def watch_serial_and_insert():
    """
    STM32 보드로부터 데이터를 읽어와서 시연용 10초 주기로 DB에 적재합니다.
    """
    global ser
    print(f"[시리얼] 포트 {SERIAL_PORT} 연결 시도 중...")

    while ser is None:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException:
            print(
                f"[시리얼 에러] {SERIAL_PORT} 포트가 닫혀있거나 사용 중입니다. 3초 후 재시도..."
            )
            time.sleep(3)

    print(f"[시리얼] {SERIAL_PORT} 연결 성공. 시연용 10초당 1회 DB 저장 필터 가동.")

    # 10초 주기 제어를 위한 시간 기록 변수
    last_saved_time = 0.0

    while True:
        try:
            if ser.in_waiting > 0:
                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()

                if not raw_line:
                    continue

                for char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ=:|":
                    raw_line = raw_line.replace(char, "")

                clean_line = "".join(
                    [c for c in raw_line if c.isdigit() or c in [".", "-", ",", " "]]
                )
                tokens = [t for t in clean_line.replace(",", " ").split() if t]

                if not tokens:
                    continue

                current_lux = 0.0
                current_angle = 0.0

                try:
                    if len(tokens) >= 2:
                        current_lux = float(tokens[0])
                        incoming_val = float(tokens[1])

                        # 하드웨어 500 ~ 2500 스펙 역연산 (최신 180도 대응 공식)
                        if incoming_val >= 500.0:
                            raw_pwm_value = max(500.0, min(incoming_val, 2500.0))
                            current_angle = (raw_pwm_value - 500.0) * 180.0 / 2000.0
                        else:
                            current_angle = incoming_val
                    else:
                        current_lux = float(tokens[0])
                        latest_rows = db.fetch_recent(1)
                        if latest_rows:
                            current_angle = latest_rows[0]["predicted_angle"] or 0.0
                        else:
                            current_angle = 85.0
                except ValueError:
                    continue

                current_angle = max(0.0, min(current_angle, 180.0))
                current_angle = round(current_angle, 1)

                # 시연용 10초 시간 필터 검사
                current_time = time.time()
                if current_time - last_saved_time < 10.0:
                    ser.reset_input_buffer()
                    continue

                # 데이터베이스 적재
                rid = db.insert(
                    sensor=SENSOR_DEVICE_NAME,
                    lux=current_lux,
                    predicted_angle=current_angle,
                )
                print(
                    f"[실시간 저장 완료] ID: {rid} | 조도: {current_lux} Lux | 모터 각도: {current_angle}°"
                )
                print(
                    f"[시연용 10초 저장 완료] ID: {rid} | 조도: {current_lux} Lux | 모터 각도: {current_angle}°"
                )

                last_saved_time = current_time
                ser.reset_input_buffer()

        except Exception as e:
            print(f"[시스템에러] 수집 루프 내 예외 발생: {e}")

        time.sleep(0.1)


# ── [GET] /api/data 등 REST API 웹 라우터 (기존 유지) ───────────────────────
@app.route("/api/data", methods=["GET"])
def get_data():
    limit = request.args.get("limit", 20, type=int)
    limit = max(1, min(limit, 500))
    rows = db.fetch_recent(limit)
    return jsonify({"count": len(rows), "data": rows})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    return jsonify(db.fetch_stats())


@app.route("/api/range", methods=["GET"])
def get_range():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "start, end 파라미터가 필요합니다."}), 400
    return jsonify(
        {
            "count": len(db.fetch_by_range(start, end)),
            "data": db.fetch_by_range(start, end),
        }
    )


@app.route("/api/latest", methods=["GET"])
def get_latest():
    rows = db.fetch_recent(1)
    if not rows:
        return jsonify({"error": "데이터가 존재하지 않습니다."}), 404
    return jsonify(rows[0])


@app.route("/", methods=["GET"])
def index():
    return jsonify({"name": "STM32 IoT Solar Tracking API", "version": "1.5"})


@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = os.path.join(current_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return render_template_string(html_content)
    except FileNotFoundError:
        return jsonify({"error": "index.html 파일을 찾을 수 없습니다."}), 404


# ── 메인 구동 부 (스레드 2개 동시 실행) ──────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()

    # 1. STM32로부터 데이터를 읽어서 DB에 넣는 수집 스레드 가동
    collector_thread = threading.Thread(target=watch_serial_and_insert, daemon=True)
    collector_thread.start()

    # 2. 🎯 [핵심 추가] AI가 태양 각도를 예측해서 STM32로 명령을 쏘는 제어 스레드 가동
    control_thread = threading.Thread(target=predict_and_send_stm32, daemon=True)
    control_thread.start()

    print("[Flask] API 서버 및 AI 이중 엔진 관제반 가동 중: http://localhost:5000")
    print("[대시보드] 웹 브라우저 접속 주소: http://localhost:5000/dashboard\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
