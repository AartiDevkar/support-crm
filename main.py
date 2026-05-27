from fastapi import FastAPI, Form
from pydantic import BaseModel
from typing import Optional
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import uuid
from datetime import datetime

# ─── App Setup ───────────────────────────────────────────
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ─── Database Setup ──────────────────────────────────────
def get_db():
    conn = sqlite3.connect("crm.db")
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn

def create_tables():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT UNIQUE,
            customer_name TEXT,
            customer_email TEXT,
            subject TEXT,
            description TEXT,
            status TEXT DEFAULT 'Open',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            note_text TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

create_tables()  # runs when app starts

# ─── Helper: Generate Ticket ID ──────────────────────────
def generate_ticket_id():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return f"TKT-{str(count + 1).zfill(3)}"  # TKT-001, TKT-002...

# ─── API ROUTES ──────────────────────────────────────────

# 1. CREATE a new ticket
@app.post("/api/tickets")
def create_ticket(
    customer_name: str = Form(...),
    customer_email: str = Form(...),
    subject: str = Form(...),
    description: str = Form(...)
):
    ticket_id = generate_ticket_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("""
        INSERT INTO tickets (ticket_id, customer_name, customer_email, subject, description, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'Open', ?, ?)
    """, (ticket_id, customer_name, customer_email, subject, description, now, now))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

# 2. GET all tickets (with optional search and filter)
@app.get("/api/tickets")
def get_tickets(status: str = None, search: str = None):
    conn = get_db()
    query = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (customer_name LIKE ? OR customer_email LIKE ? OR subject LIKE ? OR ticket_id LIKE ?)"
        params.extend([f"%{search}%"] * 4)
    query += " ORDER BY created_at DESC"
    tickets = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(t) for t in tickets]

# 3. GET a single ticket by ID
@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    conn = get_db()
    ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    notes = conn.execute("SELECT * FROM notes WHERE ticket_id = ? ORDER BY created_at DESC", (ticket_id,)).fetchall()
    conn.close()
    if not ticket:
        return {"error": "Ticket not found"}
    result = dict(ticket)
    result["notes"] = [dict(n) for n in notes]
    return result

# 4. UPDATE ticket status and/or add a note
class UpdateTicket(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None

@app.put("/api/tickets/{ticket_id}")
def update_ticket(ticket_id: str, body: UpdateTicket):
    conn = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if body.status:
        conn.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE ticket_id = ?", (body.status, now, ticket_id))
    if body.note:
        conn.execute("INSERT INTO notes (ticket_id, note_text, created_at) VALUES (?, ?, ?)", (ticket_id, body.note, now))
    conn.commit()
    conn.close()
    return {"success": True, "updated_at": now}

# ─── PAGE ROUTES (HTML Pages) ─────────────────────────────
from fastapi.responses import FileResponse

# Home page
@app.get("/")
def home():
    return FileResponse("templates/index.html")

# Create ticket page
@app.get("/create")
def create_page():
    return FileResponse("templates/create.html")

# Ticket detail page
@app.get("/ticket/{ticket_id}")
def ticket_detail(ticket_id: str):
    return FileResponse("templates/detail.html")