-- Initial schema: chore catalog, ordering, people, history, plans, run log.
-- Statements are separated by semicolons at the end of a line.

CREATE TABLE chore (
  id                   VARCHAR(64)  PRIMARY KEY,
  name                 VARCHAR(255) NOT NULL,
  category             VARCHAR(16)  NOT NULL CHECK (category IN ('home', 'garden')),
  type                 VARCHAR(16)  NOT NULL CHECK (type IN ('recurring', 'one_off')),
  interval_min_days    INT NULL,
  interval_target_days INT NULL,
  interval_max_days    INT NULL,
  duration_h           DECIMAL(4,2) NULL,
  priority             TINYINT NULL CHECK (priority BETWEEN 1 AND 5),
  assignee             VARCHAR(16) NULL CHECK (assignee IN ('nicolas', 'anna', 'either', 'both')),
  outdoor              BOOLEAN NOT NULL DEFAULT FALSE,
  active               BOOLEAN NOT NULL DEFAULT TRUE,
  created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CHECK (interval_min_days IS NULL OR interval_min_days <= interval_target_days),
  CHECK (interval_target_days IS NULL OR interval_target_days <= interval_max_days)
);

CREATE TABLE chore_need (
  chore_id  VARCHAR(64) NOT NULL,
  needs_id  VARCHAR(64) NOT NULL,
  PRIMARY KEY (chore_id, needs_id),
  FOREIGN KEY (chore_id) REFERENCES chore(id) ON DELETE CASCADE,
  FOREIGN KEY (needs_id) REFERENCES chore(id),
  CHECK (chore_id <> needs_id)
);

CREATE TABLE person (
  id             VARCHAR(16) PRIMARY KEY,
  name           VARCHAR(64) NOT NULL,
  weekend_hours  DECIMAL(4,2) NOT NULL
);

CREATE TABLE completion (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  chore_id      VARCHAR(64) NOT NULL,
  person_id     VARCHAR(16) NULL,
  completed_on  DATE NOT NULL,
  outcome       VARCHAR(16) NOT NULL CHECK (outcome IN ('done', 'skipped')),
  source        VARCHAR(16) NOT NULL CHECK (source IN ('claude', 'manual', 'import')),
  recorded_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (chore_id) REFERENCES chore(id),
  FOREIGN KEY (person_id) REFERENCES person(id),
  INDEX idx_completion_chore (chore_id, outcome, completed_on)
);

CREATE TABLE planned (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  chore_id    VARCHAR(64) NOT NULL,
  person_id   VARCHAR(16) NOT NULL,
  plan_date   DATE NOT NULL,
  caldav_uid  VARCHAR(255) NOT NULL UNIQUE,
  status      VARCHAR(16) NOT NULL CHECK (status IN ('open', 'done', 'skipped', 'expired')),
  FOREIGN KEY (chore_id) REFERENCES chore(id),
  FOREIGN KEY (person_id) REFERENCES person(id)
);

CREATE TABLE run_log (
  id       INT AUTO_INCREMENT PRIMARY KEY,
  run_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  kind     VARCHAR(16) NOT NULL CHECK (kind IN ('weekly', 'daily', 'manual')),
  summary  TEXT NULL
);
