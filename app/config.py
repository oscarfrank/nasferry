import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_data_dir() -> Path:
    raw = os.environ.get("DATA_DIR", "/data")
    path = Path(raw)
    if raw == "/data" and not path.exists():
        path = Path("data")
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = resolve_data_dir()


def parse_extra_excludes(raw: str | list | None) -> list[str]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else str(raw).replace("\n", ",").split(",")
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        name = str(item).strip().strip("/")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def expand_exclude_rule(rule: str) -> list[str]:
    rule = rule.strip()
    if not rule:
        return []
    if any(char in rule for char in "*?[]"):
        return [rule]
    path = rule.strip("/")
    if "/" in path:
        return [f"{path}/**", path]
    return [f"**/{path}/**", f"**/{path}"]


def extra_excludes_csv(raw: str | list | None) -> str:
    return ",".join(parse_extra_excludes(raw))


RCLONE_CONF = DATA_DIR / "rclone.conf"
RCLONE_LOG = DATA_DIR / "rclone.log"
STATE_FILE = DATA_DIR / "state.json"
JOBS_FILE = DATA_DIR / "jobs.jsonl"
SETUP_FILE = DATA_DIR / "setup.json"

DEFAULT_EXCLUDES = [
    "**/.zfs/**",
    "**/.zfs",
    "**/#recycle/**",
    "**/.recycle/**",
    "**/.recycle",
    "**/.windows/**",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/._*",
    "**/@eaDir/**",
    "**/.Trash-*/**",
    "**/CacheClip/**",
    "**/CacheClip",
]

BUILTIN_EXCLUDE_LABELS = [
    ".zfs",
    "#recycle",
    ".recycle",
    ".windows",
    ".DS_Store",
    "Thumbs.db",
    "@eaDir",
    "CacheClip",
]

SYSTEM_EXCLUDES = [
    ".system/**",
    "/.system/**",
    "**/.system/**",
    "ix-applications/**",
    "/ix-applications/**",
    "**/ix-applications/**",
    "ix-apps/**",
    "/ix-apps/**",
    "**/ix-apps/**",
    "ix-virt/**",
    "/ix-virt/**",
    "**/ix-virt/**",
    "**/.ix-virt/**",
]

SYSTEM_EXCLUDE_LABELS = [
    "ix-applications",
    "ix-apps",
    ".system",
    "ix-virt",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    source_kind: Literal["local", "smb"] = "local"
    source_path: str = "/source"
    source_smb_host: str = ""
    source_smb_share: str = ""
    source_smb_path: str = ""
    source_smb_user: str = ""
    source_smb_pass: str = ""
    source_smb_domain: str = "WORKGROUP"

    dest_kind: Literal["local", "smb"] = "smb"
    dest_path: str = "/dest"
    dest_smb_host: str = ""
    dest_smb_share: str = ""
    dest_smb_path: str = ""
    dest_smb_user: str = ""
    dest_smb_pass: str = ""
    dest_smb_domain: str = "WORKGROUP"

    transfers: int = 4
    checkers: int = 8
    auto_start: bool = False
    dashboard_user: str = ""
    dashboard_pass: str = ""
    extra_excludes: str = ""
    exclude_system: bool = True

    rclone_rc: str = "http://127.0.0.1:5572"
    public_port: int = 8080

    source_label: str = Field(default="This NAS")
    dest_label: str = Field(default="Backup NAS")

    def setup_complete(self) -> bool:
        source_ok = bool(self.source_path.strip()) if self.source_kind == "local" else bool(
            self.source_smb_host and self.source_smb_share
        )
        dest_ok = bool(self.dest_smb_host and self.dest_smb_share) if self.dest_kind == "smb" else bool(
            self.dest_path.strip()
        )
        return source_ok and dest_ok

    def extra_exclude_list(self) -> list[str]:
        return parse_extra_excludes(self.extra_excludes)

    def excludes(self) -> list[str]:
        rules = list(DEFAULT_EXCLUDES)
        if self.exclude_system:
            rules.extend(SYSTEM_EXCLUDES)
        for item in self.extra_exclude_list():
            rules.extend(expand_exclude_rule(item))
        seen: set[str] = set()
        unique: list[str] = []
        for rule in rules:
            if rule in seen:
                continue
            seen.add(rule)
            unique.append(rule)
        return unique
