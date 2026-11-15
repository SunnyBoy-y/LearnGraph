"""Scholarly discovery, bounded OA reading, and explicit library persistence.

No general search provider is constructed here. Searches and reads never save
materials automatically. Existing SourceRecord, SourceCitation and SourceLink
remain the library and graph-association source of truth.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import SourceCitation, SourceRecord, UserWebFetchPolicy, WorkspaceSetting
from app.domain.schemas.papers import Paper, PaperExcerpt, PaperReadRequest, PaperReadResponse, PaperSaveRequest, PaperSearchRequest
from app.providers.remote.papers import PaperSearchProvider
from app.providers.remote.search import normalize_domain, domain_is_allowed
from app.repositories.audit import AuditRepository
from app.services.document_parsers import DocumentParseError, _parse_pdf
from app.services.external_acquisition import AcquisitionApprovalRequired, PublicImageDownloadClient


def bibtex(paper: Paper) -> str:
    def escape(value: str) -> str:
        replacements = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "#": r"\#", "_": r"\_", "$": r"\$", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
        return "".join(replacements.get(c, c) for c in " ".join(value.split()))
    key = re.sub(r"[^A-Za-z0-9]", "", f"{paper.provider}{paper.paper_id}")
    kind = "misc" if paper.publication_type == "preprint" else "article"
    fields = {"title": paper.title, "author": " and ".join(paper.authors),
              "year": str(paper.year) if paper.year else "", "doi": paper.doi or "", "url": paper.url}
    if paper.venue:
        fields["note" if kind == "misc" else "journal"] = paper.venue
    if paper.provider == "arxiv":
        fields.update({"eprint": paper.paper_id, "archivePrefix": "arXiv"})
    return f"@{kind}{{{key},\n" + ",\n".join(f"  {name} = {{{escape(value)}}}" for name, value in fields.items() if value) + "\n}\n"


class PaperService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings,
                 *, provider: PaperSearchProvider | None = None,
                 source_access: Callable[[str, str], bool] | None = None):
        self.db, self.workspace_id, self.actor_id, self.settings = db, workspace_id, actor_id, settings
        self.provider = provider or PaperSearchProvider()
        self.source_access = source_access
        self.audit = AuditRepository(db, workspace_id)

    def search(self, request: PaperSearchRequest):
        # Fixed scholarly API destinations must still respect workspace source restrictions.
        from app.providers.factory import research_policy_domains, access_allow_all
        allowed = research_policy_domains(self.db, self.workspace_id)
        endpoint = "https://api.openalex.org" if request.provider == "openalex" else "https://export.arxiv.org"
        if allowed and not access_allow_all(self.db, self.workspace_id) and not domain_is_allowed(endpoint, set(allowed)):
            raise AppError(403, "paper_provider_domain_denied", "论文服务域名不在工作区允许的来源范围内")
        self.db.commit()  # no database transaction across remote I/O
        response = self.provider.search(request)
        if allowed and not access_allow_all(self.db, self.workspace_id):
            response.results = [paper for paper in response.results if domain_is_allowed(paper.url, set(allowed))]
        self.audit.record(actor_id=self.actor_id, action="paper.search", resource_type="search_query", resource_id="not-persisted",
                          details={"provider": request.provider, "result_count": len(response.results)})
        self.db.commit()
        return response

    def _allowed_domains(self, requested: list[str]) -> tuple[set[str], bool]:
        from app.providers.factory import access_allow_all, access_allowlist_domains
        row = self.db.scalar(select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == self.workspace_id,
                                                          WorkspaceSetting.key == "web_fetch.policy"))
        policy = row.value if row and isinstance(row.value, dict) else {}
        user = self.db.scalar(select(UserWebFetchPolicy).where(UserWebFetchPolicy.workspace_id == self.workspace_id,
                                                             UserWebFetchPolicy.user_id == self.actor_id))
        values = [*requested, *(policy.get("allowed_domains") or []), *(user.allowed_domains if user else []),
                  *access_allowlist_domains(self.db, self.workspace_id)]
        domains = {normalize_domain(value) for value in values if isinstance(value, str) and normalize_domain(value)}
        allow_all = bool(policy.get("allow_without_confirmation") or (user and user.allow_without_confirmation)
                         or access_allow_all(self.db, self.workspace_id))
        return domains, allow_all

    def _resolve(self, reference) -> Paper:
        # Reuse the search-domain policy for singleton lookups too, so save/read
        # cannot bypass a workspace block by skipping the discovery endpoint.
        from app.providers.factory import research_policy_domains, access_allow_all
        allowed = research_policy_domains(self.db, self.workspace_id)
        endpoint = "https://api.openalex.org" if reference.provider == "openalex" else "https://export.arxiv.org"
        if allowed and not access_allow_all(self.db, self.workspace_id) and not domain_is_allowed(endpoint, set(allowed)):
            raise AppError(403, "paper_provider_domain_denied", "论文服务域名不在工作区允许的来源范围内")
        unrestricted = access_allow_all(self.db, self.workspace_id)
        self.db.commit()
        paper = self.provider.resolve(reference)
        if allowed and not unrestricted and not domain_is_allowed(paper.url, set(allowed)):
            raise AppError(403, "paper_source_domain_denied", "论文来源不在工作区允许的来源范围内")
        return paper

    def read(self, request: PaperReadRequest, *, paper: Paper | None = None) -> PaperReadResponse:
        paper = paper or self._resolve(request)
        if not paper.open_access or not paper.pdf_url:
            raise AppError(422, "paper_full_text_unavailable", "未找到可公开获取的 PDF；目前仅有元数据或摘要，不能声称已读取正文")
        domains, allow_all = self._allowed_domains(request.authorized_domains)
        host = PublicImageDownloadClient.initial_hostname(paper.pdf_url)
        if not allow_all and (not domains or not domain_is_allowed(paper.pdf_url, domains)):
            raise AppError(403, "paper_domain_authorization_required", "读取论文原文需要授权该域名",
                           {"hostname": host, "url": paper.pdf_url})
        # Expand approved parent domains to the exact host expected by the pinned downloader.
        permitted = {host, *domains}
        self.db.commit()
        downloader = PublicImageDownloadClient(self.settings, deadline=time.monotonic() + 35)
        try:
            # Preserve existing parent-domain grants on redirected subdomains.
            # Every actual hop still receives pinned public-IP validation.
            for _ in range(self.settings.external_download_max_redirects + 1):
                try:
                    response = downloader._download(paper.pdf_url, allowed_hosts=permitted, max_bytes=20_000_000,
                                                    accept="application/pdf")
                    break
                except AcquisitionApprovalRequired as exc:
                    if not allow_all and (not domains or not domain_is_allowed(f"https://{exc.hostname}", domains)):
                        raise
                    permitted.add(exc.hostname)
            else:
                raise AppError(422, "paper_redirect_limit", "论文原文重定向过多")
        except AcquisitionApprovalRequired as exc:
            raise AppError(403, "paper_redirect_authorization_required", "论文原文跳转到了尚未授权的域名",
                           {"hostname": exc.hostname, "url": paper.pdf_url}) from exc
        finally:
            downloader.close()
        if not response.data.lstrip().startswith(b"%PDF-"):
            raise AppError(422, "paper_not_pdf", "返回内容不是有效 PDF，可能是登录或访问限制页面；未读取正文")
        try:
            parsed = _parse_pdf(response.data, max_pages=200)
        except DocumentParseError as exc:
            raise AppError(422, "paper_pdf_unreadable", "未能提取 PDF 正文，可能为扫描件、加密文件或超过 200 页；未将其标为已读") from exc
        remaining = 48000
        excerpts = []
        truncated = False
        for chunk in parsed.chunks:
            if remaining <= 0:
                truncated = True
                break
            text = chunk.content[:min(6000, remaining)]
            truncated = truncated or len(text) < len(chunk.content)
            excerpts.append(PaperExcerpt(locator=chunk.locator, content=text))
            remaining -= len(text)
        if not excerpts:
            raise AppError(422, "paper_pdf_unreadable", "PDF 中没有可引用的文本")
        result = PaperReadResponse(paper=paper, url=response.final_url,
            content_hash=hashlib.sha256(response.data).hexdigest(), excerpts=excerpts, truncated=truncated)
        self.audit.record(actor_id=self.actor_id, action="paper.read", resource_type="paper", resource_id=paper.paper_id,
                          details={"provider": paper.provider, "document_sha256": result.content_hash,
                                   "excerpt_count": len(excerpts), "truncated": truncated})
        self.db.commit()
        return result

    def save(self, request: PaperSaveRequest) -> SourceRecord:
        paper = self._resolve(request)
        # Deterministic primary key provides concurrent/idempotent saves without
        # altering the existing SourceRecord schema or deduping unrelated sources.
        identity = paper.doi.casefold() if paper.doi else f"{paper.provider}:{paper.paper_id}"
        source_id = str(uuid5(NAMESPACE_URL, f"learngraph:paper:{self.workspace_id}:{identity}"))
        record = self.db.scalar(select(SourceRecord).where(SourceRecord.workspace_id == self.workspace_id, SourceRecord.id == source_id))
        if record is not None:
            if self.source_access and not self.source_access(record.id, "write"):
                raise AppError(404, "source_not_found", "Source was not found")
            if record.metadata_json.get("evidence_kind") == "full_text" or not request.include_full_text:
                return record
        self.db.commit()
        reading = self.read(request, paper=paper) if request.include_full_text else None
        excerpts = reading.excerpts if reading else []
        content = "\n\n".join(f"[{e.locator}]\n{e.content}" for e in excerpts) if reading else paper.abstract
        metadata = {"kind": "paper", "paper": paper.model_dump(mode="json"), "bibtex": bibtex(paper),
                    "evidence_kind": "full_text" if reading else paper.evidence_kind,
                    "page_read": bool(reading), "saved_by": self.actor_id,
                    "excerpts": [e.model_dump() for e in excerpts],
                    "document_sha256": reading.content_hash if reading else None,
                    "truncated": reading.truncated if reading else False}
        values = dict(provider_id=f"papers:{paper.provider}", source_url=paper.url,
            final_url=reading.url if reading else paper.url, title=paper.title, content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(), content_type="text/plain",
            authorized_domain=urlsplit(reading.url if reading else paper.url).hostname or "",
            cache_status="full_text" if reading else paper.evidence_kind, metadata_json=metadata)
        inserted = False
        if record is None:
            candidate = SourceRecord(id=source_id, workspace_id=self.workspace_id, **values)
            try:
                with self.db.begin_nested():
                    self.db.add(candidate)
                    self.db.flush()
                record, inserted = candidate, True
            except IntegrityError:
                record = self.db.scalar(select(SourceRecord).where(SourceRecord.workspace_id == self.workspace_id,
                    SourceRecord.id == source_id).execution_options(populate_existing=True))
                if record is None:
                    raise AppError(409, "paper_save_conflict", "论文保存冲突，请重试")
        if self.source_access and not self.source_access(record.id, "write"):
            self.db.rollback()
            raise AppError(404, "source_not_found", "Source was not found")
        if not inserted:
            if not reading:
                self.db.commit()
                self.db.refresh(record)
                return record
            # Compare-and-swap is enforced by the database, including SQLite.
            # Only one concurrent reader may upgrade metadata to a body snapshot.
            # Body, hash, provenance and the exact quote group commit together.
            changed = self.db.execute(update(SourceRecord).where(
                SourceRecord.id == source_id, SourceRecord.workspace_id == self.workspace_id,
                SourceRecord.cache_status.in_(["metadata", "abstract"])).values(**values))
            if changed.rowcount != 1:
                self.db.commit()
                self.db.refresh(record)
                return record
        if reading:
            self.db.execute(delete(SourceCitation).where(SourceCitation.workspace_id == self.workspace_id,
                SourceCitation.source_id == source_id, SourceCitation.locator.like("page:%")))
            for excerpt in excerpts:
                self.db.add(SourceCitation(workspace_id=self.workspace_id, source_id=source_id,
                                          quote=excerpt.content, locator=excerpt.locator))
        self.audit.record(actor_id=self.actor_id, action="paper.save", resource_type="source", resource_id=record.id,
                          details={"provider": paper.provider, "evidence_kind": metadata["evidence_kind"]})
        self.db.commit()
        self.db.refresh(record)
        return record

    def library(self) -> list[SourceRecord]:
        rows = self.db.scalars(select(SourceRecord).where(SourceRecord.workspace_id == self.workspace_id,
            SourceRecord.provider_id.in_(["papers:openalex", "papers:arxiv"])).order_by(SourceRecord.created_at.desc()).limit(200)).all()
        return [r for r in rows if not self.source_access or self.source_access(r.id, "read")]
