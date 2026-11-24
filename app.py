
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_from_directory
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


# DATABASE
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def get_ip():
    # X-Forwarded-For-г зөвхөн итгэмжлэгдсэн proxy дээр тохируулна.
    return request.remote_addr or "0.0.0.0"


# DEVICE AND BROWSER
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


# DATABASE INITIALIZATION
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

    # VISITORS MIGRATION
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
        c.execute(
            "ALTER TABLE visitors RENAME TO visitors_old"
        )

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

    # USERS MIGRATION
    user_columns = [
        row["name"]
        for row in c.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    ]

    for col in [
        "vip_until TEXT",
        "last_ip TEXT",
        "is_banned INTEGER DEFAULT 0"
    ]:
        name = col.split()[0]
        if name not in user_columns:
            c.execute(
                f"ALTER TABLE users ADD COLUMN {col}"
            )

    # DEFAULT ADMIN
    admin = os.environ.get("ADMIN_USERNAME", "admin")
    pw = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")

    exists = c.execute(
        "SELECT id FROM users WHERE username=?",
        (admin,)
    ).fetchone()

    if not exists:
        c.execute("""
            INSERT INTO users(username,password,is_admin)
            VALUES(?,?,1)
        """, (
            admin,
            generate_password_hash(pw)
        ))

    c.commit()
    c.close()


init()


# VISITOR TRACKING AND BAN CHECK
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
            return """
            <div style="
                background:#0b0b12;
                color:#ff4d6d;
                height:100vh;
                display:flex;
                flex-direction:column;
                align-items:center;
                justify-content:center;
                font-family:sans-serif;
                text-align:center;
                padding:20px;
            ">
                <h1>🚫 ХАНДАХ ЭРХ ХААГДСАН</h1>
                <p style="color:#aaa;">
                    Таны IP хаяг энэ сайтад нэвтрэх эрхгүй болсон байна.
                </p>
            </div>
            """, 403

        if session.get("uid"):
            u = c.execute(
                "SELECT is_banned FROM users WHERE id=?",
                (session["uid"],)
            ).fetchone()

            if u and u["is_banned"]:
                session.clear()
                c.close()
                return "Таны бүртгэл хаагдсан байна.", 403

    # НЭГ SESSION = НЭГ VISITOR
    visitor_id = session.get("visitor_id")

    if not visitor_id:
        visitor_id = uuid.uuid4().hex
        session["visitor_id"] = visitor_id

    device, browser = get_device()

    uname = session.get("username") or "Зочин"
    now = datetime.utcnow().isoformat()

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
        uname,
        device,
        browser,
        request.path,
        now
    ))

    if session.get("uid"):
        c.execute(
            "UPDATE users SET last_ip=? WHERE id=?",
            (ip, session["uid"])
        )

    c.commit()
    c.close()


# LOGIN REQUIRED
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


# ADMIN REQUIRED
def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            flash("Админ эрх шаардлагатай.")
            return redirect(url_for("index"))
        return f(*a, **k)
    return w


# HOME PAGE
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


# REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        u = request.form.get(
            "username", ""
        ).strip()

        p = request.form.get(
            "password", ""
        )

        if len(u) < 3 or len(p) < 8:
            flash(
                "Нэр 3-аас дээш, нууц үг 8-аас дээш тэмдэгт байна."
            )
            return redirect(url_for("register"))

        c = db()

        try:
            c.execute("""
                INSERT INTO users(
                    username, password, last_ip
                )
                VALUES(?,?,?)
            """, (
                u,
                generate_password_hash(p),
                get_ip()
            ))

            c.commit()

            flash("Бүртгэл амжилттай. Нэвтэрнэ үү.")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Энэ хэрэглэгчийн нэр бүртгэлтэй байна.")

        finally:
            c.close()

    return render_template(
        "auth.html",
        mode="register"
    )


# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        c = db()

        u = c.execute(
            "SELECT * FROM users WHERE username=?",
            (request.form.get("username", ""),)
        ).fetchone()

        c.close()

        if u and check_password_hash(
            u["password"],
            request.form.get("password", "")
        ):

            if u["is_banned"]:
                flash("Таны бүртгэл хаагдсан байна.")
                return redirect(url_for("login"))

            session.clear()

            session["uid"] = u["id"]
            session["username"] = u["username"]
            session["admin"] = bool(u["is_admin"])
            session["visitor_id"] = uuid.uuid4().hex

            return redirect(url_for("index"))

        flash("Нэвтрэх нэр эсвэл нууц үг буруу.")

    return render_template(
        "auth.html",
        mode="login"
    )


# LOGOUT
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# MOVIE PAGE
@app.route("/movie/<int:mid>")
def movie(mid):

    c = db()

    m = c.execute(
        "SELECT * FROM movies WHERE id=?",
        (mid,)
    ).fetchone()

    unlocked = False

    if m:

        if not m["paid"] or session.get("admin"):
            unlocked = True

        elif session.get("uid"):

            u = c.execute(
                "SELECT vip_until FROM users WHERE id=?",
                (session["uid"],)
            ).fetchone()

            if u and u["vip_until"]:
                try:
                    if datetime.fromisoformat(
                        u["vip_until"]
                    ) > datetime.utcnow():
                        unlocked = True
                except ValueError:
                    pass

            if not unlocked:
                single = c.execute("""
                    SELECT id FROM payments
                    WHERE user_id=?
                    AND movie_id=?
                    AND status='approved'
                """, (
                    session["uid"],
                    mid
                )).fetchone()

                if single:
                    unlocked = True

    c.close()

    if not m:
        return "Кино олдсонгүй", 404

    return render_template(
        "movie.html",
        movie=m,
        unlocked=unlocked
    )


# PAYMENT PAGE
@app.route("/payment/<int:mid>", methods=["GET", "POST"])
@login_required
def payment(mid):

    c = db()

    m = c.execute(
        "SELECT * FROM movies WHERE id=?",
        (mid,)
    ).fetchone()

    if not m:
        m = {
            "id": 0,
            "title": "VIP Багц"
        }

    if request.method == "POST":

        method = request.form.get(
            "method",
            "1 Сар - 10,000₮"
        )

        c.execute("""
            INSERT INTO payments(
                user_id, movie_id, method, proof, status
            )
            VALUES(?,?,?,?, 'pending')
        """, (
            session["uid"],
            mid,
            method,
            ""
        ))

        c.commit()
        c.close()

        flash(
            "Хүсэлт илгээгдлээ! Админ төлбөрийг шалгасны дараа эрх нээгдэнэ."
        )

        return redirect(url_for("index"))

    c.close()

    return render_template(
        "payment.html",
        movie=m
    )


# ADMIN PANEL
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
        COALESCE(
            u.username,
            'Хэрэглэгч #' || p.user_id
        ) AS username,
        COALESCE(m.title, 'VIP Эрх') AS title

        FROM payments p

        LEFT JOIN users u
        ON u.id = p.user_id

        LEFT JOIN movies m
        ON m.id = p.movie_id

        ORDER BY p.id DESC
    """).fetchall()

    users = c.execute(
        "SELECT * FROM users ORDER BY id DESC"
    ).fetchall()

    cutoff = (
        datetime.utcnow() - timedelta(minutes=15)
    ).isoformat()

    visitors = c.execute("""
        SELECT *
        FROM visitors
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


# BAN IP
@app.route("/admin/ban_ip", methods=["POST"])
@login_required
@admin_required
def ban_ip():

    ip = request.form.get(
        "ip", ""
    ).strip()

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
            datetime.utcnow().isoformat()
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


# UNBAN IP
@app.route("/admin/unban_ip", methods=["POST"])
@login_required
@admin_required
def unban_ip():

    ip = request.form.get(
        "ip", ""
    ).strip()

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


# ADD MOVIE
@app.route("/admin/add", methods=["POST"])
@login_required
@admin_required
def add_movie():

    title = request.form.get(
        "title", ""
    ).strip()

    st = request.form.get("source_type")

    source = request.form.get(
        "source", ""
    ).strip()

    upload = request.files.get("video")

    if st == "file" and upload and upload.filename:

        name = secure_filename(upload.filename)

        if not name:
            flash("Файлын нэр буруу байна.")
            return redirect(url_for("admin"))

        # Ижил нэртэй файлыг давхар дарж бичихээс сэргийлнэ.
        ext = os.path.splitext(name)[1]
        name = uuid.uuid4().hex + ext

        upload.save(
            os.path.join(UPLOAD, name)
        )

        source = url_for(
            "uploaded",
            name=name
        )

    if not title or not source:
        flash(
            "Киноны нэр болон видео эх сурвалж шаардлагатай."
        )
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
        st,
        source,
        1 if request.form.get("paid") else 0
    ))

    c.commit()
    c.close()

    flash("Кино нэмэгдлээ.")

    return redirect(url_for("admin"))


# UPLOADED VIDEO
@app.route("/uploads/<path:name>")
def uploaded(name):
    return send_from_directory(UPLOAD, name)


# APPROVE / REJECT PAYMENT
@app.route(
    "/admin/payment/<int:pid>/<action>",
    methods=["GET", "POST"]
)
@login_required
@admin_required
def payment_action(pid, action):

    if action not in ("approve", "reject"):
        return "Bad request", 400

    c = db()

    p = c.execute(
        "SELECT * FROM payments WHERE id=?",
        (pid,)
    ).fetchone()

    if not p:
        c.close()
        return "Payment not found", 404

    if p["status"] != "pending":
        c.close()
        flash(
            "Энэ төлбөр өмнө нь шийдвэрлэгдсэн байна."
        )
        return redirect(url_for("admin"))

    status = (
        "approved"
        if action == "approve"
        else "rejected"
    )

    c.execute(
        "UPDATE payments SET status=? WHERE id=?",
        (status, pid)
    )

    if action == "approve":

        method = p["method"] or ""

        if "1 Кино" not in method:

            days = 30

            if "3 Сар" in method:
                days = 90

            elif "6 Сар" in method:
                days = 180

            elif "VIP" in method or "Хязгааргүй" in method:
                days = 36500

            u = c.execute(
                "SELECT vip_until FROM users WHERE id=?",
                (p["user_id"],)
            ).fetchone()

            base = datetime.utcnow()

            if u and u["vip_until"]:
                try:
                    cur = datetime.fromisoformat(
                        u["vip_until"]
                    )

                    if cur > base:
                        base = cur

                except ValueError:
                    pass

            new_until = (
                base + timedelta(days=days)
            ).isoformat()

            c.execute(
                "UPDATE users SET vip_until=? WHERE id=?",
                (new_until, p["user_id"])
            )

    c.commit()
    c.close()

    flash("Төлбөр амжилттай шийдвэрлэгдлээ.")

    return redirect(url_for("admin"))


# DELETE MOVIE
@app.route(
    "/admin/delete/<int:mid>",
    methods=["GET", "POST"]
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


# RUN WEBSITE
if __name__ == "__main__":
    app.run(debug=False)
