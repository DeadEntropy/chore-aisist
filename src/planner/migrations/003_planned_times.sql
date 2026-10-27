-- Planned chores with their time slot and everyone doing them; 'unknown' replaces 'expired'.
-- The table has never been written to, so it is simply recreated.
DROP TABLE IF EXISTS planned;

CREATE TABLE IF NOT EXISTS planned (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  chore_id    VARCHAR(64) NOT NULL,
  people      VARCHAR(64) NOT NULL,                     -- 'nicolas' or 'nicolas,anna'
  start_at    DATETIME NOT NULL,                        -- local time
  end_at      DATETIME NOT NULL,
  caldav_uid  VARCHAR(255) NOT NULL UNIQUE,
  status      VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'skipped', 'unknown')),
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (chore_id) REFERENCES chore(id),
  INDEX idx_planned_start (start_at)
);
