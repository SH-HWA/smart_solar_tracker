# app.py
# Flask REST API — DB 센서 데이터 제공 및 STM32 실시간 수집 (중복 수집 방지 필터 내장)

import threading
import time
from flask import Flask, jsonify, request, render_template_string
import serial
import db  # SQLite 제어를 담당하는 db.py 모듈을 가져옵니다.
import os  # 📌 [코드 추가] 파일 시스템의 실제 경로를 추적하기 위해 임포트

app = Flask(__name__)

# ── 시리얼 하드웨어 통신 설정 ────────────────────────────────────────────────────
SERIAL_PORT = "COM3"  
BAUD_RATE = 115200      
SENSOR_DEVICE_NAME = "STM32_NUCLEO"


# ── 백그라운드 문자열 파싱 및 실시간 저장 엔진 (독립 스레드) ──────────────────────
def watch_serial_and_insert():
    """
    STM32 보드로부터 데이터를 읽어옵니다.
    [해결] 직전 저장 값과 비교하여 '변화가 있을 때만' 1건씩 깨끗하게 저장합니다.
    """
    print(f"[시리얼] 포트 {SERIAL_PORT} 연결 시도 중...")

    ser = None
    while ser is None:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException:
            print(f"[시리얼 에러] {SERIAL_PORT} 포트가 닫혀있거나 사용 중입니다. 3초 후 재시도...")
            time.sleep(3)

    print(f"[시리얼] {SERIAL_PORT} 연결 성공. 실시간 데이터 중복 수집 필터 가동.")

    # 중복 저장 방지를 위한 상태 기억 변수 초기화
    last_inserted_lux = None
    last_inserted_angle = None

    while True:
        try:
            if ser.in_waiting > 0:
                # 1. STM32 패킷 수신 및 깨짐 방어
                raw_line = ser.readline().decode('utf-8', errors='ignore').strip()

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

                # 📌 [핵심 에러 해결] 중복 저장 검사 필터링 규칙
                # 조도와 각도 값이 직전에 저장한 데이터와 소수점까지 완전히 똑같다면 DB 저장을 건너뜁니다.
                if current_lux == last_inserted_lux and current_angle == last_inserted_angle:
                    # 불필요하게 3번씩 찍히는 중복 데이터 수신 잔여 버퍼 청소 후 패스
                    ser.reset_input_buffer()
                    continue

                # 3. 데이터베이스 적재 (db.py 구조에 맞춰 온/습도 필드 제거 매핑)
                rid = db.insert(
                    sensor=SENSOR_DEVICE_NAME,
                    lux=current_lux,
                    predicted_angle=current_angle
                )
                print(f"[실시간 저장 완료] ID: {rid} | 조도: {current_lux} Lux | 모터 각도: {current_angle}°")

                # 직전 성공 데이터로 상태 업데이트
                last_inserted_lux = current_lux
                last_inserted_angle = current_angle

                # 한 건 안전하게 저장 완료 후 쌓인 시리얼 버퍼 잔여물 강제 리셋 (중복 호출 방지 핵심)
                ser.reset_input_buffer()

        except Exception as e:
            print(f"[系统에러] 수집 루프 내 예외 발생: {e}")

        # 통신 오버랩 방지를 위한 여유 타임 딜레이
        time.sleep(0.1)


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
        return jsonify({
            "error": "start, end 파라미터가 필요합니다.",
            "example": "/api/range?start=2026-05-13 14:00:00&end=2026-05-13 15:00:00",
        }), 400

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
    return jsonify({
        "name": "STM32 IoT Solar Tracking API",
        "version": "1.4",
        "endpoints": {
            "GET /api/data": "최근 N건 조회",
            "GET /api/stats": "조도 전체 통계 데이터",
            "GET /api/latest": "가장 최근 데이터 1건 (온습도 완전 배제)",
            "GET /api/range": "특정 시간 범위 조회",
        },
    })


# ── 📌 [코드 추가] 상위 폴더 실행 대응 절대 경로 index.html 대시보드 라우팅 ──────
@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    기존 API 기능을 완전히 유지한 상태에서 추가된 웹 대시보드 엔드포인트입니다.
    터미널의 실행 위치와 무관하게 app.py 파일이 위치한 실제 디렉토리를 찾아 index.html을 로드합니다.
    """
    try:
        # app.py의 절대 물리적 위치를 기준으로 index.html의 전체 경로 생성
        current_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = os.path.join(current_dir, "index.html")
        
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return render_template_string(html_content)
    except FileNotFoundError:
        return jsonify({
            "error": "index.html 파일을 찾을 수 없습니다.",
            "resolved_path": index_path,
            "guide": "app.py와 완벽히 같은 위치(폴더)에 index.html 파일이 저장되어 있는지 확인해 주세요."
        }), 404


if __name__ == "__main__":
    db.init_db()

    collector_thread = threading.Thread(target=watch_serial_and_insert, daemon=True)
    collector_thread.start()

    print("[Flask] API 서버 및 모니터링 관제반 가동 중: http://localhost:5000")
    print("[대시보드] 웹 브라우저를 열고 다음 주소로 접속하세요: http://localhost:5000/dashboard\n")
    app.run(host="0.0.0.0", port=5000, debug=False)