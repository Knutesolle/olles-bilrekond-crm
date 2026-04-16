/**
 * /api/bookings
 * GET    /              – lista (filter: location_id, date, status)
 * POST   /              – skapa bokning
 * PUT    /:id           – uppdatera bokning
 * DELETE /:id           – ta bort bokning
 * POST   /:id/checkin   – manuell incheckning
 * POST   /:id/complete  – attestera klar
 */

const express = require('express');
const router  = express.Router();
const { getDb } = require('../db');

function toFrontend(row) {
  if (!row) return null;
  return {
    id:           row.id,
    locationId:   row.location_id,
    regNr:        row.reg_nr,
    name:         row.customer_name || '',
    phone:        row.phone         || '',
    service:      row.service       || '',
    price:        row.price         || 0,
    date:         row.booking_date,
    time:         row.booking_time  || '',
    status:       row.status,
    arrivedAt:    row.arrived_at,
    completedAt:  row.completed_at,
    notes:        row.notes         || '',
    unbooked:     !!row.unbooked,
    createdAt:    row.created_at,
  };
}

// GET /api/bookings
router.get('/', (req, res) => {
  const { location_id, date, status } = req.query;
  let sql    = 'SELECT * FROM bookings WHERE 1=1';
  const args = [];

  if (location_id) { sql += ' AND location_id = ?'; args.push(location_id); }
  if (date)        { sql += ' AND booking_date = ?'; args.push(date); }
  if (status)      { sql += ' AND status = ?';       args.push(status); }

  sql += ' ORDER BY booking_date, booking_time';
  const rows = getDb().prepare(sql).all(...args);
  res.json(rows.map(toFrontend));
});

// POST /api/bookings
router.post('/', (req, res) => {
  const { locationId, regNr, name, phone, service, price, date, time, notes } = req.body;
  if (!locationId || !regNr || !date) {
    return res.status(400).json({ error: 'locationId, regNr och date krävs' });
  }
  const info = getDb().prepare(`
    INSERT INTO bookings (location_id, reg_nr, customer_name, phone, service, price, booking_date, booking_time, notes, status, unbooked)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'bokad', 0)
  `).run(locationId, regNr.toUpperCase(), name||'', phone||'', service||'', price||0, date, time||'09:00', notes||'');

  const row = getDb().prepare('SELECT * FROM bookings WHERE id = ?').get(info.lastInsertRowid);
  const result = toFrontend(row);

  // Broadcast till alla klienter
  req.app.get('broadcast')({ type: 'booking_created', booking: result });
  res.status(201).json(result);
});

// PUT /api/bookings/:id
router.put('/:id', (req, res) => {
  const { regNr, name, phone, service, price, date, time, notes, status, arrivedAt, completedAt } = req.body;
  const db  = getDb();
  const old = db.prepare('SELECT * FROM bookings WHERE id = ?').get(req.params.id);
  if (!old) return res.status(404).json({ error: 'Bokning saknas' });

  db.prepare(`
    UPDATE bookings SET
      reg_nr        = COALESCE(?, reg_nr),
      customer_name = COALESCE(?, customer_name),
      phone         = COALESCE(?, phone),
      service       = COALESCE(?, service),
      price         = COALESCE(?, price),
      booking_date  = COALESCE(?, booking_date),
      booking_time  = COALESCE(?, booking_time),
      notes         = COALESCE(?, notes),
      status        = COALESCE(?, status),
      arrived_at    = COALESCE(?, arrived_at),
      completed_at  = COALESCE(?, completed_at)
    WHERE id = ?
  `).run(
    regNr   ? regNr.toUpperCase() : null,
    name    ?? null, phone  ?? null, service ?? null,
    price   ?? null, date   ?? null, time    ?? null,
    notes   ?? null, status ?? null,
    arrivedAt   ?? null,
    completedAt ?? null,
    req.params.id
  );

  const row = db.prepare('SELECT * FROM bookings WHERE id = ?').get(req.params.id);
  const result = toFrontend(row);
  req.app.get('broadcast')({ type: 'booking_updated', booking: result });
  res.json(result);
});

// DELETE /api/bookings/:id
router.delete('/:id', (req, res) => {
  getDb().prepare('DELETE FROM bookings WHERE id = ?').run(req.params.id);
  req.app.get('broadcast')({ type: 'booking_deleted', id: parseInt(req.params.id) });
  res.json({ ok: true });
});

// POST /api/bookings/:id/checkin
router.post('/:id/checkin', (req, res) => {
  const now = new Date().toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' });
  getDb().prepare(`UPDATE bookings SET status='inkort', arrived_at=? WHERE id=?`).run(now, req.params.id);
  const row    = getDb().prepare('SELECT * FROM bookings WHERE id = ?').get(req.params.id);
  const result = toFrontend(row);
  req.app.get('broadcast')({ type: 'booking_updated', booking: result });
  res.json(result);
});

// POST /api/bookings/:id/complete
router.post('/:id/complete', (req, res) => {
  const { note } = req.body;
  const now = new Date().toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' });
  const db  = getDb();
  if (note) {
    const old = db.prepare('SELECT notes FROM bookings WHERE id=?').get(req.params.id);
    const newNotes = old.notes ? old.notes + ' | ' + note : note;
    db.prepare(`UPDATE bookings SET status='klar', completed_at=?, notes=? WHERE id=?`).run(now, newNotes, req.params.id);
  } else {
    db.prepare(`UPDATE bookings SET status='klar', completed_at=? WHERE id=?`).run(now, req.params.id);
  }
  const row    = db.prepare('SELECT * FROM bookings WHERE id = ?').get(req.params.id);
  const result = toFrontend(row);
  req.app.get('broadcast')({ type: 'booking_updated', booking: result });
  res.json(result);
});

module.exports = router;
