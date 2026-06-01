# db.py
# 태양광 추적 시스템 — SQLite 데이터베이스 관리 모듈 (온/습도 제거 버전)

import sqlite3

DB_PATH = "solar_tracking.db"


# ── DB 구조 초기화 ─────────────────────────────────────────────────────────
def init_db():
    """
    sensor_data 테이블을 생성합니다. 온/습도를 완전히 제외하고 
    조도와 모터 각도만 저장하도록 슬림화합니다.
    """
    conn = sqlite3.connect(DB_PATH)
    
    # 신규 구조 테이블 생성 (기존 테이블이 꼬이지 않도록 새롭게 빌드)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id              INTEGER  PRIMARY KEY AUTOINCREMENT,
            sensor          TEXT     NOT NULL,  -- 기기 식별 명칭 (예: 'STM32_NUCLEO')
            lux             REAL,               -- 실측 조도 수치
            predicted_angle REAL,               -- 서보모터 추적 각도 수치
            timestamp       TEXT     DEFAULT (datetime('now','localtime')) -- 수집 시각
        )
    """)
    
    # 타임스탬프 기반 인덱스로 조회 성능 최적화
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON sensor_data(timestamp)")
    conn.commit()
    conn.close()
    print(f"[DB] 구조 검증 및 초기화 완료: {DB_PATH}")


# ── 실시간 데이터 삽입 (온/습도 인자 제거) ───────────────────────────────────────
def insert(sensor: str, lux: float, predicted_angle: float) -> int:
    """
    조도와 서보모터 각도 1건을 데이터베이스에 실시간 적재합니다.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sensor_data (sensor, lux, predicted_angle) VALUES (?, ?, ?)",
        (sensor, lux, predicted_angle),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


# ── 최근 N건 조회 (웹 차트 연동용) ─────────────────────────────────────────────
def fetch_recent(limit: int = 20) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sensor_data ORDER BY timestamp DESC LIMIT ?", (limit,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


# ── 데이터 통계 집계 ─────────────────────────────────────────────────────────
def fetch_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COUNT(*)                    AS total,
            ROUND(AVG(lux),  2)         AS avg_lux,
            ROUND(MAX(lux),  2)         AS max_lux,
            ROUND(MIN(lux),  2)         AS min_lux,
            MAX(timestamp)              AS last_ts
        FROM sensor_data
    """)
    row = cursor.fetchone()
    conn.close()
    
    if not row or row[0] == 0:
        return {"total": 0, "avg_lux": 0.0, "max_lux": 0.0, "min_lux": 0.0, "last_ts": "-"}

    return {
        "total": row[0],
        "avg_lux": row[1],
        "max_lux": row[2],
        "min_lux": row[3],
        "last_ts": row[4],
    }


# ── 시간 범위 분석 조회 ───────────────────────────────────────────────────────
def fetch_by_range(start: str, end: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM sensor_data
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (start, end),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


if __name__ == "__main__":
    init_db()