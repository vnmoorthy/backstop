# Backstop — live demo backend (FastAPI swarm server + the dashboard).
# Deploy this to a host (Render / Railway / Fly) WITH the sponsor keys set as
# server-side env vars, and the website becomes genuinely live: real Moss, real
# MiniMax, real TrueFoundry redaction/audit, real PAVO routing — not a replay.
FROM python:3.11-slim

WORKDIR /app
COPY orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Layout must preserve repo-root/web + repo-root/data (the server resolves them
# relative to its package root, i.e. /app).
COPY orchestrator/ ./orchestrator/
COPY web/ ./web/
COPY data/ ./data/

WORKDIR /app/orchestrator
EXPOSE 8000
# Keys come from the host's env (NOT baked in). When MINIMAX_API_KEY / MOSS_* /
# TRUEFOUNDRY_API_KEY / etc. are present, the integrations flip to real.
CMD ["sh", "-c", "python -m uvicorn backstop.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
