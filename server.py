import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

DB_PATH = Path(os.environ.get("GTD_DB", "/home/pveadmin/assistant/gtd.db"))
NOTES_DIR = Path(os.environ.get("GTD_NOTES", "/home/pveadmin/assistant/notes"))
AUTH_TOKEN = os.environ.get("GTD_AUTH_TOKEN", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT    NOT NULL,
    captured_at TEXT    NOT NULL DEFAULT (datetime('now')),
    processed   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    outcome    TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    context    TEXT,
    status     TEXT NOT NULL DEFAULT 'next',
    due        TEXT,
    project_id INTEGER REFERENCES projects(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS waiting_for (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    what       TEXT NOT NULL,
    who        TEXT NOT NULL,
    due        TEXT,
    project_id INTEGER REFERENCES projects(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS someday (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    category   TEXT,
    phone      TEXT,
    email      TEXT,
    website    TEXT,
    notes      TEXT,
    project_id INTEGER REFERENCES projects(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS quotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id),
    project_id  INTEGER REFERENCES projects(id),
    amount      REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'EUR',
    scope       TEXT NOT NULL,
    valid_until TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS call_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    project_id INTEGER REFERENCES projects(id),
    outcome    TEXT NOT NULL,
    notes      TEXT,
    follow_up  TEXT,
    called_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    what      TEXT NOT NULL,
    notes     TEXT,
    logged_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


_CONTACT_MIGRATIONS = [
    "ALTER TABLE contacts ADD COLUMN category   TEXT",
    "ALTER TABLE contacts ADD COLUMN phone      TEXT",
    "ALTER TABLE contacts ADD COLUMN email      TEXT",
    "ALTER TABLE contacts ADD COLUMN website    TEXT",
    "ALTER TABLE contacts ADD COLUMN project_id INTEGER REFERENCES projects(id)",
]


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        for stmt in _CONTACT_MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


mcp = FastMCP("GTD Assistant")


@mcp.tool()
def capture(content: str) -> dict:
    """Capture anything to the inbox for later processing."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO inbox (content) VALUES (?) RETURNING id, content, captured_at",
            (content,),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def list_next_actions(context: str = None, project_id: int = None) -> list:
    """List next actions, optionally filtered by context or project."""
    with db() as conn:
        query = (
            "SELECT id, title, context, due, project_id "
            "FROM actions WHERE status = 'next'"
        )
        params: list = []
        if context:
            query += " AND context = ?"
            params.append(context)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY due NULLS LAST, id"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def add_action(
    title: str,
    context: str = None,
    due: str = None,
    project_id: int = None,
) -> dict:
    """Add a next action. due format: YYYY-MM-DD."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO actions (title, context, due, project_id) "
            "VALUES (?, ?, ?, ?) RETURNING id, title, context, due, project_id",
            (title, context, due, project_id),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def complete_action(action_id: int, notes: str = None) -> dict:
    """Mark an action done and optionally write to the log."""
    with db() as conn:
        conn.execute(
            "UPDATE actions SET status = 'done', updated_at = datetime('now') WHERE id = ?",
            (action_id,),
        )
        row = conn.execute(
            "SELECT title FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if notes and row:
            conn.execute(
                "INSERT INTO log (what, notes) VALUES (?, ?)", (row["title"], notes)
            )
    return {"action_id": action_id, "status": "done"}


@mcp.tool()
def add_waiting_for(
    what: str,
    who: str,
    due: str = None,
    project_id: int = None,
) -> dict:
    """Track something you're waiting for from someone. due format: YYYY-MM-DD."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO waiting_for (what, who, due, project_id) "
            "VALUES (?, ?, ?, ?) RETURNING id, what, who, due",
            (what, who, due, project_id),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def create_project(name: str, description: str = None) -> dict:
    """Create a new project. Returns id, name, status."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, outcome) VALUES (?, ?) RETURNING id, name, status",
            (name, description),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def list_projects(status: str = None) -> list:
    """List projects. status filter: active (default if omitted), someday, done."""
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT id, name, status FROM projects WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, status FROM projects WHERE status = 'active' ORDER BY id"
            ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def list_inbox() -> list:
    """List unprocessed inbox items."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, content, captured_at FROM inbox WHERE processed = 0 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def process_inbox(
    item_id: int,
    into: str,
    title: str = None,
    context: str = None,
    due: str = None,
    project_id: int = None,
    who: str = None,
    notes: str = None,
) -> dict:
    """
    Process an inbox item and remove it from the inbox.

    into must be one of: action, project, someday, waiting_for, trash.

    action      → requires title; optional context, due, project_id
    project     → requires title (used as project name); optional notes (outcome)
    someday     → requires title; optional notes
    waiting_for → requires title (what) and who; optional due, project_id
    trash       → no extra fields needed
    """
    if into not in ("action", "project", "someday", "waiting_for", "trash"):
        return {"error": f"Unknown destination: {into}"}

    with db() as conn:
        item = conn.execute(
            "SELECT id, content FROM inbox WHERE id = ? AND processed = 0", (item_id,)
        ).fetchone()
        if not item:
            return {"error": "Inbox item not found or already processed"}

        effective_title = title or item["content"]
        result: dict = {"item_id": item_id, "into": into}

        if into == "action":
            cur = conn.execute(
                "INSERT INTO actions (title, context, due, project_id) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (effective_title, context, due, project_id),
            )
            result["action_id"] = cur.fetchone()["id"]

        elif into == "project":
            cur = conn.execute(
                "INSERT INTO projects (name, outcome) VALUES (?, ?) RETURNING id",
                (effective_title, notes),
            )
            result["project_id"] = cur.fetchone()["id"]

        elif into == "someday":
            cur = conn.execute(
                "INSERT INTO someday (title, notes) VALUES (?, ?) RETURNING id",
                (effective_title, notes),
            )
            result["someday_id"] = cur.fetchone()["id"]

        elif into == "waiting_for":
            if not who:
                return {"error": "waiting_for requires who"}
            cur = conn.execute(
                "INSERT INTO waiting_for (what, who, due, project_id) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (effective_title, who, due, project_id),
            )
            result["waiting_for_id"] = cur.fetchone()["id"]

        conn.execute(
            "UPDATE inbox SET processed = 1 WHERE id = ?", (item_id,)
        )
    return result


@mcp.tool()
def get_project(project_id: int) -> dict:
    """Get a project with its open next actions and waiting-fors."""
    with db() as conn:
        proj = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj:
            return {"error": "Project not found"}
        actions = conn.execute(
            "SELECT id, title, context, due FROM actions "
            "WHERE project_id = ? AND status = 'next' ORDER BY due NULLS LAST",
            (project_id,),
        ).fetchall()
        waiting = conn.execute(
            "SELECT id, what, who, due FROM waiting_for WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return {
        **dict(proj),
        "next_actions": [dict(r) for r in actions],
        "waiting_for": [dict(r) for r in waiting],
    }


# ── Contacts ──────────────────────────────────────────────────────────────────

@mcp.tool()
def add_contact(
    name: str,
    category: str = None,
    phone: str = None,
    email: str = None,
    website: str = None,
    notes: str = None,
    project_id: int = None,
) -> dict:
    """Add a contact. category examples: architect, builder, plumber, family, friend."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (name, category, phone, email, website, notes, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id, name, category",
            (name, category, phone, email, website, notes, project_id),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def list_contacts(category: str = None, project_id: int = None) -> list:
    """List contacts, optionally filtered by category or project."""
    with db() as conn:
        query = "SELECT id, name, category, phone, email, website, project_id FROM contacts WHERE 1=1"
        params: list = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY name"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def update_contact(
    contact_id: int,
    name: str = None,
    category: str = None,
    phone: str = None,
    email: str = None,
    website: str = None,
    notes: str = None,
    project_id: int = None,
) -> dict:
    """Update any fields on a contact. Only provided fields are changed."""
    fields = {
        "name": name, "category": category, "phone": phone,
        "email": email, "website": website, "notes": notes,
        "project_id": project_id,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return {"error": "No fields provided"}
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with db() as conn:
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            [*updates.values(), contact_id],
        )
        row = conn.execute(
            "SELECT id, name, category, phone, email, website, project_id FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
    if not row:
        return {"error": "Contact not found"}
    return dict(row)


# ── Quotes ────────────────────────────────────────────────────────────────────

@mcp.tool()
def add_quote(
    contact_id: int,
    project_id: int,
    amount: float,
    scope: str,
    currency: str = "EUR",
    valid_until: str = None,
    notes: str = None,
) -> dict:
    """Add a quote from a contact for a project. valid_until format: YYYY-MM-DD."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO quotes (contact_id, project_id, amount, currency, scope, valid_until, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id, contact_id, project_id, amount, currency, scope, valid_until",
            (contact_id, project_id, amount, currency, scope, valid_until, notes),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def list_quotes(project_id: int) -> list:
    """List all quotes for a project, joined with contact name."""
    with db() as conn:
        rows = conn.execute(
            "SELECT q.id, c.name AS contact, q.amount, q.currency, q.scope, "
            "q.valid_until, q.notes, q.created_at "
            "FROM quotes q JOIN contacts c ON c.id = q.contact_id "
            "WHERE q.project_id = ? ORDER BY q.amount",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Call log ──────────────────────────────────────────────────────────────────

@mcp.tool()
def log_call(
    contact_id: int,
    outcome: str,
    notes: str = None,
    follow_up: str = None,
    project_id: int = None,
) -> dict:
    """
    Log a call with a contact.
    outcome: reached, no_answer, voicemail, callback
    follow_up: free text or date for reminder.
    """
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO call_log (contact_id, project_id, outcome, notes, follow_up) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id, contact_id, outcome, called_at",
            (contact_id, project_id, outcome, notes, follow_up),
        )
        row = cur.fetchone()
    return dict(row)


@mcp.tool()
def list_calls(
    contact_id: int = None,
    project_id: int = None,
    since: str = None,
) -> list:
    """
    List call log entries. Filters are optional and combinable.
    since format: YYYY-MM-DD
    """
    with db() as conn:
        query = (
            "SELECT cl.id, c.name AS contact, cl.outcome, cl.notes, "
            "cl.follow_up, cl.called_at "
            "FROM call_log cl JOIN contacts c ON c.id = cl.contact_id WHERE 1=1"
        )
        params: list = []
        if contact_id:
            query += " AND cl.contact_id = ?"
            params.append(contact_id)
        if project_id:
            query += " AND cl.project_id = ?"
            params.append(project_id)
        if since:
            query += " AND cl.called_at >= ?"
            params.append(since)
        query += " ORDER BY cl.called_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Notes ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_notes(project_id: int) -> list:
    """List note filenames for a project."""
    folder = NOTES_DIR / str(project_id)
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.md"))


@mcp.tool()
def read_note(project_id: int, name: str) -> dict:
    """Read a markdown note. name is the filename without .md extension."""
    path = NOTES_DIR / str(project_id) / f"{name}.md"
    if not path.exists():
        return {"error": f"Note '{name}' not found for project {project_id}"}
    return {"project_id": project_id, "name": name, "content": path.read_text()}


@mcp.tool()
def write_note(project_id: int, name: str, content: str) -> dict:
    """Write (create or overwrite) a markdown note."""
    folder = NOTES_DIR / str(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text(content)
    return {"project_id": project_id, "name": name, "bytes": len(content.encode())}


# ── Maintenance ───────────────────────────────────────────────────────────────

@mcp.tool()
def update_action(
    action_id: int,
    title: str = None,
    context: str = None,
    due: str = None,
    project_id: int = None,
) -> dict:
    """Update any fields on a next action. Only provided fields are changed."""
    fields = {"title": title, "context": context, "due": due, "project_id": project_id}
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return {"error": "No fields provided"}
    updates["updated_at"] = "datetime('now')"
    set_clause = ", ".join(
        f"{k} = datetime('now')" if k == "updated_at" else f"{k} = ?"
        for k in updates
    )
    params = [v for k, v in updates.items() if k != "updated_at"]
    with db() as conn:
        conn.execute(
            f"UPDATE actions SET {set_clause} WHERE id = ?",
            [*params, action_id],
        )
        row = conn.execute(
            "SELECT id, title, context, due, project_id, status FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    if not row:
        return {"error": "Action not found"}
    return dict(row)


@mcp.tool()
def list_waiting_for(project_id: int = None) -> list:
    """List waiting-for items, optionally filtered by project."""
    with db() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT id, what, who, due, project_id FROM waiting_for "
                "WHERE project_id = ? ORDER BY due NULLS LAST, id",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, what, who, due, project_id FROM waiting_for "
                "ORDER BY due NULLS LAST, id"
            ).fetchall()
    return [dict(r) for r in rows]


class TokenAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not AUTH_TOKEN:
            return await call_next(request)
        bearer = request.headers.get("Authorization", "")
        token = request.query_params.get("token", "")
        if bearer == f"Bearer {AUTH_TOKEN}" or token == AUTH_TOKEN:
            return await call_next(request)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)


if __name__ == "__main__":
    import uvicorn

    init_db()
    app = mcp.http_app(path="/mcp")
    app.add_middleware(TokenAuth)
    uvicorn.run(app, host="127.0.0.1", port=8000)
