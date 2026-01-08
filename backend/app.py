from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
from PIL import Image
import imagehash
import io
import os
import sqlite3
from datetime import datetime, timedelta, date
from typing import Optional, Literal, List, Dict, Any
from collections import Counter
import hashlib
import secrets
import re
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

DATASET_DIR = os.path.join(ROOT_DIR, "dataset")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
UPLOADS_DIR = os.path.join(ROOT_DIR, "uploads")

INDEX_PATH = os.path.join(DATASET_DIR, "index_phash.csv")
CATALOG_PATH = os.path.join(DATASET_DIR, "products_catalog_with_images.csv")
DB_PATH = os.path.join(ROOT_DIR, "app.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = FastAPI(title="Product Recognition & Reconciliation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(FRONTEND_DIR):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

if not os.path.exists(INDEX_PATH):
    raise RuntimeError(f"missing index file: {INDEX_PATH}")

df_index = pd.read_csv(INDEX_PATH)

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def table_columns(conn: sqlite3.Connection, table: str) -> set:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}

def ensure_columns(conn: sqlite3.Connection, table: str, cols_sql: List[str]):
    existing = table_columns(conn, table)
    cur = conn.cursor()
    for coldef in cols_sql:
        colname = coldef.split()[0]
        if colname not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    conn.commit()

def safe_ext(filename: str) -> str:
    filename = (filename or "").lower()
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        if filename.endswith(ext):
            return ext
    return ".jpg"

def to_int_or_str(x):
    s = str(x)
    return int(s) if s.isdigit() else s

def hash_password(pw: str, salt: str) -> str:
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()

TOKENS: Dict[str, Dict[str, Any]] = {}

def extract_token(request: Request, token_q: Optional[str]) -> Optional[str]:
    if token_q:
        return token_q
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    m = re.match(r"Bearer\s+(.+)", auth.strip(), flags=re.IGNORECASE)
    return m.group(1) if m else None

def require_role(request: Request, token_q: Optional[str], allowed: List[str]) -> str:
    token = extract_token(request, token_q)
    if not token or token not in TOKENS:
        raise HTTPException(status_code=401, detail="missing/invalid token")
    role = TOKENS[token]["role"]
    if role not in allowed:
        raise HTTPException(status_code=403, detail="forbidden")
    return token

def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        name TEXT,
        articleType TEXT,
        material TEXT,
        internal_tag TEXT,
        price REAL,
        image_path TEXT,
        phash TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_submissions (
        submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
        submitted_at TEXT,
        source TEXT,
        image_filename TEXT,
        extracted_text TEXT,
        suggested_id TEXT,
        suggested_name TEXT,
        suggested_articleType TEXT,
        suggested_material TEXT,
        suggested_internal_tag TEXT,
        suggested_price REAL,
        confidence REAL,
        distance INTEGER,
        employee_id TEXT,
        employee_note TEXT,
        edited_id TEXT,
        edited_name TEXT,
        edited_articleType TEXT,
        edited_material TEXT,
        edited_internal_tag TEXT,
        edited_price REAL,
        edited_confidence REAL,
        best_match_json TEXT,
        top5_json TEXT,
        status TEXT,
        supervisor_note TEXT,
        decided_at TEXT,
        decided_by TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER,
        error_type TEXT,
        description TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customer_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rating INTEGER,
        comment TEXT,
        store TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        salt TEXT,
        password_hash TEXT,
        role TEXT,
        is_active INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        actor_username TEXT,
        action TEXT,
        submission_id INTEGER,
        product_id TEXT,
        details TEXT
    )
    """)

    conn.commit()

    ensure_columns(conn, "products", ["price REAL"])
    ensure_columns(conn, "pending_submissions", [
        "suggested_price REAL",
        "employee_id TEXT",
        "employee_note TEXT",
        "edited_id TEXT",
        "edited_name TEXT",
        "edited_articleType TEXT",
        "edited_material TEXT",
        "edited_internal_tag TEXT",
        "edited_price REAL",
        "edited_confidence REAL",
        "best_match_json TEXT",
        "top5_json TEXT",
        "supervisor_note TEXT",
        "decided_at TEXT",
        "decided_by TEXT",
    ])

    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        defaults = [
            ("employee", "employee", "employee"),
            ("supervisor", "supervisor", "supervisor"),
            ("admin", "admin", "admin"),
        ]
        for username, pw, role in defaults:
            salt = secrets.token_hex(8)
            pw_hash = hash_password(pw, salt)
            cur.execute("""
                INSERT INTO users (username, salt, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (username, salt, pw_hash, role, now_iso()))
        conn.commit()

    conn.close()

def seed_products_if_empty():
    if not os.path.exists(CATALOG_PATH):
        return

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM products")
    count = cur.fetchone()["c"]

    if count == 0:
        df_cat = pd.read_csv(CATALOG_PATH)
        cols = df_cat.columns.astype(str).tolist()

        def pick(*candidates):
            for c in candidates:
                if c in cols:
                    return c
            return None

        col_id = pick("id", "ID", "product_id")
        col_name = pick("productDisplayName", "name", "product_name")
        col_type = pick("articleType", "type")
        col_mat = pick("material")
        col_tag = pick("internal_tag", "tag")
        col_img = pick("image_path", "image", "img_path")
        col_phash = pick("phash")
        col_price = pick("price", "Price", "unit_price")

        rows = []
        for _, r in df_cat.iterrows():
            pid = str(r[col_id]) if col_id else None
            if not pid:
                continue

            price_val = None
            if col_price and str(r[col_price]).strip() not in ("", "nan", "None"):
                try:
                    price_val = float(r[col_price])
                except:
                    price_val = None

            rows.append((
                pid,
                str(r[col_name]) if col_name else None,
                str(r[col_type]) if col_type else None,
                str(r[col_mat]) if col_mat else None,
                str(r[col_tag]) if col_tag else None,
                price_val,
                str(r[col_img]) if col_img else None,
                str(r[col_phash]) if col_phash else None,
                now_iso()
            ))

        cur.executemany("""
            INSERT OR IGNORE INTO products
            (id, name, articleType, material, internal_tag, price, image_path, phash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()

    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()
    seed_products_if_empty()

class LoginPayload(BaseModel):
    username: str
    password: str

class SubmissionEditPayload(BaseModel):
    employee_id: Optional[str] = None
    employee_note: Optional[str] = None
    edited_id: Optional[str] = None
    edited_name: Optional[str] = None
    edited_articleType: Optional[str] = None
    edited_material: Optional[str] = None
    edited_internal_tag: Optional[str] = None
    edited_price: Optional[float] = None
    edited_confidence: Optional[float] = None

class DecisionPayload(BaseModel):
    submission_id: int
    decision: Literal["approve", "reject"]
    supervisor_note: Optional[str] = None
    final_id: Optional[str] = None
    final_name: Optional[str] = None
    final_articleType: Optional[str] = None
    final_material: Optional[str] = None
    final_internal_tag: Optional[str] = None
    final_price: Optional[float] = None

class FeedbackPayload(BaseModel):
    rating: int
    comment: Optional[str] = None
    store: Optional[str] = None

class UserCreatePayload(BaseModel):
    username: str
    password: str
    role: Literal["employee", "supervisor", "admin"] = "employee"

class UserUpdatePayload(BaseModel):
    role: Optional[Literal["employee", "supervisor", "admin"]] = None
    is_active: Optional[bool] = None

@app.get("/")
def root():
    return {"status": "ok", "message": "api running"}

@app.post("/auth/login")
def login(payload: LoginPayload):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (payload.username,))
    u = cur.fetchone()
    conn.close()

    if not u or u["is_active"] != 1:
        raise HTTPException(status_code=401, detail="invalid credentials")

    salt = u["salt"]
    if hash_password(payload.password, salt) != u["password_hash"]:
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = secrets.token_hex(16)
    TOKENS[token] = {"username": u["username"], "role": u["role"], "ts": now_iso()}
    return {"token": token, "role": u["role"], "username": u["username"]}

@app.post("/scan-image")
async def scan_image(file: UploadFile = File(...)):
    img_bytes = await file.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid image")

    scan_hash = imagehash.phash(img)
    dists = df_index["phash"].apply(lambda h: scan_hash - imagehash.hex_to_hash(h))

    tmp = df_index.copy()
    tmp["dist"] = dists
    top = tmp.nsmallest(5, "dist")
    best = top.iloc[0]

    dist = int(best["dist"])
    conf = max(0.0, 1.0 - (float(dist) / 64.0))

    best_match = {
        "id": to_int_or_str(best["id"]),
        "name": best.get("productDisplayName", None),
        "articleType": best.get("articleType", None),
        "material": best.get("material", None),
        "internal_tag": best.get("internal_tag", None),
        "distance": dist,
        "confidence": round(conf, 3),
    }

    top5 = top[["id", "productDisplayName", "material", "internal_tag", "dist"]].to_dict(orient="records")

    best_match_json = json.dumps(best_match)
    top5_json = json.dumps(top5)

    ext = safe_ext(file.filename)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    saved_name = f"scan_{ts}{ext}"
    saved_path = os.path.join(UPLOADS_DIR, saved_name)
    with open(saved_path, "wb") as f:
        f.write(img_bytes)

    image_url = f"/uploads/{saved_name}"

    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pending_submissions
        (submitted_at, source, image_filename,
         suggested_id, suggested_name, suggested_articleType, suggested_material, suggested_internal_tag,
         suggested_price, confidence, distance,
         best_match_json, top5_json,
         status)
        VALUES (?, 'fallback', ?,
                ?, ?, ?, ?, ?,
                NULL, ?, ?,
                ?, ?,
                'pending')
    """, (
        now_iso(),
        saved_name,
        str(best_match["id"]),
        best_match["name"],
        best_match["articleType"],
        best_match["material"],
        best_match["internal_tag"],
        float(best_match["confidence"]),
        int(best_match["distance"]),
        best_match_json,
        top5_json,
    ))
    submission_id = cur.lastrowid

    cur.execute("""
        INSERT INTO audit_log (ts, actor_username, action, submission_id, product_id, details)
        VALUES (?, NULL, 'SUBMIT', ?, NULL, ?)
    """, (now_iso(), submission_id, f"auto-created from scan-image, file={saved_name}"))

    if conf < 0.40:
        cur.execute("""
            INSERT INTO system_errors (submission_id, error_type, description, created_at)
            VALUES (?, 'low_confidence', ?, ?)
        """, (submission_id, f"confidence={round(conf,3)} dist={dist}", now_iso()))

    conn.commit()
    conn.close()

    return JSONResponse({
        "submission_id": submission_id,
        "image_url": image_url,
        "best_match": best_match,
        "top5": top5
    })

@app.get("/submission/{submission_id}")
def get_submission(submission_id: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_submissions WHERE submission_id = ?", (submission_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="submission not found")

    d = dict(row)
    if d.get("image_filename"):
        d["image_url"] = f"/uploads/{d['image_filename']}"

    try:
        d["top5"] = json.loads(d.get("top5_json") or "[]")
    except:
        d["top5"] = []
    try:
        d["best_match"] = json.loads(d.get("best_match_json") or "null")
    except:
        d["best_match"] = None

    return d

@app.patch("/submission/{submission_id}")
def edit_submission(submission_id: int, payload: SubmissionEditPayload):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_submissions WHERE submission_id = ?", (submission_id,))
    sub = cur.fetchone()
    if not sub:
        conn.close()
        raise HTTPException(status_code=404, detail="submission not found")

    if payload.edited_price is not None and payload.edited_price < 0:
        cur.execute("""
            INSERT INTO system_errors (submission_id, error_type, description, created_at)
            VALUES (?, 'weird_price', ?, ?)
        """, (submission_id, f"negative price: {payload.edited_price}", now_iso()))

    cur.execute("""
        UPDATE pending_submissions
        SET employee_id = COALESCE(?, employee_id),
            employee_note = COALESCE(?, employee_note),
            edited_id = COALESCE(?, edited_id),
            edited_name = COALESCE(?, edited_name),
            edited_articleType = COALESCE(?, edited_articleType),
            edited_material = COALESCE(?, edited_material),
            edited_internal_tag = COALESCE(?, edited_internal_tag),
            edited_price = COALESCE(?, edited_price),
            edited_confidence = COALESCE(?, edited_confidence)
        WHERE submission_id = ?
    """, (
        payload.employee_id,
        payload.employee_note,
        payload.edited_id,
        payload.edited_name,
        payload.edited_articleType,
        payload.edited_material,
        payload.edited_internal_tag,
        payload.edited_price,
        payload.edited_confidence,
        submission_id
    ))

    cur.execute("""
        INSERT INTO audit_log (ts, actor_username, action, submission_id, product_id, details)
        VALUES (?, ?, 'EDIT', ?, NULL, ?)
    """, (now_iso(), payload.employee_id, submission_id, "employee edited fields"))

    conn.commit()
    conn.close()
    return {"status": "ok", "submission_id": submission_id}

@app.get("/admin/submissions")
def admin_submissions(request: Request, status: str = "pending", limit: int = 100, token: Optional[str] = None):
    require_role(request, token, ["supervisor", "admin"])

    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending/approved/rejected")

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT submission_id, submitted_at, image_filename,
               suggested_name, suggested_internal_tag, suggested_price, confidence, distance,
               edited_name, edited_internal_tag, edited_price, edited_confidence,
               status, decided_at, decided_by
        FROM pending_submissions
        WHERE status = ?
        ORDER BY submitted_at DESC
        LIMIT ?
    """, (status, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    for r in rows:
        if r.get("image_filename"):
            r["image_url"] = f"/uploads/{r['image_filename']}"
    return {"items": rows}

@app.post("/supervisor/decision")
def supervisor_decision(request: Request, payload: DecisionPayload, token: Optional[str] = None):
    token = require_role(request, token, ["supervisor", "admin"])
    actor = TOKENS[token]["username"]

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_submissions WHERE submission_id = ?", (payload.submission_id,))
    sub = cur.fetchone()
    if not sub:
        conn.close()
        raise HTTPException(status_code=404, detail="submission not found")

    if payload.decision == "reject":
        cur.execute("""
            UPDATE pending_submissions
            SET status='rejected',
                supervisor_note=?,
                decided_at=?,
                decided_by=?
            WHERE submission_id=?
        """, (payload.supervisor_note, now_iso(), actor, payload.submission_id))

        cur.execute("""
            INSERT INTO audit_log (ts, actor_username, action, submission_id, product_id, details)
            VALUES (?, ?, 'REJECT', ?, NULL, ?)
        """, (now_iso(), actor, payload.submission_id, payload.supervisor_note or ""))

        conn.commit()
        conn.close()
        return {"status": "ok", "result": "rejected"}

    final_id = payload.final_id or sub["edited_id"] or sub["suggested_id"] or str(sub["submission_id"])
    final_name = payload.final_name or sub["edited_name"] or sub["suggested_name"]
    final_type = payload.final_articleType or sub["edited_articleType"] or sub["suggested_articleType"]
    final_mat = payload.final_material or sub["edited_material"] or sub["suggested_material"]
    final_tag = payload.final_internal_tag or sub["edited_internal_tag"] or sub["suggested_internal_tag"]
    final_price = payload.final_price
    if final_price is None:
        final_price = sub["edited_price"] if sub["edited_price"] is not None else sub["suggested_price"]

    img_path = f"/uploads/{sub['image_filename']}" if sub["image_filename"] else None

    if final_tag:
        cur.execute("SELECT COUNT(*) AS c FROM products WHERE internal_tag = ?", (final_tag,))
        if cur.fetchone()["c"] > 0:
            cur.execute("""
                INSERT INTO system_errors (submission_id, error_type, description, created_at)
                VALUES (?, 'duplicate_ref', ?, ?)
            """, (payload.submission_id, f"internal_tag already exists: {final_tag}", now_iso()))

    cur.execute("""
        INSERT OR REPLACE INTO products
        (id, name, articleType, material, internal_tag, price, image_path, phash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
    """, (
        str(final_id),
        final_name,
        final_type,
        final_mat,
        final_tag,
        final_price,
        img_path,
        now_iso()
    ))

    cur.execute("""
        UPDATE pending_submissions
        SET status='approved',
            supervisor_note=?,
            decided_at=?,
            decided_by=?
        WHERE submission_id=?
    """, (payload.supervisor_note, now_iso(), actor, payload.submission_id))

    cur.execute("""
        INSERT INTO audit_log (ts, actor_username, action, submission_id, product_id, details)
        VALUES (?, ?, 'APPROVE', ?, ?, ?)
    """, (now_iso(), actor, payload.submission_id, str(final_id), payload.supervisor_note or ""))

    conn.commit()
    conn.close()
    return {"status": "ok", "result": "approved", "product_id": str(final_id)}

@app.get("/products")
def list_products(limit: int = 100):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, articleType, material, internal_tag, price, image_path, created_at
        FROM products
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"products": rows}

@app.get("/admin/errors")
def admin_errors(request: Request, limit: int = 200, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.submission_id, e.error_type, e.description, e.created_at,
               s.image_filename, s.status
        FROM system_errors e
        LEFT JOIN pending_submissions s ON s.submission_id = e.submission_id
        ORDER BY e.created_at DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    for r in rows:
        if r.get("image_filename"):
            r["image_url"] = f"/uploads/{r['image_filename']}"
    return {"items": rows}

@app.get("/admin/audit")
def admin_audit(request: Request, limit: int = 300, token: Optional[str] = None):
    require_role(request, token, ["supervisor", "admin"])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"items": rows}

@app.post("/feedback")
def submit_feedback(payload: FeedbackPayload):
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="rating must be 1..5")

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO customer_feedback (rating, comment, store, created_at)
        VALUES (?, ?, ?, ?)
    """, (payload.rating, payload.comment, payload.store, now_iso()))
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "ok", "feedback_id": fid}

def simple_sentiment(text: str) -> str:
    t = (text or "").lower()
    if not t.strip():
        return "neutral"
    pos = ["good", "great", "love", "amazing", "nice", "perfect", "excellent", "helpful"]
    neg = ["bad", "hate", "terrible", "awful", "worst", "poor", "broken", "rude", "angry", "slow"]
    score = 0
    for w in pos:
        if w in t:
            score += 1
    for w in neg:
        if w in t:
            score -= 1
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"

@app.get("/admin/feedback/stats")
def feedback_stats(request: Request, days: int = 30, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    since = datetime.utcnow() - timedelta(days=days)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT rating, comment, created_at
        FROM customer_feedback
        WHERE created_at >= ?
    """, (since.isoformat(),))
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        return {"total": 0, "sentiment": {"positive": 0, "neutral": 0, "negative": 0}, "avg_rating": None, "top_keywords": []}

    sentiments = Counter()
    ratings = []
    words = Counter()

    for r in rows:
        ratings.append(int(r["rating"]))
        s = simple_sentiment(r["comment"] or "")
        sentiments[s] += 1

        txt = (r["comment"] or "").lower()
        for w in re.findall(r"[a-z]{3,}", txt):
            if w in {"the", "and", "but", "for", "with", "this", "that", "was", "were"}:
                continue
            words[w] += 1

    top_keywords = [{"word": k, "count": v} for k, v in words.most_common(12)]
    avg_rating = sum(ratings) / len(ratings)

    return {
        "total": total,
        "sentiment": {"positive": sentiments["positive"], "neutral": sentiments["neutral"], "negative": sentiments["negative"]},
        "avg_rating": round(avg_rating, 2),
        "top_keywords": top_keywords
    }

@app.get("/admin/users")
def list_users(request: Request, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, role, is_active, created_at FROM users ORDER BY id ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"items": rows}

@app.post("/admin/users")
def create_user(request: Request, payload: UserCreatePayload, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    salt = secrets.token_hex(8)
    pw_hash = hash_password(payload.password, salt)

    conn = db_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users (username, salt, password_hash, role, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (payload.username, salt, pw_hash, payload.role, now_iso()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="username already exists")

    uid = cur.lastrowid
    conn.close()
    return {"status": "ok", "user_id": uid}

@app.patch("/admin/users/{user_id}")
def update_user(request: Request, user_id: int, payload: UserUpdatePayload, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="user not found")

    if payload.role is not None:
        cur.execute("UPDATE users SET role = ? WHERE id = ?", (payload.role, user_id))
    if payload.is_active is not None:
        cur.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if payload.is_active else 0, user_id))

    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/dashboard/stats")
def dashboard_stats(request: Request, days: int = 30, token: Optional[str] = None):
    require_role(request, token, ["admin"])

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")

    since = datetime.utcnow() - timedelta(days=days)

    conn = db_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM pending_submissions")
    total_scans = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM pending_submissions WHERE status='pending'")
    pending = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM pending_submissions WHERE status='approved'")
    approved = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM pending_submissions WHERE status='rejected'")
    rejected = cur.fetchone()["c"]

    cur.execute("SELECT submitted_at FROM pending_submissions WHERE submitted_at >= ?", (since.isoformat(),))
    stamps = [r["submitted_at"] for r in cur.fetchall()]
    per_day = Counter()
    for s in stamps:
        try:
            d = datetime.fromisoformat(s).date()
            per_day[str(d)] += 1
        except:
            pass

    series = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i))
        series.append({"date": str(d), "count": per_day.get(str(d), 0)})

    cur.execute("""
        SELECT suggested_material, suggested_articleType, edited_material, edited_articleType
        FROM pending_submissions
        WHERE submitted_at >= ?
    """, (since.isoformat(),))
    mats = Counter()
    types = Counter()
    for r in cur.fetchall():
        m = r["edited_material"] or r["suggested_material"] or "unknown"
        t = r["edited_articleType"] or r["suggested_articleType"] or "unknown"
        mats[str(m)] += 1
        types[str(t)] += 1

    top_materials = [{"label": k, "count": v} for k, v in mats.most_common(10)]
    top_article_types = [{"label": k, "count": v} for k, v in types.most_common(10)]

    cur.execute("""
        SELECT error_type, COUNT(*) AS c
        FROM system_errors
        WHERE created_at >= ?
        GROUP BY error_type
        ORDER BY c DESC
    """, (since.isoformat(),))
    error_by_type = [{"type": r["error_type"], "count": r["c"]} for r in cur.fetchall()]

    cur.execute("""
        SELECT rating, comment
        FROM customer_feedback
        WHERE created_at >= ?
    """, (since.isoformat(),))
    feedback_rows = cur.fetchall()
    ratings = [int(r["rating"]) for r in feedback_rows] if feedback_rows else []
    sentiments = Counter()
    for r in feedback_rows:
        sentiments[simple_sentiment(r["comment"] or "")] += 1

    conn.close()

    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    return {
        "summary": {
            "total_scans": total_scans,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "avg_rating": avg_rating,
        },
        "series_scans_per_day": series,
        "top_materials": top_materials,
        "top_article_types": top_article_types,
        "error_by_type": error_by_type,
        "feedback_sentiment": {
            "positive": sentiments["positive"],
            "neutral": sentiments["neutral"],
            "negative": sentiments["negative"],
        }
    }
