import os
import functools
import logging
from datetime import date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection, init_db, log_integration_call
from integrations.fitness1c import config as fitness1c_config
from integrations.fitness1c.service import FitnessClubService
from integrations.fitness1c.client import (
    Fitness1CConfigError,
    Fitness1CAuthError,
    Fitness1CRequestError,
)
from integrations.fitness1c.sync import sync_schedule_from_1c

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

logger = logging.getLogger("app")

# Сколько дней вперёд показываем расписание Бабайки на сайте (считая
# сегодняшний день) — обычный диапазон "на эту и следующую неделю".
# Глубину истории назад не запрашиваем: прошедшие занятия на публичной
# витрине не нужны.
BABAJKA_SCHEDULE_DAYS_AHEAD = 14

# Ключ JS API Яндекс.Карт — получить бесплатно на developer.tech.yandex.ru
# и указать в переменной окружения YANDEX_MAPS_API_KEY на сервере.
# Без ключа карта на странице просто не подключится (шаблон это обработает).
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")

init_db()


@app.context_processor
def inject_maps_key():
    return {"yandex_maps_api_key": YANDEX_MAPS_API_KEY}


@app.template_global("dateformat_weekday")
def dateformat_weekday(date_str):
    """
    'YYYY-MM-DD' -> номер дня недели (0=понедельник, ... 6=воскресенье),
    для шаблона branches.html (расписание Бабайки из 1С). Дата уже
    хранится в локальном времени Астрахани (см. integrations/fitness1c/sync.py),
    поэтому здесь не требуется никакой работы с часовым поясом —
    достаточно разобрать календарную дату.
    """
    return date.fromisoformat(date_str).weekday()


def normalize_phone(raw):
    """
    Приводит телефон к единому виду +7XXXXXXXXXX, чтобы при регистрации
    и входе "8 912 345-67-89", "+7(912)345-67-89" и "9123456789"
    считались одним и тем же номером.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


# --------------------------------------------------------------------------
# Вспомогательные функции для чтения данных (используются и публичными
# страницами, и админкой)
# --------------------------------------------------------------------------

def fetch_trainers(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM trainers WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    return rows


def fetch_directions(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM directions WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    return rows


def fetch_schedule(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Расписание филиала "Бабайка" — из 1С (trainings, source='1c_sync').
#
# Филиал Началовское шоссе этот код не использует вообще: он по-прежнему
# работает через fetch_schedule()/schedule_items выше, без изменений.
# --------------------------------------------------------------------------

def _try_sync_babajka_schedule_from_1c():
    """
    Пытается обновить локальную копию расписания Бабайки свежими данными
    из 1С. НИКОГДА не поднимает исключение наружу — любая ошибка (1С не
    настроена, недоступна, вернула неожиданный ответ) логируется, и
    страница расписания просто продолжает показывать последнюю
    сохранённую в БД копию (см. sync.py о том, почему это обоснованно).

    Возвращает (ok: bool, message: str) для необязательного показа
    статуса администратору — на публичной витрине эта информация не
    выводится напрямую, только сам факт "данные могут быть неактуальны".
    """
    ok, message = fitness1c_config.get_config_status()
    if not ok:
        # Интеграция не включена/не настроена — это ожидаемое состояние
        # при разработке и не должно шуметь в логах на уровне warning.
        logger.info("[fitness1c] Синхронизация расписания Бабайки пропущена: %s", message)
        return False, message

    try:
        service = FitnessClubService(branch="babajka", log_fn=log_integration_call)
        date_from = date.today().isoformat()
        date_to = (date.today() + timedelta(days=BABAJKA_SCHEDULE_DAYS_AHEAD)).isoformat()
        raw_classes = service.get_schedule(date_from, date_to)
    except Fitness1CConfigError as exc:
        logger.warning("[fitness1c] Расписание Бабайки: ошибка конфигурации: %s", exc)
        return False, str(exc)
    except Fitness1CAuthError as exc:
        logger.error("[fitness1c] Расписание Бабайки: ошибка авторизации (%s): %s", exc.http_status, exc)
        return False, str(exc)
    except Fitness1CRequestError as exc:
        logger.warning("[fitness1c] Расписание Бабайки: 1С недоступна или вернула ошибку: %s", exc)
        return False, str(exc)
    except Exception:
        # Подстраховка: даже непредвиденная ошибка не должна ронять
        # страницу расписания — показываем то, что уже есть в БД.
        logger.exception("[fitness1c] Расписание Бабайки: непредвиденная ошибка синхронизации")
        return False, "Непредвиденная ошибка синхронизации с 1С."

    try:
        stats = sync_schedule_from_1c(raw_classes, branch="babajka")
    except Exception:
        logger.exception("[fitness1c] Расписание Бабайки: ошибка при сохранении полученных данных")
        return False, "Ошибка при сохранении полученного расписания."

    logger.info(
        "[fitness1c] Синхронизация расписания Бабайки: создано=%s, обновлено=%s, отменено=%s, ошибок=%s",
        stats["created"], stats["updated"], stats["canceled"], stats["failed"],
    )
    return True, "ok"


def fetch_babajka_schedule():
    """
    Возвращает расписание Бабайки для публичной витрины: список строк из
    `trainings` (branch='babajka', source='1c_sync', status='scheduled',
    дата от сегодня на BABAJKA_SCHEDULE_DAYS_AHEAD дней вперёд),
    сгруппированный по дате.

    Сначала пытается обновить данные из 1С (см.
    _try_sync_babajka_schedule_from_1c) — но при любой ошибке продолжает
    работать с тем, что уже есть в локальной БД, и сообщает об этом
    через schedule_is_fresh=False, чтобы шаблон мог (не навязчиво)
    отметить, что расписание может быть устаревшим.

    Отменённые занятия (status='cancelled') не возвращаются — они не
    должны быть видны на сайте (задание, п.5).
    """
    schedule_is_fresh, _sync_message = _try_sync_babajka_schedule_from_1c()

    date_from = date.today().isoformat()
    date_to = (date.today() + timedelta(days=BABAJKA_SCHEDULE_DAYS_AHEAD)).isoformat()

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM trainings
        WHERE branch = 'babajka' AND source = '1c_sync' AND status = 'scheduled'
          AND date >= ? AND date <= ?
        ORDER BY date, start_time
        """,
        (date_from, date_to),
    ).fetchall()
    conn.close()

    days = []
    current_date = None
    current_group = None
    for row in rows:
        if row["date"] != current_date:
            current_date = row["date"]
            current_group = {"date": current_date, "items": []}
            days.append(current_group)
        current_group["items"].append(row)

    # "configured" здесь означает "есть ли вообще что показать на
    # витрине", а не "включена ли сейчас переменная FITNESS1C_ENABLED":
    # если интеграция настроена и синк прошёл успешно, либо в БД уже
    # есть ранее сохранённая копия расписания (даже если именно сейчас
    # 1С недоступна или интеграция временно выключена) — страница
    # показывает то, что есть, с пометкой свежести. Сообщение
    # "недоступно" нужно только когда данных нет вовсе ни в 1С, ни в
    # локальном кэше.
    return {
        "days": days,
        "is_fresh": schedule_is_fresh,
        "configured": schedule_is_fresh or bool(days) or fitness1c_config.is_enabled(),
    }


def fetch_prices_by_category(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM prices WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)
    return grouped


def fetch_reviews(branch=None, only_published=True):
    conn = get_connection()
    query = "SELECT * FROM reviews"
    conditions = []
    params = []
    if branch:
        conditions.append("branch = ?")
        params.append(branch)
    if only_published:
        conditions.append("status = 'published'")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Публичные страницы
# --------------------------------------------------------------------------

@app.route("/")
def index():
    reviews = fetch_reviews(only_published=True)[:3]
    return render_template("index.html", reviews=reviews)


@app.route("/prices")
def prices():
    nach_prices = fetch_prices_by_category("nachalovskoe")
    return render_template("prices.html", nach_prices=nach_prices)


@app.route("/branches")
def branches():
    data = {
        "babajka": {
            "trainers": fetch_trainers("babajka"),
            "directions": fetch_directions("babajka"),
            "schedule_1c": fetch_babajka_schedule(),
        },
        "nachalovskoe": {
            "trainers": fetch_trainers("nachalovskoe"),
            "directions": fetch_directions("nachalovskoe"),
            "schedule": fetch_schedule("nachalovskoe"),
            "prices": fetch_prices_by_category("nachalovskoe"),
        },
    }
    return render_template("branches.html", data=data)


# --------------------------------------------------------------------------
# Регистрация, вход и личный кабинет клиента
#
# Отдельная роль от админа: клиент входит по телефону+паролю в свою же
# таблицу `clients`, session-ключ "client_id" никак не пересекается с
# "admin_id", поэтому вход клиента не даёт доступа к /admin и наоборот.
# --------------------------------------------------------------------------

def client_login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("client_id"):
            return redirect(url_for("client_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/register", methods=["GET", "POST"])
def client_register():
    if session.get("client_id"):
        return redirect(url_for("client_account"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        raw_phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        phone = normalize_phone(raw_phone)

        error = None
        if not name:
            error = "Укажите имя."
        elif not phone:
            error = "Проверьте номер телефона — похоже, в нём не хватает цифр."
        elif len(password) < 6:
            error = "Пароль должен быть не короче 6 символов."
        elif password != password2:
            error = "Пароли не совпадают."

        if not error:
            conn = get_connection()
            existing = conn.execute(
                "SELECT id FROM clients WHERE phone = ?", (phone,)
            ).fetchone()
            if existing:
                error = "Клиент с таким телефоном уже зарегистрирован. Попробуйте войти."
            else:
                conn.execute(
                    "INSERT INTO clients (name, phone, password_hash) VALUES (?, ?, ?)",
                    (name, phone, generate_password_hash(password)),
                )
                conn.commit()
                new_client = conn.execute(
                    "SELECT id FROM clients WHERE phone = ?", (phone,)
                ).fetchone()
                conn.close()

                session["client_id"] = new_client["id"]
                session["client_name"] = name
                flash("Добро пожаловать! Регистрация прошла успешно.")
                return redirect(url_for("client_account"))
            conn.close()

        flash(error)

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def client_login():
    if session.get("client_id"):
        return redirect(url_for("client_account"))

    if request.method == "POST":
        raw_phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        phone = normalize_phone(raw_phone)

        client = None
        if phone:
            conn = get_connection()
            client = conn.execute(
                "SELECT * FROM clients WHERE phone = ?", (phone,)
            ).fetchone()
            conn.close()

        if client and check_password_hash(client["password_hash"], password):
            session["client_id"] = client["id"]
            session["client_name"] = client["name"]
            next_url = request.args.get("next") or url_for("client_account")
            return redirect(next_url)

        flash("Неверный телефон или пароль.")

    return render_template("login.html")


@app.route("/logout")
def client_logout():
    session.pop("client_id", None)
    session.pop("client_name", None)
    return redirect(url_for("index"))


@app.route("/account")
@client_login_required
def client_account():
    conn = get_connection()
    client = conn.execute(
        "SELECT * FROM clients WHERE id = ?", (session["client_id"],)
    ).fetchone()
    bookings = conn.execute(
        """
        SELECT b.id, b.status, b.source, t.title, t.sport, t.branch,
               t.date, t.start_time, t.end_time
        FROM bookings b
        JOIN trainings t ON t.id = b.training_id
        WHERE b.client_id = ? AND b.status != 'cancelled'
        ORDER BY t.date, t.start_time
        """,
        (session["client_id"],),
    ).fetchall()
    conn.close()
    return render_template("account.html", client=client, bookings=bookings)


@app.route("/account/bookings/<int:booking_id>/cancel", methods=["POST"])
@client_login_required
def client_cancel_booking(booking_id):
    conn = get_connection()
    booking = conn.execute(
        """
        SELECT b.*, t.branch, t.external_id as training_external_id
        FROM bookings b
        JOIN trainings t ON t.id = b.training_id
        WHERE b.id = ? AND b.client_id = ?
        """,
        (booking_id, session["client_id"]),
    ).fetchone()

    if not booking:
        conn.close()
        flash("Запись не найдена.")
        return redirect(url_for("client_account"))

    if booking["branch"] == "babajka":
        # Филиал на 1С: прежде чем отменять запись у нас, нужно
        # отменить её в 1С — это требование задания ("не подтверждай
        # бронирование/изменение до подтверждения от 1С"), применённое
        # и к отмене. Пока не подтверждено на тестовой базе, каким
        # параметром 1С идентифицирует клиента при Basic Auth от имени
        # пользователя 1С (см. FitnessClubService.get_client_usertoken
        # и cancel_client_from_class), поэтому честно сообщаем об
        # ограничении, не трогая запись.
        conn.close()
        flash(
            "Отмена записей филиала «мкр. Бабаевского» временно "
            "недоступна онлайн — сейчас этот шаг требует звонка в "
            "филиал, пока не завершена техническая проверка "
            "интеграции с 1С. Приносим извинения за неудобство."
        )
        return redirect(url_for("client_account"))

    # Началовское шоссе — локальная система, отмена безопасна и
    # ничего во внешней системе менять не нужно.
    conn.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = datetime('now') WHERE id = ?",
        (booking_id,),
    )
    conn.commit()
    conn.close()
    flash("Запись отменена.")
    return redirect(url_for("client_account"))


# --------------------------------------------------------------------------
# Авторизация администратора
# --------------------------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_connection()
        user = conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            next_url = request.args.get("next") or url_for("admin_dashboard")
            return redirect(next_url)

        flash("Неверный логин или пароль.")

    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# --------------------------------------------------------------------------
# Админ-панель: дашборд
# --------------------------------------------------------------------------

@app.route("/admin")
@login_required
def admin_dashboard():
    conn = get_connection()
    counts = {
        "trainers": conn.execute("SELECT COUNT(*) c FROM trainers").fetchone()["c"],
        "directions": conn.execute("SELECT COUNT(*) c FROM directions").fetchone()["c"],
        "schedule_items": conn.execute("SELECT COUNT(*) c FROM schedule_items").fetchone()["c"],
        "prices": conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"],
        "reviews_published": conn.execute("SELECT COUNT(*) c FROM reviews WHERE status='published'").fetchone()["c"],
        "reviews_hidden": conn.execute("SELECT COUNT(*) c FROM reviews WHERE status='hidden'").fetchone()["c"],
    }
    conn.close()
    return render_template("admin/dashboard.html", counts=counts)


# --------------------------------------------------------------------------
# Админ-панель: тренеры
# --------------------------------------------------------------------------

@app.route("/admin/trainers")
@login_required
def admin_trainers():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trainers ORDER BY branch, sort_order").fetchall()
    conn.close()
    return render_template("admin/trainers.html", trainers=rows)


@app.route("/admin/trainers/new", methods=["GET", "POST"])
@login_required
def admin_trainer_new():
    if request.method == "POST":
        conn = get_connection()
        conn.execute(
            "INSERT INTO trainers (branch, name, sport, bio, sort_order) VALUES (?, ?, ?, ?, ?)",
            (
                request.form["branch"],
                request.form["name"],
                request.form["sport"],
                request.form.get("bio", ""),
                int(request.form.get("sort_order") or 0),
            ),
        )
        conn.commit()
        conn.close()
        flash("Тренер добавлен.")
        return redirect(url_for("admin_trainers"))
    return render_template("admin/trainer_form.html", trainer=None)


@app.route("/admin/trainers/<int:trainer_id>/edit", methods=["GET", "POST"])
@login_required
def admin_trainer_edit(trainer_id):
    conn = get_connection()
    if request.method == "POST":
        conn.execute(
            "UPDATE trainers SET branch=?, name=?, sport=?, bio=?, sort_order=? WHERE id=?",
            (
                request.form["branch"],
                request.form["name"],
                request.form["sport"],
                request.form.get("bio", ""),
                int(request.form.get("sort_order") or 0),
                trainer_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Тренер обновлён.")
        return redirect(url_for("admin_trainers"))

    trainer = conn.execute("SELECT * FROM trainers WHERE id=?", (trainer_id,)).fetchone()
    conn.close()
    return render_template("admin/trainer_form.html", trainer=trainer)


@app.route("/admin/trainers/<int:trainer_id>/delete", methods=["POST"])
@login_required
def admin_trainer_delete(trainer_id):
    conn = get_connection()
    conn.execute("DELETE FROM trainers WHERE id=?", (trainer_id,))
    conn.commit()
    conn.close()
    flash("Тренер удалён.")
    return redirect(url_for("admin_trainers"))


# --------------------------------------------------------------------------
# Админ-панель: цены (только Началовское шоссе)
# --------------------------------------------------------------------------

@app.route("/admin/prices")
@login_required
def admin_prices():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM prices WHERE branch = 'nachalovskoe' ORDER BY category, sort_order"
    ).fetchall()
    conn.close()
    return render_template("admin/prices.html", prices=rows)


@app.route("/admin/prices/new", methods=["GET", "POST"])
@login_required
def admin_price_new():
    if request.method == "POST":
        conn = get_connection()
        conn.execute(
            "INSERT INTO prices (branch, category, item_name, price_rub, sort_order) VALUES ('nachalovskoe', ?, ?, ?, ?)",
            (
                request.form["category"],
                request.form["item_name"],
                int(request.form["price_rub"]),
                int(request.form.get("sort_order") or 0),
            ),
        )
        conn.commit()
        conn.close()
        flash("Позиция добавлена.")
        return redirect(url_for("admin_prices"))
    return render_template("admin/price_form.html", price=None)


@app.route("/admin/prices/<int:price_id>/edit", methods=["GET", "POST"])
@login_required
def admin_price_edit(price_id):
    conn = get_connection()
    if request.method == "POST":
        conn.execute(
            "UPDATE prices SET category=?, item_name=?, price_rub=?, sort_order=? WHERE id=?",
            (
                request.form["category"],
                request.form["item_name"],
                int(request.form["price_rub"]),
                int(request.form.get("sort_order") or 0),
                price_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Цена обновлена.")
        return redirect(url_for("admin_prices"))

    price = conn.execute("SELECT * FROM prices WHERE id=?", (price_id,)).fetchone()
    conn.close()
    return render_template("admin/price_form.html", price=price)


@app.route("/admin/prices/<int:price_id>/delete", methods=["POST"])
@login_required
def admin_price_delete(price_id):
    conn = get_connection()
    conn.execute("DELETE FROM prices WHERE id=?", (price_id,))
    conn.commit()
    conn.close()
    flash("Позиция удалена.")
    return redirect(url_for("admin_prices"))


# --------------------------------------------------------------------------
# Админ-панель: расписание
# --------------------------------------------------------------------------

@app.route("/admin/schedule")
@login_required
def admin_schedule():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM schedule_items ORDER BY branch, sort_order").fetchall()
    conn.close()
    return render_template("admin/schedule.html", items=rows)


@app.route("/admin/schedule/new", methods=["GET", "POST"])
@login_required
def admin_schedule_new():
    if request.method == "POST":
        conn = get_connection()
        conn.execute(
            "INSERT INTO schedule_items (branch, days, time_range, class_name, sort_order) VALUES (?, ?, ?, ?, ?)",
            (
                request.form["branch"],
                request.form["days"],
                request.form["time_range"],
                request.form["class_name"],
                int(request.form.get("sort_order") or 0),
            ),
        )
        conn.commit()
        conn.close()
        flash("Занятие добавлено в расписание.")
        return redirect(url_for("admin_schedule"))
    return render_template("admin/schedule_form.html", item=None)


@app.route("/admin/schedule/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def admin_schedule_edit(item_id):
    conn = get_connection()
    if request.method == "POST":
        conn.execute(
            "UPDATE schedule_items SET branch=?, days=?, time_range=?, class_name=?, sort_order=? WHERE id=?",
            (
                request.form["branch"],
                request.form["days"],
                request.form["time_range"],
                request.form["class_name"],
                int(request.form.get("sort_order") or 0),
                item_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Запись расписания обновлена.")
        return redirect(url_for("admin_schedule"))

    item = conn.execute("SELECT * FROM schedule_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return render_template("admin/schedule_form.html", item=item)


@app.route("/admin/schedule/<int:item_id>/delete", methods=["POST"])
@login_required
def admin_schedule_delete(item_id):
    conn = get_connection()
    conn.execute("DELETE FROM schedule_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    flash("Запись удалена.")
    return redirect(url_for("admin_schedule"))


# --------------------------------------------------------------------------
# Админ-панель: отзывы (модерация)
# --------------------------------------------------------------------------

@app.route("/admin/reviews")
@login_required
def admin_reviews():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM reviews ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin/reviews.html", reviews=rows)


@app.route("/admin/reviews/<int:review_id>/toggle", methods=["POST"])
@login_required
def admin_review_toggle(review_id):
    conn = get_connection()
    row = conn.execute("SELECT status FROM reviews WHERE id=?", (review_id,)).fetchone()
    if row:
        new_status = "hidden" if row["status"] == "published" else "published"
        conn.execute("UPDATE reviews SET status=? WHERE id=?", (new_status, review_id))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_reviews"))


@app.route("/admin/reviews/<int:review_id>/reply", methods=["POST"])
@login_required
def admin_review_reply(review_id):
    conn = get_connection()
    conn.execute(
        "UPDATE reviews SET admin_reply=? WHERE id=?",
        (request.form.get("admin_reply", "").strip() or None, review_id),
    )
    conn.commit()
    conn.close()
    flash("Ответ сохранён.")
    return redirect(url_for("admin_reviews"))


@app.route("/admin/reviews/<int:review_id>/delete", methods=["POST"])
@login_required
def admin_review_delete(review_id):
    conn = get_connection()
    conn.execute("DELETE FROM reviews WHERE id=?", (review_id,))
    conn.commit()
    conn.close()
    flash("Отзыв удалён.")
    return redirect(url_for("admin_reviews"))


# --------------------------------------------------------------------------
# Личный кабинет тренера
#
# Третья, отдельная роль от клиента и администратора — session-ключ
# "trainer_id" не пересекается ни с "client_id", ни с "admin_id".
# Тренер видит только свои занятия (WHERE trainer_id = session[...]) и
# только клиентов своих занятий — никогда чужие данные.
# --------------------------------------------------------------------------

def trainer_login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("trainer_id"):
            return redirect(url_for("trainer_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/trainer/login", methods=["GET", "POST"])
def trainer_login():
    if session.get("trainer_id"):
        return redirect(url_for("trainer_dashboard"))

    if request.method == "POST":
        raw_phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        phone = normalize_phone(raw_phone)

        trainer = None
        if phone:
            conn = get_connection()
            trainer = conn.execute(
                "SELECT * FROM integration_trainers WHERE phone = ? AND is_active = 1",
                (phone,),
            ).fetchone()
            conn.close()

        if trainer and check_password_hash(trainer["password_hash"], password):
            session["trainer_id"] = trainer["id"]
            session["trainer_name"] = trainer["name"]
            next_url = request.args.get("next") or url_for("trainer_dashboard")
            return redirect(next_url)

        flash("Неверный телефон или пароль.")

    return render_template("trainer/login.html")


@app.route("/trainer/logout")
def trainer_logout():
    session.pop("trainer_id", None)
    session.pop("trainer_name", None)
    return redirect(url_for("index"))


@app.route("/trainer")
@trainer_login_required
def trainer_dashboard():
    conn = get_connection()
    trainer = conn.execute(
        "SELECT * FROM integration_trainers WHERE id = ?", (session["trainer_id"],)
    ).fetchone()

    trainings = conn.execute(
        """
        SELECT id, branch, sport, title, date, start_time, end_time, capacity, source
        FROM trainings
        WHERE trainer_id = ? AND status = 'scheduled' AND date >= date('now')
        ORDER BY date, start_time
        """,
        (session["trainer_id"],),
    ).fetchall()

    # Количество подтверждённых записей на каждое занятие — для
    # Началовского шоссе это полный список (источник истины — наша
    # БД). Для Бабайки (1С) это количество по ЗАПИСЯМ, СДЕЛАННЫМ ЧЕРЕЗ
    # НАШ САЙТ — не полный список участников занятия в 1С, так как
    # метод получения полного списка не подтверждён документацией
    # (см. FitnessClubService.get_class_participants). Это прямо
    # показывается в шаблоне, чтобы тренер не считал список исчерпывающим
    # для занятий филиала на 1С.
    booking_counts = {}
    for t in trainings:
        count = conn.execute(
            "SELECT count(*) as c FROM bookings WHERE training_id = ? AND status != 'cancelled'",
            (t["id"],),
        ).fetchone()["c"]
        booking_counts[t["id"]] = count

    conn.close()
    return render_template(
        "trainer/dashboard.html",
        trainer=trainer,
        trainings=trainings,
        booking_counts=booking_counts,
    )


@app.route("/trainer/trainings/<int:training_id>")
@trainer_login_required
def trainer_training_detail(training_id):
    conn = get_connection()
    training = conn.execute(
        "SELECT * FROM trainings WHERE id = ? AND trainer_id = ?",
        (training_id, session["trainer_id"]),
    ).fetchone()

    if not training:
        conn.close()
        flash("Занятие не найдено или не относится к вам.")
        return redirect(url_for("trainer_dashboard"))

    bookings = conn.execute(
        """
        SELECT b.id as booking_id, b.status as booking_status, b.source,
               c.name as client_name, c.phone as client_phone,
               a.status as attendance_status, a.synced_to_1c
        FROM bookings b
        JOIN clients c ON c.id = b.client_id
        LEFT JOIN attendance a ON a.booking_id = b.id
        WHERE b.training_id = ? AND b.status != 'cancelled'
        ORDER BY c.name
        """,
        (training_id,),
    ).fetchall()
    conn.close()

    # Для занятий филиала на 1С список участников выше — это ТОЛЬКО
    # записи, сделанные через наш сайт (bookings.source='site'), а не
    # полный список из 1С (включая записи администратора клуба) — этот
    # метод 1С не подтверждён. Шаблон должен явно показать это
    # предупреждение для branch='babajka'.
    participants_are_complete = training["branch"] != "babajka"

    return render_template(
        "trainer/training_detail.html",
        training=training,
        bookings=bookings,
        participants_are_complete=participants_are_complete,
    )


@app.route("/trainer/bookings/<int:booking_id>/attendance", methods=["POST"])
@trainer_login_required
def trainer_mark_attendance(booking_id):
    status = request.form.get("status")
    if status not in ("present", "absent"):
        flash("Некорректный статус посещения.")
        return redirect(request.referrer or url_for("trainer_dashboard"))

    conn = get_connection()
    booking = conn.execute(
        """
        SELECT b.*, t.trainer_id, t.branch
        FROM bookings b
        JOIN trainings t ON t.id = b.training_id
        WHERE b.id = ?
        """,
        (booking_id,),
    ).fetchone()

    if not booking or booking["trainer_id"] != session["trainer_id"]:
        conn.close()
        flash("Запись не найдена или не относится к вашим занятиям.")
        return redirect(url_for("trainer_dashboard"))

    existing = conn.execute(
        "SELECT id FROM attendance WHERE booking_id = ?", (booking_id,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE attendance
            SET status = ?, marked_by_trainer_id = ?, marked_at = datetime('now')
            WHERE booking_id = ?
            """,
            (status, session["trainer_id"], booking_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO attendance (booking_id, status, marked_by_trainer_id)
            VALUES (?, ?, ?)
            """,
            (booking_id, status, session["trainer_id"]),
        )
    conn.commit()

    training_id = booking["training_id"]

    # Передача отметки в 1С — НЕ подтверждённый метод (см.
    # FitnessClubService.push_attendance). Посещаемость сохраняется в
    # нашей БД с synced_to_1c=0. Мы НЕ делаем вид, что отправили её в
    # 1С, и не пытаемся вызвать неподтверждённый метод — это прямое
    # требование задания.
    if booking["branch"] == "babajka":
        flash(
            "Отметка сохранена на сайте. Передача посещаемости в 1С "
            "пока не подключена — метод для этого не подтверждён "
            "документацией API и требует отдельной проверки."
        )
    else:
        flash("Отметка посещаемости сохранена.")

    conn.close()
    return redirect(url_for("trainer_training_detail", training_id=training_id))

init_db()

# На бесплатном тарифе Render нет Pre-Deploy Command и нет постоянного
# диска — файловая система (включая instance/krym.db) пересоздаётся
# заново при каждом деплое/перезапуске контейнера. Чтобы сайт не
# оставался с пустыми таблицами после этого, вызываем seed() здесь же:
# он заполняет дефолтными данными только пустые таблицы (см. seed.py),
# поэтому не затирает правки, уже сделанные администратором через
# /admin после последнего заполнения.
try:
    from seed import seed
    seed()
except Exception:
    logger.exception("[seed] Не удалось выполнить автозаполнение БД при старте приложения")

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
