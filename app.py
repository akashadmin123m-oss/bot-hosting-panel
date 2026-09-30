import os, sys, uuid, time, signal, subprocess, threading, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STORAGE_ROOT = os.environ.get("BOT_STORAGE", os.path.join(BASE_DIR, "bot_storage"))
os.makedirs(STORAGE_ROOT, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-in-production")
db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "pinium.db"))
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
db = SQLAlchemy(app)

MAX_BOTS = int(os.environ.get("MAX_BOTS_PER_USER", "15"))
processes = {}
process_lock = threading.Lock()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Bot(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    bot_file = db.Column(db.String(255), default="bot.py")
    req_file = db.Column(db.String(255), default="requirements.txt")
    status = db.Column(db.String(20), default="stopped")
    pid = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    log = db.Column(db.Text, default="")

def utcnow():
    return datetime.now(timezone.utc)

def current_user():
    uid = session.get("uid")
    return db.session.get(User, uid) if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or not u.is_admin:
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper

def bot_dir(bot):
    path = os.path.join(STORAGE_ROOT, str(bot.user_id), bot.id)
    os.makedirs(path, exist_ok=True)
    return path

def append_log(bot, text):
    bot.log = ((bot.log or "") + text)[-12000:]
    db.session.commit()

def run_bot(bot):
    path = bot_dir(bot)
    bot_path = os.path.join(path, "bot.py")
    req_path = os.path.join(path, "requirements.txt")
    if not os.path.exists(bot_path):
        bot.status = "error"
        bot.log = (bot.log or "") + "\nMissing bot.py"
        db.session.commit()
        return

    # Optional dependency installation. Set INSTALL_REQUIREMENTS=false if packages
    # are preinstalled in your runner image.
    if os.environ.get("INSTALL_REQUIREMENTS", "true").lower() == "true" and os.path.exists(req_path):
        try:
            pip = [sys.executable, "-m", "pip", "install", "-r", req_path, "--disable-pip-version-check"]
            p = subprocess.run(pip, cwd=path, capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                bot.status = "error"
                bot.log = (bot.log or "") + "\n[pip]\n" + (p.stdout + p.stderr)[-5000:]
                db.session.commit()
                return
        except Exception as e:
            bot.status = "error"
            bot.log = (bot.log or "") + f"\n[pip error] {e}"
            db.session.commit()
            return

    try:
        proc = subprocess.Popen(
            [sys.executable, "bot.py"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        with process_lock:
            processes[bot.id] = proc
        bot.pid = proc.pid
        bot.status = "running"
        bot.started_at = utcnow()
        bot.log = (bot.log or "") + f"\n[{utcnow().isoformat()}] Bot started (PID {proc.pid})\n"
        db.session.commit()

        for line in iter(proc.stdout.readline, ""):
            with app.app_context():
                b = db.session.get(Bot, bot.id)
                if b:
                    b.log = ((b.log or "") + line)[-12000:]
                    db.session.commit()
        proc.stdout.close()
        code = proc.wait()
        with app.app_context():
            b = db.session.get(Bot, bot.id)
            if b:
                b.status = "stopped" if code == 0 else "crashed"
                b.pid = None
                b.log = ((b.log or "") + f"\n[process exited: {code}]\n")[-12000:]
                db.session.commit()
        with process_lock:
            processes.pop(bot.id, None)
    except Exception as e:
        with app.app_context():
            b = db.session.get(Bot, bot.id)
            if b:
                b.status = "error"
                b.pid = None
                b.log = ((b.log or "") + f"\n[runner error] {e}\n")[-12000:]
                db.session.commit()
        with process_lock:
            processes.pop(bot.id, None)

def start_bot(bot):
    with process_lock:
        existing = processes.get(bot.id)
        if existing and existing.poll() is None:
            bot.status = "running"
            db.session.commit()
            return
    bot.status = "starting"
    db.session.commit()
    threading.Thread(target=run_bot, args=(bot,), daemon=True).start()

def stop_bot(bot):
    with process_lock:
        proc = processes.get(bot.id)
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    bot.status = "stopped"
    bot.pid = None
    db.session.commit()

def owned_bot(bot_id):
    u = current_user()
    bot = db.session.get(Bot, bot_id)
    if not u or not bot or (bot.user_id != u.id and not u.is_admin):
        return None
    return bot

@app.context_processor
def inject():
    return {"user": current_user(), "max_bots": MAX_BOTS, "site_name": os.environ.get("SITE_NAME", "Pinium Host")}

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": os.environ.get("SITE_NAME", "Pinium Host")}, 200

@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user() else "login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not name or not email or len(password) < 6:
            flash("Name, email and a 6+ character password are required.", "error")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("register.html")
        u = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(u); db.session.commit()
        session["uid"] = u.id
        return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        u = User.query.filter_by(email=email).first()
        if not u or not check_password_hash(u.password_hash, password):
            flash("Invalid login details.", "error")
            return render_template("login.html")
        session["uid"] = u.id
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    bots = Bot.query.filter_by(user_id=u.id).order_by(Bot.created_at.desc()).all()
    return render_template("dashboard.html", bots=bots)

@app.route("/bot/create", methods=["POST"])
@login_required
def create_bot():
    u = current_user()
    count = Bot.query.filter_by(user_id=u.id).count()
    if count >= MAX_BOTS:
        flash(f"Maximum {MAX_BOTS} bots per user.", "error")
        return redirect(url_for("dashboard"))
    bot_file = request.files.get("bot_file")
    req_file = request.files.get("req_file")
    name = request.form.get("name","").strip() or f"Bot-{count+1}"
    if not bot_file or not req_file:
        flash("Upload both bot.py and requirements.txt.", "error")
        return redirect(url_for("dashboard"))
    if bot_file.filename != "bot.py" or req_file.filename != "requirements.txt":
        flash("Files must be named exactly bot.py and requirements.txt.", "error")
        return redirect(url_for("dashboard"))
    bid = str(uuid.uuid4())
    bot = Bot(id=bid, user_id=u.id, name=name)
    db.session.add(bot); db.session.commit()
    path = bot_dir(bot)
    bot_file.save(os.path.join(path, "bot.py"))
    req_file.save(os.path.join(path, "requirements.txt"))
    flash("Bot saved. Press Run to start it.", "success")
    return redirect(url_for("dashboard"))

@app.route("/bot/<bot_id>/run", methods=["POST"])
@login_required
def bot_run(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    start_bot(bot)
    return redirect(url_for("dashboard"))

@app.route("/bot/<bot_id>/stop", methods=["POST"])
@login_required
def bot_stop(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    stop_bot(bot)
    return redirect(url_for("dashboard"))

@app.route("/bot/<bot_id>/restart", methods=["POST"])
@login_required
def bot_restart(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    stop_bot(bot)
    time.sleep(0.2)
    start_bot(bot)
    return redirect(url_for("dashboard"))

@app.route("/bot/<bot_id>/delete", methods=["POST"])
@login_required
def bot_delete(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    stop_bot(bot)
    path = bot_dir(bot)
    try:
        shutil = __import__("shutil")
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
    db.session.delete(bot); db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/bot/<bot_id>/editor")
@login_required
def editor(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    path = bot_dir(bot)
    def read(name):
        p=os.path.join(path,name)
        return open(p, "r", encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
    return render_template("editor.html", bot=bot, bot_code=read("bot.py"), req_code=read("requirements.txt"))

@app.route("/bot/<bot_id>/save", methods=["POST"])
@login_required
def save_code(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return ("Not found", 404)
    path = bot_dir(bot)
    bot_code = request.form.get("bot_code","")
    req_code = request.form.get("req_code","")
    if len(bot_code.encode()) > 1024*1024 or len(req_code.encode()) > 256*1024:
        flash("File is too large.", "error")
        return redirect(url_for("editor", bot_id=bot.id))
    open(os.path.join(path,"bot.py"),"w",encoding="utf-8").write(bot_code)
    open(os.path.join(path,"requirements.txt"),"w",encoding="utf-8").write(req_code)
    flash("Code saved.", "success")
    if request.form.get("restart") == "1":
        stop_bot(bot); time.sleep(0.2); start_bot(bot)
    return redirect(url_for("editor", bot_id=bot.id))

@app.route("/bot/<bot_id>/logs")
@login_required
def logs(bot_id):
    bot = owned_bot(bot_id)
    if not bot: return jsonify(error="not found"), 404
    return jsonify(status=bot.status, pid=bot.pid, log=bot.log or "")

@app.route("/api/bots")
@login_required
def api_bots():
    u=current_user()
    bots=Bot.query.filter_by(user_id=u.id).order_by(Bot.created_at.desc()).all()
    return jsonify([{
        "id":b.id,"name":b.name,"status":b.status,"pid":b.pid,
        "created_at":b.created_at.isoformat() if b.created_at else None,
        "started_at":b.started_at.isoformat() if b.started_at else None
    } for b in bots])

@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", users=User.query.order_by(User.created_at.desc()).all(), bots=Bot.query.order_by(Bot.created_at.desc()).all())

@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    u=db.session.get(User,user_id)
    if u and not u.is_admin:
        for b in Bot.query.filter_by(user_id=u.id).all():
            stop_bot(b)
            import shutil
            shutil.rmtree(bot_dir(b), ignore_errors=True)
            db.session.delete(b)
        db.session.delete(u); db.session.commit()
    return redirect(url_for("admin"))

with app.app_context():
    db.create_all()
    if not User.query.filter_by(email="admin@pinium.local").first():
        admin_user=User(name="Administrator",email="admin@pinium.local",password_hash=generate_password_hash(os.environ.get("ADMIN_PASSWORD","Admin@12345")),is_admin=True)
        db.session.add(admin_user); db.session.commit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","5000")), debug=False)
