from __future__ import annotations

import base64
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import User
from app.domain.schemas.auth import ProfileView


MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 16_000_000
AVATAR_SIZE = 256


def _image_url(image: Image.Image) -> str:
    output = BytesIO()
    image.info.clear()
    image.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def normalize_avatar(content: bytes) -> str:
    if len(content) > MAX_AVATAR_BYTES:
        raise AppError(413, "avatar_too_large", "头像图片不能超过 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise AppError(422, "avatar_format_invalid", "请选择 PNG、JPG、WebP 或 GIF 图片")
                if image.width * image.height > MAX_AVATAR_PIXELS:
                    raise AppError(422, "avatar_dimensions_invalid", "图片分辨率过大，请选择不超过 1600 万像素的图片")
                # Re-encoding strips metadata and flattens animated inputs to the first frame.
                oriented = ImageOps.exif_transpose(image)
                square = ImageOps.fit(oriented.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
                return _image_url(square)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AppError(422, "avatar_image_invalid", "无法读取这张图片，请重新选择有效的图片") from exc


class UserProfileService:
    def __init__(self, db: Session, principal: Principal) -> None:
        self.db = db
        self.principal = principal

    def update(self, *, display_name: str | None = None, avatar_url: str | None = None, reset_avatar: bool = False) -> ProfileView:
        user = self.db.get(User, self.principal.user_id)
        if user is None or user.tenant_id != self.principal.tenant_id or user.status != "active":
            raise AppError(401, "identity_inactive", "The authenticated identity is not active")
        if display_name is not None:
            user.display_name = display_name
        if reset_avatar:
            # A missing image uses the original name-based color and initial in every client.
            user.avatar_url = None
        elif avatar_url is not None:
            user.avatar_url = avatar_url
        self.db.commit()
        return ProfileView(id=user.id, display_name=user.display_name or user.username, avatar_url=user.avatar_url)
