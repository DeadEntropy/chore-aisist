-- A chore can be held back until a date (e.g. car service in early December): not planned before it.
ALTER TABLE chore ADD COLUMN IF NOT EXISTS not_before DATE NULL AFTER outdoor;
