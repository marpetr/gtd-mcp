# GTD MCP Server

A self-hosted [Getting Things Done](https://gettingthingsdone.com/) server exposed over the [Model Context Protocol](https://modelcontextprotocol.io/), so an AI assistant can capture, organise and review your tasks. It's built with [FastMCP](https://github.com/jlowin/fastmcp) and stores its data in SQLite, with project notes kept as Markdown files.

## Features

- **Inbox**: capture thoughts quickly and process them later into actions, projects or someday/maybe items.
- **Next actions**: filter by context or project, set due dates, update and complete actions.
- **Projects**: track each project's outcome and status, with a single view of its actions, waiting-fors, contacts, quotes and calls.
- **Waiting for**: record what you're waiting on and who you're waiting on.
- **Contacts, quotes and call log**: keep track of suppliers or tradespeople, the quotes they've given you and the calls you've had with them.
- **Notes**: store per-project Markdown notes on disk.

## Tools

| Area | Tools |
|------|-------|
| Inbox | `capture`, `list_inbox`, `process_inbox` |
| Actions | `add_action`, `list_next_actions`, `update_action`, `complete_action` |
| Projects | `create_project`, `list_projects`, `get_project` |
| Waiting for | `add_waiting_for`, `list_waiting_for` |
| Contacts | `add_contact`, `list_contacts`, `update_contact` |
| Quotes | `add_quote`, `list_quotes` |
| Calls | `log_call`, `list_calls` |
| Notes | `list_notes`, `read_note`, `write_note` |

## Setup

Requires Python 3.10+.

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit
```

### Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `GTD_AUTH_TOKEN` | Shared secret that clients must send. **If this is empty, authentication is turned off.** | *(empty)* |
| `GTD_DB` | Path to the SQLite database. It's created on first run. | `/home/pveadmin/assistant/gtd.db` |
| `GTD_NOTES` | Directory for project notes (`<project_id>/<name>.md`). | `/home/pveadmin/assistant/notes` |

### Running

```bash
set -a; source .env; set +a
venv/bin/python server.py
```

The server listens on `http://127.0.0.1:8000/mcp`, using the streamable HTTP transport. It binds to localhost only, so to reach it from other machines you need to put it behind a reverse proxy or tunnel (for example Tailscale Serve/Funnel, Caddy or nginx).

### Authentication

Clients authenticate in one of two ways:

- an `Authorization: Bearer <token>` header, or
- a `?token=<token>` query parameter, for clients that can't set headers.

### systemd

`gtd-mcp.service` runs the server as a service. Edit `User`, `WorkingDirectory`, `EnvironmentFile` and `ExecStart` to match your install, then:

```bash
sudo cp gtd-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gtd-mcp
journalctl -u gtd-mcp -f
```

## Connecting a client

To use the server as a remote MCP connector, point the client at your public URL:

```
https://<your-host>/mcp?token=<GTD_AUTH_TOKEN>
```
