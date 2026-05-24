-- Migration 001: Cyber Cafe Gaming Stations
-- Run via: supabase db push --linked

-- ═══════════════════════════════════════════════════════════
-- PLAYSTATIONS (gaming consoles/PCs)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS playstations (
  id BIGSERIAL PRIMARY KEY,
  playstation_number INTEGER NOT NULL UNIQUE,
  name TEXT NOT NULL,
  hourly_rate DECIMAL(10,2) NOT NULL DEFAULT 50,
  status TEXT DEFAULT 'available' CHECK (status IN ('available', 'occupied', 'maintenance')),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed 4 playstations
INSERT INTO playstations (playstation_number, name, hourly_rate, status) VALUES
  (1, 'PS1 - Corner', 50, 'available'),
  (2, 'PS2 - Center', 50, 'available'),
  (3, 'PS3 - Window', 50, 'available'),
  (4, 'PS4 - Booth', 50, 'available')
ON CONFLICT (playstation_number) DO NOTHING;

-- ═══════════════════════════════════════════════════════════
-- SESSIONS (gaming time slots)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sessions (
  id BIGSERIAL PRIMARY KEY,
  playstation_id BIGINT REFERENCES playstations(id) ON DELETE CASCADE,
  player_name TEXT DEFAULT 'Guest',
  player_phone TEXT DEFAULT '',
  num_players INTEGER DEFAULT 1,
  start_time TIMESTAMPTZ NOT NULL,
  end_time TIMESTAMPTZ,
  hours_booked DECIMAL(5,2) DEFAULT 0,
  rate_per_hour DECIMAL(10,2) NOT NULL,
  total_amount DECIMAL(10,2) DEFAULT 0,
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'cancelled')),
  end_reason TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════
-- RLS POLICIES
-- ═══════════════════════════════════════════════════════════
ALTER TABLE playstations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

-- Public can read playstations (for customer view)
CREATE POLICY "Public can read playstations" ON playstations FOR SELECT USING (true);

-- Public can insert sessions (customer starts session)
CREATE POLICY "Public can insert sessions" ON sessions FOR INSERT WITH CHECK (true);

-- Anon can update sessions (for owner to end session)
CREATE POLICY "Anon can update sessions" ON sessions FOR UPDATE USING (true) WITH CHECK (true);

-- Public can read sessions (for checking status)
CREATE POLICY "Public can read sessions" ON sessions FOR SELECT USING (true);

-- ═══════════════════════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_playstations_status ON playstations(status);
CREATE INDEX IF NOT EXISTS idx_sessions_playstation ON sessions(playstation_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time DESC);
