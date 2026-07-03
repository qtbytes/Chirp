from app.api.router import api_router
from app.core.config import settings
from app.db.database import Base, engine
from app.models import (  # noqa: F401
    Comment,
    CommentLike,
    CommentRetweet,
    FeedItem,
    Follow,
    Like,
    Retweet,
    Tweet,
    User,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Interview-focused Twitter system skeleton with pull/push timeline strategies.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root() -> dict:
    return {
        "message": "Twitter system skeleton is running.",
        "focus": [
            "fan-out on read",
            "fan-out on write",
            "cursor pagination",
            "N+1 query avoidance",
            "Redis timeline cache",
            "background feed fan-out",
        ],
    }
