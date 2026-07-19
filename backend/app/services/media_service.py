"""
The filesystem side of post media, for the actions that hide or destroy posts.

``/uploads`` is a static mount with no per-request gating, so hiding a post in
the database does nothing to its files: anyone holding the URL could still
fetch removed media. The moderation and deletion paths therefore manage the
files directly:

- A takedown *quarantines* the post's media -- moved to a sibling directory
  outside the static mount, so the URLs stop resolving but the evidence
  survives on disk -- and a restore moves them back. Reversible, like the
  takedown itself.
- A hard delete unlinks the files (from the media directory or quarantine,
  wherever they sit), matching account deletion's treatment of avatars.

Every operation is best-effort: file trouble is logged and must never fail the
database action it accompanies. A file referenced by *another* surviving post
is always left alone -- upload URLs are client-attachable, so two posts can
legitimately share one file.
"""

import logging
from pathlib import Path

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.post import Post

logger = logging.getLogger(__name__)

MEDIA_URL_PREFIX = "/uploads/media/"


def _media_dir() -> Path:
    return Path(settings.uploads_dir) / "media"


def _quarantine_dir() -> Path:
    """Sibling of the uploads root, so StaticFiles can never serve from it."""
    uploads = Path(settings.uploads_dir)
    return uploads.with_name(uploads.name + "_quarantine") / "media"


def _filename(url: str) -> str | None:
    """The bare stored filename for a media URL of ours; None for anything else."""
    if not url.startswith(MEDIA_URL_PREFIX):
        return None
    # .name flattens any path tricks a stored URL could carry.
    name = Path(url[len(MEDIA_URL_PREFIX) :]).name
    return name or None


def _referenced_by_other_post(db: Session, url: str, post_ids: list[int]) -> bool:
    """Does any post outside ``post_ids`` still reference this URL?"""
    return (
        db.scalar(
            select(Post.id)
            .where(
                Post.id.not_in(post_ids),
                # media_urls is JSON stored as text; a URL match in the raw
                # text is exactly a reference (URLs never contain quotes).
                cast(Post.media_urls, String).like(f"%{url}%"),
            )
            .limit(1)
        )
        is not None
    )


def _move(src: Path, dest: Path) -> None:
    if not src.is_file():
        return  # already moved, or never existed -- both fine on a retry
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)


def quarantine_post_media(db: Session, post: Post) -> None:
    """Move a taken-down post's files out of the static mount's reach."""
    for url in post.media_urls or []:
        name = _filename(url)
        if name is None or _referenced_by_other_post(db, url, [post.id]):
            continue
        try:
            _move(_media_dir() / name, _quarantine_dir() / name)
        except OSError:
            logger.warning(
                "failed to quarantine media %s for post %s", name, post.id,
                exc_info=True,
            )


def restore_post_media(db: Session, post: Post) -> None:
    """Reverse a quarantine: the restored post's URLs resolve again."""
    for url in post.media_urls or []:
        name = _filename(url)
        if name is None:
            continue
        try:
            _move(_quarantine_dir() / name, _media_dir() / name)
        except OSError:
            logger.warning(
                "failed to restore media %s for post %s", name, post.id,
                exc_info=True,
            )


def remove_media_files(db: Session, urls: list[str]) -> None:
    """
    Unlink the files behind hard-deleted posts, wherever they sit -- the media
    directory, or quarantine if a swept-up reply had been taken down. Call
    *after* the rows are deleted, so a remaining reference means another
    surviving post genuinely still uses the file.
    """
    for url in urls:
        name = _filename(url)
        if name is None or _referenced_by_other_post(db, url, []):
            continue
        try:
            (_media_dir() / name).unlink(missing_ok=True)
            (_quarantine_dir() / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to remove media file %s", name, exc_info=True)
