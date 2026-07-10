from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.schemas.link_preview import LinkPreviewOut
from app.services import link_preview as link_preview_service

router = APIRouter(prefix="/link-preview", tags=["link-preview"])


@router.get(
    "",
    response_model=LinkPreviewOut,
    dependencies=[Depends(rate_limiter("link_preview"))],
)
def get_link_preview(
    url: str = Query(..., min_length=1, max_length=2048),
    current_user_id: int = Depends(get_current_user_id),
) -> LinkPreviewOut:
    """
    Unfurl a URL into a preview card.

    Requires auth and is rate limited so it can't be abused as an open
    server-side fetch proxy. Returns 404 when the URL is unsafe, unreachable, or
    carries no usable metadata — the client then just shows the plain link.
    """
    preview = link_preview_service.fetch_link_preview(url)
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no preview available",
        )
    return preview
