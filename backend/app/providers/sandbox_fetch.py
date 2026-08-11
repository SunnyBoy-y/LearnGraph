"""Sandbox-isolated web fetch provider.

Retrieval of an approved public URL and the parsing of its (untrusted) HTML
happen inside fixed-runner containers — never in the host process. The host
only authorizes the URL against the unified ``web_fetch.policy`` allowlist,
writes an immutable hash-bound fetch spec, and reads back a validated Markdown
artifact. The container has no general Agent argv access and no user profile,
so malicious pages cannot reach the host or carry user cookies.

By default the provider runs fetches on a process-wide pool of warm
containers (see ``app.providers.sandbox_fetch_pool``): containers are created
once per workspace/allowlist/settings tuple and reused, so repeated fetches
skip the per-fetch create/delete cost and up to ``sandbox_web_fetch_pool_size``
fetches run concurrently. Setting the pool size to 0 restores the legacy
short-lived create-per-fetch behavior.

The container's only network path is the internal egress proxy; its outbound
policy is derived from the same unified allowlist (``web_fetch_egress_envelope``),
so the DNS/address classification that matters happens at CONNECT time in the
proxy, while this provider and the runner both enforce exact-host HTTPS
consistency with that policy.

This provider is a settings-gated built-in (opt-in via
``sandbox_web_fetch_enabled`` + ``sandbox_egress_enabled``), not a ProviderConfig
row: it needs no base URL, secret, or remote-capability declaration. When the
gate is off, the empty, or the runtime image is missing, the factory falls back
to the explicit remote FetchProvider / Qwen companion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from typing import Any
from urllib.parse import urlparse

from app.core.config import Settings
from app.domain.models import new_id
from app.providers.ports.fetch import FetchProviderPort
from app.providers.remote.fetch import (
    FetchedDocument,
    FetchProviderError,
    FetchProviderTimeout,
    UnsafeFetchURL,
)
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxBackendUnavailable,
    SandboxOutputLimitExceeded,
    SandboxCreateSpec,
    SandboxSessionHandle,
)
from app.services.sandbox_runtime import resolve_sandbox_image

logger = logging.getLogger(__name__)

_FETCH_TASK = ("python", "/opt/learngraph/runner.py", "--task", "web_fetch")


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_hostname(value: str) -> str:
    return value.strip().casefold().rstrip(".")


def _exact_host_allowed(url: str, allowed_domains: frozenset[str]) -> bool:
    parsed = urlparse(url)
    return parsed.hostname is not None and _normalize_hostname(parsed.hostname) in allowed_domains


class SandboxFetchProvider:
    """``FetchProviderPort`` backed by the isolated fixed ``web_fetch`` runner."""

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        settings: Settings,
        workspace_id: str,
        allowed_domains: frozenset[str],
        backend: Any | None = None,
        pool_namespace: str | None = None,
    ) -> None:
        if not allowed_domains:
            raise ValueError("SandboxFetchProvider requires a non-empty allowlist")
        self.provider_id = provider_id
        self.settings = settings
        self.workspace_id = workspace_id
        self.allowed_domains = allowed_domains
        # Injectable for hermetic tests; production uses the settings-resolved
        # Docker backend.
        self._injected_backend = backend
        # Isolates the process-wide container pool in tests that inject a fake
        # backend; production callers leave it None (backend_id is used).
        self._pool_namespace = pool_namespace

    # --- FetchProviderPort ----------------------------------------------------

    def fetch(self, url: str) -> FetchedDocument:
        target = url.strip()
        if not _exact_host_allowed(target, self.allowed_domains):
            raise UnsafeFetchURL(
                "The requested URL is outside the sandbox web fetch allowlist"
            )
        spec, spec_digest = self._build_spec(target)
        artifact = self._run_container(target, spec, spec_digest)
        final_url = str(artifact["final_url"]).strip()
        if not _exact_host_allowed(final_url, self.allowed_domains):
            raise UnsafeFetchURL(
                "The fetched page is outside the sandbox web fetch allowlist"
            )
        content = str(artifact["markdown"])
        return FetchedDocument(
            source_url=target,
            final_url=final_url,
            title=str(artifact.get("title") or "")[:1_000],
            content=content,
            content_type="text/markdown",
            metadata={
                "provider": "sandbox_web_fetch",
                "extracted_by": str(artifact.get("extracted_by") or "trafilatura"),
                "truncated": bool(artifact.get("truncated")),
                "spec_sha256": spec_digest,
            },
        )

    def probe(self) -> dict[str, object]:
        # No live network probe: the fixed container is single-purpose and the
        # allowlist may not include a probe host. The factory already verified the
        # runtime gate (image resolved) before selecting this provider.
        return {
            "capability": "fetch",
            "provider_type": "sandbox_web_fetch",
            "egress_enabled": self.settings.sandbox_egress_enabled,
            "allowlist_domains": len(self.allowed_domains),
        }

    # --- internals ------------------------------------------------------------

    def _build_spec(self, url: str) -> tuple[dict[str, Any], str]:
        """Build the immutable fetch spec; returns ``(spec, spec_sha256)``.

        The spec is returned (never stored on the instance) so concurrent
        ``fetch()`` calls cannot clobber each other's input files.
        """
        body = {
            "schema_version": "1.0",
            "url": url,
            "allowed_domains": sorted(self.allowed_domains),
            "max_redirects": 5,
            "max_bytes": self.settings.sandbox_web_fetch_max_bytes,
            "timeout_seconds": self.settings.sandbox_web_fetch_timeout_seconds,
        }
        spec_sha256 = hashlib.sha256(_canonical_json(body)).hexdigest()
        return {**body, "spec_sha256": spec_sha256}, spec_sha256

    def _run_container(
        self, url: str, spec: dict[str, Any], spec_digest: str
    ) -> dict[str, Any]:
        backend = (
            self._injected_backend
            if self._injected_backend is not None
            else _backend_for_settings(self.settings)
        )
        if self.settings.sandbox_web_fetch_pool_size > 0:
            artifact = self._run_pooled(backend, spec, spec_digest)
        else:
            artifact = self._run_ephemeral(backend, url, spec)
        if (
            not isinstance(artifact, dict)
            or artifact.get("schema_version") != "1.0"
            or artifact.get("task_type") != "web_fetch"
            or artifact.get("status") != "ok"
            or artifact.get("spec_sha256") != spec_digest
            or not isinstance(artifact.get("markdown"), str)
            or not artifact["markdown"].strip()
            or not isinstance(artifact.get("final_url"), str)
        ):
            raise FetchProviderError(
                "Sandbox web fetch returned an invalid artifact"
            )
        return artifact

    def _exec_timeout_seconds(self) -> int:
        return min(
            self.settings.sandbox_wall_time_seconds,
            int(self.settings.sandbox_web_fetch_timeout_seconds) + 15,
        )

    def _run_pooled(
        self, backend: Any, spec: dict[str, Any], spec_digest: str
    ) -> dict[str, Any]:
        """Execute one fetch on a warm pooled container (reused, concurrent).

        Containers are checked out exclusively (one runner per container at a
        time — a timeout/truncation kills the container, so parallel execs in
        the same container are never safe). Any round-trip failure evicts the
        container so a poisoned container is never reused.
        """
        from app.services.sandbox import _sandbox_workspace_path  # lazy import
        from app.providers.sandbox_fetch_pool import (
            FetchPoolSaturated,
            FetchPoolUnavailable,
            get_fetch_pool,
        )

        namespace = self._pool_namespace or getattr(
            backend, "backend_id", "docker"
        )
        pool = get_fetch_pool(
            namespace=namespace,
            backend=backend,
            settings=self.settings,
            workspace_id=self.workspace_id,
            allowed_domains=self.allowed_domains,
            max_size=self.settings.sandbox_web_fetch_pool_size,
            idle_ttl=self.settings.sandbox_web_fetch_pool_idle_seconds,
        )
        exec_timeout = self._exec_timeout_seconds()
        try:
            entry = pool.acquire(timeout_seconds=exec_timeout + 10)
        except (FetchPoolSaturated, FetchPoolUnavailable) as exc:
            raise FetchProviderError(str(exc)) from exc
        except (SandboxBackendUnavailable, SandboxBackendError) as exc:
            raise FetchProviderError(str(exc)) from exc
        session_token = f"fetch-{new_id()}"
        input_path = f"input/{session_token}.json"
        output_path = f"output/{session_token}.json"
        succeeded = False
        try:
            backend.write(
                entry.handle,
                input_path,
                json.dumps(spec, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
            result = backend.exec_fixed(
                entry.handle,
                (
                    *_FETCH_TASK,
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                ),
                timeout_seconds=exec_timeout,
                output_limit=self.settings.sandbox_output_bytes,
            )
            if result.timed_out:
                raise FetchProviderTimeout("Sandbox web fetch timed out")
            if result.truncated:
                raise SandboxOutputLimitExceeded(
                    "Sandbox web fetch exceeded the configured output limit"
                )
            if result.exit_code != 0:
                detail = result.stderr.decode("utf-8", errors="replace").strip()[:2_000]
                raise FetchProviderError(
                    f"Sandbox web fetch failed: {detail or result.exit_code}"
                )
            artifact_bytes = backend.read(
                entry.handle, output_path, self.settings.sandbox_output_bytes
            )
            artifact = json.loads(artifact_bytes)
            succeeded = True
            return artifact
        except json.JSONDecodeError as exc:
            raise FetchProviderError(
                "Sandbox web fetch returned a non-JSON artifact"
            ) from exc
        except (SandboxBackendUnavailable, SandboxBackendError) as exc:
            raise FetchProviderError(str(exc)) from exc
        finally:
            # Per-fetch input/output files are removed from the host workspace
            # (the container's /workspace is a bind mount) so the pooled
            # workspace never accumulates spec/artifact files across fetches.
            workspace = _sandbox_workspace_path(
                self.settings, entry.workspace_relative
            )
            for relative in (input_path, output_path):
                try:
                    (workspace / relative).unlink(missing_ok=True)
                except OSError:
                    pass
            if succeeded:
                pool.release(entry)
            else:
                # The container is in an unknown/killed state; never reuse it.
                pool.evict(entry)

    def _run_ephemeral(
        self, backend: Any, url: str, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Legacy path (pool disabled): one throwaway container per fetch."""
        from app.services.sandbox import (  # lazy: avoids factory<->sandbox cycle
            _initialize_workspace,
            _sandbox_workspace_path,
            web_fetch_egress_envelope,
        )

        capability = backend.probe()
        if not capability.available:
            raise FetchProviderError(
                capability.reason or "The sandbox runtime is unavailable"
            )
        egress = web_fetch_egress_envelope(
            self.settings, self.workspace_id, self.allowed_domains
        )
        if egress is None:
            raise FetchProviderError(
                "Sandbox web fetch requires enabled egress and a non-empty allowlist"
            )
        sandbox_session_id = f"fetch-{new_id()}"
        relative = _initialize_workspace(self.settings, self.workspace_id, sandbox_session_id)
        handle: SandboxSessionHandle | None = None
        try:
            handle = backend.create(
                SandboxCreateSpec(
                    session_id=sandbox_session_id,
                    image_ref=resolve_sandbox_image(self.settings) or "",
                    memory_bytes=self.settings.sandbox_memory_bytes,
                    memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                    cpu_count=self.settings.sandbox_cpu_count,
                    pids_max=self.settings.sandbox_pids_max,
                    disk_bytes=self.settings.sandbox_disk_bytes,
                    workspace_path=str(
                        _sandbox_workspace_path(self.settings, relative)
                    ),
                    runtime_kind="python-node",
                    egress=egress,
                )
            )
            input_path = f"input/fetch-spec.json"
            output_path = f"output/{sandbox_session_id}.json"
            backend.write(
                handle,
                input_path,
                json.dumps(spec, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
            result = backend.exec_fixed(
                handle,
                (
                    *_FETCH_TASK,
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                ),
                timeout_seconds=self._exec_timeout_seconds(),
                output_limit=self.settings.sandbox_output_bytes,
            )
            if result.timed_out:
                raise FetchProviderTimeout("Sandbox web fetch timed out")
            if result.truncated:
                raise SandboxOutputLimitExceeded(
                    "Sandbox web fetch exceeded the configured output limit"
                )
            if result.exit_code != 0:
                detail = result.stderr.decode("utf-8", errors="replace").strip()[:2_000]
                raise FetchProviderError(
                    f"Sandbox web fetch failed: {detail or result.exit_code}"
                )
            artifact_bytes = backend.read(
                handle, output_path, self.settings.sandbox_output_bytes
            )
            return json.loads(artifact_bytes)
        except json.JSONDecodeError as exc:
            raise FetchProviderError("Sandbox web fetch returned a non-JSON artifact") from exc
        except SandboxBackendUnavailable as exc:
            raise FetchProviderError(str(exc)) from exc
        except SandboxBackendError as exc:
            raise FetchProviderError(str(exc)) from exc
        finally:
            if handle is not None:
                try:
                    backend.delete(handle)
                except Exception:
                    logger.exception(
                        "Failed to clean isolated web fetch container %s",
                        sandbox_session_id,
                    )
            shutil.rmtree(
                _sandbox_workspace_path(self.settings, relative),
                ignore_errors=True,
            )


def _backend_for_settings(settings: Settings):
    # Lazy import: sandbox_bootstrap imports sandbox_runtime (safe) but not
    # factory / sandbox service, so importing here keeps the module cycle-free.
    from app.services.sandbox_bootstrap import backend_for_settings

    return backend_for_settings(settings)
