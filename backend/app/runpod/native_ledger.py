"""Durable estimated-budget reservations for offline native Serverless preparation.

No provider calls or secrets. All admission is conditional on trusted, reviewed
qualification; this ledger cannot impose a provider-guaranteed dollar cap.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .native_admission import (
    CatalogEvidence,
    EndpointEvidence,
    NativeAdmissionError,
    NativeQualification,
    OwnedCleanupEvidence,
    WorkerEvidence,
    billing_time,
    fresh,
    identity,
    integer,
    money_micro,
    timestamp,
    uuid_text,
)

_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class NativeLedger:
    """One shared file per verified account, outside user-specific app profiles.

    Caller cannot reset allowance; construction only binds identity. Changing an
    approved amount needs a separate audited approval path, absent here.
    """

    def __init__(self, path: Path, account_id: str):
        self.path, self.account = path, identity(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS binding(account TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS budget(
                  account TEXT PRIMARY KEY, approved INTEGER NOT NULL, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                  id TEXT PRIMARY KEY, account TEXT NOT NULL, state TEXT NOT NULL,
                  endpoint TEXT UNIQUE, qualification TEXT NOT NULL, rate INTEGER NOT NULL,
                  reserved INTEGER NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
                  created REAL NOT NULL, last_now REAL NOT NULL, catalog_at REAL NOT NULL,
                  deadline REAL NOT NULL, stop_by REAL NOT NULL,
                  warm_until REAL, absence_count INTEGER NOT NULL DEFAULT 0,
                  last_absence REAL, stopped_at REAL, settlement TEXT);
                CREATE TABLE IF NOT EXISTS operations(
                  id TEXT PRIMARY KEY, session TEXT NOT NULL, hash TEXT NOT NULL,
                  state TEXT NOT NULL, execution INTEGER NOT NULL, queue INTEGER NOT NULL,
                  job TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS epochs(
                  session TEXT NOT NULL, worker TEXT NOT NULL, gpu TEXT NOT NULL,
                  observed REAL NOT NULL, PRIMARY KEY(session,worker));
                CREATE TABLE IF NOT EXISTS bills(
                  session TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                  amount INTEGER NOT NULL, PRIMARY KEY(session,start,end));
            """)
            # executescript commits its initial schema script: explicitly reenter the lock.
            db.execute("BEGIN IMMEDIATE")
            binding = db.execute("SELECT account FROM binding").fetchone()
            if binding and binding[0] != self.account:
                raise NativeAdmissionError("Ledger belongs to a different Runpod account")
            db.execute("INSERT OR IGNORE INTO binding VALUES(?)", (self.account,))

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error as exc:
            db.rollback()
            raise NativeAdmissionError("Local cloud ledger needs recovery") from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def approve_once(self, usd: str, *, approval_receipt: str) -> None:
        amount = money_micro(usd)
        self._sha(approval_receipt)
        if amount < 10_000:
            raise NativeAdmissionError("Explicit spending approval is required")
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM budget WHERE account=?", (self.account,)
            ).fetchone()
            if existing and (
                existing["approved"] != amount or existing["receipt"] != approval_receipt
            ):
                raise NativeAdmissionError("Approved allowance cannot be reset")
            db.execute(
                "INSERT OR IGNORE INTO budget VALUES(?,?,?)",
                (self.account, amount, approval_receipt),
            )

    @staticmethod
    def _sha(value: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise NativeAdmissionError("A reviewed receipt or payload hash is required")
        return value

    def _row(self, db, session_id: str):
        row = db.execute(
            "SELECT * FROM sessions WHERE id=? AND account=?", (uuid_text(session_id), self.account)
        ).fetchone()
        if not row:
            raise NativeAdmissionError("Unknown native session")
        return row

    @staticmethod
    def _qualification(row) -> NativeQualification:
        return NativeQualification(**json.loads(row["qualification"]))

    @staticmethod
    def _now(db, row, now: float) -> float:
        now = timestamp(now)
        if now < row["last_now"]:
            raise NativeAdmissionError("Local clock moved backwards; recovery is required")
        db.execute("UPDATE sessions SET last_now=? WHERE id=?", (now, row["id"]))
        return now

    def refresh_catalog(
        self, session_id: str, catalog: CatalogEvidence, *, minimum_vram_gb: int, now: float
    ) -> None:
        with self._transaction() as db:
            row = self._row(db, session_id)
            self._now(db, row, now)
            q = self._qualification(row)
            rate = catalog.ceiling(q, now=now, minimum_vram_gb=minimum_vram_gb)
            if row["state"] not in {"registered", "starting", "active"}:
                raise NativeAdmissionError("Session does not admit rate updates")
            rate = max(rate, row["rate"])
            extra = (rate - row["rate"]) * q.effective_workers * (row["stop_by"] - row["created"])
            extra = int(extra)
            if self._available(db) < extra:
                raise NativeAdmissionError("New GPU rate exceeds approved allowance")
            db.execute(
                "UPDATE sessions SET rate=?,reserved=reserved+?,catalog_at=? WHERE id=?",
                (rate, extra, catalog.observed_at, row["id"]),
            )

    def _available(self, db) -> int:
        budget = db.execute(
            "SELECT approved FROM budget WHERE account=?", (self.account,)
        ).fetchone()
        if not budget:
            raise NativeAdmissionError("Explicit spending approval is required")
        # Settled rows retain actual recorded spend forever. Unsettled rows retain
        # max(reserved, paid), including uncertain starts and delayed invoices.
        total = db.execute(
            """SELECT COALESCE(SUM(CASE WHEN settlement IS NULL
                  THEN MAX(reserved,paid) ELSE paid END),0)
                  FROM sessions WHERE account=?""",
            (self.account,),
        ).fetchone()[0]
        return budget[0] - total

    def available_micro(self) -> int:
        with self._transaction() as db:
            return self._available(db)

    def open_session(
        self,
        session_id: str,
        *,
        qualification: NativeQualification,
        catalog: CatalogEvidence,
        minimum_vram_gb: int,
        duration_seconds: int,
        disk_reserve_usd: str,
        now: float,
    ) -> dict:
        session_id, now = uuid_text(session_id), timestamp(now)
        qualification.check(now)
        rate = catalog.ceiling(qualification, now=now, minimum_vram_gb=minimum_vram_gb)
        integer(duration_seconds, 120, qualification.max_session_seconds)
        # All startup attempts and final idle/stop tail must fit the allocated window.
        floor = (
            qualification.startup_seconds * (qualification.restart_attempts + 1)
            + 60
            + qualification.stop_reserve_seconds
            + 10
        )
        if duration_seconds < floor:
            raise NativeAdmissionError("Insufficient startup and stopping allowance")
        disk = money_micro(disk_reserve_usd)
        if disk <= 0:
            raise NativeAdmissionError("Qualified disk reserve is required")
        reserved = rate * qualification.effective_workers * duration_seconds + disk
        evidence = json.dumps(asdict(qualification), allow_nan=False, sort_keys=True)
        with self._transaction() as db:
            prior = db.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if prior:
                raise NativeAdmissionError("Session intent already exists; reconcile it")
            if db.execute(
                "SELECT 1 FROM sessions WHERE account=? AND state NOT IN ('stopped')",
                (self.account,),
            ).fetchone():
                raise NativeAdmissionError("Previous cloud session needs recovery")
            if self._available(db) < reserved:
                raise NativeAdmissionError("Insufficient approved compute allowance")
            stop_by = now + duration_seconds
            if stop_by >= qualification.expires_at:
                raise NativeAdmissionError("Qualification does not cover session lifetime")
            deadline = stop_by - qualification.stop_reserve_seconds - 60
            db.execute(
                """INSERT INTO sessions
                (id,account,state,qualification,rate,reserved,created,last_now,catalog_at,deadline,stop_by)
                VALUES(?,?,'registered',?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    self.account,
                    evidence,
                    rate,
                    reserved,
                    now,
                    now,
                    catalog.observed_at,
                    deadline,
                    stop_by,
                ),
            )
            return dict(self._row(db, session_id))

    def claim_create(self, session_id: str, *, now: float) -> bool:
        """One transport owner only. Crashed/lost creation never gets a new claim."""
        with self._transaction() as db:
            row = self._row(db, session_id)
            self._qualification(row).check(now)
            self._now(db, row, now)
            fresh(row["catalog_at"], now, 300)
            if timestamp(now) >= row["deadline"]:
                raise NativeAdmissionError("Native session expired")
            return (
                db.execute(
                    "UPDATE sessions SET state='starting' WHERE id=? AND state='registered'",
                    (row["id"],),
                ).rowcount
                == 1
            )

    def bind_endpoint(self, session_id: str, endpoint: EndpointEvidence, *, now: float) -> None:
        with self._transaction() as db:
            row = self._row(db, session_id)
            q = self._qualification(row)
            q.check(now)
            self._now(db, row, now)
            fresh(endpoint.observed_at, now, 30)
            if (
                row["state"] != "starting"
                or timestamp(now) >= row["deadline"]
                or endpoint.session_id != row["id"]
                or (row["endpoint"] and row["endpoint"] != endpoint.endpoint_id)
                or (endpoint.image, endpoint.volume_id, endpoint.region, endpoint.pool)
                != (q.image, q.volume_id, q.region, q.pool)
            ):
                raise NativeAdmissionError("Endpoint binding is unconfirmed")
            db.execute(
                "UPDATE sessions SET endpoint=?,state='active' WHERE id=?",
                (identity(endpoint.endpoint_id), row["id"]),
            )

    def observe_workers(self, session_id: str, evidence: WorkerEvidence, *, now: float) -> None:
        with self._transaction() as db:
            row = self._row(db, session_id)
            q = self._qualification(row)
            q.check(now)
            self._now(db, row, now)
            fresh(evidence.observed_at, now, 30)
            if (
                evidence.endpoint_id != row["endpoint"]
                or len(evidence.workers) > q.effective_workers
            ):
                raise NativeAdmissionError("Worker evidence does not match session")
            for worker_id, gpu_id in evidence.workers:
                existing = db.execute(
                    "SELECT gpu FROM epochs WHERE session=? AND worker=?",
                    (row["id"], identity(worker_id)),
                ).fetchone()
                if existing and existing["gpu"] != gpu_id:
                    raise NativeAdmissionError("Worker identity changed hardware")
                db.execute(
                    "INSERT OR IGNORE INTO epochs VALUES(?,?,?,?)",
                    (row["id"], worker_id, gpu_id, evidence.observed_at),
                )
            epochs = db.execute(
                "SELECT COUNT(*) FROM epochs WHERE session=?", (row["id"],)
            ).fetchone()[0]
            if epochs > q.effective_workers * (q.restart_attempts + 1):
                raise NativeAdmissionError("Qualified startup attempts exceeded")

    def reserve_intent(
        self,
        session_id: str,
        operation_id: str,
        payload_hash: str,
        *,
        execution_seconds: int,
        queue_seconds: int,
        now: float,
    ) -> dict:
        operation_id, payload_hash, now = (
            uuid_text(operation_id),
            self._sha(payload_hash),
            timestamp(now),
        )
        with self._transaction() as db:
            row = self._row(db, session_id)
            q = self._qualification(row)
            q.check(now)
            self._now(db, row, now)
            fresh(row["catalog_at"], now, 300)
            integer(execution_seconds, 5, q.execution_seconds)
            integer(queue_seconds, 0, 600)
            prior = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if prior:
                if (prior["session"], prior["hash"], prior["execution"], prior["queue"]) != (
                    row["id"],
                    payload_hash,
                    execution_seconds,
                    queue_seconds,
                ):
                    raise NativeAdmissionError("Operation identity conflicts with prior work")
                return dict(prior)
            if operation_id == row["id"] or row["state"] != "active" or not row["endpoint"]:
                raise NativeAdmissionError("Native session is not ready for admission")
            if now + execution_seconds + queue_seconds > row["deadline"]:
                raise NativeAdmissionError("Insufficient admitted generation time")
            prior_count = db.execute(
                "SELECT COUNT(*) FROM operations WHERE session=?", (row["id"],)
            ).fetchone()[0]
            if prior_count and (row["warm_until"] is None or now >= row["warm_until"]):
                raise NativeAdmissionError("Warm session ended; stop and reconcile first")
            if db.execute(
                "SELECT 1 FROM operations WHERE session=? AND state NOT IN ('terminal')",
                (row["id"],),
            ).fetchone():
                raise NativeAdmissionError("Another operation needs confirmation")
            used = db.execute(
                "SELECT COALESCE(SUM(execution+queue),0) FROM operations WHERE session=?",
                (row["id"],),
            ).fetchone()[0]
            capacity = (
                row["deadline"] - row["created"] - q.startup_seconds * (q.restart_attempts + 1)
            )
            if used + execution_seconds + queue_seconds > capacity:
                raise NativeAdmissionError("Cumulative generation reservation exceeded")
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,'prepared',?,?,NULL,?)",
                (operation_id, row["id"], payload_hash, execution_seconds, queue_seconds, now),
            )
            db.execute("UPDATE sessions SET warm_until=NULL WHERE id=?", (row["id"],))
            return dict(
                db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            )

    def claim_submit(self, operation_id: str, *, now: float) -> bool:
        with self._transaction() as db:
            op = db.execute(
                "SELECT * FROM operations WHERE id=?", (uuid_text(operation_id),)
            ).fetchone()
            if not op:
                raise NativeAdmissionError("Unknown native operation")
            row = self._row(db, op["session"])
            self._qualification(row).check(now)
            self._now(db, row, now)
            fresh(row["catalog_at"], now, 300)
            if (
                row["state"] != "active"
                or timestamp(now) + op["execution"] + op["queue"] > row["deadline"]
            ):
                raise NativeAdmissionError("Operation no longer fits native session")
            return (
                db.execute(
                    "UPDATE operations SET state='submitting' WHERE id=? AND state='prepared'",
                    (op["id"],),
                ).rowcount
                == 1
            )

    def record_job(self, operation_id: str, job_id: str) -> None:
        with self._transaction() as db:
            if (
                db.execute(
                    "UPDATE operations SET job=?,state='submitted' "
                    "WHERE id=? AND state='submitting'",
                    (identity(job_id), uuid_text(operation_id)),
                ).rowcount
                != 1
            ):
                raise NativeAdmissionError("Job submission must not be replayed")

    def observe_job(
        self, operation_id: str, *, job_id: str | None, status: str, now: float
    ) -> None:
        with self._transaction() as db:
            op = db.execute(
                "SELECT * FROM operations WHERE id=?", (uuid_text(operation_id),)
            ).fetchone()
            if not op:
                raise NativeAdmissionError("Unknown native operation")
            row = self._row(db, op["session"])
            self._now(db, row, now)
            if (
                job_id is None
                or op["job"] != job_id
                or not isinstance(status, str)
                or status not in _TERMINAL | {"IN_QUEUE", "IN_PROGRESS"}
            ):
                # 404/unknown cannot release reservations or permit replay.
                raise NativeAdmissionError("Cloud job state is uncertain; reconcile or stop")
            if status in _TERMINAL and op["state"] != "terminal":
                if op["state"] != "submitted":
                    raise NativeAdmissionError("Job state is not confirmed")
                db.execute("UPDATE operations SET state='terminal' WHERE id=?", (op["id"],))
                db.execute(
                    "UPDATE sessions SET warm_until=? WHERE id=? AND state='active'",
                    (min(row["deadline"], now + 60), row["id"]),
                )
            elif op["state"] == "terminal" and status not in _TERMINAL:
                raise NativeAdmissionError("Terminal job state changed")

    def request_stop(self, session_id: str) -> None:
        with self._transaction() as db:
            row = self._row(db, session_id)
            if row["state"] != "stopped":
                db.execute(
                    "UPDATE sessions SET state='stopping',warm_until=NULL WHERE id=?", (row["id"],)
                )

    def observe_absence(
        self,
        session_id: str,
        *,
        endpoint_id: str,
        endpoint_absent: bool,
        workers_absent: bool,
        now: float,
    ) -> None:
        with self._transaction() as db:
            row = self._row(db, session_id)
            now = self._now(db, row, now)
            if row["state"] != "stopping" or identity(endpoint_id) != row["endpoint"]:
                raise NativeAdmissionError("Stop ownership is unconfirmed")
            if endpoint_absent is not True or workers_absent is not True:
                db.execute(
                    "UPDATE sessions SET absence_count=0,last_absence=NULL WHERE id=?", (row["id"],)
                )
                return
            if row["last_absence"] is not None and now <= row["last_absence"]:
                raise NativeAdmissionError("Stop requires independent later observations")
            count = row["absence_count"] + 1
            db.execute(
                "UPDATE sessions SET absence_count=?,last_absence=?,state=?,stopped_at=? "
                "WHERE id=?",
                (
                    count,
                    now,
                    "stopped" if count >= 2 else "stopping",
                    now if count >= 2 else None,
                    row["id"],
                ),
            )

    def apply_billing(self, session_id: str, records: list[dict]) -> int:
        """Exact bucket upserts, never sum repeated/overlapping history queries."""
        if not isinstance(records, list) or len(records) > 1000:
            raise NativeAdmissionError("Invalid billing evidence")
        with self._transaction() as db:
            row = self._row(db, session_id)
            for record in records:
                if not isinstance(record, dict) or record.get("serverlessId") != row["endpoint"]:
                    raise NativeAdmissionError("Billing ownership changed")
                start, end = (
                    billing_time(record.get("startTime")),
                    billing_time(record.get("endTime")),
                )
                if start >= end or start % 3600 or end % 3600:
                    raise NativeAdmissionError("Unaligned billing interval")
                amount = money_micro(record.get("totalAmount"))
                if db.execute(
                    """SELECT 1 FROM bills WHERE session=? AND start<? AND end>?
                    AND NOT(start=? AND end=?)""",
                    (row["id"], end, start, start, end),
                ).fetchone():
                    raise NativeAdmissionError("Overlapping billing bucket shapes")
                db.execute(
                    """INSERT INTO bills VALUES(?,?,?,?) ON CONFLICT(session,start,end)
                    DO UPDATE SET amount=MAX(amount,excluded.amount)""",
                    (row["id"], start, end, amount),
                )
            paid = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM bills WHERE session=?", (row["id"],)
            ).fetchone()[0]
            db.execute("UPDATE sessions SET paid=? WHERE id=?", (paid, row["id"]))
            return paid

    def settle_reviewed_billing(self, session_id: str, *, receipt_sha256: str, now: float) -> None:
        """Trusted reviewed finality audit only, not a status/404/UI boolean.

        Provider bucket API has no documented finality marker. Controller must
        not call automatically from an empty or unchanged history response.
        """
        self._sha(receipt_sha256)
        with self._transaction() as db:
            row = self._row(db, session_id)
            q = self._qualification(row)
            self._now(db, row, now)
            if (
                row["state"] != "stopped"
                or timestamp(now) < row["stopped_at"] + q.billing_lag_seconds
            ):
                raise NativeAdmissionError("Stop and billing review are incomplete")
            if not db.execute("SELECT 1 FROM bills WHERE session=?", (row["id"],)).fetchone():
                raise NativeAdmissionError("Missing billing cannot credit the allowance")
            if row["settlement"] and row["settlement"] != receipt_sha256:
                raise NativeAdmissionError("Billing settlement identity changed")
            db.execute("UPDATE sessions SET settlement=? WHERE id=?", (receipt_sha256, row["id"]))

    def view(self, session_id: str) -> dict:
        with self._transaction() as db:
            return dict(self._row(db, session_id))

    def journal_digest(self, session_id: str) -> str:
        """Sanitized deterministic metadata digest; no generation payload."""
        row = self.view(session_id)
        return hashlib.sha256(json.dumps(row, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def record_cleanup_target(
        self, session_id: str, evidence: OwnedCleanupEvidence, *, now: float
    ) -> None:
        """Late/lost create recovery records identity for stopping, never admission."""
        with self._transaction() as db:
            row = self._row(db, session_id)
            self._now(db, row, now)
            fresh(evidence.observed_at, now, 30)
            q = self._qualification(row)
            if (
                row["state"] not in {"starting", "active", "stopping"}
                or evidence.session_id != row["id"]
                or (evidence.image, evidence.volume_id) != (q.image, q.volume_id)
                or (row["endpoint"] and row["endpoint"] != evidence.endpoint_id)
            ):
                raise NativeAdmissionError("Late cleanup ownership is unconfirmed")
            db.execute(
                "UPDATE sessions SET endpoint=?,state='stopping',warm_until=NULL WHERE id=?",
                (identity(evidence.endpoint_id), row["id"]),
            )

    def due_sessions(self, *, now: float) -> tuple[str, ...]:
        """Desktop cleanup work list; no independent PC-off scheduler is claimed."""
        now = timestamp(now)
        with self._transaction() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE account=? AND state!='stopped'", (self.account,)
            ).fetchall()
            result = []
            for row in rows:
                if now < row["last_now"]:
                    raise NativeAdmissionError("Local clock moved backwards; recovery is required")
                if (
                    row["state"] == "stopping"
                    or now >= row["deadline"]
                    or (row["warm_until"] is not None and now >= row["warm_until"])
                ):
                    result.append(row["id"])
            return tuple(result)

    def operation_view(self, operation_id: str) -> dict:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM operations WHERE id=?", (uuid_text(operation_id),)
            ).fetchone()
            if row is None:
                raise NativeAdmissionError("Unknown native operation")
            self._row(db, row["session"])
            return dict(row)

    def unresolved_sessions(self) -> tuple[dict, ...]:
        with self._transaction() as db:
            return tuple(dict(row) for row in db.execute(
                "SELECT * FROM sessions WHERE account=? AND state!='stopped' ORDER BY created",
                (self.account,),
            ))

    def record_creation(
        self, session_id: str, evidence: OwnedCleanupEvidence, *, now: float
    ) -> None:
        """Flush a confirmed owned identity before worker/readiness checks can fail."""
        with self._transaction() as db:
            row = self._row(db, session_id)
            q = self._qualification(row)
            self._now(db, row, now)
            fresh(evidence.observed_at, now, 30)
            if (row["state"] != "starting" or evidence.session_id != row["id"]
                    or (row["endpoint"] and row["endpoint"] != evidence.endpoint_id)
                    or (evidence.image, evidence.volume_id) != (q.image, q.volume_id)):
                raise NativeAdmissionError("Creation ownership is unconfirmed")
            db.execute(
                "UPDATE sessions SET endpoint=? WHERE id=?",
                (identity(evidence.endpoint_id), row["id"]),
            )
