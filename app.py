import os, sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-deployment")
DB = os.environ.get("DATABASE", "kinotime.db")
UPLOAD = os.path.join("static", "uploads")
os.makedirs(UPLOAD, exist_ok=True)
os.makedirs("static/posters", exist_ok=True)

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def get_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, is_admin INTEGER DEFAULT 0, vip_until TEXT, last_ip TEXT, is_banned INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS movies(id INTEGER PRIMARY KEY, title TEXT, description TEXT, category TEXT, poster TEXT, source_type TEXT, source TEXT, paid INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, user_id INTEGER, movie_id INTEGER, method TEXT, proof TEXT, status TEXT DEFAULT 'pending')""")
    c.execute("""CREATE TABLE IF NOT EXISTS banned_ips(ip TEXT PRIMARY KEY, reason TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS visitors(ip TEXT PRIMARY KEY, username TEXT, last_page TEXT, last_seen TEXT)""")
    for col in ["vip_until TEXT", "last_ip TEXT", "is_banned INTEGER DEFAULT 0"]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    admin = os.environ.get("ADMIN_USERNAME", "admin")
    pw = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
    if not c.execute("SELECT id FROM users WHERE username=?", (admin,)).fetchone():
        c.execute("INSERT INTO users(username,password,is_admin) VALUES(?,?,1)", (admin, generate_password_hash(pw)))
    c.commit()
    c.close()

init()

@app.before_request
def track_and_check_ban():
    if request.path.startswith("/static"):
        return
    ip = get_ip()
    c = db()
    # 1. IP хаяг Ban хийгдсэн эсэхийг шалгах (Админ биш бол шууд хаана)
    if not session.get("admin"):
        banned = c.execute("SELECT ip FROM banned_ips WHERE ip=?", (ip,)).fetchone()
        if banned:
            c.close()
            return """
            <div style="background:#0b0b12;color:#ff4d6d;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:sans-serif;text-align:center;padding:20px;">
                <h1>🚫 ХАНДАХ ЭРХ ХААГДСАН</h1>
                <p style="color:#aaa;">Таны IP хаяг энэ сайтад нэвтрэх эрхгүй болсон байна.</p>
            </div>
            """, 403
        if session.get("uid"):
            u = c.execute("SELECT is_banned FROM users WHERE id=?", (session["uid"],)).fetchone()
            if u and u["is_banned"]:
                session.clear()
                c.close()
                return "Таны бүртгэл хаагдсан байна.", 403

    # 2. Үзэгчийн мэдээллийг бүртгэх
    uname = session.get("username") or "Зочин (Нэвтрээгүй)"
    now = datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO visitors(ip, username, last_page, last_seen) VALUES(?,?,?,?)
        ON CONFLICT(ip) DO UPDATE SET username=excluded.username, last_page=excluded.last_page, last_seen=excluded.last_seen
    """, (ip, uname, request.path, now))
    if session.get("uid"):
        c.execute("UPDATE users SET last_ip=? WHERE id=?", (ip, session["uid"]))
    c.commit()
    c.close()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            flash("Админ эрх шаардлагатай.")
            return redirect(url_for("index"))
        return f(*a, **k)
    return w

@app.route("/")
def index():
    c = db()
    movies = c.execute("SELECT * FROM movies ORDER BY id DESC").fetchall()
    c.close()
    return render_template("index.html", movies=movies)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        if len(u) < 3 or len(p) < 8:
            flash("Нэр 3-аас дээш, нууц үг 8-аас дээш тэмдэгт байна.")
            return redirect(url_for("register"))
        c = db()
        try:
            c.execute("INSERT INTO users(username,password,last_ip) VALUES(?,?,?)", (u, generate_password_hash(p), get_ip()))
            c.commit()
            flash("Бүртгэл амжилттай. Нэвтэрнэ үү.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Энэ хэрэглэгчийн нэр бүртгэлтэй байна.")
        finally:
            c.close()
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        c = db()
        u = c.execute("SELECT * FROM users WHERE username=?", (request.form.get("username", ""),)).fetchone()
        c.close()
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            if u["is_banned"]:
                flash("Таны бүртгэл хаагдсан байна.")
                return redirect(url_for("login"))
            session["uid"] = u["id"]
            session["username"] = u["username"]
            session["admin"] = bool(u["is_admin"])
            return redirect(url_for("index"))
        flash("Нэвтрэх нэр эсвэл нууц үг буруу.")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/movie/<int:mid>")
def movie(mid):
    c = db()
    m = c.execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone()
    unlocked = False
    if m:
        if not m["paid"] or session.get("admin"):
            unlocked = True
        elif session.get("uid"):
            u = c.execute("SELECT vip_until FROM users WHERE id=?", (session["uid"],)).fetchone()
            if u and u["vip_until"]:
                try:
                    if datetime.fromisoformat(u["vip_until"]) > datetime.utcnow():
                        unlocked = True
                except ValueError:
                    pass
            if not unlocked:
                single = c.execute("SELECT id FROM payments WHERE user_id=? AND movie_id=? AND status='approved'", (session["uid"], mid)).fetchone()
                if single:
                    unlocked = True
    c.close()
    if not m:
        return "Кино олдсонгүй", 404
    return render_template("movie.html", movie=m, unlocked=unlocked)

@app.route("/payment/<int:mid>", methods=["GET", "POST"])
@login_required
def payment(mid):
    c = db()
    m = c.execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone()
    if not m:
        m = {"id": 0, "title": "VIP Багц"}
    if request.method == "POST":
        method = request.form.get("method", "1 Сар - 10,000₮")
        c.execute("INSERT INTO payments(user_id,movie_id,method,proof,status) VALUES(?,?,?,?,'pending')", (session["uid"], mid, method, ""))
        c.commit()
        c.close()
        flash("Хүсэлт илгээгдлээ! Админ төлбөрийг шалгасны дараа эрх нээгдэнэ.")
        return redirect(url_for("index"))
    c.close()
    return render_template("payment.html", movie=m)

@app.route("/admin")
@login_required
@admin_required
def admin():
    c = db()
    movies = c.execute("SELECT * FROM movies ORDER BY id DESC").fetchall()
    payments = c.execute("""
        SELECT p.*, COALESCE(u.username, 'Хэрэглэгч #' || p.user_id) AS username, COALESCE(m.title, 'VIP Эрх') AS title 
        FROM payments p 
        LEFT JOIN users u ON u.id = p.user_id 
        LEFT JOIN movies m ON m.id = p.movie_id 
        ORDER BY p.id DESC
    """).fetchall()
    users = c.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    # Сүүлийн 15 минутад сайт руу орсон үзэгчид
    cutoff = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
    visitors = c.execute("SELECT * FROM visitors WHERE last_seen > ? ORDER BY last_seen DESC", (cutoff,)).fetchall()
    banned_ips = [row["ip"] for row in c.execute("SELECT ip FROM banned_ips").fetchall()]
    c.close()
    return render_template("admin.html", movies=movies, payments=payments, users=users, visitors=visitors, banned_ips=banned_ips)

@app.route("/admin/ban_ip", methods=["POST"])
@login_required
@admin_required
def ban_ip():
    ip = request.form.get("ip", "").strip()
    uid = request.form.get("uid")
    if ip and ip != get_ip():
        c = db()
        c.execute("INSERT OR IGNORE INTO banned_ips(ip, reason, created_at) VALUES(?,?,?)", (ip, "Admin banned", datetime.utcnow().isoformat()))
        if uid:
            c.execute("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
        c.commit()
        c.close()
        flash(f"{ip} хаяг блоклогдлоо.")
    return redirect(url_for("admin"))

@app.route("/admin/unban_ip", methods=["POST"])
@login_required
@admin_required
def unban_ip():
    ip = request.form.get("ip", "").strip()
    uid = request.form.get("uid")
    c = db()
    if ip:
        c.execute("DELETE FROM banned_ips WHERE ip=?", (ip,))
    if uid:
        c.execute("UPDATE users SET is_banned=0 WHERE id=?", (uid,))
    c.commit()
    c.close()
    flash("Блок цуцлагдлаа.")
    return redirect(url_for("admin"))

@app.route("/admin/add", methods=["POST"])
@login_required
@admin_required
def add_movie():
    title = request.form.get("title", "").strip()
