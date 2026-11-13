import os
import functools

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# Ключ JS API Яндекс.Карт — получить бесплатно на developer.tech.yandex.ru
# и указать в переменной окружения YANDEX_MAPS_API_KEY на сервере.
# Без ключа карта на странице просто не подключится (шаблон это обработает).
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")

init_db()


@app.context_processor
def inject_maps_key():
    return {"yandex_maps_api_key": YANDEX_MAPS_API_KEY}


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
            "schedule": fetch_schedule("babajka"),
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
    conn.close()
    # Записей на тренировки пока нет — раздел бронирования ещё не
    # реализован (см. план: следующий этап после личного кабинета).
    bookings = []
    return render_template("account.html", client=client, bookings=bookings)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
