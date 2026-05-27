from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
from datetime import datetime
import hashlib
import secrets

app = FastAPI()

# ─── Session Store ───────────────────────────────────────
sessions = {}

# ─── Database ────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect("crm.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'customer',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT UNIQUE,
            subject TEXT,
            description TEXT,
            status TEXT DEFAULT 'Open',
            priority TEXT DEFAULT 'Medium',
            assigned_to INTEGER,
            created_by INTEGER,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (assigned_to) REFERENCES users(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            user_id INTEGER,
            note_text TEXT,
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Create default admin account
    admin = conn.execute("SELECT id FROM users WHERE email = 'admin@crm.com'").fetchone()
    if not admin:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pw = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute("""
            INSERT INTO users (name, email, password_hash, role, created_at)
            VALUES ('Admin', 'admin@crm.com', ?, 'admin', ?)
        """, (pw, now))

    conn.commit()
    conn.close()

create_tables()

# ─── Helpers ─────────────────────────────────────────────
def hash_pw(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_ticket_id():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return f"TKT-{str(count + 1).zfill(3)}"

def get_session(request: Request):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        return sessions[token]
    return None

def require_auth(request: Request):
    return get_session(request)

def require_admin(request: Request):
    s = get_session(request)
    return s if s and s.get("role") == "admin" else None

# ─── AUTH ROUTES ─────────────────────────────────────────

@app.get("/login")
def login_page(request: Request):
    if get_session(request):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse("templates/login.html")

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if user and user["password_hash"] == hash_pw(password):
        token = secrets.token_hex(32)
        sessions[token] = {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session_token", token, httponly=True)
        return resp
    resp = RedirectResponse(url="/login?error=1", status_code=303)
    return resp

@app.get("/signup")
def signup_page(request: Request):
    if get_session(request):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse("templates/signup.html")

@app.post("/signup")
def signup(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return RedirectResponse(url="/signup?error=1", status_code=303)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO users (name, email, password_hash, role, created_at)
        VALUES (?, ?, ?, 'customer', ?)
    """, (name, email, hash_pw(password), now))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/login?signup=1", status_code=303)

@app.get("/logout")
def logout(request: Request):
    token = request.cookies.get("session_token")
    if token in sessions:
        del sessions[token]
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp

# ─── SESSION API ─────────────────────────────────────────

@app.get("/api/me")
def get_me(request: Request):
    session = require_auth(request)
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return session

# ─── TICKET API ──────────────────────────────────────────

@app.post("/api/tickets")
def create_ticket(
    request: Request,
    subject: str = Form(...),
    description: str = Form(...),
    priority: str = Form("Medium")
):
    session = require_auth(request)
    if not session:
        return RedirectResponse(url="/login", status_code=303)
    ticket_id = generate_ticket_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("""
        INSERT INTO tickets (ticket_id, subject, description, status, priority, created_by, created_at, updated_at)
        VALUES (?, ?, ?, 'Open', ?, ?, ?, ?)
    """, (ticket_id, subject, description, priority, session["id"], now, now))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/tickets")
def get_tickets(request: Request, status: str = None, search: str = None, priority: str = None):
    session = require_auth(request)
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()

    if session["role"] == "customer":
        query = """SELECT t.*, u.name as creator_name, a.name as assignee_name
                   FROM tickets t
                   LEFT JOIN users u ON t.created_by = u.id
                   LEFT JOIN users a ON t.assigned_to = a.id
                   WHERE t.created_by = ?"""
        params = [session["id"]]
    else:
        query = """SELECT t.*, u.name as creator_name, a.name as assignee_name
                   FROM tickets t
                   LEFT JOIN users u ON t.created_by = u.id
                   LEFT JOIN users a ON t.assigned_to = a.id
                   WHERE 1=1"""
        params = []

    if status:
        query += " AND t.status = ?"
        params.append(status)
    if priority:
        query += " AND t.priority = ?"
        params.append(priority)
    if search:
        query += " AND (t.subject LIKE ? OR t.description LIKE ? OR t.ticket_id LIKE ?)"
        params.extend([f"%{search}%"] * 3)

    query += " ORDER BY t.created_at DESC"
    tickets = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(t) for t in tickets]

@app.get("/api/tickets/{ticket_id}")
def get_ticket(request: Request, ticket_id: str):
    session = require_auth(request)
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    ticket = conn.execute("""
        SELECT t.*, u.name as creator_name, a.name as assignee_name
        FROM tickets t
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.ticket_id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    if session["role"] == "customer" and ticket["created_by"] != session["id"]:
        conn.close()
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    notes = conn.execute("""
        SELECT n.*, u.name as author_name
        FROM notes n
        LEFT JOIN users u ON n.user_id = u.id
        WHERE n.ticket_id = ?
        ORDER BY n.created_at ASC
    """, (ticket_id,)).fetchall()
    conn.close()

    result = dict(ticket)
    result["notes"] = [dict(n) for n in notes]
    return result

class UpdateTicket(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[int] = None

@app.put("/api/tickets/{ticket_id}")
def update_ticket(ticket_id: str, body: UpdateTicket, request: Request):
    session = require_auth(request)
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if body.status and session["role"] in ["admin", "agent"]:
        conn.execute("UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?", (body.status, now, ticket_id))
    if body.priority and session["role"] in ["admin", "agent"]:
        conn.execute("UPDATE tickets SET priority=?, updated_at=? WHERE ticket_id=?", (body.priority, now, ticket_id))
    if body.assigned_to is not None and session["role"] == "admin":
        conn.execute("UPDATE tickets SET assigned_to=?, updated_at=? WHERE ticket_id=?", (body.assigned_to, now, ticket_id))
    if body.note:
        conn.execute("INSERT INTO notes (ticket_id, user_id, note_text, created_at) VALUES (?,?,?,?)",
                     (ticket_id, session["id"], body.note, now))
    conn.commit()
    conn.close()
    return {"success": True, "updated_at": now}

@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: str, request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    conn.execute("DELETE FROM tickets WHERE ticket_id=?", (ticket_id,))
    conn.execute("DELETE FROM notes WHERE ticket_id=?", (ticket_id,))
    conn.commit()
    conn.close()
    return {"success": True}

# ─── USER API ────────────────────────────────────────────

@app.get("/api/users")
def get_users(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    users = conn.execute("SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(u) for u in users]

@app.get("/api/agents")
def get_agents(request: Request):
    if not require_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    agents = conn.execute("SELECT id, name, email FROM users WHERE role IN ('admin','agent')").fetchall()
    conn.close()
    return [dict(a) for a in agents]

class UpdateUser(BaseModel):
    role: str

@app.put("/api/users/{user_id}")
def update_user(user_id: int, body: UpdateUser, request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/stats")
def get_stats(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    stats = {
        "total": conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        "open": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='Open'").fetchone()[0],
        "in_progress": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='In Progress'").fetchone()[0],
        "closed": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='Closed'").fetchone()[0],
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "agents": conn.execute("SELECT COUNT(*) FROM users WHERE role='agent'").fetchone()[0],
        "customers": conn.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0],
    }
    conn.close()
    return stats

# ─── PAGE ROUTES ─────────────────────────────────────────

@app.get("/")
def home(request: Request):
    if not require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse("templates/index.html")

@app.get("/create")
def create_page(request: Request):
    if not require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse("templates/create.html")

@app.get("/ticket/{ticket_id}")
def ticket_detail(request: Request, ticket_id: str):
    if not require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse("templates/detail.html")

@app.get("/admin")
def admin_page(request: Request):
    if not require_admin(request):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse("templates/admin.html")