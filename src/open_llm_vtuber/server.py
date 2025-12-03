"""
Open-LLM-VTuber Server
========================
This module contains the WebSocket server for Open-LLM-VTuber, which handles
the WebSocket connections, serves static files, and manages the web tool.
It uses FastAPI for the server and Starlette for static file serving.
"""

import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles as StarletteStaticFiles

from .routes import init_client_ws_route, init_webtool_routes, init_proxy_route
from .service_context import ServiceContext
from .config_manager.utils import Config

# Optional: catalog builder (skip gracefully if missing in older installs)
try:
    from .items_catalog import build_items_catalog
except ModuleNotFoundError:  # pragma: no cover - safety fallback
    def build_items_catalog(*args, **kwargs):
        return []


# Create a custom StaticFiles class that adds CORS headers
class CORSStaticFiles(StarletteStaticFiles):
    """
    Static files handler that adds CORS headers to all responses.
    Needed because Starlette StaticFiles might bypass standard middleware.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)

        # Add CORS headers to all responses
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"

        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"

        return response


class AvatarStaticFiles(CORSStaticFiles):
    """
    Avatar files handler with security restrictions and CORS headers
    """

    async def get_response(self, path: str, scope):
        allowed_extensions = (".jpg", ".jpeg", ".png", ".gif", ".svg")
        if not any(path.lower().endswith(ext) for ext in allowed_extensions):
            return Response("Forbidden file type", status_code=403)
        response = await super().get_response(path, scope)
        return response


class ModelFilteredStaticFiles(CORSStaticFiles):
    """
    Static files handler that blocks PNGs inside any directory (or subdirectory)
    containing a Live2D model definition (e.g., model3.json/model.json).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._model_dirs = self._compute_model_dirs()

    def _compute_model_dirs(self):
        model_dirs = set()
        root = Path(self.directory).resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            if any(
                name.lower().endswith("model3.json") or ("model" in name.lower() and name.lower().endswith(".json"))
                for name in filenames
            ):
                model_dirs.add(Path(dirpath))
                # Do not prune dirnames; descendants remain blocked via ancestor check
        return model_dirs

    def _is_blocked_png(self, fs_path: Path) -> bool:
        if fs_path.suffix.lower() != ".png":
          return False
        try:
          resolved = fs_path.resolve()
        except FileNotFoundError:
          return False
        for model_dir in self._model_dirs:
            try:
                resolved.relative_to(model_dir)
                return True
            except ValueError:
                continue
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)

        if response.status_code == 200:
            fs_path_str, _ = self.lookup_path(path)
            if fs_path_str and self._is_blocked_png(Path(fs_path_str)):
                return Response(status_code=404)

        return response


class WebSocketServer:
    """
    API server for Open-LLM-VTuber. This contains the websocket endpoint for the client, hosts the web tool, and serves static files.

    Creates and configures a FastAPI app, registers all routes
    (WebSocket, web tools, proxy) and mounts static assets with CORS.

    Args:
        config (Config): Application configuration containing system settings.
        default_context_cache (ServiceContext, optional):
            Pre‑initialized service context for sessions' service context to reference to.
            **If omitted, `initialize()` method needs to be called to load service context.**

    Notes:
        - If default_context_cache is omitted, call `await initialize()` to load service context cache.
        - Use `clean_cache()` to clear and recreate the local cache directory.
    """

    def __init__(self, config: Config, default_context_cache: ServiceContext = None):
        self.app = FastAPI(title="Open-LLM-VTuber Server")  # Added title for clarity
        self.config = config
        self.default_context_cache = (
            default_context_cache or ServiceContext()
        )  # Use provided context or initialize a new empty one waiting to be loaded
        # It will be populated during the initialize method call

        # Add global CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Include routes, passing the context instance
        # The context will be populated during the initialize step
        self.app.include_router(
            init_client_ws_route(default_context_cache=self.default_context_cache),
        )
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache),
        )

        # Initialize and include proxy routes if proxy is enabled
        system_config = config.system_config
        if hasattr(system_config, "enable_proxy") and system_config.enable_proxy:
            # Construct the server URL for the proxy
            host = system_config.host
            port = system_config.port
            server_url = f"ws://{host}:{port}/client-ws"
            self.app.include_router(
                init_proxy_route(server_url=server_url),
            )

        # Mount cache directory first (to ensure audio file access)
        if not os.path.exists("cache"):
            os.makedirs("cache")
        self.app.mount(
            "/cache",
            CORSStaticFiles(directory="cache"),
            name="cache",
        )

        # Regenerate items catalog on startup (used by frontend fallback)
        try:
            # Resolve project root: server.py -> open_llm_vtuber -> src -> PROJECT ROOT
            project_root = Path(__file__).resolve().parents[2]
            items_dir = project_root / "live2d-models" / "items"

            logger.info(
                f"[ItemsCatalog] project_root={project_root}, "
                f"items_dir={items_dir}, exists={items_dir.exists()}"
            )

            built = build_items_catalog(
                base_dir=str(items_dir),
                url_prefix="/live2d-models/items",
            )

            logger.info(
                f"[ItemsCatalog] Live2D items catalog generated with "
                f"{len(built)} entries at {items_dir}"
            )
        except Exception as exc:  # pragma: no cover - best-effort
            logger.exception(f"[ItemsCatalog] Generation skipped/failed: {exc}")
            
        # Mount static files with CORS-enabled handlers
        self.app.mount(
            "/live2d-models",
            CORSStaticFiles(directory="live2d-models"),
            name="live2d-models",
        )
        self.app.mount(
            "/bg",
            CORSStaticFiles(directory="backgrounds"),
            name="backgrounds",
        )
        self.app.mount(
            "/avatars",
            AvatarStaticFiles(directory="avatars"),
            name="avatars",
        )

        # Mount web tool directory separately from frontend
        self.app.mount(
            "/web-tool",
            CORSStaticFiles(directory="web_tool", html=True),
            name="web_tool",
        )

        # Mount main frontend last (as catch-all)
        self.app.mount(
            "/",
            CORSStaticFiles(directory="frontend", html=True),
            name="frontend",
        )

    async def initialize(self):
        """Asynchronously load the service context from config.
        Calling this function is needed if default_context_cache was not provided to the constructor."""
        await self.default_context_cache.load_from_config(self.config)

    @staticmethod
    def clean_cache():
        """Clean the cache directory by removing and recreating it."""
        cache_dir = "cache"
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)
