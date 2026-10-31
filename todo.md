# To do

## Unreported (❔) copies of a chore stay on the calendar after it's reported done

**Seen:** 28 Oct 2026. A chore planned at 08:00 wasn't reported, became ❔, and was planned again at 10:15; that one
became ❔ too. `planner done` then marked only one item done (`plans.find_record` picks the single open/unknown
item closest to the date). The other ❔ copy stays in the Chores calendar for the `HISTORY_DAYS` (14) window. The
same happened with ❔ copies from the day before (two closet-door chores, done the next day).

**Why it's a problem:** the calendar shows ❔ "not reported" for chores that are done. The only ways to clear them
now are a direct database write or a second `done`, which records a duplicate completion.

**Idea:** when a chore is reported done (or skipped) on a day, also resolve its other `unknown` items on or before
that day, and any `open` ones that day: e.g. a new status (`superseded`) that isn't shown in the calendar and isn't
history (no completion row). Touch points: `jobs.report` (the lock is held there), `plans` (a pure rule picking
the items to resolve, with tests), `SHOWN`/`STATUS_MARK`, and a migration for the `planned.status` CHECK
constraint if a new status is added. Decide: should `skip` resolve them too? Should a `done` dated in the past
clear ❔ items after that date (probably not: the chore may have come due again)?

**Then:** clear the copies already on the calendar from 27–28 Oct (they age out after 14 days anyway).

## Chores allowed only on certain days (e.g. bulk trash pickup) — on hold

**Goal:** the city's bulk-trash truck comes on the **2nd Tuesday of every month**. Garden cuttings (palm fronds,
branches) can only be put out on the **weekend right before** it. We want a chore "Take out bulk trash for
pickup" (3 h, both people, high priority) that is only ever planned on that weekend. The weekends are 4 or 5 weeks
apart (e.g. 7–8 Nov, 5–6 Dec, 9–10 Jan), so a plain interval drifts.

**Idea discussed:**
- A new chore field `days` holding a short rule, e.g. `days="weekend before 2nd tue"`; first grammar:
  `<sat|sun|weekend> before <1st|2nd|3rd|4th|last> <mon…sun>`, validated when saved. A pure module
  (`dayrules.py`) parses it and answers "is this date allowed?".
- Fitting skips days the rule doesn't allow (like `not_before`); the dry run says why it's not planned.
- Scoring stays as is: a recurring interval of 21–28–35 days (min 21 so it can't come back twice per pickup).
- Plumbing: migration for the column, model/db/seed/`chore show`/`chore list`, tests, docs.

**Open questions:** every month or only after cutting chores (could be linked to palm/bush trimming later)?
Not outdoor, so rain can't push it out of its only two days? Priority 5? On the same weekend, plan it after any
trimming chores?
