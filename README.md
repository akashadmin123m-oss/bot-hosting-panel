# Pinium Host — Deploy-Friendly Telegram Bot Hosting Panel

A mobile-first Flask panel based on the supplied purple/glass UI reference.

## Included

- User registration/login/logout
- Up to 15 bots per user by default (`MAX_BOTS_PER_USER`)
- Upload exactly `bot.py` + `requirements.txt`
- Run / Stop / Restart / Delete
- Live code editor for both files
- Syntax highlighting
- Save / Save & Restart
- Per-bot console and logs
- Hosting timer
- Admin panel
- SQLite locally; PostgreSQL via `DATABASE_URL`
- `/healthz` health endpoint
- Render Blueprint (`render.yaml`)
- Procfile, Dockerfile, start script and Python runtime pin

## Fastest local test

```bash
python -m venv .venv
# Linux/macOS/Termux:
source .venv/bin/activate
# Windows:
# .venv\\Scripts\\activate
pip install -r requirements.txt
export SECRET_KEY="change-me"
export ADMIN_PASSWORD="change-me-now"
python app.py
```

Open `http://127.0.0.1:5000`.

Default admin email: `admin@pinium.local`
Default admin password if `ADMIN_PASSWORD` is not set: `Admin@12345`

Change the admin password before using this publicly.

## GitHub → Render (easiest)

1. Create a GitHub repository.
2. Extract this ZIP and upload the project files to the repository root. **Do not upload the ZIP inside another folder.**
3. In Render choose **New → Blueprint** and select the repository. Render will read `render.yaml`.
4. Set the `ADMIN_PASSWORD` environment variable when Render asks for it.
5. Deploy.
6. Open the generated Render URL.

If you create a Web Service manually instead:

- Build: `pip install -r requirements.txt`
- Start: `./start.sh`
- Health check: `/healthz`

## Database

Local development uses SQLite. For a persistent production database, set:

`DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME`

Do not put database passwords in GitHub. Put them in the hosting provider's secret/environment settings.

## Important: bot uptime and persistence

This project is a deployable **panel starter**, but the included runner executes uploaded Python code as a subprocess of the web service. That is not a hardened multi-tenant sandbox.

For a real public hosting business, use a separate runner/worker service and isolate each bot (containers or another sandbox), with CPU/RAM/process/file/network limits and dependency controls.

Also, a free web service is not a reliable always-on Telegram bot server and its local filesystem is not guaranteed to persist across restarts/redeploys. For persistent bot source files use persistent storage/object storage, and use a persistent database for account metadata.

## Security checklist before public launch

- Use a strong `SECRET_KEY` and `ADMIN_PASSWORD`.
- Add CSRF protection and rate limiting.
- Never let users execute arbitrary host shell commands.
- Do not store bot tokens in source code; use encrypted secrets/environment variables.
- Isolate bot processes from the web app.
- Enforce CPU/RAM/disk/network limits.
- Allowlist or sandbox Python dependencies.
- Add audit logs and backups.
