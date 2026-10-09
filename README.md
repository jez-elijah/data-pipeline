# Data Pipeline with CI/CD

Extract → Validate → Load → Model (star schema) → Schedule, with a GitHub Actions test suite.
**This costs nothing to run** — see [Quick start: run this for
free](#quick-start-run-this-for-free) below.

- **Extract**: pulls current weather readings from the [Open-Meteo](https://open-meteo.com)
  public API (no API key required) for a configurable list of cities.
- **Validate**: a Pydantic schema enforces types, required fields, and
  sane-range checks (lat/lon bounds, temperature/windspeed bounds, known
  weather codes, timestamp sanity). Bad records are rejected individually —
  one bad row never blocks the rest of the batch.
- **Load**: upserts validated rows into Postgres or MySQL (dialect-aware,
  `ON CONFLICT` / `ON DUPLICATE KEY UPDATE`) via SQLAlchemy Core.
- **Schedule**: a cron-triggered GitHub Actions workflow (free), or a plain
  crontab entry if you'd rather run it on your own server.
- **CI/CD**: every push/PR runs the full unit + integration test suite
  against a real Postgres service container, plus a lint job.

## Architecture

```mermaid
flowchart LR
    API["Open-Meteo public API"] --> E["extract.py"]
    E --> V["validate.py<br/>Pydantic schema + range checks"]
    V -->|valid rows| L["load.py<br/>idempotent upsert"]
    V -.->|rejected rows are logged,<br/>batch continues| X["Rejected"]
    L --> S[("weather_readings<br/>staging table")]
    S --> W["warehouse.py<br/>star schema build"]
    W --> F[("fact_weather_reading<br/>+ 3 dimension tables")]
    GH["GitHub Actions<br/>hourly cron"] -.->|runs| E
    CI["GitHub Actions CI<br/>unit + integration tests"] -.->|tests| V
    CI -.->|tests, live Postgres| L
```

The staging table is the raw, validated landing zone. The warehouse stage only reads from it, so the extract → validate → load flow is unchanged.

## Quick start: run this for free

No AWS/Azure account, no credit card, no time-limited trial. Just a free
hosted database and GitHub Actions (which is what runs the schedule).

1. **Get a free Postgres database.** Sign up at [neon.tech](https://neon.tech)
   or [supabase.com](https://supabase.com) — both have a permanently free
   tier (not a 12-month trial). Create a project and copy the connection
   string, e.g. `postgresql://user:pass@host/dbname`.
2. **Add the SQLAlchemy driver prefix.** Change `postgresql://` to
   `postgresql+psycopg2://`, so it reads
   `postgresql+psycopg2://user:pass@host/dbname`.
3. **Push this repo to GitHub.**
   ```bash
   git init && git add . && git commit -m "initial commit"
   # create a repo on GitHub, then:
   git remote add origin <your-repo-url>
   git push -u origin main
   ```
4. **Add the connection string as a repo secret.** In your GitHub repo:
   *Settings → Secrets and variables → Actions → New repository secret* →
   name it `DATABASE_URL` → paste the string from step 2.
5. **Done.** `.github/workflows/ci.yml` already runs the test suite on every
   push using its own disposable Postgres container (no secret needed).
   `.github/workflows/run-pipeline.yml` reads your `DATABASE_URL` secret and
   runs the real pipeline hourly, writing into your free database.

That's the whole setup: two free services (GitHub Actions + Neon/Supabase),
no cloud billing account required. One thing to know: free-tier hosted
Postgres (Neon in particular) can pause an idle database after a period of
inactivity — the next run just has a brief cold-start delay reconnecting,
it doesn't fail or cost anything.

## Project layout

```
pipeline/
  config.py    # env-var driven settings
  extract.py   # pull from the public API
  validate.py  # Pydantic schema + range checks  <- unit-tested
  db.py        # SQLAlchemy engine + table definition
  load.py      # upsert into Postgres/MySQL
  warehouse.py # staging -> star schema (dim_city, dim_datetime, dim_weather_code, fact_weather_reading)
  main.py      # orchestrates extract -> validate -> load -> warehouse

tests/
  test_warehouse.py         # star schema: idempotency, no orphan facts, dimensions
  test_validate.py          # 51 tests in total (41 validation, 7 warehouse, 3 load integration).
  test_load_integration.py  # SQLite always; Postgres if DATABASE_URL is set
.github/workflows/
  ci.yml            # runs tests on every push (matrix: 3.10/3.11/3.12 + lint)
  run-pipeline.yml  # scheduled (hourly) real pipeline run -- this is the free scheduler
docker-compose.yml   # local Postgres + pipeline container
crontab.example      # plain-server scheduling alternative
```
## Data model

Two layers, both created and updated by `python -m pipeline.main`:

1. **Staging** (`weather_readings`): one validated row per city per observed timestamp, enforced by a unique constraint on `(city, observed_at)`.
2. **Warehouse** (star schema): built from staging for analytics.

```mermaid
erDiagram
    dim_city ||--o{ fact_weather_reading : city_key
    dim_datetime ||--o{ fact_weather_reading : datetime_key
    dim_weather_code ||--o{ fact_weather_reading : weather_code_key

    dim_city {
        int city_key PK
        string city UK
        float latitude
        float longitude
    }
    dim_datetime {
        int datetime_key PK "YYYYMMDDHH, UTC"
        datetime hour_start_utc
        int year
        int month
        int day
        int hour
        int day_of_week "Monday = 0"
        bool is_weekend
    }
    dim_weather_code {
        int weather_code_key PK
        string description
        string category
    }
    fact_weather_reading {
        int reading_id PK
        int city_key FK
        int datetime_key FK
        int weather_code_key FK
        float temperature_c
        float windspeed_kmh
        datetime fetched_at
    }
```

**Grain:** one reading per city per observed hour (`UNIQUE (city_key, datetime_key)`).

**Design notes**
- `dim_datetime` uses a smart key (`YYYYMMDDHH`, UTC), so facts can be filtered by date without a join to compute the key.
- `dim_weather_code` maps Open-Meteo weather codes to a description and a coarser category (for example, thunderstorm variants roll up to "Thunderstorm").
- **Idempotent:** re-running the warehouse stage on the same staging data creates no duplicate dimension or fact rows; existing facts are updated in place.
- **Portable:** SQLAlchemy Core with plain select / insert / update, so the same code runs on SQLite, Postgres, and MySQL.



## Run it locally

```bash
cp .env.example .env
docker compose up -d postgres     # starts local Postgres on :5432
pip install -r requirements.txt
python -m pipeline.main
```

Or run the whole thing (Postgres + pipeline) in containers:

```bash
docker compose up --build
```

## Run the tests

```bash
pip install -r requirements.txt
pytest                 # unit tests + SQLite integration test (no setup needed)

# To also exercise the real Postgres upsert path locally:
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg2://pipeline:pipeline@localhost:5432/pipeline pytest
```

## CI/CD

`.github/workflows/ci.yml` runs on every push and pull request:
1. Spins up a `postgres:16` service container.
2. Installs dependencies for Python 3.10/3.11/3.12 (matrix).
3. Runs `pytest`, including the Postgres upsert integration test against the
   service container.
4. A separate `lint` job runs `ruff` over `pipeline/` and `tests/`.

`.github/workflows/run-pipeline.yml` is the **scheduler**: it runs the real
`extract → validate → load` job hourly via `cron: "0 * * * *"`, reading
`DATABASE_URL` from a repository secret so it can write to your hosted
database. This is the simplest way to "schedule it" with zero extra
infrastructure — GitHub Actions is the cron daemon.

## Deploying elsewhere (if you ever want to)

`pipeline.main.run_pipeline()` is the single entrypoint everything else
wraps — it doesn't know or care whether it's invoked by GitHub Actions, a
crontab, or a cloud function. If you later want it running on AWS Lambda,
Azure Functions, or similar, it's a small wrapper: a handler that calls
`run_pipeline()` and a scheduled trigger (EventBridge, a Timer trigger,
etc.) pointed at it. Just be aware that managed databases on those
platforms (RDS, Azure Database for PostgreSQL) are typically free for 12
months and then bill normally — point at a free Neon/Supabase database
instead if you want to keep costs at zero.

## Configuration

All settings are environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | SQLAlchemy connection string (Postgres or MySQL) |
| `API_BASE_URL` | Open-Meteo forecast endpoint | Swap in a different API/CSV source |
| `REQUEST_TIMEOUT_S` | `10` | HTTP timeout per city request |
| `TABLE_NAME` | `weather_readings` | Target table name |
| `CITIES` | 5 major cities | `City:lat:lon,...` list to fetch |

## Extending to a different source

`extract.py` is the only module that knows about Open-Meteo. To pull from a
CSV feed instead, replace `extract()`'s body with a `csv.DictReader` over the
feed URL/file and keep yielding the same flat dict shape — `validate.py` and
`load.py` don't change.

## Validation rules (unit-tested in `tests/test_validate.py`)

- Required fields: `city`, `latitude`, `longitude`, `temperature_c`,
  `windspeed_kmh`, `observed_at` (all rejected if missing).
- `city` must be non-empty.
- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180].
- `temperature_c` ∈ [-90, 60] °C (physically plausible extremes).
- `windspeed_kmh` ∈ [0, 500].
- `weathercode` (optional) ∈ [0, 99] — Open-Meteo's documented WMO code range.
- `observed_at` must parse as a timestamp and not be more than 15 minutes in
  the future (catches clock skew / garbled payloads).
