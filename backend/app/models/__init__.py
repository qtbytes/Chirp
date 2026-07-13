from app.models.block import Block
from app.models.feed import FeedItem
from app.models.follow import Follow
from app.models.like import Like
from app.models.mute import Mute
from app.models.notification import Notification
from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.post_view import PostView
from app.models.post_mention import PostMention
from app.models.report import Report
from app.models.user import User

# Importing this registers the SQLite FTS ``after_create`` / ``before_drop``
# metadata events, so ``create_all()`` (the test suite) builds the search index
# alongside the modelled tables. Kept last to avoid an import cycle: it imports
# Base from app.db.database, which the models above have already pulled in.
from app.db import fts  # noqa: E402,F401

__all__ = [
    "User",
    "Post",
    "PostHashtag",
    "PostMention",
    "Follow",
    "Like",
    "FeedItem",
    "Notification",
    "Block",
    "Mute",
    "PostView",
    "Report",
]
