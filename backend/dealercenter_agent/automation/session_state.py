"""Shared load/save for the Playwright storage_state session file
(.auth_state.json at the repo root) — the single place that decides
what "save" means (create vs overwrite) and reports which happened, so
every caller (the web app's login flow, the manual CLI scripts) behaves
the same way instead of each reimplementing write_text() inline."""

import json
from pathlib import Path


def load_saved_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    existed = path.exists()
    path.write_text(json.dumps(state))
    action = "Overwrote existing" if existed else "Created new"
    print(f"[session] {action} session file: {path}")
