from __future__ import annotations

import json
from typing import Any

from app.config import (
    BUILTIN_EXCLUDE_LABELS,
    SETUP_FILE,
    SYSTEM_EXCLUDE_LABELS,
    Settings,
    extra_excludes_csv,
)


SETUP_KEYS = (
    "source_kind",
    "source_path",
    "source_label",
    "dest_kind",
    "dest_path",
    "dest_smb_host",
    "dest_smb_share",
    "dest_smb_path",
    "dest_smb_user",
    "dest_smb_pass",
    "dest_smb_domain",
    "dest_label",
    "exclude_system",
    "extra_excludes",
    "transfers",
    "checkers",
)


def load_setup() -> dict[str, Any]:
    if not SETUP_FILE.exists():
        return {}
    try:
        return json.loads(SETUP_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_setup(data: dict[str, Any]) -> None:
    SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = load_setup()
    payload = dict(data)
    if "extra_excludes" in payload:
        if payload["extra_excludes"] is None:
            payload.pop("extra_excludes")
        else:
            payload["extra_excludes"] = extra_excludes_csv(payload["extra_excludes"])
    for key, value in payload.items():
        if key in SETUP_KEYS:
            current[key] = value
    SETUP_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    try:
        SETUP_FILE.chmod(0o600)
    except OSError:
        pass


def apply_to_settings(settings: Settings, data: dict[str, Any] | None = None) -> None:
    payload = dict(data) if data is not None else load_setup()
    if "extra_excludes" in payload and payload["extra_excludes"] is not None:
        payload["extra_excludes"] = extra_excludes_csv(payload["extra_excludes"])
    for key, value in payload.items():
        if key in SETUP_KEYS and value is not None and hasattr(settings, key):
            setattr(settings, key, value)


def public_setup(settings: Settings) -> dict[str, Any]:
    return {
        "complete": settings.setup_complete(),
        "source_kind": settings.source_kind,
        "source_path": settings.source_path,
        "source_label": settings.source_label,
        "dest_kind": settings.dest_kind,
        "dest_path": settings.dest_path,
        "dest_smb_host": settings.dest_smb_host,
        "dest_smb_share": settings.dest_smb_share,
        "dest_smb_path": settings.dest_smb_path,
        "dest_smb_user": settings.dest_smb_user,
        "dest_smb_pass_set": bool(settings.dest_smb_pass),
        "dest_smb_domain": settings.dest_smb_domain,
        "dest_label": settings.dest_label,
        "exclude_system": settings.exclude_system,
        "extra_excludes": settings.extra_exclude_list(),
        "builtin_excludes": list(BUILTIN_EXCLUDE_LABELS),
        "system_excludes": list(SYSTEM_EXCLUDE_LABELS),
        "transfers": settings.transfers,
        "checkers": settings.checkers,
    }
