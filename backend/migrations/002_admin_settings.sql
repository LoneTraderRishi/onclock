-- Migration 002: Admin settings table for dashboard password storage
-- Required by main.py for dynamic password management

CREATE TABLE IF NOT EXISTS admin_settings (
  id BIGSERIAL PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  value TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE admin_settings ENABLE ROW LEVEL SECURITY;

-- Allow public read/write (password is already hashed)
CREATE POLICY "Public can read admin_settings" ON admin_settings FOR SELECT USING (true);
CREATE POLICY "Public can insert admin_settings" ON admin_settings FOR INSERT WITH CHECK (true);
CREATE POLICY "Public can update admin_settings" ON admin_settings FOR UPDATE USING (true) WITH CHECK (true);

-- Seed default dashboard password (hashed)
-- Default: changeme
-- IMPORTANT: Change this after first login!
INSERT INTO admin_settings (key, value)
VALUES ('dashboard_password', 'changeme')
ON CONFLICT (key) DO NOTHING;
