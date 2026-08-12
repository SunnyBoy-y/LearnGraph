"""Trusted host-side acquisition of public images and GitHub source snapshots.

The sandbox remains offline. This service validates and fetches remote content on
its behalf, pins every HTTPS connection to a previously classified public IP,
verifies the downloaded bytes, persists immutable receipts, and only then links
content-addressed files into the durable session workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import http.client
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import ssl
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    ExternalAcquisitionFile,
    ExternalAcquisitionReceipt,
    FileRecord,
)
from app.providers.remote.sandbox import validate_agent_workspace_path
from app.repositories.audit import AuditRepository
from app.services.sandbox_network_policy import (
    EgressPolicyDenied,
    normalize_hostname,
    system_resolver,
    _classify_all,
)
from app.services.session_workspace import SessionWorkspaceService


GITHUB_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}


class AcquisitionApprovalRequired(Exception):
    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        super().__init__(hostname)


@dataclass(frozen=True)
class DownloadedResponse:
    requested_url: str
    final_url: str
    redirect_chain: list[dict[str, Any]]
    resolved_addresses: dict[str, list[str]]
    status: int
    declared_mime: str
    data: bytes


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP destination is a pre-classified public IP."""

    def __init__(self, host: str, ip: str, *, port: int, timeout: float) -> None:
        super().__init__(host=host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        import socket

        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class ExternalAcquisitionService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace = SessionWorkspaceService(db, workspace_id, actor_id, settings)
        self.audit = AuditRepository(db, workspace_id)

    @staticmethod
    def canonical_spec(kind: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        spec = {"kind": kind, **arguments}
        encoded = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return spec, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def initial_hostname(url: str) -> str:
        parsed = urlsplit(url.strip())
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise AppError(422, "external_download_url_invalid", "Only credential-free HTTPS URLs are supported")
        if parsed.port not in {None, 443}:
            raise AppError(422, "external_download_port_invalid", "External downloads only support HTTPS port 443")
        if not parsed.hostname:
            raise AppError(422, "external_download_url_invalid", "Download URL must include a public hostname")
        try:
            return normalize_hostname(parsed.hostname)
        except Exception as exc:
            raise AppError(422, "external_download_host_invalid", "Download host must be a public DNS name") from exc

    def download_image(
        self,
        *,
        chat_session_id: str,
        url: str,
        destination_path: str,
        allowed_hosts: set[str],
        request_spec_sha256: str,
        approval_by_host: dict[str, str] | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        safe_path = validate_agent_workspace_path(destination_path)
        response = self._download(
            url,
            allowed_hosts=allowed_hosts,
            max_bytes=self.settings.external_image_download_max_bytes,
        )
        wire_sha = hashlib.sha256(response.data).hexdigest()
        if expected_sha256 and wire_sha != expected_sha256.casefold():
            raise AppError(422, "external_download_hash_mismatch", "Downloaded image did not match expected SHA-256")
        safe_data, mime, width, height, image_format = self._sanitize_image(response.data)
        if len(safe_data) > self.settings.external_image_download_max_bytes:
            raise AppError(413, "external_image_sanitized_too_large", "Sanitized image exceeds the configured limit")
        self._check_workspace_quota(len(safe_data))
        self._ensure_destination_available(
            chat_session_id,
            safe_path,
            hashlib.sha256(safe_data).hexdigest(),
        )
        view = self.workspace.put_bytes(
            chat_session_id=chat_session_id,
            path=safe_path,
            data=safe_data,
            role="input",
            source="external_download",
            mime_type=mime,
            publish_file=True,
            commit=False,
        )
        acquired_files = [{
            "path": safe_path,
            "sha256": view["blob_sha256"],
            "size_bytes": view["size_bytes"],
            "file_id": view.get("file_id"),
            "mime_type": mime,
        }]
        receipt = self._receipt(
            kind="image",
            request_spec_sha256=request_spec_sha256,
            requested_url=response.requested_url,
            final_url=response.final_url,
            redirect_chain=response.redirect_chain,
            resolved_addresses=response.resolved_addresses,
            declared_mime=response.declared_mime,
            detected_mime=mime,
            wire_bytes=len(response.data),
            stored_bytes=len(safe_data),
            sha256=view["blob_sha256"],
            file_id=view.get("file_id"),
            destination_path=safe_path,
            files=acquired_files,
            commit=False,
            provenance={
                "wire_sha256": wire_sha,
                "image_format": image_format,
                "width": width,
                "height": height,
                "sanitized": True,
                "approval_by_host": dict(approval_by_host or {}),
            },
        )
        # Blob/SessionWorkspaceEntry/FileRecord/Receipt/association become visible
        # in one commit, so a persistence failure leaves no receipt-less files.
        self.db.commit()
        self.db.refresh(receipt)
        return {**view, "receipt_id": receipt.id, "width": width, "height": height, "sanitized": True}

    def download_images(
        self,
        *,
        chat_session_id: str,
        urls: list[str],
        destination_dir: str,
        allowed_hosts: set[str],
        request_spec_sha256: str,
        approval_by_host: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Download several images in parallel into one destination directory.

        Host approval is validated BEFORE any network activity, so a missing
        authorization raises ``AcquisitionApprovalRequired`` and the caller can
        show one approval card. Network fetches and image sanitization run in a
        thread pool (no DB access in workers); persistence happens sequentially
        on the caller thread inside a single transaction, so a failure cannot
        leave receipt-less files. Per-image failures are collected and returned
        without aborting the rest of the batch.
        """
        unique: list[str] = []
        seen: set[str] = set()
        for raw in urls:
            url = raw.strip()
            if url and url not in seen:
                seen.add(url)
                unique.append(url)
        if not unique:
            raise AppError(422, "invalid_tool_arguments", "download_external_image requires at least one URL")
        missing: list[str] = []
        for url in unique:
            host = self.initial_hostname(url)
            if host not in allowed_hosts and host not in missing:
                missing.append(host)
        if missing:
            raise AcquisitionApprovalRequired(missing[0])
        base = validate_agent_workspace_path(destination_dir).rstrip("/")

        prepared: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        max_parallel = max(1, int(getattr(self.settings, "external_image_download_max_parallel", 4) or 4))
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(unique))) as executor:
            futures = {
                executor.submit(self._prepare_image, url, allowed_hosts): url
                for url in unique
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    prepared[url] = future.result()
                except AppError as exc:
                    errors.append({
                        "url": url,
                        "error_code": exc.code,
                        "error_message": exc.message,
                    })
                except Exception as exc:  # noqa: BLE001 — per-image isolation
                    errors.append({
                        "url": url,
                        "error_code": "external_download_failed",
                        "error_message": str(exc)[:300],
                    })

        total_incoming = sum(len(item["safe"]) for item in prepared.values())
        if total_incoming:
            self._check_workspace_quota(total_incoming)

        downloaded: list[dict[str, Any]] = []
        for index, url in enumerate(unique, start=1):
            item = prepared.get(url)
            if item is None:
                continue
            filename = self._image_filename(url, item["mime"], index)
            path = validate_agent_workspace_path(f"{base}/{filename}")
            if len(item["safe"]) > self.settings.external_image_download_max_bytes:
                errors.append({
                    "url": url,
                    "error_code": "external_image_sanitized_too_large",
                    "error_message": "Sanitized image exceeds the configured limit",
                })
                continue
            safe_sha = hashlib.sha256(item["safe"]).hexdigest()
            self._ensure_destination_available(chat_session_id, path, safe_sha)
            view = self.workspace.put_bytes(
                chat_session_id=chat_session_id,
                path=path,
                data=item["safe"],
                role="input",
                source="external_download",
                mime_type=item["mime"],
                publish_file=True,
                commit=False,
            )
            receipt = self._receipt(
                kind="image",
                request_spec_sha256=request_spec_sha256,
                requested_url=url,
                final_url=item["response"].final_url,
                redirect_chain=item["response"].redirect_chain,
                resolved_addresses=item["response"].resolved_addresses,
                declared_mime=item["response"].declared_mime,
                detected_mime=item["mime"],
                wire_bytes=len(item["response"].data),
                stored_bytes=len(item["safe"]),
                sha256=view["blob_sha256"],
                file_id=view.get("file_id"),
                destination_path=path,
                files=[{
                    "path": path,
                    "sha256": view["blob_sha256"],
                    "size_bytes": view["size_bytes"],
                    "file_id": view.get("file_id"),
                    "mime_type": item["mime"],
                }],
                commit=False,
                provenance={
                    "wire_sha256": item["wire_sha"],
                    "image_format": item["format"],
                    "width": item["width"],
                    "height": item["height"],
                    "sanitized": True,
                    "batch": True,
                    "approval_by_host": dict(approval_by_host or {}),
                },
            )
            downloaded.append({
                **view,
                "receipt_id": receipt.id,
                "url": url,
                "width": item["width"],
                "height": item["height"],
                "sanitized": True,
            })
        self.db.commit()
        return {"downloaded": downloaded, "failed": errors}

    def _prepare_image(self, url: str, allowed_hosts: set[str]) -> dict[str, Any]:
        response = self._download(
            url,
            allowed_hosts=allowed_hosts,
            max_bytes=self.settings.external_image_download_max_bytes,
        )
        wire_sha = hashlib.sha256(response.data).hexdigest()
        safe, mime, width, height, image_format = self._sanitize_image(response.data)
        return {
            "response": response,
            "safe": safe,
            "mime": mime,
            "width": width,
            "height": height,
            "format": image_format,
            "wire_sha": wire_sha,
        }

    @staticmethod
    def _image_filename(url: str, mime: str, index: int) -> str:
        name = PurePosixPath(urlsplit(url).path).name
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]
        if not name or PurePosixPath(name).suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}:
            extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "img")
            name = f"image_{index}.{extension}"
        return name

    def download_github_source(
        self,
        *,
        chat_session_id: str,
        owner: str,
        repo: str,
        ref: str,
        path: str,
        destination_root: str,
        allowed_hosts: set[str],
        request_spec_sha256: str,
        approval_by_host: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not GITHUB_NAME_RE.fullmatch(owner) or not GITHUB_NAME_RE.fullmatch(repo):
            raise AppError(422, "github_reference_invalid", "GitHub owner and repository names are invalid")
        clean_ref = ref.strip() or "HEAD"
        if len(clean_ref) > 200 or any(char in clean_ref for char in "\r\n\0"):
            raise AppError(422, "github_reference_invalid", "GitHub ref is invalid")
        clean_path = self._github_path(path)
        root = validate_agent_workspace_path(destination_root).rstrip("/")

        api_base = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
        commit_payload, commit_response = self._download_json(
            f"{api_base}/commits/{quote(clean_ref, safe='')}",
            allowed_hosts=allowed_hosts,
            max_bytes=self.settings.external_github_metadata_max_bytes,
        )
        commit = str(commit_payload.get("sha") or "").casefold() if isinstance(commit_payload, dict) else ""
        if not GITHUB_COMMIT_RE.fullmatch(commit):
            raise AppError(502, "github_resolve_failed", "GitHub did not return an immutable commit SHA")

        tree_payload, tree_response = self._download_json(
            f"{api_base}/git/trees/{commit}?{urlencode({'recursive': '1'})}",
            allowed_hosts=allowed_hosts,
            max_bytes=self.settings.external_github_metadata_max_bytes,
        )
        if not isinstance(tree_payload, dict) or not isinstance(tree_payload.get("tree"), list):
            raise AppError(502, "github_tree_failed", "GitHub repository tree response is invalid")
        if tree_payload.get("truncated"):
            raise AppError(413, "github_tree_truncated", "GitHub truncated this repository tree; narrow the requested path")

        prefix = f"{clean_path}/" if clean_path else ""
        candidates: list[tuple[str, int]] = []
        for item in tree_payload["tree"]:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            item_path = self._github_path(str(item.get("path") or ""))
            if clean_path and item_path != clean_path and not item_path.startswith(prefix):
                continue
            mode = str(item.get("mode") or "")
            if mode != "100644" and mode != "100755":
                # Reject symlinks, submodules and other non-regular Git entries.
                raise AppError(422, "github_entry_unsupported", f"Unsupported Git entry mode at {item_path}")
            size = int(item.get("size") or 0)
            if size > self.settings.external_github_file_max_bytes:
                raise AppError(413, "github_file_too_large", f"GitHub file exceeds the limit: {item_path}")
            candidates.append((item_path, size))
        if not candidates:
            raise AppError(404, "github_path_not_found", "No regular GitHub files matched the requested path")
        if len(candidates) > self.settings.external_github_max_files:
            raise AppError(413, "github_too_many_files", "GitHub snapshot exceeds the configured file-count limit")
        estimated = sum(size for _, size in candidates)
        if estimated > self.settings.external_github_total_max_bytes:
            raise AppError(413, "github_snapshot_too_large", "GitHub snapshot exceeds the configured byte limit")
        self._check_workspace_quota(estimated)

        staged: list[tuple[str, str, bytes]] = []
        total = 0
        download_redirects = [
            *commit_response.redirect_chain,
            *tree_response.redirect_chain,
        ]
        resolved_addresses = {
            **commit_response.resolved_addresses,
            **tree_response.resolved_addresses,
        }
        raw_host = "raw.githubusercontent.com"
        if raw_host not in allowed_hosts:
            raise AcquisitionApprovalRequired(raw_host)
        for item_path, _ in candidates:
            quoted_path = "/".join(quote(segment, safe="") for segment in item_path.split("/"))
            raw_url = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/{commit}/{quoted_path}"
            response = self._download(
                raw_url,
                allowed_hosts=allowed_hosts,
                max_bytes=self.settings.external_github_file_max_bytes,
            )
            total += len(response.data)
            if total > self.settings.external_github_total_max_bytes:
                raise AppError(413, "github_snapshot_too_large", "GitHub snapshot exceeded the configured byte limit")
            download_redirects.extend(response.redirect_chain)
            resolved_addresses.update(response.resolved_addresses)
            if response.data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise AppError(422, "github_lfs_unsupported", f"Git LFS pointers are not downloaded: {item_path}")
            relative = (
                item_path[len(prefix):]
                if prefix and item_path.startswith(prefix)
                else PurePosixPath(item_path).name
                if item_path == clean_path
                else item_path
            )
            target = validate_agent_workspace_path(f"{root}/{relative}")
            staged.append((target, item_path, response.data))

        # No durable workspace writes occur until every remote file is fetched and
        # validated, so network/limit failures cannot leave a partial snapshot.
        # All DB rows (blobs, entries, FileRecords, receipt, associations) are
        # flushed and committed together below.
        manifest: list[dict[str, Any]] = []
        acquired_files: list[dict[str, Any]] = []
        for target, item_path, data in staged:
            self._ensure_destination_available(
                chat_session_id,
                target,
                hashlib.sha256(data).hexdigest(),
            )
        for target, item_path, data in staged:
            view = self.workspace.put_bytes(
                chat_session_id=chat_session_id,
                path=target,
                data=data,
                role="input",
                source="github_snapshot",
                publish_file=True,
                commit=False,
            )
            manifest.append({
                "path": target,
                "source_path": item_path,
                "size_bytes": len(data),
                "sha256": view["blob_sha256"],
                "file_id": view.get("file_id"),
            })
            acquired_files.append({
                "path": target,
                "sha256": view["blob_sha256"],
                "size_bytes": view["size_bytes"],
                "file_id": view.get("file_id"),
                "mime_type": view.get("mime_type") or "application/octet-stream",
            })

        manifest_sha = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt = self._receipt(
            kind="github_snapshot",
            request_spec_sha256=request_spec_sha256,
            requested_url=f"https://github.com/{owner}/{repo}/tree/{quote(clean_ref, safe='')}/{clean_path}",
            final_url=tree_response.final_url,
            redirect_chain=download_redirects,
            resolved_addresses=resolved_addresses,
            declared_mime=tree_response.declared_mime,
            detected_mime="application/vnd.learngraph.github-manifest+json",
            wire_bytes=total,
            stored_bytes=total,
            sha256=manifest_sha,
            file_id=None,
            destination_path=root,
            files=acquired_files,
            commit=False,
            provenance={
                "owner": owner,
                "repo": repo,
                "requested_ref": clean_ref,
                "commit_sha": commit,
                "requested_path": clean_path,
                "manifest": manifest,
                "manifest_sha256": manifest_sha,
                "approval_by_host": dict(approval_by_host or {}),
            },
        )
        self.db.commit()
        self.db.refresh(receipt)
        return {
            "receipt_id": receipt.id,
            "owner": owner,
            "repo": repo,
            "commit_sha": commit,
            "destination_root": root,
            "file_count": len(manifest),
            "total_bytes": total,
            "manifest_sha256": manifest_sha,
            "files": manifest,
        }

    def _download_json(self, url: str, *, allowed_hosts: set[str], max_bytes: int) -> tuple[Any, DownloadedResponse]:
        response = self._download(
            url,
            allowed_hosts=allowed_hosts,
            max_bytes=max_bytes,
            accept="application/vnd.github+json",
        )
        try:
            return json.loads(response.data.decode("utf-8")), response
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(502, "external_download_invalid_json", "Remote service returned invalid JSON") from exc

    def _download(
        self,
        url: str,
        *,
        allowed_hosts: set[str],
        max_bytes: int,
        accept: str = "*/*",
    ) -> DownloadedResponse:
        requested_url = url.strip()
        current = requested_url
        redirects: list[dict[str, Any]] = []
        resolved: dict[str, list[str]] = {}
        for _ in range(self.settings.external_download_max_redirects + 1):
            parsed = urlsplit(current)
            host = self.initial_hostname(current)
            if host not in allowed_hosts:
                raise AcquisitionApprovalRequired(host)
            addresses = system_resolver(host)
            try:
                classified = _classify_all(addresses)
            except EgressPolicyDenied as exc:
                raise AppError(422, "external_download_host_blocked", "Download host did not resolve exclusively to public addresses") from exc
            resolved[host] = [address for address, _ in classified]
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            connection = _PinnedHTTPSConnection(
                host,
                classified[0][0],
                port=443,
                timeout=float(self.settings.external_download_timeout_seconds),
            )
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Host": host,
                        "User-Agent": "LearnGraph-ExternalAcquisition/1.0",
                        "Accept": accept,
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                status = int(response.status)
                if status in REDIRECT_STATUSES:
                    location = response.getheader("Location")
                    response.read(4096)
                    if not location:
                        raise AppError(502, "external_download_redirect_invalid", "Remote redirect omitted Location")
                    target = urljoin(current, location)
                    redirects.append({"status": status, "from": current, "to": target})
                    current = target
                    continue
                if status < 200 or status >= 300:
                    response.read(4096)
                    raise AppError(502, "external_download_http_error", f"Remote server returned HTTP {status}")
                declared_length = response.getheader("Content-Length")
                try:
                    declared_size = int(declared_length) if declared_length else None
                except (TypeError, ValueError):
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes:
                    raise AppError(413, "external_download_too_large", "Remote content exceeds the configured limit")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise AppError(413, "external_download_too_large", "Remote content exceeds the configured limit")
                return DownloadedResponse(
                    requested_url=requested_url,
                    final_url=current,
                    redirect_chain=redirects,
                    resolved_addresses=resolved,
                    status=status,
                    declared_mime=(response.getheader("Content-Type") or "application/octet-stream").split(";", 1)[0].strip().casefold(),
                    data=data,
                )
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                raise AppError(502, "external_download_failed", "Remote download failed") from exc
            finally:
                connection.close()
        raise AppError(422, "external_download_too_many_redirects", "Remote download exceeded the redirect limit")

    def _sanitize_image(self, data: bytes) -> tuple[bytes, str, int, int, str]:
        try:
            with Image.open(BytesIO(data)) as probe:
                image_format = str(probe.format or "").upper()
                width, height = probe.size
                frames = int(getattr(probe, "n_frames", 1) or 1)
                if image_format not in SUPPORTED_IMAGE_FORMATS:
                    raise AppError(422, "external_image_format_unsupported", "Only PNG, JPEG, and WebP images are supported")
                if frames != 1:
                    raise AppError(422, "external_image_animated_unsupported", "Animated images are not supported")
                if width <= 0 or height <= 0 or width * height > self.settings.external_image_download_max_pixels:
                    raise AppError(413, "external_image_pixels_exceeded", "Image dimensions exceed the configured pixel limit")
                probe.load()
                sanitized = probe.convert("RGB") if image_format == "JPEG" else probe.convert("RGBA")
                output = BytesIO()
                if image_format == "JPEG":
                    sanitized.save(output, format="JPEG", quality=92, optimize=True)
                    mime = "image/jpeg"
                elif image_format == "WEBP":
                    sanitized.save(output, format="WEBP", quality=92, method=4)
                    mime = "image/webp"
                else:
                    sanitized.save(output, format="PNG", optimize=True)
                    mime = "image/png"
                return output.getvalue(), mime, width, height, image_format
        except AppError:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
            raise AppError(422, "external_image_invalid", "Downloaded content is not a valid supported image") from exc

    @staticmethod
    def _github_path(value: str) -> str:
        raw = value.strip().strip("/")
        if not raw:
            return ""
        parts = raw.split("/")
        if any(part in {"", ".", ".."} or "\0" in part or "\\" in part for part in parts):
            raise AppError(422, "github_path_invalid", "GitHub path is invalid")
        normalized = PurePosixPath(*parts).as_posix()
        if len(normalized) > 1000:
            raise AppError(422, "github_path_invalid", "GitHub path is too long")
        return normalized

    def _ensure_destination_available(
        self,
        chat_session_id: str,
        path: str,
        expected_sha256: str,
    ) -> None:
        try:
            entry = self.workspace.get_entry(chat_session_id, path)
        except AppError as exc:
            if exc.code == "session_workspace_entry_not_found":
                return
            raise
        if entry.blob_sha256 != expected_sha256:
            raise AppError(
                409,
                "external_download_destination_exists",
                f"Destination already contains different content: {path}",
            )

    def _check_workspace_quota(self, incoming: int) -> None:
        quota = int(self.settings.workspace_storage_quota_bytes or 0)
        if quota <= 0:
            return
        used = self.db.scalar(
            select(func.coalesce(func.sum(FileRecord.size_bytes), 0)).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.lifecycle_status != "deleted",
            )
        ) or 0
        if int(used) + incoming > quota:
            raise AppError(413, "workspace_storage_quota_exceeded", "Download would exceed the workspace storage quota")

    def _receipt(
        self,
        *,
        files: list[dict[str, Any]] | None = None,
        commit: bool = True,
        **values: Any,
    ) -> ExternalAcquisitionReceipt:
        receipt = ExternalAcquisitionReceipt(
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            downloader_version="1",
            **values,
        )
        self.db.add(receipt)
        self.db.flush()
        for item in files or []:
            self.db.add(
                ExternalAcquisitionFile(
                    workspace_id=self.workspace_id,
                    receipt_id=receipt.id,
                    file_id=item.get("file_id"),
                    path=item["path"],
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                    mime_type=item.get("mime_type") or "application/octet-stream",
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="external_acquisition.completed",
            resource_type="external_acquisition_receipt",
            resource_id=receipt.id,
            details={
                "kind": receipt.kind,
                "request_spec_sha256": receipt.request_spec_sha256,
                "sha256": receipt.sha256,
                "stored_bytes": receipt.stored_bytes,
                "destination_path": receipt.destination_path,
            },
        )
        if commit:
            self.db.commit()
            self.db.refresh(receipt)
        return receipt
