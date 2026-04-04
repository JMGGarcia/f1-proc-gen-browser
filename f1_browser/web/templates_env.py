"""Shared Jinja2Templates instance so globals are available in all routes."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="web/templates")
