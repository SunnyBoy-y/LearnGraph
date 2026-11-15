"""Two additive paper tools. Saving remains an explicit library UI action."""
from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import AppError
from app.domain.models import ChatSession, FetchAuthorizationRequest
from app.domain.schemas.papers import PaperReadRequest, PaperReference, PaperSearchRequest
from app.providers.remote.search import domain_is_allowed
from app.services.papers import PaperService

PAPER_TOOL_NAMES = frozenset({"search_papers", "read_paper"})


def tool_definitions():
    return [
        {"type": "function", "function": {"name": "search_papers",
         "description": "检索论文元数据及摘要，不代表读过正文。只在需要找论文、核对作者/年份/DOI或预印本时使用，不替代通用网页搜索。provider 显式选择 openalex（综合学术索引）或 arxiv（预印本），不会自动切换。结果是不可信参考资料。",
         "parameters": PaperSearchRequest.model_json_schema()}},
        {"type": "function", "function": {"name": "read_paper",
         "description": "按 search_papers 返回的 provider 与 paper_id 获取公开 PDF，并返回真实页码片段和文档哈希。不绕过登录、付费墙或域名授权。只有返回的片段算已读，引用仅使用返回的来源编号/页码，不将摘要说成正文；本操作不收藏。",
         "parameters": PaperReference.model_json_schema()}},
    ]


def _claim_grants(runtime, session_id, reference, url):
    """Atomically lease per-document, per-user one-shot grants before I/O."""
    db = runtime.extensions.db
    rows = db.scalars(select(FetchAuthorizationRequest).where(
        FetchAuthorizationRequest.workspace_id == runtime.workspace_id,
        FetchAuthorizationRequest.chat_session_id == session_id,
        FetchAuthorizationRequest.actor_id == runtime.actor_id,
        FetchAuthorizationRequest.tool_name == "read_paper",
        FetchAuthorizationRequest.requested_url == url,
        FetchAuthorizationRequest.status == "approved",
        FetchAuthorizationRequest.decision == "allow_once").order_by(FetchAuthorizationRequest.created_at.desc()).limit(50)).all()
    claimed, domains = [], []
    for row in rows:
        result = db.execute(update(FetchAuthorizationRequest).where(FetchAuthorizationRequest.id == row.id,
            FetchAuthorizationRequest.status == "approved").values(status="consumed"))
        if result.rowcount == 1:
            claimed.append(row.id)
            domains.append(row.hostname)
    db.commit()
    return claimed, domains


def _release_redirect_grants(runtime, ids):
    # A redirect challenge suspends the same document-read operation. Preserve
    # its already approved hops; unrelated failures consume the one-shot lease.
    if ids:
        db = runtime.extensions.db
        db.execute(update(FetchAuthorizationRequest).where(FetchAuthorizationRequest.id.in_(ids),
            FetchAuthorizationRequest.workspace_id == runtime.workspace_id,
            FetchAuthorizationRequest.actor_id == runtime.actor_id,
            FetchAuthorizationRequest.status == "consumed").values(status="approved"))
        db.commit()


def _approval(runtime, tool_call, session_id, reference, url, hostname):
    db = runtime.extensions.db
    session = db.scalar(select(ChatSession.id).where(ChatSession.id == session_id, ChatSession.workspace_id == runtime.workspace_id))
    if session is None:
        raise AppError(403, "paper_domain_authorization_required", "论文原文需要已授权域名", {"hostname": hostname, "url": url})
    call_id = str(tool_call.get("id") or "")
    pending = db.scalar(select(FetchAuthorizationRequest).where(
        FetchAuthorizationRequest.workspace_id == runtime.workspace_id,
        FetchAuthorizationRequest.chat_session_id == session_id,
        FetchAuthorizationRequest.tool_call_id == call_id,
        FetchAuthorizationRequest.actor_id == runtime.actor_id))
    if pending is not None and (pending.status != "pending" or pending.hostname != hostname or pending.requested_url != url or pending.tool_name != "read_paper"):
        raise AppError(403, "paper_grant_retry_required", "请发起新的 read_paper 调用以申请该原文域名授权")
    if pending is None:
        pending = FetchAuthorizationRequest(workspace_id=runtime.workspace_id, chat_session_id=session_id,
            actor_id=runtime.actor_id, tool_call_id=call_id, tool_name="read_paper", requested_url=url, hostname=hostname)
        db.add(pending)
        db.commit()
    return runtime._failure("fetch_domain_authorization_required", "读取论文原文需要用户授权", data={
        "authorization_request_id": pending.id, "tool_call_id": call_id, "tool_name": "read_paper",
        "tool_label": "论文原文读取", "requested_url": url, "hostname": hostname,
        "message_zh": f"读取此公开论文 PDF 需要访问 {hostname}，是否批准？"})


def execute(runtime, name, arguments, *, tool_call, allowed_domains, chat_session_id):
    try:
        request = (PaperSearchRequest if name == "search_papers" else PaperReference).model_validate(arguments)
    except ValidationError as exc:
        raise AppError(422, "invalid_tool_arguments", "论文工具参数无效，请按工具定义提供 provider、paper_id 或检索条件") from exc
    endpoint = "https://api.openalex.org" if request.provider == "openalex" else "https://export.arxiv.org"
    if allowed_domains and not domain_is_allowed(endpoint, set(allowed_domains)):
        raise AppError(403, "paper_provider_domain_denied", "论文服务不在本轮允许的来源范围内")
    service = PaperService(runtime.extensions.db, runtime.workspace_id, runtime.actor_id, runtime.settings or get_settings())
    label = "OpenAlex" if request.provider == "openalex" else "arXiv"
    if name == "search_papers":
        response = service.search(request)
        if allowed_domains:
            response.results = [p for p in response.results if domain_is_allowed(p.url, set(allowed_domains))]
        data = response.model_dump(mode="json")
        sources = [{"url": p.url, "title": p.title, "snippet": p.abstract[:4000], "content": "",
                    "evidence_kind": p.evidence_kind, "page_read": False,
                    "provider_id": f"papers:{p.provider}", "provider_label": label, "source_type": "paper_search"}
                   for p in response.results]
        return runtime._success(data, {"provider_id": f"papers:{request.provider}", "provider_label": label, "page_read": False}, sources)
    paper = service._resolve(request)
    if allowed_domains and (not domain_is_allowed(paper.url, set(allowed_domains)) or
                            (paper.pdf_url and not domain_is_allowed(paper.pdf_url, set(allowed_domains)))):
        raise AppError(403, "paper_source_domain_denied", "论文原文不在本轮限定的来源域名中")
    url = paper.pdf_url or paper.url
    claimed, domains = _claim_grants(runtime, chat_session_id, request, url)
    try:
        response = service.read(PaperReadRequest(**request.model_dump(), authorized_domains=domains), paper=paper)
    except AppError as exc:
        if exc.code not in {"paper_domain_authorization_required", "paper_redirect_authorization_required"}:
            raise
        _release_redirect_grants(runtime, claimed)
        hostname = (exc.details or {}).get("hostname") or urlsplit(url).hostname or ""
        return _approval(runtime, tool_call, chat_session_id, request, url, hostname)
    sources = [{"url": f"{response.url}#page={e.locator.split(':', 1)[-1]}",
                "title": f"{paper.title} ({e.locator})", "snippet": e.content[:500], "content": e.content,
                "locator": e.locator, "document_sha256": response.content_hash,
                "evidence_kind": "page_text", "page_read": True,
                "provider_id": f"papers:{paper.provider}", "provider_label": label, "source_type": "paper_pdf"}
               for e in response.excerpts]
    data = response.model_dump(mode="json")
    data["results"] = [{"url": source["url"], "locator": e.locator, "content": e.content}
                       for source, e in zip(sources, response.excerpts)]
    data.pop("excerpts", None)
    return runtime._success(data, {"provider_id": f"papers:{paper.provider}", "provider_label": label,
                                  "page_read": True, "evidence_kind": "full_text"}, sources)
