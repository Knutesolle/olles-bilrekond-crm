/**
 * /api/locations
 * GET    /          – lista alla anläggningar
 * POST   /          – skapa ny anläggning
 * DELETE /:id       – ta bort anläggning
 */

const express = require('express');
const router  = express.Router();
const { getDb } = require('../db');

// GET /api/locations
router.get('/', (req, res) => {
  const rows = getDb().prepare('SELECT * FROM locations ORDER BY name').all();
  res.json(rows);
});

// POST /api/locations
router.post('/', (req, res) => {
  const { name, city } = req.body;
  if (!name) return res.status(400).json({ error: 'name krävs' });

  const id = name.toLowerCase()
    .replace(/å/g,'a').replace(/ä/g,'a').replace(/ö/g,'o')
    .replace(/[^a-z0-9]/g,'_')
    + '_' + Date.now();

  getDb().prepare('INSERT INTO locations (id, name, city) VALUES (?, ?, ?)').run(id, name, city || name);
  const loc = getDb().prepare('SELECT * FROM locations WHERE id = ?').get(id);
  res.status(201).json(loc);
});

// DELETE /api/locations/:id
router.delete('/:id', (req, res) => {
  getDb().prepare('DELETE FROM locations WHERE id = ?').run(req.params.id);
  res.json({ ok: true });
});

module.exports = router;
