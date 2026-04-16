/**
 * /api/anpr
 * POST /webhook   – tar emot avläsningar från Plate Recognizer Stream
 * POST /simulate  – manuell simulering (test/demo)
 * GET  /events    – lista ANPR-händelser per anläggning/datum
 *
 * ─── Plate Recognizer webhook-format ──────────────────────────────────────
 * Plate Recognizer Stream skickar POST med JSON:
 * {
 *   "data": {
 *     "camera_id": "falun_in",     <-- använd för att avgöra anläggning + riktning
 *     "timestamp": "2024-01-01T09:00:00Z",
 *     "results": [{ "plate": "ABC123", "score": 0.95 }]
 *   }
 * }
 *
 * Namnge dina kameror som:  {location_id}_in  eller  {location_id}_out
 * Exempel: "falun_in", "falun_out", "borlange_in", "borlange_out"
 * ──────────────────────────────────────────────────────────────────────────
 */

const express = require('express');
const router  = express.Router();
const { getDb } = require('../db');

function nowTime() {
  return new Date().toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' });
}
function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Kärn-logik: behandla en ANPR-avläsning
 */
function processPlate(regNr, direction, locationId, cameraId, confidence, broadcast) {
  const db     = getDb();
  const today  = todayStr();
  const t      = nowTime();
  regNr        = regNr.toUpperCase().replace(/\s/g, '');

  // Finns bokning för regnumret idag på denna anläggning?
  const booking = db.prepare(`
    SELECT * FROM bookings
    WHERE location_id = ? AND reg_nr = ? AND booking_date = ?
    ORDER BY booking_time LIMIT 1
  `).get(locationId, regNr, today);

  let matchedId = null;
  let action    = 'no_match';

  if (direction === 'in') {
    if (booking && booking.status === 'bokad') {
      // Uppdatera till inkört
      db.prepare(`UPDATE bookings SET status='inkort', arrived_at=? WHERE id=?`).run(t, booking.id);
      matchedId = booking.id;
      action    = 'checked_in';
      console.log(`[ANPR] ${regNr} IN → inkört (bokning #${booking.id})`);
    } else if (!booking) {
      // Okänd bil – skapa ej-bokad post om den inte redan finns
      const existing = db.prepare(`
        SELECT id FROM bookings
        WHERE location_id = ? AND reg_nr = ? AND booking_date = ? AND unbooked = 1
      `).get(locationId, regNr, today);

      if (!existing) {
        const info = db.prepare(`
          INSERT INTO bookings (location_id, reg_nr, booking_date, booking_time, status, arrived_at, notes, unbooked)
          VALUES (?, ?, ?, ?, 'ejbokad', ?, 'Detekterad av ANPR – ej bokad', 1)
        `).run(locationId, regNr, today, t, t);
        matchedId = info.lastInsertRowid;
        action    = 'unbooked_created';
        console.log(`[ANPR] ${regNr} IN → ej bokad (ny post #${matchedId})`);
      } else {
        action = 'already_unbooked';
      }
    } else {
      action = 'already_in';
    }
  } else if (direction === 'out') {
    if (booking && booking.status === 'inkort') {
      action    = 'ready_for_attestation';
      matchedId = booking.id;
      console.log(`[ANPR] ${regNr} UT → väntar på attestering`);
    }
  }

  // Logga ANPR-händelsen
  db.prepare(`
    INSERT INTO anpr_events (location_id, reg_nr, direction, confidence, camera_id, matched_booking_id)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(locationId, regNr, direction, confidence || null, cameraId || null, matchedId);

  // Hämta uppdaterad bokning för broadcast
  let updatedBooking = null;
  if (matchedId) {
    const row = db.prepare('SELECT * FROM bookings WHERE id = ?').get(matchedId);
    if (row) {
      updatedBooking = {
        id:          row.id,
        locationId:  row.location_id,
        regNr:       row.reg_nr,
        name:        row.customer_name || '',
        phone:       row.phone || '',
        service:     row.service || '',
        price:       row.price || 0,
        date:        row.booking_date,
        time:        row.booking_time || '',
        status:      row.status,
        arrivedAt:   row.arrived_at,
        completedAt: row.completed_at,
        notes:       row.notes || '',
        unbooked:    !!row.unbooked,
      };
    }
  }

  const event = {
    type:       'anpr_event',
    regNr,
    direction,
    time:       t,
    locationId,
    cameraId:   cameraId || null,
    confidence: confidence || null,
    action,
    matched:    !!booking,
    booking:    updatedBooking,
  };

  broadcast(event);
  return event;
}

// ─── POST /api/anpr/webhook ────────────────────────────────────────────────
// Plate Recognizer Stream skickar hit
router.post('/webhook', (req, res) => {
  try {
    const body = req.body;

    // Stöd för Plate Recognizer Stream-format
    const data      = body.data || body;
    const cameraId  = data.camera_id || 'unknown';
    const results   = data.results || [];

    if (!results.length) {
      return res.json({ ok: true, skipped: 'no_results' });
    }

    // Tolka riktning och anläggning från camera_id
    // Format: "{location_id}_in" eller "{location_id}_out"
    let locationId = 'falun';
    let direction  = 'in';

    if (cameraId.endsWith('_out')) {
      direction  = 'out';
      locationId = cameraId.slice(0, -4);
    } else if (cameraId.endsWith('_in')) {
      direction  = 'in';
      locationId = cameraId.slice(0, -3);
    } else {
      locationId = cameraId;
    }

    // Välj bästa avläsning (högst score)
    const best    = results.sort((a, b) => (b.score || 0) - (a.score || 0))[0];
    const regNr   = best.plate;
    const confidence = best.score;

    const broadcast = req.app.get('broadcast');
    const event = processPlate(regNr, direction, locationId, cameraId, confidence, broadcast);

    res.json({ ok: true, event });
  } catch (err) {
    console.error('[ANPR webhook error]', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── POST /api/anpr/simulate ───────────────────────────────────────────────
// Manuell simulering från CRM-gränssnittet
router.post('/simulate', (req, res) => {
  const { regNr, direction, locationId, cameraId } = req.body;
  if (!regNr || !direction || !locationId) {
    return res.status(400).json({ error: 'regNr, direction och locationId krävs' });
  }
  const broadcast = req.app.get('broadcast');
  const event = processPlate(regNr, direction, locationId, cameraId || `${locationId}_${direction}`, null, broadcast);
  res.json({ ok: true, event });
});

// ─── GET /api/anpr/events ─────────────────────────────────────────────────
router.get('/events', (req, res) => {
  const { location_id, date } = req.query;
  let sql  = `SELECT * FROM anpr_events WHERE 1=1`;
  const args = [];
  if (location_id) { sql += ' AND location_id = ?'; args.push(location_id); }
  if (date)        { sql += ' AND date(event_time) = ?'; args.push(date); }
  sql += ' ORDER BY event_time DESC LIMIT 100';
  res.json(getDb().prepare(sql).all(...args));
});

module.exports = router;
