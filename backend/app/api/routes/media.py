import uuid
from pathlib import Path

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.schemas.media import MediaUploadOut
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

router = APIRouter(prefix="/media", tags=["media"])

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_VIDEO_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
ALLOWED_MEDIA_TYPES = {**ALLOWED_IMAGE_TYPES, **ALLOWED_VIDEO_TYPES}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024


@router.post("", response_model=MediaUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile,
    current_user_id: int = Depends(get_current_user_id),
) -> MediaUploadOut:
    """
    Store an uploaded image or video and return a relative URL to reference it.

    The returned URL is later attached to a tweet/comment via its media_url
    field. Requiring auth keeps anonymous clients from filling the disk.
    """
    content_type = file.content_type or ""
    extension = ALLOWED_MEDIA_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="media must be a JPEG, PNG, WebP, or GIF image, or an MP4, WebM, or MOV video",
        )

    is_video = content_type in ALLOWED_VIDEO_TYPES
    max_bytes = MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES

    # Read in bounded chunks so an oversized upload is rejected before the full
    # body is held in memory, rather than trusting Content-Length.
    chunks = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{'video' if is_video else 'image'} must be {limit_mb} MB or smaller",
            )

    media_dir = Path(settings.uploads_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    (media_dir / filename).write_bytes(bytes(chunks))

    return MediaUploadOut(url=f"/uploads/media/{filename}")
