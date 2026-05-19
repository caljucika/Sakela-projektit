import os
import sqlite3
import smtplib
import zipfile
import tempfile
import boto3
from datetime import datetime
from functools import wraps
from email.message import EmailMessage

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

LITTERA_OPTIONS = [
    ("1", "1 = Maa- ja pohjarakennus"),
    ("2", "2 = Rakennustekniset työt"),
    ("21", "21 = Betonityöt"),
    ("22", "22 = Teräsrakenteet"),
    ("23", "23 = Väestönsuojarakenteet"),
    ("3", "3 = Vesikattorakenteet"),
    ("31", "31 = Bitumikermityöt"),
    ("4", "4 = Täydentävät rakenteet"),
    ("41", "41 = Ovet ja ikkunat"),
    ("42", "42 = Lukitus"),
    ("43", "43 = Listoitus"),
    ("44", "44 = Kaiteet ja käsijohteet"),
    ("45", "45 = Väliseinät"),
    ("46", "46 = Listoitus"),
    ("5", "5 = Pintarakenteet"),
    ("51", "51 = Laatoitus"),
    ("52", "52 = Mattotyöt"),
    ("53", "53 = Massalattiat"),
    ("54", "54 = Alakatot"),
    ("55", "55 = Tasoitus- ja maalaus"),
    ("6", "6 = Kalusteet, varusteet ja laitteet"),
    ("61", "61 = Kiintokalusteet"),
    ("62", "62 = Varusteet"),
    ("63", "63 = Laitoskeittiö"),
    ("67", "67 = Väestönsuojanvarusteet"),
    ("7", "7 = Konetekniset työt"),
    ("71", "71 = LVI"),
    ("72", "72 = Automaatio"),
    ("73", "73 = Sähkö"),
    ("74", "74 = Hissi"),
]


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-later")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

DATABASE = os.path.join(DATA_DIR, "sakela_portal.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")

ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "webp", "gif",
    "dwg", "doc", "docx", "xls", "xlsx", "txt",
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_price_value(price_text):
    if not price_text:
        return 999999999999

    cleaned = ""

    for char in price_text:
        if char.isdigit() or char in [",", "."]:
            cleaned += char

    cleaned = cleaned.replace(" ", "").replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return 999999999999


def make_stored_filename(original_filename):
    safe_name = secure_filename(original_filename)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    return f"{timestamp}_{safe_name}"


def safe_remove_file(stored_filename):
    if not stored_filename:
        return

    path = os.path.join(UPLOAD_FOLDER, stored_filename)

    if os.path.exists(path):
        os.remove(path)


def send_email(to_email, subject, body, reply_to=None):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("FROM_EMAIL", smtp_user)

    if not smtp_host or not smtp_user or not smtp_password or not from_email:
        print("EMAIL SKIPPED: SMTP settings missing")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=5) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    return True

def upload_backup_to_r2(local_file_path, object_name):
    r2_endpoint = os.environ.get("R2_ENDPOINT")
    r2_bucket = os.environ.get("R2_BUCKET")
    r2_access_key = os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not r2_endpoint or not r2_bucket or not r2_access_key or not r2_secret_key:
        print("R2 BACKUP SKIPPED: missing settings")
        return False

    s3 = boto3.client(
        "s3",
        endpoint_url=r2_endpoint,
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        region_name="auto",
    )

    s3.upload_file(local_file_path, r2_bucket, object_name)
    return True


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table_name, column_name):
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(column["name"] == column_name for column in columns)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'contractor',
            contractor_status TEXT NOT NULL DEFAULT 'pending',
            company_name TEXT,
            phone TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            location TEXT,
            description TEXT,
            deadline TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            Littera TEXT,
            deadline TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS section_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            uploaded_by INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_type TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (section_id) REFERENCES project_sections(id) ON DELETE CASCADE,
            FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            contractor_id INTEGER NOT NULL,
            price TEXT,
            message TEXT,
            attachment_original_filename TEXT,
            attachment_stored_filename TEXT,
            status TEXT NOT NULL DEFAULT 'submitted',
            created_at TEXT NOT NULL,
            FOREIGN KEY (section_id) REFERENCES project_sections(id) ON DELETE CASCADE,
            FOREIGN KEY (contractor_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            contractor_id INTEGER NOT NULL,
            invited_at TEXT NOT NULL,
            UNIQUE(project_id, contractor_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS section_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            contractor_id INTEGER NOT NULL,
            invited_at TEXT NOT NULL,
            UNIQUE(section_id, contractor_id)
        )
    """)

    if not column_exists(conn, "bids", "attachment_original_filename"):
        cur.execute("ALTER TABLE bids ADD COLUMN attachment_original_filename TEXT")

    if not column_exists(conn, "bids", "attachment_stored_filename"):
        cur.execute("ALTER TABLE bids ADD COLUMN attachment_stored_filename TEXT")

    if not column_exists(conn, "project_sections", "Littera"):
        cur.execute("ALTER TABLE project_sections ADD COLUMN Littera TEXT")

    if not column_exists(conn, "projects", "visibility"):
        cur.execute("ALTER TABLE projects ADD COLUMN visibility TEXT DEFAULT 'public'")

    if not column_exists(conn, "users", "business_id"):
        cur.execute("ALTER TABLE users ADD COLUMN business_id TEXT")

    if not column_exists(conn, "users", "contractor_litteras"):
        cur.execute("ALTER TABLE users ADD COLUMN contractor_litteras TEXT")
    if not column_exists(conn, "bids", "matches_request"):
        cur.execute("ALTER TABLE bids ADD COLUMN matches_request TEXT DEFAULT 'yes'")

    conn.commit()

    admin_email = "admin@sakela.fi"
    existing_admin = cur.execute(
        "SELECT id FROM users WHERE email = ?",
        (admin_email,)
    ).fetchone()

    if not existing_admin:
        cur.execute("""
            INSERT INTO users (
                name,
                email,
                password_hash,
                role,
                contractor_status,
                company_name,
                phone,
                business_id,
                contractor_litteras,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Sakela Admin",
            admin_email,
            generate_password_hash("admin123"),
            "admin",
            "approved",
            "Sakela",
            "",
            "",
            "",
            now_str(),
        ))

        conn.commit()

    conn.close()


def current_user():
    if "user_id" not in session:
        return None

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    conn.close()

    return user


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Kirjaudu ensin sisään.", "warning")
            return redirect(url_for("login"))

        return view_func(*args, **kwargs)

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()

        if not user or user["role"] != "admin":
            flash("Sinulla ei ole oikeutta tähän näkymään.", "danger")
            return redirect(url_for("index"))

        return view_func(*args, **kwargs)

    return wrapper


def staff_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()

        if not user:
            flash("Kirjaudu ensin sisään.", "warning")
            return redirect(url_for("login"))

        if user["role"] not in ["admin", "manager"]:
            flash("Sinulla ei ole oikeutta tähän näkymään.", "danger")
            return redirect(url_for("index"))

        return view_func(*args, **kwargs)

    return wrapper


def approved_contractor_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()

        if not user:
            flash("Kirjaudu ensin sisään.", "warning")
            return redirect(url_for("login"))

        if user["role"] in ["admin", "manager"]:
            return view_func(*args, **kwargs)

        if user["role"] == "contractor" and user["contractor_status"] == "approved":
            return view_func(*args, **kwargs)

        flash("Tilisi odottaa vielä hyväksyntää.", "warning")
        return redirect(url_for("pending"))

    return wrapper



@app.context_processor
def inject_user():
    return {"current_user": current_user()}


@app.route("/")
def index():
    user = current_user()

    if user:
        if user["role"] in ["admin", "manager"]:
            return redirect(url_for("admin_dashboard"))

        if user["contractor_status"] == "approved":
            return redirect(url_for("contractor_dashboard"))

        return redirect(url_for("pending"))

    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        company_name = request.form.get("company_name", "").strip()
        phone = request.form.get("phone", "").strip()
        business_id = request.form.get("business_id", "").strip()
        contractor_litteras = ", ".join(request.form.getlist("contractor_litteras"))

        if not name or not email or not password or not company_name:
            flash("Täytä kaikki pakolliset kentät.", "danger")
            return redirect(url_for("register"))

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing:
            conn.close()
            flash("Tällä sähköpostilla on jo käyttäjä.", "danger")
            return redirect(url_for("register"))

        conn.execute("""
            INSERT INTO users (
                name,
                email,
                password_hash,
                role,
                contractor_status,
                company_name,
                phone,
                business_id,
                contractor_litteras,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            email,
            generate_password_hash(password),
            "contractor",
            "pending",
            company_name,
            phone,
            business_id,
            contractor_litteras,
            now_str(),
        ))

        conn.commit()
        conn.close()

        flash("Rekisteröityminen vastaanotettu. Tili odottaa hyväksyntää.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", littera_options=LITTERA_OPTIONS)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Virheellinen sähköposti tai salasana.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        flash("Kirjautuminen onnistui.", "success")

        if user["role"] in ["admin", "manager"]:
            return redirect(url_for("admin_dashboard"))

        if user["contractor_status"] == "approved":
            return redirect(url_for("contractor_dashboard"))

        return redirect(url_for("pending"))

    return render_template("login.html")

@app.route("/tuki")
def support():
    return render_template("support.html")


@app.route("/tietosuoja")
def privacy():
    return render_template("privacy.html")


@app.route("/kayttoehdot")
def terms():
    return render_template("terms.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Kirjauduit ulos.", "success")
    return redirect(url_for("index"))


@app.route("/pending")
@login_required
def pending():
    user = current_user()

    if user["role"] in ["admin", "manager"]:
        return redirect(url_for("admin_dashboard"))

    if user["contractor_status"] == "approved":
        return redirect(url_for("contractor_dashboard"))

    return render_template("pending.html")


@app.route("/files/<int:file_id>/download")
@login_required
@approved_contractor_required
def download_section_file(file_id):
    conn = get_db()

    file_row = conn.execute("""
        SELECT *
        FROM section_files
        WHERE id = ?
    """, (file_id,)).fetchone()

    conn.close()

    if not file_row:
        flash("Tiedostoa ei löytynyt.", "danger")
        return redirect(url_for("index"))

    return send_from_directory(
        UPLOAD_FOLDER,
        file_row["stored_filename"],
        as_attachment=True,
        download_name=file_row["original_filename"],
    )


@app.route("/bid-attachments/<int:bid_id>/download")
@login_required
def download_bid_attachment(bid_id):
    user = current_user()
    conn = get_db()

    bid = conn.execute("""
        SELECT *
        FROM bids
        WHERE id = ?
    """, (bid_id,)).fetchone()

    conn.close()

    if not bid or not bid["attachment_stored_filename"]:
        flash("Tarjousliitettä ei löytynyt.", "danger")
        return redirect(url_for("index"))

    if user["role"] not in ["admin", "manager"] and bid["contractor_id"] != user["id"]:
        flash("Sinulla ei ole oikeutta tähän tiedostoon.", "danger")
        return redirect(url_for("index"))

    return send_from_directory(
        UPLOAD_FOLDER,
        bid["attachment_stored_filename"],
        as_attachment=True,
        download_name=bid["attachment_original_filename"],
    )


@app.route("/admin")
@login_required
@staff_required
def admin_dashboard():
    conn = get_db()

    stats = {
        "projects": conn.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"],
        "sections": conn.execute("SELECT COUNT(*) AS count FROM project_sections").fetchone()["count"],
        "bids": conn.execute("SELECT COUNT(*) AS count FROM bids").fetchone()["count"],
        "pending_contractors": conn.execute("""
            SELECT COUNT(*) AS count
            FROM users
            WHERE role = 'contractor' AND contractor_status = 'pending'
        """).fetchone()["count"],
    }

    recent_projects = conn.execute("""
        SELECT *
        FROM projects
        ORDER BY created_at DESC
        LIMIT 5
    """).fetchall()

    pending_contractors = conn.execute("""
        SELECT *
        FROM users
        WHERE role = 'contractor' AND contractor_status = 'pending'
        ORDER BY created_at DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        recent_projects=recent_projects,
        pending_contractors=pending_contractors,
    )

@app.route("/admin/managers/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_manager():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Täytä kaikki pakolliset kentät.", "danger")
            return redirect(url_for("new_manager"))

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing:
            conn.close()
            flash("Tällä sähköpostilla on jo käyttäjä.", "danger")
            return redirect(url_for("new_manager"))

        conn.execute("""
            INSERT INTO users (
                name,
                email,
                password_hash,
                role,
                contractor_status,
                company_name,
                phone,
                business_id,
                contractor_litteras,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            email,
            generate_password_hash(password),
            "manager",
            "approved",
            "Sakela",
            "",
            "",
            "",
            now_str(),
        ))

        conn.commit()
        conn.close()

        flash("Työnjohtaja lisätty.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("manager_form.html")


@app.route("/admin/contractors")
@login_required
@staff_required
def admin_contractors():
    conn = get_db()

    contractors = conn.execute("""
        SELECT *
        FROM users
        WHERE role = 'contractor'
        ORDER BY
            CASE
                WHEN company_name IS NULL OR company_name = ''
                THEN name
                ELSE company_name
            END COLLATE NOCASE ASC
    """).fetchall()

    conn.close()

    return render_template("admin_contractors.html", contractors=contractors)

@app.route("/admin/contractors/invite", methods=["POST"])
@login_required
@staff_required
def invite_new_contractor():
    email = request.form.get("email", "").strip().lower()
    message = request.form.get("message", "").strip()

    if not email:
        flash("Sähköposti on pakollinen.", "danger")
        return redirect(url_for("admin_contractors"))

    register_link = request.host_url.rstrip("/") + url_for("register")

    extra_message = ""

    if message:
        extra_message = f"""
Työnjohdon viesti:
{message}
"""

    email_subject = "Kutsu Sakela Urakkaportaaliin"

    email_body = f"""Hei,

Sinut on kutsuttu rekisteröitymään Sakela Urakkaportaaliin.

Portaalissa voit vastaanottaa tarjouspyyntöjä, ladata piirustuksia ja jättää tarjouksia sähköisesti.
{extra_message}

Rekisteröidy tästä:
{register_link}

Terveisin,
Sakela Urakkaportaali
"""

    try:
        if send_email(email, email_subject, email_body):
            flash("Kutsu lähetetty urakoitsijalle.", "success")
        else:
            flash("Kutsun lähetys epäonnistui.", "danger")
    except Exception as e:
        print("INVITE NEW CONTRACTOR EMAIL ERROR:", e)
        flash("Kutsun lähetys epäonnistui.", "danger")

    return redirect(url_for("admin_contractors"))


@app.route("/admin/contractors/<int:user_id>/approve", methods=["POST"])
@login_required
@staff_required
def approve_contractor(user_id):
    conn = get_db()

    conn.execute("""
        UPDATE users
        SET contractor_status = 'approved'
        WHERE id = ? AND role = 'contractor'
    """, (user_id,))

    conn.commit()
    conn.close()

    flash("Urakoitsija hyväksytty.", "success")
    return redirect(url_for("admin_contractors"))


@app.route("/admin/contractors/<int:user_id>/reject", methods=["POST"])
@login_required
@staff_required
def reject_contractor(user_id):
    conn = get_db()

    conn.execute("""
        UPDATE users
        SET contractor_status = 'rejected'
        WHERE id = ? AND role = 'contractor'
    """, (user_id,))

    conn.commit()
    conn.close()

    flash("Urakoitsija hylätty.", "success")
    return redirect(url_for("admin_contractors"))


@app.route("/admin/projects")
@login_required
@staff_required
def admin_projects():
    search = request.args.get("search", "").strip()
    show_all = request.args.get("all")

    conn = get_db()

    where_parts = []
    params = []

    if not show_all:
        where_parts.append("p.status = 'open'")

    if search:
        where_parts.append("""
            (
                p.title LIKE ?
                OR p.location LIKE ?
                OR p.description LIKE ?
            )
        """)
        params.extend([
            f"%{search}%",
            f"%{search}%",
            f"%{search}%",
        ])

    where_sql = ""

    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    projects = conn.execute(f"""
        SELECT
            p.*,
            COUNT(DISTINCT ps.id) AS section_count,
            COUNT(DISTINCT b.id) AS bid_count,
            MAX(b.created_at) AS latest_bid_at
        FROM projects p
        LEFT JOIN project_sections ps ON ps.project_id = p.id
        LEFT JOIN bids b ON b.section_id = ps.id
        {where_sql}
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, params).fetchall()

    conn.close()

    return render_template(
        "admin_projects.html",
        projects=projects,
        search=search,
        show_all=show_all,
    )


@app.route("/admin/projects/new", methods=["GET", "POST"])
@login_required
@staff_required
def new_project():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline", "").strip()
        status = request.form.get("status", "open")
        visibility = request.form.get("visibility", "public")

        if not title:
            flash("Projektin nimi on pakollinen.", "danger")
            return redirect(url_for("new_project"))

        conn = get_db()

        conn.execute("""
            INSERT INTO projects (
                title,
                location,
                description,
                deadline,
                status,
                visibility,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
            location,
            description,
            deadline,
            status,
            visibility,
            now_str(),
        ))

        conn.commit()
        conn.close()

        flash("Projekti lisätty.", "success")
        return redirect(url_for("admin_projects"))

    return render_template("project_form.html", project=None)


@app.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
@staff_required
def edit_project(project_id):
    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline", "").strip()
        status = request.form.get("status", "open")
        visibility = request.form.get("visibility", "public")

        if not title:
            conn.close()
            flash("Projektin nimi on pakollinen.", "danger")
            return redirect(url_for("edit_project", project_id=project_id))

        conn.execute("""
            UPDATE projects
            SET
                title = ?,
                location = ?,
                description = ?,
                deadline = ?,
                status = ?,
                visibility = ?
            WHERE id = ?
        """, (
            title,
            location,
            description,
            deadline,
            status,
            visibility,
            project_id,
        ))

        conn.commit()
        conn.close()

        flash("Projektin tiedot päivitetty.", "success")
        return redirect(url_for("admin_project_detail", project_id=project_id))

    conn.close()

    return render_template("project_form.html", project=project)


@app.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_project(project_id):
    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    section_ids = conn.execute(
        "SELECT id FROM project_sections WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    for section in section_ids:
        section_id = section["id"]

        files = conn.execute(
            "SELECT stored_filename FROM section_files WHERE section_id = ?",
            (section_id,)
        ).fetchall()

        for file_row in files:
            safe_remove_file(file_row["stored_filename"])

        attachments = conn.execute(
            "SELECT attachment_stored_filename FROM bids WHERE section_id = ?",
            (section_id,)
        ).fetchall()

        for bid in attachments:
            safe_remove_file(bid["attachment_stored_filename"])

        conn.execute("DELETE FROM section_files WHERE section_id = ?", (section_id,))
        conn.execute("DELETE FROM bids WHERE section_id = ?", (section_id,))
        conn.execute("DELETE FROM project_sections WHERE id = ?", (section_id,))

    conn.execute("DELETE FROM project_invites WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    conn.commit()
    conn.close()

    flash("Projekti poistettu.", "success")
    return redirect(url_for("admin_projects"))


@app.route("/admin/projects/<int:project_id>")
@login_required
@staff_required
def admin_project_detail(project_id):
    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    sections = conn.execute("""
        SELECT
            ps.*,
            COUNT(DISTINCT b.id) AS bid_count,
            COUNT(DISTINCT sf.id) AS file_count
        FROM project_sections ps
        LEFT JOIN bids b ON b.section_id = ps.id
        LEFT JOIN section_files sf ON sf.section_id = ps.id
        WHERE ps.project_id = ?
        GROUP BY ps.id
        ORDER BY ps.created_at DESC
    """, (project_id,)).fetchall()

    conn.close()

    return render_template(
        "admin_project_detail.html",
        project=project,
        sections=sections,
    )


@app.route("/admin/projects/<int:project_id>/sections/new", methods=["GET", "POST"])
@login_required
@staff_required
def new_section(project_id):
    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        littera = request.form.get("Littera", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline", "").strip()
        status = request.form.get("status", "open")

        if not title:
            conn.close()
            flash("Urakkaosan nimi on pakollinen.", "danger")
            return redirect(url_for("new_section", project_id=project_id))

        conn.execute("""
            INSERT INTO project_sections (
                project_id,
                title,
                description,
                Littera,
                deadline,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            project_id,
            title,
            description,
            littera,
            deadline,
            status,
            now_str(),
        ))

        conn.commit()
        conn.close()

        flash("Urakkaosa lisätty.", "success")
        return redirect(url_for("admin_project_detail", project_id=project_id))

    conn.close()

    return render_template("section_form.html", project=project, section=None, littera_options=LITTERA_OPTIONS)


@app.route("/admin/sections/<int:section_id>/edit", methods=["GET", "POST"])
@login_required
@staff_required
def edit_section(section_id):
    conn = get_db()

    section = conn.execute("""
        SELECT
            ps.*,
            p.title AS project_title
        FROM project_sections ps
        JOIN projects p ON p.id = ps.project_id
        WHERE ps.id = ?
    """, (section_id,)).fetchone()

    if not section:
        conn.close()
        flash("Urakkaosaa ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (section["project_id"],)
    ).fetchone()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        littera = request.form.get("Littera", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline", "").strip()
        status = request.form.get("status", "open")

        if not title:
            conn.close()
            flash("Urakkaosan nimi on pakollinen.", "danger")
            return redirect(url_for("edit_section", section_id=section_id))

        conn.execute("""
            UPDATE project_sections
            SET
                title = ?,
                description = ?,
                Littera = ?,
                deadline = ?,
                status = ?
            WHERE id = ?
        """, (
            title,
            description,
            littera,
            deadline,
            status,
            section_id,
        ))

        conn.commit()
        conn.close()

        flash("Urakkaosan tiedot päivitetty.", "success")
        return redirect(url_for("admin_section_detail", section_id=section_id))

    conn.close()

    return render_template("section_form.html", project=project, section=section, littera_options=LITTERA_OPTIONS)


@app.route("/admin/sections/<int:section_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_section(section_id):
    conn = get_db()

    section = conn.execute(
        "SELECT * FROM project_sections WHERE id = ?",
        (section_id,)
    ).fetchone()

    if not section:
        conn.close()
        flash("Urakkaosaa ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    files = conn.execute(
        "SELECT stored_filename FROM section_files WHERE section_id = ?",
        (section_id,)
    ).fetchall()

    attachments = conn.execute(
        "SELECT attachment_stored_filename FROM bids WHERE section_id = ?",
        (section_id,)
    ).fetchall()

    for file_row in files:
        safe_remove_file(file_row["stored_filename"])

    for bid in attachments:
        safe_remove_file(bid["attachment_stored_filename"])

    project_id = section["project_id"]

    conn.execute("DELETE FROM section_files WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM bids WHERE section_id = ?", (section_id,))
    conn.execute("DELETE FROM project_sections WHERE id = ?", (section_id,))

    conn.commit()
    conn.close()

    flash("Urakkaosa poistettu.", "success")
    return redirect(url_for("admin_project_detail", project_id=project_id))


@app.route("/admin/sections/<int:section_id>")
@login_required
@staff_required
def admin_section_detail(section_id):
    conn = get_db()

    section = conn.execute("""
        SELECT
            ps.*,
            p.title AS project_title,
            p.id AS project_id
        FROM project_sections ps
        JOIN projects p ON p.id = ps.project_id
        WHERE ps.id = ?
    """, (section_id,)).fetchone()

    if not section:
        conn.close()
        flash("Urakkaosaa ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    files = conn.execute("""
        SELECT
            sf.*,
            u.name AS uploaded_by_name
        FROM section_files sf
        JOIN users u ON u.id = sf.uploaded_by
        WHERE sf.section_id = ?
        ORDER BY sf.created_at DESC
    """, (section_id,)).fetchall()

    bids = conn.execute("""
        SELECT
            b.*,
            u.name AS contractor_name,
            u.company_name,
            u.email,
            u.phone,
            u.business_id
        FROM bids b
        JOIN users u ON u.id = b.contractor_id
        WHERE b.section_id = ?
    """, (section_id,)).fetchall()

    bids = sorted(
        bids,
        key=lambda bid: parse_price_value(bid["price"])
    )

    approved_contractors = conn.execute("""
        SELECT
            id,
            name,
            company_name,
            email
        FROM users
        WHERE role = 'contractor'
        AND contractor_status = 'approved'
        ORDER BY company_name ASC, name ASC
    """).fetchall()

    conn.close()

    return render_template(
        "admin_section_detail.html",
        section=section,
        files=files,
        bids=bids,
        littera_options=LITTERA_OPTIONS,
        approved_contractors=approved_contractors,
    )

@app.route("/admin/sections/<int:section_id>/files/upload", methods=["POST"])
@login_required
@staff_required
def upload_section_file(section_id):
    note = request.form.get("note", "").strip()
    uploaded_files = request.files.getlist("files")

    uploaded_files = [
        file for file in uploaded_files
        if file and file.filename
    ]

    if not uploaded_files:
        flash("Valitse vähintään yksi ladattava tiedosto.", "danger")
        return redirect(url_for("admin_section_detail", section_id=section_id))

    conn = get_db()

    section = conn.execute(
        "SELECT id FROM project_sections WHERE id = ?",
        (section_id,)
    ).fetchone()

    if not section:
        conn.close()
        flash("Urakkaosaa ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    saved_count = 0
    skipped_count = 0

    for uploaded_file in uploaded_files:
        if not allowed_file(uploaded_file.filename):
            skipped_count += 1
            continue

        original_filename = uploaded_file.filename
        stored_filename = make_stored_filename(original_filename)
        file_type = original_filename.rsplit(".", 1)[1].lower()

        uploaded_file.save(os.path.join(UPLOAD_FOLDER, stored_filename))

        conn.execute("""
            INSERT INTO section_files (
                section_id,
                uploaded_by,
                original_filename,
                stored_filename,
                file_type,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            section_id,
            session["user_id"],
            original_filename,
            stored_filename,
            file_type,
            note,
            now_str(),
        ))

        saved_count += 1

    conn.commit()
    conn.close()

    if saved_count and skipped_count:
        flash(f"{saved_count} tiedostoa lisätty. {skipped_count} tiedostoa ohitettiin virheellisen tiedostomuodon vuoksi.", "warning")
    elif saved_count:
        flash(f"{saved_count} tiedostoa lisätty urakkaosaan.", "success")
    else:
        flash("Yhtään tiedostoa ei lisätty. Tarkista tiedostomuodot.", "danger")

    return redirect(url_for("admin_section_detail", section_id=section_id))


@app.route("/admin/files/<int:file_id>/delete", methods=["POST"])
@login_required
@staff_required
def delete_section_file(file_id):
    conn = get_db()

    file_row = conn.execute(
        "SELECT * FROM section_files WHERE id = ?",
        (file_id,)
    ).fetchone()

    if not file_row:
        conn.close()
        flash("Tiedostoa ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    section_id = file_row["section_id"]

    safe_remove_file(file_row["stored_filename"])

    conn.execute("DELETE FROM section_files WHERE id = ?", (file_id,))

    conn.commit()
    conn.close()

    flash("Tiedosto poistettu.", "success")
    return redirect(url_for("admin_section_detail", section_id=section_id))


@app.route("/admin/bids/<int:bid_id>/status", methods=["POST"])
@login_required
@staff_required
def update_bid_status(bid_id):
    new_status = request.form.get("status", "submitted")
    allowed_statuses = {"submitted", "reviewing", "accepted", "rejected"}

    if new_status not in allowed_statuses:
        flash("Virheellinen tarjousstatus.", "danger")
        return redirect(url_for("admin_projects"))

    conn = get_db()

    bid = conn.execute(
        "SELECT * FROM bids WHERE id = ?",
        (bid_id,)
    ).fetchone()

    if not bid:
        conn.close()
        flash("Tarjousta ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    conn.execute(
        "UPDATE bids SET status = ? WHERE id = ?",
        (new_status, bid_id)
    )

    conn.commit()
    conn.close()

    flash("Tarjouksen status päivitetty.", "success")
    return redirect(url_for("admin_section_detail", section_id=bid["section_id"]))



@app.route("/admin/projects/<int:project_id>/invite", methods=["POST"])
@login_required
@staff_required
def invite_contractors_to_project(project_id):
    littera = request.form.get("littera", "").strip()
    message = request.form.get("message", "").strip()
    section_id = request.form.get("section_id")

    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt.", "danger")
        return redirect(url_for("admin_projects"))

    section = None

    if section_id:
        section = conn.execute("""
            SELECT *
            FROM project_sections
            WHERE id = ?
            AND project_id = ?
        """, (section_id, project_id)).fetchone()

        if not section:
            conn.close()
            flash("Urakkaosaa ei löytynyt.", "danger")
            return redirect(url_for("admin_project_detail", project_id=project_id))

    if littera:
        contractors = conn.execute("""
            SELECT *
            FROM users
            WHERE role = 'contractor'
            AND contractor_status = 'approved'
            AND contractor_litteras LIKE ?
        """, (f"%{littera}%",)).fetchall()
    else:
        contractors = conn.execute("""
            SELECT *
            FROM users
            WHERE role = 'contractor'
            AND contractor_status = 'approved'
        """).fetchall()

    invited_count = 0
    invited_contractors = []

    for contractor in contractors:
        try:
            conn.execute("""
                INSERT INTO project_invites (
                    project_id,
                    contractor_id,
                    invited_at
                )
                VALUES (?, ?, ?)
            """, (
                project_id,
                contractor["id"],
                now_str(),
            ))

            invited_count += 1

        except sqlite3.IntegrityError:
            pass

        if section:
            conn.execute("""
                INSERT OR IGNORE INTO section_invites (
                    section_id,
                    contractor_id,
                    invited_at
                )
                VALUES (?, ?, ?)
            """, (
                section["id"],
                contractor["id"],
                now_str(),
            ))

        invited_contractors.append(contractor)

    conn.commit()
    conn.close()

    email_sent_count = 0
    email_failed_count = 0

    for contractor in invited_contractors:
        if section:
            project_link = request.host_url.rstrip("/") + url_for(
                "contractor_section_detail",
                section_id=section["id"]
            )
        else:
            project_link = request.host_url.rstrip("/") + url_for(
                "contractor_project_detail",
                project_id=project_id
            )

        email_subject = f"Kutsu tarjouspyyntöön: {project['title']}"

        section_text = ""

        if section:
            section_text = f"""
Urakkaosa:
{section['title']}

Littera:
{section['Littera'] or '-'}
"""

        extra_message = ""

        if message:
            extra_message = f"""
Työnjohdon viesti:
{message}
"""

        email_body = f"""Hei {contractor['name']},

Sinut on kutsuttu Sakela Urakkaportaalin tarjouspyyntöön.

Projekti:
{project['title']}

Sijainti:
{project['location'] or '-'}

Rakennusaika:
{project['deadline'] or '-'}
{section_text}
{extra_message}

Avaa tarjouspyyntö tästä:
{project_link}

Terveisin,
Sakela Urakkaportaali
"""

        try:
            if send_email(contractor["email"], email_subject, email_body):
                email_sent_count += 1
            else:
                email_failed_count += 1
        except Exception as e:
            print("EMAIL ERROR:", e)
            email_failed_count += 1

    flash(
        f"Kutsuttu {invited_count} uutta urakoitsijaa. "
        f"Sähköposteja lähetetty: {email_sent_count}. "
        f"Epäonnistui: {email_failed_count}.",
        "success"
    )

    if section:
        return redirect(url_for("admin_section_detail", section_id=section["id"]))

    return redirect(url_for("admin_project_detail", project_id=project_id))

@app.route("/admin/sections/<int:section_id>/invite-single", methods=["POST"])
@login_required
@staff_required
def invite_single_contractor_to_section(section_id):
    contractor_id = request.form.get("contractor_id")
    message = request.form.get("message", "").strip()

    if not contractor_id:
        flash("Valitse urakoitsija.", "danger")
        return redirect(url_for("admin_section_detail", section_id=section_id))

    conn = get_db()

    section = conn.execute("""
        SELECT
            ps.*,
            p.id AS project_id,
            p.title AS project_title,
            p.location AS project_location,
            p.deadline AS project_deadline
        FROM project_sections ps
        JOIN projects p ON p.id = ps.project_id
        WHERE ps.id = ?
    """, (section_id,)).fetchone()

    contractor = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
        AND role = 'contractor'
        AND contractor_status = 'approved'
    """, (contractor_id,)).fetchone()

    if not section or not contractor:
        conn.close()
        flash("Urakkaosaa tai urakoitsijaa ei löytynyt.", "danger")
        return redirect(url_for("admin_section_detail", section_id=section_id))

    now = now_str()

    conn.execute("""
        INSERT OR IGNORE INTO project_invites (
            project_id,
            contractor_id,
            invited_at
        )
        VALUES (?, ?, ?)
    """, (
        section["project_id"],
        contractor["id"],
        now,
    ))

    conn.execute("""
        INSERT OR IGNORE INTO section_invites (
            section_id,
            contractor_id,
            invited_at
        )
        VALUES (?, ?, ?)
    """, (
        section["id"],
        contractor["id"],
        now,
    ))

    conn.commit()
    conn.close()

    project_link = request.host_url.rstrip("/") + url_for(
        "contractor_section_detail",
        section_id=section["id"]
    )

    extra_message = ""

    if message:
        extra_message = f"""
Työnjohdon viesti:
{message}
"""

    email_subject = f"Kutsu tarjouspyyntöön: {section['project_title']}"

    email_body = f"""Hei {contractor['name']},

Sinut on kutsuttu Sakela Urakkaportaalin tarjouspyyntöön.

Projekti:
{section['project_title']}

Sijainti:
{section['project_location'] or '-'}

Rakennusaika:
{section['project_deadline'] or '-'}

Urakkaosa:
{section['title']}

Littera:
{section['Littera'] or '-'}
{extra_message}

Avaa tarjouspyyntö tästä:
{project_link}

Terveisin,
Sakela Urakkaportaali
"""

    try:
        if send_email(contractor["email"], email_subject, email_body):
            flash("Urakoitsija kutsuttu ja sähköposti lähetetty.", "success")
        else:
            flash("Kutsu tallennettu, mutta sähköpostin lähetys epäonnistui.", "warning")
    except Exception as e:
        print("EMAIL ERROR:", e)
        flash("Kutsu tallennettu, mutta sähköpostin lähetys epäonnistui.", "warning")

    return redirect(url_for("admin_section_detail", section_id=section_id))


@app.route("/admin/backup/r2", methods=["POST"])
@login_required
@admin_required
def create_r2_backup():
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"sakela_backup_{timestamp}.zip"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = os.path.join(tmpdir, backup_filename)

            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                if os.path.exists(DATABASE):
                    zipf.write(DATABASE, "sakela_portal.db")

                if os.path.exists(UPLOAD_FOLDER):
                    for root, dirs, files in os.walk(UPLOAD_FOLDER):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, DATA_DIR)
                            zipf.write(file_path, arcname)

            uploaded = upload_backup_to_r2(
                backup_path,
                f"manual/{backup_filename}"
            )

            if uploaded:
                flash("Backup luotu ja lähetetty Cloudflare R2:een.", "success")
            else:
                flash("Backup luotiin, mutta R2-asetukset puuttuvat.", "warning")

    except Exception as e:
        print("BACKUP ERROR:", e)
        flash(f"Backup epäonnistui: {e}", "danger")

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/bids")
@login_required
@staff_required
def admin_bids():
    conn = get_db()

    rows = conn.execute("""
        SELECT
            b.*,
            u.name AS contractor_name,
            u.company_name,
            u.email,
            u.phone,
            u.business_id,
            ps.id AS section_id,
            ps.title AS section_title,
            ps.Littera,
            p.id AS project_id,
            p.title AS project_title,
            p.location AS project_location
        FROM bids b
        JOIN users u ON u.id = b.contractor_id
        JOIN project_sections ps ON ps.id = b.section_id
        JOIN projects p ON p.id = ps.project_id
        ORDER BY p.title ASC, ps.title ASC, b.created_at DESC
    """).fetchall()

    conn.close()

    grouped = {}

    for bid in rows:
        project_id = bid["project_id"]
        section_id = bid["section_id"]

        if project_id not in grouped:
            grouped[project_id] = {
                "project_title": bid["project_title"],
                "project_location": bid["project_location"],
                "sections": {}
            }

        if section_id not in grouped[project_id]["sections"]:
            grouped[project_id]["sections"][section_id] = {
                "section_title": bid["section_title"],
                "Littera": bid["Littera"],
                "bids": []
            }

        grouped[project_id]["sections"][section_id]["bids"].append(bid)

    for project in grouped.values():
        for section in project["sections"].values():
            section["bids"] = sorted(
                section["bids"],
                key=lambda bid: parse_price_value(bid["price"])
            )

    return render_template(
        "admin_bids.html",
        grouped=grouped,
    )


@app.route("/contractor")
@login_required
@approved_contractor_required
def contractor_dashboard():
    conn = get_db()

    projects = conn.execute("""
        SELECT
            p.*,
            COUNT(DISTINCT ps.id) AS section_count,
            COUNT(DISTINCT b.id) AS bid_count,
            MAX(b.created_at) AS latest_bid_at

        FROM projects p

        JOIN project_sections ps
            ON ps.project_id = p.id

        JOIN section_invites si
            ON si.section_id = ps.id
            AND si.contractor_id = ?

        LEFT JOIN bids b
            ON b.section_id = ps.id
            AND b.contractor_id = ?

        WHERE p.status = 'open'
        AND ps.status = 'open'

        GROUP BY p.id
        ORDER BY p.created_at DESC
        LIMIT 5
    """, (
        session["user_id"],
        session["user_id"],
    )).fetchall()

    my_bids = conn.execute("""
        SELECT
            b.*,
            ps.title AS section_title,
            p.title AS project_title
        FROM bids b
        JOIN project_sections ps ON ps.id = b.section_id
        JOIN projects p ON p.id = ps.project_id
        WHERE b.contractor_id = ?
        ORDER BY b.created_at DESC
        LIMIT 5
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "contractor_dashboard.html",
        projects=projects,
        my_bids=my_bids,
    )


@app.route("/projects")
@login_required
@approved_contractor_required
def contractor_projects():
    show_all = request.args.get("all")
    user_id = session["user_id"]

    conn = get_db()

    status_filter = ""

    if not show_all:
        status_filter = "AND p.status = 'open'"

    projects = conn.execute(f"""
        SELECT
            p.*,
            COUNT(DISTINCT ps.id) AS section_count,
            COUNT(DISTINCT b.id) AS bid_count,
            MAX(b.created_at) AS latest_bid_at
        FROM projects p

        JOIN project_sections ps
            ON ps.project_id = p.id

        JOIN section_invites si
            ON si.section_id = ps.id
            AND si.contractor_id = ?

        LEFT JOIN bids b
            ON b.section_id = ps.id
            AND b.contractor_id = ?

        WHERE ps.status = 'open'
        {status_filter}

        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, (
        user_id,
        user_id,
    )).fetchall()

    conn.close()

    return render_template(
        "contractor_projects.html",
        projects=projects,
        show_all=show_all,
    )


@app.route("/projects/<int:project_id>")
@login_required
@approved_contractor_required
def contractor_project_detail(project_id):
    conn = get_db()

    project = conn.execute("""
        SELECT
            p.*
        FROM projects p
        LEFT JOIN project_invites pi
            ON pi.project_id = p.id
            AND pi.contractor_id = ?
        WHERE p.id = ?
        AND p.status = 'open'
        AND (
            COALESCE(p.visibility, 'public') = 'public'
            OR pi.id IS NOT NULL
        )
    """, (session["user_id"], project_id)).fetchone()

    if not project:
        conn.close()
        flash("Projektia ei löytynyt tai se ei ole avoinna.", "danger")
        return redirect(url_for("contractor_projects"))

    sections = conn.execute("""
        SELECT
            ps.*,
            (
                SELECT COUNT(*)
                FROM bids b
                WHERE b.section_id = ps.id
                AND b.contractor_id = ?
            ) AS has_my_bid,
            (
                SELECT COUNT(*)
                FROM section_files sf
                WHERE sf.section_id = ps.id
            ) AS file_count
        FROM project_sections ps
        JOIN section_invites si
            ON si.section_id = ps.id
            AND si.contractor_id = ?
        WHERE ps.project_id = ?
        AND ps.status = 'open'
        ORDER BY ps.created_at DESC
    """, (
        session["user_id"],
        session["user_id"],
        project_id,
    )).fetchall()

    conn.close()

    return render_template(
        "contractor_project_detail.html",
        project=project,
        sections=sections,
    )


@app.route("/sections/<int:section_id>", methods=["GET", "POST"])
@login_required
@approved_contractor_required
def contractor_section_detail(section_id):
    conn = get_db()

    section = conn.execute("""
        SELECT
            ps.*,
            p.title AS project_title,
            p.id AS project_id,
            p.location AS project_location
        FROM project_sections ps
        JOIN projects p ON p.id = ps.project_id

        JOIN section_invites si
            ON si.section_id = ps.id
            AND si.contractor_id = ?

        LEFT JOIN project_invites pi
            ON pi.project_id = p.id
            AND pi.contractor_id = ?

        WHERE ps.id = ?
        AND ps.status = 'open'
        AND p.status = 'open'
        AND (
            COALESCE(p.visibility, 'public') = 'public'
            OR pi.id IS NOT NULL
        )
    """, (
        session["user_id"],
        session["user_id"],
        section_id,
    )).fetchone()

    if not section:
        conn.close()
        flash("Urakkaosaa ei löytynyt tai se ei ole avoinna.", "danger")
        return redirect(url_for("contractor_projects"))

    existing_bid = conn.execute("""
        SELECT *
        FROM bids
        WHERE section_id = ?
        AND contractor_id = ?
    """, (section_id, session["user_id"])).fetchone()

    files = conn.execute("""
        SELECT
            sf.*,
            u.name AS uploaded_by_name
        FROM section_files sf
        JOIN users u ON u.id = sf.uploaded_by
        WHERE sf.section_id = ?
        ORDER BY sf.created_at DESC
    """, (section_id,)).fetchall()

    if request.method == "POST":
        price = request.form.get("price", "").strip()
        matches_request = request.form.get("matches_request", "yes")
        message = request.form.get("message", "").strip()
        attachment = request.files.get("attachment")

        if not price:
            conn.close()
            flash("Tarjouksen hinta on pakollinen.", "danger")
            return redirect(url_for("contractor_section_detail", section_id=section_id))

        attachment_original_filename = None
        attachment_stored_filename = None

        if attachment and attachment.filename:
            if not allowed_file(attachment.filename):
                conn.close()
                flash("Tarjousliitteen tiedostomuoto ei ole sallittu.", "danger")
                return redirect(url_for("contractor_section_detail", section_id=section_id))

            attachment_original_filename = attachment.filename
            attachment_stored_filename = make_stored_filename(attachment_original_filename)
            attachment.save(os.path.join(UPLOAD_FOLDER, attachment_stored_filename))

        if existing_bid:
            if attachment_stored_filename:
                safe_remove_file(existing_bid["attachment_stored_filename"])

                conn.execute("""
                    UPDATE bids
                    SET
                        price = ?,
                        matches_request = ?,
                        message = ?,
                        attachment_original_filename = ?,
                        attachment_stored_filename = ?,
                        status = 'submitted',
                        created_at = ?
                    WHERE id = ?
                """, (
                    price,
                    matches_request,
                    message,
                    attachment_original_filename,
                    attachment_stored_filename,
                    now_str(),
                    existing_bid["id"],
                ))
            else:
                conn.execute("""
                    UPDATE bids
                    SET
                        price = ?,
                        matches_request = ?,
                        message = ?,
                        status = 'submitted',
                        created_at = ?
                    WHERE id = ?
                """, (
                    price,
                    matches_request,
                    message,
                    now_str(),
                    existing_bid["id"],
                ))

            flash("Tarjous päivitetty.", "success")
        else:
            conn.execute("""
                INSERT INTO bids (
                    section_id,
                    contractor_id,
                    price,
                    matches_request,
                    message,
                    attachment_original_filename,
                    attachment_stored_filename,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                section_id,
                session["user_id"],
                price,
                matches_request,
                message,
                attachment_original_filename,
                attachment_stored_filename,
                "submitted",
                now_str(),
            ))

            flash("Tarjous lähetetty.", "success")

        conn.commit()
        conn.close()

        return redirect(url_for("contractor_section_detail", section_id=section_id))

    conn.close()

    return render_template(
        "contractor_section_detail.html",
        section=section,
        existing_bid=existing_bid,
        files=files,
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True)