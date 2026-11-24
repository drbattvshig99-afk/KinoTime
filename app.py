
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_from_directory,
    jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-before-deployment"
)

DB = os.environ.get("DATABASE", "kinotime.db")
UPLOAD = os.path.join("static", "uploads")

os.makedirs(UPLOAD, exist_ok=True)
os.makedirs("static/posters", exist_ok=True)


# =========================
# DATABASE
# =========================

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.utcnow()


def date_text(value):
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value


def vip_info(vip_until):
    if not vip_until:
        return {
            "active": False,
            "days": 0,
            "until": "-"
        }

    try:
        expiry = datetime.fromisoformat(vip_until)
        remaining = expiry - now()

        if remaining.total_seconds() <= 0:
            return {
                "active": False,
                "days": 0,
                "until": date_text(vip_until)
            }

        days = max(
            1,
            (remaining.total_seconds() + 86399) // 86400
        )

        return {
            "active": True,
            "days": int(days),
            "until": date_text(vip_until)
        }

    except (ValueError, TypeError):
        return {
            "active": False,
            "days": 0,
            "until": "-"
        }


def get_ip():
    return request.remote_addr or "0.0.0.0"


def get_device():
    ua = request.headers.get("User-Agent", "").lower()

    if "ipad" in ua or "tablet" in ua:
        device = "iPad"
    elif "iphone" in ua:
        device = "iPhone"
    elif "android" in ua:
        device = "Android"
    elif "windows" in ua:
        device = "Windows"
    elif "macintosh" in ua or "mac os" in ua:
        device = "Mac"
    elif "linux" in ua:
        device = "Linux"
    else:
        device = "Unknown"

    if "edg/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "samsungbrowser" in ua:
        browser = "Samsung Internet"
    elif "chrome/" in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua:
        browser = "Safari"
    else:
        browser = "Unknown"

    return device, browser


def add_column(c, table, column, definition):
    columns = [
        row["name"]
        for row in c.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]

    if column not in columns:
        c.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init():
    c = db()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT,
            is_admin INTEGER DEFAULT 0,
            vip_until TEXT,
            last_ip TEXT,
            is_banned INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS movies(
            id INTEGER PRIMARY KEY,
            title TEXT,
            description TEXT,
            category TEXT,
            poster TEXT,
            source_type TEXT,
            source TEXT,
            paid INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            movie_id INTEGER,
            method TEXT,
            proof TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS banned_ips(
            ip TEXT PRIMARY KEY,
            reason TEXT,
            created_at TEXT
        )
    """)

    # USER MIGRATIONS
    add_column(c, "users", "vip_started_at", "TEXT")
    add_column(c, "users", "vip_until", "TEXT")
    add_column(c, "users", "last_ip", "TEXT")
    add_column(c, "users", "is_banned", "INTEGER DEFAULT 0")

    # PAYMENT MIGRATIONS
    add_column(c, "payments", "request_type", "TEXT")
    add_column(c, "payments", "amount", "INTEGER DEFAULT 0")
    add_column(c, "payments", "created_at", "TEXT")
    add_column(c, "payments", "decided_at", "TEXT")

    # Old payment records are classified by movie_id.
    c.execute("""
        UPDATE payments
        SET request_type =
            CASE WHEN movie_id = 0 THEN 'vip' ELSE 'movie' END
        WHERE request_type IS NULL OR request_type = ''
    """)

    c.execute("""
        UPDATE payments
        SET created_at = ?
        WHERE created_at IS NULL
    """, (now().isoformat(),))

    # WATCH HISTORY
    c.execute("""
        CREATE TABLE IF NOT EXISTS watch_history(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            movie_id INTEGER NOT NULL,
            watched_seconds REAL DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            progress REAL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(user_id, movie_id)
        )
    """)

    # VISITORS
    columns = [
        row["name"]
        for row in c.execute(
            "PRAGMA table_info(visitors)"
        ).fetchall()
    ]

    if not columns:
        c.execute("""
            CREATE TABLE visitors(
                visitor_id TEXT PRIMARY KEY,
                ip TEXT,
                username TEXT,
                device TEXT,
                browser TEXT,
                last_page TEXT,
                last_seen TEXT
            )
        """)

    elif "visitor_id" not in columns:
        c.execute("ALTER TABLE visitors RENAME TO visitors_old")

        c.execute("""
            CREATE TABLE visitors(
                visitor_id TEXT PRIMARY KEY,
                ip TEXT,
                username TEXT,
                device TEXT,
                browser TEXT,
                last_page TEXT,
                last_seen TEXT
            )
        """)

        old_rows = c.execute(
            "SELECT * FROM visitors_old"
        ).fetchall()

        for row in old_rows:
            c.execute("""
                INSERT INTO visitors(
                    visitor_id, ip, username, device,
                    browser, last_page, last_seen
                )
                VALUES(?,?,?,?,?,?,?)
            """, (
                uuid.uuid4().hex,
                row["ip"],
                row["username"],
                "Unknown",
                "Unknown",
                row["last_page"],
                row["last_seen"]
            ))

        c.execute("DROP TABLE visitors_old")

    else:
        for col in [
            "ip TEXT",
            "username TEXT",
            "device TEXT",
            "browser TEXT",
            "last_page TEXT",
            "last_seen TEXT"
        ]:
            name = col.split()[0]
            if name not in columns:
                c.execute(
                    f"ALTER TABLE visitors ADD COLUMN {col}"
                )

    # DEFAULT ADMIN
    admin_name = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password = os.environ.get(
        "ADMIN_PASSWORD",
        "ChangeMe123!"
    )

    exists = c.execute(
        "SELECT id FROM users WHERE username=?",
        (admin_name,)
    ).fetchone()

    if not exists:
        c.execute("""
            INSERT INTO users(username,password,is_admin)
            VALUES(?,?,1)
        """, (
            admin_name,
            generate_password_hash(admin_password)
        ))

    c.commit()
    c.close()


init()


# =========================
# VISITOR TRACKING / BAN
# =========================

@app.before_request
def track_and_check_ban():

    if request.path.startswith("/static"):
        return

    ip = get_ip()
    c = db()

    if not session.get("admin"):

        banned = c.execute(
            "SELECT ip FROM banned_ips WHERE ip=?",
            (ip,)
        ).fetchone()

        if banned:
            c.close()
            return "Таны IP хаяг блоклогдсон байна.", 403

        if session.get("uid"):
            user = c.execute(
                "SELECT is_banned FROM users WHERE id=?",
                (session["uid"],)
            ).fetchone()

            if user and user["is_banned"]:
                session.clear()
                c.close()
                return "Таны бүртгэл хаагдсан байна.", 403

    visitor_id = session.get("visitor_id")

    if not visitor_id:
        visitor_id = uuid.uuid4().hex
        session["visitor_id"] = visitor_id

    device, browser = get_device()
    username = session.get("username") or "Зочин"

    c.execute("""
        INSERT INTO visitors(
            visitor_id, ip, username, device,
            browser, last_page, last_seen
        )
        VALUES(?,?,?,?,?,?,?)

        ON CONFLICT(visitor_id) DO UPDATE SET
            ip=excluded.ip,
            username=excluded.username,
            device=excluded.device,
            browser=excluded.browser,
            last_page=excluded.last_page,
            last_seen=excluded.last_seen
    """, (
        visitor_id,
        ip,
        username,
        device,
        browser,
        request.path,
        now().isoformat()
    ))

    if session.get("uid"):
        c.execute(
            "UPDATE users SET last_ip=? WHERE id=?",
            (ip, session["uid"])
        )

    c.commit()
    c.close()


# =========================
# LOGIN / ADMIN DECORATORS
# =========================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            flash("Эхлээд нэвтэрнэ үү.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            flash("Админ эрх шаардлагатай.")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


# =========================
# HOME
# =========================

@app.route("/")
def index():
    c = db()

    movies = c.execute(
        "SELECT * FROM movies ORDER BY id DESC"
    ).fetchall()

    c.close()

    return render_template(
        "index.html",
        movies=movies
    )


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get("password", "")

        if len(username) < 3 or len(password) < 8:
            flash("Нэр 3-аас дээш, нууц үг 8-аас дээш тэмдэгт байна.")
            return redirect(url_for("register"))

        c = db()

        try:
            c.execute("""
                INSERT INTO users(username,password,last_ip)
                VALUES(?,?,?)
            """, (
                username,
                generate_password_hash(password),
                get_ip()
            ))

            c.commit()
            flash("Бүртгэл амжилттай. Нэвтэрнэ үү.")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Энэ хэрэглэгчийн нэр бүртгэлтэй байна.")

        finally:
            c.close()

    return render_template("auth.html", mode="register")


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        c = db()

        user = c.execute(
            "SELECT * FROM users WHERE username=?",
            (request.form.get("username", ""),)
        ).fetchone()

        c.close()

        if user and check_password_hash(
            user["password"],
            request.form.get("password", "")
        ):

            if user["is_banned"]:
                flash("Таны бүртгэл хаагдсан байна.")
                return redirect(url_for("login"))

            session.clear()
            session["uid"] = user["id"]
            session["username"] = user["username"]
            session["admin"] = bool(user["is_admin"])
            session["visitor_id"] = uuid.uuid4().hex

            return redirect(url_for("index"))

        flash("Нэвтрэх нэр эсвэл нууц үг буруу.")

    return render_template("auth.html", mode="login")


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# =========================
# USER PROFILE
# =========================

@app.route("/profile")
@login_required
def profile():

    c = db()

    user = c.execute(
        "SELECT * FROM users WHERE id=?",
        (session["uid"],)
    ).fetchone()

    payments = c.execute("""
        SELECT p.*,
               m.title AS movie_title
        FROM payments p
        LEFT JOIN movies m ON m.id=p.movie_id
        WHERE p.user_id=?
        ORDER BY p.id DESC
    """, (session["uid"],)).fetchall()

    history = c.execute("""
        SELECT h.*, m.title, m.poster
        FROM watch_history h
        JOIN movies m ON m.id=h.movie_id
        WHERE h.user_id=?
        ORDER BY h.updated_at DESC
    """, (session["uid"],)).fetchall()

    c.close()

    info = vip_info(user["vip_until"])

    return render_template(
        "profile.html",
        user=user,
        vip=info,
        payments=payments,
        history=history,
        vip_started=date_text(user["vip_started_at"])
    )


# =========================
# MOVIE PAGE
# =========================

@app.route("/movie/<int:mid>")
def movie(mid):

    c = db()

    m = c.execute(
        "SELECT * FROM movies WHERE id=?",
        (mid,)
    ).fetchone()

    if not m:
        c.close()
        return "Кино олдсонгүй", 404

    unlocked = False

    if not m["paid"] or session.get("admin"):
        unlocked = True

    elif session.get("uid"):

        user = c.execute(
            "SELECT vip_until FROM users WHERE id=?",
            (session["uid"],)
        ).fetchone()

        if user and vip_info(user["vip_until"])["active"]:
            unlocked = True

        if not unlocked:
            single = c.execute("""
                SELECT id FROM payments
                WHERE user_id=?
                  AND movie_id=?
                  AND request_type='movie'
                  AND status='approved'
            """, (
                session["uid"],
                mid
            )).fetchone()

            if single:
                unlocked = True

    c.close()

    return render_template(
        "movie.html",
        movie=m,
        unlocked=unlocked
    )


# =========================
# PAYMENT REQUEST
# mid = 0 means monthly VIP
# mid > 0 means a single movie
# =========================

@app.route("/payment/<int:mid>", methods=["GET", "POST"])
@login_required
def payment(mid):

    c = db()

    if mid == 0:
        movie_data = {
            "id": 0,
            "title": "1 сарын VIP эрх"
        }
        request_type = "vip"
        default_method = "1 Сар - 10,000₮"

    else:
        movie_data = c.execute(
            "SELECT * FROM movies WHERE id=?",
            (mid,)
        ).fetchone()

        if not movie_data:
            c.close()
            return "Кино олдсонгүй", 404

        request_type = "movie"
        default_method = "1 Кино"

    if request.method == "POST":

        method = request.form.get(
            "method",
            default_method
        ).strip()

        proof = request.form.get("proof", "").strip()

        try:
            amount = int(request.form.get("amount", "0") or 0)
        except ValueError:
            amount = 0

        pending = c.execute("""
            SELECT id FROM payments
            WHERE user_id=?
              AND movie_id=?
              AND request_type=?
              AND status='pending'
        """, (
            session["uid"],
            mid,
            request_type
        )).fetchone()

        if pending:
            c.close()
            flash("Энэ хүсэлт админы шийдвэрийг хүлээж байна.")
            return redirect(url_for("profile"))

        c.execute("""
            INSERT INTO payments(
                user_id, movie_id, method, proof,
                status, request_type, amount, created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            session["uid"],
            mid,
            method,
            proof,
            "pending",
            request_type,
            amount,
            now().isoformat()
        ))

        c.commit()
        c.close()

        if request_type == "vip":
            flash("Сарын VIP эрх авах хүсэлт илгээгдлээ.")
        else:
            flash("Тухайн кино үзэх хүсэлт илгээгдлээ.")

        return redirect(url_for("profile"))

    c.close()

    return render_template(
        "payment.html",
        movie=movie_data,
        request_type=request_type,
        default_method=default_method
    )


# =========================
# WATCH HISTORY PROGRESS
# =========================

@app.route("/watch-progress", methods=["POST"])
@login_required
def watch_progress():

    data = request.get_json(silent=True) or {}

    try:
        movie_id = int(data.get("movie_id", 0))
        watched = max(0, float(data.get("watched", 0)))
        duration = max(0, float(data.get("duration", 0)))
    except (ValueError, TypeError):
        return jsonify({"ok": False}), 400

    if movie_id <= 0 or duration <= 0:
        return jsonify({"ok": False}), 400

    progress = min(100, (watched / duration) * 100)

    c = db()

    movie_exists = c.execute(
        "SELECT id FROM movies WHERE id=?",
        (movie_id,)
    ).fetchone()

    if not movie_exists:
        c.close()
        return jsonify({"ok": False}), 404

    c.execute("""
        INSERT INTO watch_history(
            user_id, movie_id, watched_seconds,
            duration_seconds, progress, updated_at
        )
        VALUES(?,?,?,?,?,?)

        ON CONFLICT(user_id,movie_id) DO UPDATE SET
            watched_seconds=MAX(
                watch_history.watched_seconds,
                excluded.watched_seconds
            ),
            duration_seconds=excluded.duration_seconds,
            progress=MAX(
                watch_history.progress,
                excluded.progress
            ),
            updated_at=excluded.updated_at
    """, (
        session["uid"],
        movie_id,
        watched,
        duration,
        progress,
        now().isoformat()
    ))

    c.commit()
    c.close()

    return jsonify({
        "ok": True,
        "progress": round(progress, 1)
    })


# =========================
# ADMIN PANEL
# =========================

@app.route("/admin")
@login_required
@admin_required
def admin():

    c = db()

    movies = c.execute(
        "SELECT * FROM movies ORDER BY id DESC"
    ).fetchall()

    payments = c.execute("""
        SELECT p.*,
               COALESCE(u.username, 'Хэрэглэгч') AS username,
               m.title AS movie_title
        FROM payments p
        LEFT JOIN users u ON u.id=p.user_id
        LEFT JOIN movies m ON m.id=p.movie_id
        ORDER BY p.id DESC
    """).fetchall()

    raw_users = c.execute(
        "SELECT * FROM users ORDER BY id DESC"
    ).fetchall()

    users = []

    for user in raw_users:
        item = dict(user)
        item["vip"] = vip_info(user["vip_until"])
        item["vip_started"] = date_text(user["vip_started_at"])
        users.append(item)

    cutoff = (
        now() - timedelta(minutes=15)
    ).isoformat()

    visitors = c.execute("""
        SELECT * FROM visitors
        WHERE last_seen > ?
        ORDER BY last_seen DESC
    """, (cutoff,)).fetchall()

    banned_ips = [
        row["ip"]
        for row in c.execute(
            "SELECT ip FROM banned_ips"
        ).fetchall()
    ]

    c.close()

    return render_template(
        "admin.html",
        movies=movies,
        payments=payments,
        users=users,
        visitors=visitors,
        banned_ips=banned_ips
    )


# =========================
# BAN IP
# =========================

@app.route("/admin/ban_ip", methods=["POST"])
@login_required
@admin_required
def ban_ip():

    ip = request.form.get("ip", "").strip()
    uid = request.form.get("uid")

    if ip and ip != get_ip():

        c = db()

        c.execute("""
            INSERT OR IGNORE INTO banned_ips(
                ip, reason, created_at
            )
            VALUES(?,?,?)
        """, (
            ip,
            "Admin banned",
            now().isoformat()
        ))

        if uid:
            c.execute(
                "UPDATE users SET is_banned=1 WHERE id=?",
                (uid,)
            )

        c.commit()
        c.close()

        flash(f"{ip} хаяг блоклогдлоо.")

    return redirect(url_for("admin"))


# =========================
# UNBAN IP
# =========================

@app.route("/admin/unban_ip", methods=["POST"])
@login_required
@admin_required
def unban_ip():

    ip = request.form.get("ip", "").strip()
    uid = request.form.get("uid")

    c = db()

    if ip:
        c.execute(
            "DELETE FROM banned_ips WHERE ip=?",
            (ip,)
        )

    if uid:
        c.execute(
            "UPDATE users SET is_banned=0 WHERE id=?",
            (uid,)
        )

    c.commit()
    c.close()

    flash("Блок цуцлагдлаа.")
    return redirect(url_for("admin"))


# =========================
# ADD MOVIE
# =========================

@app.route("/admin/add", methods=["POST"])
@login_required
@admin_required
def add_movie():

    title = request.form.get("title", "").strip()
    source_type = request.form.get("source_type", "url")

    source = request.form.get("source", "").strip()
    upload = request.files.get("video")

    if source_type == "file" and upload and upload.filename:

        filename = secure_filename(upload.filename)

        if not filename:
            flash("Файлын нэр буруу байна.")
            return redirect(url_for("admin"))

        ext = os.path.splitext(filename)[1]
        filename = uuid.uuid4().hex + ext

        upload.save(os.path.join(UPLOAD, filename))

        source = url_for("uploaded", name=filename)

    if not title or not source:
        flash("Киноны нэр болон видео эх сурвалж шаардлагатай.")
        return redirect(url_for("admin"))

    c = db()

    c.execute("""
        INSERT INTO movies(
            title, description, category, poster,
            source_type, source, paid
        )
        VALUES(?,?,?,?,?,?,?)
    """, (
        title,
        request.form.get("description", ""),
        request.form.get("category", "Бусад"),
        request.form.get("poster", ""),
        source_type,
        source,
        1 if request.form.get("paid") else 0
    ))

    c.commit()
    c.close()

    flash("Кино нэмэгдлээ.")
    return redirect(url_for("admin"))


# =========================
# UPLOADED VIDEO
# =========================

@app.route("/uploads/<path:name>")
def uploaded(name):
    return send_from_directory(UPLOAD, name)


# =========================
# APPROVE / REJECT REQUEST
# =========================

@app.route(
    "/admin/payment/<int:pid>/<action>",
    methods=["POST"]
)
@login_required
@admin_required
def payment_action(pid, action):

    if action not in ("approve", "reject"):
        return "Bad request", 400

    c = db()

    payment = c.execute(
        "SELECT * FROM payments WHERE id=?",
        (pid,)
    ).fetchone()

    if not payment:
        c.close()
        return "Хүсэлт олдсонгүй", 404

    if payment["status"] != "pending":
        c.close()
        flash("Энэ хүсэлт өмнө нь шийдвэрлэгдсэн байна.")
        return redirect(url_for("admin"))

    status = "approved" if action == "approve" else "rejected"

    c.execute("""
        UPDATE payments
        SET status=?, decided_at=?
        WHERE id=?
    """, (
        status,
        now().isoformat(),
        pid
    ))

    # ЗӨВХӨН VIP ХҮСЭЛТЭД ЭРХ НЭЭНЭ
    request_type = payment["request_type"]

    if not request_type:
        request_type = "vip" if payment["movie_id"] == 0 else "movie"

    if action == "approve" and request_type == "vip":

        user = c.execute(
            "SELECT vip_until, vip_started_at FROM users WHERE id=?",
            (payment["user_id"],)
        ).fetchone()

        current_time = now()
        base_time = current_time

        if user and user["vip_until"]:
            try:
                old_expiry = datetime.fromisoformat(user["vip_until"])

                if old_expiry > current_time:
                    base_time = old_expiry

            except (ValueError, TypeError):
                pass

        new_expiry = base_time + timedelta(days=30)

        started_at = (
            user["vip_started_at"]
            if user and user["vip_started_at"]
            else current_time.isoformat()
        )

        c.execute("""
            UPDATE users
            SET vip_started_at=?, vip_until=?
            WHERE id=?
        """, (
            started_at,
            new_expiry.isoformat(),
            payment["user_id"]
        ))

    c.commit()
    c.close()

    if action == "approve":
        flash("Хүсэлтийг зөвшөөрлөө.")
    else:
        flash("Хүсэлтээс татгалзлаа.")

    return redirect(url_for("admin"))


# =========================
# DELETE MOVIE
# =========================

@app.route(
    "/admin/delete/<int:mid>",
    methods=["POST"]
)
@login_required
@admin_required
def delete_movie(mid):

    c = db()

    c.execute(
        "DELETE FROM movies WHERE id=?",
        (mid,)
    )

    c.commit()
    c.close()

    flash("Кино устгагдлаа.")
    return redirect(url_for("admin"))


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(debug=False)
