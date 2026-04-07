"""Shared Jinja2 templates instance — imported by routers to avoid circular imports."""
from fastapi.templating import Jinja2Templates
from app.config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
