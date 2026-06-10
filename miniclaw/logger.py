import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_DIR = Path.home() / ".miniclaw" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 格式：时间 [级别] 模块: 消息
FMT = logging.Formatter(
    "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    只在名为 ``miniclaw`` 的根 logger 上挂控制台 + 文件 handler。

    若给每个 ``miniclaw.*`` 子 logger 各挂一套 handler，且子 logger 默认
    ``propagate=True``，同一条日志会经子、父各打一次，控制台出现重复行。
    """
    root = logging.getLogger("miniclaw")
    if not root.handlers:
        root.setLevel(level)
        root.propagate = False
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(FMT)
        root.addHandler(ch)
        # 当 miniclaw.log 达到 10MB 时轮转，保留 5 个备份
        fh = RotatingFileHandler(
            LOG_DIR / "miniclaw.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(FMT)
        root.addHandler(fh)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if name.startswith("miniclaw."):
        logger.propagate = True
        while logger.handlers:
            logger.removeHandler(logger.handlers[0])
    elif name == "miniclaw":
        pass
    else:
        if not logger.handlers:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(FMT)
            logger.addHandler(ch)
            fh = RotatingFileHandler(
                LOG_DIR / "miniclaw.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setFormatter(FMT)
            logger.addHandler(fh)

    return logger