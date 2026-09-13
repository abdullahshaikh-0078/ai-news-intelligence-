# Developer Guide & Contribution Workflow

## 1. Local Development Environment

### Prerequisites
- Python 3.11 or higher
- Node.js 20+ and npm 10+
- Docker & Docker Compose (optional for local PostgreSQL)

---

## 2. Backend Setup

### Virtual Environment Initialization
```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Command Prompt)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

### Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### PostgreSQL Setup
The application connects via a canonical async PostgreSQL URL:
```bash
DATABASE_URL=postgresql+asyncpg://postgres@localhost:5433/ai_news_intel
TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5433/ai_news_intel_test
```

### Database Migrations (Alembic)
Apply migrations to the development database:
```bash
cd backend
alembic upgrade head
```

To roll back:
```bash
alembic downgrade -1
```

### Run Server Locally
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
- Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Liveness probe: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Readiness probe (PostgreSQL ping): [http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready)

### Working with the Source Registry
Manage upstream content providers using the `/api/v1/sources` REST API:
```bash
# Seed default baseline sources (ArXiv, MIT Tech Review, OpenAI, Two Minute Papers, GitHub Trending)
curl -X POST http://127.0.0.1:8000/api/v1/sources/seed

# List all active sources
curl http://127.0.0.1:8000/api/v1/sources?enabled=true

# Register a custom RSS feed
curl -X POST http://127.0.0.1:8000/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"OpenAI Research","type":"RSS","url":"https://openai.com/news/rss.xml","fetch_interval_minutes":60}'

# Disable a source by UUID
curl -X POST http://127.0.0.1:8000/api/v1/sources/<SOURCE_UUID>/disable
```

### Triggering Ingestion

Execute manual ingestion runs against registered sources via `/api/v1/ingestion`:
```bash
# Ingest all enabled YouTube video channels
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/youtube

# Ingest all enabled Hacker News community signal sources
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/hacker-news

# Ingest all authoritative official AI organization sources (OpenAI, Anthropic, Google DeepMind)
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/official

# Ingest all enabled ArXiv research sources
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/arxiv

# Ingest all enabled RSS sources
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/rss

# Ingest a specific source by UUID
curl -X POST http://127.0.0.1:8000/api/v1/ingestion/sources/<SOURCE_UUID>
```

---

## 3. Test Database Strategy & Execution

Tests run against an isolated database `ai_news_intel_test` using `NullPool` to prevent connection leaks across async event loops:
```bash
cd backend
pytest -v
```

All repository tests rollback transactions after execution, preserving an empty/clean test environment without interfering with production or development data.

---

## 4. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```
The interface is served on [http://localhost:3000](http://localhost:3000).

---

## 5. Running Tests

### Backend Test Suite
```bash
cd backend
pytest -v -s
```

### Type Checking & Linting
```bash
# Backend
flake8 app tests
mypy app

# Frontend
npm run lint
```

---

## 6. Development Rules & Philosophy
1. **Never mock production data paths**: Use real RSS and lab feeds; isolate fixtures strictly to unit tests.
2. **Phase Completion**: A phase is complete only when implementation, automated tests, linting, and manual verification are verified.
3. **Keep it Runnable**: The system must run cleanly at the end of every task and phase.
