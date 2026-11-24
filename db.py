import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "krym.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    days TEXT NOT NULL,
    time_range TEXT NOT NULL,
    class_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trainers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    photo_path TEXT,
    bio TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    title TEXT NOT NULL,
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    group_title TEXT NOT NULL,
    title TEXT NOT NULL,
    price TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS integration_trainers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    name TEXT NOT NULL,
    phone TEXT,
    password_hash TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (branch, external_id)
);

CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    trainer_id INTEGER REFERENCES integration_trainers(id),
    sport TEXT NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,            -- YYYY-MM-DD, в часовом поясе Астрахани (Europe/Astrakhan, UTC+4)
    start_time TEXT NOT NULL,      -- HH:MM, местное время Астрахани
    end_time TEXT,
    capacity INTEGER,              -- NULL/0 = не ограничено (как в 1С: "Ёмкость 0")
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled', 'completed')),
    source TEXT NOT NULL DEFAULT 'site' CHECK (source IN ('site', '1c_sync')),
    external_id TEXT,              -- id занятия в 1С (appointment_id / class id)
    synced_at TEXT,                -- когда последний раз сверено с 1С
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Поля ниже заполняются только для source='1c_sync' (филиал 'babajka').
    -- Для занятий сайта (source='site') остаются NULL — старая логика
    -- Началовского шоссе их не читает и не устанавливает.
    room_title TEXT,               -- 1С: room.title — зал, где идёт занятие
    trainer_name TEXT,             -- 1С: employee.name — показываем даже если
                                    -- trainer_id не найден в integration_trainers
    booking_online INTEGER,        -- 1С: booking_online (0/1) — единственное поле,
                                    -- по которому на сайте показывается доступность
                                    -- записи; capacity/available_slots/booked для
                                    -- показа на сайте не используются (неоднозначны)
    UNIQUE (branch, external_id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    training_id INTEGER NOT NULL REFERENCES trainings(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled', 'attended', 'no_show')),
    source TEXT NOT NULL DEFAULT 'site' CHECK (source IN ('site', '1c_sync')),
    external_id TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS integration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trainings_branch_date ON trainings (branch, date);
CREATE INDEX IF NOT EXISTS idx_trainings_trainer ON trainings (trainer_id);
CREATE INDEX IF NOT EXISTS idx_bookings_client ON bookings (client_id);
CREATE INDEX IF NOT EXISTS idx_bookings_training ON bookings (training_id);
CREATE INDEX IF NOT EXISTS idx_integration_log_branch_method ON integration_log (branch, method);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_trainings_1c_columns(conn):
    """
    Миграция "на лету" для баз, созданных до добавления интеграции с 1С:
    SQLite не поддерживает ADD COLUMN IF NOT EXISTS, поэтому проверяем
    через PRAGMA table_info и добавляем недостающие колонки по одной.
    Безопасно для уже заполненной таблицы — новые колонки добавляются
    со значением NULL, существующие строки (source='site') не трогаются.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trainings)").fetchall()}
    migrations = {
        "room_title": "ALTER TABLE trainings ADD COLUMN room_title TEXT",
        "trainer_name": "ALTER TABLE trainings ADD COLUMN trainer_name TEXT",
        "booking_online": "ALTER TABLE trainings ADD COLUMN booking_online INTEGER",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    _ensure_trainings_1c_columns(conn)
    conn.commit()
    conn.close()


def log_integration_call(branch, method, status, message=None):
    """
    Пишет лог вызова интеграции в integration_log. НИКОГДА не передавайте
    сюда секреты (пароли, apikey, токены) — только статус и безопасное
    текстовое сообщение об ошибке/результате.
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO integration_log (branch, method, status, message) VALUES (?, ?, ?, ?)",
        (branch, method, status, message),
    )
    conn.commit()
    conn.close()
