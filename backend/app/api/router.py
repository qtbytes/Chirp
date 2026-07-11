from app.api.routes import (
    auth,
    blocks,
    comments,
    follows,
    interactions,
    link_preview,
    media,
    mutes,
    notifications,
    timeline,
    tweets,
    users,
)
from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(blocks.router)
api_router.include_router(mutes.router)
api_router.include_router(comments.router)
api_router.include_router(users.router)
api_router.include_router(follows.router)
api_router.include_router(tweets.router)
api_router.include_router(interactions.router)
api_router.include_router(timeline.router)
api_router.include_router(media.router)
api_router.include_router(notifications.router)
api_router.include_router(link_preview.router)
