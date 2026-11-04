import os
import functools

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash

from db import get_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

init_db()


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
            "directions": fetch_directions("nachalovskoe"),
            "schedule": fetch_schedule("nachalovskoe"),
            "prices": fetch_prices_by_category("nachalovskoe"),
        },
    }
    return render_template("branches.html", data=data)


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
