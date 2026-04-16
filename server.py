#!/usr/bin/env python3
"""
Olles Bilrekond – CRM Backend (Python stdlib, inga beroenden)
=============================================================
Kör: python3 server.py
Öppna: http://localhost:3000

Använder enbart Python stdlib:
  - http.server   → HTTP-server
  - sqlite3       → databas
  - threading     → parallella SSE-klienter
  - json          → API responses
  - Server-Sent Events (SSE) → realtidsuppdateringar till frontend
"""

import http.server
import socketserver
import sqlite3
import json
import threading
import os
import time
import re
import urllib.parse
from datetime import datetime, date

# ─── Config ─────────────────────────────────────────────────────────────────
PORT    = 3000
DB_PATH = os.path.join(os.path.dirname(__file__), 'crm.db')
PUBLIC  = os.path.join(os.path.dirname(__file__), 'public')

class SSEBroker:
    def __init__(self):
        self._clients = []
        self._lock    = threading.Lock()

    def add_client(self, queue):
        with self._lock:
            self._clients.append(queue)

    def remove_client(self, queue):
        with self._lock:
            self._clients = [c for c in self._clients if c is not queue]

    def broadcast(self, data: dict):
        msg = f"data: {json.dumps(data)}\n\n".encode()
        dead = []
        with self._lock:
            for q in self._clients:
                try:
                    q.append(msg)
                except Exception:
                    dead.append(q)
        for q in dead:
            self.remove_client(q)

broker = SSEBroker()

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS locations (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            city       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id   TEXT    NOT NULL,
            reg_nr        TEXT    NOT NULL,
            customer_name TEXT    DEFAULT '',
            phone         TEXT    DEFAULT '',
            service       TEXT    DEFAULT '',
            price         INTEGER DEFAULT 0,
            booking_date  TEXT    NOT NULL,
            booking_time  TEXT    DEFAULT '09:00',
            status        TEXT    DEFAULT 'bokad',
            arrived_at    TEXT,
            completed_at  TEXT,
            notes         TEXT    DEFAULT '',
            unbooked      INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS anpr_events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id        TEXT,
            reg_nr             TEXT    NOT NULL,
            direction          TEXT,
            confidence         REAL,
            camera_id          TEXT,
            matched_booking_id INTEGER,
            event_time         TEXT    DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    seed_if_empty(db)
    db.close()

def seed_if_empty(db):
    count = db.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    if count > 0:
        return
    today = date.today().isoformat()
    db.execute("INSERT INTO locations (id,name,city) VALUES (?,?,?)", ('falun','Falun','Falun'))
    db.execute("INSERT INTO locations (id,name,city) VALUES (?,?,?)", ('borlange','Borlänge','Borlänge'))
    rows = [
        ('falun','ABC123','Maria Lindqvist','070-111 22 33','Helrekond – 2 495 kr',2495,today,'08:30','klar','08:28','10:45','',0),
        ('falun','DEF456','Lars Ström','073-444 55 66','Polering – 2 995 kr',2995,today,'09:00','inkort','09:03',None,'Repa vänster dörr',0),
        ('falun','GHI789','Anna Persson','076-777 88 99','Standard – 1 295 kr',1295,today,'10:00','bokad',None,None,'',0),
        ('falun','JKL012','Björn Eriksson','070-000 12 34','Keramiskt lackskydd – 6 449 kr',6449,today,'11:00','bokad',None,None,'Ny bil',0),
        ('falun','MNO345','Sofia Karlsson','072-333 45 67','Invändig – 795 kr',795,today,'13:00','bokad',None,None,'',0),
        ('borlange','PQR678','Erik Johansson','070-678 90 12','Helrekond – 2 495 kr',2495,today,'08:00','klar','07:58','10:10','',0),
        ('borlange','STU901','Lena Magnusson','073-901 23 45','Begagnat rekond – 2 500 kr',2500,today,'09:30','inkort','09:35',None,'',0),
        ('borlange','VWX234','Jonas Bergström','076-234 56 78','Standard – 1 295 kr',1295,today,'11:00','bokad',None,None,'',0),
        ('borlange','YZA567','Camilla Nilsson','070-567 89 01','Polering – 2 995 kr',2995,today,'13:30','bokad',None,None,'',0),
    ]
    for r in rows:
        db.execute("""
            INSERT INTO bookings (location_id,reg_nr,customer_name,phone,service,price,
                booking_date,booking_time,status,arrived_at,completed_at,notes,unbooked)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", r)
    db.commit()

def row_to_booking(r):
    return {
        "id":          r["id"],
        "locationId":  r["location_id"],
        "regNr":       r["reg_nr"],
        "name":        r["customer_name"] or "",
        "phone":       r["phone"] or "",
        "service":     r["service"] or "",
        "price":       r["price"] or 0,
        "date":        r["booking_date"],
        "time":        r["booking_time"] or "",
        "status":      r["status"],
        "arrivedAt":   r["arrived_at"],
        "completedAt": r["completed_at"],
        "notes":       r["notes"] or "",
        "unbooked":    bool(r["unbooked"]),
    }

def now_time():
    return datetime.now().strftime("%H:%M")

def today_str():
    return date.today().isoformat()

def process_plate(reg_nr, direction, location_id, camera_id=None, confidence=None):
    reg_nr = reg_nr.upper().replace(" ", "")
    today  = today_str()
    t      = now_time()
    db     = get_db()
    booking = db.execute("""
        SELECT * FROM bookings
        WHERE location_id=? AND reg_nr=? AND booking_date=?
        ORDER BY booking_time LIMIT 1
    """, (location_id, reg_nr, today)).fetchone()
    matched_id = None
    action     = "no_match"
    if direction == "in":
        if booking and booking["status"] == "bokad":
            db.execute("UPDATE bookings SET status='inkort', arrived_at=? WHERE id=?", (t, booking["id"]))
            db.commit()
            matched_id = booking["id"]
            action     = "checked_in"
        elif not booking:
            existing = db.execute("""
                SELECT id FROM bookings
                WHERE location_id=? AND reg_nr=? AND booking_date=? AND unbooked=1
            """, (location_id, reg_nr, today)).fetchone()
            if not existing:
                cur = db.execute("""
                    INSERT INTO bookings (location_id,reg_nr,booking_date,booking_time,
                        status,arrived_at,notes,unbooked)
                    VALUES (?,?,?,?,'ejbokad',?,'Detekterad av ANPR – ej bokad',1)
                """, (location_id, reg_nr, today, t, t))
                db.commit()
                matched_id = cur.lastrowid
                action     = "unbooked_created"
        else:
            action = "already_in"
    elif direction == "out":
        if booking and booking["status"] == "inkort":
            action     = "ready_for_attestation"
            matched_id = booking["id"]
    db.execute("""
        INSERT INTO anpr_events (location_id,reg_nr,direction,confidence,camera_id,matched_booking_id)
        VALUES (?,?,?,?,?,?)
    """, (location_id, reg_nr, direction, confidence, camera_id, matched_id))
    db.commit()
    updated_booking = None
    if matched_id:
        row = db.execute("SELECT * FROM bookings WHERE id=?", (matched_id,)).fetchone()
        if row:
            updated_booking = row_to_booking(row)
    db.close()
    event = {
        "type":       "anpr_event",
        "regNr":      reg_nr,
        "direction":  direction,
        "time":       t,
        "locationId": location_id,
        "cameraId":   camera_id,
        "confidence": confidence,
        "action":     action,
        "matched":    booking is not None,
        "booking":    updated_booking,
    }
    broker.broadcast(event)
    return event

class CRMHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, status=400):
        self.send_json({"error": msg}, status)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        qs     = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "" or path == "/":
            self._serve_file(os.path.join(PUBLIC, "index.html"))
            return
        if not path.startswith("/api"):
            fpath = os.path.join(PUBLIC, path.lstrip("/"))
            if os.path.isfile(fpath):
                self._serve_file(fpath)
            else:
                self._serve_file(os.path.join(PUBLIC, "index.html"))
            return
        if path == "/api/health":
            self.send_json({"status": "ok", "time": datetime.now().isoformat()})
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/locations":
            db   = get_db()
            rows = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
            db.close()
            self.send_json([dict(r) for r in rows])
        elif path == "/api/bookings":
            db   = get_db()
            sql  = "SELECT * FROM bookings WHERE 1=1"
            args = []
            if qs.get("location_id"): sql += " AND location_id=?"; args.append(qs["location_id"])
            if qs.get("date"):        sql += " AND booking_date=?"; args.append(qs["date"])
            if qs.get("status"):      sql += " AND status=?";       args.append(qs["status"])
            sql += " ORDER BY booking_date, booking_time"
            rows = db.execute(sql, args).fetchall()
            db.close()
            self.send_json([row_to_booking(r) for r in rows])
        elif path == "/api/anpr/events":
            db   = get_db()
            sql  = "SELECT * FROM anpr_events WHERE 1=1"
            args = []
            if qs.get("location_id"): sql += " AND location_id=?"; args.append(qs["location_id"])
            sql += " ORDER BY event_time DESC LIMIT 100"
            rows = db.execute(sql, args).fetchall()
            db.close()
            self.send_json([dict(r) for r in rows])
        elif path == "/api/fortnox/preview":
            loc  = qs.get("location_id")
            d    = qs.get("date", today_str())
            if not loc:
                self.send_error_json("location_id krävs")
                return
            db   = get_db()
            rows = db.execute("""
                SELECT * FROM bookings
                WHERE location_id=? AND booking_date=? AND status='klar'
                ORDER BY completed_at
            """, (loc, d)).fetchall()
            db.close()
            total = sum(r["price"] or 0 for r in rows)
            self.send_json({"rows": [dict(r) for r in rows], "total": total, "count": len(rows)})
        elif path == "/api/fortnox/export":
            loc = qs.get("location_id")
            d   = qs.get("date", today_str())
            if not loc:
                self.send_error_json("location_id krävs")
                return
            db   = get_db()
            rows = db.execute("""
                SELECT b.*, l.name as location_name FROM bookings b
                JOIN locations l ON b.location_id=l.id
                WHERE b.location_id=? AND b.booking_date=? AND b.status='klar'
                ORDER BY b.completed_at
            """, (loc, d)).fetchall()
            db.close()
            if not rows:
                self.send_error_json("Inga slutförda arbeten", 404)
                return
            loc_name = re.sub(r'[^a-zA-Z0-9]', '_', rows[0]["location_name"])
            filename = f"Olles_Bilrekond_{loc_name}_{d}_Fortnox.csv"
            header   = "RegNr;Kund;Telefon;Tjänst;Pris;Datum;Klar;Anläggning"
            lines    = [header] + [
                f"{r['reg_nr']};{r['customer_name'] or 'Okänd'};{r['phone'] or ''};{r['service']};{r['price']};{r['booking_date']};{r['completed_at'] or ''};{r['location_name']}"
                for r in rows
            ]
            csv_data = "\n".join(lines)
            body = ("\ufeff" + csv_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error_json("Okänd endpoint", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        body   = self.read_json()
        if path == "/api/locations":
            name = body.get("name","").strip()
            if not name:
                self.send_error_json("name krävs")
                return
            loc_id = re.sub(r'[åä]','a', name.lower())
            loc_id = re.sub(r'ö','o', loc_id)
            loc_id = re.sub(r'[^a-z0-9]','_', loc_id)
            loc_id += f"_{int(time.time())}"
            city = body.get("city", name)
            db = get_db()
            db.execute("INSERT INTO locations (id,name,city) VALUES (?,?,?)", (loc_id, name, city))
            db.commit()
            row = db.execute("SELECT * FROM locations WHERE id=?", (loc_id,)).fetchone()
            db.close()
            broker.broadcast({"type": "location_created", "location": dict(row)})
            self.send_json(dict(row), 201)
        elif path == "/api/bookings":
            loc  = body.get("locationId")
            reg  = (body.get("regNr") or "").upper()
            d    = body.get("date")
            if not (loc and reg and d):
                self.send_error_json("locationId, regNr och date krävs")
                return
            db = get_db()
            cur = db.execute("""
                INSERT INTO bookings (location_id,reg_nr,customer_name,phone,service,price,
                    booking_date,booking_time,notes,status,unbooked)
                VALUES (?,?,?,?,?,?,?,?,?,'bokad',0)
            """, (loc, reg, body.get("name",""), body.get("phone",""), body.get("service",""),
                  body.get("price",0), d, body.get("time","09:00"), body.get("notes","")))
            db.commit()
            row  = db.execute("SELECT * FROM bookings WHERE id=?", (cur.lastrowid,)).fetchone()
            db.close()
            result = row_to_booking(row)
            broker.broadcast({"type": "booking_created", "booking": result})
            self.send_json(result, 201)
        elif path == "/api/anpr/webhook":
            data      = body.get("data", body)
            camera_id = data.get("camera_id","unknown")
            results   = data.get("results",[])
            if not results:
                self.send_json({"ok": True, "skipped": "no_results"})
                return
            if camera_id.endswith("_out"):
                direction  = "out"
                location_id = camera_id[:-4]
            elif camera_id.endswith("_in"):
                direction  = "in"
                location_id = camera_id[:-3]
            else:
                direction   = "in"
                location_id = camera_id
            best = sorted(results, key=lambda x: x.get("score",0), reverse=True)[0]
            event = process_plate(best["plate"], direction, location_id, camera_id, best.get("score"))
            self.send_json({"ok": True, "event": event})
        elif path == "/api/anpr/simulate":
            reg = body.get("regNr","").upper()
            dir_ = body.get("direction","in")
            loc  = body.get("locationId")
            if not (reg and loc):
                self.send_error_json("regNr och locationId krävs")
                return
            event = process_plate(reg, dir_, loc, f"{loc}_{dir_}", None)
            self.send_json({"ok": True, "event": event})
        elif re.match(r'^/api/bookings/(\d+)/checkin$', path):
            bid = int(re.search(r'\d+', path).group())
            t   = now_time()
            db  = get_db()
            db.execute("UPDATE bookings SET status='inkort', arrived_at=? WHERE id=?", (t, bid))
            db.commit()
            row  = db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
            db.close()
            if not row:
                self.send_error_json("Bokning saknas", 404)
                return
            result = row_to_booking(row)
            broker.broadcast({"type": "booking_updated", "booking": result})
            self.send_json(result)
        elif re.match(r'^/api/bookings/(\d+)/complete$', path):
            bid  = int(re.search(r'\d+', path).group())
            note = body.get("note","")
            t    = now_time()
            db   = get_db()
            if note:
                old = db.execute("SELECT notes FROM bookings WHERE id=?", (bid,)).fetchone()
                new_notes = (old["notes"] or "") + (" | " if old["notes"] else "") + note
                db.execute("UPDATE bookings SET status='klar', completed_at=?, notes=? WHERE id=?", (t, new_notes, bid))
            else:
                db.execute("UPDATE bookings SET status='klar', completed_at=? WHERE id=?", (t, bid))
            db.commit()
            row  = db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
            db.close()
            result = row_to_booking(row)
            broker.broadcast({"type": "booking_updated", "booking": result})
            self.send_json(result)
        else:
            self.send_error_json("Okänd endpoint", 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        body   = self.read_json()
        m = re.match(r'^/api/bookings/(\d+)$', path)
        if m:
            bid = int(m.group(1))
            db  = get_db()
            old = db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
            if not old:
                db.close()
                self.send_error_json("Bokning saknas", 404)
                return
            fields = {
                "reg_nr":        (body.get("regNr") or "").upper() or None,
                "customer_name": body.get("name"),
                "phone":         body.get("phone"),
                "service":       body.get("service"),
                "price":         body.get("price"),
                "booking_date":  body.get("date"),
                "booking_time":  body.get("time"),
                "notes":         body.get("notes"),
                "status":        body.get("status"),
                "arrived_at":    body.get("arrivedAt"),
                "completed_at":  body.get("completedAt"),
            }
            updates = {k: v for k, v in fields.items() if v is not None}
            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates)
                vals = list(updates.values()) + [bid]
                db.execute(f"UPDATE bookings SET {set_clause} WHERE id=?", vals)
                db.commit()
            row    = db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
            db.close()
            result = row_to_booking(row)
            broker.broadcast({"type": "booking_updated", "booking": result})
            self.send_json(result)
        else:
            self.send_error_json("Okänd endpoint", 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        m = re.match(r'^/api/bookings/(\d+)$', path)
        if m:
            bid = int(m.group(1))
            db  = get_db()
            db.execute("DELETE FROM bookings WHERE id=?", (bid,))
            db.commit()
            db.close()
            broker.broadcast({"type": "booking_deleted", "id": bid})
            self.send_json({"ok": True})
        elif re.match(r'^/api/locations/(.+)$', path):
            loc_id = path.split("/")[-1]
            db = get_db()
            db.execute("DELETE FROM locations WHERE id=?", (loc_id,))
            db.commit()
            db.close()
            self.send_json({"ok": True})
        else:
            self.send_error_json("Okänd endpoint", 404)

    def _serve_file(self, fpath):
        if not os.path.isfile(fpath):
            self.send_response(404)
            self.end_headers()
            return
        ext  = os.path.splitext(fpath)[1]
        mime = {".html":"text/html",".js":"application/javascript",".css":"text/css",
                ".json":"application/json",".png":"image/png",".ico":"image/x-icon"}.get(ext,"text/plain")
        with open(fpath,"rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type",  "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection",    "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        queue = []
        broker.add_client(queue)
        try:
            self.wfile.write(b"data: {\"type\": \"connected\"}\n\n")
            self.wfile.flush()
            while True:
                if queue:
                    while queue:
                        self.wfile.write(queue.pop(0))
                    self.wfile.flush()
                else:
                    time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            broker.remove_client(queue)

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    init_db()
    server = ThreadedServer(("", PORT), CRMHandler)
    print(f"\n🚗  Olles Bilrekond CRM")
    print(f"    Öppna: http://localhost:{PORT}")
    print(f"    ANPR webhook: POST http://localhost:{PORT}/api/anpr/webhook")
    print(f"    Realtid (SSE): GET http://localhost:{PORT}/api/events\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Stängd.")
