"""
Logging configuration for World of Doors bot
Suppresses verbose Pipecat logs and highlights API calls
"""

import sys
import logging
from loguru import logger


def setup_logging():
    """Configure logging to suppress Pipecat and highlight API calls"""

    # Remove default logger
    logger.remove()

    # Add custom logger with format that highlights API calls
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>",
        level="INFO",
        filter=custom_filter
    )

    # Suppress noisy loggers
    logging.getLogger("pipecat").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("daily").setLevel(logging.WARNING)
    logging.getLogger("dailyai").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("silero").setLevel(logging.WARNING)

    # Keep important ones at INFO
    logging.getLogger("src").setLevel(logging.DEBUG)
    logging.getLogger("api_client").setLevel(logging.DEBUG)


def custom_filter(record):
    """Filter to highlight API-related logs"""

    # Always show these
    show_modules = [
        "api_client",
        "world_of_doors_flow",
        "appointment_handlers",
        "main",
        "bot"
    ]

    # Check if this log is from our code
    if any(mod in record["name"] for mod in show_modules):
        # Highlight API calls
        if any(keyword in record["message"].lower() for keyword in [
            "api", "request", "response", "calling", "endpoint",
            "creating appointment", "checking availability", "updating appointment",
            "cancelling", "deleting", "post", "get", "patch", "delete"
        ]):
            record["message"] = f"🔵 API | {record['message']}"
        return True

    # Suppress pipecat internal logs
    if "pipecat" in record["name"].lower():
        return record["level"].name in ["WARNING", "ERROR", "CRITICAL"]

    # Suppress other noisy libraries
    suppress_modules = [
        "httpx", "httpcore", "daily", "asyncio",
        "websockets", "silero", "uvicorn.access"
    ]

    if any(mod in record["name"].lower() for mod in suppress_modules):
        return record["level"].name in ["WARNING", "ERROR", "CRITICAL"]

    return True
