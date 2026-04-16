/**
 * /api/fortnox
 * GET /export  – exportera dagskassa som CSV-fil för Fortnox-import
 *   ?location_id=falun&date=2024-01-15
 *
 * Kolumnformat: RegNr;Kund;Telefon;Tjänst;Pris;Datum;Klar
 *
 * Framtida utökning: Fortnox REST API (OAuth2)
 *   - POST /api/fortnox/push  – skicka direkt till Fortnox via API
 *   - Kräver: CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN i .env
 */

const express = require('express');
const router  = express.Router();
const { getDb } = require('../db');

// GET /api/fortnox/export
router.get('/export', (req, res) => {
  const { location_id, date } = req.query;
  if (!location_id || !date) {
    return res.status(400).json({ error: 'location_id och date krävs' });
  }

  const rows = getDb().prepare(`
    SELECT b.*, l.name as location_name
    FROM bookings b
    JOIN locations l ON b.location_id = l.id
    WHERE b.location_id = ? AND b.booking_date = ? AND b.status = 'klar'
    ORDER BY b.completed_at
  `).all(location_id, date);

  if (!rows.length) {
    return res.status(404).json({ error: 'Inga slutförda arbeten för detta datum och anläggning' });
  }

  // Bygg CSV med BOM för korrekt ÅÄÖ i Excel
  const header = 'RegNr;Kund;Telefon;Tjänst;Pris;Datum;Klar;Anläggning';
  const csvRows = rows.map(r => [
    r.reg_nr,
    r.customer_name || 'Okänd',
    r.phone || '',
    r.service || '',
    r.price || 0,
    r.booking_date,
    r.completed_at || '',
    r.location_name,
  ].join(';'));

  const csv      = [header, ...csvRows].join('\n');
  const locName  = rows[0].location_name.replace(/[^a-zA-Z0-9]/g, '_');
  const filename = `Olles_Bilrekond_${locName}_${date}_Fortnox.csv`;

  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
  res.send('\ufeff' + csv);  // BOM
});

// GET /api/fortnox/preview
// Returnerar JSON (för förhandsgranskning i CRM)
router.get('/preview', (req, res) => {
  const { location_id, date } = req.query;
  if (!location_id || !date) {
    return res.status(400).json({ error: 'location_id och date krävs' });
  }

  const rows = getDb().prepare(`
    SELECT reg_nr, customer_name, phone, service, price, booking_date, completed_at
    FROM bookings
    WHERE location_id = ? AND booking_date = ? AND status = 'klar'
    ORDER BY completed_at
  `).all(location_id, date);

  const total = rows.reduce((s, r) => s + (r.price || 0), 0);
  res.json({ rows, total, count: rows.length });
});

module.exports = router;
