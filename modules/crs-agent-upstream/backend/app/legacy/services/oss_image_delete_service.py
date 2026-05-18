"""Async deletion service for user-uploaded OSS images."""

from __future__ import annotations

import base64
import email.utils
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.legacy.models.database import OssImageDeleteJob


logger = logging.getLogger(__name__)


class OssImageDeleteService:
    """Persist, retry and confirm deletion of OSS images."""

    TERMINAL_STATUSES = {"confirmed"}
    RETRYABLE_STATUSES = {"pending", "running", "failed"}

    def __init__(self, *, session_factory: sessionmaker | None = None, config_service: Any | None = None):
        self._session_factory = session_factory
        self._config_service = config_service

    def _get_config(self, key: str, default):
        if self._config_service is None:
            return default
        value = self._config_service.get(key, default)
        if isinstance(value, str) and not value.strip():
            return default
        return value if value is not None else default

    @property
    def enabled(self) -> bool:
        return bool(self._get_config("aliyun_oss_delete_enabled", settings.aliyun_oss_delete_enabled))

    @property
    def image_dir(self) -> str:
        return str(self._get_config("aliyun_oss_image_dir", settings.aliyun_oss_image_dir) or "chat_images").strip("/")

    @property
    def bucket(self) -> str:
        return str(self._get_config("aliyun_oss_bucket_name", settings.aliyun_oss_bucket_name) or "").strip()

    @property
    def endpoint(self) -> str:
        endpoint = str(self._get_config("aliyun_oss_endpoint", settings.aliyun_oss_endpoint) or "").strip()
        return endpoint.removeprefix("https://").removeprefix("http://").strip("/")

    @property
    def access_key_id(self) -> str:
        return str(
            self._get_config("aliyun_oss_access_key_id", settings.aliyun_oss_access_key_id)
            or self._get_config("aliyun_speech_access_key_id", settings.aliyun_speech_access_key_id)
            or ""
        ).strip()

    @property
    def access_key_secret(self) -> str:
        return str(
            self._get_config("aliyun_oss_access_key_secret", settings.aliyun_oss_access_key_secret)
            or self._get_config("aliyun_speech_access_key_secret", settings.aliyun_speech_access_key_secret)
            or ""
        ).strip()

    @property
    def delete_token_secret(self) -> str:
        return str(
            self._get_config("aliyun_oss_delete_token_secret", settings.aliyun_oss_delete_token_secret)
            or self.access_key_secret
            or ""
        ).strip()

    @property
    def token_expire_seconds(self) -> int:
        return max(
            60,
            int(
                self._get_config(
                    "aliyun_oss_delete_token_expire_seconds",
                    settings.aliyun_oss_delete_token_expire_seconds,
                )
            ),
        )

    @property
    def worker_interval_seconds(self) -> int:
        return max(
            1,
            int(
                self._get_config(
                    "aliyun_oss_delete_worker_interval_seconds",
                    settings.aliyun_oss_delete_worker_interval_seconds,
                )
            ),
        )

    @property
    def worker_batch_size(self) -> int:
        return max(
            1,
            min(
                100,
                int(
                    self._get_config(
                        "aliyun_oss_delete_worker_batch_size",
                        settings.aliyun_oss_delete_worker_batch_size,
                    )
                ),
            ),
        )

    @property
    def retry_base_seconds(self) -> int:
        return max(
            1,
            int(
                self._get_config(
                    "aliyun_oss_delete_retry_base_seconds",
                    settings.aliyun_oss_delete_retry_base_seconds,
                )
            ),
        )

    @property
    def retry_max_seconds(self) -> int:
        return max(
            self.retry_base_seconds,
            int(
                self._get_config(
                    "aliyun_oss_delete_retry_max_seconds",
                    settings.aliyun_oss_delete_retry_max_seconds,
                )
            ),
        )

    @property
    def max_attempts(self) -> int:
        return max(
            0,
            int(
                self._get_config(
                    "aliyun_oss_delete_max_attempts",
                    settings.aliyun_oss_delete_max_attempts,
                )
            ),
        )

    def ensure_schema(self) -> None:
        if self._session_factory is None:
            return
        bind = self._session_factory.kw.get("bind")
        if bind is None:
            return
        OssImageDeleteJob.__table__.create(bind=bind, checkfirst=True)

    def _validate_object_key(self, object_key: str) -> str:
        normalized = str(object_key or "").strip().lstrip("/")
        prefix = f"{self.image_dir}/"
        if not normalized:
            raise ValueError("object_key is required")
        if len(normalized) > 512:
            raise ValueError("object_key is too long")
        if "\\" in normalized or ".." in normalized:
            raise ValueError("invalid object_key")
        if not normalized.startswith(prefix):
            raise ValueError("object_key is outside image directory")
        return normalized

    def _token_payload(self, *, object_key: str, session_id: str | None, user_id: int | None, expires_at: int) -> str:
        return "|".join(
            [
                object_key,
                str(session_id or ""),
                str(user_id or ""),
                str(expires_at),
            ]
        )

    def _sign_delete_token(self, payload: str) -> str:
        digest = hmac.new(self.delete_token_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def create_delete_token(self, *, object_key: str, session_id: str | None, user_id: int | None) -> str:
        object_key = self._validate_object_key(object_key)
        if not self.delete_token_secret:
            raise ValueError("OSS delete token secret is not configured")
        expires_at = int(time.time()) + self.token_expire_seconds
        payload = self._token_payload(object_key=object_key, session_id=session_id, user_id=user_id, expires_at=expires_at)
        signature = self._sign_delete_token(payload)
        return f"{expires_at}.{signature}"

    def validate_delete_token(
        self,
        *,
        object_key: str,
        delete_token: str,
        session_id: str | None,
        user_id: int | None,
    ) -> str:
        object_key = self._validate_object_key(object_key)
        try:
            expires_raw, signature = str(delete_token or "").split(".", 1)
            expires_at = int(expires_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid delete token") from exc
        if expires_at < int(time.time()):
            raise ValueError("delete token expired")
        payload = self._token_payload(object_key=object_key, session_id=session_id, user_id=user_id, expires_at=expires_at)
        expected = self._sign_delete_token(payload)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("delete token mismatch")
        return object_key

    def enqueue_delete_jobs(
        self,
        *,
        objects: list[dict[str, str]],
        session_id: str | None,
        user_id: int | None,
        reason: str,
    ) -> dict[str, int]:
        if self._session_factory is None:
            raise RuntimeError("database is unavailable")
        if not self.enabled:
            raise RuntimeError("OSS image deletion is disabled")

        accepted = 0
        skipped = 0
        invalid = 0
        now = datetime.now(UTC).replace(tzinfo=None)
        db: Session = self._session_factory()
        try:
            for item in objects[:50]:
                object_key = str(item.get("key") or item.get("object_key") or "").strip()
                delete_token = str(item.get("delete_token") or item.get("deleteToken") or "").strip()
                try:
                    object_key = self.validate_delete_token(
                        object_key=object_key,
                        delete_token=delete_token,
                        session_id=session_id,
                        user_id=user_id,
                    )
                except ValueError:
                    invalid += 1
                    continue

                token_hash = hashlib.sha256(delete_token.encode("utf-8")).hexdigest()
                existing = db.query(OssImageDeleteJob).filter(OssImageDeleteJob.object_key == object_key).first()
                if existing is not None:
                    if existing.status == "confirmed":
                        skipped += 1
                    else:
                        existing.delete_token_hash = token_hash
                        existing.session_id = session_id
                        existing.user_id = user_id
                        existing.reason = reason[:50] or "new_search"
                        existing.status = "pending"
                        existing.next_retry_at = now
                        existing.last_error = None
                        accepted += 1
                    continue

                db.add(
                    OssImageDeleteJob(
                        object_key=object_key,
                        session_id=session_id,
                        user_id=user_id,
                        reason=reason[:50] or "new_search",
                        status="pending",
                        delete_token_hash=token_hash,
                        next_retry_at=now,
                    )
                )
                accepted += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {"accepted": accepted, "skipped": skipped, "invalid": invalid}

    def _authorization_header(self, *, method: str, date: str, object_key: str) -> str:
        canonical_resource = f"/{self.bucket}/{object_key}"
        string_to_sign = f"{method}\n\n\n{date}\n{canonical_resource}"
        signature = base64.b64encode(
            hmac.new(self.access_key_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        ).decode("ascii")
        return f"OSS {self.access_key_id}:{signature}"

    def _object_url(self, object_key: str) -> str:
        return f"https://{self.bucket}.{self.endpoint}/{quote(object_key, safe='/')}"

    async def _request_oss(self, *, method: str, object_key: str) -> httpx.Response:
        date = email.utils.formatdate(usegmt=True)
        headers = {
            "Date": date,
            "Authorization": self._authorization_header(method=method, date=date, object_key=object_key),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.request(method, self._object_url(object_key), headers=headers)

    async def _delete_and_confirm(self, object_key: str) -> None:
        if not self.access_key_id or not self.access_key_secret or not self.bucket or not self.endpoint:
            raise RuntimeError("OSS deletion configuration is incomplete")

        delete_response = await self._request_oss(method="DELETE", object_key=object_key)
        if delete_response.status_code not in (200, 202, 204, 404):
            raise RuntimeError(f"OSS delete failed: {delete_response.status_code} {delete_response.text[:300]}")

        head_response = await self._request_oss(method="HEAD", object_key=object_key)
        if head_response.status_code == 404:
            return
        if head_response.status_code in (200, 204):
            raise RuntimeError("OSS object still exists after delete")
        raise RuntimeError(f"OSS delete confirmation failed: {head_response.status_code} {head_response.text[:300]}")

    def _retry_delay_seconds(self, attempt_count: int) -> int:
        delay = self.retry_base_seconds * (2 ** max(0, min(attempt_count - 1, 10)))
        return min(self.retry_max_seconds, delay)

    async def process_due_jobs_once(self) -> dict[str, int]:
        if self._session_factory is None or not self.enabled:
            return {"processed": 0, "confirmed": 0, "failed": 0}

        now = datetime.now(UTC).replace(tzinfo=None)
        db: Session = self._session_factory()
        try:
            jobs = (
                db.query(OssImageDeleteJob)
                .filter(
                    OssImageDeleteJob.status.in_(self.RETRYABLE_STATUSES),
                    or_(OssImageDeleteJob.next_retry_at.is_(None), OssImageDeleteJob.next_retry_at <= now),
                )
                .order_by(OssImageDeleteJob.created_at.asc())
                .limit(self.worker_batch_size)
                .all()
            )
            job_snapshots = [(int(job.id), str(job.object_key)) for job in jobs]
            for job in jobs:
                job.status = "running"
            db.commit()
        finally:
            db.close()

        processed = 0
        confirmed = 0
        failed = 0
        for job_id, object_key in job_snapshots:
            processed += 1
            try:
                await self._delete_and_confirm(object_key)
            except Exception as exc:
                failed += 1
                await self._mark_failed(job_id, str(exc))
            else:
                confirmed += 1
                await self._mark_confirmed(job_id)

        return {"processed": processed, "confirmed": confirmed, "failed": failed}

    async def _mark_confirmed(self, job_id: int) -> None:
        if self._session_factory is None:
            return
        db: Session = self._session_factory()
        try:
            job = db.query(OssImageDeleteJob).filter(OssImageDeleteJob.id == job_id).first()
            if job is not None:
                job.status = "confirmed"
                job.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
                job.last_error = None
            db.commit()
        finally:
            db.close()

    async def _mark_failed(self, job_id: int, error: str) -> None:
        if self._session_factory is None:
            return
        db: Session = self._session_factory()
        try:
            job = db.query(OssImageDeleteJob).filter(OssImageDeleteJob.id == job_id).first()
            if job is not None:
                job.attempt_count = int(job.attempt_count or 0) + 1
                max_attempts = self.max_attempts
                if max_attempts > 0 and job.attempt_count >= max_attempts:
                    job.status = "failed"
                else:
                    job.status = "pending"
                job.last_error = error[:2000]
                job.next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                    seconds=self._retry_delay_seconds(job.attempt_count)
                )
            db.commit()
        finally:
            db.close()
