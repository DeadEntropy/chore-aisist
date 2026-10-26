-- No hours budget any more: any free time in the chore windows can be planned.
ALTER TABLE person DROP COLUMN IF EXISTS weekend_hours;
