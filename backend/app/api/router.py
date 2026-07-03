from app.api.routes import auth, follows, interactions, timeline, tweets, users
from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(follows.router)
api_router.include_router(tweets.router)
api_router.include_router(interactions.router)
api_router.include_router(timeline.router)
