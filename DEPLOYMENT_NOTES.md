# Deployment notes

## This ZIP is now deployment-oriented

The root contains the files most hosts expect:

- `app.py` — Flask application
- `requirements.txt` — Python dependencies
- `start.sh` — Gunicorn startup using `$PORT`
- `Procfile` — process declaration for Procfile-based hosts
- `render.yaml` — Render Blueprint
- `runtime.txt` — Python 3.11 runtime pin
- `Dockerfile` — generic container deployment
- `.env.example` — environment variable template
- `/healthz` — health check endpoint

## Render

Use **New → Blueprint** with the GitHub repository. Render reads `render.yaml` and uses `./start.sh`.

For manual Web Service setup:

Build command:
`pip install -r requirements.txt`

Start command:
`./start.sh`

Health check:
`/healthz`

Set `ADMIN_PASSWORD` yourself in Render's Environment settings.

## Critical architecture note

The current starter intentionally keeps the web and bot runner in one process/container to make the project easy to test. This is not a safe production multi-tenant design. Uploaded code can execute Python on the host/container.

For production, split into:

1. Web/API panel
2. Persistent PostgreSQL database
3. Persistent object/file storage
4. Bot runner/worker
5. Per-bot sandbox/container
6. Queue for start/stop/restart operations

This is also the architecture to use if you want to scale beyond a small test deployment.
