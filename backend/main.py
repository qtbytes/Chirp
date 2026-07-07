from pathlib import Path

from app.api.router import api_router
from app.core.config import settings
from app.db.database import Base, engine
from app.db.dev_schema import sync_sqlite_dev_schema
from app.models import (  # noqa: F401
    Comment,
    CommentLike,
    CommentRetweet,
    FeedItem,
    Follow,
    Like,
    Notification,
    Retweet,
    Tweet,
    User,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

if settings.dev_auto_sync_sqlite_schema:
    sync_sqlite_dev_schema(engine, Base.metadata)
else:
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

uploads_path = Path(settings.uploads_dir)
uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")


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
