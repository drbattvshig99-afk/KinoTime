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

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, is_admin INTEGER DEFAULT 0, vip_until TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS movies(id INTEGER PRIMARY KEY, title TEXT, description TEXT, category TEXT, poster TEXT, source_type TEXT, source TEXT, paid INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, user_id INTEGER, movie_id INTEGER, method TEXT, proof TEXT, status TEXT DEFAULT 'pending')""")
    try:
        c.execute("ALTER TABLE users ADD COLUMN vip_until TEXT")
    except sqlite3.OperationalError:
        pass
    admin = os.environ.get("ADMIN_USERNAME", "admin")
    pw = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
    if not c.execute("SELECT id FROM users WHERE username=?", (admin,)).fetchone():
        c.execute("INSERT INTO users(username,password,is_admin) VALUES(?,?,1)", (admin, generate_password_hash(pw)))
    c.commit()
    c.close()

init()

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
            c.execute("INSERT INTO users(username,password) VALUES(?,?)", (u, generate_password_hash(p)))
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
        c.close()
        return "Кино олдсонгүй", 404
    if request.method == "POST":
        method = request.form.get("method")
        proof = request.files.get("proof")
        proofname = ""
        if proof and proof.filename:
            proofname = secure_filename(proof.filename)
            proofname = f"{session['uid']}_{mid}_{proofname}"
            proof.save(os.path.join(UPLOAD, proofname))
        c.execute("INSERT INTO payments(user_id,movie_id,method,proof) VALUES(?,?,?,?)", (session["uid"], mid, method, proofname))
        c.commit()
        c.close()
        flash("Хүсэлт илгээгдлээ. Админ төлбөрийг шалгасны дараа бүх VIP кино үзэх эрх нээгдэнэ.")
        return redirect(url_for("movie", mid=mid))
    c.close()
    return render_template("payment.html", movie=m)

@app.route("/admin")
@login_required
@admin_required
def admin():
    c = db()
    movies = c.execute("SELECT * FROM movies ORDER BY id DESC").fetchall()
    payments = c.execute("""SELECT p.*,u.username,m.title FROM payments p JOIN users u ON u.id=p.user_id JOIN movies m ON m.id=p.movie_id ORDER BY p.id DESC""").fetchall()
    c.close()
    return render_template("admin.html", movies=movies, payments=payments)

@app.route("/admin/add", methods=["POST"])
@login_required
@admin_required
def add_movie():
    title = request.form.get("title", "").strip()
    st = request.form.get("source_type")
    source = request.form.get("source", "").strip()
    upload = request.files.get("video")
    if st == "file" and upload and upload.filename:
        name = secure_filename(upload.filename)
        upload.save(os.path.join(UPLOAD, name))
        source = url_for("uploaded", name=name)
    if not title or not source:
        flash("Киноны нэр болон видео эх сурвалж шаардлагатай.")
        return redirect(url_for("admin"))
    c = db()
    c.execute("INSERT INTO movies(title,description,category,poster,source_type,source,paid) VALUES(?,?,?,?,?,?,?)",
              (title, request.form.get("description", ""), request.form.get("category", "Бусад"), request.form.get("poster", ""), st, source, 1 if request.form.get("paid") else 0))
    c.commit()
    c.close()
    flash("Кино нэмэгдлээ.")
    return redirect(url_for("admin"))

@app.route("/uploads/<path:name>")
def uploaded(name):
    return send_from_directory(UPLOAD, name)

@app.route("/admin/payment/<int:pid>/<action>")
@login_required
@admin_required
def payment_action(pid, action):
    if action not in ("approve", "reject"):
        return "Bad request", 400
    c = db()
    p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if p:
        c.execute("UPDATE payments SET status=? WHERE id=?", ("approved" if action == "approve" else "rejected", pid))
        if action == "approve":
            method = p["method"] or ""
            days = 30
            if "3 Сар" in method:
                days = 90
            elif "6 Сар" in method:
                days = 180
            elif "1 Жил" in method:
                days = 365
            u = c.execute("SELECT vip_until FROM users WHERE id=?", (p["user_id"],)).fetchone()
            base = datetime.utcnow()
            if u and u["vip_until"]:
                try:
                    cur = datetime.fromisoformat(u["vip_until"])
                    if cur > base:
                        base = cur
                except ValueError:
                    pass
            new_until = (base + timedelta(days=days)).isoformat()
            c.execute("UPDATE users SET vip_until=? WHERE id=?", (new_until, p["user_id"]))
    c.commit()
    c.close()
    return redirect(url_for("admin"))

@app.route("/admin/delete/<int:mid>")
@login_required
@admin_required
def delete_movie(mid):
    c = db()
    c.execute("DELETE FROM movies WHERE id=?", (mid,))
    c.commit()
    c.close()
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(debug=True)
