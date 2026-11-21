import os
import sqlite3
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "confplan.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deployment")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','attendee')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        capacity INTEGER NOT NULL DEFAULT 50,
        location TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS conferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        venue TEXT NOT NULL,
        event_date TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conference_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        speaker TEXT NOT NULL,
        description TEXT,
        room_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        capacity INTEGER NOT NULL DEFAULT 50,
        FOREIGN KEY(conference_id) REFERENCES conferences(id) ON DELETE CASCADE,
        FOREIGN KEY(room_id) REFERENCES rooms(id)
    );
    CREATE TABLE IF NOT EXISTS registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attendee_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(attendee_id, session_id),
        FOREIGN KEY(attendee_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );
    """)
    # Starter admin account
    admin = db.execute("SELECT id FROM users WHERE email = ?", ("admin@confplan.com",)).fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            ("ConfPlan Admin", "admin@confplan.com",
             generate_password_hash("admin123"), "admin")
        )
    # Starter rooms
    if db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO rooms(name,capacity,location) VALUES(?,?,?)",
            [("Auditorium A", 150, "Block A, Ground Floor"),
             ("Seminar Hall B", 80, "Block B, First Floor"),
             ("Meeting Room C", 35, "Block C, Second Floor")]
        )
    db.commit()
    db.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access is required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def index():
    db = get_db()
    conferences = db.execute("""
        SELECT c.*, COUNT(DISTINCT s.id) AS session_count
        FROM conferences c LEFT JOIN sessions s ON s.conference_id=c.id
        GROUP BY c.id ORDER BY c.event_date
    """).fetchall()
    return render_template("index.html", conferences=conferences)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 6:
            flash("Enter all details. Password must contain at least 6 characters.", "danger")
            return redirect(url_for("register"))
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                (name, email, generate_password_hash(password), "attendee")
            )
            db.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That email address is already registered.", "danger")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash("Welcome back, " + user["name"] + "!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    if session["role"] == "admin":
        stats = {
            "attendees": db.execute("SELECT COUNT(*) FROM users WHERE role='attendee'").fetchone()[0],
            "conferences": db.execute("SELECT COUNT(*) FROM conferences").fetchone()[0],
            "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "registrations": db.execute("SELECT COUNT(*) FROM registrations").fetchone()[0],
        }
        recent = db.execute("""
            SELECT r.registered_at, u.name AS attendee, s.title AS session_title
            FROM registrations r JOIN users u ON u.id=r.attendee_id
            JOIN sessions s ON s.id=r.session_id
            ORDER BY r.registered_at DESC LIMIT 8
        """).fetchall()
        return render_template("admin_dashboard.html", stats=stats, recent=recent)
    registrations = db.execute("""
        SELECT s.*, c.title AS conference_title, c.event_date,
               r.name AS room_name, r.location AS room_location
        FROM registrations reg JOIN sessions s ON s.id=reg.session_id
        JOIN conferences c ON c.id=s.conference_id
        JOIN rooms r ON r.id=s.room_id
        WHERE reg.attendee_id=?
        ORDER BY c.event_date, s.start_time
    """, (session["user_id"],)).fetchall()
    return render_template("attendee_dashboard.html", registrations=registrations)


@app.route("/conferences")
def conferences():
    rows = get_db().execute("""
        SELECT c.*, COUNT(s.id) AS session_count
        FROM conferences c LEFT JOIN sessions s ON s.conference_id=c.id
        GROUP BY c.id ORDER BY c.event_date
    """).fetchall()
    return render_template("conferences.html", conferences=rows)


@app.route("/admin/conference/add", methods=["GET", "POST"])
@admin_required
def add_conference():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        venue = request.form.get("venue", "").strip()
        event_date = request.form.get("event_date", "")
        description = request.form.get("description", "").strip()
        if not title or not venue or not event_date:
            flash("Title, venue and date are required.", "danger")
        else:
            get_db().execute(
                "INSERT INTO conferences(title,description,venue,event_date) VALUES(?,?,?,?)",
                (title, description, venue, event_date)
            )
            get_db().commit()
            flash("Conference created successfully.", "success")
            return redirect(url_for("conferences"))
    return render_template("conference_form.html")


@app.route("/conference/<int:conference_id>")
def conference_detail(conference_id):
    db = get_db()
    conference = db.execute("SELECT * FROM conferences WHERE id=?", (conference_id,)).fetchone()
    if not conference:
        flash("Conference not found.", "danger")
        return redirect(url_for("conferences"))
    sessions = db.execute("""
        SELECT s.*, r.name AS room_name, r.location AS room_location,
               (SELECT COUNT(*) FROM registrations x WHERE x.session_id=s.id) AS enrolled
        FROM sessions s JOIN rooms r ON r.id=s.room_id
        WHERE s.conference_id=? ORDER BY s.start_time
    """, (conference_id,)).fetchall()
    return render_template("conference_detail.html", conference=conference, sessions=sessions)


@app.route("/admin/session/add/<int:conference_id>", methods=["GET", "POST"])
@admin_required
def add_session(conference_id):
    db = get_db()
    conference = db.execute("SELECT * FROM conferences WHERE id=?", (conference_id,)).fetchone()
    if not conference:
        flash("Conference not found.", "danger")
        return redirect(url_for("conferences"))
    rooms = db.execute("SELECT * FROM rooms ORDER BY name").fetchall()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        speaker = request.form.get("speaker", "").strip()
        description = request.form.get("description", "").strip()
        room_id = request.form.get("room_id", type=int)
        start = request.form.get("start_time", "")
        end = request.form.get("end_time", "")
        capacity = request.form.get("capacity", 50, type=int)
        try:
            valid_times = bool(start and end and datetime.strptime(start, "%H:%M") < datetime.strptime(end, "%H:%M"))
        except ValueError:
            valid_times = False
        room = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if not title or not speaker or not room or not valid_times or capacity < 1:
            flash("Check the session details, room, capacity and time range.", "danger")
        elif capacity > room["capacity"]:
            flash(f"Session capacity cannot exceed the room capacity ({room['capacity']}).", "danger")
        else:
            conflict = db.execute("""
                SELECT s.id FROM sessions s
                WHERE s.room_id=? AND s.conference_id=?
                AND NOT (? >= s.end_time OR ? <= s.start_time)
            """, (room_id, conference_id, start, end)).fetchone()
            if conflict:
                flash("This room is already booked during that time.", "danger")
            else:
                db.execute("""
                    INSERT INTO sessions(conference_id,title,speaker,description,room_id,start_time,end_time,capacity)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (conference_id, title, speaker, description, room_id, start, end, capacity))
                db.commit()
                flash("Session added successfully.", "success")
                return redirect(url_for("conference_detail", conference_id=conference_id))
    return render_template("session_form.html", conference=conference, rooms=rooms)


@app.route("/admin/session/delete/<int:session_id>", methods=["POST"])
@admin_required
def delete_session(session_id):
    db = get_db()
    row = db.execute("SELECT conference_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row:
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        db.commit()
        flash("Session deleted.", "info")
        return redirect(url_for("conference_detail", conference_id=row["conference_id"]))
    flash("Session not found.", "danger")
    return redirect(url_for("conferences"))


@app.route("/admin/rooms", methods=["GET", "POST"])
@admin_required
def rooms():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        capacity = request.form.get("capacity", 0, type=int)
        if not name or not location or capacity < 1:
            flash("Enter a room name, location and positive capacity.", "danger")
        else:
            try:
                db.execute("INSERT INTO rooms(name,capacity,location) VALUES(?,?,?)", (name, capacity, location))
                db.commit()
                flash("Room added.", "success")
                return redirect(url_for("rooms"))
            except sqlite3.IntegrityError:
                flash("A room with that name already exists.", "danger")
    all_rooms = db.execute("SELECT * FROM rooms ORDER BY name").fetchall()
    return render_template("rooms.html", rooms=all_rooms)


@app.route("/register-session/<int:session_id>", methods=["POST"])
@login_required
def register_session(session_id):
    if session["role"] != "attendee":
        flash("Only attendee accounts can register for sessions.", "warning")
        return redirect(url_for("dashboard"))
    db = get_db()
    target = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not target:
        flash("Session not found.", "danger")
        return redirect(url_for("conferences"))
    enrolled = db.execute("SELECT COUNT(*) FROM registrations WHERE session_id=?", (session_id,)).fetchone()[0]
    if enrolled >= target["capacity"]:
        flash("This session is full.", "danger")
        return redirect(url_for("conference_detail", conference_id=target["conference_id"]))
    overlap = db.execute("""
        SELECT s.title FROM registrations r JOIN sessions s ON s.id=r.session_id
        WHERE r.attendee_id=? AND s.conference_id=?
        AND NOT (? >= s.end_time OR ? <= s.start_time)
    """, (session["user_id"], target["conference_id"], target["start_time"], target["end_time"])).fetchone()
    if overlap:
        flash("You already have another session at this time: " + overlap["title"], "danger")
    else:
        try:
            db.execute("INSERT INTO registrations(attendee_id,session_id) VALUES(?,?)",
                       (session["user_id"], session_id))
            db.commit()
            flash("You are registered for this session.", "success")
        except sqlite3.IntegrityError:
            flash("You are already registered for this session.", "warning")
    return redirect(url_for("conference_detail", conference_id=target["conference_id"]))


@app.route("/cancel-registration/<int:session_id>", methods=["POST"])
@login_required
def cancel_registration(session_id):
    get_db().execute("DELETE FROM registrations WHERE attendee_id=? AND session_id=?",
                     (session["user_id"], session_id))
    get_db().commit()
    flash("Registration cancelled.", "info")
    return redirect(url_for("dashboard"))


@app.route("/my-route")
@login_required
def my_route():
    if session["role"] != "attendee":
        return redirect(url_for("dashboard"))
    route = get_db().execute("""
        SELECT c.title AS conference_title, c.event_date, c.venue,
               s.title AS session_title, s.speaker, s.start_time, s.end_time,
               r.name AS room_name, r.location AS room_location
        FROM registrations reg JOIN sessions s ON s.id=reg.session_id
        JOIN conferences c ON c.id=s.conference_id
        JOIN rooms r ON r.id=s.room_id
        WHERE reg.attendee_id=?
        ORDER BY c.event_date, s.start_time
    """, (session["user_id"],)).fetchall()
    return render_template("my_route.html", route=route)


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", message="The page you requested could not be found."), 404


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
