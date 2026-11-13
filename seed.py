"""
Наполняет БД текущими данными сайта (тренеры-заглушки, направления,
расписание, цены Началовского шоссе, пример отзывов) и создаёт
администратора по умолчанию.

Запускать один раз при первом разворачивании: python3 seed.py
Безопасно перезапускать — очищает таблицы перед заполнением.
"""

from werkzeug.security import generate_password_hash
from db import get_connection, init_db

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "krym2026"  # сменить после первого входа


def seed():
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # --- admin user ---
    cur.execute("SELECT id FROM admin_users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (DEFAULT_ADMIN_USERNAME, generate_password_hash(DEFAULT_ADMIN_PASSWORD, method="pbkdf2:sha256")),
        )

    # --- очистка перед заполнением (кроме admin_users, чтобы не терять пароль) ---
    for table in ("trainers", "directions", "schedule_items", "prices", "reviews"):
        cur.execute(f"DELETE FROM {table}")

    # --- тренеры (заглушки) ---
    trainers = [
        ("babajka", "Тренер 1", "Бокс", None, 1),
        ("babajka", "Тренер 2", "Тайский бокс", None, 2),
        ("babajka", "Тренер 3", "Бассейн", None, 3),
        ("babajka", "Тренер 4", "Тренажёрный зал", None, 4),
    ]
    cur.executemany(
        "INSERT INTO trainers (branch, name, sport, bio, sort_order) VALUES (?, ?, ?, ?, ?)",
        trainers,
    )

    # --- направления ---
    directions = [
        ("babajka", "Бокс", "Группы для начинающих и опытных.", 1),
        ("babajka", "Тайский бокс", "Групповые тренировки.", 2),
        ("babajka", "Вольная борьба", "Групповые тренировки.", 3),
        ("babajka", "Грэпплинг", "Групповые тренировки.", 4),
        ("babajka", "Рукопашный бой", "Групповые тренировки.", 5),
        ("babajka", "Бассейн", "Свободное плавание, аквааэробика, обучение.", 6),
        ("babajka", "Тренажёрный зал", "Дневные и вечерние абонементы.", 7),
        ("babajka", "Фитнес-аэробика", "Групповые занятия.", 8),
        ("babajka", "Женский фитнес", "Отдельная фитнес-зона только для женщин.", 9),
        ("nachalovskoe", "Бассейн", "Свободное плавание, аквааэробика, обучение плаванию, сауна.", 1),
        ("nachalovskoe", "Бокс", "Групповые тренировки, разовое посещение или абонемент на 12 занятий.", 2),
        ("nachalovskoe", "Тренажёрный зал", "Свободный доступ по абонементу, дневные и вечерние тарифы.", 3),
    ]
    cur.executemany(
        "INSERT INTO directions (branch, name, description, sort_order) VALUES (?, ?, ?, ?)",
        directions,
    )

    # --- расписание: мкр. Бабаевского ---
    babajka_schedule = [
        ("babajka", "Пн, ср, пт", "09:00", "Обучение плаванию", 1),
        ("babajka", "Пн, ср, пт", "19:00–21:00", "Муай тай", 2),
        ("babajka", "Пн, ср, пт", "19:00–21:00", "Вольная борьба", 3),
        ("babajka", "Вт, чт, сб", "14:00–19:00", "Обучение плаванию детей", 4),
        ("babajka", "Вт, чт, сб", "19:00–20:00", "Обучение плаванию взрослых", 5),
        ("babajka", "Вт, чт", "20:00–21:00", "Аквааэробика", 6),
        ("babajka", "Вт, чт, сб", "17:00–18:30, 19:00–21:00", "Универсальный бой", 7),
        ("babajka", "Вт, чт, сб", "19:00–21:00", "Бокс", 8),
        ("babajka", "Пн, вт, чт, сб", "19:00–21:00", "Грэпплинг", 9),
    ]
    # --- расписание: Началовское шоссе ---
    nach_schedule = [
        ("nachalovskoe", "Вт, чт, сб", "09:00", "Аквааэробика", 1),
        ("nachalovskoe", "Вт, чт, сб", "10:00", "Детское обучение плаванию", 2),
        ("nachalovskoe", "Вт, чт, сб", "11:00–14:00", "Индивидуальные занятия", 3),
        ("nachalovskoe", "Вт, чт, сб", "14:00", "Детское обучение плаванию", 4),
        ("nachalovskoe", "Вт, чт, сб", "15:00", "Аквааэробика", 5),
        ("nachalovskoe", "Вт, чт, сб", "16:00–18:00", "Детское обучение плаванию", 6),
        ("nachalovskoe", "Вт, чт, сб", "19:00", "Аквааэробика", 7),
        ("nachalovskoe", "Вт, чт, сб", "20:00", "Индивидуальные занятия", 8),
        ("nachalovskoe", "Пн", "10:00", "Аквааэробика", 9),
        ("nachalovskoe", "Пн, ср, пт", "19:00", "Бокс", 10),
        ("nachalovskoe", "Вт, чт, сб", "19:00", "Бокс", 11),
    ]
    cur.executemany(
        "INSERT INTO schedule_items (branch, days, time_range, class_name, sort_order) VALUES (?, ?, ?, ?, ?)",
        babajka_schedule + nach_schedule,
    )

    # --- цены: только Началовское шоссе (мкр. Бабаевского остаётся в коде шаблона) ---
    nach_prices = [
        ("nachalovskoe", "Бассейн", "Разовое посещение", 300, 1),
        ("nachalovskoe", "Бассейн", "Детям до 5 лет", 150, 2),
        ("nachalovskoe", "Бассейн", "Абонемент 8 посещений", 2200, 3),
        ("nachalovskoe", "Бассейн", "Абонемент 12 посещений", 3000, 4),
        ("nachalovskoe", "Бассейн", "Свободный (безлимит)", 7000, 5),
        ("nachalovskoe", "Аквааэробика", "Разовое", 500, 6),
        ("nachalovskoe", "Аквааэробика", "8 занятий", 3800, 7),
        ("nachalovskoe", "Аквааэробика", "12 занятий", 5400, 8),
        ("nachalovskoe", "Обучение плаванию", "Разовое занятие", 450, 9),
        ("nachalovskoe", "Обучение плаванию", "Индивидуально", 1000, 10),
        ("nachalovskoe", "Сауна", "Сауна с бассейном", 1500, 11),
        ("nachalovskoe", "Бокс", "Разовое", 400, 12),
        ("nachalovskoe", "Бокс", "Абонемент 12 занятий", 3000, 13),
    ]
    cur.executemany(
        "INSERT INTO prices (branch, category, item_name, price_rub, sort_order) VALUES (?, ?, ?, ?, ?)",
        nach_prices,
    )

    # --- пример отзывов ---
    reviews = [
        ("babajka", "Пример отзыва", "Хожу на бокс уже полгода — тренер объясняет технику очень подробно, прогресс видно каждую неделю.", "published", None),
        ("nachalovskoe", "Пример отзыва", "Удобно, что можно записаться онлайн и сразу видно расписание на неделю.", "published", None),
        ("babajka", "Пример отзыва", "Хороший зал, доброжелательная атмосфера. Детям тоже нравится на секции.", "published", None),
    ]
    cur.executemany(
        "INSERT INTO reviews (branch, author_name, text, status, admin_reply) VALUES (?, ?, ?, ?, ?)",
        reviews,
    )

    conn.commit()
    conn.close()
    print("База заполнена. Логин администратора: {} / пароль: {}".format(
        DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD
    ))


if __name__ == "__main__":
    seed()
