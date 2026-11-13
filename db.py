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

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL CHECK (branch IN ('babajka', 'nachalovskoe')),
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'hidden')),
    admin_reply TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
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
