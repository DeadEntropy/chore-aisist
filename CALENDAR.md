# Calendar Access

How the planner (and anyone working on this repo) reads and writes the family calendar.

Placeholders in angle brackets (`<nextcloud-host>`, `<app-user>`, …) stand for site-specific values. The real values
live in the local `.env` (gitignored) and in private notes kept outside this repository.

## Where it lives

Nextcloud runs on the same home server as this repo, as its own Docker compose stack:

| Container | Role |
|---|---|
| Nextcloud app | App server, proxied by Nginx Proxy Manager (NPM) |
| Nextcloud database (`<nextcloud-db-container>`) | MariaDB — calendar data is stored here |
| Redis | Cache |
| Cron worker | Background jobs |

Web UI / CalDAV base URL: `https://<nextcloud-host>`.
Plain HTTP to the app container does **not** work (`overwriteprotocol = https`).

## Calendars

| Name | Owner | Components | Use for the planner |
|---|---|---|---|
| Nicolas | `<owner-user>` | VEVENT | Read — Nicolas's constraints (gym, work, travel) |
| Family | `<owner-user>` | VEVENT, VTODO | Read — kids' events, parties, family travel |
| School | `<owner-user>` | VEVENT, VTODO | Read — school events |
| Anna | `<spouse-user>` | VEVENT, VTODO, VJOURNAL | Read — Anna's constraints |
| Chores | **`<app-user>`** | VEVENT, VTODO, VJOURNAL | **Write** — planner output (chore events only) |

Family and School are shared read-write with Anna and the family group.

### The Chores calendar and the app user

- `<app-user>` is a dedicated Nextcloud user for this app — not admin, not in any group.
  It **owns** Chores (URI `chores`) and is the only account that writes to it.
- Chores holds **events only** (one VEVENT per planned chore, on the weekend). No tasks, no phone task apps.
- Nicolas and Anna get **read-only** access: they see the plan in their calendar apps but don't edit it.
- Completions are reported through Claude Code ("I've done the lawn") — the app records them and re-plans.
- The planner only writes to Chores and only reads Nicolas, Family, School and Anna, so chores can never count
  as busy time and a re-run can't touch real family events.

History: created 2026-10-25 with `occ dav:create-calendar <owner-user> chores`, renamed/coloured in the DB,
then moved with `occ dav:move-calendar chores <owner-user> <app-user>`.

Shares (inserted into `oc_dav_shares` on 2026-10-25, `access = 3` = read-only):

| Share | Seen by recipient as | Why |
|---|---|---|
| Chores → `<owner-user>` | `chores_shared_by_<app-user>` | See the plan |
| Chores → `<spouse-user>` | `chores_shared_by_<app-user>` | See the plan |
| Nicolas → `<app-user>` | `personal_shared_by_<owner-user>` | Planner reads busy time |
| Family → `<app-user>` | `family_shared_by_<owner-user>` | Planner reads busy time |
| School → `<app-user>` | `school_shared_by_<owner-user>` | Planner reads busy time |
| Anna → `<app-user>` | `personal_shared_by_<spouse-user>` | Planner reads busy time |

Timezone for all timed events: `America/New_York`.

## Access method 1 — direct database (current practice)

This is how calendars are edited today, via scripts on the home server and a `/calendar` Claude Code command
(the full reference for inserts, recurrences, EXDATEs and deletes).

Run queries inside the Nextcloud database container; credentials come from the container's own env:

```bash
docker exec <nextcloud-db-container> bash -c 'mariadb -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "<SQL>"'
```

Multi-line SQL via stdin:

```bash
docker exec -i <nextcloud-db-container> bash -c 'mariadb -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < /tmp/query.sql
```

### Tables

- `oc_calendars` — one row per calendar (`id`, `principaluri`, `displayname`, `uri`, `components`, `synctoken`).
- `oc_calendarobjects` — one row per event/task. Raw iCalendar text in `calendardata`; `componenttype` is
  `VEVENT` or `VTODO`; `firstoccurence` / `lastoccurence` are UTC Unix timestamps.

### Reading events for a date range

```sql
SELECT c.displayname, o.id, o.componenttype,
       FROM_UNIXTIME(o.firstoccurence) AS first_utc,
       FROM_UNIXTIME(o.lastoccurence)  AS last_utc,
       o.calendardata
FROM oc_calendarobjects o
JOIN oc_calendars c ON c.id = o.calendarid
WHERE o.calendarid IN (<calendar-ids>)
  AND o.deleted_at IS NULL
  AND o.firstoccurence < UNIX_TIMESTAMP('2026-11-02 00:00:00')
  AND o.lastoccurence  >= UNIX_TIMESTAMP('2026-10-31 00:00:00');
```

Recurring events have a single row covering the whole series — the `RRULE`/`EXDATE` lines in `calendardata`
must be expanded in code (e.g. Python `icalendar` + `recurring-ical-events`) to get actual occurrences.

### Rules when writing

- Stored ICS uses **CRLF** line endings.
- After every INSERT/UPDATE/DELETE: `UPDATE oc_calendars SET synctoken = synctoken + 1 WHERE id = <id>;`
  otherwise phones won't pick up the change.
- Escape `'` as `''` in SQL strings.
- Always SELECT back to verify.

Pros: no credentials beyond Docker access, fast, already proven. Cons: bypasses Nextcloud's
API (sync tokens and change log must be handled by hand), and ties the planner to the DB schema.

## Access method 2 — CalDAV (target for the planner)

The design calls for CalDAV so the planner uses Nextcloud's supported API and keeps sync history correct.

- Base: `https://<nextcloud-host>/remote.php/dav/calendars/<user>/<calendar-uri>/`
  - e.g. Family: `https://<nextcloud-host>/remote.php/dav/calendars/<owner-user>/family/`
  - Chores: `https://<nextcloud-host>/remote.php/dav/calendars/<app-user>/chores/`
  - Shared calendars appear under the app user with a suffix, e.g. `.../calendars/<app-user>/family_shared_by_<owner-user>/`
- Auth: `<app-user>` + **app password**, in `.env` (gitignored, mode 600):
  `NEXTCLOUD_USER`, `NEXTCLOUD_APP_PASSWORD`, `CALDAV_URL`, `CHORES_CALENDAR_URI`.
  The app itself should only use the app password, never the account's login password.
  Rotate with `occ user:auth-tokens:list|delete|add <app-user>`.
- DNS: the home server does not resolve `<nextcloud-host>` itself. Point it at the server's LAN IP (e.g. `curl --resolve
  <nextcloud-host>:443:<server-ip>`, or an `/etc/hosts` / container `extra_hosts` entry).
- TLS: NPM serves a certificate from the homelab CA — clients must trust that CA certificate,
  or the planner must be given that CA bundle.
- If the planner runs as a container, it can join Nextcloud's Docker network and reach the app container
  directly, but `overwriteprotocol=https` may cause redirects — going through NPM is simpler.
- Python: the `caldav` library handles discovery, `date_search`, VEVENT read and write.
- Smoke test (expects `207`):
  ```bash
  set -a; . ./.env; set +a
  curl -s -o /dev/null -w '%{http_code}\n' --resolve <nextcloud-host>:443:<server-ip> \
    --cacert <homelab-ca.crt> -u "$NEXTCLOUD_USER:$NEXTCLOUD_APP_PASSWORD" \
    -X PROPFIND -H 'Depth: 0' "$CALDAV_URL/calendars/<app-user>/chores/"
  ```

The planner uses method 2 (CalDAV as the app user). Method 1 stays useful for inspection and admin fixes.
