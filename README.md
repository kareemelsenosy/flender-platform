# FLENDER Platform

A full-stack web application for managing fashion order sheets — import from Google Sheets or Excel/CSV, run AI-powered product image search, review and curate images, then export a formatted 23-column Excel order sheet.

---

## Features

- **Multi-source import** — Upload `.xlsx` / `.csv` files or paste Google Sheets URLs (single or batch)
- **Parallel processing** — Multiple imports and image searches run simultaneously with live progress bars
- **AI-powered image search** — Uses Google Custom Search + Bing + DuckDuckGo with Claude/Gemini AI for query building and result re-ranking
- **Smart column mapping** — Auto-detect columns with AI suggestions; save mapping formats for reuse
- **Full review UI** — Full-screen SPA for approving / swapping / rejecting candidate images per product
- **Export** — Generates formatted Excel order sheets with embedded product images
- **Background tasks** — Navigate away freely; searches and imports continue in the background with toast notifications on completion
- **Persistent state** — Task progress and notifications survive server restarts

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.11+) |
| Database | SQLite (dev) / PostgreSQL (production) |
| ORM | SQLAlchemy 2.0 + Alembic migrations |
| Auth | bcrypt + itsdangerous signed cookies |
| Templates | Jinja2 |
| AI | Anthropic Claude API or Google Gemini API |
| Search | Google Custom Search API + Bing + DuckDuckGo |
| Sheets | gspread + Google OAuth2 service account |
| Excel | openpyxl |

---

## Local Development

### 1. Clone and install

```bash
git clone https://github.com/kareemelsenosy/flender-platform.git
cd flender-platform
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add your API keys (all optional, app works without them)
```

Required only for specific features:

| Key | Feature | Where to get |
|-----|---------|--------------|
| `GEMINI_API_KEY` | AI search + column mapping | [aistudio.google.com](https://aistudio.google.com/app/apikey) — free |
| `CLAUDE_API_KEY` | AI search (alternative) | [platform.anthropic.com](https://platform.anthropic.com) |
| `GOOGLE_SEARCH_KEY` + `GOOGLE_CSE_ID` | Web image search | [programmablesearchengine.google.com](https://programmablesearchengine.google.com) — 100 free/day |
| Google service account JSON | Google Sheets import | [console.cloud.google.com](https://console.cloud.google.com) |

### 3. Run

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000)

---

## Google Sheets Setup

1. Create a Google Cloud project and enable the **Google Sheets API** and **Google Drive API**
2. Create a **Service Account** and download the JSON key
3. In the FLENDER app, go to **Settings → Google Credentials** and upload the JSON file
4. Share your Google Sheets with the service account email (it's in the JSON file under `client_email`)

---

## Deployment (Railway — recommended)

Railway runs the app as a persistent process — background threads, SSE streams, and file storage all work without modification.

### 1. Add PostgreSQL

In Railway dashboard → **New → Database → PostgreSQL**. The `DATABASE_URL` env var is injected automatically.

### 2. Set environment variables

```
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
GEMINI_API_KEY=...
GOOGLE_SEARCH_KEY=...
GOOGLE_CSE_ID=...
```

### 3. Add persistent volumes

Mount `/app/uploads`, `/app/output`, and `/app/data` as Railway volumes so user files survive redeploys.

### 4. Run migrations

```bash
# Via Railway console:
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

---

## Project Structure

```
flender-platform/
├── app/
│   ├── main.py                  # FastAPI app + lifespan hooks
│   ├── config.py                # Environment variable config
│   ├── database.py              # SQLAlchemy engine (SQLite + PostgreSQL)
│   ├── models.py                # ORM models
│   ├── auth.py                  # Auth utilities (bcrypt, signed cookies)
│   ├── routers/
│   │   ├── api_routes.py        # Notifications polling, active tasks
│   │   ├── auth_routes.py       # Login, register, logout
│   │   ├── upload_routes.py     # File upload + session management
│   │   ├── mapping_routes.py    # Column mapping + AI suggestions
│   │   ├── search_routes.py     # Image search (web + local)
│   │   ├── review_routes.py     # Review SPA API
│   │   ├── generate_routes.py   # Excel export + downloads
│   │   ├── sheets_routes.py     # Google Sheets batch import
│   │   └── settings_routes.py   # Brand configs + credentials
│   ├── core/
│   │   ├── parser.py            # Excel/CSV file parser
│   │   ├── searcher.py          # Multi-source image search engine
│   │   ├── sheets_reader.py     # Google Sheets reader
│   │   └── generator.py         # Excel order sheet generator
│   ├── services/
│   │   ├── notifications.py     # Persistent notification store
│   │   ├── task_state.py        # Batch progress persistence
│   │   ├── ai_service.py        # Claude/Gemini AI integration
│   │   └── local_search.py      # Local folder image search
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS, JS, images
├── migrations/                  # Alembic DB migrations
├── requirements.txt
├── Procfile                     # Railway/Heroku start command
├── railway.json                 # Railway deploy config
├── alembic.ini                  # Migration config
└── .env.example                 # Environment variable template
```

---

## License

Private — all rights reserved.
