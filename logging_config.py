import logging.config

from parser_storage import LOG_DIR

MODULE_LOGGERS = ["app", "parser", "train_parser", "design_number_parser"]

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def configure_logging(level: str = "INFO") -> None:
    """Настраивает по логгеру-файлу с ротацией для каждого модуля + вывод в консоль."""
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    }
    loggers = {}
    for name in MODULE_LOGGERS:
        handler_key = f"file_{name}"
        handlers[handler_key] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": str(LOG_DIR / f"{name}.log"),
            "maxBytes": MAX_BYTES,
            "backupCount": BACKUP_COUNT,
            "encoding": "utf-8",
        }
        loggers[name] = {
            "handlers": ["console", handler_key],
            "level": level,
            "propagate": False,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                },
            },
            "handlers": handlers,
            "loggers": loggers,
            "root": {
                "handlers": ["console"],
                "level": level,
            },
        }
    )
