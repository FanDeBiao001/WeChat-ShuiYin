# Media-parser integration

This service embeds the MIT-licensed `ucmao/media-parser` source at commit
`48ac2b915e4a8df73c8faa54797b5d5b0e0a648c` (see `MEDIA_PARSER_LICENSE`).
The original FastAPI implementation is retained in `server/` for rollback.

Render keeps the existing `wechat-shuiyin.onrender.com` hostname. Configure:

- Build: `pip install -r requirements.txt`
- Start: `API_ONLY=true gunicorn --workers 1 --threads 4 --worker-class gthread --timeout 120 --bind 0.0.0.0:$PORT app:app`
- Health check: `/health`

The published mini-program calls `POST /api/parse` with `mode=native`; that
request receives the legacy response shape and signed, same-origin media links.
Other clients can use the upstream `POST /api/v1/parse` API. Video links expire
after 30 minutes. The free Render filesystem is ephemeral, so links signed
before a service restart may need to be re-parsed.

Check the deployment with a public video before relying on it. Platform login
pages, deleted/private posts and anti-bot challenges may still prevent parsing.
