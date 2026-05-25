-- Migration 001: Stations & Sessions — generic schema for any station-based business
-- Use cases: gaming lounges, co-working spaces, laundry mats, car washes, rental shops

CREATE TABLE IF NOT EXISTS stations (
  id BIGSERIAL PRIMARY KEY,
  station_number INTEGER NOT NULL UNIQUE,
  name TEXT NOT NULL,
  hourly_rate DECIMAL(10,2) NOT NULL DEFAULT 50,
  status TEXT DEFAULT 'available' CHECK (status IN ('available', 'occupied', 'maintenance')),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default stations (customize for your business)
INSERT INTO stations (station_number, name, hourly_rate, status) VALUES
  (1, 'Station 1', 50, 'available'),
  (2, 'Station 2', 50, 'available'),
  (3, 'Station 3', 50, 'available'),
  (4, 'Station 4', 50, 'available')
ON CONFLICT (station_number) DO NOTHING;

CREATE TABLE IF NOT EXISTS sessions (
  id BIGSERIAL PRIMARY KEY,
  station_id BIGINT REFERENCES stations(id) ON DELETE CASCADE,
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
  players JSONB DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS Policies
ALTER TABLE stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public can read stations" ON stations FOR SELECT USING (true);
CREATE POLICY "Public can insert sessions" ON sessions FOR INSERT WITH CHECK (true);
CREATE POLICY "Anon can update sessions" ON sessions FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "Public can read sessions" ON sessions FOR SELECT USING (true);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_stations_status ON stations(status);
CREATE INDEX IF NOT EXISTS idx_sessions_station ON sessions(station_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time DESC);
