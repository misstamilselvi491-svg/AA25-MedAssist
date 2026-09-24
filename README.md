# AA-25 MedAssist

A deployable FastAPI + SQLite prototype with three evidence agents, a reconciliation layer, human-review gate, audit log, and responsive frontend.

## Local run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```
Open `http://127.0.0.1:8000`.

## Docker
```bash
docker build -t aa25-medassist .
docker run --rm -p 8000:8000 aa25-medassist
```

## API
- `GET /api/health`
- `GET /api/demo`
- `GET /api/audit?limit=50`
- `POST /api/analyze` as multipart form data
- `GET /docs` for Swagger UI

## Important scope
This is a prototype decision-support workflow, not a clinical diagnostic system. The included ranges and keyword rules are demo logic only. Do not use it for real clinical decisions without qualified clinical validation, security review, privacy controls, and regulatory assessment.
