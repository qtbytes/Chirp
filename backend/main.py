from pathlib import Path

from app.api.router import api_router
from app.core.config import settings
from app.models import (  # noqa: F401
    Block,
    FeedItem,
    Follow,
    Like,
    Notification,
    Post,
    User,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# The schema is owned by Alembic (`cd backend && uv run alembic upgrade head`),
# not by this process. Importing the app used to run create_all() against
# settings.database_url as a side effect, which meant even the test suite
# reached into the real twitter.db just by importing `main`.

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
