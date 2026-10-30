"""Feishu Base (多维表格) helpers built on the `lark-cli` command line tool.

The CLI is the only supported transport, and it has two constraints that shape
this module:

1. ``--json`` takes a literal JSON string, not ``@file`` (unlike the field
   commands). Windows command lines cap out around 32k characters, so payloads
   must be split into small batches.
2. ``record-list`` returns at most 2000 records per call, so a table larger
   than that has to be paged through with ``--offset``.

Record writes are therefore batched (25 records per call by default) and
existing rows are updated rather than re-created, so the module is safe to
re-run.

Typical use::

    from feishu_base import FeishuBase
    base = FeishuBase("UGWbbFsBzaSQSbsrZuUcyNIon1e")
    base.ensure_table("视频清单", fields=[{"name": "视频ID", "type": "text"}])
    base.upsert_records("tblXXX", [{"视频ID": "BV1...", "平台": ["bilibili"]}])
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


DEFAULT_LARK_CLI = "lark-cli"
DEFAULT_BATCH = 200
RECORD_PAGE_LIMIT = 2000

# Field type names accepted by the CLI.
FIELD_TYPES = (
    "text", "number", "select", "multiSelect", "date", "checkbox",
    "url", "email", "phone", "attachment", "link", "formula", "user",
    "createdTime", "modifiedTime", "createdUser", "modifiedUser", "autoNumber",
)


class FeishuError(RuntimeError):
    """Raised when the CLI reports a failure."""


def _resolve_cli() -> str:
    """Locate lark-cli: explicit override, PATH, then the managed node install."""
    override = os.environ.get("LARK_CLI_PATH")
    if override:
        return override
    found = shutil.which(DEFAULT_LARK_CLI)
    if found:
        return found
    managed = Path.home() / ".workbuddy" / "binaries" / "node"
    if managed.exists():
        for version_dir in sorted(managed.glob("versions/*/lark-cli"), reverse=True):
            if version_dir.exists():
                return str(version_dir)
    return DEFAULT_LARK_CLI


class FeishuBase:
    """Thin, batching wrapper over the `lark-cli base` commands."""

    def __init__(self, base_token: str, *, identity: str = "user",
                 cli: str | None = None, batch_size: int = DEFAULT_BATCH):
        self.base_token = base_token
        self.identity = identity
        self.cli = cli or _resolve_cli()
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    # process plumbing
    # ------------------------------------------------------------------
    def _run(self, args: list[str], *, check: bool = True) -> str:
        command = [self.cli] + args + ["--as", self.identity]
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0 and not result.stdout.strip():
            raise FeishuError(
                f"lark-cli failed (rc={result.returncode}): "
                f"{(result.stderr or '').strip()[:300]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FeishuError(
                f"cannot parse lark-cli output: {result.stdout[:300]}") from exc
        if check and not payload.get("ok"):
            raise FeishuError(f"{args[0]} failed: {json.dumps(payload)[:300]}")
        return result.stdout

    def _run_json(self, args: list[str], *, check: bool = True) -> dict:
        return json.loads(self._run(args, check=check))

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def create_base(self, name: str, *, table_name: str = "",
                    fields: list[dict] | None = None,
                    time_zone: str = "Asia/Shanghai") -> dict:
        args = ["base", "+base-create", "--base-token", self.base_token,
                "--name", name, "--time-zone", time_zone]
        if table_name:
            args += ["--table-name", table_name]
        if fields:
            args += ["--json", json.dumps(fields, ensure_ascii=False)]
        return self._run_json(args)["data"]

    def list_tables(self) -> list[dict]:
        data = self._run_json(["base", "+table-list", "--base-token",
                                self.base_token])["data"]
        return data.get("tables", [])

    def list_fields(self, table_id: str) -> list[dict]:
        data = self._run_json(["base", "+field-list", "--base-token",
                                self.base_token, "--table-id", table_id])["data"]
        return data.get("fields", [])

    def create_field(self, table_id: str, field: dict) -> dict:
        data = self._run_json(["base", "+field-create", "--base-token",
                                self.base_token, "--table-id", table_id,
                                "--json", json.dumps(field, ensure_ascii=False)])
        return data.get("field", {})

    def ensure_field(self, table_id: str, name: str, field_type: str,
                     **kwargs) -> dict | None:
        """Create a field unless one with the same name already exists."""
        for existing in self.list_fields(table_id):
            if existing.get("name") == name:
                return existing
        payload = {"name": name, "type": field_type}
        payload.update(kwargs)
        return self.create_field(table_id, payload)

    # ------------------------------------------------------------------
    # records
    # ------------------------------------------------------------------
    def _record_list(self, table_id: str, fields: list[str], offset: int,
                     workdir: Path) -> tuple[list[dict], bool]:
        args = ["base", "+record-list", "--base-token", self.base_token,
                "--table-id", table_id, "--format", "ndjson",
                "--output", str(workdir / f"page_{offset}.ndjson"), "--overwrite"]
        for field in fields:
            args += ["--field-id", field]
        if offset:
            args += ["--offset", str(offset)]
        data = self._run_json(args, check=False)
        payload = data.get("data", data)
        record_file = payload.get("record_file")
        rows: list[dict] = []
        if record_file and Path(record_file).exists():
            with open(record_file, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            self._remove_artifact(record_file)
        return rows, bool(payload.get("has_more"))

    @staticmethod
    def _remove_artifact(record_file: str) -> None:
        """Drop the ndjson plus the manifest the CLI writes beside it."""
        path = Path(record_file)
        path.unlink(missing_ok=True)
        path.with_suffix(".manifest.json").unlink(missing_ok=True)

    def list_records(self, table_id: str, fields: list[str] | None = None,
                     *, workdir: Path | None = None) -> list[dict]:
        """Read every record, paging past the 2000-record API limit."""
        if workdir is None:
            with tempfile.TemporaryDirectory(prefix="feishu-") as tmp:
                return self.list_records(table_id, fields, workdir=Path(tmp))
        workdir.mkdir(parents=True, exist_ok=True)
        wanted = fields or ["视频ID"]
        out: list[dict] = []
        offset = 0
        while True:
            rows, has_more = self._record_list(table_id, wanted, offset, workdir)
            out.extend(rows)
            if not has_more or not rows:
                break
            offset += len(rows)
        return out

    def record_id_map(self, table_id: str, key_field: str = "视频ID",
                      *, workdir: Path | None = None) -> dict[str, str]:
        """Map a business key (e.g. video id) to its Feishu record id."""
        rows = self.list_records(table_id, [key_field, "record_id"],
                                 workdir=workdir)
        mapping: dict[str, str] = {}
        for row in rows:
            key = row.get(key_field)
            record_id = row.get("record_id")
            if key and record_id:
                mapping[str(key)] = record_id
        return mapping

    def _write_batches(self, table_id: str, command: str, payload_key: str,
                       chunker) -> int:
        """Send batched write payloads.

        ``chunker`` yields already-shaped payload fragments; the batch write
        commands expect different shapes per key (a list for create, a
        record-id map for update), so the caller owns the shaping.

        The payload travels through a temp file referenced as ``@file``.
        Passing it inline blows the Windows command-line limit once a batch
        holds a few dozen long titles.
        """
        written = 0
        with tempfile.TemporaryDirectory(prefix="feishu-") as workdir:
            for index, fragment in enumerate(chunker):
                payload = {payload_key: fragment}
                path = Path(workdir) / f"batch_{index}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
                self._run(["base", f"+{command}", "--base-token", self.base_token,
                           "--table-id", table_id, "--json", f"@{path}"])
                written += len(fragment)
        return written

    def create_records(self, table_id: str, records: list[dict]) -> int:
        """Append new rows. Each record is a field map."""

        def chunks():
            for start in range(0, len(records), self.batch_size):
                yield records[start:start + self.batch_size]

        return self._write_batches(table_id, "record-batch-create",
                                   "create_records", chunks())

    def update_records(self, table_id: str, records: dict[str, dict]) -> int:
        """Update existing rows. Maps record id -> field map.

        The wire format is ``{"update_records": {"recXXX": {...}}}`` — a map,
        not an array of pairs.
        """

        def chunks():
            items = list(records.items())
            for start in range(0, len(items), self.batch_size):
                yield dict(items[start:start + self.batch_size])

        return self._write_batches(table_id, "record-batch-update",
                                   "update_records", chunks())

    def upsert_records(self, table_id: str, records: list[dict],
                       *, key_field: str = "视频ID",
                       workdir: Path | None = None) -> dict:
        """Update rows whose key already exists, create the rest.

        Returns a summary with ``updated`` / ``created`` / ``skipped`` counts.
        """
        mapping = self.record_id_map(table_id, key_field, workdir=workdir)
        to_update: dict[str, dict] = {}
        to_create: list[dict] = []
        skipped = 0
        for record in records:
            key = record.get(key_field)
            record_id = mapping.get(str(key)) if key is not None else None
            if record_id:
                to_update[record_id] = record
            elif key is not None:
                to_create.append(record)
            else:
                skipped += 1
        updated = self.update_records(table_id, to_update) if to_update else 0
        created = self.create_records(table_id, to_create) if to_create else 0
        return {"updated": updated, "created": created, "skipped": skipped}

    # ------------------------------------------------------------------
    # permissions
    # ------------------------------------------------------------------
    def list_members(self, token: str) -> list[dict]:
        data = self._run_json(["drive", "+member-list", "--token", token,
                               "--fields", "*"])["data"]
        return data.get("items", [])

    def add_member(self, token: str, member_id: str, *,
                   member_type: str = "openid", perm: str = "full_access",
                   dry_run: bool = False) -> dict:
        args = ["drive", "+member-add", "--token", token,
                "--member-type", member_type, "--member-id", member_id,
                "--perm", perm]
        if dry_run:
            args.append("--dry-run")
        else:
            args.append("--yes")
        return self._run_json(args)["data"]

    def remove_member(self, token: str, member_id: str, *,
                      member_type: str = "openid") -> dict:
        return self._run_json(["drive", "+member-remove", "--token", token,
                               "--member-type", member_type,
                               "--member-id", member_id, "--yes"])["data"]
