"""Shared fixtures and pytest configuration."""
import pytest


# Ensure pytest-asyncio uses asyncio mode without per-test markers
pytest_plugins = ["pytest_asyncio"]
