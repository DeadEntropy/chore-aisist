# Calendar guide: how the planner reads your calendars

This is the user guide: what to put in your calendar so the planner books chores at the right times, and how to
connect another calendar (e.g. work). For the technical side (CalDAV, shares, credentials) see `CALENDAR.md`.

The planner only **reads** your calendars. It never changes your events. (From phase 3 it writes its own plan to
the separate **Chores** calendar, which you can see but not edit; see section 4.)

## 1. The basics

- **Chore time by default:** Saturday and Sunday, 08:00–18:00, for both of you.
- **Every timed event blocks its time, plus 30 minutes before and after**, for the people that calendar belongs to.
  A 14:00–16:00 party blocks 13:30–16:30.
- **All-day events are ignored**, unless their title contains one of the keywords below. Birthdays, "No school",
  holidays without a keyword: no effect.
- Any free time left in the chore windows can get chores. **There is no hours limit**: if you want time kept free,
  put it in your calendar.

Which calendar blocks whom:

| Calendar | Blocks |
|---|---|
| Nicolas | Nicolas |
| Anna | Anna |
| Family | both |
| School | both |

So: put an event on **your own** calendar if it's only about you, and on **Family** if it's about both of you.

## 2. Adding and removing chore time

Use these words in the **event title**. They're not case-sensitive and must start a word: "Trip to Paris" and
"Business TRIP" match `trip`; "Giveaway" does not match `away`.

| You want to… | Put this in the calendar | Example |
|---|---|---|
| **Add a whole day** of chore time (long weekend, day off) | **All-day** event with `Day off` or `Holiday` in the title | All-day "Day off" on Monday 3 Nov → Monday 08:00–18:00 is chore time |
| **Add a few hours** on any day | **Timed** event with `Chore` / `Chores` in the title | "Chores" Thursday 14:00–18:00 on your calendar → you're available Thursday afternoon |
| **Remove a whole day** (no chores, but you're home) | **All-day** event with `No chores` in the title | All-day "No chores" on Sunday |
| **Mark yourself away** (travel) | **All-day** event with `Travel`, `Trip` or `Away` in the title, covering every day you're gone | All-day "Trip to NYC" Fri–Mon |
| **Block a few hours** | Any **timed** event, whatever the title | "Lunch with Joe" Sat 12:00–14:00 |
| **Keep an event without it blocking you** | Set the event to **Free** ("Show as: free" / "Free/Busy: free") in your calendar app | A reminder you don't need to be home for |

What each one does in more detail:
- **"Day off" / "Holiday" (all-day):** the whole day becomes chore time, 08:00–18:00, for that calendar's people.
  Your other events that day still block their time.
- **"Chores" (timed):** exactly that time becomes chore time for that calendar's people. If another event overlaps
  it, the overlap is still blocked.
- **"No chores" (all-day):** no chores that day, even on a Saturday or a day off.
- **Travel (all-day):** you're away every day the event covers, so no chores for you.
- **Look-ahead:** if **neither** of you has chore time on the next chore days (both away, a "No chores" weekend, or a
  weekend fully booked), the planner brings forward the chores that would become overdue by then and does them first.
- **Cancelled events** never block.

**Which rule wins** when several apply to the same day:
1. "No chores" or travel → no chore time that day, whatever else is there.
2. Otherwise, the default windows plus any "Day off" / "Chores" time…
3. …minus every busy timed event (with its 30-minute buffer).

### Things to watch out for

- **"Holiday in Spain"** contains `holiday`, so it counts as a *day off*, not as being away. For travel, use a travel
  word: "**Trip** to Spain", "**Away** – Spain".
- **"No chores"** wins over "Chores": a timed "No chores" event just blocks its time.
- **A trip on the Family calendar marks both of you away**, and a kid's "School trip" on the School calendar does too.
  Put kids' trips as *timed* events, or leave out the travel word, if you're both home.
- **A multi-day timed event** (e.g. camping Friday 17:00 → Sunday 19:00) blocks everything in between, just like a
  trip.

### Checking what the planner sees

```bash
cd <deploy-dir>/chore-aisist
docker compose exec chore-aisist planner plan --dry-run
```

Each 7-day period starts with the events that affect chore time:
```
=== Tue 27 Oct – Mon 02 Nov 2026
  Busy: Sat 31 Oct 09:15-10:30  Swimming  (Nicolas & Anna)
  Day off: Mon 02 Nov  Day off  (Nicolas)
  Chore time: Thu 29 Oct 14:00-18:00  Chores  (Nicolas)
  Away: Fri 06 Nov–Sun 08 Nov  Trip to NYC  (Nicolas & Anna)
```
If an event you expected isn't listed, or is listed under the wrong label, check its title against the keywords
above. Busy events are only listed on days that have chore time.

## 3. Connecting another calendar (e.g. your work calendar)

Do this when you want the planner to avoid times on a calendar it doesn't read yet. Note that with the default
windows (weekends only), a work calendar only matters on weekends, or on weekdays where you've added chore time
("Chores" / "Day off").

### Step 1: let the app read the calendar

The app reads calendars as its own Nextcloud user, **`ChoreAisist`**. Which step you need depends on where the
calendar lives.

**A. The calendar is in Nextcloud** (you created it there, or it syncs there from your phone):
1. Open Nextcloud in a browser → **Calendar**.
2. Next to the calendar, open the **⋯** menu → **Share**.
3. Type `ChoreAisist` and pick the user.
4. **Untick "Can edit"**: the planner only needs to read it.

**B. The calendar is outside Nextcloud** (Outlook / Exchange / Google work calendar):
1. In your work calendar, find its **ICS / iCal publish link** (in Outlook: Settings → Calendar → Shared calendars →
   Publish a calendar → "ICS" link; in Google: calendar settings → "Secret address in iCal format"). Treat the
   link like a password.
2. Log in to Nextcloud **as `ChoreAisist`** (its login password is in the deploy folder's `.env`).
3. **Calendar** → **+ New calendar** → **New subscription from link (read-only)** → paste the link.
4. Nextcloud refreshes subscriptions **once a week** by default, which is too slow for a work calendar. An admin can
   make it hourly:
   ```bash
   docker exec -u www-data nextcloud php occ config:app:set dav calendarSubscriptionRefreshRate --value "PT1H"
   ```

*Check it in step 2: if the subscription doesn't appear in `planner calendars`, tell Claude Code. Nextcloud's
handling of subscriptions over CalDAV is the one part of this guide not tested yet.*

### Step 2: find the calendar's name

```bash
docker compose exec chore-aisist planner calendars
```
```
URI (use this in BUSY_CALENDARS)  NAME                  BLOCKS
family_shared_by_<owner>          Family (<owner>)      nicolas, anna
work_shared_by_<owner>            Work (<owner>)        (not used)
...
```
The first column is the name the planner needs. `(not used)` means it isn't connected yet.

### Step 3: say whose time it blocks

In the deploy folder's `.env`, add the calendar to `BUSY_CALENDARS`, with `=` and the people it blocks:
```
BUSY_CALENDARS=…existing entries…;work_shared_by_<owner>=nicolas
```
- Separate calendars with `;`, and people with `,` (`=nicolas,anna` for both).
- People are `nicolas` and `anna`. A typo stops the planner with an error rather than silently ignoring it.

### Step 4: reload and check

```bash
docker compose up -d                                       # the running container only reads .env when it starts
docker compose exec chore-aisist planner calendars        # BLOCKS should now show "nicolas"
docker compose exec chore-aisist planner plan --dry-run   # work events show up as "Busy:" lines on chore days
```

**To disconnect a calendar,** remove its entry from `BUSY_CALENDARS` and run `docker compose up -d`. You can also
remove the share or subscription in Nextcloud.

## 4. The Chores calendar (what the planner writes)

`planner plan` saves the plan for the next 7 days and shows it in the **Chores** calendar, which you and Anna see
read-only. It shows only what's still to do. Each chore is a timed event, starting on a quarter hour, with a
reminder 30 minutes before (`REMINDER_MINUTES`):

| Title | Meaning |
|---|---|
| `🧹 Mow the lawn (Nicolas)` | planned |
| `❔ Mow the lawn (Nicolas)` | its time passed and nobody reported it |

Open an event to see its details:
```
Brown dyed mulch, 2 cu ft bags × 70 (Home Depot: https://www.homedepot.com/p/...)

Forecast: rain 10-15%, feels like up to 31°C, UV up to 6
ID: order-mulch
```
- the chore's **description**, if it has one: which trees, which mulch, a store link, the paint reference. Ask Claude
  Code to add or change it ("for the mulch, we want …"); the events update at once;
- for outdoor chores, the **forecast** over its time, refreshed by every re-plan;
- the chore's **ID**, to say exactly what you did ("done order-mulch").

A chore reported done or skipped **leaves the calendar** at once. The history stays in the planner's database:
`planner status`, or ask Claude Code ("when did we last mow?").

How re-planning works. The planner re-plans **from the current moment** to the end of a 7-day window: every evening
at 20:00 (`PLAN_TIMES`), and **right after every `done` / `skip`**.
- Chores that already started are fixed: nothing in the past is moved. A chore in progress keeps its time, so
  nothing new is planned on top of it, and a chore that needs it comes after it.
- Planning "from now" starts at the next quarter hour (a report at 10:17 plans from 10:30).
- Chores still planned (🧹) from now on are re-planned: kept if nothing changed, moved if your calendar changed,
  removed if they no longer fit, and new ones are added. Chores go in the **earliest** free slot, so tomorrow's plan
  doesn't change just because a new day appears at the end of the window.
- A planned chore whose time has passed without a report becomes ❔ and goes back into the pool, so it can be
  planned again (e.g. on the next day). If you did it, report it, even later: it leaves the calendar, and the
  re-plan that follows removes any copy that was planned again.
- Reported chores (done or skipped) are never changed in the database: they're the history.

Example: on Saturday evening nothing has been reported yet, so the 20:00 run marks Saturday's chores ❔ and plans some
of them again on Sunday. On Sunday morning you tell Claude Code "I did all of Saturday's chores": they leave the
calendar and Sunday goes back to its original plan.

**Reporting** is done through Claude Code (or the commands directly):
```bash
docker compose exec chore-aisist planner done weed-pool-deck              # today, by whoever it was planned for
docker compose exec chore-aisist planner done buy-caulk --by anna --date 2026-11-01
docker compose exec chore-aisist planner skip trim-hedge-mendel
docker compose exec chore-aisist planner status                           # what's planned, done, unknown, overdue
```
A skip doesn't reset the chore's clock: it stays due and comes back.

## 5. The weather (outdoor chores)

Outdoor chores (`outdoor=yes` in the chore list) are kept out of rain, heat and strong sun, using the hourly
forecast for the whole week planned (`WEATHER_DAYS`, 10 days). The forecast changes: every evening's re-plan uses the
latest one, so a chore kept off a day that turns dry can come back to it, and the other way round.

An outdoor chore doesn't go at a time when:
- the **chance of rain, averaged over the chore**, is over 20% (`MAX_RAIN_PERCENT`). A 3-hour chore with 40% in the
  first hour and none after averages 13%: it goes ahead. A wet Sunday at 75% all day: no outdoor chores that day;
- **any hour** of it feels hotter than 35 °C / 95 °F (`MAX_FEELS_LIKE_C`), or has a UV index over 7
  (`MAX_UV_INDEX`; 8 and up burns fast): typically the early afternoon in summer.

It then goes to the next good time (later that day, or another day); if there's none in the 7 days, it's left out this
time and comes back later. Indoor chores ignore the weather. `planner plan --dry-run` shows the forecast it used:
```
  Weather: Sun 01 Nov  rain 33-86%, feels like up to 32°C, UV up to 6
  Didn't fit: trim-bushes (weather whenever it would fit (Sat 31 Oct 11:00: rain 40% > 20%)); ...
```
If the weather service can't be reached, the planner tries twice more (after 30 s, then 60 s), then says so in its
log and plans as if the weather were fine. If it refuses the settings (e.g. a typo in `WEATHER_MODELS`), the planner
also plans without the weather, and `planner health` reports it (the container shows as unhealthy) until a run gets the
forecast again.

## 6. Settings

All in the deploy folder's `.env`. After a change, run `docker compose up -d` in the deploy folder: the planner
container keeps the settings it started with until it's recreated.

| Setting | Default | What it does |
|---|---|---|
| `CHORE_WINDOWS` | `sat 08:00-18:00; sun 08:00-18:00` | Default chore time, every week, for both of you. Add a regular slot with e.g. `; wed 18:00-20:00` |
| `EXTRA_DAY_WINDOW` | `08:00-18:00` | Chore time on a "Day off" / "Holiday" |
| `EVENT_BUFFER_MINUTES` | `30` | Margin blocked before and after every timed event |
| `BUSY_CALENDARS` | the 4 family calendars | Which calendars are read, and whom they block |
| `DAY_OFF_KEYWORDS` | `day off,holiday` | All-day titles that add a whole day |
| `CHORE_TIME_KEYWORDS` | `chore` | Timed titles that add that time (also matches "Chores") |
| `NO_CHORES_KEYWORDS` | `no chores` | All-day titles that remove a day |
| `TRAVEL_KEYWORDS` | `travel,trip,away` | All-day titles that mean away |
| `REMINDER_MINUTES` | `30` | Phone reminder before each planned chore (0 = none) |
| `WEATHER_LATITUDE`, `WEATHER_LONGITUDE` | unset | Where the chores happen; unset = no weather |
| `WEATHER_DAYS` | `10` | Forecast used for today and this many days after (0 = today only); lower it to ignore far-off forecasts |
| `WEATHER_MODELS` | `ecmwf_ifs025,best_match` | [Open-Meteo models](https://open-meteo.com/en/docs), preferred first; each value comes from the first that has it |
| `MAX_RAIN_PERCENT` | `20` | Highest average chance of rain over an outdoor chore |
| `MAX_FEELS_LIKE_C` | `35` | Hottest feels-like temperature (°C) in any hour of an outdoor chore |
| `MAX_UV_INDEX` | `7` | Highest UV index in any hour of an outdoor chore |
| `PLAN_TIMES` | `20:00` | When the planner re-plans every day on its own; add a morning run with e.g. `07:00,20:00` |
| `BACKUP_FOLDER` | `./backups` | Host folder for the database dumps made before every scheduled run; on another disk than `./db`, writable by the image's user (`APP_UID`) |
| `BACKUP_KEEP_DAYS` | `30` | Days of dumps to keep (the newest is never deleted) |
