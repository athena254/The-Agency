"""Gateway Launcher - reads config and launches the correct mode."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class GatewayMode(Enum):
    """Supported gateway operational modes."""

    BUTLER_ONLY = "butler_only"
    BUTLER_SEPARATE = "butler_separate"
    BUTLER_MERGED = "butler_merged"


class GatewayLauncher:
    """Reads configuration and launches the appropriate gateway mode.

    Usage:
        launcher = GatewayLauncher()
        launcher.launch()
    """

    DEFAULT_CONFIG_PATH = Path(__file__).parent / "CONFIG" / "gateway_modes.yaml"

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path) if config_path else self.DEFAULT_CONFIG_PATH
        self.config: dict[str, Any] = {}
        self.mode: GatewayMode = GatewayMode.BUTLER_SEPARATE

    def load_config(self) -> dict[str, Any]:
        """Load and validate configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        if not isinstance(self.config, dict):
            raise ValueError("Config must be a YAML mapping at the top level")

        # Validate mode
        mode_str = self.config.get("active_mode", "butler_separate")
        try:
            self.mode = GatewayMode(mode_str)
        except ValueError:
            valid = [m.value for m in GatewayMode]
            raise ValueError(f"Invalid mode '{mode_str}'. Must be one of: {valid}")

        logger.info("Loaded config from %s (mode=%s)", self.config_path, self.mode.value)
        return self.config

    def _apply_env_overrides(self) -> None:
        """Allow environment variables to override config values."""
        env_mode = os.environ.get("NEXUS_GATEWAY_MODE")
        if env_mode:
            self.mode = GatewayMode(env_mode)
            logger.info("Mode overridden by NEXUS_GATEWAY_MODE to %s", env_mode)

        log_level = os.environ.get("NEXUS_LOG_LEVEL")
        if log_level:
            self.config.setdefault("logging", {})["level"] = log_level

    def _setup_logging(self) -> None:
        """Configure logging based on config."""
        log_cfg = self.config.get("logging", {})
        level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
        fmt = log_cfg.get(
            "format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        log_file = log_cfg.get("file")

        handlers: list[logging.Handler] = []
        if log_file:
            handlers.append(logging.FileHandler(log_file))
        else:
            handlers.append(logging.StreamHandler(sys.stdout))

        logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)

    async def launch(self) -> None:
        """Load config, apply overrides, and start the gateway."""
        self.load_config()
        self._apply_env_overrides()
        self._setup_logging()

        logger.info("Launching Nexus Gateway in %s mode", self.mode.value)

        if self.mode == GatewayMode.BUTLER_ONLY:
            await self._launch_butler_only()
        elif self.mode == GatewayMode.BUTLER_SEPARATE:
            await self._launch_butler_separate()
        elif self.mode == GatewayMode.BUTLER_MERGED:
            await self._launch_butler_merged()

    async def _launch_butler_only(self) -> None:
        """Launch the Butler-only gateway."""
        from .butler_only import ButlerOnlyGateway

        gateway = ButlerOnlyGateway(self.config)
        await gateway.start()

    async def _launch_butler_separate(self) -> None:
        """Launch the Butler + Buddy separate gateway (production)."""
        from .butler_separate import ButlerSeparateGateway

        gateway = ButlerSeparateGateway(self.config)
        await gateway.start()

    async def _launch_butler_merged(self) -> None:
        """Launch the Butler + Buddy merged gateway."""
        from .butler_merged import ButlerMergedGateway

        gateway = ButlerMergedGateway(self.config)
        await gateway.start()


def launch_gateway(config_path: str | None = None) -> None:
    """Convenience entry point."""
    launcher = GatewayLauncher(config_path)
    asyncio.run(launcher.launch())


if __name__ == "__main__":
    launch_gateway()
