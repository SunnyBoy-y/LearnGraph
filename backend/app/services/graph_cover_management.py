from __future__ import annotations

import base64
import binascii
import io
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import Graph, GraphNode
from app.domain.schemas.graphs import GraphCoverUpdateRequest, GraphCoverView
from app.repositories.audit import AuditRepository
from app.services.graph_cover import (
    default_graph_cover,
    generate_graph_cover,
    generate_template_cover,
    validate_custom_svg,
)


def normalize_cover_bytes(raw: bytes, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    """Decode, resize, and re-encode a raster cover; extraction is the only trust.

    Shared by the user upload path and the AI image engine: the source bytes are
    decoded here, EXIF-rotated, fitted to 640×300 and re-encoded as JPEG, so a
    filename or MIME claim never influences the stored artifact.

    ``max_bytes`` differs per caller on purpose — an upload is capped at the
    documented 2 MB, while an image model may legitimately return a larger PNG
    that we are about to shrink anyway.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        if len(raw) > max_bytes:
            raise AppError(
                413,
                "cover_image_too_large",
                f"封面图片不能超过 {max_bytes // (1024 * 1024)} MB",
            )
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP", "GIF"} or image.width * image.height > 25_000_000:
                raise ValueError("Unsupported cover image dimensions or format")
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image.convert("RGB"), (640, 300), method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (binascii.Error, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise AppError(422, "invalid_cover_image", "图片无法读取，请重新选择有效图片") from exc


def normalize_cover_image(data_url: str) -> str:
    """Decode, resize, and re-encode uploads; filenames/MIME claims are not trusted."""
    match = re.fullmatch(r"data:image/(?:png|jpeg|webp|gif);base64,([A-Za-z0-9+/=]+)", data_url)
    if not match:
        raise AppError(422, "invalid_cover_image", "请选择 PNG、JPG、WebP 或 GIF 图片")
    try:
        raw = base64.b64decode(match[1], validate=True)
    except binascii.Error as exc:
        raise AppError(422, "invalid_cover_image", "图片无法读取，请重新选择有效图片") from exc
    return normalize_cover_bytes(raw)


class GraphCoverService:
    """One authorization and persistence boundary for HTTP and model tools."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str, *, can_access: Callable[[str, str], bool]):
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.can_access = can_access

    def _graph(self, graph_id: str, permission: str) -> Graph:
        graph = self.db.scalar(select(Graph).where(Graph.workspace_id == self.workspace_id, Graph.id == graph_id))
        if graph is None or not self.can_access(graph_id, permission):
            raise AppError(404, "graph_not_found", "Graph was not found")
        return graph

    def _nodes(self, graph_id: str) -> list[GraphNode]:
        return list(self.db.scalars(select(GraphNode).where(
            GraphNode.workspace_id == self.workspace_id, GraphNode.graph_id == graph_id,
        ).order_by(GraphNode.id)).all())

    @staticmethod
    def _generated(graph: Graph, nodes: list[GraphNode]) -> str:
        return generate_graph_cover(
            graph.title, node_labels=[node.label for node in nodes],
            progress=sum(node.mastery_stars >= 3 for node in nodes) / len(nodes) if nodes else 0.0,
        )

    def _view(
        self,
        graph: Graph,
        nodes: list[GraphNode],
        *,
        used_default: bool | None = None,
    ) -> GraphCoverView:
        cover = graph.cover_svg or self._generated(graph, nodes)
        if used_default is None:
            used_default = cover == default_graph_cover(graph.title)
        return GraphCoverView(
            graph_id=graph.id, title=graph.title, graph_revision=graph.revision,
            node_count=len(nodes), cover_svg=cover,
            used_default=used_default,
            templates=[
                {"id": key, "name": name, "cover_svg": generate_template_cover(graph.title, key)}
                for key, name in (("ancient", "古风"), ("literature", "文学"), ("history", "历史"), ("science", "理科"), ("chemistry", "化学"), ("paper", "纸感"), ("midnight", "星夜"), ("sunrise", "晨光"))
            ],
        )

    def read(self, graph_id: str) -> GraphCoverView:
        graph = self._graph(graph_id, "read")
        return self._view(graph, self._nodes(graph_id))

    def update(self, graph_id: str, payload: GraphCoverUpdateRequest) -> GraphCoverView:
        graph = self._graph(graph_id, "write")
        nodes = self._nodes(graph_id)
        used_default = False
        if payload.mode == "image":
            cover = normalize_cover_image(payload.image_data_url or "")
        elif payload.mode == "svg":
            try:
                cover = validate_custom_svg(payload.svg or "")
            except (ValueError, UnicodeError, SyntaxError):
                cover = default_graph_cover(graph.title)
                used_default = True
        elif payload.mode == "template":
            cover = generate_template_cover(graph.title, payload.template or "paper")
        else:
            cover = self._generated(graph, nodes)
            used_default = cover == default_graph_cover(graph.title)
        graph.cover_svg = cover
        AuditRepository(self.db, self.workspace_id).record(
            actor_id=self.actor_id, action="graph.cover_updated", resource_type="graph", resource_id=graph.id,
            details={"mode": payload.mode, "template": payload.template, "used_default": used_default},
        )
        self.db.commit()
        return self._view(graph, nodes, used_default=used_default)
