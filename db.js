/**
 * Databas – SQLite via better-sqlite3
 * Filen crm.db skapas automatiskt vid första start.
 */

const Database = require('better-sqlite3');
const path     = require('path');

const DB_PATH = path.join(__dirname, 'crm.db');
let db;

function getDb() {
  if (!db) db = new Database(DB_PATH);
  return db;
}

function initDb() {
  const db = getDb();

  // Aktivera foreign keys
  db.pragma('foreign_keys = ON');
  db.pragma('journal_mode = WAL');

  // ── Anläggningar ────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS locations (
      id         TEXT PRIMARY KEY,
      name       TEXT NOT NULL,
      city       TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // ── Bokningar ───────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS bookings (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      location_id   TEXT    NOT NULL,
      reg_nr        TEXT    NOT NULL,
      customer_name TEXT,
      phone         TEXT,
      service       TEXT,
      price         INTEGER DEFAULT 0,
      booking_date  TEXT    NOT NULL,
      booking_time  TEXT,
      status        TEXT    DEFAULT 'bokad',
      arrived_at    TEXT,
      completed_at  TEXT,
      notes         TEXT,
      unbooked      INTEGER DEFAULT 0,
      created_at    TEXT    DEFAULT (datetime('now')),
      FOREIGN KEY (location_id) REFERENCES locations(id)
    )
  `);

  // ── ANPR-händelser ──────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS anpr_events (
      id                 INTEGER PRIMARY KEY AUTOINCREMENT,
      location_id        TEXT,
      reg_nr             TEXT    NOT NULL,
      direction          TEXT,
      confidence         REAL,
      camera_id          TEXT,
      matched_booking_id INTEGER,
      event_time         TEXT    DEFAULT (datetime('now'))
    )
  `);

  // ── Seed: grunddata ─────────────────────────────────────
  seedIfEmpty(db);

  console.log('[DB] Databas initierad:', DB_PATH);
  return db;
}

function seedIfEmpty(db) {
  const count = db.prepare('SELECT COUNT(*) as n FROM locations').get();
  if (count.n > 0) return;

  const today = new Date().toISOString().slice(0, 10);

  // Anläggningar
  db.prepare(`INSERT INTO locations (id, name, city) VALUES (?, ?, ?)`).run('falun', 'Falun', 'Falun');
  db.prepare(`INSERT INTO locations (id, name, city) VALUES (?, ?, ?)`).run('borlange', 'Borlänge', 'Borlänge');

  // Exempelbokningar Falun
  const insertBooking = db.prepare(`
    INSERT INTO bookings (location_id, reg_nr, customer_name, phone, service, price, booking_date, booking_time, status, arrived_at, completed_at, notes, unbooked)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  insertBooking.run('falun', 'ABC123', 'Maria Lindqvist',  '070-111 22 33', 'Helrekond – 2 495 kr',           2495, today, '08:30', 'klar',   '08:28', '10:45', '', 0);
  insertBooking.run('falun', 'DEF456', 'Lars Ström',       '073-444 55 66', 'Polering – 2 995 kr',            2995, today, '09:00', 'inkort', '09:03', null,    'Repa vänster dörr', 0);
  insertBooking.run('falun', 'GHI789', 'Anna Persson',     '076-777 88 99', 'Standard – 1 295 kr',            1295, today, '10:00', 'bokad',  null,    null,    '', 0);
  insertBooking.run('falun', 'JKL012', 'Björn Eriksson',   '070-000 12 34', 'Keramiskt lackskydd – 6 449 kr', 6449, today, '11:00', 'bokad',  null,    null,    'Ny bil', 0);
  insertBooking.run('falun', 'MNO345', 'Sofia Karlsson',   '072-333 45 67', 'Invändig – 795 kr',               795, today, '13:00', 'bokad',  null,    null,    '', 0);

  // Exempelbokningar Borlänge
  insertBooking.run('borlange', 'PQR678', 'Erik Johansson',  '070-678 90 12', 'Helrekond – 2 495 kr',       2495, today, '08:00', 'klar',   '07:58', '10:10', '', 0);
  insertBooking.run('borlange', 'STU901', 'Lena Magnusson',  '073-901 23 45', 'Begagnat rekond – 2 500 kr', 2500, today, '09:30', 'inkort', '09:35', null,    '', 0);
  insertBooking.run('borlange', 'VWX234', 'Jonas Bergström', '076-234 56 78', 'Standard – 1 295 kr',        1295, today, '11:00', 'bokad',  null,    null,    '', 0);
  insertBooking.run('borlange', 'YZA567', 'Camilla Nilsson', '070-567 89 01', 'Polering – 2 995 kr',        2995, today, '13:30', 'bokad',  null,    null,    '', 0);

  console.log('[DB] Exempeldata inlagd');
}

module.exports = { getDb, initDb };
