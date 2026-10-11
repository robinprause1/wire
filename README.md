# Wire

Wire reads a set of sources on a schedule, indexes what it finds, and asks [Jev](https://typesafe.ai) one yes/no question about it. Which sources, how often, and which question is up to you: a **desk** bundles those choices, and one Wire process runs as many desks as you like.

The default example is a crypto desk (26 market and news feeds, one question per coin). The same pipeline works for any set of URLs: ten supplier pages crawled hourly with the question "did {subject} announce a delay?", a few RSS feeds with the question "is there anything here my team has to react to today?", a Telegram channel plus a watchlist of product names.

The desk page and the control panel (Steuerung) are one process. Open the desk at [http://127.0.0.1:8787](http://127.0.0.1:8787) and the panel at [http://127.0.0.1:8787/manage](http://127.0.0.1:8787/manage). The interface is in German. Questions to Jev are English, because most notes are English.

`src/` is a separate local trading UI. It is not imported by Wire and is not required to run this app.

## Quick start

Docker is the path that does not ask you to install PyTorch, Haystack, or Qdrant yourself. The first start downloads the embedding model (about 120 MB) into a volume.

```bash
docker compose up --build
```

Without Docker, from the repository root:

```bash
python3 -m venv intel/.venv
intel/.venv/bin/pip install -r intel/requirements.txt
./intel/run.sh
```

`intel/run.sh` uses `WIRE_PYTHON`, then `intel/.venv`, then creates that venv.

On the first start Wire creates one desk from the crypto template so the panel is not empty. Nothing runs until you either switch on its schedule or press "Jetzt laufen lassen". Open the panel, add the keys you need under Zugangsdaten, then decide.

Keys can also live in a gitignored `.env` at the repository root:

```bash
TYPESAFE_API_KEY=
FOMO_API_KEY=
JEV_MODEL=jev-latest
```

Values saved in the panel override `.env` until you clear them. Responses from the API contain only the last four characters of a key.

## A desk

| Part | What it holds |
| --- | --- |
| Sources | A list of connectors with their parameters: an RSS URL, a page URL plus crawl depth, a Telegram channel, or one of the crypto APIs. Each source can be switched off without deleting it. |
| Steps | The four stations of a run. Switch one off and the runner skips it. |
| Schedule | Every full hour, or a fixed interval from 15 minutes to 24 hours. Off means manual runs only. |
| Brief | Who the candidates are (picker), the question with a `{subject}` slot, what "yes" and "no" mean, how many hours of notes count as evidence, and from which probability the desk shows "Ja". |

Desks are stored as JSON in SQLite. Everything a desk writes — runs, answers, state, index documents — carries its id, so desks do not see each other's data. Deleting a desk removes its runs and answers; its documents stay in the index.

### The four stations

1. **Quellen holen.** Every enabled source is pulled on its own; a failing source does not stop the others. Metered connectors report cost and balance back.
2. **Index schreiben.** Titles and bodies are cleaned, embedded locally, and written to Qdrant with a dense vector and a sparse BM25 vector. Document ids are stable per desk, source and item, so re-ingesting overwrites instead of duplicating.
3. **Kandidaten lesen.** The picker turns the notes of the evidence window into subjects.
4. **Jev fragen.** One yes/no question per subject, built from the brief. Jev returns P(yes); the desk shows "Ja" from the configured bar. If the notes behind the subject list did not change since the last call, the call is skipped.

Search on the desk page runs the same hybrid retrieval (dense plus BM25, fused by reciprocal rank) scoped to the desk and does not call Jev.

### Pickers

| Picker | Subjects | Notes per subject |
| --- | --- | --- |
| `corpus` | One subject: the whole window. | The passages a hybrid search for the question finds. |
| `watchlist` | Each term of your list that occurs in the window (word boundaries, case-insensitive). | Up to three notes that mention the term. |
| `coins` | Tickers detected in the notes, weighted by connector kind. | The strongest note per coin. |

### Connectors

Generic: `rss`, `page` (one URL, optionally plus up to ten same-host links), `telegram` (public channel preview).

Crypto pack: `fomo`, `tree`, `coingecko-trending`, `coingecko-memes`, `fear-greed`, `dexscreener`, `geckoterminal`, `binance`, `bybit`, `okx`.

A connector is a `Connector(kind, label, summary, fetch, …)` in `intel/connectors/`. `fetch(ctx, params)` returns a `Pull` of `Item`s. `ctx` gives scoped state (`ctx.get/set`), account-level state shared across desks (`ctx.shared_get/set`), the previous run of this source, and secrets. Connectors that cost money declare a `secret` and a `balance` so the panel can show both. Add the connector to `REGISTRY` in `intel/connectors/__init__.py` and the panel offers it.

FOMO is the only metered connector in the box: 125 credits per page, one page per hour per desk, and a floor of 20,000 credits below which it refuses to pull.

### Templates

`blank` starts with no sources and the corpus picker. `crypto` is the full crypto desk. A template is a function `(desk_id, name) -> Desk` in `intel/templates.py`.

## API

All routes return JSON. Desk ids are slugs, `[a-z0-9-]{1,40}`.

| Method | Path | Body / Result |
| --- | --- | --- |
| GET | `/api/desks` | `{desks: [summary]}` |
| POST | `/api/desks` | `{template, name}` → 201 `{desk, status}` |
| GET | `/api/desks/{id}` | `{desk, status}` |
| PUT | `/api/desks/{id}` | full desk JSON → `{desk, status}`; 400 with a message on invalid input |
| DELETE | `/api/desks/{id}` | `{ok: true}`; 409 while the desk is running |
| GET | `/api/desks/{id}/board` | sources with last runs, items, verdicts, balances, schedule, brief |
| POST | `/api/desks/{id}/run` | `{forceJev?, fetch?}` → 202; 409 while any desk is running |
| POST | `/api/desks/{id}/ask` | `{question}` → `{question, hits}` (search only, no Jev) |
| GET | `/api/options` | connectors with their fields, pickers, templates, steps, embedder |
| GET / POST | `/api/settings` | credentials (masked) and the Jev model; `{op: "secret", key, value}` or `{op: "model", value}` |

Only one desk runs at a time. The clock fires each desk at its stored target and recomputes the target only after a run, so a slow run never skips the next hour.

## Layout

```
intel/connectors/     base types, HTTP helpers, generic feeds, crypto pack, registry
intel/desk.py         Desk / SourceSpec / Schedule / Brief, validation, JSON
intel/templates.py    blank and crypto templates
intel/subjects.py     pickers (corpus, watchlist, coins)
intel/coins.py        ticker detection for the coins picker
intel/index.py        clean, embed, Qdrant, hybrid query, per-desk filters
intel/judge.py        Jev / TypeSafe client, question building, signatures
intel/cycle.py        one run through the four stations; the runner lock
intel/clock.py        per-desk scheduling thread
intel/desks.py        desk CRUD facade and first-start bootstrap
intel/board.py        read-only payloads for the desk page and the panel
intel/accounts.py     registration, sessions, who may see answers
intel/settings.py     credentials and model
intel/store.py        SQLite: desks, runs, answers, state, secrets, users
intel/server.py       HTTP routing and static files
intel/static/         desk page, panel, globe and line stages, shared helpers
```

SQLite and Qdrant live in `intel/data/` (override with `WIRE_DATA`), which is gitignored. Only one process may open the Qdrant directory.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `WIRE_HOST` | `127.0.0.1` | Bind address. Compose sets `0.0.0.0` inside the container and publishes to localhost only. |
| `WIRE_PORT` | `8787` | Port. |
| `WIRE_DATA` | `intel/data` | Directory for `wire.sqlite` and `qdrant/`. |
| `WIRE_EMBEDDER` | unset | `lexicon` swaps the sentence-transformer and on-disk Qdrant for an in-memory stand-in. Tests use it. |
| `TYPESAFE_API_KEY` | | Jev. Without it every subject is recorded as failed with "TYPESAFE_API_KEY fehlt." instead of calling out. |
| `FOMO_API_KEY` | | Only for the `fomo` connector. |
| `JEV_MODEL` | `jev-latest` | Model name sent to TypeSafe. |
| `WIRE_ADMIN_EMAIL` | | Optional. With `WIRE_ADMIN_PASSWORD`, creates the admin while nobody is registered yet. |
| `WIRE_ADMIN_PASSWORD` | | Password for that bootstrap admin. At least 8 characters. |
| `WIRE_ADMIN_NAME` | `Admin` | Display name for the bootstrap admin. |

## Zugang

Desk and Steuerung require a login. The first account (the setup form, or the admin environment variables) is the admin. Everyone else registers and stays pending until the admin sets them active under **Nutzer**. Active users see the desk and its answers. Runs, sources, the question and credentials stay on Steuerung, which only the admin can open.

## Security

- Passwords are stored as PBKDF2 hashes. The session cookie is `HttpOnly` and holds a random token; only its hash is stored.
- Secrets are written to the local SQLite file; responses contain a masked hint, never the key.
- `.env`, `intel/data/`, and `intel/.venv/` are gitignored. Do not commit them.

## Tests

```bash
WIRE_EMBEDDER=lexicon python -m unittest intel.test_wire -q
```

## Models

| Piece | Role |
| --- | --- |
| `intfloat/multilingual-e5-small` | Local embeddings, 384 dimensions. No API cost. |
| TypeSafe System One, model `jev-latest` | The only paid model call, and only for the subject list. |
