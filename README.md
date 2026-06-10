# smart_solar_tracker

STM32 MCU와 Python Flask, 그리고 머신러닝(Random Forest)을 결합하여 태양의 방위각을 실시간으로 예측하고 최적의 각도로 태양광 패널(서보모터)을 제어하는 지능형 추적 솔루션입니다.

## 🛠 Tech Stack

* **Hardware**: STM32 Nucleo, BH1750 (조도 센서), Servo Motor
* **Backend**: Python 3.x, Flask, PySerial, SQLite3
* **Machine Learning**: Scikit-learn (RandomForestRegressor), Pandas, NumPy, SciPy

## 🌟 Key Features

1. **머신러닝 기반 방위각 예측**
   * 일 3회(09시, 12시, 15시)의 태양 방위각 데이터를 2차 곡선 스플라인(Quadratic Spline) 보간법으로 5분 단위로 증강.
   * 증강된 데이터를 바탕으로 Random Forest 모델을 학습하여, 시간(월/일/시/분)에 따른 최적의 모터 각도를 추론.
2. **실시간 하드웨어 제어 및 통신 안정성**
   * STM32에서 UART 인터럽트를 통해 서버의 제어 명령(`SERVO:{angle}\n`)을 비동기 수신 및 PWM 제어.
   * 통신 노이즈로 인한 Overrun 에러 발생 시 수신 엔진을 강제 재가동하는 방어 로직 구현.
3. **비동기 멀티스레딩 서버**
   * Flask 서버 내에서 STM32 제어 송신 스레드와 조도 센서 데이터 수집 스레드를 분리하여 병목 현상 방지.
   * 값의 변화가 있을 때만 명령을 전송하고, 중복 데이터를 필터링하여 SQLite DB에 효율적으로 실시간 로깅.
4. **시연용 가속 모드(Demo Mode) 지원**
   * 발표 및 시연을 위해 가상 시간(08시~18시)을 순환하며 시스템을 고속 구동하는 API 제공.

## 📁 Repository Structure

```text
.
├── ml_engine/
│   ├── train.py                 # 데이터 증강 및 Random Forest 모델 학습
│   ├── solar_azimuth_3years.csv # 학습용 원본 데이터
│   └── solar_model.pkl          # 학습 완료된 예측 모델
├── server/
│   ├── app.py                   # Flask REST API 및 멀티스레드 시리얼 통신
│   ├── db.py                    # SQLite 데이터베이스 제어
│   └── index.html               # 웹 관제 대시보드 UI
└── stm32_firmware/
    ├── Core/Src/main.c          # STM32 메인 제어 로직 (UART, I2C, PWM)
    └── ...

