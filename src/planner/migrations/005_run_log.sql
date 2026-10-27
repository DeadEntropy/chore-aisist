-- One row per real planning run (scheduled, manual `plan`, or the re-plan after a report): when, how it went.
-- The scheduler uses it to catch up on a run missed while the container was down, and to retry after a failure.
-- Replaces the placeholder run_log from 001, which was never written to.
DROP TABLE IF EXISTS run_log;

CREATE TABLE IF NOT EXISTS run_log (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  started_at   DATETIME NOT NULL,                       -- local time
  finished_at  DATETIME NOT NULL,
  trigger_kind VARCHAR(16) NOT NULL CHECK (trigger_kind IN ('schedule', 'manual', 'report')),
  outcome      VARCHAR(16) NOT NULL CHECK (outcome IN ('ok', 'error')),
  message      TEXT NULL,                               -- summary of the changes, or the error
  INDEX idx_run_log_started (started_at)
);
