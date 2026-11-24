"""
Слой работы с базой данных.

Сейчас использует встроенный sqlite3 — это временное решение для
разработки и показа результата без доступа к интернету (см. README).
При деплое на реальный сервер этот модуль стоит заменить на
SQLAlchemy + Flask-SQLAlchemy, сохранив ту же схему таблиц:
модели ниже прямо соответствуют описанным здесь SQL-таблицам,
так что миграция — это перенос схемы, а не передумывание архитектуры.

Переключение SQLite -> PostgreSQL на реальном сервере через SQLAlchemy
делается одной строкой конфигурации (SQLALCHEMY_DATABASE_URI), без
переписывания бизнес-логики.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "krym.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trainers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    bio TEXT,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    name TEXT NOT NULL,
    description TEXT,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    days TEXT NOT NULL,
    time_range TEXT NOT NULL,
    class_name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('nachalovskoe')),
    category TEXT NOT NULL,
    item_name TEXT NOT NULL,
    price_rub INTEGER NOT NULL,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Система бронирования (этап 1С-интеграции)
--
-- Общая схема для обоих филиалов, но сейчас заполняется только для
-- Бабайки: Началовское шоссе будет подключено позже без изменения схемы.
--
-- Поле source показывает, откуда взялась запись: 'site' (создана через
-- наш сайт), '1c_sync' (обнаружена в 1С при синхронизации — например,
-- создана администратором прямо в кассе клуба). Поле external_id хранит
-- идентификатор сущности в 1С, если она пришла оттуда или была туда
-- отправлена, чтобы можно было сопоставлять записи сайта и 1С и не
-- создавать дублей.
-- ============================================================================

CREATE TABLE IF NOT EXISTS integration_trainers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    name TEXT NOT NULL,
    phone TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    external_id TEXT,              -- идентификатор тренера/сотрудника в 1С, если есть
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    trainer_id INTEGER REFERENCES integration_trainers(id),
    sport TEXT NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,            -- YYYY-MM-DD
    start_time TEXT NOT NULL,      -- HH:MM
    end_time TEXT,
    capacity INTEGER,              -- NULL/0 = не ограничено (как в 1С: "Ёмкость 0")
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled', 'completed')),
    source TEXT NOT NULL DEFAULT 'site' CHECK (source IN ('site', '1c_sync')),
    external_id TEXT,              -- id занятия в 1С (appointment_id / class id)
    synced_at TEXT,                -- когда последний раз сверено с 1С
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (branch, external_id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    training_id INTEGER NOT NULL REFERENCES trainings(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending', 'confirmed', 'cancelled', 'failed')),
    source TEXT NOT NULL DEFAULT 'site' CHECK (source IN ('site', '1c_sync')),
    external_id TEXT,              -- id записи в 1С, когда она туда отправлена/оттуда получена
    idempotency_key TEXT UNIQUE,   -- защита от повторного создания при повторном запросе/клике
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    cancelled_at TEXT,
    UNIQUE (training_id, client_id)  -- нельзя записаться на одно и то же занятие дважды
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    status TEXT NOT NULL CHECK (status IN ('present', 'absent')),
    marked_by_trainer_id INTEGER REFERENCES integration_trainers(id),
    marked_at TEXT NOT NULL DEFAULT (datetime('now')),
    synced_to_1c INTEGER NOT NULL DEFAULT 0,  -- передано ли в 1С (пока всегда 0 — метод не подтверждён)
    sync_error TEXT                            -- текст последней ошибки синхронизации, если была
);

-- Технический журнал обращений к 1С: что запросили, какой код ответа,
-- без логина/пароля (Basic Auth) внутри — см. integrations/fitness1c/client.py,
-- где это поле формируется.
CREATE TABLE IF NOT EXISTS integration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL,
    method TEXT NOT NULL,          -- например 'client_to_class'
    request_summary TEXT,          -- краткое описание без секретов
    http_status INTEGER,
    success INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'hidden')),
    admin_reply TEXT,
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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def log_integration_call(branch, method, request_summary, http_status, success, error_message=None):
    """
    Пишет строку в integration_log. Передаётся в FitnessClubService как
    log_fn, чтобы модуль интеграции не был завязан напрямую на sqlite3 —
    при переходе на PostgreSQL/SQLAlchemy меняется только эта функция.

    Открывает собственное соединение (а не переиспользует соединение
    вызывающего кода), потому что пишет лог даже когда основной запрос
    к 1С завершился ошибкой и его соединение могло быть уже закрыто/
    находиться в неопределённом состоянии транзакции.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO integration_log
                (branch, method, request_summary, http_status, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (branch, method, request_summary, http_status, 1 if success else 0, error_message),
        )
        conn.commit()
    finally:
        conn.close()
