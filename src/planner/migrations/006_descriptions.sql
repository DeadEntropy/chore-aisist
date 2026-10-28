-- Chore details shown in the body of its calendar events (which trees, which mulch, a store link, the paint...).
ALTER TABLE chore ADD COLUMN IF NOT EXISTS description TEXT NULL AFTER name;
-- The forecast over a planned outdoor chore's time, from the last run that had one (shown in its event).
ALTER TABLE planned ADD COLUMN IF NOT EXISTS weather VARCHAR(255) NULL AFTER status;
